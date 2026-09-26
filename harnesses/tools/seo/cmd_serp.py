#!/usr/bin/env python3
"""
cmd_serp.py — `seo.py serp` (SERPs) y `seo.py competitors` (quién gana).

serp: top-N por query en Brave (sin geo; el metro va en el texto) o en
Google/Bing vía SerpAPI (geolocalizado). Espejo de `get_serp_results` de
open-seo: {keyword, ok, items[{rank, domain, title, url, type, description}]}.
Escribe una línea por pase en <ops_dir>/serp.jsonl.

competitors: tally offline de dominios sobre serp.jsonl (+ el JSONL de
serp_watch si existe). Espejo de `find_serp_competitors`: score por posición,
clase (competitor / aggregator / ugc / brand_collision / other) y `known`.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, timedelta

from clients import brave_search, serpapi_left, serpapi_search
from core import (append_jsonl, emit_json, human, is_ours, ops_path, quota_charge,
                  quota_guard, read_jsonl, run_id, secret, today)

SETS = ("money", "brand", "tracking")


# --------------------------------------------------------------- helpers --

def query_set(cfg: dict, which: str) -> list[str]:
    qs = cfg["queries"]
    if which == "all":
        out = []
        for s in SETS:
            out += [q for q in qs.get(s, []) if q not in out]
        return out
    return list(qs.get(which, []))


def geo_for(q: str, cfg: dict) -> tuple[str, str | None]:
    """(etiqueta, serpapi_location) según el metro/ciudad que aparezca en la query."""
    k = q.lower()
    for m in cfg["geo"].get("metros", []):
        toks = [m.get("name", ""), m.get("county", ""), *(m.get("cities") or [])]
        if any(t and t.lower() in k for t in toks):
            return m.get("id") or m.get("name"), m.get("serpapi_location")
    return cfg["geo"].get("state", "default"), cfg["geo"].get("default_location")


def _recent(cfg: dict, engine: str, max_age: int) -> dict:
    """{query: result} de serp.jsonl más reciente que max_age días para ese motor."""
    if max_age <= 0:
        return {}
    cutoff = str(date.today() - timedelta(days=max_age))
    out = {}
    for snap in read_jsonl(ops_path(cfg, "serp.jsonl")):
        if snap.get("ts", "") < cutoff:
            continue
        for r in snap.get("results", []):
            if r.get("engine") == engine and r.get("ok"):
                out[r["query"]] = r
    return out


def _probe_serpapi(cfg: dict, key: str, engine: str, q: str, loc: str | None, depth: int) -> dict:
    data = serpapi_search(key, engine, q, loc, num=depth)
    organic = data.get("organic_results", []) or []
    items = [{"rank": r.get("position", i + 1), "url": r.get("link", ""),
              "domain": _dom(r.get("link", "")), "title": r.get("title", ""),
              "description": r.get("snippet", ""), "type": "organic"}
             for i, r in enumerate(organic)]
    return {"items": items, "n": len(items)}


def _dom(url: str) -> str:
    from core import domain
    return domain(url)


def run_serp(cfg: dict, queries: list[str], engine: str, depth: int = 20,
             force: bool = False, dry_run: bool = False, max_age: int = 0,
             label: str = "serp") -> dict:
    """Ejecuta el pase y devuelve el snapshot (también lo escribe en serp.jsonl)."""
    cached = _recent(cfg, engine, max_age)
    todo = [q for q in queries if q not in cached]
    source = "brave" if engine == "brave" else "serpapi"
    human(f"{label} · motor={engine} · {len(queries)} queries "
          f"({len(cached)} en caché <{max_age}d, {len(todo)} llamadas · cuota {source})")
    if dry_run:
        for q in todo:
            human(f"  {source:8} 1  {q}  [{geo_for(q, cfg)[0]}]")
        return {"ts": today(), "run_id": run_id(), "engines": [engine], "dry_run": True,
                "planned": len(todo), "results": [cached[q] for q in queries if q in cached]}
    if todo:
        quota_guard(cfg, source, len(todo), force)
        key = secret(cfg, source)
        if source == "serpapi":
            left = serpapi_left(key)
            reserve = int(cfg.get("serpapi_reserve", 0))
            if left is not None and left - len(todo) < reserve and not force:
                raise SystemExit(f"cuota serpapi: quedan {left}, reserva {reserve} para serp_watch.py; "
                                 f"--force para saltarlo")
    snap = {"ts": today(), "run_id": run_id(), "engines": [engine], "results": []}
    try:
        for q in queries:
            if q in cached:
                snap["results"].append(dict(cached[q], cached=True))
                continue
            geo_label, loc = geo_for(q, cfg)
            res = {"engine": engine, "query": q, "geo": geo_label if source == "serpapi"
                   else f"n/a ({geo_label} en texto)", "ok": True}
            try:
                if engine == "brave":
                    r = brave_search(key, q, count=depth, country=cfg["geo"]["country"],
                                     lang=cfg["geo"]["language"])
                    time.sleep(1.1)                      # Free: 1 req/s
                else:
                    r = _probe_serpapi(cfg, key, engine, q, loc, depth)
                quota_charge(cfg, source, 1)
            except Exception as e:                       # un fallo no tira el pase
                res.update({"ok": False, "error": str(e)[:300]})
                snap["results"].append(res)
                human(f"  ERROR         {q} · {str(e)[:120]}")
                continue
            ours = next((it for it in r["items"] if is_ours(it["url"], cfg)), None)
            res.update({"items": r["items"], "n": r["n"],
                        "our_rank": ours["rank"] if ours else None,
                        "our_url": ours["url"] if ours else None})
            snap["results"].append(res)
            rank = f"#{res['our_rank']}" if res["our_rank"] else f"NO (top-{depth})"
            tops = ", ".join(it["domain"] for it in r["items"][:5])
            human(f"  {rank:>12}  {q}\n{'':16}gana: {tops}")
    finally:
        if any(not r.get("cached") for r in snap["results"]):
            append_jsonl(ops_path(cfg, "serp.jsonl"), snap)
    return snap


# ------------------------------------------------------------------ serp --

def register(sub, common):
    p = sub.add_parser("serp", parents=[common], help="SERPs top-N (Brave o SerpAPI)")
    p.add_argument("--queries", choices=[*SETS, "all"], default="all")
    p.add_argument("--engine", choices=["brave", "google", "bing"], default="brave")
    p.add_argument("--depth", type=int, default=20)
    p.add_argument("-q", "--query", action="append", default=[],
                   help="query ad-hoc (repetible); sustituye a --queries")
    p.add_argument("--max-age-days", type=int, default=0,
                   help="reutilizar resultados de serp.jsonl más recientes que N días")
    p.set_defaults(func=run)

    c = sub.add_parser("competitors", parents=[common],
                       help="tally de dominios que ganan nuestras queries (offline)")
    c.add_argument("--days", type=int, default=30)
    c.add_argument("--fetch", action="store_true", help="correr serp (brave, all) antes")
    c.set_defaults(func=run_competitors)


def _select(args, queries: list[str]) -> list[str]:
    if args.only:
        queries = [q for q in queries if args.only.lower() in q.lower()]
    if args.limit:
        queries = queries[:args.limit]
    return queries


def run(args, cfg) -> int:
    queries = args.query or query_set(cfg, args.queries)
    queries = _select(args, queries)
    if not queries:
        human("sin queries")
        return 2
    snap = run_serp(cfg, queries, args.engine, args.depth, args.force, args.dry_run,
                    args.max_age_days)
    if args.json:
        emit_json(snap)
    if args.dry_run:
        return 0
    ok = sum(1 for r in snap["results"] if r.get("ok"))
    human(f"\nsnapshot → {ops_path(cfg, 'serp.jsonl')} · {ok}/{len(snap['results'])} ok")
    return 0 if ok else 2


# ----------------------------------------------------------- competitors --

def classify_domain(dom: str, cfg: dict) -> tuple[str, bool]:
    """(clase, conocido) según competitors[] y domain_classes del config."""
    for c in cfg.get("competitors", []):
        cd = c.get("domain") if isinstance(c, dict) else c
        if cd and (dom == cd or dom.endswith("." + cd)):
            t = (c.get("type") if isinstance(c, dict) else "") or ""
            return ("brand_collision" if "collision" in t else "competitor"), True
    for cls, doms in (cfg.get("domain_classes") or {}).items():
        if any(dom == d or dom.endswith("." + d) for d in doms):
            return cls, True
    return "other", False


def tally(cfg: dict, days: int) -> dict:
    cutoff = str(date.today() - timedelta(days=days))
    agg: dict[str, dict] = defaultdict(lambda: {"score": 0.0, "appearances": 0, "queries_won": 0,
                                                "ranks": [], "queries": set(), "engines": set()})
    queries_seen, engines = set(), set()

    def add(dom, rank, q, engine):
        if not dom or is_ours("https://" + dom, cfg):
            return
        a = agg[dom]
        a["score"] += max(0, 21 - rank) / 20
        a["appearances"] += 1
        a["queries_won"] += 1 if rank == 1 else 0
        a["ranks"].append(rank)
        a["queries"].add(q)
        a["engines"].add(engine)
        queries_seen.add(q)
        engines.add(engine)

    for snap in read_jsonl(ops_path(cfg, "serp.jsonl")):
        if snap.get("ts", "") < cutoff:
            continue
        for r in snap.get("results", []):
            if r.get("ok"):
                for it in r.get("items", []):
                    add(it.get("domain"), int(it.get("rank") or 99), r["query"], r["engine"])
    sw = (cfg.get("sources") or {}).get("serp_watch_jsonl")
    if sw:
        for snap in read_jsonl(sw):
            if snap.get("ts", "") < cutoff:
                continue
            for r in snap.get("results", []):
                for t in r.get("top", []):          # serp_watch guarda top-5 {pos, domain}
                    add(t.get("domain"), int(t.get("pos") or 99), r["query"], r["engine"])

    rows = []
    for dom, a in agg.items():
        cls, known = classify_domain(dom, cfg)
        rows.append({"domain": dom, "class": cls, "score": round(a["score"], 2),
                     "appearances": a["appearances"], "queries_won": a["queries_won"],
                     "best_rank": min(a["ranks"]), "avg_rank": round(sum(a["ranks"]) / len(a["ranks"]), 1),
                     "queries": sorted(a["queries"]), "engines": sorted(a["engines"]), "known": known})
    rows.sort(key=lambda r: -r["score"])
    return {"ts": today(), "window_days": days, "queries_n": len(queries_seen),
            "engines": sorted(engines), "domains": rows}


def run_competitors(args, cfg) -> int:
    if args.fetch:
        run_serp(cfg, query_set(cfg, "all"), "brave", 20, args.force, args.dry_run, 7,
                 label="competitors --fetch")
        if args.dry_run:
            return 0
    res = tally(cfg, args.days)
    if not res["domains"]:
        human("sin datos: corre `serp` (o `competitors --fetch`) primero")
        return 2
    append_jsonl(ops_path(cfg, "competitors.jsonl"), res)
    human(f"competidores · {res['queries_n']} queries · {res['window_days']}d · motores {res['engines']}")
    human(f"  {'score':>6} {'apar':>4} {'#1':>3} {'best':>4} {'avg':>5}  clase            dominio")
    for r in res["domains"][:25]:
        flag = "" if r["known"] else "  ← nuevo"
        human(f"  {r['score']:6.2f} {r['appearances']:4d} {r['queries_won']:3d} {r['best_rank']:4d} "
              f"{r['avg_rank']:5.1f}  {r['class']:<16} {r['domain']}{flag}")
    human(f"snapshot → {ops_path(cfg, 'competitors.jsonl')}")
    if args.json:
        emit_json(res)
    return 0
