#!/usr/bin/env python3
"""herd — one attention list of every live Claude Code session on this machine.

    herd [ls] [--json]                       blocked first, then done, then working
    herd watch [-n SECS] [--no-bell]         the same, redrawn; rings when a session blocks
    herd new <name> [dir] [-- claude args…]  start claude in a detached tmux session
    herd go <name>                           attach (or switch) to a session's tmux pane
    herd serve [-p PORT]                     dashboard on 127.0.0.1: sessions, workflows below

Read-only over what Claude Code already writes: its session registry
(~/.claude/sessions/<pid>.json), transcript tails and workflow journals. No hooks and
no state of its own beyond the dashboard's access token. Stdlib only, Python 3.9+.
See harnesses/herd/README.md."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

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


def _workflow(run_id: str, parts: dict, session_live: bool, now: float,
              session_start: float = 0.0, launches: Optional[tuple] = None) -> Optional[dict]:
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
        # Payload bound: finished agents are counted, not listed (a phase can hold hundreds).
        shown = [a for a in members if a["status"] != "done"] if live else \
            [a for a in counted if a["status"] == "failed"]
        for a in shown:
            if not a["updated_at"] and run_dir:
                a["updated_at"] = int(_mtime(run_dir / f"agent-{a['id']}.jsonl"))
        phases.append({"title": t or "—", "agents": shown, "counts": counts, "_members": counted})
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


def workflows(session_id: str, session_live: bool, now: float, session_start: float = 0.0) -> list[dict]:
    """The session's workflow runs: every unfinished one, plus those that ended in the
    last 24 h; oldest first. A run resumed from an earlier one names it in
    `resumed_from`, and the earlier one points back with `superseded_by`."""
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
        w = _workflow(rid, parts, session_live, now, session_start, launches)
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


def subagents(session_id: str, session_live: bool, now: float, session_start: float = 0.0) -> list[dict]:
    """Subagents the session spawned itself (workflow agents live under their run)."""
    res = []
    for d in session_dirs(session_id):
        for meta_path in (d / "subagents").glob("agent-*.meta.json"):
            meta = read_json(meta_path) or {}
            if not isinstance(meta, dict) or meta.get("agentType") == "workflow-subagent":
                continue
            tx = meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))
            started, updated = _mtime(meta_path), _mtime(tx) or _mtime(meta_path)
            turns = tail_turns(tx)
            status = "stopped" if meta.get("stoppedByUser") else \
                ended(turns, now - updated) or _live_status(updated, session_live, now, session_start)
            if status not in LIVE and now - updated > WINDOW:
                continue
            res.append({"id": meta_path.name[len("agent-"):-len(".meta.json")],
                        "type": meta.get("agentType") or "agent",
                        "description": meta.get("description") or "",
                        "background": meta.get("requestShape") == "background",
                        "status": status, "doing": doing(turns) if status in LIVE else "",
                        "started_at": int(started), "updated_at": int(updated)})
    res.sort(key=lambda a: a["started_at"])
    return res


# --- the list ------------------------------------------------------------------------

def sessions(detail: bool = False) -> list[dict]:
    """Every live session, most urgent first; within a state, waiting longest first.
    With `detail`, each also carries its workflows, its own subagents, what it is asking
    for (blocked) or answered (done), and when it last wrote anything."""
    panes, branches, rows = tmux_panes(), {}, []
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
            "cwd": cwd, "branch": git_branch(cwd, branches) if cwd else "",
            "tmux": tmux_target(int(e["pid"]), panes) or "",
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

    def do_GET(self):  # noqa: N802
        # Only a page served from this machine may read it: a foreign Host is DNS rebinding.
        if (self.headers.get("Host") or "").rsplit(":", 1)[0] not in LOCAL_HOSTS:
            return self._send(403, b"forbidden host", "text/plain")
        url = urlparse(self.path)
        tok = self.server.token
        jar = SimpleCookie(self.headers.get("Cookie") or "")
        authed = COOKIE in jar and secrets.compare_digest(jar[COOKIE].value, tok)
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
        name = "index.html" if url.path in ("/", "/index.html") else \
            url.path[len("/static/"):] if url.path.startswith("/static/") else ""
        f = STATIC / name
        if name and "/" not in name and f.suffix in STATIC_TYPES and f.is_file():
            return self._send(200, f.read_bytes(), STATIC_TYPES[f.suffix])
        return self._send(404, b"not found", "text/plain")


def cmd_serve(args) -> int:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        print(f"herd: cannot listen on 127.0.0.1:{args.port}: {e.strerror}", file=sys.stderr)
        return 1
    server.daemon_threads = True
    server.token = token()
    port = server.server_address[1]
    print(f"herd dashboard: http://127.0.0.1:{port}/?t={server.token}", flush=True)
    print(f"(remote machine? forward the port first: ssh -L {port}:localhost:{port} <host>, "
          "or VS Code's Ports tab)  Ctrl-C to stop", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
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
    p = sub.add_parser("serve", help="dashboard on localhost: sessions with their workflows")
    p.add_argument("-p", "--port", type=int, default=7798)
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["ls", *argv]
    extra: list[str] = []
    if argv[0] == "new" and "--" in argv:       # everything after -- goes to claude untouched
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    args = ap.parse_args(argv)
    args.claude_args = extra
    return {"ls": cmd_ls, "watch": cmd_watch, "new": cmd_new, "go": cmd_go,
            "serve": cmd_serve}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
