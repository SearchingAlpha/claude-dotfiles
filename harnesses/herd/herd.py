#!/usr/bin/env python3
"""herd — one attention list of every live Claude Code session on this machine.

    herd [ls] [--json]                       blocked first, then done, then working
    herd watch [-n SECS] [--no-bell]         the same, redrawn; rings when a session blocks
    herd new <name> [dir] [-- claude args…]  start claude in a detached tmux session
    herd go <name>                           attach (or switch) to a session's tmux pane

Read-only over what Claude Code already writes: its session registry
(~/.claude/sessions/<pid>.json) and the tail of each transcript. No daemon, no
hooks, no state of its own. Stdlib only, Python 3.9+. See harnesses/herd/README.md."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

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


def tail_records(path: Optional[Path], types: tuple[str, ...]) -> dict:
    """The latest record of each wanted type. Claude Code re-appends its bookkeeping
    records (last-prompt, ai-title) after every turn, so the tail almost always has
    them; the window grows only when a huge tool result sits at the end."""
    found: dict = {}
    if path is None:
        return found
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            step = 65536
            while True:
                f.seek(max(0, size - step))
                lines = f.read().split(b"\n")
                if size > step:
                    lines = lines[1:]           # the first line is probably cut
                for ln in reversed(lines):
                    if not ln.strip():
                        continue
                    try:
                        rec = json.loads(ln)
                    except ValueError:
                        continue
                    t = rec.get("type") if isinstance(rec, dict) else None
                    if t in types and t not in found:
                        found[t] = rec
                if len(found) == len(types) or size <= step or step >= 4 * 1048576:
                    return found
                step *= 4
    except OSError:
        return found


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


# --- the list ------------------------------------------------------------------------

def sessions() -> list[dict]:
    """Every live session, most urgent first; within a state, waiting longest first."""
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
    if not argv or argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["ls", *argv]
    extra: list[str] = []
    if argv[0] == "new" and "--" in argv:       # everything after -- goes to claude untouched
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    args = ap.parse_args(argv)
    args.claude_args = extra
    return {"ls": cmd_ls, "watch": cmd_watch, "new": cmd_new, "go": cmd_go}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
