#!/usr/bin/env python3
"""
cmd_rank.py — `seo.py rank-track {add,list,run,history,diff}`.

Tracker de posición propia por query × motor. `run` usa Brave (barato) y
guarda filas planas en <ops_dir>/rank-history.jsonl; `history` fusiona
Brave + Google/Bing (JSONL de serp_watch, sin gastar) + GSC (gsc_queries.py
o aeo-gsc-watch) en una matriz por query; `diff` compara los dos últimos
pases. `add` edita queries.tracking del config conservando las claves `_x`.
"""

from __future__ import annotations

from collections import defaultdict

from cmd_serp import query_set, run_serp
from core import (append_jsonl, emit_json, human, ops_path, read_jsonl, save_project, today)


def register(sub, common):
    p = sub.add_parser("rank-track", parents=[common], help="tracker de posición propia")
    s = p.add_subparsers(dest="sub", metavar="{add,list,run,history,diff}")
    a = s.add_parser("add", parents=[common]); a.add_argument("query"); a.set_defaults(func=run_add)
    s.add_parser("list", parents=[common]).set_defaults(func=run_list)
    r = s.add_parser("run", parents=[common])
    r.add_argument("--engine", choices=["brave", "google", "bing"], default="brave")
    r.add_argument("--queries", choices=["money", "brand", "tracking", "all"], default="all")
    r.add_argument("--depth", type=int, default=20)
    r.set_defaults(func=run_run)
    h = s.add_parser("history", parents=[common])
    h.add_argument("--query", help="subcadena")
    h.add_argument("--last", type=int, default=1, help="nº de pases anteriores a mostrar")
    h.set_defaults(func=run_history)
    d = s.add_parser("diff", parents=[common])
    d.add_argument("--engine", default="brave")
    d.set_defaults(func=run_diff)
    p.set_defaults(func=lambda a, c: (human("uso: rank-track {add,list,run,history,diff}"), 1)[1])


def run_add(args, cfg) -> int:
    q = args.query.strip().lower()
    tr = cfg["queries"].setdefault("tracking", [])
    if q in tr or q in query_set(cfg, "all"):
        human(f"ya existe: {q}")
        return 0
    tr.append(q)
    if not args.dry_run:
        save_project(cfg)
    human(f"añadida a queries.tracking: {q} ({len(tr)} en tracking)")
    return 0


def run_list(args, cfg) -> int:
    for s in ("money", "brand", "tracking"):
        human(f"[{s}]")
        for q in cfg["queries"].get(s, []):
            human(f"  {q}")
    if args.json:
        emit_json(cfg["queries"])
    return 0


def run_run(args, cfg) -> int:
    queries = query_set(cfg, args.queries)
    if args.only:
        queries = [q for q in queries if args.only.lower() in q.lower()]
    if args.limit:
        queries = queries[:args.limit]
    snap = run_serp(cfg, queries, args.engine, args.depth, args.force, args.dry_run, 0,
                    label="rank-track run")
    if args.dry_run:
        return 0
    n = 0
    for r in snap["results"]:
        if not r.get("ok"):
            continue
        append_jsonl(ops_path(cfg, "rank-history.jsonl"),
                     {"ts": snap["ts"], "engine": r["engine"], "query": r["query"],
                      "geo": r.get("geo"), "rank": r.get("our_rank"), "url": r.get("our_url"),
                      "top3": [it["domain"] for it in r.get("items", [])[:3]], "n": r.get("n")})
        n += 1
    human(f"rank-history: {n} filas → {ops_path(cfg, 'rank-history.jsonl')}")
    if args.json:
        emit_json(snap)
    return 0 if n else 2


# ----------------------------------------------------------------- matriz --

def _series(cfg: dict, engine: str) -> dict[str, list[dict]]:
    """{query: [filas ordenadas por ts]} de rank-history para un motor."""
    out = defaultdict(list)
    for r in read_jsonl(ops_path(cfg, "rank-history.jsonl")):
        if r.get("engine") == engine:
            out[r["query"]].append(r)
    for v in out.values():
        v.sort(key=lambda r: r["ts"])
    return out


def _serp_watch_latest(cfg: dict) -> tuple[dict, str | None]:
    """{(engine, query): our_rank} del último snapshot de serp_watch.py."""
    path = (cfg.get("sources") or {}).get("serp_watch_jsonl")
    snaps = read_jsonl(path) if path else []
    if not snaps:
        return {}, None
    last = snaps[-1]
    return {(r.get("engine"), r.get("query")): r.get("our_rank")
            for r in last.get("results", []) if "error" not in r}, last.get("ts")


def _gsc_latest(cfg: dict) -> tuple[dict, str | None, str]:
    """{query: {position, impressions, clicks, page}} de gsc_queries.py, o de aeo-gsc-watch."""
    src = cfg.get("sources") or {}
    snaps = read_jsonl(src.get("gsc_queries_jsonl") or "")
    if snaps:
        last = snaps[-1]
        best: dict = {}
        for row in last.get("rows", []):        # varias páginas por query → la de más impresiones
            q = row["query"]
            if q not in best or row.get("impressions", 0) > best[q].get("impressions", 0):
                best[q] = row
        return best, last.get("ts"), "gsc_queries"
    snaps = read_jsonl(src.get("gsc_watch_jsonl") or "")
    if snaps:
        last = snaps[-1]
        return ({r["query"]: {"position": r.get("position"), "impressions": r.get("impressions", 0),
                              "clicks": r.get("clicks", 0), "page": (r.get("pages") or [None])[0]}
                 for r in last.get("results", [])}, last.get("ts"), "aeo-gsc-watch")
    return {}, None, "none"


def rank_matrix(cfg: dict, last: int = 1) -> dict:
    brave = _series(cfg, "brave")
    sw, sw_ts = _serp_watch_latest(cfg)
    gsc, gsc_ts, gsc_src = _gsc_latest(cfg)
    rows = []
    for q in query_set(cfg, "all"):
        b = brave.get(q, [])
        cur = b[-1] if b else None
        prev = b[-1 - last] if len(b) > last else None
        g = gsc.get(q) or {}
        rows.append({"query": q,
                     "brave": cur["rank"] if cur else None, "brave_ts": cur["ts"] if cur else None,
                     "brave_prev": prev["rank"] if prev else None,
                     "google": sw.get(("google", q)), "bing": sw.get(("bing", q)),
                     "gsc_pos": g.get("position"), "gsc_imp": g.get("impressions"),
                     "gsc_page": g.get("page")})
    return {"ts": today(), "sources": {"brave": "rank-history.jsonl", "serpapi_ts": sw_ts,
                                        "gsc": gsc_src, "gsc_ts": gsc_ts}, "rows": rows}


def _fmt(v) -> str:
    return "—" if v in (None, "") else str(v)


def run_history(args, cfg) -> int:
    m = rank_matrix(cfg, args.last)
    rows = [r for r in m["rows"] if not args.query or args.query.lower() in r["query"].lower()]
    if not rows:
        human("sin filas")
        return 2
    human(f"matriz · brave=rank-history · google/bing=serp_watch {m['sources']['serpapi_ts'] or '—'} · "
          f"gsc={m['sources']['gsc']} {m['sources']['gsc_ts'] or '—'}")
    human(f"  {'brave':>5} {'prev':>5} {'goog':>5} {'bing':>5} {'gsc':>5} {'imp':>5}  query")
    for r in rows:
        human(f"  {_fmt(r['brave']):>5} {_fmt(r['brave_prev']):>5} {_fmt(r['google']):>5} "
              f"{_fmt(r['bing']):>5} {_fmt(r['gsc_pos']):>5} {_fmt(r['gsc_imp']):>5}  {r['query']}")
    if args.json:
        emit_json(m)
    return 0


def diff(cfg: dict, engine: str) -> dict:
    series = _series(cfg, engine)
    ts_all = sorted({r["ts"] for v in series.values() for r in v})
    if len(ts_all) < 2:
        return {"engine": engine, "ts_prev": ts_all[-1] if ts_all else None, "ts_cur": None, "rows": []}
    ts_prev, ts_cur = ts_all[-2], ts_all[-1]
    rows = []
    for q, v in series.items():
        cur = next((r for r in reversed(v) if r["ts"] == ts_cur), None)
        prev = next((r for r in reversed(v) if r["ts"] == ts_prev), None)
        if not cur:
            continue
        c, p = cur["rank"], prev["rank"] if prev else None
        if prev is None:
            ev = "new_query"
        elif p is None and c is None:
            ev = "same"
        elif p is None:
            ev = "entered"
        elif c is None:
            ev = "left"
        elif c < p:
            ev = "up"
        elif c > p:
            ev = "down"
        else:
            ev = "same"
        rows.append({"engine": engine, "query": q, "prev": p, "cur": c,
                     "delta": (p - c) if (p is not None and c is not None) else None, "event": ev})
    order = {"entered": 0, "up": 1, "down": 2, "left": 3, "new_query": 4, "same": 5}
    rows.sort(key=lambda r: (order[r["event"]], r["query"]))
    return {"engine": engine, "ts_prev": ts_prev, "ts_cur": ts_cur, "rows": rows}


def run_diff(args, cfg) -> int:
    d = diff(cfg, args.engine)
    if not d["rows"]:
        human(f"diff {args.engine}: hacen falta ≥2 pases (último: {d['ts_prev'] or '—'})")
        return 2
    human(f"diff {args.engine} · {d['ts_prev']} → {d['ts_cur']}")
    for r in d["rows"]:
        human(f"  {r['event']:<9} {_fmt(r['prev']):>4} → {_fmt(r['cur']):<4} {r['query']}")
    if args.json:
        emit_json(d)
    return 0
