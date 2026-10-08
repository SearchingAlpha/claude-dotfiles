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


def doing(turns: list[dict]) -> str:
    """The agent's latest step: its newest tool call, as "Tool: hint"."""
    for rec in turns:
        content = (rec.get("message") or {}).get("content")
        if rec.get("type") != "assistant" or not isinstance(content, list):
            continue
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                inp = block.get("input") or {}
                hint = inp.get("description") or os.path.basename(str(inp.get("file_path") or "")) \
                    or inp.get("pattern") or inp.get("query") or inp.get("command") or ""
                hint = " ".join(str(hint).split())[:100]
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
WINDOW = 24 * 3600      # finished workflows and subagents older than this are left out
LIVE = ("running", "stalled")

_META = re.compile(r"export\s+const\s+meta\s*=\s*\{(.*?)\n\}", re.S)
_PHASES = re.compile(r"phases\s*:\s*\[(.*?)\]", re.S)
_TITLE = re.compile(r"title\s*:\s*(['\"`])(.*?)\1")
_DESC = re.compile(r"description\s*:\s*(['\"`])(.*?)\1")
_SCRIPT = re.compile(r"(.+)-(wf_[0-9a-f]+-[0-9a-f]+)\.js$")
_journal_cache: dict = {}


def session_dirs(session_id: str) -> list[Path]:
    return [p for p in (claude_dir() / "projects").glob(f"*/{session_id}") if p.is_dir()]


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _script_meta(path: Path) -> dict:
    """Description and phase titles from the script's `export const meta = {…}` literal."""
    try:
        text = path.read_text(errors="replace")[:20000]
    except OSError:
        return {}
    m = _META.search(text)
    if not m:
        return {}
    body = m.group(1)
    phases = _PHASES.search(body)
    desc = _DESC.search(body[:phases.start()] if phases else body)
    return {"description": desc.group(2) if desc else "",
            "phases": [t.group(2) for t in _TITLE.finditer(phases.group(1))] if phases else []}


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


def _live_status(updated: float, session_live: bool, now: float) -> str:
    if not session_live:
        return "interrupted"
    return "stalled" if now - updated > QUIET else "running"


def _workflow(run_id: str, parts: dict, session_live: bool, now: float) -> Optional[dict]:
    final = read_json(parts["final"]) if parts.get("final") else None
    final = final if isinstance(final, dict) else {}
    meta = _script_meta(parts["script"]) if parts.get("script") else {}
    run_dir = parts.get("dir")
    journal = run_dir / "journal.jsonl" if run_dir else None
    agents: dict = {}
    for ev in _journal(journal) if journal else []:
        key = ev.get("key") or ev.get("agentId")
        if ev.get("type") == "started":
            agents[key] = {"id": ev.get("agentId") or "", "label": ev.get("label") or "agent",
                           "phase": ev.get("phase") or "", "status": "running", "doing": ""}
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
        status = _live_status(last or started, session_live, now)
        end = 0.0
    if status not in LIVE and now - (end or last or started) > WINDOW:
        return None
    for a in agents.values():
        tx = a.pop("tx", None)
        if tx is None:
            continue
        updated = _mtime(tx) or last
        turns = tail_turns(tx)
        a["status"] = ended(turns, now - updated) or \
            ("interrupted" if final else _live_status(updated, session_live, now))
        a["doing"] = doing(turns) if a["status"] in LIVE else ""
    titles = [p.get("title") for p in final.get("phases") or [] if isinstance(p, dict)] \
        or meta.get("phases") or []
    for a in agents.values():
        if a["phase"] not in titles:
            titles.append(a["phase"])
    phases = []
    for t in titles:
        members = [a for a in agents.values() if a["phase"] == t]
        if not t and not members:
            continue
        phases.append({"title": t or "—", "agents": members,
                       "counts": {s: sum(a["status"] == s for a in members) for s in
                                  ("running", "stalled", "done", "failed", "interrupted")}})
    error = str(final.get("error") or "").strip().splitlines()
    return {"id": run_id, "name": final.get("workflowName") or parts.get("name") or run_id,
            "description": meta.get("description") or final.get("summary") or "",
            "status": status, "started_at": int(started), "ended_at": int(end),
            "elapsed": int((end or now) - started) if started else 0,
            "agent_count": len(agents) or final.get("agentCount") or 0,
            "tokens": final.get("totalTokens") or 0, "error": error[0] if error else "",
            "phases": phases}


def workflows(session_id: str, session_live: bool, now: float) -> list[dict]:
    """The session's workflow runs: every unfinished one, plus those that ended in the
    last 24 h; oldest first."""
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
    out = []
    for rid, parts in runs.items():
        w = _workflow(rid, parts, session_live, now)
        if w:
            out.append(w)
    out.sort(key=lambda w: w["started_at"])
    return out


def subagents(session_id: str, session_live: bool, now: float) -> list[dict]:
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
                ended(turns, now - updated) or _live_status(updated, session_live, now)
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
    With `detail`, each also carries its workflows and its own subagents."""
    panes, branches, rows = tmux_panes(), {}, []
    now = time.time()
    for p in (claude_dir() / "sessions").glob("*.json"):
        e = read_json(p)
        if not isinstance(e, dict) or not e.get("sessionId") or e.get("spare"):
            continue
        if e.get("kind") not in (None, "interactive", "bg") or not is_live(e):
            continue
        recs = tail_records(_transcript(e["sessionId"]), ("last-prompt", "ai-title"))
        title = (recs.get("ai-title") or {}).get("aiTitle") or ""
        name = e.get("name") or e["sessionId"][:8]
        if e.get("nameSource") == "derived" and title:
            name = title                        # "pablomv-6a" says less than the title
        status = e.get("status") or ""
        changed = (e.get("statusUpdatedAt") or e.get("updatedAt") or e.get("startedAt") or 0) / 1000
        cwd = e.get("cwd") or ""
        rows.append({
            "pid": e["pid"], "session_id": e["sessionId"], "name": name, "title": title,
            "kind": e.get("kind") or "interactive", "state": STATES.get(status, status or "?"),
            "waiting_for": e.get("waitingFor") or "", "since": max(0, int(now - changed)),
            "cwd": cwd, "branch": git_branch(cwd, branches) if cwd else "",
            "tmux": tmux_target(int(e["pid"]), panes) or "",
            "prompt": (recs.get("last-prompt") or {}).get("lastPrompt") or "",
        })
        if detail:
            rows[-1]["workflows"] = workflows(e["sessionId"], True, now)
            rows[-1]["agents"] = subagents(e["sessionId"], True, now)
    rows.sort(key=lambda r: (ORDER.get(r["state"], 3), -r["since"]))
    return rows


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


def resolve(key: str, rows: list[dict]) -> Optional[str]:
    """A tmux target for a tmux session name, session name, title, pid or session id prefix."""
    if shutil.which("tmux") and _tmux_has(key):
        return key
    for r in rows:
        if key in (r["name"], r["title"], str(r["pid"])) or \
                (len(key) >= 4 and r["session_id"].startswith(key)):
            return r["tmux"] or None
    return None


def cmd_go(args) -> int:
    rows = sessions()
    target = resolve(args.name, rows)
    if not target:
        hit = next((r for r in rows if args.name in (r["name"], r["title"], str(r["pid"]))), None)
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
