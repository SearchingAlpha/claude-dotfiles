#!/usr/bin/env python3
"""Install the verify-gate Stop hook into a repo.

    install_gate.py REPO [--apply] [--mode fast|full] [--timeout S] [--max-failures N]
                         [--watch PATH]... [--check CMD] [--no-baseline] [--check-template]
    install_gate.py REPO --remove [--apply]

Copies stop_gate.py to REPO/.claude/hooks/ and merges two handlers into
REPO/.claude/settings.json: Stop (the gate) and SessionStart (the baseline tree). REPO is the
folder Claude Code is started in: a git work tree, or a subproject folder inside one. Dry run
by default: prints the plan and a diff of settings.json; --apply writes. Re-running replaces
its own handlers in place, so it is idempotent and keeps every other hook and key. Exit status
is 0 on success and 1 on any error, after which nothing it wrote is left behind. Stdlib only.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import math
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
GATE_SRC = HERE / "stop_gate.py"
TEMPLATE = HERE.parent / "templates" / "check.sh"
HOOK_REL = ".claude/hooks/stop_gate.py"
SETTINGS_REL = ".claude/settings.json"
CHECK_REL = "scripts/check"
MARK = f'f="$CLAUDE_PROJECT_DIR/{HOOK_REL}"'  # how our own handlers start
BASELINE_SOURCES = "startup|resume|clear|fork"  # not compact: same session, same baseline


class InstallError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # usage errors exit 1 like every other error, not argparse's 2
        self.print_usage(sys.stderr)
        self.exit(1, f"install_gate: {message}\n")


def hook_command(args) -> str:
    """Shell form. A checkout without the hook file, or a machine without python3, allows the
    stop. Only the gate's JSON reply blocks: if python3 itself exits 2 (it can't open the
    file), the wrapper turns that into 1, a non-blocking hook error. -I keeps sibling hooks
    and PYTHON* variables out of the gate's imports."""
    flags = ["--mode", args.mode, "--timeout", f"{args.timeout:g}"]
    if args.max_failures != 3:
        flags += ["--max-failures", str(args.max_failures)]
    flags += [f"--watch={path}" for path in args.watch]  # one token: a value may start with -
    if args.check:
        flags.append(f"--check={args.check}")
    return (f'{MARK}; [ -r "$f" ] && command -v python3 >/dev/null || exit 0; '
            f'python3 -I "$f" {" ".join(shlex.quote(x) for x in flags)}; '
            f'c=$?; [ $c -eq 2 ] && c=1; exit $c')


def wanted_groups(args) -> dict:
    cmd = hook_command(args)
    groups = {"Stop": {"hooks": [{"type": "command", "command": cmd,
                                  "timeout": int(args.timeout) + 60,
                                  "statusMessage": "stop_gate: checking the tree"}]},
              "SessionStart": {"matcher": BASELINE_SOURCES,
                               "hooks": [{"type": "command", "command": cmd, "timeout": 15}]}}
    if args.remove:
        return {}
    if args.no_baseline:
        del groups["SessionStart"]
    return groups


def is_ours(handler) -> bool:
    return isinstance(handler, dict) and str(handler.get("command", "")).startswith(MARK)


def place(groups: list, wanted) -> list:
    """Drop our handlers from every matcher group; put `wanted` where the first one was."""
    out, pos = [], None
    for group in groups:
        handlers = group.get("hooks") if isinstance(group, dict) else None
        if isinstance(handlers, list) and any(is_ours(h) for h in handlers):
            if pos is None:
                pos = len(out)
            rest = [h for h in handlers if not is_ours(h)]
            if rest:
                out.append(dict(group, hooks=rest))
            continue
        out.append(group)
    if wanted is not None:
        out.insert(len(out) if pos is None else pos, wanted)
    return out


def merge(settings: dict, wanted: dict) -> dict:
    if not isinstance(settings, dict):
        raise InstallError(f"{SETTINGS_REL} is not a JSON object")
    new = copy.deepcopy(settings)
    hooks = new.get("hooks")
    hooks = {} if hooks is None else hooks
    if not isinstance(hooks, dict):
        raise InstallError(f"{SETTINGS_REL}: \"hooks\" is not an object")
    changed = False
    for event in list(hooks) + [e for e in wanted if e not in hooks]:
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise InstallError(f"{SETTINGS_REL}: hooks.{event} is not a list")
        placed = place(groups, copy.deepcopy(wanted.get(event)))
        if placed == groups:
            continue
        changed = True
        if placed:
            hooks[event] = placed
        else:
            del hooks[event]
    if not changed:
        return new
    if hooks:
        new["hooks"] = hooks
    else:
        new.pop("hooks", None)
    return new


def load_json(text: str) -> tuple:
    """The parsed object and the keys that appear twice in one object (JSON keeps the last)."""
    dups = []

    def pairs(items):
        seen = set()
        for key, _ in items:
            if key in seen:
                dups.append(key)
            seen.add(key)
        return dict(items)

    return json.loads(text, object_pairs_hook=pairs), dups


def render(settings: dict, old_text: str) -> str:
    """JSON in the file's own style: its indent (spaces or tabs), line endings and final
    newline, so the diff shows only what changed."""
    m = re.search(r'\n([ \t]+)"', old_text)
    text = json.dumps(settings, indent=m.group(1) if m else 2, ensure_ascii=False)
    if not old_text or old_text.endswith("\n"):
        text += "\n"
    return text.replace("\n", "\r\n") if "\r\n" in old_text else text


def target(repo: Path, rel: str) -> Path:
    """Where rel really lives. A symlink is written through when it stays inside the repo;
    one that leads out of it (a shared settings file, a linked .claude/) is refused."""
    path = repo / rel
    try:
        real = path.resolve()
    except (RuntimeError, OSError) as exc:  # a symlink loop (RuntimeError before Python 3.13)
        raise InstallError(f"{rel} can't be resolved ({exc}); fix the link first") from None
    try:
        real.relative_to(repo)
    except ValueError:
        raise InstallError(f"{rel} resolves to {real}, outside the repo; refusing to write "
                           f"there (edit it yourself, or replace the link)") from None
    return real


def plan(repo: Path, args, notes: list) -> list:
    """Every file to touch, settings.json last: (action, rel, path, data, mode, note, diff).
    Appends warnings for the user to `notes`."""
    steps = []
    rel_of = lambda p: os.path.relpath(p, repo)  # noqa: E731
    hook = target(repo, HOOK_REL)
    src = GATE_SRC.read_bytes()
    if args.remove:
        if hook.exists():
            steps.append(("delete", HOOK_REL, hook, None, None, "", ""))
            if hook.parent.is_dir() and os.listdir(hook.parent) == [hook.name]:
                steps.append(("rmdir", rel_of(hook.parent), hook.parent, None, None,
                              "left empty", ""))
    elif not hook.exists():
        steps.append(("create", HOOK_REL, hook, src, 0o755, "", ""))
    elif hook.read_bytes() != src:
        old = hook.read_text(errors="replace").splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(old, src.decode().splitlines(keepends=True),
                                            f"a/{HOOK_REL}", f"b/{HOOK_REL}"))
        steps.append(("update", HOOK_REL, hook, src, 0o755, "differs from the skill's copy",
                      diff))

    if args.check_template and not args.remove:  # only then must scripts/ stay in the repo
        check = target(repo, CHECK_REL)
        if not check.exists():
            steps.append(("create", CHECK_REL, check, TEMPLATE.read_bytes(), 0o755,
                          "from templates/check.sh; fill its slots before relying on the gate",
                          ""))

    path = target(repo, SETTINGS_REL)
    old_text = ""
    if path.exists():
        with open(path, encoding="utf-8", newline="") as f:  # keep CRLF for render()
            old_text = f.read()
    try:
        settings, dups = load_json(old_text) if old_text.strip() else ({}, [])
    except ValueError as exc:
        raise InstallError(f"{SETTINGS_REL} is not valid JSON ({exc}); fix it first") from exc
    if dups:
        notes.append(f"warning: {SETTINGS_REL} repeats the key(s) "
                     f"{', '.join(repr(k) for k in dict.fromkeys(dups))} in one object. JSON "
                     f"(and Claude Code) keeps the last value; the rewrite drops the others.")
    new = merge(settings, wanted_groups(args))
    via = rel_of(path)
    if args.remove and new == {} and new != settings and path.exists() and via == SETTINGS_REL:
        # Nothing but our handlers was in it: take the file out, and .claude/ if that empties it.
        diff = "".join(difflib.unified_diff(old_text.splitlines(keepends=True), [],
                                            f"a/{SETTINGS_REL}", "/dev/null"))
        steps.append(("delete", SETTINGS_REL, path, None, None, "empty without the gate", diff))
        gone = {s[2] for s in steps if s[0] in ("delete", "rmdir")}
        folder = path.parent
        if all(folder / name in gone for name in os.listdir(folder)):
            steps.append(("rmdir", rel_of(folder), folder, None, None, "left empty", ""))
    elif new != settings:
        new_text = render(new, old_text)
        diff = "".join(difflib.unified_diff(old_text.splitlines(keepends=True),
                                            new_text.splitlines(keepends=True),
                                            f"a/{SETTINGS_REL}", f"b/{SETTINGS_REL}"))
        steps.append(("update" if path.exists() else "create", SETTINGS_REL, path,
                      new_text.encode(), None, f"via {via}" if via != SETTINGS_REL else "", diff))
    return steps


def write(path: Path, data, mode) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    if mode is not None:
        os.chmod(tmp, mode)
    elif path.exists():
        os.chmod(tmp, path.stat().st_mode & 0o7777)
    os.replace(tmp, path)


def apply(steps: list) -> None:
    """Run the steps; on an error, put back what the earlier ones changed and re-raise."""
    undo = []
    try:
        for action, _, path, data, mode, _, _ in steps:
            if action == "rmdir":
                os.rmdir(path)
                undo.append(lambda p=path: p.mkdir())
                continue
            old = path.read_bytes() if path.exists() else None
            old_mode = path.stat().st_mode & 0o7777 if old is not None else None
            new_dirs, d = [], path.parent
            while not d.exists():  # deepest first, so undo can rmdir them in order
                new_dirs.append(d)
                d = d.parent
            undo.extend(lambda d=d: d.rmdir() for d in reversed(new_dirs))
            if action == "delete":
                path.unlink()
            else:
                write(path, data, mode)
            undo.append(lambda p=path, b=old, m=old_mode:
                        write(p, b, m) if b is not None else p.unlink())
    except OSError:
        for step in reversed(undo):
            try:
                step()
            except OSError:
                pass
        raise


def check_args(args) -> str:
    if not (math.isfinite(args.timeout) and args.timeout > 0):
        return "--timeout must be a positive number"
    if args.max_failures < 2:
        return "--max-failures must be 2 or more (at 1 the gate would never block)"
    for path in args.watch:
        if not path or os.path.isabs(path) or os.path.normpath(path).split(os.sep)[0] == "..":
            return f"--watch {path!r} must be a path inside the repo, relative to its root"
    return ""


def _git(repo: Path, *args) -> Optional[str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")}
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, env=env, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def where_notes(repo: Path, top: Optional[Path], args) -> list:
    """What the user should know about where the gate looks: a subproject, a --watch typo."""
    notes = []
    if top is not None and top != repo:
        notes.append(f"note: {repo} is a subfolder of the git work tree {top}. When Claude "
                     f"Code is started here, the gate hashes all of {top} and runs "
                     f"{CHECK_REL} from this folder (or {top}/{CHECK_REL} if this one has "
                     f"none). --watch paths are relative to this folder.")
    for path in args.watch:
        if path.startswith(":") or (repo / path).exists() or (_git(repo, "ls-files", "--", path)
                                                              or "").strip():
            continue
        notes.append(f"note: --watch {path!r} matches nothing in {repo} yet; until it does, "
                     f"no change runs the check")
    return notes


def main(argv=None) -> int:
    p = _Parser(description=__doc__.split("\n\n")[0])
    p.add_argument("repo", type=Path)
    p.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    p.add_argument("--remove", action="store_true", help="take the gate out again")
    p.add_argument("--mode", choices=("fast", "full"), default="fast",
                   help="flag passed to scripts/check (default fast)")
    p.add_argument("--timeout", type=float, default=240, help="check timeout in seconds")
    p.add_argument("--max-failures", type=int, default=3,
                   help="the gate blocks on failures 1 to N-1 in a prompt and lets failure N "
                        "through with a notice (default 3, at least 2)")
    p.add_argument("--watch", action="append", default=[], metavar="PATH",
                   help="only changes under these paths count (repeatable; default all)")
    p.add_argument("--check", default="", metavar="CMD",
                   help="shell command to run instead of scripts/check")
    p.add_argument("--no-baseline", action="store_true",
                   help="skip the SessionStart handler that records the starting tree")
    p.add_argument("--check-template", action="store_true",
                   help="also copy templates/check.sh to scripts/check when it is missing")
    args = p.parse_args(argv)
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"install_gate: {repo} is not a directory", file=sys.stderr)
        return 1
    problem = check_args(args)
    if problem:
        print(f"install_gate: {problem}", file=sys.stderr)
        return 1
    top = None
    if not args.remove:
        out = _git(repo, "rev-parse", "--show-toplevel")
        top = Path(out.strip()).resolve() if out and out.strip() else None
        if top is None:
            print(f"install_gate: {repo} is not inside a git work tree (or git is missing); "
                  f"the gate uses git to see what changed, so it would never run. "
                  f"Run `git init` there first.", file=sys.stderr)
            return 1
    notes = where_notes(repo, top, args) if not args.remove else []
    try:
        steps = plan(repo, args, notes)
    except (InstallError, OSError, UnicodeDecodeError) as exc:
        print(f"install_gate: {exc}", file=sys.stderr)
        return 1

    verb = "remove" if args.remove else "install"
    mode = "applying" if args.apply else "dry run; re-run with --apply to write"
    print(f"verify-gate {verb} -> {repo} ({mode})")
    if not steps:
        print("  nothing to change")
    for action, rel, _, _, _, note, _ in steps:
        print(f"  {action:<7} {rel}" + (f"  ({note})" if note else ""))
    for *_, diff in steps:
        if diff:
            sys.stdout.write("\n" + diff)
    if not args.remove and not args.check and not (repo / CHECK_REL).exists() \
            and not args.check_template:
        if top is not None and top != repo and (top / CHECK_REL).exists():
            notes.append(f"note: no {CHECK_REL} in this folder; the gate runs "
                         f"{top / CHECK_REL} instead")
        else:
            notes.append("note: no scripts/check yet; the gate allows every stop until it "
                         "exists (copy templates/check.sh or pass --check-template)")
    if os.name == "nt" and not args.remove:
        notes.append("note: the gate is POSIX-only and untested on Windows. Claude Code runs it "
                     "under Git Bash, but Windows can't start scripts/check directly, so every "
                     "stop would be allowed as 'could not start'; pass --check \"bash "
                     "scripts/check --fast\" there.")
    for line in notes:
        print("\n" + line)
    if not args.apply:
        return 0
    try:
        apply(steps)
    except OSError as exc:
        print(f"install_gate: {exc}; nothing was changed", file=sys.stderr)
        return 1
    if steps and not args.remove:
        print(f"\ndone. Commit {HOOK_REL} and {SETTINGS_REL}; running sessions pick the "
              f"hooks up from the settings file watcher.")
    elif steps:
        print(f"\ndone. Commit the removal of {HOOK_REL} and the {SETTINGS_REL} change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
