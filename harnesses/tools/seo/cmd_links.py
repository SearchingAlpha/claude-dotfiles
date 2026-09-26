#!/usr/bin/env python3
"""
cmd_links.py — `seo.py backlinks-own`, `seo.py mentions`, `seo.py cc-check`.

backlinks-own: enlaces entrantes al dominio propio vía Bing WMT
(GetLinkCounts → GetUrlLinks por página) o desde un export CSV de Ahrefs
Webmaster Tools. Espejo de `get_backlinks_overview` (rank siempre null: no
hay DR gratuito). Solo dominio propio: el grafo de competidores no es gratis.

mentions: proxy de citaciones/menciones vía Brave ("dominio" -site:dominio,
marca, teléfono). Sirve para el propio dominio (auditoría offsite-authority)
y para competidores (--target).

cc-check: ¿está el dominio en el último índice de Common Crawl? (0 llamadas de pago)
"""

from __future__ import annotations

import csv
import time
from collections import defaultdict
from pathlib import Path

from clients import (bingwmt_link_counts, bingwmt_url_links, brave_search, cc_latest_crawl_id,
                     cc_lookup)
from core import (append_jsonl, domain, emit_json, human, is_ours, ops_path, quota_charge,
                  quota_guard, secret, today)


def register(sub, common):
    b = sub.add_parser("backlinks-own", parents=[common], help="enlaces entrantes propios")
    b.add_argument("--pages", type=int, default=20, help="páginas propias a expandir con GetUrlLinks")
    b.add_argument("--ahrefs-csv", help="export de Ahrefs WT (Backlinks) en vez de Bing")
    b.set_defaults(func=run_backlinks)

    m = sub.add_parser("mentions", parents=[common], help="menciones/citaciones vía Brave")
    m.add_argument("--target", help="dominio objetivo (default: el propio)")
    m.add_argument("--depth", type=int, default=20)
    m.set_defaults(func=run_mentions)

    c = sub.add_parser("cc-check", parents=[common], help="presencia en Common Crawl")
    c.add_argument("--domains", nargs="*", help="default: propio + competidores")
    c.set_defaults(func=run_cc)


# ---------------------------------------------------------- backlinks-own --

def _list(d):
    """Bing devuelve a veces la lista directa y a veces {Links:[…], TotalPages:n}."""
    if isinstance(d, dict):
        return d.get("Links") or d.get("Items") or [], d.get("TotalPages")
    return d or [], None


def _from_bing(cfg: dict, pages: int) -> dict:
    key, site = secret(cfg, "bing_wmt"), cfg.get("site_url") or f"https://{cfg['domain']}"
    counts, _ = _list(bingwmt_link_counts(key, site, 0))
    counts = sorted(counts, key=lambda r: -int(r.get("Count") or 0))
    human(f"GetLinkCounts: {len(counts)} páginas con enlaces entrantes")
    ref: dict[str, dict] = defaultdict(lambda: {"backlinks": 0, "pages": set(), "targets": set()})
    total, sampled = 0, 0
    for row in counts[:pages]:
        url, n = row.get("Url"), int(row.get("Count") or 0)
        total += n
        try:
            links, _ = _list(bingwmt_url_links(key, site, url, 0))
        except Exception as e:
            human(f"  GetUrlLinks {url}: {str(e)[:100]}")
            continue
        sampled += 1
        for l in links:
            src = l.get("Url") or ""
            d = domain(src)
            if not d or is_ours(src, cfg):
                continue
            ref[d]["backlinks"] += 1
            ref[d]["pages"].add(src)
            ref[d]["targets"].add(url.replace(site, "") or "/")
        time.sleep(0.3)
    doms = [{"domain": d, "backlinks": v["backlinks"], "referringPages": len(v["pages"]),
             "targets": sorted(v["targets"])[:10]} for d, v in ref.items()]
    doms.sort(key=lambda r: -r["backlinks"])
    return {"source": "bing_wmt",
            "overview": {"backlinks": total, "referringDomains": len(doms),
                         "referringPages": sum(r["referringPages"] for r in doms), "rank": None},
            "referringDomains": doms, "pages_sampled": sampled, "pages_total": len(counts),
            "note": "Bing solo cuenta lo que su índice ha rastreado; contrastar con Ahrefs WT (CSV)"}


def _from_ahrefs(cfg: dict, path: str) -> dict:
    ref: dict[str, dict] = defaultdict(lambda: {"backlinks": 0, "pages": set(), "targets": set(), "dr": None})
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        cols = {c.lower(): c for c in rd.fieldnames or []}
        src_c = next((cols[c] for c in cols if "referring page url" in c or c == "source url"), None)
        tgt_c = next((cols[c] for c in cols if "target url" in c), None)
        dr_c = next((cols[c] for c in cols if c in ("domain rating", "dr")), None)
        if not src_c:
            raise SystemExit(f"CSV sin columna 'Referring page URL': {rd.fieldnames}")
        n = 0
        for r in rd:
            src = r.get(src_c) or ""
            d = domain(src)
            if not d:
                continue
            n += 1
            ref[d]["backlinks"] += 1
            ref[d]["pages"].add(src)
            if tgt_c and r.get(tgt_c):
                ref[d]["targets"].add(r[tgt_c].replace(cfg.get("site_url", ""), "") or "/")
            if dr_c and r.get(dr_c):
                try:
                    ref[d]["dr"] = float(r[dr_c])
                except ValueError:
                    pass
    doms = [{"domain": d, "backlinks": v["backlinks"], "referringPages": len(v["pages"]),
             "targets": sorted(v["targets"])[:10], "dr": v["dr"]} for d, v in ref.items()]
    doms.sort(key=lambda r: -r["backlinks"])
    return {"source": "ahrefs_csv", "file": str(Path(path).name),
            "overview": {"backlinks": n, "referringDomains": len(doms),
                         "referringPages": sum(r["referringPages"] for r in doms), "rank": None},
            "referringDomains": doms, "pages_sampled": None, "note": "export manual Ahrefs WT"}


def run_backlinks(args, cfg) -> int:
    if args.dry_run:
        human(f"backlinks-own: Bing GetLinkCounts 1 + GetUrlLinks ≤{args.pages} (WMT, sin cuota de pago)")
        return 0
    res = _from_ahrefs(cfg, args.ahrefs_csv) if args.ahrefs_csv else _from_bing(cfg, args.pages)
    res["ts"] = today()
    append_jsonl(ops_path(cfg, "backlinks-own.jsonl"), res)
    o = res["overview"]
    human(f"{res['source']}: backlinks={o['backlinks']} dominios={o['referringDomains']} "
          f"páginas={o['referringPages']}")
    for r in res["referringDomains"][:15]:
        human(f"  {r['backlinks']:4d}  {r['domain']:<35} → {', '.join(r['targets'][:3])}")
    human(f"snapshot → {ops_path(cfg, 'backlinks-own.jsonl')}")
    if args.json:
        emit_json(res)
    return 0


# --------------------------------------------------------------- mentions --

def mention_queries(cfg: dict, target: str) -> list[str]:
    qs = [f'"{target}" -site:{target}']
    if is_ours("https://" + target, cfg):
        b = cfg.get("brand") or {}
        if b.get("name"):
            qs.append(f'"{b["name"]}" -site:{target}')
        for t in b.get("tokens", [])[:2]:
            q = f'"{t}" -site:{target}'
            if q not in qs:
                qs.append(q)
        if b.get("phone"):
            qs.append(f'"{b["phone"]}"')
    else:
        name = target.split(".")[0]
        qs.append(f'"{name}" -site:{target}')
    return qs


def run_mentions(args, cfg) -> int:
    target = (args.target or cfg["domain"]).lower().replace("www.", "")
    qs = mention_queries(cfg, target)
    if args.limit:
        qs = qs[:args.limit]
    human(f"mentions · {target} · {len(qs)} queries Brave")
    if args.dry_run:
        for q in qs:
            human(f"  brave 1  {q}")
        return 0
    quota_guard(cfg, "brave", len(qs), args.force)
    key = secret(cfg, "brave")
    res = {"ts": today(), "target": target, "queries": qs, "hits": [], "errors": []}
    try:
        for q in qs:
            try:
                r = brave_search(key, q, count=args.depth, country=cfg["geo"]["country"],
                                 lang=cfg["geo"]["language"])
                quota_charge(cfg, "brave", 1)
            except Exception as e:
                res["errors"].append({"query": q, "error": str(e)[:200]})
                human(f"  ERROR {q} · {str(e)[:100]}")
                continue
            for it in r["items"]:
                self_ = it["domain"] == target or it["domain"].endswith("." + target)
                res["hits"].append({"query": q, "rank": it["rank"], "url": it["url"],
                                    "domain": it["domain"], "title": it["title"], "self": self_})
            time.sleep(1.1)
    finally:
        indep = {h["domain"] for h in res["hits"] if not h["self"]}
        res["independent_count"] = len(indep)
        res["independent_domains"] = sorted(indep)
        append_jsonl(ops_path(cfg, "mentions.jsonl"), res)
    human(f"menciones independientes: {res['independent_count']} dominios")
    for d in res["independent_domains"][:20]:
        human(f"  {d}")
    human(f"snapshot → {ops_path(cfg, 'mentions.jsonl')}")
    if args.json:
        emit_json(res)
    return 0


# --------------------------------------------------------------- cc-check --

def run_cc(args, cfg) -> int:
    doms = args.domains or [cfg["domain"], *[(c.get("domain") if isinstance(c, dict) else c)
                                             for c in cfg.get("competitors", [])]]
    if args.dry_run:
        human(f"cc-check: {len(doms)} dominios (Common Crawl, gratis)")
        return 0
    crawl = cc_latest_crawl_id()
    res = {"ts": today(), "crawl": crawl, "domains": []}
    human(f"Common Crawl {crawl}")
    for d in doms:
        try:
            present = cc_lookup(crawl, d)
        except Exception as e:
            res["domains"].append({"domain": d, "present": None, "error": str(e)[:150]})
            human(f"  ?    {d} · {str(e)[:80]}")
            continue
        res["domains"].append({"domain": d, "present": present})
        human(f"  {'SÍ ' if present else 'no '}  {d}")
        time.sleep(1.0)
    append_jsonl(ops_path(cfg, "cc-check.jsonl"), res)
    if args.json:
        emit_json(res)
    return 0
