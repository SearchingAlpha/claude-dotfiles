"""Who is acting: the Claude Code session behind this process, read from the env
vars Claude Code sets on every tool subprocess and from its session registry
(~/.claude/sessions/<pid>.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .store import claude_dir, read_json


def proc_start(pid: int) -> Optional[str]:
    """Start time of a process in clock ticks (field 22 of /proc/<pid>/stat)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = stat.rsplit(")", 1)[-1].split()
    return fields[19] if len(fields) > 19 else None


def is_live(pid, start=None) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if Path("/proc").is_dir():
        if not Path(f"/proc/{pid}").exists():
            return False
        return start is None or str(start) == proc_start(pid)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def registry() -> list[dict]:
    """Running and recently-running Claude Code sessions, each with `live`."""
    res = []
    for p in (claude_dir() / "sessions").glob("*.json"):
        s = read_json(p)
        if not isinstance(s, dict) or not s.get("sessionId"):
            continue
        s["live"] = is_live(s.get("pid"), s.get("procStart"))
        res.append(s)
    return res


def sessions_by_id() -> dict:
    return {s["sessionId"]: s for s in registry()}


def agent(session_id: Optional[str], pid=None) -> Optional[dict]:
    """The agent record for a session (as stored on tracks), or None."""
    if not session_id:
        return None
    entry = None
    if pid:
        e = read_json(claude_dir() / "sessions" / f"{pid}.json")
        if isinstance(e, dict) and e.get("sessionId") == session_id:
            entry = e
    if entry is None:
        entry = sessions_by_id().get(session_id)
    return {"kind": "claude", "session_id": session_id,
            "name": (entry or {}).get("name"),
            "pid": (entry or {}).get("pid") or (int(pid) if pid else None),
            "proc_start": (entry or {}).get("procStart")}


def me(env=None) -> Optional[dict]:
    """The Claude Code session running this process, or None (a human)."""
    env = os.environ if env is None else env
    return agent(env.get("CLAUDE_CODE_SESSION_ID"), env.get("CLAUDE_PID"))


def in_agent(env=None) -> bool:
    env = os.environ if env is None else env
    return bool(env.get("CLAUDE_CODE_SESSION_ID"))


def agent_live(a: Optional[dict]) -> bool:
    if not a or not a.get("session_id"):
        return False
    if a.get("pid") and is_live(a["pid"], a.get("proc_start")):
        # the pid may have been reused by a later session of the same process
        e = read_json(claude_dir() / "sessions" / f"{a['pid']}.json")
        return not isinstance(e, dict) or e.get("sessionId") in (None, a["session_id"])
    s = sessions_by_id().get(a["session_id"])
    return bool(s and s.get("live"))


_title_cache: dict = {}


def title(session_id: str) -> Optional[str]:
    """The session's auto-generated title (last `ai-title` line of its transcript)."""
    if not session_id:
        return None
    paths = list((claude_dir() / "projects").glob(f"*/{session_id}.jsonl"))
    if not paths:
        return None
    p = paths[0]
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    hit = _title_cache.get(session_id)
    if hit and hit[0] == mtime:
        return hit[1]
    found = None
    try:
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            step, pos = 262144, size
            while pos > 0 and found is None:
                pos = max(0, pos - step)
                f.seek(pos)
                for line in reversed(f.read(min(step, size - pos) + 4096).split(b"\n")):
                    if b'"ai-title"' in line:
                        try:
                            found = json.loads(line).get("aiTitle")
                        except ValueError:
                            continue
                        if found:
                            break
                if size - pos > 4_000_000:   # don't scan huge transcripts to the start
                    break
    except OSError:
        pass
    _title_cache[session_id] = (mtime, found)
    return found
