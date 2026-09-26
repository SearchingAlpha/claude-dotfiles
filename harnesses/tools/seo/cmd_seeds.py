#!/usr/bin/env python3
"""
cmd_seeds.py — `seo.py seeds gen`: candidatos de keyword por metro, sin red.

Producto cartesiano recortado de plantillas del config (heads × modifiers ×
geo_forms, cantidades, proyectos, informacionales) más las keywords de ads
(--include-ads) y, opcionalmente, ciudades de la BD (--from-db N). Filtra
negativas por token, deduplica y recorta a --max por prioridad
(metro por orden × tipo × intención). Salida: <ops_dir>/seeds.json.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from core import classify_intent, emit_json, has_negative, human, load_negatives, ops_path, today

INTENT_PRIO = {"transactional": 0, "commercial": 1, "navigational": 2, "informational": 3}


def register(sub, common):
    p = sub.add_parser("seeds", parents=[common], help="generar seeds de keywords (sin red)")
    s = p.add_subparsers(dest="sub")
    g = s.add_parser("gen", parents=[common])
    g.add_argument("--max", type=int, default=150)
    g.add_argument("--from-db", type=int, default=0, metavar="N",
                   help="añadir las N ciudades activas más pobladas de geo.metros_db")
    g.add_argument("--include-ads", action="store_true", help="incluir seeds.ads_keywords_csv")
    g.add_argument("--metro", help="solo este metro (id o nombre)")
    g.set_defaults(func=run_gen)
    p.set_defaults(func=lambda a, c: (human("uso: seeds gen"), 1)[1])


def metros(cfg: dict, from_db: int) -> list[dict]:
    out = [dict(m) for m in cfg["geo"].get("metros", [])]
    seen = {m.get("name", "").lower() for m in out}
    db = cfg["geo"].get("metros_db") or {}
    if from_db and db.get("path") and Path(db["path"]).exists():
        cx = sqlite3.connect(db["path"])
        for name, county in cx.execute(db["sql"], (from_db,)).fetchall():
            if name.lower() not in seen:
                out.append({"id": name.lower().replace(" ", "-"), "name": name,
                            "county": county, "cities": [], "from_db": True})
                seen.add(name.lower())
    return out


def _geo_forms(cfg: dict, place: str) -> list[str]:
    return [f.replace("{metro}", place) for f in cfg["seeds"].get("geo_forms", ["{metro}"])]


def generate(cfg: dict, from_db: int = 0, include_ads: bool = False,
             only_metro: str | None = None) -> list[dict]:
    s = cfg.get("seeds") or {}
    mods = s.get("modifiers") or {}
    negs = load_negatives(cfg)
    state = (cfg["geo"].get("state") or "").lower()
    rows, seen = [], set()

    def add(kw, metro, county, seed, origin, tier):
        kw = " ".join(kw.lower().split())
        if kw in seen or has_negative(kw, negs):
            return
        seen.add(kw)
        rows.append({"keyword": kw, "metro": metro, "county": county, "seed": seed,
                     "intent_guess": classify_intent(kw, cfg), "origin": origin, "_tier": tier})

    # Tier (metro_idx, kind, place_idx). kind: 0 head sin geo · 1 head+estado · 2 head×mod×metro
    # · 3 cantidades · 4 proyectos · 5 informacional. Los kinds 0-1 son los que traen volumen
    # en Bing (la long-tail hiperlocal suele dar 0): van primero y sin metro.
    for head in s.get("heads", []):
        add(head, None, None, head, "head", (-1, 0, 0))
        for mod in [*mods.get("transactional", []), *mods.get("commercial", [])]:
            add(f"{head} {mod}", None, None, head, "head", (-1, 0, 1))
            add(f"{head} {mod} {state}", None, None, head, "head", (-1, 1, 0))
    for qty in s.get("quantities", []):
        for tail in ("delivered", "delivered cost"):
            add(f"{qty} {tail}", None, None, qty, "head", (-1, 0, 2))
            add(f"{qty} {tail} {state}", None, None, qty, "head", (-1, 1, 1))
    for info in mods.get("informational", []):
        add(info, None, None, info, "head", (-1, 0, 3))
        add(f"{info} {state}", None, None, info, "head", (-1, 1, 2))

    for mi, m in enumerate(metros(cfg, from_db)):
        if only_metro and only_metro.lower() not in (str(m.get("id", "")).lower(), m["name"].lower()):
            continue
        places = [m["name"], *(m.get("cities") or [])]
        for pi, place in enumerate(places):
            for gi, geo in enumerate(_geo_forms(cfg, place)):
                pp = pi * 10 + gi                        # forma "{metro}" antes que "{metro} fl"
                for head in s.get("heads", []):
                    for mod in mods.get("transactional", []):
                        add(f"{head} {mod} {geo}", m["name"], m.get("county"), head, "template", (mi, 2, pp))
                    for mod in mods.get("commercial", []):
                        add(f"{head} {mod} {geo}", m["name"], m.get("county"), head, "template", (mi, 2, pp))
                for qty in s.get("quantities", []):
                    for tail in ("delivered", "delivered cost", "delivered price"):
                        add(f"{qty} {tail} {geo}", m["name"], m.get("county"), qty, "template", (mi, 3, pp))
                for proj in s.get("projects", []):
                    for tail in ("delivered", "delivery"):
                        add(f"concrete for {proj} {tail} {geo}", m["name"], m.get("county"), proj,
                            "template", (mi, 4, pp))
            if pi == 0:
                for info in mods.get("informational", []):
                    add(f"{info} {m.get('county') or place} county", m["name"], m.get("county"), info,
                        "template", (mi, 5, 0))

    if include_ads and s.get("ads_keywords_csv") and Path(s["ads_keywords_csv"]).exists():
        with open(s["ads_keywords_csv"], encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                kw = (r.get("Keyword") or "").strip().strip('"[]+')
                if kw:
                    add(kw, None, None, kw, "ads", (-1, 0, 4))
    return rows


def select(rows: list[dict], max_n: int, per_round: int = 4) -> list[dict]:
    """Recorte con diversidad: round-robin entre grupos (metro, kind) por prioridad.

    Dentro de cada grupo: intención (transactional > commercial > …) y place_idx.
    Así 150 seeds cubren heads, estado, varios metros y tipos, no solo el primer metro.
    """
    groups: dict[tuple, list[dict]] = {}
    for r in sorted(rows, key=lambda r: (r["_tier"][0], r["_tier"][1],
                                         INTENT_PRIO.get(r["intent_guess"], 9), r["_tier"][2])):
        groups.setdefault((r["_tier"][0], r["_tier"][1]), []).append(r)
    out: list[dict] = []
    while len(out) < max_n and any(groups.values()):
        for k in list(groups):
            take, groups[k] = groups[k][:per_round], groups[k][per_round:]
            out.extend(take)
            if len(out) >= max_n:
                break
    return out[:max_n]


def run_gen(args, cfg) -> int:
    rows = generate(cfg, args.from_db, args.include_ads, args.metro)
    total = len(rows)
    rows = select(rows, args.max)
    for r in rows:
        r.pop("_tier", None)
    out = {"generated": today(), "n": len(rows), "candidates_total": total, "seeds": rows}
    if not args.dry_run:
        path = ops_path(cfg, "seeds.json")
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        human(f"seeds: {len(rows)}/{total} candidatos → {path}")
    else:
        human(f"seeds: {len(rows)}/{total} candidatos (dry-run)")
    by, bym = {}, {}
    for r in rows:
        by[r["intent_guess"]] = by.get(r["intent_guess"], 0) + 1
        bym[r["metro"] or "(sin geo)"] = bym.get(r["metro"] or "(sin geo)", 0) + 1
    human("  por intención: " + ", ".join(f"{k}={v}" for k, v in sorted(by.items())))
    human("  por metro:     " + ", ".join(f"{k}={v}" for k, v in bym.items()))
    for r in rows[:12]:
        human(f"  {r['intent_guess']:<13} {r['keyword']}")
    if args.json:
        emit_json(out)
    return 0
