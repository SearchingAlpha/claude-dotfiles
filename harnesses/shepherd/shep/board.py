"""The board: one snapshot of every enrolled repo, its tracks and the sessions
working in it. Local git and files only — no network (reviews come from the
state the poller stores)."""

from __future__ import annotations

import os
import time
from typing import Optional

from . import gitx, hooks, identity, store

_cache: dict = {}


def _session_view(a: Optional[dict], sessions: dict) -> dict:
    a = a or {}
    sid = a.get("session_id")
    s = sessions.get(sid) if sid else None
    live = identity.agent_live(a) if sid else False
    return {"session_id": sid, "name": (s or {}).get("name") or a.get("name"),
            "live": live, "status": (s or {}).get("status") if live else "ended" if sid else None,
            "title": identity.title(sid) if sid else None,
            "cwd": (s or {}).get("cwd")}


def _last_activity(entries: list[dict]) -> dict:
    last: dict = {}
    for e in entries:
        k = e.get("track") or f"@{e.get('wt')}"
        if e.get("at", 0) >= last.get(k, {}).get("at", 0):
            last[k] = e
    return last


def repo_view(common: str, sessions: dict) -> Optional[dict]:
    cfg = store.load_config(common)
    if not cfg:
        return None
    primary = os.path.dirname(common)       # the main checkout holds the common git dir
    repo = gitx.repo_of(primary)
    if repo is None:
        return None
    remote, main = cfg.get("remote"), cfg.get("main") or "main"
    base = gitx.base_ref(primary, remote, main)
    heads = gitx.branch_heads(primary)
    wts = gitx.worktrees(primary)
    tracks = store.tracks(common)
    activity = store.tail_jsonl(store.activity_path(common), 262144)
    last = _last_activity(activity)

    cards = []
    tracked_paths = set()
    for t in tracks:
        if t.get("status") not in store.ACTIVE:
            continue
        wt = t.get("worktree")
        tracked_paths.add(wt)
        sha = heads.get(t.get("branch") or "")
        warnings = []
        ahead = behind = 0
        last_commit = None
        if sha:
            ahead, behind = gitx.ahead_behind(primary, sha, base)
            lc = gitx.log(primary, sha, 1)
            last_commit = lc[0] if lc else None
        else:
            warnings.append("branch is gone")
        dirty = []
        if wt and os.path.isdir(wt):
            dirty = gitx.dirty(wt)
        elif wt:
            warnings.append("worktree missing — `shepherd resume`")
        agent = _session_view(t.get("agent"), sessions)
        if not agent["session_id"]:
            warnings.append("unclaimed")
        elif not agent["live"] and t.get("status") in store.LIVE_WORK:
            warnings.append("owner session ended")
        rv = t.get("review") or {}
        if t.get("status") == "merged":
            warnings.append("merged — run `shepherd finish`")
        if behind and t.get("status") in store.LIVE_WORK:
            warnings.append(f"{behind} behind {base}")
        if rv.get("error"):
            warnings.append(f"review: {rv['error'][:120]}" + (f" ({rv['hint']})" if rv.get("hint") else ""))
        act = last.get(t["name"])
        changed = hooks.changed_on(repo, t, heads) if sha else []
        cards.append({
            "name": t["name"], "goal": t.get("goal"), "status": t.get("status"),
            "note": t.get("note"), "branch": t.get("branch"), "worktree": wt,
            "owns": t.get("owns") or [], "avoid": t.get("avoid") or [], "live": t.get("live") or [],
            "agent": agent, "ahead": ahead, "behind": behind, "dirty": dirty[:50],
            "dirty_count": len(dirty), "changed": changed[:200], "changed_count": len(changed),
            "last_commit": last_commit, "last_activity": act.get("at") if act else None,
            "last_file": act.get("file") if act else None, "review": rv or None,
            "warnings": warnings, "created_at": t.get("created_at"), "updated_at": t.get("updated_at"),
            "sha": sha,
        })

    protected = cfg.get("protected") or []
    lane_warnings = []
    unpushed = 0
    if remote and gitx.rev(primary, f"refs/remotes/{remote}/{main}") and main in heads:
        unpushed, _ = gitx.ahead_behind(primary, heads[main], f"{remote}/{main}")
    primary_repo = gitx.repo_of(primary)
    main_dirty = gitx.dirty(primary) if primary_repo and primary_repo.branch in protected else []
    if main_dirty:
        lane_warnings.append(f"{len(main_dirty)} uncommitted file(s) on protected "
                             f"`{primary_repo.branch}` in the main checkout")
    if unpushed:
        lane_warnings.append(f"local `{main}` has {unpushed} commit(s) not on {remote}/{main}")
    untracked = []
    for w in wts:
        if w["path"] == primary or w["path"] in tracked_paths or w.get("bare"):
            continue
        untracked.append({"path": w["path"], "branch": w.get("branch"),
                          "dirty_count": len(gitx.dirty(w["path"])) if os.path.isdir(w["path"]) else 0})
    owners = {(t.get("agent") or {}).get("session_id") for t in tracks
              if t.get("status") in store.ACTIVE}
    on_main = []
    for s in sessions.values():
        # a track owner's registry cwd stays where it launched; it works in its worktree
        if not s.get("live") or not s.get("cwd") or s.get("sessionId") in owners:
            continue
        cwd = os.path.realpath(s["cwd"])
        if cwd == primary or cwd.startswith(primary + os.sep):
            inside_track = any(p and (cwd == p or cwd.startswith(p + os.sep)) for p in tracked_paths)
            if not inside_track and primary_repo and primary_repo.branch in protected:
                on_main.append({"name": s.get("name"), "session_id": s.get("sessionId"),
                                "status": s.get("status")})
    if on_main and cfg.get("mode") == "observe":
        lane_warnings.append(f"{len(on_main)} session(s) working in the main checkout")

    return {"name": repo.name, "primary": primary, "common": common, "mode": cfg.get("mode"),
            "main": main, "base": base, "remote": remote, "protected": protected,
            "unpushed": unpushed, "main_dirty": len(main_dirty), "on_main": on_main,
            "tracks": cards, "untracked": untracked, "warnings": lane_warnings,
            "overlaps": overlaps(primary, common, cards)}


def overlaps(primary: str, common: str, cards: list[dict]) -> list[dict]:
    """Pairs of working tracks touching the same files; merge-tree runs only for
    those pairs, and its verdict is cached by the two shas."""
    work = [c for c in cards if c["status"] in store.LIVE_WORK]
    cache_path = store.state_dir(common) / "cache" / "mergetree.json"
    cache = store.read_json(cache_path, {}) or {}
    dirty_cache = False
    res = []
    for i, a in enumerate(work):
        fa = set(a["changed"]) | set(a["dirty"])
        for b in work[i + 1:]:
            fb = set(b["changed"]) | set(b["dirty"])
            shared = sorted(fa & fb)
            if not shared:
                continue
            conflicts = None
            if a.get("sha") and b.get("sha") and set(a["changed"]) & set(b["changed"]):
                key = ":".join(sorted([a["sha"], b["sha"]]))
                if key not in cache:
                    cache[key] = gitx.predict_conflicts(primary, a["sha"], b["sha"])
                    dirty_cache = True
                conflicts = cache[key]
            res.append({"a": a["name"], "b": b["name"], "shared": shared[:50],
                        "conflicts": conflicts})
    if dirty_cache:
        if len(cache) > 500:
            cache = dict(list(cache.items())[-300:])
        try:
            store.write_json(cache_path, cache)
        except OSError:
            pass
    return res


def snapshot(ttl: float = 2.0) -> dict:
    hit = _cache.get("board")
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    sessions = identity.sessions_by_id()
    repos = []
    for r in store.registered_repos():
        try:
            v = repo_view(r["common"], sessions)
        except Exception as e:      # one broken repo must not blank the board
            v = {"name": os.path.basename(r.get("primary") or r["common"]), "error": str(e),
                 "tracks": [], "untracked": [], "warnings": [f"error: {e}"], "overlaps": []}
        if v:
            repos.append(v)
    attached = {c["agent"]["session_id"] for r in repos for c in r.get("tracks", [])
                if c.get("agent", {}).get("session_id")}
    unmanaged = [{"session_id": s["sessionId"], "name": s.get("name"), "cwd": s.get("cwd"),
                  "status": s.get("status"), "title": identity.title(s["sessionId"]),
                  "updated_at": int((s.get("updatedAt") or 0) / 1000)}
                 for s in sessions.values() if s.get("live") and s["sessionId"] not in attached]
    data = {"generated_at": store.now(), "repos": repos, "unmanaged": unmanaged}
    _cache["board"] = (time.monotonic(), data)
    return data


def track_detail(common: str, name: str, diff_cap: int = 200_000) -> Optional[dict]:
    cfg = store.load_config(common)
    t = store.load_track(common, name)
    if not cfg or not t:
        return None
    primary = os.path.dirname(common)
    base = gitx.base_ref(primary, cfg.get("remote"), cfg.get("main") or "main")
    branch = t.get("branch") or ""
    exists = bool(gitx.rev(primary, f"refs/heads/{branch}"))
    commits = gitx.log(primary, f"{base}..{branch}", 100) if exists else []
    numstat = []
    diff = ""
    if exists:
        for line in gitx.out(["diff", "--numstat", f"{base}...{branch}"], primary).splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                numstat.append({"added": parts[0], "deleted": parts[1], "file": parts[2]})
        diff = gitx.run(["diff", "--no-color", f"{base}...{branch}"], primary, timeout=30).out
    wt = t.get("worktree")
    wip = ""
    dirty = []
    if wt and os.path.isdir(wt):
        dirty = gitx.dirty(wt)
        wip = gitx.run(["diff", "--no-color", "HEAD"], wt, timeout=30).out
    acts = [e for e in store.tail_jsonl(store.activity_path(common), 524288)
            if e.get("track") == name][-200:]
    thread = [e for e in store.read_log(common) if e.get("from") == name or e.get("to") == name]
    return {"track": t, "base": base, "commits": commits, "numstat": numstat,
            "diff": diff[:diff_cap], "diff_truncated": len(diff) > diff_cap,
            "wip": wip[:diff_cap], "wip_truncated": len(wip) > diff_cap, "dirty": dirty,
            "activity": acts, "log": thread[-100:],
            "agent": _session_view(t.get("agent"), identity.sessions_by_id())}


def activity(since: int) -> list[dict]:
    names = {s["sessionId"]: s.get("name") for s in identity.registry()}
    res = []
    for r in store.registered_repos():
        for e in store.tail_jsonl(store.activity_path(r["common"]), 524288):
            if e.get("at", 0) >= since:
                res.append(dict(e, repo=os.path.basename(r.get("primary") or ""),
                                who=names.get(e.get("sid")) or (e.get("sid") or "")[:8]))
    res.sort(key=lambda e: e.get("at", 0))
    return res[-2000:]
