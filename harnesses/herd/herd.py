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
    herd runs [--json] [--sources FILE]      headless claude -p runs and cron logs, judged
    herd snapshot [--sources FILE] …         tracks, sessions and runs in one JSON (the herd mod's)
    herd ack KEY… | --failed                 mark run failures as seen

Read-only over what Claude Code already writes: its session registry
(~/.claude/sessions/<pid>.json), transcripts and workflow journals, plus shepherd's
status, `git status` for the recap and the run logs a sources file names. No hooks; its
own state is the dashboard's access token, the recap skill's last summary and the acked
run failures. Stdlib only, Python 3.9+.
See harnesses/herd/README.md."""

from __future__ import annotations

import argparse
import copy
import errno
import fcntl
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
from decimal import ROUND_HALF_UP, Decimal
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
            "kind": e.get("kind") or "interactive", "state": state, "status": status,
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


def shepherd_cmd() -> Optional[list[str]]:
    """How to run shepherd: $HERD_SHEPHERD (set empty: not at all), else `shepherd` on PATH, else
    the shepherd beside herd in this checkout; None when there is none."""
    exe = os.environ.get("HERD_SHEPHERD")
    if exe is not None:
        found = shutil.which(exe) if exe else None
        return [found] if found else None
    found = shutil.which("shepherd")
    if found:
        return [found]
    sibling = Path(__file__).resolve().parent.parent / "shepherd" / "shepherd.py"
    return [sys.executable, str(sibling)] if sibling.is_file() else None


def shepherd_read(timeout: float = 60) -> tuple[dict, Optional[str], bool]:
    """`shepherd status --all --json` as (board, error, missing): tracks, reviews and their local
    state in every enrolled repo. Missing: no shepherd to run; error: it ran and failed."""
    cmd = shepherd_cmd()
    if cmd is None:
        return {}, None, True
    # Its `git status` in every worktree takes no optional index lock, so a refresh never makes
    # the person's own `git add` fail.
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        r = subprocess.run([*cmd, "status", "--all", "--json"], capture_output=True, text=True,
                           timeout=timeout, env=env)
    except FileNotFoundError:
        return {}, None, True
    except (OSError, subprocess.TimeoutExpired) as e:
        return {}, f"shepherd status: {e}", False
    if r.returncode != 0:
        return {}, f"shepherd status exited {r.returncode}: {first_line_of(r.stderr or r.stdout, 140) or 'no output'}", False
    try:
        board = json.loads(r.stdout)
    except ValueError:
        return {}, f"shepherd status printed no JSON: {first_line_of(r.stdout, 100) or 'nothing'}", False
    return (board, None, False) if isinstance(board, dict) else ({}, "not shepherd status JSON (no repos)", False)


def shepherd_board() -> dict:
    """shepherd_read's board, or {} without shepherd or when it fails."""
    return shepherd_read()[0]


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


def recap(spec: Optional[str] = None, now: Optional[float] = None, board: Optional[dict] = None) -> dict:
    """The sessions you drove since `spec` (see recap_window), most in need of you first,
    with the shepherd tracks none of them shows, and the short sessions folded away.
    `board` is shepherd's status when the caller has it already (shepherd's own dashboard)."""
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
    board = shepherd_board() if board is None else board
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


# --- headless runs -------------------------------------------------------------------
# What unattended `claude -p --output-format stream-json` runs, and the text logs of the
# cron jobs around them, say: each log judged on its own (ok, asked, denied, running,
# quiet, unfinished, failed, no result, no line, late). Which logs, what a good line
# looks like and how often a run is due come from a sources file (presets/README.md).
# Ported from the fleet mod (mods/fleet/hooks/fleet.ts at 3aa5437), wording, rounding
# and the patterns' JavaScript meaning included: tests/runs_golden.json holds what that
# code said for the same inputs.

HOUR_MS = 3_600_000
RUN_MAX_AGE_HOURS = 192   # logs older than this are not shown
RUN_RUNNING_MINUTES = 15  # a log with no verdict yet, written this recently, is a run still going
RUN_LATEST = 12           # stream logs shown per source: the newest of each run name
SNAPSHOT_SCHEMA = 3
ACKED_MAX = 500
LOG_CACHE_MAX = 400

# JavaScript's whitespace: what String.prototype.trim strips and \s matches.
_JS_WS = ("\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009"
          "\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")


def _js_re(pattern: str, flags: int = 0) -> re.Pattern:
    """A JavaScript RegExp (no u, m or s flag) as Python's: \\w, \\d and \\b ASCII-only, \\s
    JavaScript's whitespace, `.` stopping at its line terminators, `$` at the very end only."""
    out, i, in_class = [], 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            esc = pattern[i + 1]
            out.append(_JS_WS if esc == "s" and in_class else f"[{_JS_WS}]" if esc == "s"
                       else f"[^{_JS_WS}]" if esc == "S" and not in_class else pattern[i:i + 2])
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c == ".":
            c = "[^\n\r\u2028\u2029]"
        elif c == "$":
            c = r"\Z"
        out.append(c)
        i += 1
    return re.compile("".join(out), re.ASCII | flags)


def _trim(s: str) -> str:
    return s.strip(_JS_WS)


def _num(v) -> Optional[float]:
    """A JSON number that is finite, as JavaScript sees one (a bool is not)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        return v if math.isfinite(v) else None
    except OverflowError:                       # an int too big for a double is Infinity there
        return None


def _count(v) -> int:
    n = _num(v)
    return max(0, math.floor(n)) if n is not None else 0


def _text(v) -> Optional[str]:
    return v if isinstance(v, str) and v != "" else None


def _js_round(x: float) -> int:
    """Math.round: halves go up (Python's round() goes to even)."""
    r = math.floor(x)
    return r + 1 if x - r >= 0.5 else r


def _fixed2(x: float) -> str:
    """Number.prototype.toFixed(2): the exact value, halves away from zero."""
    return str(Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _u16(s: str) -> int:
    """A string's JavaScript length: UTF-16 code units."""
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def _u16_cut(s: str, n: int) -> str:
    """JavaScript's s.slice(0, n): n UTF-16 code units, half a surrogate pair included."""
    return s.encode("utf-16-le", "surrogatepass")[:2 * max(0, n)].decode("utf-16-le", "surrogatepass")


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def ago_ms(then_ms: float, now_ms: float) -> str:
    """How long ago, in one short word: 40s, 12m, 5h, 3d."""
    s = max(0, _js_round((now_ms - then_ms) / 1000))
    return f"{s}s" if s < 60 else f"{s // 60}m" if s < 3600 else f"{s // 3600}h" if s < 86400 else f"{s // 86400}d"


def first_line_of(text: str, n: int = 100) -> str:
    """The first non-blank line of a message, cut to n characters."""
    line = _trim(next((ln for ln in text.split("\n") if _trim(ln)), ""))
    return _u16_cut(line, n - 1) + "…" if _u16(line) > n else line


def last_line_of(text: str, n: int = 100) -> str:
    """The last non-blank line of a message, cut to n characters."""
    line = _trim(next((ln for ln in reversed(text.split("\n")) if _trim(ln)), ""))
    return _u16_cut(line, n - 1) + "…" if _u16(line) > n else line


def day_of(ms: float) -> str:
    """A log's day as MM-DD, local time."""
    return time.strftime("%m-%d", time.localtime(ms / 1000))


def stamp_of(ms: float) -> str:
    """A log's day and minute as MM-DD HH:MM, local time."""
    return time.strftime("%m-%d %H:%M", time.localtime(ms / 1000))


def _home() -> Optional[str]:
    return os.environ.get("HOME") or str(Path.home())


def expand_home(path: str, home: Optional[str]) -> str:
    """`~` and `~/…` under HOME; anything else as given."""
    if home is None or (path != "~" and not path.startswith("~/")):
        return path
    return home.rstrip("/") + path[1:]


def _source_re(value, where: str, errors: list) -> Optional[re.Pattern]:
    """A sources file's pattern: Python's re, searched in each trimmed line, \\w \\d \\b ASCII-only."""
    src = _text(value)
    if src is None:
        return None
    try:
        return re.compile(src, re.ASCII)
    except re.error as e:
        errors.append(f"{where}: bad pattern {json.dumps(src, ensure_ascii=False)}: {e}")
        return None


def _positive(v, fallback: float) -> float:
    n = _num(v)
    return n if n is not None and n > 0 else fallback


def _expect(v) -> Optional[float]:
    n = _num(v)
    return n if n is not None and n > 0 else None


_NAMED_GLOB = _js_re(r"^([^=/~*?]+)=(.+)$")


def sources_of(globs: list, file) -> dict:
    """The run sources: bare globs or `Label=glob` (stream logs), and a sources file's `runs`
    (stream logs) and `lines` (text logs judged by their last matching line). A malformed entry
    is skipped and named in `errors`; the rest still load."""
    runs, lines, errors = [], [], []

    def run_of(label: str, glob: str, entry: dict) -> dict:
        return {"label": label, "glob": glob,
                "maxAgeHours": _positive(entry.get("maxAgeHours"), RUN_MAX_AGE_HOURS),
                "runningMinutes": _positive(entry.get("runningMinutes"), RUN_RUNNING_MINUTES),
                "latest": math.floor(_positive(entry.get("latest"), RUN_LATEST)),
                "expectEveryHours": _expect(entry.get("expectEveryHours"))}

    for raw in globs:
        spec = _trim(raw)
        if not spec:
            continue
        named = _NAMED_GLOB.search(spec)
        runs.append(run_of(_trim(named.group(1)), _trim(named.group(2)), {}) if named else run_of("runs", spec, {}))
    if file is None:
        return {"runs": runs, "lines": lines, "errors": errors}
    if not isinstance(file, dict):
        return {"runs": runs, "lines": lines, "errors": [*errors, "sources file: not a JSON object"]}
    for i, entry in enumerate(file.get("runs") if isinstance(file.get("runs"), list) else []):
        label = _text(entry.get("label")) if isinstance(entry, dict) else None
        glob = _text(entry.get("glob")) if isinstance(entry, dict) else None
        if label is None or glob is None:
            errors.append(f"sources file: runs[{i}] needs a label and a glob")
            continue
        runs.append(run_of(label, glob, entry))
    for i, entry in enumerate(file.get("lines") if isinstance(file.get("lines"), list) else []):
        ok_entry = isinstance(entry, dict)
        label = _text(entry.get("label")) if ok_entry else None
        glob = _text(entry.get("glob")) if ok_entry else None
        match = _source_re(entry.get("match"), f"lines[{i}].match", errors) if ok_entry else None
        if label is None or glob is None or match is None:
            errors.append(f"sources file: lines[{i}] needs a label, a glob and a match pattern")
            continue
        start = _source_re(entry.get("start"), f"lines[{i}].start", errors)
        ok = _source_re(entry.get("ok"), f"lines[{i}].ok", errors)
        # A bad ok pattern must not turn every line ok, nor a bad start judge an older run.
        if (_text(entry.get("start")) is not None and start is None) or (_text(entry.get("ok")) is not None and ok is None):
            continue
        lines.append({"label": label, "glob": glob, "match": match, "start": start, "ok": ok,
                      "maxAgeHours": _positive(entry.get("maxAgeHours"), RUN_MAX_AGE_HOURS),
                      "runningMinutes": _positive(entry.get("runningMinutes"), RUN_RUNNING_MINUTES),
                      "latest": math.floor(_positive(entry.get("latest"), 1)),
                      "expectEveryHours": _expect(entry.get("expectEveryHours"))})
    return {"runs": runs, "lines": lines, "errors": errors}


_GLOB_WILD = _js_re(r"[*?[\]]")
_GLOB_SPECIAL = _js_re(r"[.+^${}()|[\]\\]")


def glob_parts(glob: str, home: Optional[str]) -> tuple[str, re.Pattern]:
    """A glob as the directory to list and a pattern for the names in it; ValueError when it
    cannot be one: wildcards only in the file name, an absolute path or ~/."""
    trimmed = _trim(glob)
    if (trimmed == "~" or trimmed.startswith("~/")) and home is None:
        raise ValueError(f"{glob}: ~ needs HOME")
    path = expand_home(trimmed, home)
    if not path.startswith("/"):
        raise ValueError(f"{glob}: needs an absolute path or ~/")
    cut_at = path.rfind("/")
    folder, name = path[:cut_at] or "/", path[cut_at + 1:]
    if _GLOB_WILD.search(folder):
        raise ValueError(f"{glob}: wildcards only in the file name")
    if not name:
        raise ValueError(f"{glob}: names no file")
    body = _GLOB_SPECIAL.sub(lambda m: "\\" + m.group(0), name).replace("*", "[^/]*").replace("?", "[^/]")
    return folder, _js_re(f"^{body}$")


# A trailing date after a separator (or alone): 2026-10-09 or 20261009, a time after it, then a
# run counter for a second run that day (-2). A real calendar date only, so an epoch stamp or a
# build number is not one.
_DATED = _js_re(r"(?:^|[-_.])(?:19|20)\d{2}-?(?:0[1-9]|1[0-2])-?(?:0[1-9]|[12]\d|3[01])"
                r"(?:[T_-]?\d{2}[:-]?\d{2}(?:[:-]?\d{2})?)?(?:[-_.]\d{1,3})?$")
_EXT = _js_re(r"\.[^.]*$")


def run_name_of(file_name: str) -> str:
    """A log's run name: the file name without its extension and a trailing date."""
    base = _EXT.sub("", file_name, count=1)
    return _DATED.sub("", base, count=1) or base


def pick_logs(files: list, pattern: re.Pattern, now_ms: float, max_age_hours: float, latest: int) -> list:
    """The logs to show: the newest of each run name, no older than max_age_hours, newest first,
    at most `latest`."""
    seen, picked = set(), []
    for f in sorted((f for f in files if pattern.search(f["name"])), key=lambda f: -f["mtimeMs"]):
        name = run_name_of(f["name"])
        if name in seen or now_ms - f["mtimeMs"] > max_age_hours * HOUR_MS:
            continue
        seen.add(name)
        picked.append(f)
        if len(picked) >= latest:
            break
    return picked


# The line `claude -p` prints on stderr when it gives up waiting on background work.
_TERMINATING = _js_re(r"Background tasks still running after \d+s?; terminating")
# The events that decide a run, compact (as `claude -p` writes them) or spaced. A quote inside
# a JSON string is escaped, so these spellings appear bare only as an event's own keys.
_WANTED = _js_re(r'"(?:type|subtype)"\s*:\s*"(?:result|permission_denied|task_)')
_STREAM_NEEDLES = (b'"result', b'"permission_denied', b'"task_', b"Background tasks still running after")

# A result's last line saying, in the present, that the run itself still waits on background
# work ("Now waiting for the deep-dive agent to finish"), not a third party ("the cron is
# waiting for 2 jobs") nor the past ("I was waiting for the agent")...
_WAITING_ON_WORK = _js_re(
    r"(?:\b(?:I(?:'m| am)|we(?:'re| are))\s+(?:(?:still|now|currently|just)\s+)?|\b(?:now|still|currently)\s+"
    r"|(?:^|[.;:!]\s*))waiting (?:for|on) (?:the |a |an |its |my |our |\d+ )?(?:[\w-]+ ){0,3}?(?:sub)?"
    r"(?:agents?|tasks?|jobs?|workers?)\b", re.I)
# ... but not one saying nothing waits any more, or that the work came back.
_NOT_WAITING = _js_re(
    r"(?:\b(?:no longer|not|never|stopped|done|finished|quit|nothing|nobody|no one|none)|n't)\s+(?:\w+\s+){0,2}"
    r"waiting\b|\b(?:has|have|had)\s+(?:now\s+|since\s+|already\s+)?(?:returned|finished|completed|reported)\b",
    re.I)
# One of a numbered or lettered list ("1. Publish anyway", "(b) Skip"); one that reports what
# was done ("2. Stock report refreshed", "3. ✅ Returns") is not a choice to make.
_OPTION = _js_re(r"^(?:\d{1,2}[.)]|\(?[a-e]\))\s+(\S.*)$")
_DONE_ITEM = _js_re(r"[✓✔✅]|\b(?:\w+ed|done|ok|complete|written|sent|built|run|up to date)[.!*`_\s]*$", re.I)
# A question that picks between things on its own, or (with an `or`) asks the person to decide.
_CHOICE = _js_re(r"\b(?:which|whether|choose|pick|prefer)\b", re.I)
_DECIDE = _js_re(r"\b(?:should|shall) (?:I|we)\b|\bdo you want\b|\bwould you (?:rather|prefer)\b"
                 r"|\bhow (?:should|do you want)\b", re.I)
_OR = _js_re(r"\bor\b", re.I)
# A closing offer or pleasantry once the job is done ("Want me to also email it?", "Anything
# else?"), not a question the run needed answered.
_OFFER = _js_re(r"\b(?:also|as well|too)\b|\b(?:would you like|want) me to\b|\bany (?:other |more |further )?"
                r"(?:questions|concerns)\b|\banything else\b|\blet me know\b|\bif you(?:'d| would)? like\b", re.I)
_QUESTION_END = _js_re(r"[*_`\s]+$")


def is_asking(text: str) -> bool:
    """Whether a result ends on a question the run needed answered (a heuristic): the last line
    asks, and it picks between things, or follows two or more numbered options that read as
    choices. A closing offer or pleasantry is not a question nobody answered."""
    lines = [ln for ln in (_trim(x) for x in text.split("\n")) if ln]
    question = _QUESTION_END.sub("", lines[-1] if lines else "", count=1)
    if not question.endswith("?") or _OFFER.search(question):
        return False
    if _CHOICE.search(question) or (_OR.search(question) and _DECIDE.search(question)):
        return True
    options = [ln for ln in lines[-13:-1] if (m := _OPTION.search(ln)) and not _DONE_ITEM.search(m.group(1))]
    return len(options) >= 2


def _kills_of(value) -> tuple[int, int]:
    """subagent_stats.killed as (killed, stopped): `parent` is the model's own TaskStop, every
    other key (`system`, `user`, one a later build adds) a stop the run did not choose; a bare
    number is all kills."""
    if isinstance(value, dict):
        return sum(_count(v) for who, v in value.items() if who != "parent"), _count(value.get("parent"))
    return _count(value), 0


def fresh_facts() -> dict:
    return {"last": None, "results": 0, "turns": 0, "cost": None, "denied_in_results": 0,
            "denial_events": 0, "terminated": False, "background": {}, "ended": {}}


def _reject_constant(name: str):
    raise ValueError(name)                      # NaN and Infinity are not JSON to JavaScript


def fact_line(line: str, acc: dict) -> None:
    """Folds one log line into acc: every `result` event, `permission_denied` events, the task
    events, and stderr's `terminating` line (2>&1); anything else is skipped unparsed."""
    s = _trim(line)
    if not s:
        return
    if not s.startswith("{"):
        acc["terminated"] = acc["terminated"] or bool(_TERMINATING.search(s))
        return
    if not _WANTED.search(s):
        return
    try:
        ev = json.loads(s, parse_constant=_reject_constant)
    except ValueError:
        return
    if not isinstance(ev, dict):
        return
    task = _text(ev.get("task_id"))
    if ev.get("type") == "result":
        acc["last"] = {"subtype": ev.get("subtype"), "is_error": ev.get("is_error"),
                       "terminal_reason": ev.get("terminal_reason"), "errors": ev.get("errors"),
                       "result": ev.get("result"), "subagent_stats": ev.get("subagent_stats")}
        acc["results"] += 1
        acc["turns"] += _count(ev.get("num_turns"))
        cost = _num(ev.get("total_cost_usd"))
        if cost is not None:                    # unknown stays unknown: no cost shows none, not ≈$0.00
            acc["cost"] = max(acc["cost"] if acc["cost"] is not None else 0, cost)
        denials = ev.get("permission_denials")
        acc["denied_in_results"] += len(denials) if isinstance(denials, list) else 0
        return
    if ev.get("type") != "system":
        return
    sub = ev.get("subtype")
    if sub == "permission_denied":
        acc["denial_events"] += 1
        return
    if task is None:
        return
    patch = ev.get("patch")
    status = ev.get("status") if sub == "task_notification" else \
        patch.get("status") if sub == "task_updated" and isinstance(patch, dict) else None
    if sub == "task_started" and ev.get("is_backgrounded") is True and ev.get("task_type") == "local_agent":
        acc["background"][task] = True
    elif status in ("completed", "failed"):
        acc["ended"][task] = status             # the last word on a task wins: a kill then its notice is one kill
    elif status in ("killed", "stopped"):
        acc["ended"][task] = "killed"


def facts_of(acc: dict) -> dict:
    """What a folded log says: its last result, the counts, and how its subagents ended (the last
    result's subagent_stats, foreground and background; `tasks` counts background ones from the
    task events, every stop as a kill since they do not say whose)."""
    last = acc["last"]
    stats = last.get("subagent_stats") if last else None
    stats = stats if isinstance(stats, dict) else None
    bg = list(acc["background"])

    def ended(end: str) -> int:
        return sum(1 for t in bg if acc["ended"].get(t) == end)

    killed, stopped = _kills_of(stats.get("killed") if stats else None)
    errors = last.get("errors") if last else None
    return {
        "results": acc["results"],
        "last": None if last is None else {
            "subtype": _text(last["subtype"]) or "?", "isError": last["is_error"] is True,
            "terminal": _text(last["terminal_reason"]),
            "errors": [e for e in errors if isinstance(e, str)] if isinstance(errors, list) else [],
            "text": last["result"] if isinstance(last["result"], str) else ""},
        "turns": acc["turns"], "cost": acc["cost"],
        "denied": max(acc["denied_in_results"], acc["denial_events"]),
        # `spawned` counts every subagent; an older build wrote only the background count.
        "stats": None if stats is None else {
            "spawned": _count(stats.get("started_in_background")) if _num(stats.get("spawned")) is None
            else _count(stats.get("spawned")),
            "completed": _count(stats.get("completed")), "failed": _count(stats.get("failed")),
            "killed": killed, "stopped": stopped},
        "tasks": {"spawned": len(bg), "completed": ended("completed"), "failed": ended("failed"),
                  "killed": ended("killed"), "stopped": 0},
        "isTerminated": acc["terminated"],
    }


def facts_of_stream(text: str) -> dict:
    """facts_of a whole stream-json log at once."""
    acc = fresh_facts()
    for line in text.split("\n"):
        fact_line(line, acc)
    return facts_of(acc)


def subagents_of(f: dict) -> dict:
    """The subagents a run left behind: from subagent_stats, else from the task events."""
    c = f["stats"] or f["tasks"]
    return {**c, "pending": max(0, c["spawned"] - c["completed"] - c["failed"] - c["killed"] - c["stopped"])}


def _verdict(verdict: str, tone: str, why: str, detail: str = "") -> dict:
    return {"verdict": verdict, "tone": tone, "why": why, "detail": detail}


def verdict_of_stream(f: dict, mtime_ms: float, now_ms: float, running_minutes: float) -> dict:
    """One headless run, judged from its facts. Unfinished when a subagent was left running,
    failed or was killed (by anything but the model's own TaskStop), or the run said it
    terminated background work, even though the run reported success; asked when its last word
    is a question nobody will answer. No result and a recent write is a run still going."""
    running_ms = running_minutes * 60_000
    age_ms = now_ms - mtime_ms
    recent = age_ms < running_ms
    if f["last"] is None:
        since = f"last write {ago_ms(mtime_ms, now_ms)} ago" + (f" · {f['denied']} denied" if f["denied"] > 0 else "")
        if f["isTerminated"]:
            return _verdict("no result", "fail", "no result: background work terminated", since)
        if recent:
            return _verdict("running", "busy", f"running, last write {ago_ms(mtime_ms, now_ms)} ago", "no result event yet")
        # A run blocked on one long, silent tool call writes nothing either: a warning before a failure.
        if age_ms < 2 * running_ms:
            return _verdict("quiet", "warn", f"no result, no write for {ago_ms(mtime_ms, now_ms)}: "
                                             "a long tool call, or dead", since)
        return _verdict("no result", "fail", "no result event: killed or crashed", since)
    last = f["last"]
    sub = subagents_of(f)
    settled = last["terminal"] is None or last["terminal"] == "completed"
    detail = " · ".join(x for x in (
        last["subtype"], plural(f["turns"], "turn"),
        None if f["cost"] is None else f"≈${_fixed2(f['cost'])}",
        f"{f['denied']} denied",
        f"subagents {sub['completed']}/{sub['spawned']} done" if sub["spawned"] > 0 else None,
        f"{sub['failed']} failed" if sub["failed"] > 0 else None,
        f"{sub['killed']} killed" if sub["killed"] > 0 else None,
        f"{sub['stopped']} stopped by the model" if sub["stopped"] > 0 else None,
        None if settled else last["terminal"],
        f"{f['results']} results" if f["results"] > 1 else None) if x is not None)
    if last["isError"] or last["subtype"] != "success" or not settled:
        reason = last["errors"][0] if last["errors"] else (
            last["subtype"] if last["subtype"] != "success" else last["terminal"]
            if not settled and last["terminal"] is not None else "is_error")
        return _verdict("failed", "fail", f"failed: {first_line_of(reason, 80)}", detail)
    # The run answered and still waits on its background work: not over yet.
    if sub["pending"] > 0 and sub["killed"] == 0 and sub["failed"] == 0 and not f["isTerminated"] and recent:
        return _verdict("running", "busy", f"waiting on {plural(sub['pending'], 'background subagent')}", detail)
    if sub["pending"] > 0 or sub["failed"] > 0 or sub["killed"] > 0 or f["isTerminated"]:
        parts = [x for x in (
            f"{sub['pending']} of {plural(sub['spawned'], 'subagent')} not finished" if sub["pending"] > 0 else None,
            f"{plural(sub['failed'], 'subagent')} failed" if sub["failed"] > 0 else None,
            f"{plural(sub['killed'], 'subagent')} killed" if sub["killed"] > 0 else None,
            "background work terminated" if f["isTerminated"] and sub["killed"] == 0 else None) if x is not None]
        return _verdict("unfinished", "fail", f"unfinished: {', '.join(parts)}", detail)
    text = last["text"]
    final = last_line_of(text, 400)
    # No subagent_stats and no task events to go by: the result's own words are the fallback.
    if f["stats"] is None and f["tasks"]["spawned"] == 0 and _WAITING_ON_WORK.search(final) \
            and not _NOT_WAITING.search(final):
        return _verdict("unfinished", "fail", f"unfinished: says {json.dumps(last_line_of(text, 60), ensure_ascii=False)}",
                        detail)
    if is_asking(text):
        return _verdict("asked", "warn", f"ended on a question: {last_line_of(text, 70)}", detail)
    if f["denied"] > 0:
        return _verdict("denied", "warn", plural(f["denied"], "permission denial"), detail)
    return _verdict("ok", "ok", "ok", detail)


def _js_source(p: Optional[re.Pattern]) -> str:
    """A pattern as JavaScript's RegExp.prototype.source spells it (the verdicts quote it)."""
    src = p.pattern if p is not None else ""
    return re.sub(r"(?<!\\)/", r"\/", src).replace("\n", r"\n") or "(?:)"


def fresh_line(start: Optional[re.Pattern]) -> dict:
    return {"line": None, "hasStart": start is None}


def line_fold(match: re.Pattern, start: Optional[re.Pattern]):
    """The fold that finds a text log's verdict line: the last line matching `match` after the
    last line matching `start` (with no start, anywhere). The start line itself never counts."""
    def fold(line: str, acc: dict) -> None:
        s = _trim(line)
        if not s:
            return
        if start is not None and start.search(s):
            acc["line"], acc["hasStart"] = None, True
        elif acc["hasStart"] and match.search(s):
            acc["line"] = s
    return fold


def last_match_of(text: str, match: re.Pattern, start: Optional[re.Pattern] = None) -> dict:
    acc, fold = fresh_line(start), line_fold(match, start)
    for line in text.split("\n"):
        fold(line, acc)
    return acc


def verdict_of_line(found: dict, match: re.Pattern, start: Optional[re.Pattern], ok: Optional[re.Pattern],
                    mtime_ms: float, now_ms: float, running_minutes: float) -> dict:
    """A text log by its verdict line: ok when it matches `ok` (or none is set), failed when not;
    none yet is running while the log is fresh, a warning after."""
    if found["line"] is not None:
        good = ok is None or bool(ok.search(found["line"]))
        return _verdict("ok" if good else "failed", "ok" if good else "fail", first_line_of(found["line"], 120))
    if now_ms - mtime_ms < running_minutes * 60_000:
        return _verdict("running", "busy", f"running, last write {ago_ms(mtime_ms, now_ms)} ago")
    if start is None:
        why = f"no line matching /{_js_source(match)}/"
    elif found["hasStart"]:
        why = f"no line matching /{_js_source(match)}/ after the last run start"
    else:
        why = f"no run start matching /{_js_source(start)}/"
    return _verdict("no line", "warn", why)


def verdict_of_lines(text: str, match: re.Pattern, ok: Optional[re.Pattern], mtime_ms: float, now_ms: float,
                     running_minutes: float, start: Optional[re.Pattern] = None) -> dict:
    return verdict_of_line(last_match_of(text, match, start), match, start, ok, mtime_ms, now_ms, running_minutes)


def late_run(source: str, glob: str, kind: str, newest_ms: Optional[float], now_ms: float,
             expect_every_hours: Optional[float]) -> Optional[dict]:
    """The failure a source raises when its newest log is older than it should be; keyed by
    label and glob, so two sources never share one."""
    if expect_every_hours is None or (newest_ms is not None and now_ms - newest_ms <= expect_every_hours * HOUR_MS):
        return None
    hours = int(expect_every_hours) if float(expect_every_hours).is_integer() else expect_every_hours
    why = f"no {'log' if kind == 'line' else 'run'} found" if newest_ms is None else \
        f"no run for {ago_ms(newest_ms, now_ms)} (expected every {hours}h)"
    return {"key": f"{source}@late@{glob}@{newest_ms if newest_ms is not None else 0}", "source": source,
            "kind": "late", "name": source, "path": "", "size": 0, "mtimeMs": newest_ms if newest_ms is not None else 0,
            **_verdict("late", "fail", why), "facts": None}


_log_cache: dict = {}


def fold_log(path: str, key, needles: tuple, fresh, fold):
    """fold(line, acc) over each line of a log that holds one of `needles` (bytes; none: every
    line), once per version of the file. A log that grew is read from where the last read
    stopped; one rewritten in place (the bytes before that point changed) or replaced is read
    again. A last line with no newline yet counts now and is read again once it is whole."""
    st = os.stat(path)
    stamp = (st.st_ino, st.st_size, st.st_mtime_ns)
    hit = _log_cache.get(key)
    if hit and hit["stamp"] == stamp:
        return hit["view"]
    with open(path, "rb") as f:
        pos, acc = 0, None
        if hit and hit["stamp"][0] == st.st_ino and hit["pos"] <= st.st_size:
            f.seek(max(0, hit["pos"] - 64))
            if f.read(min(64, hit["pos"])) == hit["sig"]:
                pos, acc = hit["pos"], hit["acc"]
        acc = fresh() if acc is None else acc
        f.seek(pos)
        carry, left = b"", st.st_size - pos
        while left > 0:
            chunk = f.read(min(BLOCK, left))
            if not chunk:
                break
            left -= len(chunk)
            data = carry + chunk
            end = data.rfind(b"\n") + 1
            for ln in data[:end].split(b"\n")[:-1] if end else []:
                if not needles or any(n in ln for n in needles):
                    fold(ln.decode("utf-8", "replace"), acc)
            pos += end
            carry = data[end:]
        f.seek(max(0, pos - 64))
        sig = f.read(min(64, pos))
    view = acc
    if carry and (not needles or any(n in carry for n in needles)):
        view = copy.deepcopy(acc)
        fold(carry.decode("utf-8", "replace"), view)
    _log_cache.pop(key, None)
    _log_cache[key] = {"stamp": stamp, "pos": pos, "sig": sig, "acc": acc, "view": view}
    while len(_log_cache) > LOG_CACHE_MAX:
        _log_cache.pop(next(iter(_log_cache)))
    return view


def _error_text(e: BaseException) -> str:
    if isinstance(e, OSError) and e.strerror:
        return f"{e.strerror}: {e.filename}" if e.filename else e.strerror
    return first_line_of(str(e), 160) or type(e).__name__


def _logs_of(glob: str, home: Optional[str]) -> tuple[str, list]:
    """The files one glob names: name, path, size and mtime (ms) of each."""
    folder, pattern = glob_parts(glob, home)
    files = []
    with os.scandir(folder) as it:
        for e in it:
            try:
                if not pattern.search(e.name) or not e.is_file():
                    continue
                st = e.stat()
            except OSError:
                continue
            files.append({"name": e.name, "path": os.path.join(folder, e.name), "size": st.st_size,
                          "mtimeMs": st.st_mtime_ns // 1_000_000})
    files.sort(key=lambda f: f["name"])
    return folder, files


def _run_of(source: str, kind: str, f: dict, v: dict, facts: Optional[dict] = None) -> dict:
    return {"key": f"{f['path']}@{f['mtimeMs']}", "source": source, "kind": kind, "name": run_name_of(f["name"]),
            "path": f["path"], "size": f["size"], "mtimeMs": f["mtimeMs"], **v, "facts": facts}


def _facts_row(f: dict) -> dict:
    """The numbers the Runs view shows for a stream run."""
    return {"results": f["results"], "turns": f["turns"], "cost": f["cost"], "denied": f["denied"],
            "subagents": subagents_of(f)}


def _read_source(s: dict, kind: str, home: Optional[str], now_ms: float) -> tuple[list, dict]:
    source = {"label": s["label"], "kind": kind, "glob": s["glob"], "error": None, "count": 0}
    runs = []
    try:
        _, files = _logs_of(s["glob"], home)
    except (OSError, ValueError) as e:
        source["error"] = _error_text(e)
        return runs, source
    for f in pick_logs(files, re.compile(""), now_ms, s["maxAgeHours"], s["latest"]):
        try:
            if kind == "stream":
                facts = facts_of(fold_log(f["path"], (f["path"], "stream"), _STREAM_NEEDLES, fresh_facts, fact_line))
                runs.append(_run_of(s["label"], kind, f, verdict_of_stream(facts, f["mtimeMs"], now_ms,
                                                                          s["runningMinutes"]), _facts_row(facts)))
            else:
                key = (f["path"], "line", s["match"].pattern, s["start"].pattern if s["start"] else None)
                found = fold_log(f["path"], key, (), lambda: fresh_line(s["start"]), line_fold(s["match"], s["start"]))
                runs.append(_run_of(s["label"], kind, f, verdict_of_line(found, s["match"], s["start"], s["ok"],
                                                                        f["mtimeMs"], now_ms, s["runningMinutes"])))
        except OSError as e:
            runs.append(_run_of(s["label"], kind, f, _verdict("unreadable", "warn", f"unreadable: {_error_text(e)}")))
    source["count"] = len(files)
    late = late_run(s["label"], s["glob"], kind, max((f["mtimeMs"] for f in files), default=None), now_ms,
                    s["expectEveryHours"])
    if late is not None:
        runs.append(late)
    return runs, source


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "herd"


def sources_path(arg: Optional[str] = None) -> Optional[str]:
    """The sources file to read: --sources, else $HERD_SOURCES (set empty: none), else
    ~/.config/herd/sources.json when it exists. Never the preset by itself: on another machine
    its schedules would all read late."""
    if arg:
        return arg
    env = os.environ.get("HERD_SOURCES")
    if env is not None:
        return env or None
    default = config_dir() / "sources.json"
    return str(default) if default.exists() else None


def read_runs(path: Optional[str] = None, now_ms: Optional[float] = None, home: Optional[str] = None) -> dict:
    """Every run source the sources file names, read and judged. Never raises for a source:
    each carries its own error, and a log two sources name is one run."""
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    home = _home() if home is None else home
    data, file_error = None, None
    if path:
        try:
            data = json.loads(Path(expand_home(path, home)).read_text())
        except (OSError, ValueError) as e:
            file_error = f"sources file {path}: {_error_text(e)}"
    parsed = sources_of([], data)
    runs, sources, seen = [], [], set()
    for kind, entries in (("stream", parsed["runs"]), ("line", parsed["lines"])):
        for s in entries:
            got, source = _read_source(s, kind, home, now_ms)
            runs += [r for r in got if not (r["key"] in seen or seen.add(r["key"]))]
            sources.append(source)
    errors = [e for e in (file_error, *parsed["errors"]) if e]
    return {"runs": runs, "sources": sources, "sourcesFile": path, "sourcesError": "; ".join(errors) or None}


def acked_path() -> Path:
    return state_dir() / "acked.json"


def acked() -> list[str]:
    """The run failures the person has seen (run keys, oldest first): shown as warnings, not
    failures, until the log changes."""
    got = read_json(acked_path())
    keys = got.get("keys") if isinstance(got, dict) else None
    return [k for k in keys if isinstance(k, str)][-ACKED_MAX:] if isinstance(keys, list) else []


def ack(keys: list) -> list[str]:
    """Adds run keys to the acked list, under a lock (every session's band acks through here),
    keeping the newest ACKED_MAX; returns the list."""
    path = acked_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    with open(path.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        new = [k for k in dict.fromkeys(keys) if isinstance(k, str) and k]
        keep = [k for k in acked() if k not in new] + new
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"keys": keep[-ACKED_MAX:]}, f, indent=1)
        os.replace(tmp, path)                   # a reader never sees half a file
    return keep[-ACKED_MAX:]


def _ms_of_seconds(v) -> Optional[float]:
    n = _num(v)
    return None if n is None else n * 1000


def tracks_of(status, now_ms: float, stale_ms: float) -> tuple[list, list]:
    """shepherd's tracks, flat, each with its repo; ValueError on JSON that is not shepherd's."""
    if not isinstance(status, dict) or not isinstance(status.get("repos"), list):
        raise ValueError("not shepherd status JSON (no repos)")
    repos, tracks = [], []
    for repo in status["repos"]:
        if not isinstance(repo, dict):
            continue
        repo_name = _text(repo.get("name")) or "?"
        overlaps = [o for o in repo.get("overlaps") or [] if isinstance(o, dict)] \
            if isinstance(repo.get("overlaps"), list) else []
        repos.append({"name": repo_name, "primary": _text(repo.get("primary")) or ""})
        for t in repo.get("tracks") if isinstance(repo.get("tracks"), list) else []:
            if not isinstance(t, dict):
                continue
            name, state = _text(t.get("name")) or "?", _text(t.get("status")) or "unknown"
            agent = t.get("agent") if isinstance(t.get("agent"), dict) else {}
            review = t.get("review") if isinstance(t.get("review"), dict) else None
            live = agent.get("live") is True
            last = _ms_of_seconds(t.get("last_activity"))
            # A merge-tree verdict is a list of files: empty is clean, null unknown.
            conflicts = sum(1 for o in overlaps if name in (o.get("a"), o.get("b"))
                            and isinstance(o.get("conflicts"), list) and o["conflicts"])
            rid = None if review is None else _text(review.get("id")) or (
                None if _num(review.get("id")) is None else _js_number(review["id"]))
            tracks.append({
                "repo": repo_name, "name": name, "status": state, "goal": _text(t.get("goal")),
                "sessionId": _text(agent.get("session_id")), "owner": _text(agent.get("status")), "isLive": live,
                "reviewId": rid, "reviewState": None if review is None else _text(review.get("state")),
                "checks": None if review is None else _text(review.get("checks")),
                "checkedAt": None if review is None else _ms_of_seconds(review.get("checked_at")),
                "reviewUrl": None if review is None else _text(review.get("url")),
                "lastActivity": last,
                "warnings": [w for w in t.get("warnings") or [] if isinstance(w, str)]
                if isinstance(t.get("warnings"), list) else [],
                "conflicts": conflicts, "isBlocked": False, "waitingFor": None,
                "isStale": state == "active" and (not live or (last is not None and now_ms - last > stale_ms))})
    return repos, tracks


def _js_number(n) -> str:
    """String(n) for a JSON number: 6 not 6.0."""
    return str(int(n)) if isinstance(n, float) and n.is_integer() and abs(n) < 1e21 else str(n)


def snapshot_sessions() -> list[dict]:
    """The live sessions (herd's list) in the snapshot's shape."""
    return [{"sessionId": r["session_id"], "shortId": None, "name": r["name"] or None,
             "kind": "background" if r["kind"] == "bg" else r["kind"], "state": r["state"],
             "status": r["status"] or None, "waitingFor": r["waiting_for"] or None,
             "startedAt": r["started_at"] * 1000 or None, "cwd": r["cwd"] or None, "isLive": True,
             "isBlocked": r["state"] == "blocked", "track": None} for r in sessions(extras=False)]


def joined(tracks: list, rows: list) -> tuple[list, list]:
    """Marks each track whose owner waits on the person, and each session with the track it owns."""
    by_id = {s["sessionId"]: s for s in rows}
    owned = {}
    out = []
    for t in tracks:
        s = by_id.get(t["sessionId"]) if t["sessionId"] is not None else None
        if s is not None and t["status"] not in ("paused", "merged"):
            owned[t["sessionId"]] = f"{t['repo']}/{t['name']}"
        out.append(t if s is None else {**t, "isBlocked": s["isBlocked"], "waitingFor": s["waitingFor"]})
    return out, [{**s, "track": owned.get(s["sessionId"])} for s in rows]


def snapshot(sources: Optional[str] = None, with_tracks: bool = True, with_sessions: bool = True,
             stale_hours: float = 24, board: Optional[dict] = None) -> dict:
    """One read of everything that may need the person: shepherd's tracks, the live sessions and
    the headless runs, joined. Each part fails on its own and says so; the rest still shows.
    The shape is the herd mod's (camelCase, ms): it draws it as is."""
    started = time.time() * 1000
    repos, tracks, t_error, t_missing = [], [], None, False
    if with_tracks:
        if board is None:
            board, t_error, t_missing = shepherd_read(timeout=20)
        if t_error is None and not t_missing:
            try:
                repos, tracks = tracks_of(board, started, max(1, stale_hours) * HOUR_MS)
            except ValueError as e:
                t_error = str(e)
    rows, s_error = [], None
    if with_sessions:
        try:
            rows = snapshot_sessions()
        except Exception as e:                  # each part fails on its own
            s_error = _error_text(e)
    tracks, rows = joined(tracks, rows)
    rr = read_runs(sources, started)
    keys = set(acked())
    done = time.time() * 1000
    return {"schema": SNAPSHOT_SCHEMA, "at": int(done), "tookMs": max(0, int(done - started)),
            "host": socket.gethostname(),
            "tracksOn": with_tracks, "repos": repos, "tracks": tracks, "tracksError": t_error,
            "tracksMissing": t_missing,
            "sessionsOn": with_sessions, "sessions": rows, "sessionsError": s_error, "sessionsMissing": False,
            "runs": rr["runs"], "sources": rr["sources"], "sourcesFile": rr["sourcesFile"],
            "sourcesError": rr["sourcesError"], "acked": [r["key"] for r in rr["runs"] if r["key"] in keys]}


TONE = {"fail": "1;31", "warn": "33", "busy": "36", "ok": "32", "dim": "2"}


def render_runs(rr: dict, acked_keys: set, width: int, color: bool) -> str:
    paint = (lambda code, s: f"\x1b[{code}m{s}\x1b[0m") if color else (lambda code, s: s)
    if not rr["sources"] and not rr["sourcesError"]:
        return ("no run sources: name a sources file with --sources, $HERD_SOURCES or "
                f"{config_dir() / 'sources.json'} (example: harnesses/herd/presets/sources.json)\n")
    out = [paint("2", f"{'SOURCE':12} {'RUN':28} {'WHEN':11}  {'VERDICT':11} WHY")]
    for r in rr["runs"]:
        seen = r["tone"] == "fail" and r["key"] in acked_keys
        tone = "warn" if seen else r["tone"]
        when = stamp_of(r["mtimeMs"]) if r["mtimeMs"] > 0 and r["kind"] != "late" else "-"
        name = "schedule" if r["kind"] == "late" else r["name"]
        head = f"{cut(r['source'], 12):12} {cut(name, 28):28} {when:11}  "
        why = ("acked · " if seen else "") + r["why"]
        out.append(head + paint(TONE.get(tone, "0"), f"{r['verdict']:11}") + " " + cut(why, max(20, width - len(head) - 12)))
        if r["detail"]:
            out.append(paint("2", " " * 13 + cut(r["detail"], max(20, width - 13))))
    for s in rr["sources"]:
        if s["error"]:
            out.append(paint(TONE["warn"], f"{s['label']}: {s['glob']}: {s['error']}"))
    if rr["sourcesError"]:
        out.append(paint(TONE["warn"], rr["sourcesError"]))
    fails = [r for r in rr["runs"] if r["tone"] == "fail" and r["key"] not in acked_keys]
    if fails:
        out.append(paint("2", f"{plural(len(fails), 'failure')} not acked: herd ack --failed once you have looked"))
    return "\n".join(out) + "\n"


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


def cmd_runs(args) -> int:
    rr = read_runs(sources_path(args.sources))
    keys = set(acked())
    if args.json:
        print(json.dumps({**rr, "acked": [r["key"] for r in rr["runs"] if r["key"] in keys]}, indent=2))
        return 0
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    sys.stdout.write(render_runs(rr, keys, shutil.get_terminal_size((160, 24)).columns, color))
    return 0


def cmd_snapshot(args) -> int:
    snap = snapshot(sources_path(args.sources), with_tracks=not args.no_tracks,
                    with_sessions=not args.no_sessions, stale_hours=args.stale_hours)
    print(json.dumps(snap, separators=(",", ":")))
    return 0


def cmd_ack(args) -> int:
    if args.failed == bool(args.keys):
        print("herd: ack takes run keys (herd runs --json) or --failed", file=sys.stderr)
        return 2
    keys = args.keys
    if args.failed:
        keys = [r["key"] for r in read_runs(sources_path(args.sources))["runs"] if r["tone"] == "fail"]
    if keys:
        ack(keys)
    print(f"acked {plural(len(keys), 'run')}" + ("" if keys else ": no failure to ack"))
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
    sources_help = "the sources file (default: $HERD_SOURCES, else ~/.config/herd/sources.json)"
    p = sub.add_parser("runs", help="headless claude -p runs and cron logs, each judged: ok, asked, unfinished…")
    p.add_argument("--json", action="store_true")
    p.add_argument("--sources", help=sources_help)
    p = sub.add_parser("snapshot", help="tracks, sessions and runs in one JSON, as the herd mod draws them")
    p.add_argument("--json", action="store_true", help="the default; the only format")
    p.add_argument("--sources", help=sources_help)
    p.add_argument("--no-tracks", action="store_true", help="skip shepherd")
    p.add_argument("--no-sessions", action="store_true", help="skip the session list")
    p.add_argument("--stale-hours", type=float, default=24.0,
                   help="an active track nobody touched this long is stale (default 24)")
    p = sub.add_parser("ack", help="mark run failures as seen: a warning, not a failure, until the log changes")
    p.add_argument("keys", nargs="*", help="run keys, as herd runs --json and herd snapshot list them")
    p.add_argument("--failed", action="store_true", help="every run failing now")
    p.add_argument("--sources", help=sources_help)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["ls", *argv]
    extra: list[str] = []
    if argv[0] == "new" and "--" in argv:       # everything after -- goes to claude untouched
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    args = ap.parse_args(argv)
    args.claude_args = extra
    return {"ls": cmd_ls, "watch": cmd_watch, "new": cmd_new, "go": cmd_go,
            "serve": cmd_serve, "url": cmd_url, "recap": cmd_recap, "resume": cmd_resume,
            "runs": cmd_runs, "snapshot": cmd_snapshot, "ack": cmd_ack}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
