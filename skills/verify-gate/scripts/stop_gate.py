#!/usr/bin/env python3
"""Claude Code Stop hook: don't let a turn end on a red tree.

install_gate.py copies this file to <repo>/.claude/hooks/stop_gate.py and registers it on
Stop (the gate) and SessionStart (records the tree the session started from). Stdlib only.

On Stop, the first matching rule decides:
  STOP_GATE=0, not a Stop event, plan mode, no git repo or
  no check command                                        -> allow, check not run
  tree already green, a baseline, or its check could not
  run before                                              -> allow, check not run
  same failing tree again in this prompt                  -> allow + notice (no progress)
  same failing tree as in an earlier prompt               -> allow, one notice
  HEAD's commit and every path that differs from it
  predate the session (a checkout, pull, new worktree)    -> allow; the tree is a baseline
  since the last judged stop here, the transcript shows
  no tool call that can write files, or every path that
  changed did so before Claude's first such call; unless
  an agent runs, or a job Claude started ended since and
  untracked files changed (its output is Claude's)        -> allow; the tree is a baseline
  a subagent, workflow or teammate runs that the gate
  hasn't waited for, first wait this prompt and tree      -> allow + notice
  check passes                                            -> allow, tree cached as green
  check fails                                             -> block, last <=21 lines to Claude
  check fails for the --max-failures-th time this prompt,
  or for the 2nd time while background agents run        -> allow + notice (gave up)
  check times out or can't run, or this script breaks    -> allow + notice

A block is hookSpecificOutput.additionalContext on stdout with exit 0: Claude Code keeps the
turn going and labels it "Stop hook feedback" instead of reporting a hook error (2.1.163+).
Notices are systemMessage. This script never exits 2.

The tree hash covers what is on disk, not the commits: every tracked path that differs from
the tree HEAD had when the session first saw this work tree, and every untracked, non-ignored
path (within --watch paths), each by its git blob id. Files over 1 MiB, or past a 16 MiB /
1,000-file read budget, count by size, mtime, ctime and inode. Dirty submodules and nested
repos are hashed the same way; worktrees of this same repository inside the tree are not.
The tree is hashed again after the check. A pass also covers that tree when only check
by-products (caches, coverage data, files it rewrote on two runs in a row) changed meanwhile
and no background task runs; a failure covers it when only untracked files changed. Otherwise
that tree is unjudged and the next stop on it runs the check.
SessionStart records the start tree as a baseline, except on resume of a work tree the session
already judged (its state stands) and on fork (Claude's edits before the copy get checked).
Files: ${CLAUDE_CONFIG_DIR:-~/.claude}/verify-gate/stop_gate.log and state/<session_id>.json.
The loop guard counts failures per prompt_id:
stop_hook_active stays true for the rest of a prompt after any Stop hook blocks (including
/goal's evaluator), so it can't tell a fresh failure from a repeated one.
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__" and sys.path and \
        os.path.realpath(sys.path[0] or os.curdir) == os.path.dirname(os.path.realpath(__file__)):
    del sys.path[0]  # .claude/hooks/ holds other hooks; one named like a stdlib module mustn't load

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402
import signal  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import NamedTuple, Optional  # noqa: E402

STATE_VERSION = 3
TAIL_LINES = 21          # the check's contract: up to 20 summary lines, then its verdict line
LINE_MAX = 300
GREEN_KEEP = 8
BASELINES_KEEP = 16
ROOTS_KEEP = 8
TASKS_KEEP = 32
OUTPUTS_KEEP = 32        # untracked files the check was seen to rewrite on every run
PATHS_KEEP = 500         # changed paths remembered per judged tree, to date later changes
FILE_MAX = 1 << 20       # read and hash files up to 1 MiB each ...
READ_BYTES = 16 << 20    # ... and 16 MiB ...
READ_FILES = 1000        # ... and 1,000 files per hash; size + mtime + ctime + inode for the rest
REPO_DEPTH = 3           # submodules / nested repos hashed this deep
SLACK = 1.0              # a path changed less than a second before a cut-off counts as after it
TICK = 0.02              # ... or 20 ms on a filesystem with sub-second times
TRANSCRIPT_MAX = 16 << 20
TRANSCRIPT_WAIT = 1.0    # Claude Code appends the turn's last message ~0.1 s into the Stop hooks
CANCEL_GRACE = 1.0       # SIGTERM to SIGKILL when Claude Code cancels the gate
STATE_MAX_AGE = 14 * 86400
LOG_MAX = 512 * 1024
CONTEXT_MAX = 9000       # Claude Code caps additionalContext at 10,000 characters
NAME_MAX = 100           # a path named in a block message is cut to its last 100 characters
OFF = {"0", "off", "false", "no"}
TERMINAL = {"completed", "complete", "done", "failed", "error", "killed", "stopped",
            "cancelled", "canceled"}
# Background agents that edit this tree and wake Claude when they finish. Shell and monitor
# tasks may never finish (dev servers, watchers, tail -f), and the rest don't touch the tree.
DEFER_TYPES = {"subagent", "workflow", "teammate"}
# Background tasks that run elsewhere. Every other non-agent task (shell, monitor, MCP task)
# is a job Claude started here: once it ends, what it wrote is Claude's work.
REMOTE_TYPES = {"cloud session"}
# Tools that can't change files in the tree. Any other tool_use in the transcript (Bash, Edit,
# Write, Agent, Skill, MCP tools, a name this list doesn't know) counts as a possible change.
READ_ONLY_TOOLS = frozenset({
    "Read", "Glob", "Grep", "LSP", "WebFetch", "WebSearch", "ToolSearch", "TodoWrite",
    "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TaskOutput", "AskUserQuestion",
    "ListMcpResourcesTool", "ReadMcpResourceTool", "CronList", "ListAgents", "ReportFindings",
    "EnterPlanMode", "ExitPlanMode"})
# Tools that write files and name them in their input, for the block message.
EDIT_TOOLS = {"Edit": "file_path", "MultiEdit": "file_path", "Write": "file_path",
              "NotebookEdit": "notebook_path"}
# What test runners and linters leave behind. When only these change while the check runs,
# its pass covers the tree it leaves behind too.
BYPRODUCT_DIRS = frozenset({b"__pycache__", b".pytest_cache", b".mypy_cache", b".ruff_cache",
                            b".hypothesis"})
# Outcomes after which the tree counts as judged: the transcript mark moves up to here.
SETTLED = {"green-cached", "baseline-cached", "inconclusive-cached", "no-progress", "still-red",
           "existing-work", "not-claudes", "pass", "check-failed", "gave-up", "inconclusive"}
SIGNALS = ("SIGTERM", "SIGINT", "SIGHUP")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
GIT_ENV_DROP = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")

_child: Optional[subprocess.Popen] = None
_guard: Optional[int] = None  # pipe to the watchdog of the running check
_ctx: dict = {}  # what the cancel handler needs to log


class Outcome(NamedTuple):
    block: bool        # True: send Claude back with `context`
    reason: str        # short tag for the log
    context: str = ""  # block message, read by Claude
    message: str = ""  # systemMessage, shown to the user


class Check(NamedTuple):
    argv: object       # list for scripts/check, str for a shell command
    shell: bool
    label: str
    cwd: Path
    runnable: bool = True


class Result(NamedTuple):
    status: str        # pass | fail | inconclusive
    code: int
    tail: str
    error: str
    seconds: float


class Tree(NamedTuple):
    digest: str        # identifies what is on disk
    tracked: str       # the tracked part alone
    untracked: str     # the untracked part alone
    base: str          # tree id the tracked paths are compared with
    changed: tuple     # (path, mode) of each tracked path that differs from `base`
    others: tuple      # each untracked path
    other_ids: tuple   # (path, mode, content id) of each untracked path


class Turn(NamedTuple):
    """What the transcript shows since the last judged stop."""
    complete: bool            # it reaches this turn's final message
    wrote: bool               # some entry may have written files
    since: Optional[float]    # time of the user entry just before the first such entry
    edited: frozenset         # real paths that Edit, Write and NotebookEdit calls named


class Place(NamedTuple):
    root: Path         # top of the work tree being gated
    sub: str           # the project's folder inside it (a monorepo subproject), "" at the top
    common: str        # git common dir: the same for every worktree of the repository

    @property
    def at(self) -> Path:  # where git runs, so --watch paths are relative to the project
        return self.root / self.sub if self.sub else self.root


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse exits 2, which Claude Code reads as "block"
        raise ValueError(message)


def parse_args(argv):
    p = _Parser(prog="stop_gate.py", add_help=False)
    p.add_argument("--mode", choices=("fast", "full"), default="fast")
    p.add_argument("--timeout", type=float, default=240.0)
    p.add_argument("--max-failures", type=int, default=3)
    p.add_argument("--watch", action="append", default=[])
    p.add_argument("--check", default="")
    cfg = p.parse_args(argv)
    if not (math.isfinite(cfg.timeout) and cfg.timeout > 0):
        raise ValueError("--timeout must be a positive number")
    if cfg.max_failures < 2:
        raise ValueError("--max-failures must be 2 or more (at 1 the gate would never block)")
    for path in cfg.watch:
        if not path or os.path.isabs(path) or os.path.normpath(path).split(os.sep)[0] == "..":
            raise ValueError(f"--watch {path!r} must be a path inside the repo")
    return cfg


# --- pure decision logic -------------------------------------------------------------------

def pending_tasks(event: dict) -> list:
    """In-flight background tasks, as listed in the Stop payload."""
    tasks = event.get("background_tasks")
    if not isinstance(tasks, list):
        return []
    out = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = task.get("status")
        if isinstance(status, str) and status.lower() in TERMINAL:
            continue
        out.append(task)
    return out


def agents_running(tasks: list) -> list:
    """Background work that can still change the tree and will wake Claude when it ends."""
    return [t for t in tasks if str(t.get("type", "")).lower() in DEFER_TYPES]


def jobs_running(tasks: list) -> list:
    """Background commands Claude started in this machine: shell, monitor and MCP tasks."""
    return [t for t in tasks if str(t.get("type", "")).lower() not in DEFER_TYPES | REMOTE_TYPES]


def task_key(task: dict) -> str:
    tid = task.get("id")
    if isinstance(tid, (str, int)) and str(tid):
        return str(tid)[:200]
    return f"{task.get('type')}:{task.get('name') or task.get('description') or ''}"[:200]


def prompt_key(event: dict, state: dict, now: float) -> str:
    pid = event.get("prompt_id")
    if isinstance(pid, str) and pid:
        return pid
    # No prompt_id: the first Stop of a prompt is the one with stop_hook_active false.
    if event.get("stop_hook_active") is True and state.get("prompt"):
        return state["prompt"]
    return f"anon-{now:.6f}"


def clean_lines(text: str) -> list:
    lines = []
    for line in ANSI.sub("", text).split("\n"):
        line = line.rstrip("\r").rsplit("\r", 1)[-1].rstrip()  # last frame of progress bars
        lines.append(line if len(line) <= LINE_MAX else line[:LINE_MAX - 1] + "…")
    while lines and not lines[-1]:
        lines.pop()
    return lines


def error_line(lines: list, code: int) -> str:
    for line in reversed(lines):
        s = line.strip()
        if s.startswith(("ERROR", "CHECK FAIL")):
            return s
    for line in reversed(lines):
        if line.strip():
            return f"ERROR exit {code}: {line.strip()}"
    return f"ERROR exit {code}"


def signal_name(num: int) -> str:
    try:
        return signal.Signals(num).name
    except ValueError:
        return f"signal {num}"


def classify(code: int, output: str, seconds: float) -> Result:
    """The check's verdict. A timeout is decided by the caller: it is the only kill that
    means "can't judge"; a check that crashes or is OOM-killed has failed."""
    lines = clean_lines(output)
    tail = "\n".join(lines[-TAIL_LINES:])
    if code == 0:
        return Result("pass", 0, tail, "", seconds)
    if code in (126, 127):  # missing tool, not executable: can't judge (see escalate)
        return Result("inconclusive", code, tail,
                      f"check could not run (exit {code}): {error_line(lines, code)}", seconds)
    if code < 0:
        return Result("fail", code, tail, f"ERROR check killed by {signal_name(-code)}", seconds)
    return Result("fail", code, tail, error_line(lines, code), seconds)


def escalate(result: Result, ran_before: bool) -> Result:
    """A check that ran in this work tree earlier in the session and now can't run (exit
    126/127, or scripts/check lost its exec bit) was broken by a change: that is a failure."""
    if ran_before and result.status == "inconclusive" and result.code in (126, 127):
        return result._replace(status="fail", error=(
            f"ERROR {result.error}; it ran earlier in this session, so a change broke it"))
    return result


def before_check(state: dict, tree: str, prompt: str, agents: bool = False) -> Optional[Outcome]:
    """Decide from cached state alone; None means run the check. Rolls the prompt counter.
    A failure seen while background agents ran is judged again once none runs."""
    if tree in state.get("green", []):
        if state.get("failures"):  # back on a green tree: the failure streak is over
            state["failures"] = 0
        return Outcome(False, "green-cached")
    if tree in state.get("baselines", []):
        return Outcome(False, "baseline-cached")
    if tree in state.get("inconclusive", []):
        return Outcome(False, "inconclusive-cached")
    if state.get("prompt") != prompt:
        state["prompt"] = prompt
        state["failures"] = 0
    fail = state.get("fail") or {}
    if tree in fail.get("trees", []) and not (fail.get("agents") and not agents):
        err = fail.get("error", "")
        if fail.get("prompt") == prompt:
            return Outcome(False, "no-progress", message=(
                f"stop_gate: nothing changed since the last failing check; not blocking again "
                f"this prompt. {err}"))
        if fail.get("noted"):
            return Outcome(False, "still-red")
        fail["noted"] = True
        return Outcome(False, "still-red", message=(
            f"stop_gate: the tree is unchanged since a failing check in an earlier prompt. {err}"))
    return None


def defer(state: dict, tree: str, prompt: str, agents: list) -> Optional[Outcome]:
    """Let one stop through for background agents the gate hasn't waited for yet: they may
    still be changing the tree, and they wake Claude when they finish. Once per agent, once
    per prompt and once per tree; after that the tree is checked even while they run."""
    if not agents:
        return None
    last = state.get("deferred") or {}
    seen = list(last.get("tasks", []))
    keys = [task_key(t) for t in agents]
    if last.get("tree") == tree or last.get("prompt") == prompt or all(k in seen for k in keys):
        return None
    seen += [k for k in dict.fromkeys(keys) if k not in seen]
    state["deferred"] = {"tree": tree, "prompt": prompt, "tasks": seen[-TASKS_KEEP:]}
    kinds = ", ".join(sorted({str(t.get("type")) for t in agents}))
    n = len(agents)
    return Outcome(False, f"background-{n}", message=(
        f"stop_gate: {n} background task{'s' if n > 1 else ''} ({kinds}) still running, so "
        f"this turn isn't checked. The gate waits once per task: the next stop is checked "
        f"even if they are still running."))


def agents_phrase(agents: list) -> str:
    n = len(agents)
    kinds = ", ".join(sorted({str(t.get("type")) for t in agents}))
    return f"{n} background agent{'s' if n > 1 else ''} ({kinds})"


def block_message(label: str, failures: int, limit: int, result: Result,
                  inherited: bool = False, agents: tuple = (), note: str = "") -> str:
    origin = " (this work tree has changes you didn't make, so it may)" if inherited else ""
    body = result.tail if result.error in result.tail else f"{result.tail}\n{result.error}"
    wait = ""
    if agents:  # the orchestrator must not edit files an agent is half-way through
        they = "they are" if len(agents) > 1 else "it is"
        wait = (f"{agents_phrase(agents)} {'are' if len(agents) > 1 else 'is'} still running "
                f"and may be mid-edit. If the failure is in files {they} changing, wait instead "
                f"of editing: say so and end your turn; the tree is checked again once no "
                f"agent runs. Otherwise ")
    fix = "fix" if wait else "Fix"
    head = (f"stop_gate: `{label}` fails on the current tree (failure {failures}; the gate "
            f"stops blocking at {limit}). {wait}{fix} the failure and run the check yourself "
            f"before finishing. If it predates your work{origin}, came in with a checkout or "
            f"pull, or needs the user, say so plainly instead of working around it.")
    return fit(head, note, body.strip())


def fit(head: str, note: str, body: str, limit: int = CONTEXT_MAX) -> str:
    """The block message within `limit` characters. Lines from the middle of the check's tail
    go first, then the note; the tail's last line, the verdict, always stays."""
    lines = body.split("\n")
    text = ""
    for intro in ((f"{head} {note}", head) if note else (head,)):
        keep, cut = list(lines), 0
        while True:
            mid = len(keep) // 2
            shown = keep[:mid] + [f"[… {cut} lines cut]"] + keep[mid:] if cut else keep
            text = f"{intro}\n\n" + "\n".join(shown) + "\n"
            if len(text) <= limit or len(keep) <= 1:
                break
            del keep[(len(keep) - 1) // 2]  # from the middle, never the last line
            cut += 1
        if len(text) <= limit:
            return text
    last = lines[-1][:limit // 2]  # only a head near the limit (a huge --check) gets here
    return f"{text[:limit - len(last) - 2]}\n{last}\n"


def _short(name: str) -> str:
    return name if len(name) <= NAME_MAX else "…" + name[-(NAME_MAX - 1):]


def _names(names: list, most: int = 8) -> str:
    shown = ", ".join(_short(n) for n in names[:most])
    return shown + (f" and {len(names) - most} more" if len(names) > most else "")


def changes_note(changes: Optional[list], turn: Optional[Turn], root: Path,
                 jobs: tuple = ()) -> str:
    """For the block message: the paths that changed since the last judged stop, those that
    changed before Claude's first tool call that can write (so they aren't its work), and
    whether any of them went through its Edit/Write calls. `jobs`: background jobs Claude
    started that finished since, so what changed may be their output, whenever written."""
    if not changes:
        return ""
    lines = [f"Changed since the last check: {_names([os.fsdecode(r) for r, _ in changes])}."]
    if jobs:
        one = len(jobs) == 1
        lines.append(f"{'A background job' if one else 'Background jobs'} you started finished "
                     f"since then ({_names([f'`{j}`' for j in jobs], 3)}): what "
                     f"{'it' if one else 'they'} wrote is your work.")
        return " ".join(lines)
    if turn is None:
        return lines[0]
    if turn.since is not None:
        early = [os.fsdecode(r) for r, when in changes
                 if when is not None and when < turn.since - SLACK]
        if early:
            lines.append(f"Not yours, as they changed before your first tool call since then: "
                         f"{_names(early)}.")
    prefix = os.fsencode(str(root)) + b"/"
    if not any(prefix + rel in turn.edited for rel, _ in changes):
        lines.append("None of them went through your Edit or Write calls. If no command of "
                     "yours changed them either, they are someone else's work in progress (the "
                     "user's editor, another process): say so instead of editing them.")
    return " ".join(lines)


def record(state: dict, tree: str, prompt: str, result: Result, cfg, label: str,
           after: Optional[str] = None, inherited: bool = False, agents: tuple = (),
           note: str = "") -> Outcome:
    """Store the verdict for the tree the check ran on and, when `after` is given, for the
    tree it left behind. While background agents run, the gate blocks once per prompt."""
    trees = [tree] if after in (None, tree) else [tree, after]
    if result.status == "pass":
        keep = [g for g in state.get("green", []) if g not in trees]
        state["green"] = (keep + trees)[-GREEN_KEEP:]
        state["failures"] = 0
        state.pop("fail", None)
        return Outcome(False, "pass")
    if result.status == "inconclusive":
        state["inconclusive"] = trees
        return Outcome(False, "inconclusive",
                       message=f"stop_gate: {result.error}. Not gating this turn.")
    failures = int(state.get("failures", 0)) + 1
    state["failures"] = failures
    state["fail"] = {"trees": trees, "prompt": prompt, "error": result.error}
    limit = min(cfg.max_failures, 2) if agents else cfg.max_failures
    if agents:
        state["fail"]["agents"] = True
        if failures >= limit:
            return Outcome(False, "gave-up", message=(
                f"stop_gate: `{label}` still fails while {agents_phrase(agents)} run; not "
                f"blocking again this prompt. The tree is checked again once no agent runs. "
                f"{result.error}"))
    if failures >= limit:
        return Outcome(False, "gave-up", message=(
            f"stop_gate: `{label}` still failing after {failures} attempts; not blocking again "
            f"this prompt. {result.error}"))
    return Outcome(True, "check-failed", context=block_message(
        label, failures, limit, result, inherited, agents, note))


def add_baseline(state: dict, tree: str) -> None:
    keep = [b for b in state.get("baselines", []) if b != tree]
    state["baselines"] = (keep + [tree])[-BASELINES_KEEP:]


# --- git and the tree hash -----------------------------------------------------------------

def _git_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}
    env.update(GIT_OPTIONAL_LOCKS="0", LC_ALL="C")  # never take index.lock behind Claude's back
    return env


def git(at, *args) -> Optional[bytes]:
    try:
        p = subprocess.run(["git", "-C", str(at), *args], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           env=_git_env(), timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def repo_info(path) -> Optional[tuple]:
    """(toplevel, common git dir) of the work tree containing path, or None."""
    if not path or not os.path.isdir(path):
        return None
    out = git(path, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir")
    if out is None:  # git < 2.31
        out = git(path, "rev-parse", "--show-toplevel", "--git-common-dir")
    lines = os.fsdecode(out).rstrip("\n").split("\n") if out else []  # any bytes a path holds
    if len(lines) < 2 or not lines[0]:
        return None
    common = lines[1] if os.path.isabs(lines[1]) else os.path.join(path, lines[1])
    return lines[0], os.path.realpath(common)


def choose_root(event: dict, env) -> Optional[Place]:
    """The work tree to gate: the one Claude is in (cwd follows worktrees), unless that is a
    different repository from the project's, in which case the project's own tree. When the
    project folder is a subfolder of its repository, `sub` is its path inside it."""
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else os.getcwd()
    here = repo_info(cwd)
    project = env.get("CLAUDE_PROJECT_DIR") or ""
    if here and (not project or os.path.realpath(project) == os.path.realpath(here[0])):
        return Place(Path(here[0]), "", here[1])
    there = repo_info(project) if project else None
    sub = ""
    if there:
        rel = os.path.relpath(os.path.realpath(project), os.path.realpath(there[0]))
        sub = "" if rel == "." or rel.startswith("..") else rel
    top = here if here and (not there or here[1] == there[1]) else (there or here)
    if not top:
        return None
    root = Path(top[0])
    if sub and not (root / sub).is_dir():  # a worktree whose branch lacks the subproject
        sub = ""
    return Place(root, sub, top[1])


def find_check(place: Place, cfg, env) -> Optional[Check]:
    """The project's scripts/check first (a monorepo subproject), then the work tree's."""
    custom = env.get("HARNESS_CHECK_CMD") or cfg.check
    if custom:
        return Check(custom, True, custom, place.at)
    spots = [(place.at, place.sub)] if place.sub else []
    for folder, rel in spots + [(place.root, "")]:
        script = folder / "scripts" / "check"
        if script.is_file():
            return Check([str(script), f"--{cfg.mode}"], False,
                         f"{rel or '.'}/scripts/check --{cfg.mode}", folder,
                         os.access(script, os.X_OK))
    return None


def head_tree(at) -> str:
    out = git(at, "rev-parse", "-q", "--verify", "HEAD^{tree}")
    if not out:  # unborn branch: the empty tree
        out = git(at, "hash-object", "-t", "tree", "/dev/null")
    if not out:
        raise RuntimeError(f"git could not read HEAD at {at}")
    return out.decode().strip()


def pathspec(place: Place, watch) -> list:
    if watch:
        return ["--", *watch]
    return ["--", ":/"] if place.sub else []  # from a subfolder, ls-files lists only that


def _budget() -> dict:
    return {"bytes": READ_BYTES, "files": READ_FILES}


def _blob_id(data: bytes, algo: str) -> bytes:
    h = hashlib.new(algo)
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest().encode()


def _linked_worktree(path: bytes, common: str) -> bool:
    """True for a worktree of this same repository inside the tree (.claude/worktrees/x): it
    is gated on its own, so its files are not part of this tree."""
    if not common:
        return False
    try:
        with open(path + b"/.git", "rb") as f:
            line = f.read(4096).split(b"\n", 1)[0]
    except OSError:  # a .git directory: a repository of its own
        return False
    if not line.startswith(b"gitdir:"):
        return False
    target = os.path.realpath(os.path.join(path, line[7:].strip()))
    return target.startswith(os.fsencode(common) + b"/worktrees/")


def _on_disk(path: bytes, budget: dict, algo: str, depth: int) -> tuple:
    """(git mode, content id) of what is at path now. The id is the git blob id when the file
    fits the read budget, so it matches the ids diff-index reports for clean index entries.
    Paths are bytes: this runs once per changed or untracked file."""
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            return b"120000", _blob_id(os.readlink(path), algo)
    except OSError:
        return b"000000", b"gone"
    meta = b"meta:%d:%d:%d:%d" % (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino)
    if stat.S_ISDIR(st.st_mode):
        if depth < REPO_DEPTH and os.path.exists(path + b"/.git"):  # submodule or nested repo
            try:
                inner = Place(Path(os.fsdecode(path)), "", "")
                sub = tree_hash(inner, (), "", budget=budget, depth=depth + 1)
                return b"160000", b"repo:" + sub.digest.encode()
            except RuntimeError:  # e.g. a .git file pointing at a gitdir that is gone
                pass
        return b"040000", meta
    mode = b"100755" if st.st_mode & stat.S_IXUSR else b"100644"
    if stat.S_ISREG(st.st_mode) and st.st_size <= min(FILE_MAX, budget["bytes"]) \
            and budget["files"] > 0:
        try:
            with open(path, "rb") as f:
                data = f.read(FILE_MAX + 1)
        except OSError:
            return mode, meta
        budget["bytes"] -= len(data)
        budget["files"] -= 1
        return mode, _blob_id(data, algo)
    return mode, meta


def tracked_changes(place: Place, base: str, spec: list, budget: dict, depth: int = 0) -> list:
    """(path, mode, content id) of every tracked path whose content differs from `base`.

    diff-index is plumbing: it never refreshes the index, and reports a stat-dirty file (new
    mtime, maybe the same bytes) with a null id instead of reading it. Those are read here,
    within the budget, and dropped when the bytes turn out to match `base`."""
    raw = git(place.at, "diff-index", "--raw", "-z", "--no-renames", "--ignore-submodules=none",
              base, *spec)
    if raw is None:
        raise RuntimeError(f"git could not compare {place.root} with {base[:12]}")
    algo = "sha256" if len(base) == 64 else "sha1"
    prefix = os.fsencode(str(place.root)) + b"/"
    out = []
    fields = raw.split(b"\0")
    for i in range(0, len(fields) - 1, 2):
        head, rel = fields[i], fields[i + 1]
        meta = head[1:].split(b" ")
        if not head.startswith(b":") or len(meta) < 5:
            raise RuntimeError("unexpected git diff-index output")
        old_mode, new_mode, old_id, new_id, status = meta[:5]
        if status.startswith(b"D"):
            mode, cid = b"000000", b"deleted"
        elif new_mode == b"160000" and os.path.exists(prefix + rel + b"/.git"):
            mode, cid = _on_disk(prefix + rel, budget, algo, depth)
        elif new_id.strip(b"0"):
            mode, cid = new_mode, new_id
        else:
            mode, cid = _on_disk(prefix + rel, budget, algo, depth)
            if (mode, cid) == (old_mode, old_id):
                continue  # only the stat info changed
        out.append((rel, mode, cid))
    return out


def tree_hash(place: Place, watch, label: str, base: Optional[str] = None,
              budget: Optional[dict] = None, depth: int = 0) -> Tree:
    base = base or head_tree(place.at)
    budget = budget if budget is not None else _budget()
    spec = pathspec(place, watch)
    tracked = sorted(tracked_changes(place, base, spec, budget, depth))
    others = git(place.at, "ls-files", "--others", "--exclude-standard", "--full-name", "-z", *spec)
    if others is None:
        raise RuntimeError(f"git could not list untracked files in {place.root}")
    algo = "sha256" if len(base) == 64 else "sha1"
    prefix = os.fsencode(str(place.root)) + b"/"
    untracked = []
    for name in sorted(x for x in others.split(b"\0") if x):
        rel = name.rstrip(b"/")  # "dir/": a nested repository
        if rel != name and _linked_worktree(prefix + rel, place.common):
            continue
        untracked.append((rel, *_on_disk(prefix + rel, budget, algo, depth)))
    seed = b"stop_gate/%d\0%s\0%s\0" % (STATE_VERSION, os.fsencode(str(place.root)),
                                         os.fsencode(label))
    whole, part_t, part_u = (hashlib.sha256(seed + base.encode()),
                             hashlib.sha256(seed + base.encode()), hashlib.sha256(seed))
    for entry in sorted(tracked + untracked):
        whole.update(b"%s\0%s\0%s\n" % entry)
    for entry in tracked:
        part_t.update(b"%s\0%s\0%s\n" % entry)
    for entry in untracked:
        part_u.update(b"%s\0%s\0%s\n" % entry)
    return Tree(whole.hexdigest()[:32], part_t.hexdigest()[:32], part_u.hexdigest()[:32], base,
                tuple((rel, mode) for rel, mode, _ in tracked), tuple(e[0] for e in untracked),
                tuple(untracked))


def byproduct(rel: bytes) -> bool:
    """A file checks leave behind: a Python or tool cache, coverage data."""
    parts = rel.split(b"/")
    if any(p in BYPRODUCT_DIRS for p in parts[:-1]) or any(
            a == b"node_modules" and b == b".cache" for a, b in zip(parts, parts[1:-1])):
        return True
    return parts[-1].endswith((b".pyc", b".pyo")) or parts[-1].startswith(b".coverage")


def untracked_delta(before: Tree, after: Tree) -> set:
    """Each untracked path that appeared, changed or went away between the two trees."""
    return {e[0] for e in set(before.other_ids) ^ set(after.other_ids)}


def only_byproducts(before: Tree, after: Tree, outputs=frozenset()) -> bool:
    """Whether every untracked path that changed between the two trees is a by-product, or a
    file this check was seen to rewrite on every run (`outputs`). A new module is not."""
    return all(byproduct(rel) or rel in outputs for rel in untracked_delta(before, after))


def untracked_source(tree: Tree) -> str:
    """The untracked part of the tree, by-products left out: what a background job's output
    changes."""
    h = hashlib.sha256()
    for entry in tree.other_ids:
        if not byproduct(entry[0]):
            h.update(b"%s\0%s\0%s\n" % entry)
    return h.hexdigest()[:32]


def root_state(state: dict, root: Path) -> dict:
    """This work tree's part of the session state; the least recently used beyond 8 go."""
    roots = state.setdefault("roots", {})
    entry = roots.setdefault(str(root), {})
    entry["t"] = time.time()
    while len(roots) > ROOTS_KEEP:
        roots.pop(min(roots, key=lambda k: roots[k].get("t", 0)))
    return entry


def session_tree(place: Place, cfg, label: str, rs: dict) -> Tree:
    """Hash against the tree HEAD had when this session first saw the work tree, so commits,
    which don't change what is on disk, don't change the hash either."""
    base = rs.get("base")
    if isinstance(base, str) and base:
        try:
            return tree_hash(place, cfg.watch, label, base)
        except RuntimeError:  # base tree gone (gc'd, or another repo): start from HEAD
            pass
    tree = tree_hash(place, cfg.watch, label)
    rs["base"] = tree.base
    return tree


def predates_session(place: Place, cfg, state: dict, tree: Tree) -> Optional[bool]:
    """Whether the tree is a commit older than the session plus changes that all are too: a
    checkout, a pull, a stash, or a worktree set up before or outside Claude's work. None if
    not, else whether it has uncommitted tracked changes. Each path is dated by its ctime,
    which only the clock sets; a deletion or a directory (a nested repo) counts as new."""
    start = state.get("start")
    if not isinstance(start, (int, float)):
        return None
    when = git(place.at, "log", "-1", "--format=%ct", "HEAD")
    try:  # %ct has whole seconds: a commit in the session's first second counts as new
        if not when or int(when.strip()) >= int(start):
            return None
    except ValueError:
        return None
    head = head_tree(place.at)
    changed = tree.changed if tree.base == head else tuple(
        (rel, mode) for rel, mode, _ in
        tracked_changes(place, head, pathspec(place, cfg.watch), _budget()))
    prefix = os.fsencode(str(place.root)) + b"/"
    for rel, mode in changed + tuple((rel, b"") for rel in tree.others):
        if mode == b"000000":
            return None
        try:
            st = os.lstat(prefix + rel)
        except OSError:
            return None
        if stat.S_ISDIR(st.st_mode) or max(st.st_mtime, st.st_ctime) >= start - SLACK:
            return None
    return bool(changed)


def changes_since(place: Place, mark, tree: Tree) -> Optional[list]:
    """(path, time) of each path that may differ from the tree judged at `mark`, timed by the
    later of its ctime and mtime. The time is None where that can't date the change: a
    deletion, a directory (a nested repo), a path that turned up with an old time. None when
    the mark can't tell (no path list, another base, too many paths)."""
    if not isinstance(mark, dict):
        return None
    then, t0 = mark.get("paths"), mark.get("t0")
    if not _strs(then) or not isinstance(t0, (int, float)) or mark.get("base") != tree.base:
        return None
    now = {rel for rel, _ in tree.changed} | set(tree.others)
    if len(now) > PATHS_KEEP:
        return None
    then = {os.fsencode(p) for p in then}
    prefix = os.fsencode(str(place.root)) + b"/"
    out = []
    for rel in sorted(now | then):
        try:
            st = os.lstat(prefix + rel)
        except OSError:
            out.append((rel, None))
            continue
        ns = max(st.st_ctime_ns, st.st_mtime_ns)
        when = ns / 1e9
        # File times lag the clock by up to a kernel tick; whole seconds mean a coarse clock.
        slack = SLACK if ns % 1_000_000_000 == 0 else TICK
        if stat.S_ISDIR(st.st_mode):
            out.append((rel, None))
        elif when >= t0 - slack:
            out.append((rel, when))
        elif rel not in now or rel not in then:
            out.append((rel, None))
    return out


# --- the transcript: did Claude write anything? -------------------------------------------

def _text_id(text) -> str:
    return hashlib.sha256(str(text).strip().encode("utf-8", "replace")).hexdigest()[:32]


def open_transcript(path: str):
    """The transcript opened for reading, or None when it isn't a regular file. Opened
    without blocking, so a FIFO can't hang the gate; raises OSError like open()."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        return None
    return os.fdopen(fd, "rb")


def transcript_mark(event: dict, tree: Tree, tasks: list, t0: float,
                    top: bool = False) -> Optional[dict]:
    """Where the transcript ends now, at a line boundary: the next stop reads from here, or
    from its top with `top`. The turn's final message is usually appended after this point,
    so its id is kept too. Also the judged tree's paths, when it was hashed (t0) and when
    this stop ended (t1), so the next stop can date what changed since."""
    path = event.get("transcript_path")
    if not isinstance(path, str) or not path:
        return None
    try:
        f = open_transcript(path)
        if f is None:
            return None
        with f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - 65536)
            f.seek(start)
            tail = f.read(size - start)
    except FileNotFoundError:
        size, start, tail = 0, 0, b""  # SessionStart runs before the file exists
    except OSError:
        return None
    cut = tail.rfind(b"\n")
    if top:
        size = 0
    elif cut < 0 and size:
        return None
    final = event.get("last_assistant_message") if not top else None
    n = len(tree.changed) + len(tree.others)
    paths = None if n > PATHS_KEEP else (
        [os.fsdecode(rel) for rel, _ in tree.changed] + [os.fsdecode(rel) for rel in tree.others])
    return {"path": path, "offset": start + cut + 1 if size else 0, "tracked": tree.tracked,
            "tasks": bool(tasks), "agents": bool(agents_running(tasks)),
            "final": _text_id(final) if isinstance(final, str) and final.strip() else "",
            "base": tree.base, "paths": paths, "t0": t0, "t1": time.time()}


def _when(entry: dict) -> Optional[float]:
    """An entry's timestamp ("2026-10-09T09:15:22.516Z") in seconds, or None."""
    from datetime import datetime
    ts = entry.get("timestamp")
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts[:-1] + "+00:00" if ts.endswith("Z") else ts)
    except ValueError:
        return None
    return dt.timestamp() if dt.tzinfo else None


def _wrote(seen: dict, dated: bool = True) -> None:
    if not seen["wrote"]:
        seen["wrote"], seen["since"] = True, seen["user"] if dated else None


def _scan(entry, final: Optional[str], seen: dict) -> None:
    """Fold one transcript entry into `seen`: whether anything may have written files
    ("wrote"), the time of the user entry just before the first such entry ("since"), the
    paths Edit-like calls named ("edited"), and whether the last assistant text so far is
    this turn's final message ("final")."""
    if not isinstance(entry, dict):
        return _wrote(seen, dated=False)
    if entry.get("isSidechain") is True:
        return _wrote(seen)
    if entry.get("type") == "user":  # a prompt or a tool result: the next request is later
        seen["user"] = _when(entry)
        return
    if entry.get("type") != "assistant":
        return
    content = (entry.get("message") or {}).get("content") \
        if isinstance(entry.get("message"), dict) else None
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
    if not isinstance(blocks, list):
        return _wrote(seen)
    for block in blocks:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "tool_use":
            name = block.get("name")
            if name not in READ_ONLY_TOOLS:
                _wrote(seen)
                args = block.get("input") if isinstance(block.get("input"), dict) else {}
                target = args.get(EDIT_TOOLS.get(name, ""))
                if isinstance(target, str) and target:
                    seen["edited"].add(os.fsencode(os.path.realpath(target)))
            seen["final"] = False
        elif kind == "text":
            tid = _text_id(block.get("text", ""))
            if seen["prev"] and tid == seen["prev"]:  # the previous stop's final, written late
                seen["prev"], seen["final"] = "", False
            else:
                seen["final"] = tid == final
        elif kind not in ("thinking", "redacted_thinking"):
            _wrote(seen)  # a block type this gate doesn't know: assume the worst


def read_turn(event: dict, mark, wait: float) -> Optional[Turn]:
    """The transcript from `mark`, the last stop here whose tree was judged, to its end. Claude
    Code appends this turn's final message shortly after Stop hooks start, so it is polled for
    up to `wait` seconds, until that message or an entry that may write shows up. None when
    the transcript can't be used (another file, rewritten, too big, not JSON lines)."""
    if not isinstance(mark, dict):
        return None
    path, final = event.get("transcript_path"), event.get("last_assistant_message")
    offset = mark.get("offset")
    if not isinstance(path, str) or path != mark.get("path") or not isinstance(offset, int):
        return None
    final = _text_id(final) if isinstance(final, str) and final.strip() else None
    seen = {"final": False, "prev": str(mark.get("final") or ""), "wrote": False,
            "user": None, "since": None, "edited": set()}
    deadline = time.monotonic() + wait
    pos, partial = offset, b""
    try:
        f = open_transcript(path)
        if f is None:
            return None
        with f:
            if os.fstat(f.fileno()).st_size < offset:  # rewritten since the mark
                return None
            while True:
                f.seek(pos)
                data = f.read(TRANSCRIPT_MAX + 1 - (pos - offset))
                pos += len(data)
                if pos - offset > TRANSCRIPT_MAX:
                    return None
                lines = (partial + data).split(b"\n")
                partial = lines.pop()  # the line being written, if any
                for line in lines:
                    if line.strip():
                        _scan(json.loads(line), final, seen)
                if seen["final"] or seen["wrote"] or final is None \
                        or time.monotonic() > deadline:
                    break
                time.sleep(0.02)
    except (OSError, ValueError, UnicodeDecodeError, RecursionError):
        return None
    return Turn(bool(seen["final"]), seen["wrote"], seen["since"], frozenset(seen["edited"]))


def not_claudes(place: Place, mark, tree: Tree, tasks: list, read,
                job_output: bool = False) -> str:
    """Why the change since `mark` isn't Claude's, or "" without positive evidence:
      "transcript": no tool call that can write files since the mark, up to this turn's
                    final message (a dev server's log, the user's editor);
      "time":       every path that changed since the mark did so after that stop ended and
                    more than a second before the user entry that preceded Claude's first such
                    call (the user's edit between prompts, on a turn that ran Bash).
    Background agents rule both out (their edits aren't in this transcript). So do background
    jobs when a tracked file changed (Claude may have started the job), and `job_output`: a
    job Claude started has ended since it was first seen, and the untracked part changed
    meanwhile. `read()` returns the Turn; it is called only when those don't rule it out."""
    if not isinstance(mark, dict) or mark.get("agents") or agents_running(tasks) or job_output:
        return ""
    if (tasks or mark.get("tasks")) and tree.tracked != mark.get("tracked"):
        return ""
    turn = read()
    if turn is None:
        return ""
    if not turn.wrote:
        return "transcript" if turn.complete else ""
    t1 = mark.get("t1")
    if turn.since is None or not isinstance(t1, (int, float)):
        return ""
    changes = changes_since(place, mark, tree)
    if not changes or any(when is None or not t1 < when < turn.since - SLACK
                          for _, when in changes):
        return ""
    return "time"


# --- running the check ---------------------------------------------------------------------

def _signals() -> list:
    return [getattr(signal, s) for s in SIGNALS if hasattr(signal, s)]


def _kill_group(proc: subprocess.Popen, grace: float = 2.0) -> None:
    if not hasattr(os, "killpg"):  # no process groups (Windows): the check's own process only
        try:
            proc.kill()
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2)):
        try:
            os.killpg(proc.pid, sig)
        except OSError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


def _watchdog(pgid: int) -> Optional[int]:
    """Fork a process that SIGKILLs the check's group if the gate dies without releasing it
    first. SIGKILL can't be handled, and the check runs in its own session, so without this a
    killed gate would leave the test run going. Returns the release pipe, or None (no fork)."""
    sigs = _signals()
    masked = hasattr(signal, "pthread_sigmask") and hasattr(os, "fork")
    try:
        r, w = os.pipe()
    except OSError:
        return None
    old = None
    if masked:  # no cancel handler may run in the child between fork and the reset below
        old = signal.pthread_sigmask(signal.SIG_BLOCK, sigs)
    try:
        pid = os.fork()
    except (AttributeError, OSError):
        pid = None
    if pid == 0:
        try:
            for s in sigs:  # Claude Code signals the whole tree; the gate's handler does the work
                signal.signal(s, signal.SIG_IGN)
            if masked:
                signal.pthread_sigmask(signal.SIG_UNBLOCK, sigs)
            os.setsid()  # out of the gate's group, so a kill aimed at that group misses it
            null = os.open(os.devnull, os.O_RDWR)
            for fd in (0, 1, 2):  # don't hold Claude Code's pipes open
                os.dup2(null, fd)
            os.close(w)
            if not os.read(r, 1):  # EOF without a release byte: the gate was killed
                os.killpg(pgid, signal.SIGKILL)
        except BaseException:
            pass
        os._exit(0)
    if masked:  # back to the caller's mask: run_check keeps the signals held until it returns
        signal.pthread_sigmask(signal.SIG_SETMASK, old)
    os.close(r)
    if pid is None:
        os.close(w)
        return None
    return w


def _release(guard: Optional[int]) -> None:
    if guard is not None:
        try:
            os.write(guard, b"x")
            os.close(guard)
        except OSError:
            pass


def run_check(check: Check, timeout: float) -> Result:
    global _child, _guard
    import tempfile
    env = dict(os.environ, STOP_GATE="0")  # a check that starts Claude must not gate itself
    sigs = _signals()
    masked = bool(sigs) and hasattr(signal, "pthread_sigmask")
    t0 = time.monotonic()
    with tempfile.TemporaryFile() as out:
        # A cancel that lands after the check has started but before _child is set would find
        # nothing to kill: hold the signals until the check and its watchdog are in place. The
        # check itself starts with the gate's original mask.
        old = signal.pthread_sigmask(signal.SIG_BLOCK, sigs) if masked else None
        try:
            try:
                _child = subprocess.Popen(
                    check.argv, shell=check.shell, cwd=str(check.cwd), stdin=subprocess.DEVNULL,
                    stdout=out, stderr=subprocess.STDOUT, env=env, start_new_session=True,
                    preexec_fn=(lambda: signal.pthread_sigmask(signal.SIG_SETMASK, old))
                    if masked else None)
            except OSError as exc:
                return Result("inconclusive", 127, "", f"check could not start ({exc})", 0.0)
            _guard = _watchdog(_child.pid)
        finally:
            if masked:  # a cancel held meanwhile runs now, with _child and _guard set
                signal.pthread_sigmask(signal.SIG_SETMASK, old)
        try:
            code = _child.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            _kill_group(_child)
            code, timed_out = -1, True
        finally:
            _release(_guard)
            _child = _guard = None
        seconds = time.monotonic() - t0
        size = out.seek(0, os.SEEK_END)
        out.seek(max(0, size - 65536))
        text = out.read().decode("utf-8", "replace")
    if timed_out:
        tail = "\n".join(clean_lines(text)[-TAIL_LINES:])
        return Result("inconclusive", -1, tail, f"`{check.label}` timed out after {timeout:g}s",
                      seconds)
    return classify(code, text, seconds)


# --- state and log -------------------------------------------------------------------------

def home_dir(env) -> Path:
    base = env.get("CLAUDE_CONFIG_DIR") or os.path.join(env.get("HOME") or str(Path.home()), ".claude")
    return Path(base) / "verify-gate"


def state_file(session, env) -> Path:
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", session if isinstance(session, str) and session else "no-session")
    return home_dir(env) / "state" / f"{name[:128]}.json"


def _strs(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _valid(state: dict) -> bool:
    """The shapes the code relies on. Anything else (a hand edit, another version's file)
    means a fresh state, not a TypeError on every stop."""
    if not all(_strs(state.get(k, [])) for k in ("green", "baselines", "inconclusive")):
        return False
    if not isinstance(state.get("failures", 0), int) \
            or not isinstance(state.get("prompt", ""), str) \
            or not isinstance(state.get("start", 0), (int, float)):
        return False
    for key, lists in (("fail", ("trees",)), ("deferred", ("tasks",))):
        part = state.get(key, {})
        if not isinstance(part, dict) or not all(_strs(part.get(k, [])) for k in lists) \
                or not all(isinstance(part.get(k, ""), str) for k in ("prompt", "error", "tree")):
            return False
    roots = state.get("roots", {})
    if not isinstance(roots, dict):
        return False
    for rs in roots.values():
        if not isinstance(rs, dict) or not isinstance(rs.get("base", ""), str) \
                or not isinstance(rs.get("t", 0), (int, float)) \
                or not isinstance(rs.get("unjudged", ""), str) \
                or not all(_strs(rs.get(k, [])) for k in ("left", "outputs")):
            return False
        jobs = rs.get("jobs", {})
        if not isinstance(jobs, dict) or not all(
                isinstance(j, dict) and isinstance(j.get("src"), str) and _strs(j.get("held", []))
                and isinstance(j.get("what", ""), str) for j in jobs.values()):
            return False
        mark = rs.get("mark")
        if mark is not None and not (isinstance(mark, dict) and isinstance(mark.get("path"), str)
                                     and isinstance(mark.get("offset"), int)):
            return False
    return True


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return {"v": STATE_VERSION}
    if not isinstance(data, dict) or data.get("v") != STATE_VERSION or not _valid(data):
        return {"v": STATE_VERSION}
    return data


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(state, sort_keys=True))
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    if fresh:  # a new session: drop state of sessions idle for two weeks
        cutoff = time.time() - STATE_MAX_AGE
        for old in path.parent.glob("*.json"):
            try:
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                pass


def log_file(env) -> Path:
    return home_dir(env) / "stop_gate.log"


def write_log(env, line: str) -> None:
    try:
        path = log_file(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_MAX:
            os.replace(path, path.with_suffix(".log.1"))
        with path.open("a", encoding="utf-8", errors="backslashreplace") as f:
            f.write(line.rstrip("\n") + "\n")
    except Exception:  # the log is best effort; it must never cost the decision
        pass


def log_line(event: dict, reason: str, verdict: str, t0: float, trace: dict) -> str:
    ms = int((time.monotonic() - t0) * 1000)
    extras = " ".join(f"{k}={v}" for k, v in trace.items())
    return (f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {event.get('hook_event_name', '-')} "
            f"session={str(event.get('session_id', '-'))[:8]} {reason} {verdict} ms={ms} {extras}")


# --- hook entry points ---------------------------------------------------------------------

def session_start(event: dict, cfg, env, trace: dict) -> Outcome:
    """Record the tree the session starts from as a baseline, with the start time and where
    the transcript ends. Two sources don't:
      resume of a work tree this session already judged keeps all of it, so edits from a turn
        cut off before its Stop (an interrupt, a crash, a killed run) are still unjudged;
      fork (a copy of the conversation: /fork, /branch, /background, --fork-session) records
        no baseline and no start, and reads the copied transcript from its top, so Claude's
        edits from before the copy are judged at the first stop."""
    source = event.get("source")
    if source == "compact":  # same session, same work: keep the baseline
        return Outcome(False, "baseline-kept")
    place = choose_root(event, env)
    check = find_check(place, cfg, env) if place else None
    if not check:
        return Outcome(False, "no-check")
    trace["root"] = str(place.root)
    path = state_file(event.get("session_id"), env)
    state = load_state(path)
    if source == "resume" and str(place.root) in state.get("roots", {}):
        return Outcome(False, "resumed")
    rs = root_state(state, place.root)
    t0 = time.time()
    tree = session_tree(place, cfg, check.label, rs)
    head = head_tree(place.at)
    if tree.base == head:
        dirty = bool(tree.changed)
    else:  # a resumed session keeps its first base; uncommitted means against HEAD
        dirty = bool(tracked_changes(place, head, pathspec(place, cfg.watch), _budget()))
    fork = source == "fork"
    if not fork:
        if not (source == "resume" and isinstance(state.get("start"), (int, float))):
            state["start"] = time.time()
        add_baseline(state, tree.digest)
    rs["inherited"] = dirty or bool(rs.get("inherited"))
    mark = transcript_mark(event, tree, [], t0, top=fork)
    if mark:
        rs["mark"] = mark
    try:
        save_state(path, state)
    except OSError as exc:  # quietly: the first Stop says the state can't be written
        trace["state_error"] = type(exc).__name__
    return Outcome(False, "fork" if fork else "baseline")


def judge(place: Place, check: Check, cfg, state: dict, rs: dict, tree: Tree, prompt: str,
          trace: dict, state_path: Path, tasks: list, judged: list, note) -> Outcome:
    """Run the check on the tree and record the verdict. Hashes the tree again afterwards:
    `judged` becomes that tree when the verdict covers it too, and otherwise it is left
    unjudged, so the next stop on it runs the check. `note()` adds to a block message."""
    try:  # no loop guard without state: don't run the check, don't block
        save_state(state_path, state)
    except OSError as exc:
        trace["state_error"] = type(exc).__name__
        return Outcome(False, "state-unwritable", message=(
            f"stop_gate: can't write its state ({type(exc).__name__}: {state_path}); "
            f"not gating."))
    if check.runnable:
        result = run_check(check, cfg.timeout)
    else:
        result = Result("inconclusive", 126, "", "scripts/check is not executable "
                        "(chmod +x scripts/check)", 0.0)
    ran = result.status in ("pass", "fail")
    result = escalate(result, bool(rs.get("ran")))
    if ran:
        rs["ran"] = True
    trace["check_s"] = round(result.seconds, 2)
    t1 = time.time()
    try:
        after = tree_hash(place, cfg.watch, check.label, tree.base)
    except RuntimeError:
        after = None
    share, notice = None, ""
    again = rs.pop("unjudged", None) == tree.digest  # what the last check left behind
    left = {os.fsencode(p) for p in rs.pop("left", [])}
    if after is not None and after.digest != tree.digest:
        trace["check_changed_tree"] = 1
        outputs = {os.fsencode(p) for p in rs.get("outputs", [])}
        fresh = {r for r in untracked_delta(tree, after) if not byproduct(r) and r not in outputs}
        if after.tracked != tree.tracked:  # a formatter, an editor or an agent wrote meanwhile
            trace["tracked_changed_meanwhile"] = 1
        elif result.status != "pass" or (not tasks and only_byproducts(tree, after, outputs)):
            share = after.digest  # a pass only over caches: a new module may be red
        elif not tasks and again:  # untracked files changed during two passing runs in a row
            names = sorted(os.fsdecode(r) for r in fresh)
            them = "it" if len(names) == 1 else "them"
            if fresh <= left:  # the same files both times: the check's own output (junit.xml)
                rs["outputs"] = (rs.get("outputs", []) + names)[-OUTPUTS_KEEP:]
                share = after.digest
                trace["outputs"] = len(names)
                notice = (f"stop_gate: `{check.label}` rewrites {_names(names)} on every run, so "
                          f"the gate now counts {them} as its output. Gitignore {them}.")
            elif not rs.get("told"):
                rs["told"] = True
                notice = (f"stop_gate: untracked files changed while `{check.label}` ran, on "
                          f"two runs in a row ({_names(names)}), so the next stop runs it "
                          f"again. If the check writes them, gitignore them.")
        if share:
            judged[:] = [after, t1]
        else:
            rs["unjudged"] = after.digest
            rs["left"] = sorted(os.fsdecode(r) for r in fresh)[:PATHS_KEEP]
    agents = tuple(agents_running(tasks))
    out = record(state, tree.digest, prompt, result, cfg, check.label, share,
                 bool(rs.get("inherited")), agents, note() if result.status == "fail" else "")
    return out._replace(message=notice) if notice and not out.message else out


def stop(event: dict, cfg, env, trace: dict) -> Outcome:
    if event.get("permission_mode") == "plan":
        return Outcome(False, "plan-mode")
    place = choose_root(event, env)
    if not place:
        return Outcome(False, "no-repo")
    check = find_check(place, cfg, env)
    if not check:
        return Outcome(False, "no-check")
    trace["root"] = str(place.root)
    if place.sub:
        trace["sub"] = place.sub
    path = state_file(event.get("session_id"), env)
    state = load_state(path)
    before = json.dumps(state, sort_keys=True)
    rs = root_state(state, place.root)
    t0 = time.time()
    tree = session_tree(place, cfg, check.label, rs)
    prompt = prompt_key(event, state, time.time())
    trace["prompt"] = prompt[:8]
    trace["active"] = int(event.get("stop_hook_active") is True)
    tasks = pending_tasks(event)
    agents = agents_running(tasks)
    if tasks:
        trace["bg"] = ",".join(sorted({str(t.get("type")).replace(" ", "_") for t in tasks}))
    # Jobs Claude started (shell, monitor, MCP tasks) that were running at a judged stop, with
    # the untracked part as it was then. Once one has ended, what it wrote is Claude's work:
    # trees taken as someone else's while it ran are no baselines any more, and a change to
    # the untracked part since it was first seen rules out the someone-else rules below.
    jobs = rs.get("jobs") or {}
    live = {task_key(t): t for t in jobs_running(tasks)}
    ended = [k for k in jobs if k not in live]
    job_output = False
    if ended:
        held = {d for k in ended for d in jobs[k].get("held", [])}
        state["baselines"] = [b for b in state.get("baselines", []) if b not in held]
        src = untracked_source(tree)
        job_output = any(jobs[k]["src"] != src for k in ended)
        trace["jobs_ended"] = len(ended)
    out = before_check(state, tree.digest, prompt, bool(agents))
    unjudged = out is None and tree.digest == rs.get("unjudged")
    if unjudged:  # left behind by the last check, which didn't see all of it: check it
        trace["unjudged"] = 1
    if out is None and not unjudged:
        dirty = predates_session(place, cfg, state, tree)
        if dirty is not None:
            add_baseline(state, tree.digest)
            rs["inherited"] = dirty or bool(rs.get("inherited"))
            out = Outcome(False, "existing-work")
    mark, turns = rs.get("mark"), []

    def turn(wait: float = TRANSCRIPT_WAIT) -> Optional[Turn]:  # read once, when first needed
        if not turns:
            turns.append(read_turn(event, mark, wait))
        return turns[0]
    if out is None and not unjudged:
        why = not_claudes(place, mark, tree, tasks, turn, job_output)
        if why:
            add_baseline(state, tree.digest)
            rs["inherited"] = tree.tracked != mark.get("tracked") or bool(rs.get("inherited"))
            trace["by"] = why
            out = Outcome(False, "not-claudes")
            src = untracked_source(tree)
            for k in live:  # maybe a running job's output: a baseline only until it ends
                if k in jobs and jobs[k]["src"] != src:
                    jobs[k]["held"] = (jobs[k].get("held", []) + [tree.digest])[-BASELINES_KEEP:]
    if out is None:
        out = defer(state, tree.digest, prompt, agents)
    judged = [tree, t0]
    if out is None:
        def note() -> str:
            done = tuple(jobs[k].get("what") or k for k in ended) if job_output else ()
            return changes_note(changes_since(place, mark, tree), turn(0), place.root, done)
        out = judge(place, check, cfg, state, rs, tree, prompt, trace, path, tasks,
                    judged, note)
    if out.reason in SETTLED:
        new = transcript_mark(event, judged[0], tasks, judged[1])
        if new:
            rs["mark"] = new
        else:
            rs.pop("mark", None)
        for k in ended:  # judged now, with whatever they wrote
            jobs.pop(k, None)
        for k, task in live.items():
            if k not in jobs:
                what = task.get("command") or task.get("description") or task.get("type")
                jobs[k] = {"src": untracked_source(tree), "held": [], "what": str(what)[:80]}
        while len(jobs) > TASKS_KEEP:
            jobs.pop(next(iter(jobs)))
        if jobs:
            rs["jobs"] = jobs
        else:
            rs.pop("jobs", None)
    if json.dumps(state, sort_keys=True) != before:
        try:
            save_state(path, state)
        except OSError as exc:
            trace["state_error"] = type(exc).__name__
            if out.block:  # without the failure on record, every stop would block again
                out = Outcome(False, "state-unwritable", message=(
                    f"stop_gate: `{check.label}` fails, but the gate can't write its state "
                    f"({type(exc).__name__}: {path}); not blocking, as it couldn't tell a "
                    f"repeat. {(state.get('fail') or {}).get('error', '')}"))
    return out


def _on_cancel(signum, frame):  # Claude Code gave up on us: take the check down too
    if _ctx.get("cancelled"):
        return  # a second signal while the first one's cleanup runs
    _ctx["cancelled"] = True
    try:
        if _child is not None:  # run_check holds these signals until _child is set
            _kill_group(_child, CANCEL_GRACE)  # SIGTERM first, so the check can clean up
            _release(_guard)
        trace = dict(_ctx.get("trace") or {}, signal=signal_name(signum))
        write_log(_ctx.get("env") or os.environ,
                  log_line(_ctx.get("event") or {}, "cancelled", "allow",
                           _ctx.get("t0", time.monotonic()), trace))
    finally:
        os._exit(0)


def main(argv=None) -> int:
    env = os.environ
    t0 = time.monotonic()
    trace: dict = {}
    event: dict = {}
    _ctx.update(env=env, t0=t0, trace=trace, event=event)
    for sig in SIGNALS:
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), _on_cancel)
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw) if raw.strip() else None
        except ValueError:
            parsed = None
        valid = isinstance(parsed, dict)
        if valid:
            event.update(parsed)
        name = str(event.get("hook_event_name") or "Stop")
        if str(env.get("STOP_GATE", "")).strip().lower() in OFF:
            out = Outcome(False, "disabled")
        elif not valid:
            out = Outcome(False, "bad-input")
        else:
            cfg = parse_args(sys.argv[1:] if argv is None else argv)
            if name == "SessionStart":
                out = session_start(event, cfg, env, trace)
            elif name == "Stop":
                out = stop(event, cfg, env, trace)
            else:
                out = Outcome(False, "not-stop")
    except Exception as exc:  # fail open: a broken gate must never trap Claude
        import traceback
        trace["error"] = traceback.format_exc().strip().replace("\n", " | ")
        out = Outcome(False, "internal-error", message=(
            f"stop_gate: internal error ({type(exc).__name__}: {exc}); not gating. "
            f"Log: {log_file(env)}"))
        print(f"stop_gate: {trace['error']}", file=sys.stderr)
    write_log(env, log_line(event, out.reason, "block" if out.block else "allow", t0, trace))
    reply: dict = {}
    if out.message:
        reply["systemMessage"] = out.message[:4000]
    if out.block:
        reply["hookSpecificOutput"] = {"hookEventName": "Stop",
                                       "additionalContext": out.context[:CONTEXT_MAX]}
    try:
        if reply:
            print(json.dumps(reply))
        sys.stdout.flush()
    except Exception:  # reader gone: point fd 1 at /dev/null so the exit-time flush can't fail
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), 1)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
