#!/usr/bin/env python3
"""
cmd_report.py — `seo.py report` (tablas markdown/JSON) y `seo.py quota`.

report: fusiona los últimos JSONL de <ops_dir>/ (rank, competitors, kw,
links, mentions) en tablas markdown listas para pegar en el informe de una
skill o enviar con _email.md_to_html. Sin llamadas de red. `--write-matrix`
deja <ops_dir>/rank-matrix.json para que un dashboard lo lea sin importar
esta librería.

quota: uso del mes por fuente frente a budgets del config (+ SerpAPI restante).
"""

from __future__ import annotations

import json
from pathlib import Path

from clients import serpapi_left
from cmd_rank import diff, rank_matrix
from core import emit_json, has_secret, human, ops_path, quota_used, read_jsonl, secret, today

SECTIONS = ("rank", "competitors", "kw", "links", "mentions")


def register(sub, common):
    p = sub.add_parser("report", parents=[common], help="tablas markdown/JSON desde ops/seo")
    p.add_argument("--sections", default=",".join(SECTIONS), help="coma-separadas: " + ",".join(SECTIONS))
    p.add_argument("--format", choices=["md", "json"], default="md")
    p.add_argument("--write-matrix", action="store_true", help="escribir rank-matrix.json")
    p.add_argument("--out", help="fichero de salida (default: stdout)")
    p.add_argument("--top", type=int, default=30)
    p.set_defaults(func=run_report)

    q = sub.add_parser("quota", parents=[common], help="uso del mes por fuente")
    q.set_defaults(func=run_quota)


def _last(cfg: dict, name: str) -> dict | None:
    rows = read_jsonl(ops_path(cfg, name), 1)
    return rows[0] if rows else None


def _f(v) -> str:
    return "n/d" if v in (None, "") else str(v)


def _table(headers: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(_f(c).replace("|", "\\|") for c in r) + " |")
    return out


def sec_rank(cfg: dict, top: int) -> tuple[list[str], dict]:
    m = rank_matrix(cfg)
    d = diff(cfg, "brave")
    delta = {r["query"]: r for r in d["rows"]}
    rows = []
    for r in m["rows"]:
        ev = delta.get(r["query"], {}).get("event")
        arrow = {"up": "↑", "down": "↓", "entered": "＋", "left": "－"}.get(ev, "")
        rows.append([r["query"], f"{_f(r['brave'])}{arrow}", r["google"], r["bing"],
                     r["gsc_pos"], r["gsc_imp"], r["gsc_page"] or ""])
    src = m["sources"]
    lines = [f"## Posiciones", "",
             f"Brave = rank-history (último pase) · Google/Bing = serp_watch {src['serpapi_ts'] or 'n/d'} "
             f"· GSC = {src['gsc']} {src['gsc_ts'] or 'n/d'}. `n/d` = sin dato (no en top-20 o sin impresiones).", ""]
    lines += _table(["query", "brave", "google", "bing", "gsc_pos", "gsc_imp", "página GSC"], rows)
    return lines, {"matrix": m, "diff": d}


def sec_competitors(cfg: dict, top: int) -> tuple[list[str], dict | None]:
    c = _last(cfg, "competitors.jsonl")
    if not c:
        return ["## Competidores", "", "n/d — corre `seo.py competitors`"], None
    rows = [[r["domain"], r["class"], r["score"], r["appearances"], r["queries_won"], r["best_rank"],
             r["avg_rank"], "sí" if r["known"] else "**nuevo**"] for r in c["domains"][:top]]
    lines = [f"## Competidores ({c['ts']}, {c['window_days']}d, {c['queries_n']} queries, motores {', '.join(c['engines'])})",
             "", "score = Σ (21−posición)/20 por aparición. Clase según config; `nuevo` = no está en el roster.", ""]
    lines += _table(["dominio", "clase", "score", "apar.", "#1", "mejor", "media", "roster"], rows)
    return lines, c


def sec_kw(cfg: dict, top: int) -> tuple[list[str], dict | None]:
    k = _last(cfg, "kw-research.jsonl")
    if not k:
        return ["## Keywords", "", "n/d — corre `seo.py kw-research`"], None
    rows = sorted(k["rows"], key=lambda r: -(r.get("searchVolume") or 0))
    nz = sum(1 for r in rows if r.get("searchVolume"))
    lines = [f"## Keywords ({k['ts']}, ventana {k['window'][0]}..{k['window'][1]}, {len(rows)} filas, {nz} con volumen)",
             "", "`bing_vol` = impresiones Bing/mes en la ventana (≈1/8 de Google; ranking relativo). "
             "KD, CPC y volumen Google: **no disponibles** sin DataForSEO.", ""]
    lines += _table(["keyword", "bing_vol", "broad", "intent", "metro", "origen"],
                    [[r["keyword"], r.get("searchVolume"), r.get("broadVolume"), r.get("intent"),
                      r.get("metro") or "", r.get("origin")] for r in rows[:top]])
    if len(rows) > top:
        zeros = [r["keyword"] for r in rows[top:] if not r.get("searchVolume")]
        lines += ["", f"Otras {len(rows) - top} filas; {len(zeros)} con volumen 0 (long-tail hiperlocal, esperado)."]
    return lines, k


def sec_links(cfg: dict, top: int) -> tuple[list[str], dict | None]:
    b = _last(cfg, "backlinks-own.jsonl")
    if not b:
        return ["## Enlaces entrantes (propios)", "", "n/d — corre `seo.py backlinks-own`"], None
    o = b["overview"]
    lines = [f"## Enlaces entrantes (propios, {b['source']}, {b['ts']})", "",
             f"backlinks={o['backlinks']} · dominios={o['referringDomains']} · páginas={o['referringPages']}. "
             f"{b.get('note', '')}", ""]
    if b["referringDomains"]:
        lines += _table(["dominio", "backlinks", "páginas", "destinos"],
                        [[r["domain"], r["backlinks"], r["referringPages"], ", ".join(r["targets"][:3])]
                         for r in b["referringDomains"][:top]])
    return lines, b


def sec_mentions(cfg: dict, top: int) -> tuple[list[str], dict | None]:
    ms = read_jsonl(ops_path(cfg, "mentions.jsonl"))
    if not ms:
        return ["## Menciones", "", "n/d — corre `seo.py mentions`"], None
    latest: dict[str, dict] = {}
    for m in ms:
        latest[m["target"]] = m
    lines = ["## Menciones (Brave, proxy de citaciones)", ""]
    rows = [[t, m["ts"], m["independent_count"], ", ".join(m.get("independent_domains", [])[:8])]
            for t, m in latest.items()]
    lines += _table(["objetivo", "fecha", "dominios independientes", "muestra"], rows)
    return lines, latest


def run_report(args, cfg) -> int:
    wanted = [s.strip() for s in args.sections.split(",") if s.strip()]
    bad = [s for s in wanted if s not in SECTIONS]
    if bad:
        human(f"secciones desconocidas: {bad} (válidas: {', '.join(SECTIONS)})")
        return 1
    fns = {"rank": sec_rank, "competitors": sec_competitors, "kw": sec_kw,
           "links": sec_links, "mentions": sec_mentions}
    md = [f"# SEO — {cfg['domain']} — {today()}", ""]
    data = {"ts": today(), "domain": cfg["domain"]}
    for s in wanted:
        lines, obj = fns[s](cfg, args.top)
        md += lines + [""]
        data[s] = obj
    if args.write_matrix and not args.dry_run:
        m = data.get("rank") or {"matrix": rank_matrix(cfg), "diff": diff(cfg, "brave")}
        path = ops_path(cfg, "rank-matrix.json")
        path.write_text(json.dumps({"ts": today(), "rows": m["matrix"]["rows"],
                                    "sources": m["matrix"]["sources"], "diff": m["diff"]},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        human(f"rank-matrix → {path}")
    out = json.dumps(data, ensure_ascii=False, indent=2) if args.format == "json" else "\n".join(md)
    if args.out and not args.dry_run:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
        human(f"informe → {args.out}")
    else:
        print(out)
    return 0


def run_quota(args, cfg) -> int:
    res = {"month": today()[:7], "sources": {}}
    for src, limit in cfg["budgets"].items():
        used = quota_used(cfg, src)
        res["sources"][src] = {"used": used, "budget": limit, "remaining": max(0, limit - used)}
    if has_secret(cfg, "serpapi") and not args.dry_run:
        left = serpapi_left(secret(cfg, "serpapi"))
        res["serpapi_account_left"] = left
        res["serpapi_reserve"] = cfg.get("serpapi_reserve", 0)
    human(f"cuota {res['month']}")
    for src, v in res["sources"].items():
        human(f"  {src:<9} {v['used']:>5} / {v['budget']:<5} usadas")
    if "serpapi_account_left" in res:
        human(f"  serpapi cuenta: quedan {res['serpapi_account_left']} (reserva cron {res['serpapi_reserve']})")
    if args.json:
        emit_json(res)
    return 0
