#!/usr/bin/env python3
"""
cmd_kw.py — `seo.py kw-research`: volumen Bing por keyword (GetKeyword).

Espejo de `research_keywords` de open-seo con lo que hay gratis: volumen
mensual estimado desde las impresiones Bing de la ventana (Impressions/meses),
`BroadImpressions` como techo, intención heurística. keywordDifficulty, cpc y
competition son SIEMPRE null (no replicables sin DataForSEO; no se inventan).
Bing ≈ 1/8 de Google: usar como ranking relativo, no como volumen absoluto.

Verificado 2026-09-26: country="us", language="en-US", fechas ISO; respuesta
{BroadImpressions, Impressions, Query}. Long-tail hiperlocal → 0 (esperado).
"""

from __future__ import annotations

import csv
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from clients import bingkw_keyword, bingkw_related
from core import (append_jsonl, classify_intent, emit_json, human, ops_path, quota_charge,
                  quota_guard, run_id, secret, today)


def register(sub, common):
    p = sub.add_parser("kw-research", parents=[common], help="volumen Bing por keyword")
    p.add_argument("--seeds", default=None,
                   help="seeds.json de `seeds gen` (default), .txt (una por línea), o '-' (stdin)")
    p.add_argument("--months", type=int, default=3)
    p.add_argument("--related", action="store_true", help="+GetRelatedKeywords por seed (1 llamada más)")
    p.add_argument("--max-calls", type=int, default=None)
    p.set_defaults(func=run)


def load_seeds(cfg: dict, arg: str | None) -> list[dict]:
    if arg == "-":
        return [{"keyword": l.strip()} for l in sys.stdin if l.strip()]
    path = Path(arg) if arg else ops_path(cfg, "seeds.json")
    if not path.exists():
        raise SystemExit(f"seeds no encontradas: {path} (corre `seeds gen` o pasa --seeds)")
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("seeds", data) if isinstance(data, dict) else data
    return [{"keyword": l.strip()} for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _ads_cpc(cfg: dict) -> dict[str, float]:
    path = (cfg.get("seeds") or {}).get("ads_keywords_csv")
    if not path or not Path(path).exists():
        return {}
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            kw = (r.get("Keyword") or "").strip().strip('"[]+').lower()
            try:
                out[kw] = float(r.get("Max CPC") or 0)
            except ValueError:
                pass
    return out


def _row(cfg: dict, kw: str, data, months: int, seed: dict, origin: str, ads: dict) -> dict:
    imp = int((data or {}).get("Impressions") or 0)
    broad = int((data or {}).get("BroadImpressions") or 0)
    row = {"keyword": kw,
           "searchVolume": round(imp / months) if data is not None else None,
           "broadVolume": round(broad / months) if data is not None else None,
           "volumeSource": "bing_wmt" if data is not None else "none",
           "volumeNote": "impresiones Bing/mes en la ventana; Bing≈1/8 Google; ranking relativo",
           "keywordDifficulty": None, "cpc": None, "competition": None,
           "intent": seed.get("intent_guess") or classify_intent(kw, cfg),
           "metro": seed.get("metro"), "county": seed.get("county"),
           "seed": seed.get("seed") or kw, "origin": origin}
    if kw in ads:
        row.update({"cpc": ads[kw], "cpcSource": "ads_bid"})
    return row


def run(args, cfg) -> int:
    seeds = load_seeds(cfg, args.seeds)
    if args.only:
        seeds = [s for s in seeds if args.only.lower() in s["keyword"].lower()]
    if args.limit:
        seeds = seeds[:args.limit]
    per = 2 if args.related else 1
    if args.max_calls:
        seeds = seeds[:max(0, args.max_calls // per)]
    calls = len(seeds) * per
    geo = cfg["geo"]
    lang = geo.get("bing_language") or f"{geo['language']}-{geo['country']}"
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=30 * args.months)
    human(f"kw-research · {len(seeds)} seeds · {calls} llamadas Bing · ventana {start}..{end}")
    if not seeds:
        return 2
    if args.dry_run:
        for s in seeds[:20]:
            human(f"  bing_kw {per}  {s['keyword']}")
        if len(seeds) > 20:
            human(f"  … {len(seeds) - 20} más")
        return 0
    quota_guard(cfg, "bing_kw", calls, args.force)
    key = secret(cfg, "bing_wmt")
    ads = _ads_cpc(cfg)
    snap = {"ts": today(), "run_id": run_id(), "source": "bing_wmt",
            "window": [str(start), str(end)], "months": args.months, "seeds_n": len(seeds),
            "calls": 0, "rows": [], "errors": []}
    seen = {s["keyword"].lower() for s in seeds}
    try:
        for s in seeds:
            kw = s["keyword"].lower()
            try:
                d = bingkw_keyword(key, kw, geo["country"], lang, start, end)
                snap["calls"] += 1
                quota_charge(cfg, "bing_kw", 1)
                snap["rows"].append(_row(cfg, kw, d or {}, args.months, s, s.get("origin", "seed"), ads))
                if args.related:
                    time.sleep(0.5)
                    rel = bingkw_related(key, kw, geo["country"], lang, start, end) or []
                    snap["calls"] += 1
                    quota_charge(cfg, "bing_kw", 1)
                    for r in rel:
                        rk = (r.get("Query") or "").lower().strip()
                        if rk and rk not in seen:
                            seen.add(rk)
                            snap["rows"].append(_row(cfg, rk, r, args.months,
                                                     {"metro": s.get("metro"), "county": s.get("county"),
                                                      "seed": kw}, "related", ads))
            except Exception as e:
                snap["errors"].append({"keyword": kw, "error": str(e)[:300]})
                human(f"  ERROR  {kw} · {str(e)[:120]}")
                if "HTTP 429" in str(e) or "HTTP 403" in str(e):
                    human("  cuota/bloqueo Bing → paro el pase (reanudar con --only/--seeds)")
                    break
            time.sleep(0.5)
    finally:
        append_jsonl(ops_path(cfg, "kw-research.jsonl"), snap)
    rows = sorted(snap["rows"], key=lambda r: -(r["searchVolume"] or 0))
    nz = sum(1 for r in rows if r["searchVolume"])
    human(f"\n{len(rows)} filas · {nz} con volumen>0 · {len(snap['errors'])} errores · "
          f"{snap['calls']} llamadas → {ops_path(cfg, 'kw-research.jsonl')}")
    human(f"  {'vol/mes':>7} {'broad':>7}  intent         keyword")
    for r in rows[:20]:
        human(f"  {r['searchVolume'] if r['searchVolume'] is not None else '—':>7} "
              f"{r['broadVolume'] if r['broadVolume'] is not None else '—':>7}  {r['intent']:<13}  {r['keyword']}")
    if args.json:
        emit_json(snap)
    return 0 if rows else 2
