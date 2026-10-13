#!/usr/bin/env python3
"""Bash guard for the Datanet write toolkit (projects/scratch/datanet-api and the
copies of its datanet_api vendored into other repos): a LIVE run needs a
matching successful dry run from the last 24 hours. A technical check, not an
approval gate — it never asks anyone, it only makes sure the exact command was
dry-run first.

- Only DRY_RUN=true (any case) is a dry run: every write script parses
  `os.environ.get("DRY_RUN", "false").lower() == "true"`, so DRY_RUN=1 or yes
  run LIVE, and the guard says so.
- PreToolUse: a dry run passes; a live run passes only when a stamp for the
  same script, arguments and argument-file contents exists, else it is denied
  (permissionDecision "deny", exit 0). `env -u DRY_RUN`, `env -i`, `unset
  DRY_RUN` and `export -n DRY_RUN` after a DRY_RUN=true clear it: LIVE.
- PostToolUse (the command succeeded): a dry run records its stamp in
  ~/.claude/guards/datanet-dryruns.jsonl.

The stamp key is sha256(script name, arguments, sha256 of every argument that
names an existing file), so editing the SQL or payload file after the dry run
needs a new one. The parser follows what Claude actually types: leading
VAR=…, `env`, `export DRY_RUN=…` earlier on the line, `cd <dir> &&`, prefix
commands (timeout, nohup, time, nice, stdbuf, sudo, uv/poetry run), python
options (-u, -X dev, -W x, -m <script>), shell keywords (if/then/do …),
subshells and $( ), and `bash -c '…'`. A command that names a write script in
any other way (xargs, a script file, python -c, python reading its program from
stdin, a pipe, <( ) or a variable, PYTHONSTARTUP, an unknown wrapper) is
refused with the plain form to use: its DRY_RUN mode can't be known. Reading or
editing a script (cat, grep, sed, git, an editor …) passes. Fails open on its
own errors."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # hooks/guards: common.py

COMPONENT = "datanet_guard"
WRITE_SCRIPTS = ("create_pipeline", "create_andes_load", "run_job", "reschedule_jobs",
                 "deschedule_jobs", "replace_table", "touch_profile", "update_profile_sql",
                 "create_klaxon_alarm", "manage_klaxon_alarm", "create_sim_ticket")
WINDOW = 24 * 3600
KEEP = 7 * 86400
FILE_CAP = 20_000_000
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SCRIPT_RX = re.compile(r"(?:^|[/\s'\"<=(`])(" + "|".join(WRITE_SCRIPTS) + r")\.py\b")
# A program python reads from stdin, a pipe or a substitution, not a named file.
NOT_A_FILE = re.compile(r"-$|<|/dev/stdin$|/dev/fd/|/proc/self/fd/|.*[$*?\[`]")
MODULE_RX = re.compile(r"-m[ \t]*(?:" + "|".join(WRITE_SCRIPTS) + r")\b")      # python3 -m run_job
FD_DUP = re.compile(r"\d*>&\d+")                 # 2>&1: dropped, its & is no separator
BOTH = re.compile(r"&>>?")                         # &> file: a plain redirect
REDIRECT = re.compile(r"^\d*(?:>>?|<)")
PYTHON = re.compile(r"(?:.*/)?(?:python(?:3(?:\.\d+)?)?|py)")
SHELLS = ("bash", "sh", "zsh", "dash", "ksh")
RUNNERS = ("uv", "poetry", "pipenv", "pdm", "rye", "hatch")
# Shell words in front of a command: `then python3 x.py`, `do …`, `! …`, `{ …`.
KEYWORDS = ("if", "then", "else", "elif", "do", "while", "until", "!", "{", "}")
# Segments that never run their words: `for f in run_job.py`, `case … in`.
NO_COMMAND = ("for", "case", "select", "function", "in", "done", "fi", "esac")
# Prefix commands that run the rest of the line, with their options that take a value.
WRAPPERS = {"nohup": (), "command": (), "builtin": (), "exec": ("-a",), "time": ("-f", "-o"),
            "nice": ("-n", "--adjustment"), "stdbuf": ("-i", "-o", "-e"),
            "ionice": ("-c", "-n", "-p", "-P", "-u"), "chronic": (), "unbuffer": (),
            "sudo": ("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U"),
            "timeout": ("-s", "--signal", "-k", "--kill-after")}
PY_VALUE_OPTS = ("-X", "-W", "--check-hash-based-pycs")
# python -m <module> that only reads a file named on its command line.
SAFE_MODULES = ("py_compile", "compileall", "pyflakes", "flake8", "pylint", "mypy", "black",
                "ruff", "isort", "pydoc", "tokenize", "ast", "dis", "tabnanny")
# Commands that read, edit or version a script without running it.
NON_EXEC = frozenset("""
cat less more head tail grep egrep fgrep rg ag ack wc ls stat file diff cmp git vim vi nvim
nano emacs code subl sed awk cut sort uniq tr nl bat view tree du cp mv rm ln chmod chown touch
mkdir find xxd od hexdump sha256sum sha1sum md5sum shasum echo printf test [ realpath readlink
basename dirname pygmentize black ruff flake8 pylint mypy isort pyflakes autopep8 yapf jq yq
open xdg-open tar zip unzip gzip rsync scp""".split())


class Unparseable(Exception):
    """A command names a write script in a form whose DRY_RUN mode can't be known."""


def _stamps_path(common) -> Path:
    return common.home() / "datanet-dryruns.jsonl"


def _segments(command: str):
    """Simple commands of a shell line, as token lists (operators, parentheses
    and $( ) dropped, so subshells and substitutions count as commands)."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lex.whitespace_split = True
    lex.commenters = ""
    seg, out = [], []
    for tok in lex:
        if tok and set(tok) <= set(";&|()"):
            if seg:
                out.append(seg)
            seg = []
        elif tok.endswith("$") and len(tok) > 1 and not seg:
            seg.append(tok[:-1])                    # out=$(…): keep the assignment
        else:
            seg.append(tok)
    if seg:
        out.append(seg)
    return out


def _mentions(tokens) -> bool:
    return any(SCRIPT_RX.search(t) for t in tokens)


def _skip_options(rest, i, value_opts):
    while i < len(rest) and rest[i].startswith("-") and rest[i] != "--":
        i += 2 if rest[i] in value_opts else 1
    return i + (1 if i < len(rest) and rest[i] == "--" else 0)


def _strip_prefix(rest, env):
    """Drop prefix commands and `env VAR=…` in front of the real command; VAR=…
    assignments found on the way go into env."""
    i = 0
    while i < len(rest):
        word = os.path.basename(rest[i])
        if ASSIGN.match(rest[i]):
            k, v = rest[i].split("=", 1)
            env[k] = v
            i += 1
        elif word == "env":
            i += 1
            while i < len(rest) and rest[i].startswith("-"):
                opt = rest[i]
                if opt in ("-S", "--split-string") or opt.startswith("-S"):
                    raise Unparseable("env -S")
                if opt in ("-", "-i", "--ignore-environment"):
                    env.clear()
                    env["DRY_RUN"] = None           # cleared: the script runs LIVE
                elif opt in ("-u", "--unset") and i + 1 < len(rest):
                    if rest[i + 1] == "DRY_RUN":
                        env["DRY_RUN"] = None
                elif opt.startswith(("--unset=", "-u")) and opt.split("=", 1)[-1].lstrip("-u") == "DRY_RUN":
                    env["DRY_RUN"] = None
                i += 2 if opt in ("-u", "--unset", "-C", "--chdir") else 1
        elif word in WRAPPERS:
            i = _skip_options(rest, i + 1, WRAPPERS[word])
            if word == "timeout" and i < len(rest):
                i += 1                              # the duration
        elif word in RUNNERS and i + 1 < len(rest) and rest[i + 1] == "run":
            j = i + 2                               # uv run [opts] python x.py / uv run x.py
            while j < len(rest) and not (PYTHON.fullmatch(rest[j]) or rest[j].endswith(".py")):
                j += 1
            if j == len(rest):
                raise Unparseable(f"{word} run")
            i = j
        else:
            break
    return rest[i:]


def _calls(command: str, cwd: str, env_in=None, depth=0):
    """[(script, args, cwd, dry_run_value)] for each write-script invocation.
    Raises Unparseable (or ValueError, for bad quoting) when a write script is
    named in a form this can't follow."""
    calls = []
    exported = dict(env_in or {})
    line = BOTH.sub(" > ", FD_DUP.sub(" ", command.replace("\\\n", " ")))
    for seg in _segments(line.replace("\n", " ; ")):
        if seg[0] in NO_COMMAND:
            continue
        while seg and seg[0] in KEYWORDS:
            seg = seg[1:]
        env = {}
        rest = _strip_prefix(seg, env)
        if not rest:
            continue            # a bare `DRY_RUN=true;` is not exported: the script runs LIVE
        word = os.path.basename(rest[0])
        if word == "export":
            unexport = "-n" in rest[1:]
            for a in rest[1:]:
                if ASSIGN.match(a):
                    k, v = a.split("=", 1)
                    exported[k] = None if unexport else v
                elif unexport and a == "DRY_RUN":
                    exported["DRY_RUN"] = None
            continue
        if word == "unset":
            if "DRY_RUN" in rest[1:] and "-f" not in rest[1:]:
                exported["DRY_RUN"] = None          # unset after set: the script runs LIVE
            continue
        if word == "cd":
            if len(rest) > 1:
                cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(rest[1])))
            continue
        scope = dict(exported, **env)
        if PYTHON.fullmatch(rest[0]) and SCRIPT_RX.search(str(scope.get("PYTHONSTARTUP") or "")):
            raise Unparseable("PYTHONSTARTUP")
        if word in SHELLS or word == "eval":
            inner = None
            if word == "eval":
                inner = " ".join(rest[1:])
            else:
                for j, t in enumerate(rest[1:], 1):
                    if not t.startswith("-"):
                        break
                    if "c" in t.lstrip("-") and not t.startswith("--") and j + 1 < len(rest):
                        inner = rest[j + 1]
                        break
            if inner is not None and depth < 3:
                calls += _calls(inner, cwd, scope, depth + 1)
            elif _mentions(rest):
                raise Unparseable(word)
            continue
        call = _python_call(rest, cwd, command)
        if call is None:
            if _mentions(rest) and word not in NON_EXEC:
                raise Unparseable(word)
            continue
        if call:
            script, args = call
            dry = scope.get("DRY_RUN", os.environ.get("DRY_RUN"))
            calls.append((script, _args(args), cwd, dry))
    return calls


def _python_call(rest, cwd, line):
    """(script, args) when rest runs a write script; () when it surely doesn't;
    None when rest isn't a python or script invocation at all. Raises Unparseable
    when python reads its program from stdin, a pipe or a substitution
    (`python3 - < run_job.py`, `cat run_job.py | python3 -`, `python3 <(…)`)
    and the line names a write script."""
    word = rest[0]
    name = os.path.basename(word)
    if name.endswith(".py") and name[:-3] in WRITE_SCRIPTS:          # ./run_job.py (shebang)
        return (name[:-3], rest[1:]) if "datanet" in word + cwd else ()
    if not PYTHON.fullmatch(word):
        return None
    j = 1
    while j < len(rest):
        t = rest[j]
        if t == "-m" or (t.startswith("-m") and len(t) > 2):
            module = rest[j + 1] if t == "-m" and j + 1 < len(rest) else t[2:]
            after = rest[j + 2:] if t == "-m" else rest[j + 1:]
            if module in WRITE_SCRIPTS:
                return (module, after) if "datanet" in cwd else ()
            if module in SAFE_MODULES or not _mentions(after):
                return ()
            raise Unparseable(f"python -m {module}")
        if t == "-c":
            if j + 1 < len(rest) and any(s in rest[j + 1] for s in WRITE_SCRIPTS):
                raise Unparseable("python -c")
            return ()
        if t in PY_VALUE_OPTS:
            j += 2
        elif t.startswith("-") and t != "-":
            j += 1
        else:
            break
    if j >= len(rest) or NOT_A_FILE.match(rest[j]):
        if SCRIPT_RX.search(line):
            raise Unparseable("python reading its program from stdin or a substitution")
        return ()
    name = os.path.basename(rest[j])
    if not name.endswith(".py") or name[:-3] not in WRITE_SCRIPTS:
        return ()                                   # another script; a write script is only an argument
    if "datanet" not in rest[j] and "datanet" not in cwd:
        return ()
    return name[:-3], rest[j + 1:]


def _args(tokens):
    """Arguments without redirections (`> log`, `2>err`), which don't change the call."""
    out, skip = [], False
    for t in tokens:
        if skip:
            skip = False
            continue
        m = REDIRECT.match(t)
        if m:
            skip = m.end() == len(t)          # bare operator: the target is the next token
            continue
        out.append(t)
    return out


def _key(script: str, args, cwd: str) -> str:
    h = hashlib.sha256()
    h.update(json.dumps([script, args]).encode())
    for a in args:
        for cand in (a, a.split("=", 1)[1] if a.startswith("-") and "=" in a else None):
            if not cand:
                continue
            p = os.path.join(cwd, os.path.expanduser(cand))
            try:
                if os.path.isfile(p) and os.path.getsize(p) <= FILE_CAP:
                    h.update(cand.encode() + b"\0" + hashlib.sha256(Path(p).read_bytes()).digest())
            except OSError:
                pass
    return h.hexdigest()


def _stamped(common, key: str):
    try:
        lines = _stamps_path(common).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    now = time.time()
    for ln in reversed(lines):
        try:
            s = json.loads(ln)
        except ValueError:
            continue
        if s.get("key") == key and now - float(s.get("at", 0)) <= WINDOW:
            return s
    return None


def _stamp(common, script: str, key: str, sid: str) -> None:
    """Record a dry run. Several handlers can run for one command (each write
    script has its own `if` filter, and Claude Code runs them all when it can't
    tell which commands a line runs), so the read-modify-write is serialised by a
    lock and a stamp from the last minute for the same key is not repeated."""
    import fcntl
    path = _stamps_path(common)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        now = time.time()
        keep = []
        try:
            for ln in path.read_text(encoding="utf-8").splitlines():
                try:
                    s = json.loads(ln)
                    at = float(s.get("at", 0))
                except (ValueError, TypeError, AttributeError):
                    continue
                if s.get("key") == key and now - at < 60:
                    return                          # a parallel handler stamped it already
                if now - at <= KEEP:
                    keep.append(ln)
        except FileNotFoundError:
            pass
        keep.append(json.dumps({"at": now, "key": key, "script": script, "session": sid}))
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
        os.replace(tmp, path)


def _deny(common, reason: str) -> int:
    return common.decide("deny", reason)


def run(inp: dict, common) -> int:
    if inp.get("tool_name") != "Bash":
        return 0
    command = (inp.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not (SCRIPT_RX.search(command) or MODULE_RX.search(command)):
        return 0
    if common.disabled(COMPONENT, common.config()):
        return 0
    cwd = inp.get("cwd") or os.getcwd()
    event = inp.get("hook_event_name")
    try:
        calls = _calls(command, cwd)
    except (Unparseable, ValueError) as e:
        if event != "PreToolUse" or "datanet" not in command + cwd:
            return 0
        what = f"`{e}`" if isinstance(e, Unparseable) else "the quoting"
        return _deny(common, f"datanet_guard: this command runs or names a Datanet write script "
                             f"through {what}, which the guard can't follow, so its DRY_RUN mode "
                             f"is unknown. Run the script as a plain command (timeout, nohup, cd "
                             f"&& and `bash -c` are fine): `DRY_RUN=true python3 "
                             f"<path>/<script>.py <args>`, then the same line without DRY_RUN.")
    if not calls:
        return 0
    if event == "PostToolUse":
        sid = str(inp.get("session_id") or "")
        for script, args, ccwd, dry in calls:
            if str(dry).lower() == "true":
                _stamp(common, script, _key(script, args, ccwd), sid)
        return 0
    if event != "PreToolUse":
        return 0
    notes = []
    for script, args, ccwd, dry in calls:
        if str(dry).lower() == "true":
            continue
        trap = (f" Note: DRY_RUN={dry} runs LIVE in this toolkit; only DRY_RUN=true is a dry run."
                if dry not in (None, "") else "")
        s = _stamped(common, _key(script, args, ccwd))
        if not s:
            return _deny(common,
                         f"datanet_guard: {script}.py would run LIVE with no matching dry run in "
                         f"the last 24 h.{trap} Run the identical command with DRY_RUN=true first "
                         f"(same arguments, same files), check its output, then run it live. "
                         f"Editing an argument file after the dry run needs a new one.")
        when = time.strftime("%H:%MZ", time.gmtime(float(s["at"])))
        notes.append(f"{script}.py: matching dry run at {when}")
    if notes:
        common.emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                            "additionalContext": "datanet_guard: live run allowed ("
                                                                 + "; ".join(notes) + ")."}})
    return 0


def main() -> int:
    inp = None
    try:
        import common
    except Exception as e:
        print(f"guards/{COMPONENT}: cannot load hooks/guards/common.py ({type(e).__name__}); "
              f"not enforced", file=sys.stderr)
        return 0
    try:
        inp = common.read_payload()
        return run(inp, common) if inp is not None else 0
    except Exception as e:
        return common.fail_open(COMPONENT, e, inp)


if __name__ == "__main__":
    sys.exit(main())
