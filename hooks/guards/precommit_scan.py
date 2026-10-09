#!/usr/bin/env python3
"""Git pre-commit secret scanner, sharing patterns.py with the Claude Code guards.
Catches what the Claude hooks can't see: commits made by cron jobs, editors,
Obsidian Git or people.

    precommit_scan.py [scan]                   scan the staged diff (what git runs)
    precommit_scan.py scan-files PATH...       scan whole files, e.g. an audit
    precommit_scan.py install REPO [--standalone] [--force]
    precommit_scan.py uninstall REPO
    precommit_scan.py status REPO

Only added lines are scanned, plus, whole, staged files git won't diff as text
(a `binary` or `-diff` attribute, an early NUL byte) that decode as UTF-8, up
to 5 MB. A finding prints `path:line  kind` (never the value) and exits 3; the
installed hook turns that into a refused commit. Its own errors print a warning
and exit 0, so a broken scanner never blocks work. The hook runs it with the
first working Python 3 (python3, python, `py -3`), as hooks/guards/run.sh does.

Optionally the same lines are checked for known confidential values (account
ids, internal hosts, table names, addresses …) that are not secret-shaped. They
are read from the `confidential` section of JSON values files kept out of git:
$CLAUDE_GUARDS_CONFIDENTIAL (paths separated by os.pathsep), else every
`guards.confidential` in the repo's git config (relative paths from the top of
the work tree). `literals` match case-insensitively as substrings; `patterns`
are Python regexes, plain strings or {"name", "regex"}. A hit is reported as
`path:line  confidential-<name or number>`, never the value. A values file that
is missing or invalid, a too-short literal or a bad regex is a warning, and the
rest of the check still runs.

`install` writes a small sh hook into the repo's common git dir (all worktrees
share it). It never sets core.hooksPath and never overwrites a hook without
keeping it: an existing pre-commit is moved to pre-commit.guards-orig and
chained to. A displaced hook that dispatches on its own file name (husky v4,
overcommit: `basename "$0"`) would then run as pre-commit.guards-orig and skip
its checks, so install refuses one unless --force is given. When pre-commit
is shepherd's wrapper, the scanner goes in as pre-commit.shepherd-orig, the
hook shepherd's wrapper runs for every commit. By default the hook calls this
file where it lives (keep it at a stable path, such as the main checkout);
--standalone copies the scanner into the hooks dir instead, for clones without
this repo."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

COMPONENT = "precommit_scan"
BLOCK = 3            # "secrets found"; any other non-zero exit lets the commit go on
BLOB_CAP = 5_000_000 # staged files git treats as binary are scanned up to this size
CONFIDENTIAL_ENV = "CLAUDE_GUARDS_CONFIDENTIAL"
CONFIDENTIAL_KEY = "guards.confidential"
LITERAL_MIN = 4      # a shorter confidential literal would refuse nearly every commit
MARK = "# guards-precommit v1"
SHEPHERD_MARK = "# shepherd-hook v1"
ORIG = "pre-commit.guards-orig"
STANDALONE_DIR = "guards-scan"
STANDALONE_FILES = ("precommit_scan.py", "patterns.py", "common.py")

STUB = """#!/bin/sh
{mark}
# Installed by `precommit_scan.py install`: scans the staged diff for
# secret-shaped literals, then runs the hook it displaced ({orig}), if any.
# Remove with `precommit_scan.py uninstall <repo>`; skip once: git commit --no-verify.
here=$(dirname "$0")
{scanner_line}
# The first working Python 3: python3, else python (3.7+), else `py -3`. On
# Windows python3 is often only the WindowsApps alias, which fails `-c`.
py= flag=
for c in python3 python; do
  p=$(command -v "$c" 2>/dev/null) || continue
  "$p" -c 'import sys; sys.exit(sys.version_info < (3, 7))' >/dev/null 2>&1 && {{ py=$p; break; }}
done
if [ -z "$py" ] && command -v py >/dev/null 2>&1 && py -3 -c "" >/dev/null 2>&1; then
  py=py flag=-3
fi
if [ -z "$py" ]; then
  echo "guards: no Python 3 found (python3, python, py -3); commit not scanned" >&2
elif [ ! -f "$scanner" ]; then
  echo "guards: secret scanner not found at $scanner; commit not scanned" >&2
else
  "$py" $flag -I "$scanner" scan
  rc=$?
  [ $rc -eq {block} ] && exit 1
  [ $rc -ne 0 ] && echo "guards: secret scan failed (rc=$rc); commit not scanned" >&2
fi
if [ -x "$here/{orig}" ]; then
  exec "$here/{orig}" "$@"
fi
exit 0
"""

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
# A hook that reads its own name: renamed to pre-commit.guards-orig it would
# no longer recognise itself as pre-commit.
NAME_SENSITIVE = re.compile(r"basename|\$\{0[#%]|argv\[[01]\]|PROGRAM_NAME|__file__|husky")


class InstallError(Exception):
    pass


# --- scanning ---------------------------------------------------------------------

def _unquote(p: str) -> str:
    """Undo git's C-style path quoting ("a/sp\\303\\251c")."""
    if not (p.startswith('"') and p.endswith('"')):
        return p
    raw = p[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
    try:
        return raw.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw


def added_blocks(diff: str) -> List[Tuple[str, int, str]]:
    """(path, first line, text) for each run of consecutive added lines in a
    unified diff."""
    blocks = []
    path, line, start, buf = None, 0, 0, []
    in_header = False

    def flush():
        if path and buf:
            blocks.append((path, start, "\n".join(buf)))
        buf.clear()

    for ln in diff.split("\n"):
        if ln.startswith("diff --git "):
            flush()
            path, in_header = None, True
            continue
        if in_header:
            if ln.startswith("+++ "):
                target = ln[4:].rstrip("\t")      # git appends a TAB to names with spaces
                path = None if target == "/dev/null" else _unquote(target)
                if path and path.startswith("b/"):
                    path = path[2:]
                continue
            elif ln.startswith("@@"):
                in_header = False
            else:
                continue
        m = HUNK.match(ln)
        if m:
            flush()
            line = int(m.group(1))
            continue
        if ln.startswith("+"):
            if not buf:
                start = line
            buf.append(ln[1:])
            line += 1
        elif ln.startswith(" "):
            flush()
            line += 1
        else:                      # "-" removed line, "\ No newline", blank
            flush()
    flush()
    return blocks


# --- confidential values ------------------------------------------------------------

class Confidential(NamedTuple):
    sources: List[str]
    kinds: Dict[str, str]                       # lower-cased literal -> its finding kind
    literals: Optional["re.Pattern"]            # the same literals, for non-ASCII text
    patterns: List[Tuple[str, "re.Pattern"]]
    problems: List[str]                         # name a file and an entry, never a value


NO_CONFIDENTIAL = Confidential([], {}, None, [], [])


def confidential_sources(repo: str = ".") -> List[str]:
    """The values files: $CLAUDE_GUARDS_CONFIDENTIAL (set but empty: none), else
    every guards.confidential in repo's git config, a relative one taken from
    the top of its work tree."""
    env = os.environ.get(CONFIDENTIAL_ENV)
    if env is not None:
        return [os.path.abspath(os.path.expanduser(p)) for p in env.split(os.pathsep) if p.strip()]
    try:
        r = subprocess.run(["git", "-C", repo, "config", "--get-all", CONFIDENTIAL_KEY],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        paths = [os.path.expanduser(p.strip()) for p in r.stdout.splitlines() if p.strip()] \
            if r.returncode == 0 else []
        if any(not os.path.isabs(p) for p in paths):
            t = subprocess.run(["git", "-C", repo, "rev-parse", "--show-toplevel"],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
            top = t.stdout.strip() if t.returncode == 0 and t.stdout.strip() else os.path.abspath(repo)
            paths = [os.path.join(top, p) for p in paths]
    except (OSError, subprocess.SubprocessError):
        return []
    return paths


def _entries(v) -> list:
    return v if isinstance(v, list) else [] if v is None else [v]


def load_confidential(paths: List[str]) -> Confidential:
    """Literals and patterns from the `confidential` object of each values file.
    Problems (a missing file, a short literal, a bad regex) are collected, not
    raised: the rest of the check still runs. Entries are numbered from 1 in
    their file; with several files the kind also says which (`…@2`)."""
    kinds: Dict[str, str] = {}
    pats: List[Tuple[str, "re.Pattern"]] = []
    problems: List[str] = []
    for n, path in enumerate(paths, 1):
        tag = f"@{n}" if len(paths) > 1 else ""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            problems.append(f"{path} not found")
            continue
        except (OSError, ValueError) as e:
            problems.append(f"{path} is unreadable or not JSON ({type(e).__name__})")
            continue
        sec = data.get("confidential") if isinstance(data, dict) else None
        if not isinstance(sec, dict):
            problems.append(f"{path} has no \"confidential\" object")
            continue
        for i, v in enumerate(_entries(sec.get("literals")), 1):
            if not isinstance(v, str) or len(v.strip()) < LITERAL_MIN:
                problems.append(f"{path}: literal {i} skipped (not a string of {LITERAL_MIN}+ characters)")
                continue
            kinds.setdefault(v.strip().lower(), f"confidential-literal-{i}{tag}")
        for i, v in enumerate(_entries(sec.get("patterns")), 1):
            name, rx = (v.get("name"), v.get("regex")) if isinstance(v, dict) else (None, v)
            if not isinstance(rx, str) or not rx:
                problems.append(f"{path}: pattern {i} skipped (no regex string)")
                continue
            try:
                compiled = re.compile(rx)
            except re.error as e:
                problems.append(f"{path}: pattern {i} skipped (invalid regex: {e.msg})")
                continue
            slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")[:40] if isinstance(name, str) else ""
            pats.append((f"confidential-{slug}{tag}" if slug else f"confidential-pattern-{i}{tag}", compiled))
    alt = re.compile("|".join(re.escape(k) for k in sorted(kinds, key=len, reverse=True)), re.I) \
        if kinds else None
    return Confidential(list(paths), kinds, alt, pats, problems)


def _confidential_hits(text: str, conf: Confidential) -> list:
    """Findings for each confidential value in text, lines with the allow marker
    left out. Literals are found with str.find on the lower-cased text (20x
    faster than one big case-insensitive alternation), unless lower-casing
    changes its length (some non-ASCII letters), where offsets would drift."""
    import patterns
    hits = []
    low = text.lower() if conf.kinds else ""
    if len(low) == len(text):
        for lit, kind in conf.kinds.items():
            i = low.find(lit)
            while i >= 0:
                hits.append(patterns.Finding(kind, i, i + len(lit)))
                i = low.find(lit, i + 1)
    elif conf.literals is not None:
        hits += [patterns.Finding(conf.kinds.get(m.group(0).lower(), "confidential-literal"),
                                  m.start(), m.end()) for m in conf.literals.finditer(text)]
    for kind, rx in conf.patterns:
        hits += [patterns.Finding(kind, m.start(), m.end()) for m in rx.finditer(text)
                 if m.end() > m.start()]
    if hits and patterns.ALLOW_MARKER in text:
        def marked(pos: int) -> bool:
            a, b = text.rfind("\n", 0, pos) + 1, text.find("\n", pos)
            return patterns.ALLOW_MARKER in text[a:len(text) if b < 0 else b]
        hits = [f for f in hits if not marked(f.start)]
    return sorted(hits, key=lambda f: f.start)


def findings_in(blocks, conf: Confidential = NO_CONFIDENTIAL) -> List[Tuple[str, int, str]]:
    """(path, line, kind) for each secret-shaped literal in the blocks and, with
    conf, each confidential value (once per kind and line)."""
    import patterns
    out = []
    for path, first, text in blocks:
        found = patterns.scan(text)
        for f, line in zip(found, patterns.line_numbers(text, found)):
            out.append((path, first + line - 1, f.kind))
        if conf.kinds or conf.patterns:
            hits = _confidential_hits(text, conf)
            seen = set()
            for f, line in zip(hits, patterns.line_numbers(text, hits)):
                if (line, f.kind) not in seen:
                    seen.add((line, f.kind))
                    out.append((path, first + line - 1, f.kind))
    return out


def _warn(problem: str) -> None:
    print(f"guards: confidential values: {problem}; not checked", file=sys.stderr)
    try:
        import common
        common.log_error(COMPONENT, ValueError(problem))
    except Exception:
        pass


def confidential_config(repo: str = ".") -> Confidential:
    """The configured confidential values; problems go to stderr and errors.log.
    Its own failure only drops this check: the secret scan still runs."""
    try:
        srcs = confidential_sources(repo)
        conf = load_confidential(srcs) if srcs else NO_CONFIDENTIAL
    except Exception as e:
        _warn(f"internal error ({type(e).__name__})")
        return NO_CONFIDENTIAL
    for p in conf.problems:
        _warn(p)
    return conf


def report(found, conf: Confidential = NO_CONFIDENTIAL) -> None:
    import patterns
    print("guards: commit refused, secrets or confidential values in the staged changes "
          "(values not shown):", file=sys.stderr)
    for path, line, kind in found[:50]:
        print(f"  {path}:{line}  {kind}", file=sys.stderr)
    if len(found) > 50:
        print(f"  ... and {len(found) - 50} more", file=sys.stderr)
    if any(k.startswith("confidential-") for _, _, k in found):
        files = ", ".join(f"[{n}] {s}" if len(conf.sources) > 1 else s
                          for n, s in enumerate(conf.sources, 1))
        print(f"confidential-* entries are values listed in {files} (numbered from 1 in their "
              f"file). Keep the value in a gitignored values file and commit a placeholder "
              f"(a *.example file) instead.", file=sys.stderr)
    print("Read the value at run time from an environment variable or a gitignored file, "
          "fix the file and `git add` it again. For a deliberately fake test fixture, put "
          f"`{patterns.ALLOW_MARKER}` on that line.", file=sys.stderr)


def _git_out(*args: str) -> bytes:
    r = subprocess.run(["git", *args], stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git {args[0]} {args[1] if len(args) > 1 else ''} failed (rc={r.returncode})")
    return r.stdout


def binary_blocks() -> List[Tuple[str, int, str]]:
    """(path, 1, text) for each staged file git won't diff as text (a `binary` or
    `-diff` attribute, a NUL byte early on) that is really text: a JSONL log
    marked binary, a log that starts with a NUL. Scanned whole, up to BLOB_CAP;
    files that don't decode as UTF-8 are left alone."""
    out = []
    stat = _git_out("diff", "--cached", "--numstat", "-z", "--no-renames", "--diff-filter=ACMRT")
    for entry in stat.decode("utf-8", errors="surrogateescape").split("\0"):
        parts = entry.split("\t", 2)
        if len(parts) != 3 or parts[:2] != ["-", "-"]:
            continue
        spec = ":0:" + parts[2]
        try:
            if int(_git_out("cat-file", "-s", spec).strip() or 0) > BLOB_CAP:
                continue
            data = _git_out("cat-file", "blob", spec)
        except (RuntimeError, ValueError):
            continue
        text = data.decode("utf-8", errors="replace")
        if text.count("\ufffd") <= len(text) // 100:
            out.append((parts[2], 1, text))
    return out


def scan_staged() -> int:
    r = subprocess.run(["git", "-c", "core.quotePath=false", "diff", "--cached", "--no-color",
                        "--no-ext-diff", "--no-textconv", "-U0", "--diff-filter=ACMRT"],
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git diff --cached failed (rc={r.returncode})")
    diff = r.stdout.decode("utf-8", errors="replace")
    blocks = added_blocks(diff)
    if "Binary files " in diff:
        blocks += binary_blocks()
    conf = confidential_config()
    found = findings_in(blocks, conf)
    if found:
        report(found, conf)
        return BLOCK
    return 0


def scan_files(paths) -> int:
    conf = confidential_config()
    found = []
    for p in paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"guards: cannot read {p}: {e.strerror}", file=sys.stderr)
            continue
        found += findings_in([(p, 1, text)], conf)
    for path, line, kind in found:
        print(f"{path}:{line}  {kind}")
    return BLOCK if found else 0


# --- installing -------------------------------------------------------------------

def _git(repo: str, *args: str) -> str:
    r = subprocess.run(["git", "-C", repo, *args], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise InstallError(f"git {' '.join(args)} failed in {repo}: {r.stderr.strip()}")
    return r.stdout.strip()


def hooks_dir(repo: str) -> Path:
    if not os.path.isdir(repo):
        raise InstallError(f"{repo}: not a directory")
    try:
        hp = _git(repo, "config", "--get", "core.hooksPath")
    except InstallError:
        hp = ""
    if hp:
        raise InstallError(f"{repo} sets core.hooksPath={hp}; hooks there are managed by "
                           f"something else. Add `python3 -I {HERE / 'precommit_scan.py'} scan` "
                           f"to that pre-commit by hand.")
    common = _git(repo, "rev-parse", "--git-common-dir")
    path = Path(common)
    if not path.is_absolute():
        path = Path(repo) / path
    return path.resolve() / "hooks"


def _text(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def _location(d: Path) -> Path:
    """Where the scanner hook goes: pre-commit, or behind shepherd's wrapper."""
    pc = d / "pre-commit"
    if pc.exists() and SHEPHERD_MARK in _text(pc):
        return d / "pre-commit.shepherd-orig"
    return pc


def _ours(d: Path) -> Optional[Path]:
    for p in (d / "pre-commit", d / "pre-commit.shepherd-orig"):
        if p.exists() and MARK in _text(p):
            return p
    return None


def _quote_sh(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _stub(standalone: bool) -> str:
    if standalone:
        scanner_line = f'scanner="$here/{STANDALONE_DIR}/precommit_scan.py"'
    else:
        scanner_line = f"scanner={_quote_sh(str(HERE / 'precommit_scan.py'))}"
    return STUB.format(mark=MARK, orig=ORIG, block=BLOCK, scanner_line=scanner_line)


def _present(p: Path) -> bool:
    return p.is_symlink() or p.exists()         # a dangling symlink is still in the way


def install(repo: str, standalone: bool = False, force: bool = False) -> List[str]:
    d = hooks_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    notes = []
    target = _ours(d) or _location(d)
    if _present(target) and MARK not in _text(target):
        backup = d / ORIG
        if _present(backup):
            raise InstallError(f"{target} exists and so does {backup}; resolve by hand")
        if NAME_SENSITIVE.search(_text(target)) and not force:
            raise InstallError(
                f"{target} looks at its own file name (basename \"$0\", husky, overcommit ...). "
                f"Chained as {ORIG} it may skip its checks. Add `python3 -I "
                f"{HERE / 'precommit_scan.py'} scan` (exit 3 = refuse) to that hook by hand, or "
                f"re-run with --force to chain it anyway")
        os.replace(target, backup)              # a symlink moves as the link, never its target
        notes.append(f"kept the existing {target.name} as {backup.name} (chained after the scan)"
                     + (" (a symlink; it dangles, so nothing is chained)"
                        if backup.is_symlink() and not backup.exists() else ""))
    elif target.is_symlink():
        target.unlink()                         # ours behind a link: never write through it
    if standalone:
        sd = d / STANDALONE_DIR
        sd.mkdir(exist_ok=True)
        for name in STANDALONE_FILES:
            shutil.copy2(HERE / name, sd / name)
        notes.append(f"copied the scanner into {sd}")
    elif next(((p / ".git").is_file() for p in (HERE, *HERE.parents) if (p / ".git").exists()), False):
        notes.append(f"warning: {HERE} is in a linked worktree (or submodule); once it is removed "
                     f"commits go unscanned. Install from the main checkout or use --standalone.")
    target.write_text(_stub(standalone))
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    where = "behind shepherd's wrapper" if target.name.endswith("shepherd-orig") else ""
    notes.insert(0, f"installed {target}" + (f" ({where})" if where else ""))
    return notes


def uninstall(repo: str) -> List[str]:
    d = hooks_dir(repo)
    ours = _ours(d)
    notes = []
    if ours is None:
        return ["no guards pre-commit hook installed"]
    ours.unlink()
    notes.append(f"removed {ours}")
    backup = d / ORIG
    if _present(backup):
        os.replace(backup, ours)
        notes.append(f"restored {ours.name} from {ORIG}")
    sd = d / STANDALONE_DIR
    if sd.is_dir():
        shutil.rmtree(sd)
        notes.append(f"removed {sd}")
    return notes


def _runs(*cmd: str) -> bool:
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def hook_python() -> str:
    """The interpreter the installed hook picks on this PATH, as the STUB does; ''
    when there is none."""
    for name in ("python3", "python"):
        p = shutil.which(name)
        if p and _runs(p, "-c", "import sys; sys.exit(sys.version_info < (3, 7))"):
            return p
    p = shutil.which("py")
    return "py -3" if p and _runs(p, "-3", "-c", "") else ""


def status(repo: str) -> List[str]:
    d = hooks_dir(repo)
    ours = _ours(d)
    if ours is None:
        return [f"not installed in {d}"]
    text = _text(ours)
    lines = [f"installed: {ours}"]
    m = re.search(r"^scanner=.*$", text, re.M)
    if not m or text != STUB.format(mark=MARK, orig=ORIG, block=BLOCK, scanner_line=m.group(0)):
        lines.append("hook script differs from this version's: re-run install")
    py = hook_python()
    lines.append(f"python: {py} (on this PATH; git runs the hook with its caller's)" if py else
                 "python: none found (python3, python 3.7+, py -3): commits are NOT scanned")
    if f"$here/{STANDALONE_DIR}/" in text:
        sd = d / STANDALONE_DIR
        stale = [n for n in STANDALONE_FILES
                 if not (sd / n).exists() or (sd / n).read_bytes() != (HERE / n).read_bytes()]
        lines.append(f"standalone copy in {sd}" + (f"; differs from {HERE} ({', '.join(stale)}): "
                                                   f"re-run install --standalone" if stale else ""))
    else:
        m = re.search(r"^scanner='(.*)'$", text, re.M)
        path = m.group(1) if m else "?"
        lines.append(f"scanner: {path}" + ("" if os.path.isfile(path) else " (MISSING: commits "
                                                                            "are not scanned)"))
    if (d / ORIG).exists():
        lines.append(f"chains to {d / ORIG}")
    srcs = confidential_sources(repo)
    if not srcs:
        lines.append(f"confidential values: none (optional: git config {CONFIDENTIAL_KEY} <file>)")
    else:
        conf = load_confidential(srcs)
        lines.append(f"confidential values: {len(conf.kinds)} literals, {len(conf.patterns)} patterns "
                     f"from {', '.join(srcs)}")
        lines += [f"  problem: {p} (not checked)" for p in conf.problems]
    return lines


def main(argv: List[str]) -> int:
    cmd = argv[0] if argv else "scan"
    if cmd in ("install", "uninstall", "status"):
        args = [a for a in argv[1:] if not a.startswith("--")]
        if len(args) != 1:
            print(__doc__.split("\n\n")[1], file=sys.stderr)
            return 2
        try:
            if cmd == "install":
                notes = install(args[0], standalone="--standalone" in argv,
                                force="--force" in argv)
            else:
                notes = uninstall(args[0]) if cmd == "uninstall" else status(args[0])
        except InstallError as e:
            print(f"guards: {e}", file=sys.stderr)
            return 1
        for n in notes:
            print(n)
        return 0
    try:
        import common
    except Exception as e:
        print(f"guards: secret scanner cannot load its helpers ({type(e).__name__}); "
              f"commit not scanned", file=sys.stderr)
        return 0
    try:
        if cmd == "scan":
            return scan_staged()
        if cmd == "scan-files" and len(argv) > 1:
            return scan_files(argv[1:])
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    except Exception as e:
        common.log_error(COMPONENT, e)
        print(f"guards: secret scan failed ({type(e).__name__}: {e}); not enforced. "
              f"See {common.home() / 'errors.log'}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
