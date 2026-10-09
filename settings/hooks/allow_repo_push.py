#!/usr/bin/env python3
"""PermissionRequest hook: approve a plain push of one named repo to one URL,
so a standing "commit and push without asking" rule (the notes vault) keeps
working once the gates make every `git push` ask.

    allow_repo_push.py --repo PATH --url URL [--remote NAME] --branch NAME

Allows a Bash command only when it pushes and every part of it is one of
    cd <dir>                 absolute, ~/... or ./..., with no `..` in it
    git [-C <dir>]... add|commit|pull|push <arguments>        (writes)
    git [-C <dir>]... status|log|diff <arguments>             (reads)
    echo <words>
every git part runs in --repo (its top level, not a repo nested in it), and
every argument is on the list for its subcommand:
    add     paths of files inside the repo; no options, no directory (so not
            the top level either), no pathspec magic (`:/`) and no globs
    commit  -m/--message <text> and -q, nothing else and no pathspecs. Claude
            Code's `-m "$(cat <<'EOF' ... EOF )"` form counts as text, unless
            a line in it starts with the delimiter (see heredoc_body).
    pull    --rebase, --ff-only, -q, then [<remote> [<branch>]]
    push    -q, then [<remote> [<branch>|HEAD|HEAD:<branch>]]. HEAD and a
            bare push need HEAD on <branch>; a bare push also needs @{push} to
            be <remote>/<branch> and push.default not `matching`. Without a
            `:<branch>`, push.default=upstream needs branch.<branch>.merge to
            be refs/heads/<branch>, or git would send the branch there.
            Refused when remote.<remote>.push, .mirror or .receivepack is
            configured, when push.recurseSubmodules (or submodule.recurse)
            would push submodules too, and unless `git remote get-url --push
            --all <remote>` is exactly --url: that expands pushurl, insteadOf
            and pushInsteadOf, so redirecting the remote (set-url, a pushurl,
            a URL rewrite) stops the approval.
    status  anything
    log     --oneline, -<n>, -n <n>, --format=/--pretty= (no %G, which runs
            gpg), --stat and a few more display flags, revisions, paths
    diff    --stat, --shortstat, --numstat, --name-only, --name-status,
            --cached/--staged, revisions, paths
            For log and diff, every word that isn't a flag must read as a path
            inside the repo (revisions such as HEAD~1 and A..B do): `git diff
            <outside file> /dev/null` is an implicit --no-index diff that
            prints any file on the disk.
The parts are joined by `&&` or `;`. A `cd` must be followed by `&&` (a
failed cd then stops the rest), and after a `;` only reads may follow, so a
write never runs in a directory the hook didn't check. A git or echo part may
end in `2>&1` and pipe into `head` or `tail` with an optional -<n> or -n <n>:
`git push -q origin mainline 2>&1 | tail -2; git log --oneline -1` passes.

Short options are read the way git reads them (`-qam` is -q -a -m, so -a is
seen), and long options must be spelled out in full: git also takes unique
prefixes such as `--amen`, which this refuses. The command is read with a
small lexer that knows plain words, quotes, `&&`, `;`, `|` and `2>&1`, and
refuses everything else bash or zsh would expand or treat as syntax: `$`,
backticks, `||`, `&`, other redirections, newlines, globs and braces outside
quotes, comments.

Out of scope: git config or hooks that run programs (core.sshCommand, a
pre-push hook, filters). Any git command the gates don't ask for, such as
`git fetch` or `git status`, would run those too.

Anything else gets no answer, so Claude Code asks as usual. Deny rules still
win: a force push never gets here. On an internal error it answers nothing
(fail open to the normal prompt), writes to stderr and appends to
~/.claude/gates/allow_repo_push.log.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LOG = Path(os.environ.get("CLAUDE_GATES_HOME") or Path.home() / ".claude" / "gates") \
    / "allow_repo_push.log"
# Unquoted characters a word may hold besides letters and digits: none of them
# is special to bash or zsh mid-word. `=` can't come first (zsh: `=cmd` expands).
PLAIN = set("_./:@%+,=-")
HEREDOC = re.compile(r"\"\$\(cat <<'([A-Za-z_][A-Za-z0-9_]*)'\n")
FALSE = ("false", "no", "off", "0")
WRITES = ("add", "commit", "pull", "push")
READS = ("status", "log", "diff")
LOG_FLAGS = {"--oneline", "--stat", "--shortstat", "--numstat", "--name-only", "--name-status",
             "--decorate", "--no-decorate", "--graph", "--no-color", "--reverse",
             "--abbrev-commit", "--no-merges", "--first-parent"}
DIFF_FLAGS = {"--stat", "--shortstat", "--numstat", "--name-only", "--name-status",
              "--cached", "--staged", "--no-color"}


class NoDecision(Exception):
    """The command isn't one this hook approves: let Claude Code ask."""


class Word(str):
    """One shell word after quote removal. `heredoc` marks a word with
    heredoc text in it, which is accepted only as a commit message."""
    heredoc = False


class Op(str):
    """An operator: `&&`, `;`, `|` or `2>&1`."""


class Part:
    """One simple command of the chain: its words, a trailing `2>&1`, the
    `head`/`tail` it pipes into, and the operator after it (None at the end)."""

    def __init__(self, words: list, stderr_to_stdout: bool, pipe_to, then):
        self.words, self.stderr_to_stdout = words, stderr_to_stdout
        self.pipe_to, self.then = pipe_to, then


def git(args: list[str], cwd: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=3,
                       env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    if r.returncode != 0:
        raise NoDecision(f"git {args[0]} failed in {cwd}")
    return r.stdout.strip()


# --- reading the command -----------------------------------------------------

def lex(command: str) -> list:
    """The command as Words and Ops, read the way bash and zsh read it;
    NoDecision for anything outside that small subset."""
    tokens: list = []
    buf: list[str] = []
    started = heredoc = False
    i, n = 0, len(command)

    def flush():
        nonlocal buf, started, heredoc
        if started:
            w = Word("".join(buf))
            w.heredoc = heredoc
            tokens.append(w)
        buf, started, heredoc = [], False, False

    while i < n:
        c = command[i]
        if c in " \t":
            flush()
            i += 1
        elif command.startswith("&&", i):
            flush()
            tokens.append(Op("&&"))
            i += 2
        elif c == ";" and not command.startswith(";;", i):
            flush()
            tokens.append(Op(";"))
            i += 1
        elif c == "|" and command[i + 1:i + 2] not in ("|", "&"):  # not || or |&
            flush()
            tokens.append(Op("|"))
            i += 1
        elif (not started and command.startswith("2>&1", i)
              and (command[i + 4:i + 5] in ("", " ", "\t", "|", ";")
                   or command.startswith("&&", i + 4))):
            # Only as a word of its own: `x2>&1` and `"2">&1` mean other things.
            tokens.append(Op("2>&1"))
            i += 4
        elif c == "'":
            j = command.find("'", i + 1)
            if j < 0:
                raise NoDecision("unbalanced quote")
            buf.append(command[i + 1:j])
            started, i = True, j + 1
        elif c == '"':
            m = HEREDOC.match(command, i)
            if m:
                body, i = heredoc_body(command, m)
                buf.append(body)
                started = heredoc = True
            else:
                i = double_quoted(command, i + 1, buf)
                started = True
        elif c == "\\" and command[i + 1:i + 2] not in ("", "\n", "\r"):
            buf.append(command[i + 1])
            started, i = True, i + 2
        elif c == "~" and not started and command[i + 1:i + 2] in ("", "/", " ", "\t"):
            buf.append(os.path.expanduser("~"))
            started, i = True, i + 1
        elif c.isalnum() or (c in PLAIN and (c != "=" or any(buf))):  # zsh: ''=x expands too
            buf.append(c)
            started, i = True, i + 1
        else:
            raise NoDecision(f"shell syntax {c!r}")
    flush()
    return tokens


def split_parts(tokens: list) -> list[Part]:
    """Group tokens into Parts: `a [2>&1] [| b] (&&|;) ...`."""
    parts: list[Part] = []
    seg: list = []
    for tok in tokens + [None]:
        if tok is not None and not (isinstance(tok, Op) and tok in ("&&", ";")):
            seg.append(tok)
            continue
        if not seg:
            raise NoDecision("empty command part")
        pipe_to = None
        pipes = [k for k, t in enumerate(seg) if isinstance(t, Op) and t == "|"]  # not '|'
        if pipes:
            seg, pipe_to = seg[:pipes[0]], seg[pipes[0] + 1:]
            if not pipe_to or any(isinstance(t, Op) for t in pipe_to):
                raise NoDecision("a pipe into more than one head or tail")
            check_filter(pipe_to)
        redirect = bool(seg) and isinstance(seg[-1], Op) and seg[-1] == "2>&1"
        if redirect:
            seg = seg[:-1]
        if not seg or any(isinstance(t, Op) for t in seg):
            raise NoDecision("2>&1 or | in the wrong place")
        parts.append(Part(seg, redirect, pipe_to, str(tok) if tok is not None else None))
        seg = []
    return parts


def check_filter(words: list) -> None:
    """`head` or `tail`, alone or with -<n>, -n<n> or -n <n>: no file, no -f."""
    args = [plain(w) for w in words]
    if args[0] not in ("head", "tail"):
        raise NoDecision(f"a pipe into {args[0]}")
    rest = args[1:]
    if not (rest == [] or (len(rest) == 1 and re.fullmatch(r"-n?\d+", rest[0]))
            or (len(rest) == 2 and rest[0] == "-n" and rest[1].isdigit())):
        raise NoDecision(f"{args[0]} arguments")


def double_quoted(command: str, i: int, buf: list[str]) -> int:
    """Read a "..." string from just after its opening quote into buf; return
    the index after the closing quote. `$` and backticks would expand."""
    n = len(command)
    while i < n:
        c = command[i]
        if c == '"':
            return i + 1
        if c in "$`":
            raise NoDecision("expansion inside double quotes")
        if c == "\\" and i + 1 < n and command[i + 1] in '"\\$`\n':
            if command[i + 1] != "\n":  # backslash-newline joins lines
                buf.append(command[i + 1])
            i += 2
            continue
        buf.append(c)
        i += 1
    raise NoDecision("unbalanced quote")


def heredoc_body(command: str, m: re.Match) -> tuple[str, int]:
    """`"$(cat <<'EOF'` newline, body, a line `EOF`, then `)"`. The quoted
    delimiter turns expansion off, so the body is literal. As in the shell it
    ends at the first line that is exactly the delimiter, and $(...) drops the
    trailing newlines. Returns (body, index after the closing quote)."""
    delim, start = m.group(1), m.end()
    pos = start
    while True:
        nl = command.find("\n", pos)
        if nl < 0:
            raise NoDecision("unterminated heredoc")
        line = command[pos:nl]
        if line == delim:
            break
        if line.startswith(delim):
            # bash 5.2 ends the heredoc and the $(...) at a `)` on such a line
            # (`EOF)" && cmd && echo "` runs cmd); zsh doesn't.
            raise NoDecision("a heredoc line that starts with the delimiter")
        pos = nl + 1
    if not command.startswith(')"', nl + 1):
        raise NoDecision("heredoc not closed by )\"")
    return command[start:pos].rstrip("\n"), nl + 3


def plain(word: Word) -> Word:
    if word.heredoc:
        raise NoDecision("heredoc text outside a commit message")
    return word


# --- checking each part --------------------------------------------------------

def cd(here: str, args: list[Word]) -> str:
    """Where `cd <dir>` leaves the shell. A bare name could go anywhere on
    CDPATH, and `..` after a symlink differs between logical and physical cd."""
    if len(args) != 1:
        raise NoDecision("cd form")
    d = plain(args[0])
    if not (d.startswith(("/", "./")) or d == ".") or ".." in d.split("/"):
        raise NoDecision("cd to a bare name or through ..")
    return os.path.normpath(os.path.join(here, d))


def check_add(args: list[Word], where: str, repo: str) -> None:
    paths, options = [], True
    for a in map(plain, args):
        if options and a == "--":
            options = False
        elif options and a.startswith("-"):
            raise NoDecision(f"git add option {a}")
        else:
            paths.append(a)
    if not paths:
        raise NoDecision("git add without a path")
    for p in paths:
        if not p or p.startswith(":") or set(p) & set("*?[\\"):
            raise NoDecision("pathspec magic or a glob")  # `:/` and `*` add everything
        full = os.path.normpath(os.path.join(where, p))
        if not inside(p, where, repo) or os.path.isdir(full):  # the top level is a directory
            raise NoDecision("a directory, or a path outside the repo")


def check_commit(args: list[Word]) -> None:
    """-m/--message and -q only, read as git's option parser reads them:
    `-qam x` is -q -a -m x, `-mq` is the message "q"."""
    i, messages = 0, 0
    while i < len(args):
        a = args[i]
        i += 1
        if a.startswith("--message="):
            messages += 1
        elif plain(a) == "--message":
            if i >= len(args):
                raise NoDecision("--message without a value")
            i, messages = i + 1, messages + 1
        elif a == "--quiet":
            pass
        elif a.startswith("-") and not a.startswith("--") and len(a) > 1:
            for k, ch in enumerate(a[1:], start=2):
                if ch == "q":
                    continue
                if ch != "m":
                    raise NoDecision(f"git commit option -{ch}")
                if k == len(a):  # the value is the next word
                    if i >= len(args):
                        raise NoDecision("-m without a value")
                    i += 1
                messages += 1
                break
        else:
            raise NoDecision(f"git commit option or pathspec {a}")  # -a, --amen, -F, `.`
    if not messages:
        raise NoDecision("no -m message (an editor would open)")


def check_pull(args: list[Word], remote: str, branch: str) -> None:
    pos = []
    for a in map(plain, args):
        if a in ("--rebase", "--ff-only", "-q", "--quiet"):
            continue
        if a.startswith("-"):
            raise NoDecision(f"git pull option {a}")
        pos.append(a)
    if pos not in ([], [remote], [remote, branch]):
        raise NoDecision("pull from elsewhere")


def inside(word: str, where: str, repo: str) -> bool:
    """Whether git reads word as a path inside the repo, as its own check
    does (abspath_part_inside_repo in setup.c): made absolute from where and
    `..` resolved as text, it is under the top level, or, for an absolute
    word, a leading part of it resolves (symlinks followed) to the top level.
    So ~/vault/x passes when ~ is a symlink. A relative word gets only the
    text check, as in git."""
    full = os.path.normpath(os.path.join(where, word))
    if full == repo or full.startswith(repo + os.sep):
        return True
    if not os.path.isabs(word):
        return False
    parts = full.split(os.sep)
    return any(os.path.realpath(os.sep.join(parts[:k])) == repo for k in range(2, len(parts) + 1))


def check_read(sub: str, args: list[Word], where: str, repo: str) -> None:
    """status takes anything; log and diff only display flags, revisions and
    paths in the repo (git log --output=<file> would write a file). Given two
    paths and one outside the work tree, `git diff` turns into `diff
    --no-index` and prints any file on the disk, so every word that isn't a
    flag, before or after `--`, must read as a path inside the repo. A
    revision such as HEAD~1, A..B or origin/main does."""
    words = list(map(plain, args))
    if sub == "status":
        return
    flags = LOG_FLAGS if sub == "log" else DIFF_FLAGS
    i, paths = 0, False
    while i < len(words):
        a = words[i]
        i += 1
        if paths or not a.startswith("-"):
            if not inside(a, where, repo):
                raise NoDecision(f"git {sub} of a path outside the repo")
            continue  # a revision, or a path
        if a == "--":
            paths = True
        elif a in flags:
            pass
        elif sub == "log" and re.fullmatch(r"-\d+|-n\d+|--max-count=\d+", a):
            pass
        elif sub == "log" and a == "-n" and i < len(words) and words[i].isdigit():
            i += 1
        elif sub == "log" and a.startswith(("--format=", "--pretty=")) and "%G" not in a:
            pass  # %G? and friends run gpg
        else:
            raise NoDecision(f"git {sub} option {a}")


def push_config(where: str) -> dict:
    """git config as {key: [values]}, the last one counting for a single-valued
    key; a key without a value gets None."""
    r = subprocess.run(["git", "config", "-z", "--list"], cwd=where, capture_output=True,
                       text=True, timeout=3)
    if r.returncode != 0:
        raise NoDecision("git config failed")
    cfg: dict = {}
    for entry in r.stdout.split("\0"):
        if entry:
            key, sep, value = entry.partition("\n")
            cfg.setdefault(key, []).append(value if sep else None)
    return cfg


def last(cfg: dict, key: str, default=None):
    return cfg[key][-1] if key in cfg else default


def check_push(args: list[Word], where: str, remote: str, branch: str, url: str) -> None:
    pos = []
    for a in map(plain, args):
        if a in ("-q", "--quiet"):
            continue
        if a.startswith("-"):
            raise NoDecision(f"git push option {a}")
        pos.append(a)
    if len(pos) > 2 or pos[:1] not in ([], [remote]):
        raise NoDecision("another remote")
    cfg = push_config(where)
    mirror = last(cfg, f"remote.{remote}.mirror", "false")
    if f"remote.{remote}.push" in cfg or mirror is None or mirror.lower() not in FALSE:
        raise NoDecision("remote push refspecs or mirror configured")
    # receivepack names the program the remote end runs; only a push uses it.
    if f"remote.{remote}.receivepack" in cfg:
        raise NoDecision("remote receivepack configured")
    # on-demand or only would push submodules too, to their own URLs. Unset,
    # it follows submodule.recurse (a key with no value is true).
    if "push.recursesubmodules" in cfg:
        recurse = last(cfg, "push.recursesubmodules")
    else:
        on = last(cfg, "submodule.recurse", "false")
        recurse = "on-demand" if on is None or on.lower() not in ("",) + FALSE else "no"
    if recurse is None or recurse.lower() not in ("no", "check") + FALSE:
        raise NoDecision("push.recurseSubmodules pushes submodules")
    # Where the push really goes, after pushurl, insteadOf and pushInsteadOf;
    # more than one URL means a push to each.
    if git(["remote", "get-url", "--push", "--all", remote], where).splitlines() != [url]:
        raise NoDecision("the remote's push URL is not --url")
    # Without `:<dst>`, push.default=upstream (or its old name tracking) sends
    # <branch> to branch.<branch>.merge, which needn't be refs/heads/<branch>.
    upstream = (last(cfg, "push.default") or "").lower() in ("upstream", "tracking")
    merge = cfg.get(f"branch.{branch}.merge", [f"refs/heads/{branch}"])
    if upstream and merge != [f"refs/heads/{branch}"] and not (len(pos) == 2 and ":" in pos[1]):
        raise NoDecision("push.default=upstream maps the branch elsewhere")
    if len(pos) == 2:
        src, colon, dst = pos[1].partition(":")
        mine = (branch, f"refs/heads/{branch}")
        if src not in mine + ("HEAD",) or (colon and dst not in mine):
            raise NoDecision("another refspec")
        if src != "HEAD":
            return
    # HEAD, `git push` or `git push <remote>`: what goes depends on HEAD.
    if git(["symbolic-ref", "--short", "-q", "HEAD"], where) != branch:
        raise NoDecision("HEAD is not the branch")
    if len(pos) == 2:
        return
    if (last(cfg, "push.default") or "").lower() == "matching":
        raise NoDecision("push.default=matching pushes every branch")
    if git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{push}"],
           where) != f"{remote}/{branch}":
        raise NoDecision("@{push} is not remote/branch")


def decide(payload: dict, repo: str, remote: str, branch: str, url: str) -> bool:
    if payload.get("tool_name") != "Bash":
        return False
    command = (payload.get("tool_input") or {}).get("command")
    cwd = payload.get("cwd")
    if not isinstance(command, str) or not isinstance(cwd, str) or not os.path.isabs(cwd):
        return False
    try:
        here, pushes, toplevel, after_semicolon = os.path.normpath(cwd), 0, {}, False
        for part in split_parts(lex(command)):
            argv = part.words
            name = plain(argv[0])
            if name == "cd":
                # A failed cd must stop what follows, or a write would run in
                # the directory the shell was in before.
                if part.stderr_to_stdout or part.pipe_to or part.then != "&&":
                    raise NoDecision("cd not followed by &&, or piped")
                here = cd(here, argv[1:])
                continue
            if name == "echo":
                list(map(plain, argv[1:]))
            elif name != "git":
                raise NoDecision(f"{name} is not git")
            else:
                where, i = os.path.realpath(here), 1
                while i + 1 < len(argv) and plain(argv[i]) == "-C":
                    where = os.path.realpath(os.path.join(where, plain(argv[i + 1])))
                    i += 2
                sub, args = (plain(argv[i]), argv[i + 1:]) if i < len(argv) else ("", [])
                if sub not in WRITES + READS:
                    raise NoDecision("git option or subcommand")
                if sub in WRITES and after_semicolon:
                    raise NoDecision("a write after ;")  # a cd before it may have failed
                if where not in toplevel:
                    toplevel[where] = os.path.realpath(git(["rev-parse", "--show-toplevel"],
                                                           where))
                if toplevel[where] != repo:
                    raise NoDecision("another repository")
                if sub == "add":
                    check_add(args, where, repo)
                elif sub == "commit":
                    check_commit(args)
                elif sub == "pull":
                    check_pull(args, remote, branch)
                elif sub == "push":
                    check_push(args, where, remote, branch, url)
                    pushes += 1
                else:
                    check_read(sub, args, where, repo)
            after_semicolon = after_semicolon or part.then == ";"
        return pushes > 0
    except (NoDecision, subprocess.TimeoutExpired, OSError):
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="allow_repo_push.py")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--url", required=True,
                    help="the only push URL approved (git remote get-url --push)")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--branch", required=True)
    a = ap.parse_args(argv)
    try:
        payload = json.loads(sys.stdin.read() or "null")
        if not isinstance(payload, dict):
            return 0
        if decide(payload, os.path.realpath(os.path.expanduser(a.repo)), a.remote, a.branch,
                  a.url):
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"}}}))
        return 0
    except Exception as e:  # fail open: no answer means the normal prompt
        msg = f"allow_repo_push: internal error ({type(e).__name__}: {e}); not answering"
        print(msg, file=sys.stderr)
        try:
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
