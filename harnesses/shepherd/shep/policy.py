"""Pure decision rules — no I/O, so every case is a table-driven test.

decide_edit(): may this session edit this file?  classify_bash(): is this shell
command one of the git operations the rules forbid?"""

from __future__ import annotations

import fnmatch
import shlex
from dataclasses import dataclass, field
from typing import Optional


def owns_match(rel: str, owns: list[str]) -> Optional[str]:
    """The `owns` entry covering repo-relative path `rel`, if any. Entries are
    paths (a directory covers everything under it) or fnmatch globs."""
    for o in owns or []:
        o2 = o.strip().lstrip("./") if o.strip() not in (".", "./") else ""
        if not o2:
            return o
        base = o2.rstrip("/")
        if rel == base or rel.startswith(base + "/") or fnmatch.fnmatch(rel, o2):
            return o
    return None


def owns_overlap(a: list[str], b: list[str]) -> list[tuple[str, str]]:
    """Pairs of entries from a and b that cover a common path."""
    pairs = []
    for x in a or []:
        for y in b or []:
            if owns_match(x.rstrip("/"), [y]) or owns_match(y.rstrip("/"), [x]):
                pairs.append((x, y))
    return pairs


@dataclass
class Decision:
    deny: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


def decide_edit(*, mode: str, rel: str, branch: Optional[str], protected: bool,
                here: Optional[dict], here_owner_live: bool, me_sid: Optional[str],
                others: list[dict], recent_by_others: list[dict], cmd_hint: str) -> Decision:
    """`here`: the track whose worktree holds the file (None if untracked).
    `others`: other live-work tracks, each with `name`, `owns`, `changed` (files on
    its branch), `owner` (session name). `recent_by_others`: activity entries on
    this file by other sessions."""
    d = Decision()
    enforce = mode == "enforce"
    if protected:
        msg = (f"`{branch}` is a protected branch in this repo. Start a track and edit "
               f"there: `{cmd_hint} start <track> --goal \"…\" --owns <paths>`, then "
               f"EnterWorktree(path=<printed path>).")
        if enforce:
            d.deny = "shepherd: " + msg
            return d
        d.warnings.append("shepherd (observe mode): " + msg)
    if here is not None:
        owner = (here.get("agent") or {}).get("session_id")
        if owner and owner != me_sid:
            if here_owner_live:
                if enforce:
                    d.deny = (f"shepherd: this worktree belongs to track `{here['name']}`, "
                              f"owned by another live session. Ask its owner instead: "
                              f"`{cmd_hint} log --to {here['name']} --type request \"…\"`.")
                    return d
                d.warnings.append(f"shepherd: worktree of track `{here['name']}` is owned "
                                  f"by another live session.")
            else:
                d.warnings.append(f"shepherd: track `{here['name']}`'s owner session has "
                                  f"ended. Claim it before editing: `{cmd_hint} resume "
                                  f"{here['name']}`.")
        elif not owner:
            d.warnings.append(f"shepherd: track `{here['name']}` is unclaimed. Claim it: "
                              f"`{cmd_hint} resume {here['name']}`.")
    elif not protected and branch:
        d.warnings.append(f"shepherd: branch `{branch}` is not a track. Register it: "
                          f"`{cmd_hint} adopt --goal \"…\" --owns <paths>`.")
    for o in others:
        hit = owns_match(rel, o.get("owns") or [])
        if hit:
            d.warnings.append(f"shepherd: `{rel}` is owned by track `{o['name']}` "
                              f"({o.get('owner') or 'unknown session'}, owns `{hit}`). "
                              f"Stop and coordinate: `{cmd_hint} log --to {o['name']} "
                              f"--type request \"…\"`.")
        elif rel in (o.get("changed") or ()):
            d.warnings.append(f"shepherd: `{rel}` is also changed on track `{o['name']}` "
                              f"({o.get('owner') or 'unknown session'}); expect a merge "
                              f"conflict unless you coordinate.")
    names = sorted({e.get("who") or (e.get("sid") or "")[:8] for e in recent_by_others})
    if names:
        d.warnings.append(f"shepherd: `{rel}` was edited in the last 2 h by another "
                          f"session ({', '.join(names)}).")
    return d


# --- shell commands -------------------------------------------------------------

_SEPARATORS = {"&&", "||", ";", "|", "&", "\n", "(", ")", ";;", "|&"}
_COMMIT_VALUE_FLAGS = set("mFCctS")    # short flags of `git commit` that take a value
_GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                   "--super-prefix", "--config-env"}


@dataclass
class GitCall:
    cwd: Optional[str]          # effective directory (from `cd` / `git -C`), None = unchanged
    sub: str
    args: list[str]
    config: list[str]           # -c key=value pairs
    env: list[str]              # VAR=value prefixes


def _segments(cmd: str) -> list[list[str]]:
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        toks = list(lex)
    except ValueError:             # unbalanced quotes: fall back to a crude split
        toks = cmd.replace(";", " ; ").replace("&&", " && ").split()
    segs, cur = [], []
    for t in toks:
        if t in _SEPARATORS or (set(t) <= set("&|;()") and t):
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def git_calls(cmd: str) -> tuple[list[GitCall], list[str]]:
    """Git invocations in a shell command line, and every VAR=value assignment."""
    calls, assigns, cwd = [], [], None
    for seg in _segments(cmd):
        i, env = 0, []
        while i < len(seg) and "=" in seg[i] and not seg[i].startswith("-") and \
                seg[i].split("=", 1)[0].replace("_", "").isalnum():
            env.append(seg[i])
            i += 1
        assigns += env
        if i < len(seg) and seg[i] == "export":
            assigns += [a for a in seg[i + 1:] if "=" in a] + seg[i + 1:]
            continue
        while i < len(seg) and seg[i] in ("sudo", "command", "env", "exec", "time", "nohup"):
            i += 1
            while i < len(seg) and "=" in seg[i] and not seg[i].startswith("-"):
                assigns.append(seg[i])
                i += 1
        if i >= len(seg):
            continue
        head = seg[i]
        if head in ("cd", "pushd") and i + 1 < len(seg):
            cwd = seg[i + 1] if not cwd or seg[i + 1].startswith("/") else f"{cwd}/{seg[i + 1]}"
            continue
        if head.rsplit("/", 1)[-1] != "git":
            continue
        j, gcwd, config = i + 1, cwd, []
        while j < len(seg) and seg[j].startswith("-"):
            opt = seg[j]
            name, eq, val = opt.partition("=")
            if name in _GIT_VALUE_OPTS and not eq:
                val = seg[j + 1] if j + 1 < len(seg) else ""
                j += 1
            if name == "-C":
                gcwd = val if not gcwd or val.startswith("/") else f"{gcwd}/{val}"
            elif name in ("-c", "--config-env"):
                config.append(val)
            j += 1
        if j < len(seg):
            calls.append(GitCall(gcwd, seg[j], seg[j + 1:], config, env))
    return calls, assigns


_LONG_VALUE_OPTS = {"--message", "--file", "--author", "--date", "--template", "--reuse-message",
                    "--reedit-message", "--fixup", "--squash", "--cleanup", "--trailer",
                    "--pathspec-from-file", "--chmod", "--push-option", "--repo", "--exec",
                    "--receive-pack", "--onto", "--strategy", "--strategy-option"}


def _short_flags(args: list[str], value_flags: set) -> set:
    """Single-letter flags in `args`, skipping option values (so a message like
    `-m "-a fix"` doesn't read as `-a`)."""
    flags, skip = set(), False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "--":
            break
        if a.startswith("--"):
            skip = a in _LONG_VALUE_OPTS
            continue
        if a.startswith("-") and len(a) > 1:
            for k, ch in enumerate(a[1:]):
                flags.add(ch)
                if ch in value_flags:
                    skip = k == len(a) - 2      # value is the next token
                    break
    return flags


def classify_git(call: GitCall, protected: list[str], current_branch: Optional[str]) -> Optional[str]:
    """Why this git call breaks the rules, or None."""
    a, sub = call.args, call.sub
    for c in call.config:
        if c.lower().startswith("core.hookspath"):
            return "overriding core.hooksPath bypasses the repo's shepherd hooks"
    if "--no-verify" in a:
        return "--no-verify skips the shepherd git hooks"
    if sub == "add":
        bulk = {"-A", "--all", "--no-ignore-removal", ".", "./", ":/", ":/.", "*", "-u",
                "--update"}
        if bulk & set(a) or "A" in _short_flags(a, set()) or "u" in _short_flags(a, set()):
            return "stage files by name (`git add <paths>`), never `git add -A`, `-u` or `.`"
    if sub == "commit":
        flags = _short_flags(a, _COMMIT_VALUE_FLAGS)
        if "a" in flags or "--all" in a:
            return "`git commit -a` commits other sessions' changes; stage by name instead"
        if "n" in flags:
            return "`git commit -n` (--no-verify) skips the shepherd git hooks"
    if sub == "stash" and (not a or a[0] not in ("list", "show")):
        return "`git stash` can take other sessions' uncommitted work; commit WIP on your own branch instead"
    if sub == "reset" and "--hard" in a:
        return "`git reset --hard` discards uncommitted work that may not be yours"
    if sub == "clean" and ("f" in _short_flags(a, set()) or "--force" in a):
        return "`git clean -f` deletes untracked files that may belong to another session"
    if sub == "checkout" and "--" in a and any(x in (".", "./", ":/") for x in a[a.index("--") + 1:]):
        return "`git checkout -- .` discards uncommitted work that may not be yours"
    if sub == "checkout" and a[:1] in (["."], ["./"]):
        return "`git checkout .` discards uncommitted work that may not be yours"
    if sub == "restore" and any(x in (".", "./", ":/") for x in a) and \
            not ("--staged" in a and "--worktree" not in a and "-W" not in a):
        return "`git restore .` discards uncommitted work that may not be yours"
    if sub == "config" and any(x.lower() == "core.hookspath" for x in a):
        return "changing core.hooksPath disables the shepherd hooks"
    if sub == "push":
        if "--all" in a or "--mirror" in a:
            return "`git push --all/--mirror` would push protected branches"
        pos = [x for x in a if not x.startswith("-")]
        refspecs = pos[1:] if pos else []
        for spec in refspecs:
            dst = spec.lstrip("+").split(":")[-1]
            dst = dst[len("refs/heads/"):] if dst.startswith("refs/heads/") else dst
            if dst in protected or (dst == "HEAD" and current_branch in protected):
                return f"`{dst}` is protected; it only changes through a reviewed PR (`shepherd pr`)"
        if not refspecs and current_branch in protected:
            return (f"`{current_branch}` is protected; it only changes through a reviewed "
                    f"PR (`shepherd pr`)")
    return None


def classify_bash(cmd: str, protected: list[str], current_branch: Optional[str]) -> Optional[str]:
    if "SHEPHERD_ALLOW_MAIN" in cmd:
        return "SHEPHERD_ALLOW_MAIN is for the user, not for agents"
    calls, _ = git_calls(cmd)
    for c in calls:
        why = classify_git(c, protected, current_branch)
        if why:
            return why
    return None
