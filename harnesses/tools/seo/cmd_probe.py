#!/usr/bin/env python3
"""
cmd_probe.py — `seo.py probe`: sondea cada fuente y guarda las respuestas crudas.

Es el paso 1 obligatorio en un proyecto nuevo: decide si Bing acepta fechas ISO
o /Date(ms)/, qué campos devuelve GetKeyword, si GetRelatedKeywords trae volumen
y si Brave/SerpAPI responden con la clave dada. Salida → ops/seo/probe-<fecha>.json.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from clients import (bingkw_keyword, bingkw_keyword_stats, bingkw_related, brave_search,
                     serpapi_left)
from core import emit_json, has_secret, human, ops_path, quota_charge, secret, today


def register(sub, common):
    p = sub.add_parser("probe", parents=[common],
                       help="sondear Bing kw / Brave / SerpAPI y guardar shapes crudos")
    p.add_argument("--bing-date-style", choices=["iso", "ms", "both"], default="both")
    p.add_argument("--keyword", default=None, help="keyword de prueba (default: primera money query)")
    p.set_defaults(func=run)


def _try(label: str, fn, out: dict, cfg: dict | None = None, source: str | None = None) -> None:
    """Ejecuta fn; guarda ok/error; carga cuota solo si la llamada llegó a responder."""
    try:
        res = fn()
        out[label] = {"ok": True, "data": res}
        human(f"  OK   {label}")
    except BaseException as e:            # SystemExit de secret() incluido
        out[label] = {"ok": False, "error": str(e)}
        human(f"  FAIL {label} · {e}")
        if not str(e).startswith("red:") and cfg and source:
            quota_charge(cfg, source, 1)  # HTTP 4xx/5xx: la API sí contó la llamada
        return
    if cfg and source:
        quota_charge(cfg, source, 1)


def run(args, cfg) -> int:
    geo = cfg["geo"]
    kw = args.keyword or cfg["queries"]["money"][0]
    end = date.today() - timedelta(days=3)
    start = end - timedelta(days=90)
    out = {"ts": today(), "keyword": kw, "window": [str(start), str(end)], "sources": {}}
    src = out["sources"]
    lang = geo.get("bing_language") or f"{geo['language']}-{geo['country']}"

    human(f"probe · keyword «{kw}» · ventana {start}..{end}")
    if args.dry_run:
        human("  --dry-run: sin llamadas")
        return 0

    human("[bing_wmt keyword]")
    if has_secret(cfg, "bing_wmt"):
        key = secret(cfg, "bing_wmt")
        styles = ["iso", "ms"] if args.bing_date_style == "both" else [args.bing_date_style]
        for st in styles:
            _try(f"bing_GetKeyword_{st}",
                 lambda st=st: bingkw_keyword(key, kw, geo["country"], lang, start, end, st),
                 src, cfg, "bing_kw")
        _try("bing_GetKeywordStats",
             lambda: bingkw_keyword_stats(key, kw, geo["country"], lang), src, cfg, "bing_kw")
        ok_style = next((s for s in styles if src.get(f"bing_GetKeyword_{s}", {}).get("ok")), None)
        _try("bing_GetRelatedKeywords",
             lambda: bingkw_related(key, kw, geo["country"], lang, start, end, ok_style or "iso"),
             src, cfg, "bing_kw")
        out["bing_date_style_ok"] = ok_style
    else:
        src["bing_wmt"] = {"ok": False, "error": "secreto bing_wmt ausente"}
        human("  SKIP bing_wmt (sin clave)")

    human("[brave]")
    if has_secret(cfg, "brave"):
        _try("brave", lambda: brave_search(secret(cfg, "brave"), kw, count=5,
                                           country=geo["country"], lang=geo["language"]),
             src, cfg, "brave")
    else:
        src["brave"] = {"ok": False, "error": "secreto brave ausente"}
        human("  SKIP brave (sin clave) → api.search.brave.com, plan Free")

    human("[serpapi]")
    if has_secret(cfg, "serpapi"):
        _try("serpapi_account", lambda: {"total_searches_left": serpapi_left(secret(cfg, "serpapi"))}, src)
    else:
        src["serpapi"] = {"ok": False, "error": "secreto serpapi ausente"}
        human("  SKIP serpapi (sin clave)")

    path = ops_path(cfg, f"probe-{today()}.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    human(f"\ncrudo → {path}")

    # Resumen de lo que importa para kw-research
    bk = next((src[k] for k in src if k.startswith("bing_GetKeyword_") and src[k]["ok"]), None)
    if bk:
        d = bk["data"]
        human(f"Bing GetKeyword OK (estilo {out.get('bing_date_style_ok')}): "
              f"campos={list(d.keys()) if isinstance(d, dict) else type(d).__name__}")
    else:
        human("Bing GetKeyword: sin respuesta válida → kw-research degradará a searchVolume=null")
    rel = src.get("bing_GetRelatedKeywords", {})
    if rel.get("ok"):
        rows = rel["data"] if isinstance(rel["data"], list) else []
        human(f"Bing GetRelatedKeywords OK: {len(rows)} filas · "
              f"campos={list(rows[0].keys()) if rows else '—'}")
    if args.json:
        emit_json(out)
    return 0
