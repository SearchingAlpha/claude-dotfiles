"""Who each session fanned out to, and what every agent is doing now. Read from
the transcripts Claude Code keeps: a session's subagents live in
~/.claude/projects/<project>/<session>/subagents/agent-<id>.{jsonl,meta.json};
the meta file names the parent agent when a subagent spawned its own."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from .store import claude_dir, read_json

WINDOW = 24 * 3600      # finished subagents older than this are left out
QUIET = 15 * 60         # an unfinished subagent silent this long is "stalled"
PER_SESSION = 80

_tail_cache: dict = {}


def _turns(path: Path) -> list[dict]:
    """The last few user/assistant records of a transcript, oldest first. Lines can
    be megabytes long, and bookkeeping records (titles, modes) trail the turns."""
    try:
        st = path.stat()
    except OSError:
        return []
    hit = _tail_cache.get(str(path))
    if hit and hit[0] == (st.st_mtime, st.st_size):
        return hit[1]
    turns: list = []
    try:
        with open(path, "rb") as f:
            size, step = st.st_size, 65536
            while True:
                f.seek(max(0, size - step))
                lines = [ln for ln in f.read().split(b"\n") if ln.strip()]
                if size > step:
                    lines = lines[1:]           # the first line is probably cut
                turns = []
                for ln in lines:
                    try:
                        rec = json.loads(ln)
                    except ValueError:
                        continue
                    if isinstance(rec, dict) and rec.get("type") in ("user", "assistant"):
                        turns.append(rec)
                if any(r["type"] == "assistant" for r in turns) or size <= step \
                        or step >= 16 * 1048576:
                    break
                step *= 4
    except OSError:
        pass
    turns = turns[-12:]
    _tail_cache[str(path)] = ((st.st_mtime, st.st_size), turns)
    if len(_tail_cache) > 2000:
        _tail_cache.pop(next(iter(_tail_cache)))
    return turns


def _doing(turns: list[dict]) -> Optional[str]:
    """A short line for the agent's latest step: its last tool call."""
    for rec in reversed(turns):
        content = (rec.get("message") or {}).get("content")
        if rec.get("type") != "assistant" or not isinstance(content, list):
            continue
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                inp = block.get("input") or {}
                hint = inp.get("description") or os.path.basename(inp.get("file_path") or "") \
                    or inp.get("pattern") or inp.get("query") or inp.get("command") or ""
                hint = " ".join(str(hint).split())[:90]
                return f"{block.get('name')}: {hint}" if hint else block.get("name")
    return None


def _finished(turns: list[dict]) -> bool:
    rec = turns[-1] if turns else None
    if not rec or rec.get("type") != "assistant":
        return False
    msg = rec.get("message") or {}
    if msg.get("stop_reason") not in ("end_turn", "stop_sequence"):
        return False
    return not any(isinstance(b, dict) and b.get("type") == "tool_use"
                   for b in msg.get("content") or [])


def session_dirs(session_id: str) -> list[Path]:
    return [p for p in (claude_dir() / "projects").glob(f"*/{session_id}") if p.is_dir()]


def session_doing(session_id: str) -> Optional[str]:
    for p in (claude_dir() / "projects").glob(f"*/{session_id}.jsonl"):
        return _doing(_turns(p))
    return None


def subagents(session_id: str, session_live: bool, now: Optional[float] = None) -> list[dict]:
    """Every subagent of a session (nested ones included), newest last."""
    now = now or time.time()
    res = []
    for d in session_dirs(session_id):
        for meta_path in (d / "subagents").glob("agent-*.meta.json"):
            aid = meta_path.name[len("agent-"):-len(".meta.json")]
            tx = meta_path.with_name(f"agent-{aid}.jsonl")
            try:
                started = meta_path.stat().st_mtime
                updated = tx.stat().st_mtime if tx.exists() else started
            except OSError:
                continue
            meta = read_json(meta_path, {}) or {}
            turns = _turns(tx)
            if meta.get("stoppedByUser"):
                status = "stopped"
            elif _finished(turns):
                status = "done"
            elif not session_live:
                status = "interrupted"
            elif now - updated > QUIET:
                status = "stalled"
            else:
                status = "running"
            if status != "running" and now - updated > WINDOW:
                continue
            res.append({"id": aid, "parent": meta.get("parentAgentId") or None,
                        "depth": meta.get("spawnDepth") or 1,
                        "type": meta.get("agentType") or "agent",
                        "description": meta.get("description") or "",
                        "fork": bool(meta.get("isFork")),
                        "background": meta.get("requestShape") == "background",
                        "model": meta.get("model"),
                        "worktree_branch": meta.get("worktreeBranch"),
                        "status": status, "doing": _doing(turns) if status == "running" else None,
                        "started_at": int(started), "updated_at": int(updated)})
    res.sort(key=lambda a: a["started_at"])
    if len(res) > PER_SESSION:      # keep every running agent, then the newest
        running = [a for a in res if a["status"] == "running"]
        rest = [a for a in res if a["status"] != "running"][-(PER_SESSION - len(running)):]
        keep = {a["id"] for a in running + rest}
        res = [a for a in res if a["id"] in keep]
    known = {a["id"] for a in res}
    for a in res:                   # a parent that aged out: hang the child on the session
        if a["parent"] not in known:
            a["parent"] = None
    return res


def summary(agents: list[dict]) -> dict:
    out: dict = {}
    for a in agents:
        out[a["status"]] = out.get(a["status"], 0) + 1
    return out
