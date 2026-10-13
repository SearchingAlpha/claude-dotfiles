"""The Graph tab: omnigraph stores on this machine, what is in them, and the ops
the omnigraph mod saw on them. Read-only, like the rest of the board.

A store is the folder the mod's pane reads (mods/omnigraph/README.md, "The store
format"): manifest.json, nodes.jsonl, edges.jsonl. It is looked for under every
root the board knows: the roots omnigraph kept a feed for, enrolled repos and
their track worktrees, and running sessions' folders. Under each it tries the
mod's configured `store` option (user settings, `pluginConfigs`), then the store
of each preset in mods/omnigraph/presets/ (SupplyChainAgents' ontology/build),
with that preset's record keys. The feed is the mod's own `$.store` file.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from pathlib import Path
from typing import Optional

from . import identity, store

MOD = Path(__file__).resolve().parents[3] / "mods" / "omnigraph"
PLUGIN_IDS = ("omnigraph@claude-dotfiles", "omnigraph@inline")
FILE_MAX = 4 * 1024 * 1024      # the mod reads no store file past this; neither does the board
FEED_MAX = 50
DEFAULTS = {
    "store": "graphs/dev.omni", "storeLabel": "", "manifestFile": "manifest.json",
    "nodesFile": "nodes.jsonl", "edgesFile": "edges.jsonl", "versionKey": "version",
    "nodeKeys": "id label props", "edgeKeys": "from type to", "nameKeys": "name title",
    "statusKey": "status", "sourceKey": "source", "conflictsKey": "open_conflicts",
    "queueStatus": "proposed",
}
CONTROL = re.compile("[\u0000-\u001f\u007f-\u009f\u2028\u2029]")

_lock = threading.Lock()
_parsed: dict = {}      # store dir -> (file signature, graph)
_feed_cache: dict = {}  # $.store file -> (mtime_ns, size, {root: [ops]})


# --- adapters --------------------------------------------------------------------------

def _options_of(raw) -> dict:
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        for k in DEFAULTS:
            v = raw.get(k)
            if isinstance(v, str):
                out[k] = v.strip() or (DEFAULTS[k] if k in ("store", "nodesFile", "edgesFile",
                                                           "versionKey") else "")
    return out


def _adapter(options: dict, preset: Optional[str]) -> dict:
    words = lambda k: options[k].split()
    node_keys = (words("nodeKeys") + ["id", "label", "props"][len(words("nodeKeys")):])[:3]
    edge_keys = (words("edgeKeys") + ["from", "type", "to"][len(words("edgeKeys")):])[:3]
    return {"preset": preset, "store": options["store"], "label": options["storeLabel"],
            "files": (options["manifestFile"], options["nodesFile"], options["edgesFile"]),
            "version_key": options["versionKey"], "node_keys": tuple(node_keys),
            "edge_keys": tuple(edge_keys), "name_keys": tuple(words("nameKeys")),
            "status_key": options["statusKey"], "source_key": options["sourceKey"],
            "conflicts_key": options["conflictsKey"], "queue": tuple(words("queueStatus"))}


def adapters() -> list[dict]:
    """The configured options first, then one adapter per preset."""
    settings = store.read_json(store.claude_dir() / "settings.json", {}) or {}
    configs = settings.get("pluginConfigs") if isinstance(settings, dict) else None
    configured = {}
    for pid in PLUGIN_IDS:
        entry = configs.get(pid) if isinstance(configs, dict) else None
        if isinstance(entry, dict) and isinstance(entry.get("options"), dict):
            configured = entry["options"]
            break
    out = [_adapter(_options_of(configured), None)]
    for p in sorted((MOD / "presets").glob("*.json")):
        if p.name.endswith(".private.example.json"):
            continue
        preset = store.read_json(p, {}) or {}
        if isinstance(preset, dict) and isinstance(preset.get("options"), dict):
            out.append(_adapter(_options_of(preset["options"]), p.stem))
    return out


# --- the feed --------------------------------------------------------------------------

def _clean(text) -> str:
    return CONTROL.sub(" ", text).strip() if isinstance(text, str) else ""


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _no_constant(name):
    raise ValueError(f"{name} is not JSON")


def _loads(text: str):
    """json.loads as JavaScript's JSON.parse reads it: NaN and Infinity are not JSON
    (Python takes them, and would write them back out where the browser's parser fails)."""
    return json.loads(text, parse_constant=_no_constant)


def _op(v) -> Optional[dict]:
    """One stored op made safe to send (as the mod's opOf), or None."""
    if not isinstance(v, dict) or _num(v.get("t")) is None or not isinstance(v.get("op"), str) \
            or not isinstance(v.get("ok"), bool):
        return None
    focus = v.get("focus") if isinstance(v.get("focus"), dict) else {}
    touched = v.get("touched") if isinstance(v.get("touched"), list) else []
    return {
        "t": v["t"], "op": _clean(v["op"])[:80] or "op", "ok": v["ok"],
        "store": _clean(v["store"]) if isinstance(v.get("store"), str) else None,
        "ms": max(0, _num(v.get("ms")) or 0), "dNodes": _num(v.get("dNodes")),
        "dEdges": _num(v.get("dEdges")), "changed": _num(v.get("changed")),
        "touched": [x for x in touched if isinstance(x, str)][:12],
        "focus": {"center": focus["center"], "selected": focus["selected"]}
        if isinstance(focus.get("center"), str) and isinstance(focus.get("selected"), str) else None,
        "error": _clean(v["error"])[:200] if isinstance(v.get("error"), str) else None,
        "session": v["session"] if isinstance(v.get("session"), str) else None,
        "background": v.get("isBackground") is True,
    }


def feeds() -> dict:
    """{root: [op, ...]} from every omnigraph `$.store` file (the installed mod's and
    dev copies'), oldest first, the last FEED_MAX per root."""
    merged: dict = {}
    for p in sorted((store.claude_dir() / "plugins" / "store").glob("omnigraph_*.json")):
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_size > FILE_MAX:
            continue
        hit = _feed_cache.get(str(p))
        if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
            by_root = hit[2]
        else:
            try:
                data = _loads(p.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                data = {}
            by_root = {}
            for k, v in (data.items() if isinstance(data, dict) else []):
                if k.startswith("feed:") and isinstance(v, list):
                    by_root[k[5:]] = [op for op in map(_op, v) if op]
            _feed_cache[str(p)] = (st.st_mtime_ns, st.st_size, by_root)
        for root, ops in by_root.items():
            merged.setdefault(root, []).extend(ops)
    out = {}
    for root, ops in merged.items():
        seen, uniq = set(), []
        for op in sorted(ops, key=lambda o: o["t"]):
            key = (op["t"], op["op"], op["session"])
            if key not in seen:
                seen.add(key)
                uniq.append(op)
        out[root] = uniq[-FEED_MAX:]
    return out


# --- discovery -------------------------------------------------------------------------

def _git_top(path: Path) -> Optional[Path]:
    for p in (path, *path.parents):
        if (p / ".git").exists():
            return p
    return None


def roots(feed_roots) -> list[Path]:
    """Folders a store may sit under, each once: feed roots, enrolled repos and their
    tracks' worktrees, then the folders of running sessions (and their repo's top)."""
    found: list[Path] = []
    seen: set = set()

    def add(raw):
        if not raw or not str(raw).startswith("/"):
            return
        p = Path(raw)
        try:
            real = p.resolve()
        except OSError:
            return
        if str(real) in seen or not real.is_dir():
            return
        seen.add(str(real))
        found.append(p)

    for r in feed_roots:
        add(r)
    repos = store.registered_repos()
    for r in repos:
        add(r.get("primary"))
    for r in repos:
        for t in store.tracks(r["common"]):
            if t.get("status") in store.ACTIVE:
                add(t.get("worktree"))
    for s in identity.registry():
        if s.get("live") and s.get("cwd"):
            add(s["cwd"])
            top = _git_top(Path(s["cwd"]))
            if top:
                add(str(top))
    return found


def _store_id(dir_: Path) -> str:
    return hashlib.sha1(str(dir_).encode()).hexdigest()[:12]


def discover(feed_roots=()) -> list[dict]:
    """Every store present under a known root: {id, dir, root, adapter}."""
    out, seen = [], set()
    ads = adapters()
    for root in roots(feed_roots):
        for a in ads:
            d = Path(a["store"]) if a["store"].startswith("/") else root / a["store"]
            manifest, nodes, _ = (d / f for f in a["files"])
            if not (manifest.is_file() or nodes.is_file()):
                continue
            real = d.resolve()
            if str(real) in seen:
                continue
            seen.add(str(real))
            out.append({"id": _store_id(real), "dir": real, "root": root, "adapter": a})
    return out


# --- reading a store ---------------------------------------------------------------------

def _id_of(v) -> Optional[str]:
    """A record's id as the mod spells it (JavaScript's String(n): 1.0 is "1")."""
    if isinstance(v, str) and v:
        return v
    if _num(v) is None:
        return None
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e21:
        return str(int(v))
    return str(v)


def _sources(v) -> list:
    items = v if isinstance(v, list) else [v]
    return [_clean(x) for x in items if isinstance(x, str) and _clean(x)][:20]


def _read(p: Path):
    """(text, problem): None text when the file is missing or too big."""
    try:
        if not p.is_file():   # missing, or an empty file option naming the store's own folder
            return None, None
        size = p.stat().st_size
    except OSError:
        return None, None
    if size > FILE_MAX:
        return None, f"{p.name} is over 4 MiB: not read"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return None, f"{p.name}: {e.strerror}"
    return text[1:] if text.startswith("\ufeff") else text, None


def parse(dir_: Path, a: dict) -> dict:
    """The store as the browser draws it, read the way the mod's parseGraph reads it."""
    mf, nf, ef = (dir_ / f for f in a["files"])
    problems, bad = [], 0
    texts = {}
    for key, p in (("manifest", mf), ("nodes", nf), ("edges", ef)):
        texts[key], problem = _read(p)
        if problem:
            problems.append(problem)
    manifest = None
    if texts["manifest"] is not None:
        try:
            manifest = _loads(texts["manifest"])
        except ValueError:
            manifest = None
        if not isinstance(manifest, dict):
            manifest, bad = None, bad + 1
    id_k, label_k, props_k = a["node_keys"]
    nodes, index = [], {}
    for line in (texts["nodes"] or "").split("\n"):
        if not line.strip():
            continue
        try:
            rec = _loads(line)
        except ValueError:
            bad += 1
            continue
        nid = _id_of(rec.get(id_k)) if isinstance(rec, dict) else None
        if nid is None:
            bad += 1
            continue
        props = rec if props_k == "." else rec.get(props_k) if isinstance(rec.get(props_k), dict) else {}
        name = next((_clean(props.get(k)) for k in a["name_keys"] if _clean(props.get(k))), "")
        node = {"id": nid, "label": _clean(rec.get(label_k)) or "?", "name": name or _clean(nid) or "?",
                "status": _clean(props.get(a["status_key"])) or None if a["status_key"] else None,
                "source": _sources(props.get(a["source_key"])) if a["source_key"] else [],
                "props": props}
        if nid in index:
            nodes[index[nid]] = node
        else:
            index[nid] = len(nodes)
            nodes.append(node)
    from_k, type_k, to_k = a["edge_keys"]
    edges, seen = [], set()
    for line in (texts["edges"] or "").split("\n"):
        if not line.strip():
            continue
        try:
            rec = _loads(line)
        except ValueError:
            bad += 1
            continue
        ok = isinstance(rec, dict)
        frm, to, typ = (_id_of(rec.get(k)) if ok else None for k in (from_k, to_k, type_k))
        if frm is None or to is None or typ is None:
            bad += 1
            continue
        edge = (frm, _clean(typ) or "?", to)
        if edge not in seen:
            seen.add(edge)
            edges.append(edge)
    stated = manifest.get(a["version_key"]) if manifest else None
    if isinstance(stated, (str, int, float)) and not isinstance(stated, bool) and _clean(str(stated)):
        version = _clean(str(stated))
    elif texts["nodes"] is not None or texts["edges"] is not None:
        version = hashlib.sha1(f"{texts['nodes'] or ''}\0{texts['edges'] or ''}".encode()).hexdigest()[:8]
    else:
        version = None
    if bad:
        problems.append(f"{bad} line{'s' if bad != 1 else ''} not read")
    conflicts = manifest.get(a["conflicts_key"]) if manifest and a["conflicts_key"] else None
    name = _clean(a["label"]) or (_clean(manifest.get("name")) if manifest else "") or dir_.name
    return {"name": name, "version": version, "nodes": nodes, "edges": edges,
            "labels": sorted({n["label"] for n in nodes}),
            "conflicts": [c for c in conflicts if isinstance(c, str)] if isinstance(conflicts, list) else [],
            "queued": sum(1 for n in nodes if n["status"] in a["queue"]),
            "queue_status": list(a["queue"]),
            "problem": "; ".join(problems) or None}


def _signature(dir_: Path, a: dict) -> tuple:
    sig = []
    for f in a["files"]:
        try:
            st = (dir_ / f).stat()
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(None)
    return (tuple(sig), json.dumps(a, sort_keys=True))


def load(entry: dict) -> dict:
    """parse(), kept until one of the store's files changes. `rev` names what was read
    (the files' mtimes and sizes, and the options): the browser fetches the store again
    when it moves, as a producer's version may not change with the content."""
    sig = _signature(entry["dir"], entry["adapter"])
    hit = _parsed.get(str(entry["dir"]))
    if hit and hit[0] == sig:
        return hit[1]
    g = dict(parse(entry["dir"], entry["adapter"]), rev=hashlib.sha1(repr(sig).encode()).hexdigest()[:12])
    _parsed[str(entry["dir"])] = (sig, g)
    return g


def _branch(root: Path) -> Optional[str]:
    git = root / ".git"
    head = git / "HEAD"
    if git.is_file():
        m = re.match(r"gitdir:\s*(.+)", git.read_text(errors="replace").strip())
        if not m:
            return None
        gd = Path(m.group(1))
        head = (gd if gd.is_absolute() else root / gd) / "HEAD"
    try:
        text = head.read_text().strip()
    except OSError:
        return None
    return text[len("ref: refs/heads/"):] if text.startswith("ref: refs/heads/") else text[:8] or None


# --- the API -------------------------------------------------------------------------------

def _who() -> dict:
    return {s["sessionId"]: s.get("name") or s["sessionId"][:8] for s in identity.registry()}


def listing() -> dict:
    """/api/graphs: every store with its counts and its root's feed, most recent op first."""
    with _lock:
        fd = feeds()
        names = _who()
        out = []
        for e in discover(list(fd)):
            g = load(e)
            ops = [dict(op, who=names.get(op["session"]) or (op["session"] or "")[:8])
                   for op in fd.get(str(e["root"]), [])
                   if op["store"] in (None, g["name"])]
            out.append({"id": e["id"], "name": g["name"], "root": str(e["root"]), "dir": str(e["dir"]),
                        "branch": _branch(e["root"]), "preset": e["adapter"]["preset"],
                        "version": g["version"], "rev": g["rev"], "nodes": len(g["nodes"]), "edges": len(g["edges"]),
                        "labels": len(g["labels"]), "queued": g["queued"], "conflicts": len(g["conflicts"]),
                        "problem": g["problem"], "feed": ops,
                        "last_op_at": ops[-1]["t"] if ops else None})
        # the store with the newest op first, then in the order roots() found them
        out.sort(key=lambda s: -(s["last_op_at"] or 0))
        return {"stores": out}


def graph(store_id: str) -> Optional[dict]:
    """/api/graph?id=: one discovered store, whole. Only ids discover() hands out are served."""
    with _lock:
        for e in discover(list(feeds())):
            if e["id"] == store_id:
                g = load(e)
                return {"id": e["id"], "root": str(e["root"]), "dir": str(e["dir"]),
                        "preset": e["adapter"]["preset"], **g}
    return None
