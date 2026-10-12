#!/usr/bin/env python3
"""herd — one attention list of every live Claude Code session on this machine.

    herd [ls] [--json]                       blocked first, then done, then working
    herd watch [-n SECS] [--no-bell]         the same, redrawn; rings when a session blocks
    herd new <name> [dir] [-- claude args…]  start claude in a detached tmux session
    herd go <name>                           attach (or switch) to a session's tmux pane
    herd serve [-p PORT]                     dashboard on 127.0.0.1: the list, and a graph view
    herd url [-p PORT]                       the dashboard's URL, and whether it is running
    herd recap [since] [--json]              what you left open since your last workday
    herd recap --save-summary < topics.json  the recap skill's topics, for the Recap page
    herd resume <name> [--detach]            back into a session: claude --resume in tmux

Read-only over what Claude Code already writes: its session registry
(~/.claude/sessions/<pid>.json), transcripts and workflow journals, plus shepherd's
status and `git status` for the recap. No hooks and no state of its own beyond the
dashboard's access token. Stdlib only, Python 3.9+.
See harnesses/herd/README.md."""

from __future__ import annotations

import argparse
import errno
import http.client
import json
import math
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Registry status → herd state, in attention order.
STATES = {"waiting": "blocked", "idle": "done", "busy": "working", "shell": "working"}
ORDER = {"blocked": 0, "done": 1, "working": 2}
COLOR = {"blocked": "1;31", "done": "32", "working": "33"}

# Identity of the session herd runs in; a claude started by `herd new` must not inherit it.
SESSION_ENV = ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
               "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING_SOCKET",
               "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_SESSION_ATTENDED",
               "CLAUDE_CODE_SSE_PORT", "AI_AGENT")


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# --- processes -----------------------------------------------------------------------

def _stat(pid: int) -> list[str]:
    """Fields 3.. of /proc/<pid>/stat (the command name may contain spaces)."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()
    except OSError:
        return []


def proc_start(pid: int) -> Optional[str]:
    """Start time in clock ticks (field 22): tells a live session from a reused pid."""
    f = _stat(pid)
    return f[19] if len(f) > 19 else None


def parent(pid: int) -> int:
    f = _stat(pid)
    return int(f[1]) if len(f) > 1 else 0


def is_live(entry: dict) -> bool:
    """A crashed session leaves its registry file behind, and pids get reused."""
    try:
        pid = int(entry.get("pid"))
    except (TypeError, ValueError):
        return False
    start = proc_start(pid)
    return start is not None and str(entry.get("procStart", start)) == start


# --- transcripts ---------------------------------------------------------------------

def _transcript(session_id: str) -> Optional[Path]:
    return next((claude_dir() / "projects").glob(f"*/{session_id}.jsonl"), None)


def _main_transcripts(session_id: str) -> list[Path]:
    """Every main transcript of a session: one per project dir it has worked in."""
    return sorted((claude_dir() / "projects").glob(f"*/{session_id}.jsonl"))


def _tail(path: Optional[Path], enough) -> list[dict]:
    """A transcript's last records, newest first: 64 KB, growing ×4 up to 4 MB until
    enough(records) holds — only a huge record at the end needs more than one read."""
    if path is None:
        return []
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            step = 65536
            while True:
                f.seek(max(0, size - step))
                lines = f.read().split(b"\n")
                if size > step:
                    lines = lines[1:]           # the first line is probably cut
                recs = []
                for ln in reversed(lines):
                    try:
                        rec = json.loads(ln) if ln.strip() else None
                    except ValueError:
                        continue
                    if isinstance(rec, dict):
                        recs.append(rec)
                if size <= step or step >= 4 * 1048576 or enough(recs):
                    return recs
                step *= 4
    except OSError:
        return []


def tail_records(path: Optional[Path], types: tuple[str, ...]) -> dict:
    """The latest record of each wanted type. Claude Code re-appends its bookkeeping
    records (last-prompt, ai-title) after every turn, so the tail almost always has them."""
    recs = _tail(path, lambda rs: set(types) <= {r.get("type") for r in rs})
    found: dict = {}
    for r in recs:
        if r.get("type") in types:
            found.setdefault(r["type"], r)
    return found


_turns_cache: dict = {}


def tail_turns(path: Path) -> list[dict]:
    """The last user/assistant records of a transcript, newest first (cached per file
    version: the dashboard polls hundreds of agent transcripts that rarely change)."""
    try:
        st = path.stat()
    except OSError:
        return []
    key = (st.st_mtime_ns, st.st_size)
    hit = _turns_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    recs = _tail(path, lambda rs: any(r.get("type") == "assistant" for r in rs))
    turns = [r for r in recs if r.get("type") in ("user", "assistant")][:8]
    _turns_cache[path] = (key, turns)
    if len(_turns_cache) > 4000:
        _turns_cache.pop(next(iter(_turns_cache)))
    return turns


BLOCK = 8 * 1048576


def _grow(path: Path, cache: dict, needles: tuple, fresh, fold):
    """fold(line, acc) over each complete line appended to `path` since the last call that
    holds one of `needles`; returns acc. Transcripts only grow, so each poll reads just the
    new bytes; a half-written last line waits for the next poll, and a replaced or shrunk
    file is read again from the start."""
    try:
        st = path.stat()
    except OSError:
        return fresh()
    seen = cache.get(path)
    if not seen or seen[0] != st.st_ino or seen[1] > st.st_size:
        seen = (st.st_ino, 0, fresh())
    ino, pos, acc = seen
    if st.st_size > pos:
        try:
            with open(path, "rb") as f:
                f.seek(pos)
                carry, left = b"", st.st_size - pos
                while left > 0:
                    chunk = f.read(min(BLOCK, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                    data = carry + chunk
                    end = data.rfind(b"\n") + 1
                    for ln in data[:end].split(b"\n"):
                        if any(n in ln for n in needles):
                            fold(ln, acc)
                    pos += end
                    carry = data[end:]
        except OSError:
            pass
        cache[path] = (ino, pos, acc)
        if len(cache) > 4000:
            cache.pop(next(iter(cache)))
    return acc


_stamp_cache: dict = {}


def _first_stamp(path: Path) -> float:
    """When a transcript began: its first record's timestamp (a fork's first record, the
    context reference, has none). A file's head never changes, so a hit is kept for good."""
    if path in _stamp_cache:
        return _stamp_cache[path]
    try:
        with open(path, "rb") as f:
            for _, ln in zip(range(20), f):
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue
                at = _epoch(rec.get("timestamp")) if isinstance(rec, dict) and rec.get("timestamp") else 0.0
                if at:
                    _stamp_cache[path] = at
                    return at
    except OSError:
        pass
    return 0.0


def doing(turns: list[dict], asking: bool = False) -> str:
    """The agent's latest step: its newest tool call, as "Tool: hint". With `asking` (a
    blocked session) only a call still waiting for its result counts, and the hint is the
    command itself, since that is what needs approving. A blocked session whose calls have all
    run is held by a dialog or an input prompt, not a tool: then it is ""."""
    answered, results = set(), False        # tool_result ids from records newer than the call
    for rec in turns:
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        if rec.get("type") == "user":
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results = True
                    answered.add(b.get("tool_use_id"))
            continue
        if rec.get("type") != "assistant":
            continue
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                if asking and (block.get("id") in answered if block.get("id") else results):
                    continue                # it already ran
                inp = block.get("input") or {}
                hint = inp.get("description") or os.path.basename(str(inp.get("file_path") or "")) \
                    or inp.get("pattern") or inp.get("query") or inp.get("command") or ""
                if asking:
                    hint = inp.get("command") or inp.get("file_path") or inp.get("url") or hint
                hint = cut(str(hint), 300) if asking else " ".join(str(hint).split())[:100]
                return f"{block.get('name')}: {hint}" if hint else str(block.get("name"))
    return ""


def _tools(rec: dict) -> set:
    content = (rec.get("message") or {}).get("content")
    return {b.get("name") for b in content if isinstance(b, dict) and b.get("type") == "tool_use"} \
        if isinstance(content, list) else set()


def _texts(rec: dict) -> list[str]:
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return [content]
    return [b.get("text") or "" for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]


def ended(turns: list[dict], quiet_for: float) -> str:
    """"done" or "stopped" when an agent's transcript shows it has finished, else "".
    Claude Code writes each content block as its own record, often without a
    stop_reason, so a quiet text-only reply counts as a final answer too."""
    if not turns:
        return ""
    newest = turns[0]
    if newest.get("type") == "user":
        if any(t.startswith("[Request interrupted by user") for t in _texts(newest)):
            return "stopped"
        before = turns[1] if len(turns) > 1 else {}
        return "done" if "SubagentHandback" in _tools(before) else ""
    tools = _tools(newest)
    if "SubagentHandback" in tools:
        return "done"
    if tools:
        return ""
    stop = (newest.get("message") or {}).get("stop_reason")
    return "done" if stop in ("end_turn", "stop_sequence") or quiet_for > 60 else ""


# --- tmux / git ----------------------------------------------------------------------

def tmux_panes() -> dict[int, str]:
    """pane pid → "session:window.pane" for every pane on the default tmux server."""
    if not shutil.which("tmux"):
        return {}
    r = subprocess.run(["tmux", "list-panes", "-a", "-F",
                        "#{pane_pid}\t#{session_name}:#{window_index}.#{pane_index}"],
                       capture_output=True, text=True)
    panes = {}
    for ln in r.stdout.splitlines():
        pid, _, target = ln.partition("\t")
        if pid.isdigit():
            panes[int(pid)] = target
    return panes


def tmux_target(pid: int, panes: dict[int, str]) -> Optional[str]:
    """The pane a process runs in: it, or one of its ancestors, is the pane's process."""
    for _ in range(8):
        if pid in panes:
            return panes[pid]
        pid = parent(pid)
        if pid <= 1:
            return None
    return None


def git_branch(cwd: str, cache: dict) -> str:
    if cwd not in cache:
        r = subprocess.run(["git", "-C", cwd, "branch", "--show-current"],
                           capture_output=True, text=True)
        cache[cwd] = r.stdout.strip() if r.returncode == 0 else ""
    return cache[cwd]


# --- workflows and subagents ---------------------------------------------------------
#
# Under ~/.claude/projects/<project>/<session>/ (one such dir per project the session
# has worked in):
#   workflows/scripts/<name>-<run>.js          the script, written at launch (meta: phases)
#   subagents/workflows/<run>/journal.jsonl    started / result / failed per workflow agent
#   subagents/workflows/<run>/agent-<id>.jsonl each workflow agent's transcript
#   workflows/<run>.json                       the run's final record, written when it ends
#   subagents/agent-<id>.{meta.json,jsonl}     subagents the session spawned itself

QUIET = 15 * 60         # running but silent this long → "stalled"
QUIET_SESSION = 10 * 60  # a working session with nothing running, silent this long → "quiet"
WINDOW = 24 * 3600      # finished workflows and subagents older than this are left out
RESTART = 5             # slack (s) when comparing file times with the session's start
LIVE = ("running", "stalled")

_META = re.compile(r"export\s+const\s+meta\s*=\s*\{(.*?)\n\}", re.S)
_PHASES = re.compile(r"phases\s*:\s*\[(.*?)\]", re.S)
_TITLE = re.compile(r"title\s*:\s*(['\"`])(.*?)\1")
_DESC = re.compile(r"description\s*:\s*(['\"`])(.*?)\1")
_NAME = re.compile(r"^\s*name\s*:\s*(['\"`])(.*?)\1", re.M)
_SCRIPT = re.compile(r"(.+)-(wf_[0-9a-f]+-[0-9a-f]+)\.js$")
_LAUNCHED = re.compile(r"Workflow launched.*?/subagents/workflows/(wf_[0-9a-f]+-[0-9a-f]+)", re.S)
_journal_cache: dict = {}
_launch_cache: dict = {}


def session_dirs(session_id: str) -> list[Path]:
    return [p for p in (claude_dir() / "projects").glob(f"*/{session_id}") if p.is_dir()]


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _script_meta(path: Path) -> dict:
    """Name, description and phase titles from the script's `export const meta = {…}` literal."""
    try:
        return _meta_of(path.read_text(errors="replace"))
    except OSError:
        return {}


def _meta_of(text: str) -> dict:
    m = _META.search(text[:20000])
    if not m:
        return {}
    body = m.group(1)
    phases = _PHASES.search(body)
    head = body[:phases.start()] if phases else body
    desc, name = _DESC.search(head), _NAME.search(head)
    return {"name": name.group(2) if name else "", "description": desc.group(2) if desc else "",
            "phases": [t.group(2) for t in _TITLE.finditer(phases.group(1))] if phases else []}


def _epoch(stamp) -> float:
    """A transcript record's ISO timestamp ("2026-10-08T18:42:41.968Z") in epoch seconds."""
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def workflow_launches(session_id: str) -> tuple[list[dict], dict]:
    """Every Workflow tool call in the session's transcripts, oldest first, as
    {at, id, script_path, resume, meta}, and run id → the call that launched it (from the
    tool result's "Transcript dir: …/<run>"), read incrementally."""
    launches: list[dict] = []
    links: dict = {}
    for path in _main_transcripts(session_id):
        found, linked = _grow(path, _launch_cache, (b'"Workflow"', b"Workflow launched"),
                              lambda: ([], {}), lambda ln, acc: _launch_record(ln, *acc))
        launches += found
        links.update(linked)
    launches.sort(key=lambda c: c["at"])
    return launches, links


def _launch_record(line: bytes, found: list, linked: dict) -> None:
    try:
        rec = json.loads(line)
    except ValueError:
        return
    content = (rec.get("message") or {}).get("content") if isinstance(rec, dict) else None
    if not isinstance(content, list):
        return
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "tool_use" and b.get("name") == "Workflow":
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            found.append({"at": _epoch(rec.get("timestamp")), "id": b.get("id") or "",
                          "script_path": str(inp.get("scriptPath") or ""),
                          "resume": str(inp.get("resumeFromRunId") or ""),
                          "meta": _meta_of(inp["script"]) if isinstance(inp.get("script"), str) else {}})
        elif b.get("type") == "tool_result" and b.get("tool_use_id"):
            m = _LAUNCHED.search(json.dumps(b.get("content")))
            if m:
                linked[m.group(1)] = b["tool_use_id"]


def _recover(run_id: str, started: float, launches: list[dict], links: dict) -> dict:
    """Name, meta and the run it resumes, for a run whose script was not kept under its own
    id (a resumed run points Workflow at an existing script). The call that launched it:
    the one its tool result names, else the newest scriptPath call no later than its start."""
    call = next((c for c in launches if c["id"] and links.get(run_id) == c["id"]), None)
    if call is None:
        taken = set(links.values())             # calls that launched some other run
        call = next((c for c in reversed(launches) if c["script_path"] and c["id"] not in taken
                     and c["at"] <= started + RESTART), None)
    return _from_call(call, run_id) if call else {}


def _from_call(call: dict, run_id: str) -> dict:
    """name / resumed_from / meta from a launch: a script named <name>-<run>.js is the
    script of <run>, so this run resumes it."""
    meta = call["meta"]
    out = {"name": "", "resumed_from": call["resume"]}
    if call["script_path"]:
        path = Path(call["script_path"])
        m = _SCRIPT.match(path.name)
        if m:
            out["name"] = m.group(1)
            out["resumed_from"] = out["resumed_from"] or m.group(2)
        meta = _script_meta(path) if path.is_file() else meta
    out["name"] = out["name"] or meta.get("name") or ""
    out["meta"] = meta
    if out["resumed_from"] == run_id:
        out["resumed_from"] = ""
    return out


def _journal(path: Path) -> list[dict]:
    try:
        st = path.stat()
    except OSError:
        return []
    key = (st.st_mtime_ns, st.st_size)
    hit = _journal_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    events = []
    try:
        with open(path, "rb") as f:
            for ln in f:
                try:
                    rec = json.loads(ln)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    rec.pop("result", None)     # results can be huge; only the fact matters
                    events.append(rec)
    except OSError:
        return []
    _journal_cache[path] = (key, events)
    return events


def _live_status(updated: float, session_live: bool, now: float, session_start: float = 0.0) -> str:
    """running / stalled for an unfinished agent or run; interrupted when its session is
    gone, or when its last write predates the session's current process (the run was
    orphaned by a restart and nothing will finish it)."""
    if not session_live or (session_start and updated < session_start - RESTART):
        return "interrupted"
    return "stalled" if now - updated > QUIET else "running"


COUNTED = ("running", "stalled", "done", "failed", "interrupted")
GRAPH_PHASE_CAP = 6     # workflow agents the graph draws per phase; the rest are a "more" count


def _graph_rank(a: dict, live: bool) -> int:
    """Which of a phase's agents the graph draws first: trouble, then work, then history."""
    if a["superseded"]:
        return 3
    return {"stalled": 0, "failed": 1 if live else 4, "running": 2, "done": 5}.get(a["status"], 4)


def _workflow(run_id: str, parts: dict, session_live: bool, now: float,
              session_start: float = 0.0, launches: Optional[tuple] = None,
              graph: bool = False) -> Optional[dict]:
    final = read_json(parts["final"]) if parts.get("final") else None
    final = final if isinstance(final, dict) else {}
    meta = _script_meta(parts["script"]) if parts.get("script") else {}
    run_dir = parts.get("dir")
    journal = run_dir / "journal.jsonl" if run_dir else None
    agents: dict = {}
    for ev in _journal(journal) if journal else []:
        key = ev.get("key") or ev.get("agentId")
        if ev.get("type") == "started":
            agents.pop(key, None)               # a restarted key counts from its new start
            agents[key] = {"id": ev.get("agentId") or "", "label": ev.get("label") or "agent",
                           "phase": ev.get("phase") or "", "status": "running", "doing": "",
                           "superseded": False, "updated_at": 0}
        elif ev.get("type") in ("result", "failed") and key in agents:
            agents[key]["status"] = "done" if ev["type"] == "result" else "failed"
    # Unfinished agents: their transcript's last write says running, stalled or interrupted.
    last = _mtime(journal) if journal else 0.0
    for a in agents.values():
        if a["status"] == "running":
            a["tx"] = run_dir / f"agent-{a['id']}.jsonl"
            last = max(last, _mtime(a["tx"]))
    started = (final.get("startTime") or 0) / 1000
    if not started and parts.get("script"):
        started = _mtime(parts["script"])
    if not started and run_dir:                 # no script kept: the first agent's meta file
        started = min((_mtime(m) for m in run_dir.glob("agent-*.meta.json")), default=last)
    started = started or last
    if final:
        status = final.get("status") or "completed"
        end = started + final["durationMs"] / 1000 if final.get("durationMs") \
            else _mtime(parts["final"])
    else:
        status = _live_status(last or started, session_live, now, session_start)
        end = (last or started) if status == "interrupted" else 0.0   # frozen at its last write
    if status not in LIVE and now - (end or last or started) > WINDOW:
        return None
    for a in agents.values():
        tx = a.pop("tx", None)
        if tx is None:
            continue
        updated = _mtime(tx) or last
        turns = tail_turns(tx)
        a["updated_at"] = int(updated)
        a["status"] = ended(turns, now - updated) or \
            ("interrupted" if status not in LIVE else _live_status(updated, session_live, now, session_start))
        a["doing"] = doing(turns) if a["status"] in LIVE else ""
    # A retry: a later agent with the same phase and label that finished or is still at it.
    # The earlier attempt keeps its status but drops out of the counts and problems.
    retried: set = set()
    for a in reversed(list(agents.values())):
        slot = (a["phase"], a["label"])
        if slot in retried and a["status"] != "done":
            a["superseded"] = True
        if a["status"] == "done" or a["status"] in LIVE:
            retried.add(slot)
    recovered = {}
    if not parts.get("script"):                 # resumed: its script lives under another run id
        if final.get("scriptPath"):
            script = final.get("script")
            recovered = _from_call({"script_path": str(final["scriptPath"]), "resume": "",
                                    "meta": _meta_of(script) if isinstance(script, str) else {}}, run_id)
        elif launches is not None:
            recovered = _recover(run_id, started, *launches)
        meta = recovered.get("meta") or meta
    declared = [p.get("title") for p in final.get("phases") or [] if isinstance(p, dict)] \
        or meta.get("phases") or []
    titles = list(declared)
    for a in agents.values():
        if a["phase"] not in titles:
            titles.append(a["phase"])
    live = status in LIVE
    phases = []
    for t in titles:
        members = [a for a in agents.values() if a["phase"] == t]
        if not t and not members:
            continue
        counted = [a for a in members if not a["superseded"]]
        counts = {s: sum(a["status"] == s for a in counted) for s in COUNTED}
        counts["retried"] = len(members) - len(counted)
        more: dict = {}
        if graph:
            # The graph draws a few of every phase, finished ones too, in the order they
            # started; which few: trouble first, then work, then the newest history.
            order = {id(a): i for i, a in enumerate(members)}
            picked = sorted(members, key=lambda a: (_graph_rank(a, live), -order[id(a)]))
            shown = sorted(picked[:GRAPH_PHASE_CAP], key=lambda a: order[id(a)])
            for a in picked[GRAPH_PHASE_CAP:]:
                k = "retried" if a["superseded"] else a["status"]
                more[k] = more.get(k, 0) + 1
        else:
            # Payload bound: finished agents are counted, not listed (a phase can hold hundreds).
            shown = [a for a in members if a["status"] != "done"] if live else \
                [a for a in counted if a["status"] == "failed"]
        for a in shown:
            if not a["updated_at"] and run_dir:
                a["updated_at"] = int(_mtime(run_dir / f"agent-{a['id']}.jsonl"))
        phases.append({"title": t or "—", "agents": shown, "counts": counts, "_members": counted,
                       **({"more": more} if graph else {})})
    # The current phase is the first (declared order) still at work, not the newest one started.
    current = next((i for i, p in enumerate(phases)
                    if any(a["status"] in LIVE for a in p["_members"])), None)
    if current is None and live:
        current = next((i for i in reversed(range(len(phases))) if phases[i]["_members"]), None)
    problems = {"stalled": 0, "failed": 0}
    if live and current is not None:
        for p in phases[current:]:
            problems["stalled"] += p["counts"]["stalled"]
            problems["failed"] += p["counts"]["failed"]
    for p in phases:
        del p["_members"]
    error = str(final.get("error") or "").strip().splitlines()
    return {"id": run_id,
            "name": final.get("workflowName") or parts.get("name") or recovered.get("name") or "",
            "description": meta.get("description") or final.get("summary") or "",
            "status": status, "started_at": int(started), "ended_at": int(end),
            "elapsed": int((end or now) - started) if started else 0,
            "last_activity": int(last or started),
            "agent_count": sum(not a["superseded"] for a in agents.values()) or final.get("agentCount") or 0,
            "tokens": final.get("totalTokens") or 0, "error": error[0] if error else "",
            "resumed_from": recovered.get("resumed_from") or "", "superseded_by": "",
            "phase_total": len(declared) or None,
            "current_phase": current if live else None, "problems": problems,
            "phases": phases}


def workflows(session_id: str, session_live: bool, now: float, session_start: float = 0.0,
              graph: bool = False) -> list[dict]:
    """The session's workflow runs: every unfinished one, plus those that ended in the
    last 24 h; oldest first. A run resumed from an earlier one names it in
    `resumed_from`, and the earlier one points back with `superseded_by`. With `graph`,
    each phase lists a few of its agents whatever their state (see _workflow)."""
    runs: dict = {}
    for d in session_dirs(session_id):
        for f in (d / "workflows").glob("wf_*.json"):
            runs.setdefault(f.stem, {})["final"] = f
        for s in (d / "workflows" / "scripts").glob("*-wf_*.js"):
            m = _SCRIPT.match(s.name)
            if m:
                runs.setdefault(m.group(2), {}).update(script=s, name=m.group(1))
        for j in (d / "subagents" / "workflows").glob("wf_*/journal.jsonl"):
            runs.setdefault(j.parent.name, {})["dir"] = j.parent
    # Runs without their own script (resumed ones) get their name from the session's
    # Workflow calls; the transcript is read only when there is such a run.
    launches = workflow_launches(session_id) if any(not p.get("script") and not p.get("final")
                                                    for p in runs.values()) else None
    out = []
    for rid, parts in runs.items():
        w = _workflow(rid, parts, session_live, now, session_start, launches, graph)
        if w:
            out.append(w)
    out.sort(key=lambda w: w["started_at"])
    by_id = {w["id"]: w for w in out}
    for w in out:
        if not w["resumed_from"] and w["name"] and not runs[w["id"]].get("script"):
            # Relaunched from a copy of the script: the newest earlier run of that name
            # that a restart left unfinished.
            prev = [o for o in out if o["name"] == w["name"] and o["status"] == "interrupted"
                    and not runs[o["id"]].get("final") and o["started_at"] < w["started_at"]]
            w["resumed_from"] = prev[-1]["id"] if prev else ""
        orig = by_id.get(w["resumed_from"])
        if orig is not None and orig is not w:
            orig["superseded_by"] = w["id"]
    return out


def subagents(session_id: str, session_live: bool, now: float, session_start: float = 0.0,
              ancestors: bool = False) -> list[dict]:
    """Subagents the session spawned, nested ones included (`parent` names the agent that
    spawned one; workflow agents live under their run), oldest first. Two project dirs can
    hold a copy of one agent: the larger transcript wins. With `ancestors`, a finished agent
    older than the window stays while it leads to an agent that is shown."""
    found: dict = {}
    for d in session_dirs(session_id):
        for meta_path in (d / "subagents").glob("agent-*.meta.json"):
            meta = read_json(meta_path) or {}
            if not isinstance(meta, dict) or meta.get("agentType") == "workflow-subagent":
                continue
            aid = meta_path.name[len("agent-"):-len(".meta.json")]
            tx = meta_path.with_name(f"agent-{aid}.jsonl")
            try:
                size = tx.stat().st_size
            except OSError:
                size = 0
            if aid in found and found[aid]["size"] >= size:
                continue
            updated = _mtime(tx) or _mtime(meta_path)
            # The meta file is rewritten after the spawn; the transcript's first record is not.
            started = _first_stamp(tx) or _mtime(meta_path)
            turns = tail_turns(tx)
            status = "stopped" if meta.get("stoppedByUser") else \
                ended(turns, now - updated) or _live_status(updated, session_live, now, session_start)
            found[aid] = {"id": aid, "type": meta.get("agentType") or "agent",
                          "description": meta.get("description") or "",
                          "background": meta.get("requestShape") == "background",
                          "status": status, "doing": doing(turns) if status in LIVE else "",
                          "started_at": int(started), "updated_at": int(updated),
                          "ended_at": 0 if status in LIVE else int(updated),
                          "parent": str(meta.get("parentAgentId") or ""),
                          "fork": bool(meta.get("isFork")) or meta.get("agentType") == "fork",
                          "tool_use_id": str(meta.get("toolUseId") or ""),
                          "model": str(meta.get("model") or ""),
                          "worktree_branch": str(meta.get("worktreeBranch") or ""), "size": size}
    keep = {aid for aid, a in found.items() if a["status"] in LIVE or now - a["updated_at"] <= WINDOW}
    if ancestors:
        for aid in list(keep):
            p, path = found[aid]["parent"], set()
            while p in found and p not in path:
                path.add(p)
                keep.add(p)
                p = found[p]["parent"]
    res = [a for aid, a in found.items() if aid in keep]
    for a in res:
        if a["parent"] not in keep:             # its spawner is gone or out of the window
            a["parent"] = ""
    res.sort(key=lambda a: (a["started_at"], a["id"]))
    return res


# --- the list ------------------------------------------------------------------------

def sessions(detail: bool = False, extras: bool = True) -> list[dict]:
    """Every live session, most urgent first; within a state, waiting longest first.
    With `detail`, each also carries its workflows, its own subagents, what it is asking
    for (blocked) or answered (done), and when it last wrote anything. Without `extras`,
    no subprocess runs: branch and tmux stay empty."""
    panes, branches, rows = tmux_panes() if extras else {}, {}, []
    now = time.time()
    entries = []
    for p in (claude_dir() / "sessions").glob("*.json"):
        e = read_json(p)
        if not isinstance(e, dict) or not e.get("sessionId") or e.get("spare"):
            continue
        if e.get("kind") not in (None, "interactive", "bg") or not is_live(e):
            continue
        entries.append(e)
    # A shell that moved into a background job stays registered as a husk pointing at it.
    jobs = {e["jobId"] for e in entries if e.get("jobId")}
    for e in entries:
        if e.get("parkedJobId") and e["parkedJobId"] in jobs:
            continue
        tx = _transcript(e["sessionId"])
        recs = tail_records(tx, ("last-prompt", "ai-title"))
        title = (recs.get("ai-title") or {}).get("aiTitle") or ""
        name = e.get("name") or e["sessionId"][:8]
        if e.get("nameSource") == "derived" and title:
            name = title                        # "pablomv-6a" says less than the title
        status = e.get("status") or ""
        changed = (e.get("statusUpdatedAt") or e.get("updatedAt") or e.get("startedAt") or 0) / 1000
        started = (e.get("startedAt") or 0) / 1000
        cwd = e.get("cwd") or ""
        state = STATES.get(status, status or "?")
        row = {
            "pid": e["pid"], "session_id": e["sessionId"], "name": name, "title": title,
            "kind": e.get("kind") or "interactive", "state": state,
            "waiting_for": e.get("waitingFor") or "", "since": max(0, int(now - changed)),
            "state_changed_at": int(changed), "started_at": int(started),
            "cwd": cwd, "branch": git_branch(cwd, branches) if cwd and extras else "",
            "tmux": (tmux_target(int(e["pid"]), panes) or "") if extras else "",
            "prompt": (recs.get("last-prompt") or {}).get("lastPrompt") or "",
        }
        rows.append(row)
        if detail:
            row["workflows"] = workflows(e["sessionId"], True, now, started)
            row["agents"] = subagents(e["sessionId"], True, now, started)
            live_wf = [w for w in row["workflows"] if w["status"] in LIVE and not w["superseded_by"]]
            live_ag = [a for a in row["agents"] if a["status"] in LIVE]
            row["last_activity"] = int(max([_mtime(tx) if tx else 0.0] +
                                           [w["last_activity"] for w in live_wf] +
                                           [a["updated_at"] for a in live_ag]))
            row["quiet"] = state == "working" and not live_wf and not live_ag \
                and now - row["last_activity"] > QUIET_SESSION
            turns = tail_turns(tx) if tx and state in ("blocked", "done") else []
            row["asking"] = doing(turns, asking=True) if state == "blocked" else ""
            row["reply"] = reply(turns) if state == "done" else ""
    rows.sort(key=lambda r: (ORDER.get(r["state"], 3), -r["since"]))
    # One row per session: two live processes on one session (resumed in two terminals) share
    # its transcript; keep the most urgent, so counts and rows agree.
    once = set()
    return [r for r in rows if not (r["session_id"] in once or once.add(r["session_id"]))]


def reply(turns: list[dict]) -> str:
    """The first paragraph of the session's newest assistant text, up to 600 characters."""
    for rec in turns:
        if rec.get("type") != "assistant":
            continue
        text = "\n".join(t for t in _texts(rec) if t.strip()).strip()
        if text:
            return cut(re.split(r"\n\s*\n", text, maxsplit=1)[0], 600)
    return ""


# --- the graph -----------------------------------------------------------------------
#
# /api/graph draws each session as the root of what it started (subagents, nested ones,
# forks, workflow runs and their agents) and who talked to whom. Only exact joins count:
#   a SendMessage result's toolUseResult.msg_id == the receiver's origin.msg_id   session ↔ session
#   origin {kind: peer, handback: true, from: <agent id>}                         agent → its spawner
#   SendMessage {to: <agent id>} in the spawner's transcript                      spawner → agent
#   {"type": "continued-in", "continuedInSessionId": …}                           session → bg job
# A bg job's transcript begins as a copy of its parent's, record uuids included: every event
# is counted once, in the transcript that began first.

GRAPH_FINISHED_FAN = 5  # finished children drawn per parent; older ones fold into one "more"
GRAPH_ENDED = 10        # ended sessions drawn because they talked to (or became) a live one
# Tokens, not "key":"value" pairs, so the prefilter holds whatever the JSON spacing.
_GRAPH_NEEDLES = (b'"SendMessage"', b'"msg_id"', b'"handback"', b'"continued-in"', b'"task-notification"')
_NOTE_ID = re.compile(r"<tool-use-id>([^<]+)</tool-use-id>")
_NOTE_STATUS = re.compile(r"<status>([^<]+)</status>")
NOTE_STATUS = {"completed": "done", "failed": "failed", "killed": "stopped", "stopped": "stopped"}
_interact_cache: dict = {}
_title_cache: dict = {}


def _fresh_acts() -> dict:
    return {"calls": {}, "received": [], "handbacks": [], "continued": [], "notified": {}}


def _interaction(line: bytes, acc: dict) -> None:
    """One prefiltered transcript line into acc: SendMessage calls (with the msg_id their
    result carries), peer messages and hand-backs received, continued-in, task notifications.
    A needle inside quoted text matches nothing here: only the record's own fields count."""
    try:
        rec = json.loads(line)
    except ValueError:
        return
    if not isinstance(rec, dict):
        return
    kind, at, uid = rec.get("type"), _epoch(rec.get("timestamp")), str(rec.get("uuid") or "")
    if kind == "continued-in":
        if rec.get("continuedInSessionId"):
            acc["continued"].append({"to": str(rec["continuedInSessionId"]), "at": at})
        return
    att = rec.get("attachment") if kind == "attachment" and isinstance(rec.get("attachment"), dict) else None
    origin = (att if att is not None else rec).get("origin")
    if isinstance(origin, dict) and kind in ("user", "attachment"):
        at = at or _epoch((att or {}).get("timestamp"))
        if origin.get("kind") == "peer" and origin.get("handback"):
            aid = origin.get("senderTaskId") or origin.get("from")
            if aid:
                acc["handbacks"].append({"agent": str(aid), "at": at, "uuid": uid})
        elif origin.get("kind") == "peer" and origin.get("msg_id"):
            acc["received"].append({"msg_id": str(origin["msg_id"]), "at": at, "uuid": uid,
                                    "pid": origin.get("verifiedPeerPid"),
                                    "proc_start": str(origin.get("verifiedPeerProcStart") or "")})
        elif origin.get("kind") == "task-notification":
            text = (att or {}).get("prompt") if att is not None else (rec.get("message") or {}).get("content")
            text = text if isinstance(text, str) else " ".join(_texts(rec))
            tid, status = _NOTE_ID.search(text), _NOTE_STATUS.search(text)
            if tid and status and status.group(1) in NOTE_STATUS:
                acc["notified"][tid.group(1).strip()] = (NOTE_STATUS[status.group(1)], at)
        return
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "tool_use" and b.get("name") == "SendMessage" and b.get("id"):
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            acc["calls"][b["id"]] = {"to": str(inp.get("to") or ""), "summary": cut(str(inp.get("summary") or ""), 120),
                                     "at": at, "uuid": uid, "msg_id": ""}
        elif b.get("type") == "tool_result" and b.get("tool_use_id") in acc["calls"]:
            res = rec.get("toolUseResult")
            if isinstance(res, dict) and res.get("msg_id"):
                acc["calls"][b["tool_use_id"]]["msg_id"] = str(res["msg_id"])


def interactions(path: Path) -> dict:
    return _grow(path, _interact_cache, _GRAPH_NEEDLES, _fresh_acts, _interaction)


def _ended_info(path: Path) -> dict:
    """Name and folder of a session that is no longer running, from its transcript's tail."""
    try:
        st = path.stat()
    except OSError:
        return {"name": "", "cwd": ""}
    key = (st.st_mtime_ns, st.st_size)
    hit = _title_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    titles = ("custom-title", "ai-title", "agent-name")
    recs = _tail(path, lambda rs: any(r.get("cwd") for r in rs) and any(r.get("type") in titles for r in rs))
    found = {r["type"]: r for r in reversed(recs) if r.get("type") in titles}
    name = (found.get("custom-title") or {}).get("customTitle") or (found.get("ai-title") or {}).get("aiTitle") \
        or (found.get("agent-name") or {}).get("agentName") or ""
    info = {"name": str(name), "cwd": str(next((r["cwd"] for r in recs if r.get("cwd")), ""))}
    _title_cache[path] = (key, info)
    return info


def _agent_tx(session_id: str, agent_id: str) -> Optional[Path]:
    for d in session_dirs(session_id):
        p = d / "subagents" / f"agent-{agent_id}.jsonl"
        if p.is_file():
            return p
    return None


def _events(files: dict, now: float) -> dict:
    """Per session id, its in-window events from every transcript it owns, each counted once.
    Transcripts are taken in the order they began, so an original beats a bg job's copy;
    a bg job's events from before its hand-off are copies too."""
    began = {sid: min((_first_stamp(p) or _mtime(p) for p in ps), default=0.0) for sid, ps in files.items()}
    accs = {sid: [interactions(p) for p in sorted(ps)] for sid, ps in files.items()}
    handed = {c["to"]: c["at"] for acs in accs.values() for a in acs for c in a["continued"]}
    seen, out = set(), {}
    # A copy keeps the original's timestamps, so it "began" at the same moment: the bg job loses the tie.
    for sid in sorted(files, key=lambda s: (began[s], s in handed, s)):
        cutoff = max(now - WINDOW, handed.get(sid, 0.0))

        def fresh(ev, cutoff=cutoff):
            if ev["at"] < cutoff or (ev.get("uuid") and ev["uuid"] in seen):
                return False
            if ev.get("uuid"):
                seen.add(ev["uuid"])
            return True
        ev = {"calls": [], "received": [], "handbacks": [], "continued": [], "notified": {}}
        for a in accs[sid]:
            ev["calls"] += [c for c in a["calls"].values() if fresh(c)]
            ev["received"] += [r for r in a["received"] if fresh(r)]
            ev["handbacks"] += [h for h in a["handbacks"] if fresh(h)]
            ev["continued"] += [c for c in a["continued"] if c["at"] >= now - WINDOW]
            ev["notified"].update(a["notified"])
        out[sid] = ev
    return out


def _fold(children: list, live_of) -> tuple:
    """The children a parent shows, in start order, and the finished ones folded away: every
    child whose subtree is still at work, plus the newest few finished ones."""
    live = [c for c in children if live_of(c)]
    done = sorted((c for c in children if not live_of(c)),
                  key=lambda c: c.get("ended_at") or c.get("updated_at") or c.get("started_at") or 0, reverse=True)
    shown = sorted(live + done[:GRAPH_FINISHED_FAN], key=lambda c: (c.get("started_at") or 0, c["id"]))
    return shown, done[GRAPH_FINISHED_FAN:]


def graph() -> dict:
    """Nodes in display order (each after its parent) and the edges between them."""
    now = time.time()
    rows = sessions(extras=False)
    live = {r["session_id"]: r for r in rows}
    files: dict = {}
    for p in (claude_dir() / "projects").glob("*/*.jsonl"):
        if p.stem in live or now - _mtime(p) <= WINDOW:
            files.setdefault(p.stem, []).append(p)
    ev = _events(files, now)

    # Who talked to whom: a received message joined to its sender on msg_id, else to the live
    # process the receiver verified it came from. Names are never used: they are reused.
    senders = {c["msg_id"]: (sid, c) for sid, e in ev.items() for c in e["calls"] if c["msg_id"]}
    by_pid = {(int(r["pid"]), proc_start(int(r["pid"]))): sid for sid, r in live.items()}
    pairs: dict = {}
    got = set()
    for sid, e in ev.items():
        for r in sorted(e["received"], key=lambda r: r["at"]):
            if r["msg_id"] in got:
                continue
            got.add(r["msg_id"])
            src = senders.get(r["msg_id"])
            try:
                frm = src[0] if src else by_pid.get((int(r["pid"]), r["proc_start"]))
            except (TypeError, ValueError):
                frm = None
            if not frm or frm == sid:
                continue
            m = pairs.setdefault((frm, sid), {"count": 0, "first_at": r["at"], "last_at": 0.0, "summary": ""})
            m["count"] += 1
            m["last_at"] = max(m["last_at"], r["at"])
            if src and src[1]["summary"]:
                m["summary"] = src[1]["summary"]
    handoffs = [(sid, c["to"], c["at"]) for sid, e in ev.items() for c in e["continued"] if c["to"] != sid]

    # Roots: live sessions in start order (a session that blocks must not move the others),
    # plus ended ones one hop from a live one; an ended session sits after its anchor, and a
    # session that moved into a bg job sits right before it.
    order = sorted(live, key=lambda s: (live[s]["started_at"], s))
    links: dict = {}
    for (a, b), m in pairs.items():
        links.setdefault(a, []).append((b, m["last_at"], False))
        links.setdefault(b, []).append((a, m["last_at"], False))
    for a, b, at in handoffs:
        links.setdefault(a, []).append((b, at, b in live))
        links.setdefault(b, []).append((a, at, False))
    ended = []
    for sid, ls in links.items():
        if sid in live or sid not in files:
            continue
        near = [(order.index(o), at, before) for o, at, before in ls if o in live]
        if near:
            idx, _, before = min(near)
            ended.append((max(at for _, at, _ in near), sid, idx, before))
    ended = sorted(ended, reverse=True)[:GRAPH_ENDED]
    roots = list(order)
    for _, sid, idx, before in sorted(ended, key=lambda e: (e[2], e[1])):
        anchor = roots.index(order[idx])
        if before:
            roots.insert(anchor, sid)
        else:
            while anchor + 1 < len(roots) and roots[anchor + 1] not in live:
                anchor += 1
            roots.insert(anchor + 1, sid)
    moved = {a for a, _, _ in handoffs}
    bgs = {b for _, b, _ in handoffs}

    nodes: list = []
    edges: list = []
    shown_agents: dict = {}                     # agent id → (its parent's node id, its session)
    # An agent or run copied into a bg job's folder belongs to the session that began first.
    by_root: dict = {}
    taken: set = set()
    for sid in sorted(roots, key=lambda s: (live[s]["started_at"] if s in live else
                                            min(_first_stamp(p) or _mtime(p) for p in files[s]), s in bgs, s)):
        r = live.get(sid)
        start = r["started_at"] if r else 0.0
        ags = [a for a in subagents(sid, bool(r), now, start, ancestors=True) if a["id"] not in taken]
        runs = [w for w in workflows(sid, bool(r), now, start, graph=True)
                if not w["superseded_by"] and w["id"] not in taken]
        taken.update(a["id"] for a in ags)
        taken.update(w["id"] for w in runs)
        ids = {a["id"] for a in ags}
        for a in ags:
            if a["parent"] not in ids:
                a["parent"] = ""
        notes = ev.get(sid, {}).get("notified", {})
        for a in ags:
            note = notes.get(a["tool_use_id"]) if a["tool_use_id"] else None
            if note and note[1] >= a["updated_at"] - RESTART:
                a["status"], a["ended_at"], a["doing"] = note[0], int(note[1]), ""
        by_root[sid] = (ags, runs)

    for sid in roots:
        r = live.get(sid)
        ags, runs = by_root[sid]
        if r:
            node = {"id": f"s:{sid}", "kind": "session", "parent": "", "depth": 0, "session_id": sid,
                    "name": r["name"], "title": r["title"], "bg": r["kind"] == "bg" or sid in bgs, "live": True,
                    "state": r["state"], "waiting_for": r["waiting_for"], "state_changed_at": r["state_changed_at"],
                    "started_at": r["started_at"], "ended_at": 0, "cwd": r["cwd"], "handed_off": False}
        else:
            newest = max(files[sid], key=_mtime)
            info = _ended_info(newest)
            node = {"id": f"s:{sid}", "kind": "session", "parent": "", "depth": 0, "session_id": sid,
                    "name": info["name"] or "Unnamed session", "title": info["name"], "bg": sid in bgs,
                    "live": False, "state": "ended", "waiting_for": "", "state_changed_at": int(_mtime(newest)),
                    "started_at": int(min(_first_stamp(p) or _mtime(p) for p in files[sid])),
                    "ended_at": int(_mtime(newest)), "cwd": info["cwd"], "handed_off": sid in moved}
        node["dir"] = short_dir(node["cwd"], 60)
        nodes.append(node)
        kids: dict = {}
        for a in ags:
            kids.setdefault(a["parent"], []).append(a)
        at_work: dict = {}

        def busy_below(a) -> bool:
            """The agent, or one it spawned, is still running."""
            if a["id"] not in at_work:
                at_work[a["id"]] = a["status"] in LIVE or any(busy_below(k) for k in kids.get(a["id"], []))
            return at_work[a["id"]]

        def add_agents(parent_aid: str, parent_id: str, depth: int, extra=()):
            mixed = list(kids.get(parent_aid, [])) + list(extra)
            shown, folded = _fold(mixed, lambda c: busy_below(c) if c.get("kind") != "run" else c["status"] in LIVE)
            for c in shown:
                if c.get("kind") == "run":
                    add_run(c["run"], parent_id, depth)
                    continue
                nid = f"a:{c['id']}"
                nodes.append({"id": nid, "kind": "fork" if c["fork"] else "subagent", "parent": parent_id,
                              "depth": depth, **{k: c[k] for k in ("type", "description", "background", "model",
                                                                    "worktree_branch", "status", "doing",
                                                                    "started_at", "updated_at", "ended_at")}})
                shown_agents[c["id"]] = (parent_id, sid)
                add_agents(c["id"], nid, depth + 1)
            if folded:
                nodes.append(_more(parent_id, depth, folded))

        def add_run(w: dict, parent_id: str, depth: int):
            wid = f"w:{w['id']}"
            nodes.append({"id": wid, "kind": "workflow", "parent": parent_id, "depth": depth,
                          **{k: w[k] for k in ("name", "description", "status", "started_at", "ended_at", "elapsed",
                                               "last_activity", "agent_count", "tokens", "error", "resumed_from",
                                               "phase_total", "current_phase", "problems")},
                          "superseded_by": "", "phases": [{"title": p["title"], "counts": p["counts"]}
                                                           for p in w["phases"]]})
            one = len(w["phases"]) == 1
            for i, p in enumerate(w["phases"]):
                pid, pdepth = wid, depth + 1
                if not one:
                    pid = f"p:{w['id']}:{i}"
                    nodes.append({"id": pid, "kind": "phase", "parent": wid, "depth": depth + 1, "index": i,
                                  "title": p["title"], "counts": p["counts"]})
                    pdepth = depth + 2
                for a in p["agents"]:
                    nodes.append({"id": f"g:{w['id']}:{a['id']}", "kind": "wf-agent", "parent": pid,
                                  "depth": pdepth, "label": a["label"], "status": a["status"],
                                  "superseded": a["superseded"], "doing": a["doing"], "updated_at": a["updated_at"]})
                if p.get("more"):
                    n = sum(p["more"].values())
                    nodes.append({"id": f"more:{pid}", "kind": "more", "parent": pid, "depth": pdepth,
                                  "count": n, "counts": p["more"], "kinds": {"wf-agent": n}})

        run_items = [{"kind": "run", "id": w["id"], "run": w, "status": w["status"], "started_at": w["started_at"],
                      "ended_at": w["ended_at"], "updated_at": w["last_activity"]} for w in runs]
        add_agents("", f"s:{sid}", 1, run_items)

    # Edges between drawn nodes only.
    drawn = {n["id"] for n in nodes}
    for (a, b), m in sorted(pairs.items()):
        if f"s:{a}" in drawn and f"s:{b}" in drawn:
            edges.append({"id": f"m:{a}:{b}", "kind": "message", "from": f"s:{a}", "to": f"s:{b}",
                          "count": m["count"], "first_at": int(m["first_at"]), "last_at": int(m["last_at"]),
                          "summary": m["summary"]})
    for a, b, at in sorted(handoffs):
        if f"s:{a}" in drawn and f"s:{b}" in drawn:
            edges.append({"id": f"c:{a}:{b}", "kind": "continued", "from": f"s:{a}", "to": f"s:{b}", "at": int(at)})
    # Parent ↔ subagent: hand-backs and messages land in the spawner's transcript (the
    # session's, or the parent agent's for a nested one).
    talk: dict = {}
    sources = [ev.get(sid, {}) for sid in roots]
    for pid, sid in {(p, s) for p, s in shown_agents.values() if p.startswith("a:")}:
        tx = _agent_tx(sid, pid[2:])
        if tx is not None:
            acc = interactions(tx)
            sources.append({"handbacks": [h for h in acc["handbacks"] if h["at"] >= now - WINDOW],
                            "calls": [c for c in acc["calls"].values() if c["at"] >= now - WINDOW]})
    seen_ev: set = set()
    for e in sources:
        for h in e.get("handbacks", []):
            if h["agent"] in shown_agents and (h["uuid"], h["agent"]) not in seen_ev:
                seen_ev.add((h["uuid"], h["agent"]))
                t = talk.setdefault(("h", h["agent"]), {"count": 0, "last_at": 0.0, "summary": ""})
                t["count"] += 1
                t["last_at"] = max(t["last_at"], h["at"])
        for c in e.get("calls", []):
            if c["to"] in shown_agents and not c["msg_id"] and (c["uuid"], c["to"]) not in seen_ev:
                seen_ev.add((c["uuid"], c["to"]))
                t = talk.setdefault(("k", c["to"]), {"count": 0, "last_at": 0.0, "summary": ""})
                t["count"] += 1
                if c["at"] >= t["last_at"]:
                    t["last_at"], t["summary"] = c["at"], c["summary"]
    for (kind, aid), t in sorted(talk.items()):
        parent_id = shown_agents[aid][0]
        ends = (f"a:{aid}", parent_id) if kind == "h" else (parent_id, f"a:{aid}")
        edge = {"id": f"{kind}:{aid}", "kind": "handback" if kind == "h" else "coordinator",
                "from": ends[0], "to": ends[1], "count": t["count"], "last_at": int(t["last_at"])}
        if kind == "k":
            edge["summary"] = t["summary"]
        edges.append(edge)
    return {"window": WINDOW, "nodes": nodes, "edges": edges}


def _more(parent_id: str, depth: int, folded: list) -> dict:
    counts: dict = {}
    kinds: dict = {}
    for c in folded:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
        k = "workflow" if c.get("kind") == "run" else "subagent"
        kinds[k] = kinds.get(k, 0) + 1
    return {"id": f"more:{parent_id}", "kind": "more", "parent": parent_id, "depth": depth,
            "count": len(folded), "counts": counts, "kinds": kinds}


def ago(secs: int) -> str:
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n:
            return f"{secs // n}{unit}"
    return f"{secs}s"


def cut(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:max(0, n - 1)] + "…"


def short_dir(cwd: str, n: int) -> str:
    """~ for home (either side of a symlinked /home), and keep the end of long paths."""
    for home in sorted({str(Path.home()), os.path.realpath(Path.home())}, key=len, reverse=True):
        if cwd == home or cwd.startswith(home + "/"):
            cwd = "~" + cwd[len(home):]
            break
    return cwd if len(cwd) <= n else "…" + cwd[-(n - 1):]


def render(rows: list[dict], width: int, color: bool) -> str:
    if not rows:
        return "no live Claude Code sessions\n"
    paint = (lambda code, s: f"\x1b[{code}m{s}\x1b[0m") if color else (lambda code, s: s)
    head = f"{'STATE':8} {'FOR':>4}  {'NAME':28} {'DIR':24} {'BRANCH':16} {'TMUX':12} "
    out = [paint("2", head + "LAST PROMPT")]
    for r in rows:
        name = cut(r["name"], 23) + " (bg)" if r["kind"] == "bg" else cut(r["name"], 28)
        state = paint(COLOR.get(r["state"], "0"), f"{r['state']:8}")
        fixed = (f" {ago(r['since']):>4}  {name:28} {short_dir(r['cwd'], 24):24} "
                 f"{cut(r['branch'] or '-', 16):16} {cut(r['tmux'] or '-', 12):12} ")
        room = max(10, width - len(head))
        if r["state"] == "blocked" and r["waiting_for"]:
            tail = paint("31", cut(r["waiting_for"], room))
        else:
            tail = paint("2", cut(r["prompt"], room))
        out.append(state + fixed + tail)
    return "\n".join(out) + "\n"


def summary(rows: list[dict]) -> str:
    n = {s: sum(r["state"] == s for r in rows) for s in ORDER}
    return f"{n['blocked']} blocked · {n['done']} done · {n['working']} working"


# --- the recap -----------------------------------------------------------------------
#
# `herd recap [since]` answers "what did I leave open?": every session you drove since your
# last workday, ended ones too, with what its last message asked of you, its live state and
# the track, review and git state of where it worked. A prompt is yours when you typed it:
# Claude Code marks those origin {kind: human}. Headless runs (entrypoint sdk-…), skill and
# command expansions, task notifications and messages from other sessions carry no such mark.

RECAP_LOOKBACK = 14     # days searched back for your last workday
SHORT = 180             # an ended one-prompt session shorter than this (s) folds into one line
REPLY_CAP, ASK_CAP, PROMPT_CAP, RECENT_PROMPTS = 6000, 400, 300, 5
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_STORY_NEEDLES = (b'"human"', b'"assistant"', b'-title"', b'"agent-name"', b'"pr-link"', b'"continued-in"')
_TITLES = {"custom-title": "customTitle", "ai-title": "aiTitle", "agent-name": "agentName"}
# A lead-in (a heading, a bold line or a short line ending in ":") that hands over to you.
_ASK = re.compile(r"your (?:turn|move|steps?)|next steps?|still open|waiting on you|before you|"
                  r"after (?:you )?merg|open items|left to do|\bto do\b|not (?:yet )?(?:tested|verified)", re.I)
_HEADING = re.compile(r"^\s*(?:#{1,6}\s|\*\*[^*]+\*\*:?\s*$)")
_story_cache: dict = {}


def recap_tz():
    """The zone the recap cuts days in: HERD_TZ (an IANA name such as Europe/Paris), else
    None, the system's local time. A machine kept on UTC needs it to cut days where you live."""
    name = os.environ.get("HERD_TZ")
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):    # OSError: a directory such as "Europe"
        raise ValueError(f"unknown time zone HERD_TZ={name!r}") from None


def _local(ts: float, tz) -> datetime:
    return datetime.fromtimestamp(ts, tz)


def _midnight(day: date, tz) -> float:
    return datetime(day.year, day.month, day.day, tzinfo=tz).timestamp()


def _stamp(ts: float, tz) -> str:
    d = _local(ts, tz)
    return f"{d:%a} {d.day} {d:%b} {d:%H:%M}"


def recap_window(spec: Optional[str], now: float, tz, had_prompts) -> tuple[float, str]:
    """Where the recap starts, and how to say it. With no spec, 00:00 of your last workday:
    the latest day before today on which you typed a prompt (had_prompts(t0, t1) says whether
    you did between two times), up to RECAP_LOOKBACK days back, else yesterday. Otherwise
    today, yesterday, a weekday (the latest past one, never today), Nh / Nd, or YYYY-MM-DD."""
    today = _local(now, tz).date()
    s = (spec or "").strip().lower()
    if not s:
        end = _midnight(today, tz)
        day = next((today - timedelta(days=k) for k in range(1, RECAP_LOOKBACK + 1)
                    if had_prompts(_midnight(today - timedelta(days=k), tz), end)), today - timedelta(days=1))
    elif s == "today":
        day = today
    elif s == "yesterday":
        day = today - timedelta(days=1)
    elif len(s) >= 3 and any(d.startswith(s) for d in DAYS):
        want = next(i for i, d in enumerate(DAYS) if d.startswith(s))
        day = today - timedelta(days=(today.weekday() - want - 1) % 7 + 1)
    else:
        m = re.fullmatch(r"(\d+)\s*([hd])", s)
        if m:
            start = now - int(m.group(1)) * (3600 if m.group(2) == "h" else 86400)
            return start, _stamp(start, tz)
        try:
            day = date.fromisoformat(s)
        except ValueError:
            raise ValueError(f"cannot read {spec!r}: try friday, yesterday, 2d, 6h or 2026-10-09") from None
    start = _midnight(day, tz)
    return start, _stamp(start, tz)


def _typed(rec: dict) -> str:
    """The text of a prompt you typed, or "" for anything else a user record carries."""
    origin = rec.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") != "human" or rec.get("isMeta") \
            or rec.get("isCompactSummary") or str(rec.get("entrypoint") or "").startswith("sdk"):
        return ""
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return ""
    text = "\n".join(_texts(rec)).strip()
    return "" if text.startswith("[Request interrupted") else text


def _fresh_story() -> dict:
    return {"prompts": [], "names": {}, "cwd": "", "cwds": [], "branch": "", "prs": [], "reply": None,
            "continued": [], "last_at": 0.0}


def _story_line(line: bytes, acc: dict) -> None:
    """One prefiltered transcript line into a session's story: the prompts you typed, its
    names, where it worked, PRs it opened, its newest assistant text, and any bg job it became."""
    try:
        rec = json.loads(line)
    except ValueError:
        return
    if not isinstance(rec, dict):
        return
    kind = rec.get("type")
    if kind in _TITLES:
        if rec.get(_TITLES[kind]):
            acc["names"][kind] = str(rec[_TITLES[kind]])
        return
    if kind == "pr-link":
        if rec.get("prUrl") and rec["prUrl"] not in acc["prs"]:
            acc["prs"].append(str(rec["prUrl"]))
        return
    if kind == "continued-in":
        if rec.get("continuedInSessionId"):
            acc["continued"].append(str(rec["continuedInSessionId"]))
        return
    if kind not in ("user", "assistant") or rec.get("isSidechain"):
        return
    if kind == "assistant" and (rec.get("message") or {}).get("model") == "<synthetic>":
        return                                  # written by Claude Code, not by Claude: no activity
    at = _epoch(rec.get("timestamp")) if rec.get("timestamp") else 0.0
    cwd = rec.get("cwd")
    if cwd and acc["cwd"] != cwd:               # every folder it worked in, the latest last
        acc["cwds"] = [c for c in acc["cwds"] if c != cwd] + [cwd]
        acc["cwd"] = cwd
    branch = rec.get("gitBranch")
    acc["branch"] = (branch if branch != "HEAD" else "") if branch is not None else acc["branch"]
    acc["last_at"] = max(acc["last_at"], at)
    if kind == "assistant":
        text = "\n".join(t for t in _texts(rec) if t.strip()).strip()
        if text:
            acc["reply"] = {"at": at, "text": text if len(text) <= REPLY_CAP else "…" + text[-REPLY_CAP:]}
        return
    text = _typed(rec)
    if text:
        acc["prompts"].append({"at": at, "uuid": str(rec.get("uuid") or ""), "text": cut(text, PROMPT_CAP)})


def story(paths: list[Path]) -> dict:
    """A session's story across every main transcript it has (one per project dir), read
    incrementally. A prompt copied into a second transcript counts once."""
    accs = [_grow(p, _story_cache, _STORY_NEEDLES, _fresh_story, _story_line) for p in sorted(paths, key=_mtime)]
    out = _fresh_story()
    seen: set = set()
    for a in accs:                              # oldest file first, so the newest wins
        for p in a["prompts"]:
            if not (p["uuid"] and p["uuid"] in seen):
                seen.add(p["uuid"])
                out["prompts"].append(p)
        out["names"].update(a["names"])
        out["cwd"] = a["cwd"] or out["cwd"]
        out["cwds"] = [c for c in out["cwds"] if c not in a["cwds"]] + a["cwds"]
        out["branch"] = a["branch"] or out["branch"]
        out["prs"] += [u for u in a["prs"] if u not in out["prs"]]
        if a["reply"] and (not out["reply"] or a["reply"]["at"] >= out["reply"]["at"]):
            out["reply"] = a["reply"]
        out["continued"] += a["continued"]
        out["last_at"] = max(out["last_at"], a["last_at"])
    out["prompts"].sort(key=lambda p: p["at"])
    return out


def ask_of(reply: str) -> str:
    """What a session's last message hands over to you: the section under its first lead-in
    that says so ("Your turn:", "## Next steps", "Still open:"), else its last paragraph."""
    lines = reply.splitlines()
    fenced, inside = [], False                  # a `# comment` in a code block is no heading
    for ln in lines:
        if ln.lstrip().startswith("```"):
            inside = not inside
            fenced.append(True)
        else:
            fenced.append(inside)
    heading = [bool(_HEADING.match(ln)) and not f for ln, f in zip(lines, fenced)]
    for i, ln in enumerate(lines):
        bare = ln.strip().strip("#*").strip()
        lead = heading[i] or (not fenced[i] and ((ln.lstrip().startswith("**") and len(bare) < 80)
                                                 or (bare.endswith(":") and len(bare) < 80)))
        if lead and _ASK.search(bare):
            end = next((j for j in range(i + 1, len(lines)) if heading[j]), len(lines))
            return cut(re.sub(r"^\s*#{1,6}\s+", "", "\n".join(lines[i:end]).replace("**", "")), ASK_CAP)
    prose = "\n".join(ln for ln, f in zip(lines, fenced) if not f)
    paras = [p for p in re.split(r"\n\s*\n", prose) if p.strip()]
    return cut(paras[-1].replace("**", ""), ASK_CAP) if paras else ""


def shepherd_board() -> dict:
    """`shepherd status --all --json`: tracks, reviews and their local state in every enrolled
    repo, or {} without shepherd. HERD_SHEPHERD names another binary; set empty, it is skipped."""
    exe = os.environ.get("HERD_SHEPHERD", "shepherd")
    if not exe or not shutil.which(exe):
        return {}
    try:
        r = subprocess.run([exe, "status", "--all", "--json"], capture_output=True, text=True, timeout=60)
        board = json.loads(r.stdout) if r.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    return board if isinstance(board, dict) else {}


def git_state(cwd: str, cache: dict) -> Optional[dict]:
    """The repo a session worked in, as it is now: uncommitted files, commits its upstream lacks,
    and whether it has anywhere to push. Local only: no fetch."""
    if cwd in cache:
        return cache[cwd]
    cache[cwd] = None
    if not cwd or not os.path.isdir(cwd):
        return None
    # --no-optional-locks: refreshing the index would take index.lock under another session's commit.
    r = subprocess.run(["git", "--no-optional-locks", "-C", cwd, "status", "--porcelain=v1", "-b"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    lines = r.stdout.splitlines()
    head = lines[0][3:] if lines and lines[0].startswith("## ") else ""
    head = head[len("No commits yet on "):] if head.startswith("No commits yet on ") else \
        "" if head.startswith("HEAD (no branch)") else head      # detached: no branch to push
    track = re.search(r"\[([^\]]*)\]$", head)
    counts = dict((w, int(n)) for w, n in re.findall(r"(ahead|behind) (\d+)", track.group(1))) if track else {}
    remotes = subprocess.run(["git", "-C", cwd, "remote"], capture_output=True, text=True).stdout.split()
    top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    cache[cwd] = {"root": top.stdout.strip(), "branch": re.split(r"\.\.\.| ", head, maxsplit=1)[0],
                  "dirty": max(0, len(lines) - 1), "ahead": counts.get("ahead", 0),
                  "behind": counts.get("behind", 0), "upstream": "..." in head,
                  "gone": bool(track and track.group(1) == "gone"), "remote": bool(remotes)}
    return cache[cwd]


def _review_id(rv: dict) -> str:
    rid = str(rv.get("id") or "")
    return f"#{rid}" if rid.isdigit() else rid or "review"


def _track_view(repo: dict, t: dict) -> dict:
    rv, owner = t.get("review") or {}, t.get("agent") or {}
    return {"repo": repo.get("name") or "", "track": t.get("name") or "", "status": t.get("status") or "",
            "goal": t.get("goal") or "", "worktree": t.get("worktree") or "", "branch": t.get("branch") or "",
            "owner": owner.get("name") or "", "owner_live": bool(owner.get("live")),
            "ahead": t.get("ahead") or 0, "behind": t.get("behind") or 0, "dirty": t.get("dirty_count") or 0,
            "unpushed": bool(rv.get("pushed_sha") and t.get("sha") and rv["pushed_sha"] != t["sha"]),
            "review": {"id": _review_id(rv), "state": rv.get("state") or "unknown", "url": rv.get("url") or "",
                       "checked_at": rv.get("checked_at"), "stale": bool(rv.get("stale"))}
            if rv.get("url") or rv.get("id") else None,
            "warnings": [str(w) for w in t.get("warnings") or []]}


def _track_flags(v: dict) -> tuple[list[str], bool]:
    """A track's flags, and whether any of them is still yours to act on."""
    flags, open_ = [], v["status"] != "merged" or any("finish" in w for w in v["warnings"])
    rv = v["review"]
    if rv:
        flags.append(f"{rv['id']} {rv['state']}")
        if v["unpushed"]:
            flags.append("commits not in review")
    elif v["ahead"]:
        flags.append(f"↑{v['ahead']} no review")
    if v["dirty"]:
        flags.append(f"{v['dirty']} dirty")
    flags += v["warnings"]
    return flags, open_ and bool(flags)


def _git_flags(g: dict) -> tuple[list[str], bool]:
    flags = []
    if g["dirty"]:
        flags.append(f"{g['dirty']} dirty")
    if not g["remote"]:
        flags.append("no remote")
    elif g["gone"]:
        flags.append("upstream gone")
    elif not g["upstream"] and g["branch"]:
        flags.append("never pushed")
    elif g["ahead"]:
        flags.append(f"↑{g['ahead']}")
    return flags, bool(flags)


def _files(n: int) -> str:
    return f"{n} changed file{'' if n == 1 else 's'}"


def _todo(state: str, waiting_for: str, views: list, gits: list) -> list[str]:
    """The next steps herd can tell from a session's state alone. Those only its last message
    knows (a decision, a dated wait) come from the recap skill's summary."""
    out = []
    if state == "blocked":
        out.append("Answer it in its terminal" + (f" (waiting for: {waiting_for})" if waiting_for else ""))
    for v in views:
        rv, name = v["review"], v["track"]
        rid = rv["id"] if rv and rv["id"] != "review" else "its review"
        if rv and rv["state"] == "changes_requested":
            out.append(f"Answer the review comments on {rid}")
        elif rv and rv["state"] == "draft":
            out.append(f"Mark {rid} ready for review, then get it merged")
        elif rv and rv["state"] in ("open", "approved", "unknown"):
            out.append(f"Get {rid} merged" + (": it is approved" if rv["state"] == "approved" else ""))
        if v["unpushed"]:
            out.append(f"Send the new commits on {name} to its review: `shepherd pr`")
        elif not rv and v["ahead"]:
            out.append(f"Open a review for {name}: `shepherd pr`")
        if v["dirty"]:
            out.append(f"Commit or drop the {_files(v['dirty'])} on {name}")
        if any("finish" in w for w in v["warnings"]):
            out.append(f"Close the track: `shepherd finish {name}`")
    for g in gits:
        repo = os.path.basename(g["root"]) or g["root"]
        if g["dirty"]:
            out.append(f"Commit or drop the {_files(g['dirty'])} in {repo}")
        if not g["remote"]:
            out.append(f"Give {repo} a remote: this disk holds its only copy")
        elif g["gone"]:
            out.append(f"Push {g['branch'] or 'the branch'} in {repo} again: the branch it tracked is gone")
        elif not g["upstream"] and g["branch"]:
            out.append(f"Push {g['branch']} in {repo}: it has never been pushed")
        elif g["ahead"]:
            out.append(f"Push the {g['ahead']} commit{'' if g['ahead'] == 1 else 's'} in {repo}")
    return out


def _opener(sid: str, row: dict) -> dict:
    """How to get back into a session from a terminal: attach to its tmux pane, resume it in a
    new one, or, live outside tmux, where to find it."""
    if not row:
        return {"how": "resume", "cmd": f"herd resume {sid[:8]}", "note": ""}
    if row.get("tmux"):
        return {"how": "attach", "cmd": f"herd go {sid[:8]}", "note": ""}
    if row.get("kind") == "bg":
        return {"how": "none", "cmd": "", "note": "Runs in the background, with no terminal"}
    return {"how": "none", "cmd": "", "note": f"Live outside tmux (pid {row.get('pid')}): switch to its terminal"}


def _when(ts: float, now: float, tz) -> str:
    if not ts:
        return "-"
    d, today = _local(ts, tz), _local(now, tz).date()
    if d.date() == today:
        return f"today {d:%H:%M}"
    return f"{d:%a %H:%M}" if (today - d.date()).days < 7 else f"{d:%d %b}"


def recap(spec: Optional[str] = None, now: Optional[float] = None) -> dict:
    """The sessions you drove since `spec` (see recap_window), most in need of you first,
    with the shepherd tracks none of them shows, and the short sessions folded away."""
    now = time.time() if now is None else now
    tz = recap_tz()
    files: dict = {}
    for p in (claude_dir() / "projects").glob("*/*.jsonl"):
        files.setdefault(p.stem, []).append(p)
    newest = {sid: max(_mtime(p) for p in ps) for sid, ps in files.items()}
    live = {r["session_id"]: r for r in sessions()}

    def stories_since(t0: float) -> dict:
        return {sid: story(ps) for sid, ps in files.items() if newest[sid] >= t0}

    def probe(sid: str, s: dict) -> bool:
        """An ended session with one prompt that lasted under SHORT: folded, not a workday."""
        last = max(s["last_at"], (s["reply"] or {}).get("at") or 0.0)
        return sid not in live and len(s["prompts"]) == 1 and last - s["prompts"][0]["at"] < SHORT

    def had_prompts(t0: float, t1: float) -> bool:
        return any(t0 <= p["at"] < t1 for sid, s in stories_since(t0).items() if not probe(sid, s)
                   for p in s["prompts"])

    start, label = recap_window(spec, now, tz, had_prompts)
    stories = stories_since(start)
    stories.update({sid: story(files[sid]) for sid in live if sid in files and sid not in stories})
    board = shepherd_board()
    tracks = [(repo, t) for repo in board.get("repos") or [] for t in repo.get("tracks") or []]
    me, git_cache, shown = os.environ.get("CLAUDE_CODE_SESSION_ID"), {}, set()
    mine = {sid: [p for p in s["prompts"] if p["at"] >= start] for sid, s in stories.items()}
    picked = {sid for sid in stories if sid != me and (mine[sid] or sid in live)}
    # A session continued in a bg job is that job's past, unless it still runs or you typed on in it.
    picked -= {sid for sid in picked if sid not in live
               and any(to in picked and {p["uuid"] for p in mine[sid]} <= {p["uuid"] for p in stories[to]["prompts"]}
                       for to in stories[sid]["continued"])}
    rows, folded = [], []
    for sid in picked:
        s, row, names = stories[sid], live.get(sid) or {}, stories[sid]["names"]
        name = row.get("name") or names.get("custom-title") or names.get("ai-title") \
            or names.get("agent-name") or sid[:8]
        first = mine[sid][0]["at"] if mine[sid] else 0.0
        last = max(s["last_at"], (s["reply"] or {}).get("at") or 0.0)
        if probe(sid, s):
            folded.append({"session_id": sid, "name": name, "when": _when(last, now, tz),
                           "prompt": cut(s["prompts"][0]["text"], 120)})
            continue
        cwd = s["cwd"] or row.get("cwd") or ""
        cwds = [os.path.realpath(c) for c in (s["cwds"] or [cwd]) if c][::-1]     # the latest first

        def holds(t: dict, c: str) -> bool:
            return bool(t.get("worktree") and c and (c + "/").startswith(os.path.realpath(t["worktree"]) + "/"))
        # The tracks the session owns or ended in, and the state of every other repo it worked in.
        # A track worktree it only passed through belongs to that track's owner (or the loose ends).
        own = [(repo, t) for repo, t in tracks
               if (t.get("agent") or {}).get("session_id") == sid or (cwds and holds(t, cwds[0]))]
        views = [_track_view(repo, t) for repo, t in own]
        gits, roots = [], set()
        for c in cwds[:5]:
            g = None if any(holds(t, c) for _, t in tracks) else git_state(c, git_cache)
            if g and g["root"] not in roots:
                roots.add(g["root"])
                gits.append(g)
        flags, open_, here = [], False, cwds[0] if cwds else ""
        for g in gits:
            more, also = _git_flags(g)
            if more and not (here + "/").startswith(os.path.realpath(g["root"]) + "/"):
                more = [f"{os.path.basename(g['root'])}: {more[0]}", *more[1:]]     # not where it ended
            flags += more
            open_ = open_ or also
        for v in views:
            shown.add((v["repo"], v["track"]))
            more, also = _track_flags(v)
            flags += [f"{v['track']}: {more[0]}", *more[1:]] if len(views) > 1 and more else more
            open_ = open_ or also
        reviewed = {v["review"]["url"] for v in views if v["review"]}
        flags += [f"PR {u.rstrip('/').rsplit('/', 1)[-1]}" for u in s["prs"] if u not in reviewed]
        state = row.get("state") or "ended"
        if state == "blocked" and row.get("waiting_for"):
            flags.insert(0, row["waiting_for"])
        if row.get("tmux"):
            flags.append(f"tmux {row['tmux']}")
        reply_text = (s["reply"] or {}).get("text") or ""
        # The branch as it is now: of the track or repo it last worked in; the transcript's only if
        # that folder is gone (a finished worktree). Claude Code records a worktree's branch as HEAD.
        track_here = next((t for _, t in own if t.get("worktree") and
                           (here + "/").startswith(os.path.realpath(t["worktree"]) + "/")), None)
        git_here = git_state(here, git_cache) if here and not track_here else None
        branch = (track_here or {}).get("branch") or (git_here or {}).get("branch") \
            or ("" if here and os.path.isdir(here) else s["branch"] or row.get("branch") or "")
        rows.append({
            "session_id": sid, "name": name, "title": names.get("ai-title") or "", "state": state,
            "live": bool(row), "waiting_for": row.get("waiting_for") or "", "tmux": row.get("tmux") or "",
            "cwd": cwd, "cwds": s["cwds"], "branch": branch, "first_at": first, "last_at": last,
            "when": _when(last, now, tz), "prompt_count": len(mine[sid]),
            "prompts": [{"at": p["at"], "text": p["text"]} for p in mine[sid][-RECENT_PROMPTS:]],
            "ask": ask_of(reply_text), "final_reply": reply_text, "prs": s["prs"],
            "tracks": views, "git": gits, "flags": flags, "open": bool(row) or open_,
            "todo": _todo(state, row.get("waiting_for") or "", views, gits), "opener": _opener(sid, row),
        })
    rows.sort(key=lambda r: (ORDER.get(r["state"], 3 if r["open"] else 4), -r["last_at"]))
    loose = []
    for repo in board.get("repos") or []:
        if repo.get("unpushed"):
            loose.append({"repo": repo.get("name") or "", "track": "", "status": "main",
                          "flags": [f"{repo['unpushed']} commits on {repo.get('main') or 'main'} not pushed"]})
        for t in repo.get("tracks") or []:
            v = _track_view(repo, t)
            if (v["repo"], v["track"]) not in shown and not (me and (t.get("agent") or {}).get("session_id") == me):
                loose.append({**v, "flags": _track_flags(v)[0]})
    return {"since": start, "until": now, "label": label,
            "tz": os.environ.get("HERD_TZ") or time.tzname[0], "sessions": rows, "loose_ends": loose,
            "folded": sorted(folded, key=lambda f: f["name"])}


def render_recap(rc: dict, width: int, color: bool) -> str:
    paint = (lambda code, s: f"\x1b[{code}m{s}\x1b[0m") if color else (lambda code, s: s)
    rows = rc["sessions"]
    live = sum(r["live"] for r in rows)
    top = f"herd recap · since {rc['label']} ({rc['tz']}) · {len(rows)} session{'' if len(rows) == 1 else 's'}" \
        + (f", {live} live" if live else "")
    out = [top, ""]
    if not rows:
        out.append("no prompts typed since then")
    else:
        head = f"{'STATE':8} {'LAST':11} {'NAME':28} {'DIR':24} {'BRANCH':16} "
        out.append(paint("2", head + "OPEN"))
        room = max(10, width - len(head))
        for r in rows:
            state = paint(COLOR.get(r["state"], "2"), f"{r['state']:8}")
            fixed = (f" {r['when']:11} {cut(r['name'], 28):28} {short_dir(r['cwd'], 24):24} "
                     f"{cut(r['branch'] or '-', 16):16} ")
            out.append(state + fixed + paint("33" if r["open"] else "2", cut(" · ".join(r["flags"]) or "-", room)))
            if r["ask"]:
                out.append(paint("2", "  ↳ " + cut(r["ask"], max(10, width - 4))))
    if rc["loose_ends"]:
        out += ["", paint("2", "LOOSE ENDS — shepherd tracks no session above shows")]
        for t in rc["loose_ends"]:
            where = f"{t['repo']} {t['track']}" if t["track"] else t["repo"]
            out.append("  " + cut(f"{where} ({t['status']}): " + (" · ".join(t["flags"]) or "clean"), width - 2))
    if rc["folded"]:
        n = len(rc["folded"])
        out += ["", paint("2", cut(f"+{n} short session{'' if n == 1 else 's'} (one prompt, under "
                                   f"{SHORT // 60} min): " + ", ".join(f["name"] for f in rc["folded"]), width))]
    return "\n".join(out) + "\n"


# --- the recap summary ---------------------------------------------------------------
#
# Topics: the sessions grouped into pieces of work, each with where it stands and its next steps,
# written by the rules in skills/recap/TOPICS.md. The recap skill saves them with
# `herd recap --save-summary` (JSON on stdin); the dashboard's Summarize button has Claude write
# them headless, with no tools at all: herd builds the recap JSON, Claude reads it on stdin and
# answers with the topics, and herd checks and saves them. The input is untrusted (your prompts,
# replies, titles), so the run that reads it can do nothing but answer.

SUMMARY_TOPICS, SUMMARY_ACTIONS, SUMMARY_SESSIONS = 40, 8, 20
SUMMARY_TIMEOUT = 600   # s a headless summary may take before it is stopped
TOPICS = Path(__file__).resolve().parent.parent.parent / "skills" / "recap" / "TOPICS.md"
_summarizer: dict = {"thread": None, "proc": None, "started_at": 0.0, "since": "", "error": ""}
_summarizer_lock = threading.Lock()


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "herd"


def summary_path() -> Path:
    return state_dir() / "recap-summary.json"


def clean_summary(raw) -> dict:
    """A summary as the skill writes it, checked and trimmed; ValueError when it is not one."""
    if not isinstance(raw, dict) or not isinstance(raw.get("topics"), list):
        raise ValueError("a summary is a JSON object with a \"topics\" list")
    topics = []
    for t in raw["topics"][:SUMMARY_TOPICS]:
        if not isinstance(t, dict) or not str(t.get("title") or "").strip():
            raise ValueError("every topic needs a \"title\"")
        acts, sids = t.get("actions") or [], t.get("sessions") or []
        if not isinstance(acts, list) or not isinstance(sids, list):
            raise ValueError("a topic's \"actions\" and \"sessions\" are lists")
        topics.append({"title": cut(str(t["title"]), 120), "state": "done" if t.get("state") == "done" else "open",
                       "summary": cut(str(t.get("summary") or ""), 400),
                       "actions": [cut(str(a), 300) for a in acts if str(a).strip()][:SUMMARY_ACTIONS],
                       "sessions": [str(s) for s in sids if str(s).strip()][:SUMMARY_SESSIONS]})
    since = raw.get("since")
    ok = isinstance(since, (int, float)) and not isinstance(since, bool) and math.isfinite(since)
    return {"since": float(since) if ok else None,
            "intro": cut(str(raw.get("intro") or ""), 400), "suggested": cut(str(raw.get("suggested") or ""), 400),
            "topics": topics}


def save_summary(raw) -> dict:
    out = {"generated_at": time.time(), **clean_summary(raw)}
    path = summary_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, path)                       # a reader never sees half a file
    return out


def load_summary() -> Optional[dict]:
    got = read_json(summary_path())
    return got if isinstance(got, dict) and isinstance(got.get("topics"), list) else None


def summary_from(text: str, rc: dict) -> dict:
    """Claude's answer as topics: the JSON object in it (a code fence around it is fine), set to
    the recap's own window, naming only sessions the recap holds. ValueError when there is none."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("the answer holds no JSON object")
    raw = json.loads(m.group(0))
    if not isinstance(raw, dict):
        raise ValueError("the answer is not a JSON object")
    known = {s["session_id"] for s in rc["sessions"]}
    for t in raw.get("topics") if isinstance(raw.get("topics"), list) else []:
        if isinstance(t, dict) and isinstance(t.get("sessions"), list):
            t["sessions"] = [x for x in t["sessions"] if str(x) in known]
    raw["since"] = rc["since"]
    return raw


def _kill(proc) -> None:
    """Stop a run and everything it started: it leads its own process group."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _summarize(cmd: list, payload: str, env: dict, d: Path, rc: dict) -> None:
    """The headless run, in its own thread: feed it the recap, stop it when it takes too long,
    and save the topics it answers with."""
    err, out = "", ""
    fd = os.open(d / "summarize.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)                        # also when an older herd made it
    with os.fdopen(fd, "w") as log:
        try:
            proc = subprocess.Popen(cmd, cwd=d, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                                    env=env, start_new_session=True, text=True)
        except OSError as e:
            with _summarizer_lock:
                _summarizer["error"] = f"could not start {cmd[0]}: {e.strerror}"
            return
        with _summarizer_lock:
            _summarizer["proc"] = proc
        try:
            out, _ = proc.communicate(payload, timeout=SUMMARY_TIMEOUT)
        except subprocess.TimeoutExpired:
            _kill(proc)
            err = f"stopped after {SUMMARY_TIMEOUT // 60} minutes"
    if not err and proc.returncode != 0:
        try:
            tail = (d / "summarize.log").read_text(errors="replace").strip().splitlines()[-1:]
        except OSError:
            tail = []
        err = f"Claude exited with {proc.returncode}" + (f": {cut(tail[0], 200)}" if tail else "")
    elif not err:
        try:
            reply = json.loads(out)             # --output-format json: {"result": …, "is_error": …}
            if not isinstance(reply, dict):
                raise ValueError("not a result object")
            if reply.get("is_error"):
                raise ValueError(str(reply.get("result") or "Claude reported an error"))
            save_summary(summary_from(str(reply.get("result") or ""), rc))
        except ValueError as e:
            err = f"Claude's answer wasn't topics: {cut(str(e), 200)}"
    with _summarizer_lock:
        _summarizer.update(error=err, proc=None)


def summarize_start(spec: Optional[str], rc: dict) -> dict:
    """Have Claude write topics for this recap, unless a run is going. It runs headless with no
    tools, MCP servers, skills or hooks, and leaves no transcript: it can only answer."""
    with _summarizer_lock:
        t = _summarizer["thread"]
        if t is not None and t.is_alive():
            return _status()
        _summarizer.update(thread=None, proc=None, started_at=time.time(), since=spec or "", error="")
        try:
            rules = TOPICS.read_text()
        except OSError:
            _summarizer["error"] = f"can't read the topic rules in {TOPICS}"
            return _status()
        d = state_dir()
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(d, 0o700)                      # it holds your prompts and replies
        prompt = ("The JSON on stdin is the output of `herd recap --json`. Following the rules below, answer "
                  "with the topics JSON object only, with nothing before or after it.\n\n" + rules)
        cmd = [os.environ.get("HERD_CLAUDE") or "claude", "-p", "--tools", "", "--strict-mcp-config",
               "--disable-slash-commands", "--no-session-persistence",
               "--settings", json.dumps({"disableAllHooks": True}), "--output-format", "json", "--", prompt]
        env = {k: v for k, v in os.environ.items() if k not in SESSION_ENV}
        th = threading.Thread(target=_summarize, args=(cmd, json.dumps(rc), env, d, rc), daemon=True)
        _summarizer["thread"] = th
        th.start()
        return _status()


def summarize_stop() -> None:
    with _summarizer_lock:
        proc = _summarizer["proc"]
    if proc is not None and proc.poll() is None:
        _kill(proc)


def _status() -> dict:
    s = _summarizer
    running = s["thread"] is not None and s["thread"].is_alive()
    err = "" if running else s["error"]
    if err:
        saved = load_summary()                  # a newer summary (a /recap since) outdates the failure
        if saved and (saved.get("generated_at") or 0) > s["started_at"]:
            err = ""
    return {"running": running, "started_at": s["started_at"] or None, "error": err}


def summarize_status() -> dict:
    with _summarizer_lock:
        return _status()


# --- commands ------------------------------------------------------------------------

def cmd_ls(args) -> int:
    rows = sessions()
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    sys.stdout.write(render(rows, shutil.get_terminal_size((160, 24)).columns, color))
    return 0


def cmd_watch(args) -> int:
    color = not os.environ.get("NO_COLOR")
    blocked: Optional[set] = None
    sys.stdout.write("\x1b[?1049h")             # alternate screen: the shell comes back intact
    try:
        while True:
            rows = sessions()
            now_blocked = {r["session_id"] for r in rows if r["state"] == "blocked"}
            bell = "\a" if blocked is not None and now_blocked - blocked and not args.no_bell else ""
            blocked = now_blocked
            size = shutil.get_terminal_size((160, 24))
            body = render(rows, size.columns, color).splitlines()[:size.lines - 2]
            top = f"herd · {summary(rows)} · {time.strftime('%H:%M:%S')} · Ctrl-C to quit"
            sys.stdout.write("\x1b[H\x1b[2J" + bell + top + "\n\n" + "\n".join(body))
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        sys.stdout.write("\x1b[?1049l")
        sys.stdout.flush()


def _tmux_has(name: str) -> bool:
    return subprocess.run(["tmux", "has-session", "-t", f"={name}"],
                          capture_output=True).returncode == 0


def cmd_new(args) -> int:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        print("herd: name may hold only letters, digits, - and _ (tmux rewrites . and :)",
              file=sys.stderr)
        return 2
    if not shutil.which("tmux"):
        print("herd: tmux is not installed", file=sys.stderr)
        return 1
    if _tmux_has(args.name):
        print(f"herd: tmux session `{args.name}` already exists — herd go {args.name}",
              file=sys.stderr)
        return 1
    cwd = os.path.abspath(os.path.expanduser(args.dir))
    if not os.path.isdir(cwd):
        print(f"herd: no such directory: {cwd}", file=sys.stderr)
        return 1
    unset = [a for v in SESSION_ENV for a in ("-u", v)]
    claude = os.environ.get("HERD_CLAUDE") or "claude"
    r = subprocess.run(["tmux", "new-session", "-d", "-s", args.name, "-c", cwd,
                        "env", *unset, claude, "--name", args.name, *args.claude_args])
    if r.returncode != 0:
        return r.returncode
    print(f"started `{args.name}` in {cwd} — herd go {args.name}")
    return 0


def _matches(key: str, r: dict) -> bool:
    """The row `key` names: its name, title, pid, or a session id prefix of 4+ characters."""
    return key in (r["name"], r["title"], str(r["pid"])) or \
        (len(key) >= 4 and r["session_id"].startswith(key))


def resolve(key: str, rows: list[dict]) -> Optional[str]:
    """A tmux target for a tmux session name, session name, title, pid or session id prefix."""
    if shutil.which("tmux") and _tmux_has(key):
        return key
    for r in rows:
        if _matches(key, r):
            return r["tmux"] or None
    return None


def cmd_go(args) -> int:
    rows = sessions()
    target = resolve(args.name, rows)
    if not target:
        hit = next((r for r in rows if _matches(args.name, r)), None)
        why = f"`{args.name}` is not running in tmux (cwd {hit['cwd']})" if hit \
            else f"no session or tmux session called `{args.name}`"
        print(f"herd: {why}", file=sys.stderr)
        return 1
    session = target.split(":", 1)[0]
    if ":" in target:
        subprocess.run(["tmux", "select-window", "-t", target.rsplit(".", 1)[0]], capture_output=True)
        subprocess.run(["tmux", "select-pane", "-t", target], capture_output=True)
    verb = "switch-client" if os.environ.get("TMUX") else "attach-session"
    os.execvp("tmux", ["tmux", verb, "-t", session])
    return 0


def _project_slug(cwd: str) -> str:
    """The folder Claude Code files a session's transcripts under, for the folder it started in."""
    return re.sub(r"[^A-Za-z0-9-]", "-", cwd)


def find_session(key: str) -> Optional[dict]:
    """A session that may have ended, by session id prefix (4+ characters), name or title. Live
    ones first, then transcripts newest first. {session_id, name, cwd, row}: cwd is the folder to
    resume it from (the one its newest transcript is filed under), row its live entry or None."""
    for r in sessions():
        if _matches(key, r):
            return {"session_id": r["session_id"], "name": r["name"], "cwd": r["cwd"], "row": r}
    paths = sorted((claude_dir() / "projects").glob("*/*.jsonl"), key=_mtime, reverse=True)
    hit = next((p for p in paths if len(key) >= 4 and p.stem.startswith(key)), None) \
        or next((p for p in paths[:500] if _ended_info(p)["name"] == key), None)
    if hit is None:
        return None
    s = story(_main_transcripts(hit.stem))
    cwd = next((c for c in reversed(s["cwds"]) if _project_slug(c) == hit.parent.name), "") \
        or (s["cwds"][-1] if s["cwds"] else "")         # else the folder it worked in last
    names = s["names"]
    name = names.get("custom-title") or names.get("ai-title") or names.get("agent-name") or hit.stem[:8]
    return {"session_id": hit.stem, "name": name, "cwd": cwd, "row": None}


def _resuming(sid: str) -> Optional[str]:
    """The tmux session already running `claude --resume <sid>`, if any: claude takes a moment to
    register, and a second copy in that moment would write the same conversation."""
    r = subprocess.run(["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{pane_start_command}"],
                       capture_output=True, text=True)
    for ln in r.stdout.splitlines():
        name, _, start = ln.partition("\t")
        if re.search(rf"--resume\W+{re.escape(sid)}\b", start):
            return name
    return None


def _tmux_name(name: str, sid: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:24].strip("-") or "resume"
    for cand in (base, f"{base}-{sid[:4]}", *(f"{base}-{sid[:4]}-{i}" for i in range(2, 10))):
        if not _tmux_has(cand):
            return cand
    return f"resume-{sid[:8]}"


def cmd_resume(args) -> int:
    hit = find_session(args.name)
    if not hit:
        print(f"herd: no session called `{args.name}` (a session id prefix, name or title)", file=sys.stderr)
        return 1
    row = hit["row"]
    if row:
        if row["tmux"]:
            if args.detach:
                print(f"`{row['name']}` is live in tmux {row['tmux']} — herd go {args.name}")
                return 0
            return cmd_go(argparse.Namespace(name=args.name))
        where = "runs in the background" if row["kind"] == "bg" else \
            f"is live in a terminal outside tmux (pid {row['pid']}, {row['cwd']})"
        print(f"herd: `{row['name']}` {where}; switch to it there, since a second copy would split "
              "the conversation", file=sys.stderr)
        return 1
    if not shutil.which("tmux"):
        print("herd: tmux is not installed", file=sys.stderr)
        return 1
    already = _resuming(hit["session_id"])
    if already:
        print(f"`{hit['name']}` is already resuming in tmux session `{already}` — herd go {already}")
        if args.detach:
            return 0
        verb = "switch-client" if os.environ.get("TMUX") else "attach-session"
        os.execvp("tmux", ["tmux", verb, "-t", already])
    cwd = hit["cwd"]
    if not cwd or not os.path.isdir(cwd):
        print(f"herd: `{hit['name']}` started in {cwd or 'a folder herd can not tell'}, which is gone, "
              "so claude can't find the conversation from there", file=sys.stderr)
        return 1
    tname = _tmux_name(hit["name"], hit["session_id"])
    unset = [a for v in SESSION_ENV for a in ("-u", v)]
    claude = os.environ.get("HERD_CLAUDE") or "claude"
    r = subprocess.run(["tmux", "new-session", "-d", "-s", tname, "-c", cwd,
                        "env", *unset, claude, "--resume", hit["session_id"]])
    if r.returncode != 0:
        return r.returncode
    print(f"resumed `{hit['name']}` in tmux session `{tname}` ({short_dir(cwd, 60)}) — herd go {tname}")
    if args.detach:
        return 0
    verb = "switch-client" if os.environ.get("TMUX") else "attach-session"
    os.execvp("tmux", ["tmux", verb, "-t", tname])
    return 0


def cmd_recap(args) -> int:
    if args.save_summary:
        try:
            saved = save_summary(json.loads(sys.stdin.read()))
        except ValueError as e:                 # bad JSON is a ValueError too
            print(f"herd: not a recap summary: {e}", file=sys.stderr)
            return 2
        n = len(saved["topics"])
        print(f"saved {n} topic{'' if n == 1 else 's'} for the dashboard's Recap page ({summary_path()})")
        return 0
    try:
        rc = recap(args.since)
    except ValueError as e:
        print(f"herd: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(rc, indent=2))
        return 0
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    sys.stdout.write(render_recap(rc, shutil.get_terminal_size((160, 24)).columns, color))
    return 0


# --- dashboard -----------------------------------------------------------------------

STATIC = Path(__file__).resolve().parent / "static"
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
COOKIE = "herd"


def token() -> str:
    """The dashboard's access token, kept across restarts so bookmarks keep working."""
    path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "herd" / "token"
    try:
        tok = path.read_text().strip()
        if len(tok) >= 32:
            return tok
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    return tok


class Handler(BaseHTTPRequestHandler):
    server_version = "herd"
    lock = threading.Lock()                     # the caches are shared between request threads

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: Optional[dict] = None):
        self.send_response(code)
        for k, v in {"Content-Type": ctype, "Content-Length": str(len(body)), "Cache-Control": "no-store",
                     "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                     "Content-Security-Policy": "default-src 'self'; img-src 'self' data:",
                     **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        jar = SimpleCookie(self.headers.get("Cookie") or "")
        return COOKIE in jar and secrets.compare_digest(jar[COOKIE].value, self.server.token)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_POST(self):  # noqa: N802
        # Starting Claude is the one thing this server does besides reading, so on top of the
        # local Host and the cookie it wants the same origin and a header no plain form can send.
        host = self.headers.get("Host") or ""
        if host.rsplit(":", 1)[0] not in LOCAL_HOSTS:
            return self._send(403, b"forbidden host", "text/plain")
        origin = self.headers.get("Origin")
        if self.headers.get("X-Herd") != "1" or (origin is not None and urlparse(origin).netloc != host):
            return self._send(403, b"forbidden origin", "text/plain")
        if not self._authed():
            return self._send(401, b"Open the URL printed by `herd serve` (it carries the access token).", "text/plain")
        if urlparse(self.path).path != "/api/recap/summarize":
            return self._send(404, b"not found", "text/plain")
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 4096)) or b"{}")
            spec = (body.get("since") if isinstance(body, dict) else None) or None
            if spec is not None and not isinstance(spec, str):
                raise ValueError("since is a string such as friday or 2d")
            with self.lock:
                rc = recap(spec)                # also checks the window
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        return self._json(202, summarize_start(spec, rc))

    def do_GET(self):  # noqa: N802
        # Only a page served from this machine may read it: a foreign Host is DNS rebinding.
        if (self.headers.get("Host") or "").rsplit(":", 1)[0] not in LOCAL_HOSTS:
            return self._send(403, b"forbidden host", "text/plain")
        url = urlparse(self.path)
        tok = self.server.token
        authed = self._authed()
        given = parse_qs(url.query).get("t", [""])[0]
        if not authed and given and secrets.compare_digest(given, tok):
            return self._send(302, b"", "text/plain", {
                "Location": "/", "Set-Cookie": f"{COOKIE}={tok}; HttpOnly; SameSite=Strict; Path=/"})
        if not authed:
            return self._send(401, b"Open the URL printed by `herd serve` (it carries the access token).",
                              "text/plain")
        if url.path == "/api/sessions":
            with self.lock:
                rows = sessions(detail=True)
            for r in rows:
                r["dir"] = short_dir(r["cwd"], 60)
            return self._send(200, json.dumps({"now": int(time.time()), "host": os.uname().nodename,
                                               "sessions": rows}).encode(), "application/json")
        if url.path == "/api/graph":
            with self.lock:
                g = graph()
            return self._send(200, json.dumps({"now": int(time.time()), "host": os.uname().nodename, **g}).encode(),
                              "application/json")
        if url.path == "/api/recap":
            spec = parse_qs(url.query).get("since", [""])[0] or None
            with self.lock:
                try:
                    rc = recap(spec)
                except ValueError as e:
                    return self._json(400, {"error": str(e)})
                rc["summary"], rc["summarizing"] = load_summary(), summarize_status()
            for r in rc["sessions"]:
                r.pop("final_reply", None)      # the page shows the ↳ line; the full reply is the skill's
                r["dir"] = short_dir(r["cwd"], 60)
            return self._json(200, {"now": int(time.time()), "host": os.uname().nodename, **rc})
        name = "index.html" if url.path in ("/", "/index.html") else \
            url.path[len("/static/"):] if url.path.startswith("/static/") else ""
        f = STATIC / name
        if name and "/" not in name and f.suffix in STATIC_TYPES and f.is_file():
            return self._send(200, f.read_bytes(), STATIC_TYPES[f.suffix])
        return self._send(404, b"not found", "text/plain")


PORT = 7798


def dashboard_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/?t={token()}"


def forward_hint(port: int) -> str:
    return (f"from your laptop: ssh -NL {port}:localhost:{port} {socket.getfqdn()}  "
            f"(or forward port {port} in VS Code's Ports tab)")


def answers(port: int) -> bool:
    """Whether a herd dashboard answers on 127.0.0.1:port (it says so in its Server header)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request("GET", "/api/sessions", headers={"Host": f"127.0.0.1:{port}"})
        r = conn.getresponse()
        r.read()
        return (r.getheader("Server") or "").startswith(Handler.server_version)
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def serve_port(argv: list[str]) -> Optional[int]:
    """The port a command line runs `herd serve` on, or None when it is something else."""
    at = 0
    if argv and os.path.basename(argv[0]).startswith("python"):     # python3 [-I …] herd serve
        at = next((i for i, a in enumerate(argv) if i and not a.startswith("-")), len(argv))
    if at >= len(argv) or os.path.basename(argv[at]) not in ("herd", "herd.py") or argv[at + 1:at + 2] != ["serve"]:
        return None
    rest = argv[at + 2:]
    for i, a in enumerate(rest):
        if a in ("-p", "--port") and i + 1 < len(rest) and rest[i + 1].isdigit():
            return int(rest[i + 1])
        if a.startswith("--port=") and a[7:].isdigit():
            return int(a[7:])
        if a.startswith("-p") and a[2:].isdigit():
            return int(a[2:])
    return PORT


def serving_port() -> Optional[int]:
    """The port of a `herd serve` you already run on this machine, from its command line."""
    for cmdline in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            if cmdline.parent.stat().st_uid != os.getuid():
                continue
            port = serve_port([a.decode(errors="replace") for a in cmdline.read_bytes().split(b"\0") if a])
        except OSError:
            continue
        if port:
            return port
    return None


def cmd_url(args) -> int:
    port = args.port or serving_port() or PORT
    print(dashboard_url(port), flush=True)
    if not answers(port):
        start = "herd serve" + ("" if port == PORT else f" -p {port}")
        print(f"herd: no dashboard on port {port} — start one: tmux new -d -s herd-ui {start}", file=sys.stderr)
        return 1
    print(forward_hint(port), file=sys.stderr)
    return 0


def cmd_serve(args) -> int:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE and answers(args.port):
            print(f"herd dashboard already running: {dashboard_url(args.port)}")
            print(forward_hint(args.port))
            return 0
        print(f"herd: cannot listen on 127.0.0.1:{args.port}: {e.strerror}", file=sys.stderr)
        return 1
    server.daemon_threads = True
    server.token = token()
    port = server.server_address[1]
    print(f"herd dashboard: http://127.0.0.1:{port}/?t={server.token}", flush=True)
    print(f"{forward_hint(port)}  Ctrl-C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        summarize_stop()                        # a summary run doesn't outlive the dashboard
        server.server_close()
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="herd", description="Attention list of live Claude Code sessions.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("ls", help="list live sessions, most urgent first")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("watch", help="redraw the list until Ctrl-C")
    p.add_argument("-n", "--interval", type=float, default=2.0)
    p.add_argument("--no-bell", action="store_true")
    p = sub.add_parser("new", help="start claude in a detached tmux session")
    p.add_argument("name")
    p.add_argument("dir", nargs="?", default=".")
    p = sub.add_parser("go", help="attach to a session's tmux pane")
    p.add_argument("name")
    p = sub.add_parser("serve", help="dashboard on localhost (already running: prints its URL)")
    p.add_argument("-p", "--port", type=int, default=PORT)
    p = sub.add_parser("url", help="print the dashboard's URL (with its token) and how to reach it")
    p.add_argument("-p", "--port", type=int, help=f"default: the running dashboard's, else {PORT}")
    p = sub.add_parser("recap", help="what you left open: the sessions you drove since your last workday")
    p.add_argument("since", nargs="?", help="today, yesterday, friday, 2d, 6h or 2026-10-09 "
                                            "(default: 00:00 of your last workday; days cut in $HERD_TZ)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--save-summary", action="store_true",
                   help="save the recap skill's topics (JSON on stdin) for the dashboard's Recap page")
    p = sub.add_parser("resume", help="back into a session, ended or not: claude --resume in tmux, or herd go")
    p.add_argument("name", help="a session id prefix (as the Recap page copies it), name or title")
    p.add_argument("--detach", action="store_true", help="start it in tmux without attaching")
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["ls", *argv]
    extra: list[str] = []
    if argv[0] == "new" and "--" in argv:       # everything after -- goes to claude untouched
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    args = ap.parse_args(argv)
    args.claude_args = extra
    return {"ls": cmd_ls, "watch": cmd_watch, "new": cmd_new, "go": cmd_go,
            "serve": cmd_serve, "url": cmd_url, "recap": cmd_recap, "resume": cmd_resume}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
