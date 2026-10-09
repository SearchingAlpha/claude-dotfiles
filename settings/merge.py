#!/usr/bin/env python3
"""Deep-merge settings templates and overlays into a Claude Code settings file.

    merge.py [--target FILE] [--apply | --out FILE] [--force] [--override]
             [--override-key PATH]... [--remove-key PATH]... [--values FILE]...
             [OVERLAY...]

Objects merge recursively. Arrays are unioned: the target's items keep their
order and new items are appended. A scalar the target already has wins, unless
--override (every scalar conflict) or --override-key PATH (one path and below)
lets the overlay win. Under --override-key an array is replaced whole, so an
outdated hook or rule can be swapped for its new version; --override alone
still unions arrays. Between overlays, a later file wins over an earlier one.
With no OVERLAY, only --remove-key is applied.

A PATH is dotted (`env.FOO`), with `\\.` for a dot inside a key, or a JSON
pointer (`/extraKnownMarketplaces/my.market`): marketplace and plugin names
may contain dots. Paths are tuples of keys inside, never split strings.

A few keys are one unit in Claude Code and are never mixed: statusLine,
subagentStatusLine, fallbackModel, modelPicker and each marketplace entry in
extraKnownMarketplaces. The existing value is kept whole, or replaced whole
with --override-key; mixing two status lines would run the old command with
the new options.

Nothing is written by default: the unified diff goes to stdout and every
decision (added, kept, overridden, removed) to stderr. --apply rewrites the
target atomically after a timestamped backup; --out writes the merged result
to another file, e.g. for a `claude --settings <file>` trial. --out never
writes over an overlay, and replaces an existing file only with --force
(after a backup).

`{{DOTFILES}}` in an overlay string becomes this repo's root (or --dotfiles),
written with forward slashes, so a committed template can point at scripts in
the checkout. A path built that way that doesn't exist (a checkout without the
script yet) is a warning, and stops --apply unless --force.

Any other `{{NAME}}` comes from a --values file: a JSON object of NAME to
string, kept out of git, so a committed overlay can name a value that must
not be committed (an account id, an internal URL). A placeholder with no
value aborts, and so does a value still reading `<...>` as in the example
file. Values are inserted as plain text; nothing quotes them.

Invalid JSON in any input aborts before anything is written, and so does a
merge that would drop or change a value the target already has without
--override, --override-key or --remove-key saying so. So does a read-only
target or one that is a symlink to a missing file. Inputs are read as UTF-8
with an optional BOM; output is written without one.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import difflib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKEN = re.compile(r"\{\{([A-Z_]+)\}\}")
# A value that is still the example file's placeholder, e.g. "<aws account id>".
UNFILLED = re.compile(r"\s*<[^<>]*>\s*")
# {{DOTFILES}} and the rest of its word: the path a command or argument names.
DOTFILES_WORD = re.compile(r"\{\{DOTFILES\}\}([^\s\"'`;&|<>()]*)")

# Paths whose value is one unit; "*" stands for any single key. From the
# settings reference: a status line is one command with its options,
# fallbackModel is an ordered chain and modelPicker one list of rows (both
# taken whole from one file), and a marketplace source is one location.
ATOMIC = (("statusLine",), ("subagentStatusLine",), ("fallbackModel",), ("modelPicker",),
          ("extraKnownMarketplaces", "*"))
# The last element of a leaves() path that stands for "an item of this array".
ITEM = None


class MergeError(Exception):
    """Bad input or an unsafe write: nothing was written."""


# --- paths -----------------------------------------------------------------

def parse_path(text: str) -> tuple:
    """A command-line PATH as a tuple of keys: a JSON pointer when it starts
    with `/` (`~1` is `/`, `~0` is `~`), else dotted, `\\.` and `\\\\` being a
    literal dot and backslash."""
    if text.startswith("/"):
        return tuple(k.replace("~1", "/").replace("~0", "~") for k in text[1:].split("/"))
    keys, buf, i = [], [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and text[i + 1:i + 2] in (".", "\\"):
            buf.append(text[i + 1])
            i += 2
            continue
        if c == ".":
            keys.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    keys.append("".join(buf))
    if "" in keys:
        raise MergeError(f"PATH {text!r} has an empty key; a JSON pointer such as /a/ "
                         "names one")
    return tuple(keys)


def fmt(path: tuple) -> str:
    """A path the way PATH takes it: dotted when that reads back the same,
    else a JSON pointer. A trailing ITEM shows as `[]`."""
    keys, tail = (path[:-1], "[]") if path and path[-1] is ITEM else (path, "")
    if keys and all(k and not set(k) & set(".\\/") for k in keys):
        return ".".join(keys) + tail
    return "".join("/" + k.replace("~", "~0").replace("/", "~1") for k in keys) + tail


# --- loading ---------------------------------------------------------------

def read_text(path: Path) -> str | None:
    """The file's text, or None when it doesn't exist. utf-8-sig drops the BOM
    Windows PowerShell 5 writes with `-Encoding UTF8`."""
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as e:
        raise MergeError(f"{path}: not UTF-8 text (bad byte at offset {e.start})")
    except OSError as e:  # a directory, no permission, a file in the path
        raise MergeError(f"{path}: cannot read ({e.strerror or type(e).__name__})")


def load_json(path: Path, *, missing_ok: bool = False) -> tuple[dict, str]:
    """Return (parsed object, raw text). A missing target is an empty object."""
    text = read_text(path)
    if text is None:
        if missing_ok:
            return {}, ""
        raise MergeError(f"{path}: no such file")
    if not text.strip() and missing_ok:
        return {}, text

    def no_constant(name):  # Python reads NaN and Infinity; JSON and Claude Code don't
        raise MergeError(f"{path}: invalid JSON ({name} is not a JSON value)")

    def finite(digits):  # 1e400 reads as inf, which can't be written back
        value = float(digits)
        if math.isinf(value):
            raise MergeError(f"{path}: invalid JSON (number {digits[:20]} is out of range)")
        return value
    try:
        data = json.loads(text, parse_constant=no_constant, parse_float=finite)
    except json.JSONDecodeError as e:
        raise MergeError(f"{path}: invalid JSON ({e.msg} at line {e.lineno} col {e.colno})")
    except RecursionError:
        raise MergeError(f"{path}: invalid JSON (nested too deeply)")
    except ValueError as e:  # e.g. an integer longer than Python converts
        raise MergeError(f"{path}: invalid JSON ({e})")
    if not isinstance(data, dict):
        raise MergeError(f"{path}: top level must be a JSON object")
    return data, text


def substitute(value, variables: dict[str, str], where: str):
    """Replace {{NAME}} tokens in every string; unknown tokens are an error."""
    if isinstance(value, dict):
        return {k: substitute(v, variables, f"{where}.{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, variables, f"{where}[]") for v in value]
    if isinstance(value, str):
        def repl(m):
            if m.group(1) not in variables:
                raise MergeError(f"{where}: no value for {m.group(0)}; pass --values FILE "
                                 f"with \"{m.group(1)}\" in it (settings/overlays/"
                                 "private.example.json lists the names)")
            return variables[m.group(1)]
        return TOKEN.sub(repl, value)
    return value


def load_values(path: Path) -> dict[str, str]:
    """A --values file: {"NAME": "text", ...}. Keys starting with `$` are
    comments. DOTFILES is merge.py's own (--dotfiles)."""
    data, _ = load_json(path)
    out = {}
    for k, v in data.items():
        if k.startswith("$"):
            continue
        if not TOKEN.fullmatch("{{" + k + "}}"):
            raise MergeError(f"{path}: {k!r} is not a placeholder name (A-Z and _ only)")
        if k == "DOTFILES":
            raise MergeError(f"{path}: DOTFILES is set with --dotfiles, not in a values file")
        if not isinstance(v, str) or not v.strip():
            raise MergeError(f"{path}: {k} must be a non-empty string")
        if UNFILLED.fullmatch(v):
            raise MergeError(f"{path}: {k} is still the example's placeholder {v}; "
                             "put the real value in your copy")
        out[k] = v
    return out


def unignored(path: Path, root: Path) -> bool:
    """Whether a values file sits in the checkout without being gitignored,
    so `git add` would pick it up. False when git can't tell."""
    try:
        real = path.resolve()
        if root not in real.parents:
            return False
        r = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", str(real)],
                           capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 1  # 0 ignored, 1 not ignored, 128 not a repo or other error


def dotfiles_paths(value, root: str) -> list[str]:
    """The paths {{DOTFILES}} strings name once substituted: the token and the
    rest of its word, so `"{{DOTFILES}}/statusline/statusline.sh" --x` names
    <root>/statusline/statusline.sh."""
    if isinstance(value, dict):
        return [p for v in value.values() for p in dotfiles_paths(v, root)]
    if isinstance(value, list):
        return [p for v in value for p in dotfiles_paths(v, root)]
    if isinstance(value, str):
        return [root + m.group(1) for m in DOTFILES_WORD.finditer(value)]
    return []


# --- merging ---------------------------------------------------------------

def _key(item) -> str:
    return json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False)


def union(base: list, extra: list) -> tuple[list, list]:
    """base + the items of extra not already present; returns (merged, added)."""
    seen = {_key(i) for i in base}
    out, added = list(base), []
    for item in extra:
        k = _key(item)
        if k not in seen:
            seen.add(k)
            out.append(item)
            added.append(item)
    return out, added


def atomic(path: tuple) -> bool:
    return any(len(a) == len(path) and all(x in ("*", y) for x, y in zip(a, path))
               for a in ATOMIC)


def combine(overlays: list[dict]) -> dict:
    """Fold overlays left to right; a later overlay wins scalar conflicts."""
    acc: dict = {}
    for ov in overlays:
        acc = _fold(acc, ov)
    return acc


def _fold(a, b, path: tuple = ()):
    if path and atomic(path):
        return copy.deepcopy(b)
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _fold(a[k], v, path + (k,)) if k in a else copy.deepcopy(v)
        return out
    if isinstance(a, list) and isinstance(b, list):
        return union(a, b)[0]
    return copy.deepcopy(b)


def _covered(path: tuple, prefixes: list[tuple] | None) -> bool:
    """None means --override (everything); else path is a prefix or below."""
    if prefixes is None:
        return True
    return any(path[:len(p)] == p for p in prefixes)


def _covered_atomic(path: tuple, prefixes: list[tuple] | None) -> bool:
    """An atomic value is replaced whole, so naming a key inside it counts."""
    return _covered(path, prefixes) or any(p[:len(path)] == path for p in prefixes)


def merge(target: dict, overlay: dict, override=(), log=None, path: tuple = ()) -> dict:
    """Merge overlay into a copy of target. override: list of path tuples,
    or None for all. log collects (action, path, detail) tuples."""
    log = [] if log is None else log
    out = dict(target)
    for k, ov in overlay.items():
        p = path + (k,)
        if k not in target:
            out[k] = copy.deepcopy(ov)
            log.append(("add", p, ov))
            continue
        cur = target[k]
        if atomic(p):
            if _key(cur) == _key(ov):
                continue
            if _covered_atomic(p, override):
                out[k] = copy.deepcopy(ov)
                log.append(("override", p, (cur, ov)))
            else:
                log.append(("keep", p, (cur, ov)))
        elif isinstance(cur, dict) and isinstance(ov, dict):
            out[k] = merge(cur, ov, override, log, p)
        elif isinstance(cur, list) and isinstance(ov, list):
            if _key(cur) == _key(ov):
                continue
            if override is not None and _covered(p, override):
                # --override-key names this array or a parent: the overlay's
                # array replaces it, so an old hook or rule doesn't linger
                # next to its new version. --override alone (None) unions.
                out[k] = copy.deepcopy(ov)
                log.append(("replace", p, (cur, ov)))
            else:
                out[k], added = union(cur, ov)
                if added:
                    log.append(("extend", p, added))
        elif _key(cur) == _key(ov):
            continue
        elif _covered(p, override):
            out[k] = copy.deepcopy(ov)
            log.append(("override", p, (cur, ov)))
        else:
            log.append(("keep", p, (cur, ov)))
    return out


def remove_keys(data: dict, paths: list[tuple], log: list) -> dict:
    out = copy.deepcopy(data)
    for p in paths:
        node = out
        for key in p[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict) and p[-1] in node:
            log.append(("remove", p, node.pop(p[-1])))
        else:
            log.append(("absent", p, dotted_match(out, ".".join(p))))
    return out


def dotted_match(data, dotted: str, path: tuple = ()):
    """An existing path that reads as `dotted` once its keys are joined with
    dots, e.g. ("modelSettings", "claude-opus-4.8"): the PATH meant a dot
    inside a key. None if there is none."""
    if not isinstance(data, dict):
        return None
    for k, v in data.items():
        if dotted == k:
            return path + (k,)
        if dotted.startswith(k + "."):
            hit = dotted_match(v, dotted[len(k) + 1:], path + (k,))
            if hit:
                return hit
    return None


def leaves(value, path: tuple = ()):
    """Yield (path, scalar) for every scalar and (path + (ITEM,), item) for
    list items."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from leaves(v, path + (k,))
    elif isinstance(value, list):
        for item in value:
            yield path + (ITEM,), _key(item)
    else:
        yield path, _key(value)


def lost(before: dict, after: dict, changed: list[tuple]) -> list[tuple]:
    """Paths of `before` missing or altered in `after`, ignoring paths the
    caller deliberately overrode or removed. Must be empty before writing."""
    have = set(leaves(after))
    out = []
    for p, v in leaves(before):
        if (p, v) in have or any(p[:len(c)] == c for c in changed):
            continue
        out.append(p)
    return out


# --- output ----------------------------------------------------------------

def dumps(data: dict) -> str:
    # Same layout Claude Code writes (2-space indent, raw UTF-8, final newline),
    # so a merge that adds nothing produces an empty diff.
    return json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def short(value, width: int = 70) -> str:
    s = json.dumps(value, ensure_ascii=False)
    return s if len(s) <= width else s[: width - 1] + "…"


def report(log: list, stream) -> None:
    for action, path, detail in log:
        p = fmt(path)
        if action == "add":
            print(f"  + {p} = {short(detail)}", file=stream)
        elif action == "extend":
            print(f"  + {p}: {len(detail)} new item(s)", file=stream)
            for item in detail:
                print(f"      {short(item)}", file=stream)
        elif action == "override":
            print(f"  ~ {p}: {short(detail[0], 40)} -> {short(detail[1], 40)}", file=stream)
        elif action == "replace":
            gone = [i for i in detail[0] if _key(i) not in {_key(j) for j in detail[1]}]
            print(f"  ~ {p}: array replaced, {len(detail[0])} -> {len(detail[1])} item(s);"
                  f" dropped {len(gone)}", file=stream)
            for item in gone:
                print(f"      - {short(item)}", file=stream)
        elif action == "keep":
            print(f"  = {p}: kept existing {short(detail[0], 40)} (overlay: {short(detail[1], 40)});"
                  f" --override-key {p} to replace", file=stream)
        elif action == "remove" and isinstance(detail, (list, dict)):
            # A whole list or object goes, not one item: say how much.
            kind = "list" if isinstance(detail, list) else "object"
            print(f"  - {p}: the whole {kind}, {len(detail)} item(s) (was {short(detail, 50)})",
                  file=stream)
        elif action == "remove":
            print(f"  - {p} (was {short(detail, 50)})", file=stream)
        elif action == "absent":
            hint = f"; for the key with a dot in its name, --remove-key {fmt(detail)}" \
                if detail else ""
            print(f"  - {p}: not present, nothing to remove{hint}", file=stream)


_ANY = object()


def write_atomic(path: Path, text: str, expect=_ANY, backup: bool = False) -> Path | None:
    """Replace path with text via a same-directory temp file. With expect (the
    text read earlier, or None for "did not exist"), abort if the file changed
    since it was read: Claude Code writes this file too. The check runs again
    just before the rename, which narrows the window but can't close it:
    Claude Code takes no lock, so a write in that last instant is still lost."""
    real = Path(os.path.realpath(path))
    try:
        return _write(real, text, expect, backup)
    except OSError as e:  # read-only directory, disk full, ...
        raise MergeError(f"{e.filename or real}: cannot write "
                         f"({e.strerror or type(e).__name__}); nothing written")


def _write(real: Path, text: str, expect, backup: bool) -> Path | None:
    def changed() -> bool:
        return expect is not _ANY and read_text(real) != expect

    current = read_text(real)
    if expect is not _ANY and current != expect:
        raise MergeError(f"{real} changed while merging; nothing written, re-run")
    bak = tmp = None
    try:
        if backup and current is not None:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            bak = real.with_name(f"{real.name}.bak-{stamp}")
            n = 1
            while bak.exists():
                bak = real.with_name(f"{real.name}.bak-{stamp}-{n}")
                n += 1
            shutil.copy2(real, bak)
        mode = (real.stat().st_mode & 0o777) if current is not None else 0o600
        real.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(real.parent), prefix=f".{real.name}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        if changed():  # again, as late as possible
            raise MergeError(f"{real} changed while merging; nothing written, re-run")
        os.replace(tmp, real)
    except BaseException:
        for leftover in (tmp, bak):
            if leftover:
                try:
                    os.unlink(leftover)
                except OSError:
                    pass
        raise
    return bak


# --- CLI -------------------------------------------------------------------

def default_target() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "settings.json"


def linked_worktree(root: Path) -> bool:
    """A linked worktree has a .git file, not a directory; paths into it break
    once the worktree is removed."""
    return (root / ".git").is_file()


def check_writable(path: Path, what: str) -> None:
    """Refuse a symlink to a missing file (the write would create it, and its
    folders, wherever the link points) and a file without write permission:
    the rename would replace it regardless of its mode."""
    if path.is_symlink() and not path.exists():
        raise MergeError(f"{what} {path} is a symlink to a missing file "
                         f"({os.readlink(path)}); nothing written")
    if path.exists() and not os.access(path, os.W_OK):
        mode = path.stat().st_mode & 0o777
        raise MergeError(f"{what} {path} is read-only (mode {mode:04o}); "
                         "make it writable first; nothing written")


def check_out(out: Path, target: Path, overlays: list[Path], force: bool) -> None:
    """--out must not be the target, an overlay or a values file, and replaces
    an existing file only with --force: a typo such as settings.local.json
    would lose hand-kept rules."""
    check_writable(out, "--out")
    real = os.path.realpath(out)
    if real == os.path.realpath(target):
        raise MergeError("--out is the target; use --apply to change it")
    if any(real == os.path.realpath(p.expanduser()) for p in overlays):
        raise MergeError(f"--out {out} is one of the inputs; pick another file")
    if os.path.exists(real) and not force:
        raise MergeError(f"{out} already exists; pass --force to replace it "
                         "(a backup is kept)")


def parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="merge.py",
        description="Deep-merge settings overlays into a Claude Code settings file "
                    "(dry run by default).")
    ap.add_argument("overlays", nargs="*", type=Path, metavar="OVERLAY",
                    help="template/overlay JSON files, applied in order "
                         "(none: only apply --remove-key)")
    ap.add_argument("--target", type=Path, default=None,
                    help="settings file to merge into (default: ~/.claude/settings.json)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true",
                      help="write the target atomically after a timestamped backup")
    mode.add_argument("--out", type=Path, help="write the merged result to this file instead")
    ap.add_argument("--force", action="store_true",
                    help="with --out: replace an existing file (a timestamped backup is "
                         "kept); with --apply: write even if a {{DOTFILES}} path is missing")
    ap.add_argument("--override", action="store_true",
                    help="overlay scalars win every conflict (arrays are still unioned)")
    ap.add_argument("--override-key", action="append", default=[], metavar="PATH",
                    help="overlay wins at this path and below, arrays replaced whole "
                         "(repeatable). PATH: a.b.c, a\\.b for a dot in a key, or /a/b.c")
    ap.add_argument("--remove-key", action="append", default=[], metavar="PATH",
                    help="delete this key from the result, a whole list or object "
                         "included (repeatable)")
    ap.add_argument("--dotfiles", type=Path, default=REPO_ROOT,
                    help="value of {{DOTFILES}} (default: this repo's root)")
    ap.add_argument("--values", action="append", default=[], type=Path, metavar="FILE",
                    help="JSON object of {{NAME}} values for the overlays, kept out of git "
                         "(repeatable; a later file wins)")
    ap.add_argument("-q", "--quiet", action="store_true", help="no diff on stdout")
    a = ap.parse_args(argv)
    if not a.overlays and not a.remove_key:
        ap.error("give at least one OVERLAY, or --remove-key PATH")
    return a


def run(argv=None) -> int:
    # A settings file can hold text the console can't encode (a C locale, a
    # Windows code page): escape it in the diff rather than crash mid-way.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):  # not a text file, e.g. StringIO in tests
            pass
    a = parse_args(argv)
    target = (a.target or default_target()).expanduser()
    dotfiles = a.dotfiles.expanduser().resolve()
    # Forward slashes: Git Bash, which runs status line commands on Windows,
    # eats unquoted backslashes.
    variables = {"DOTFILES": dotfiles.as_posix()}
    try:
        override_keys = [parse_path(p) for p in a.override_key]
        remove = [parse_path(p) for p in a.remove_key]
        values = [p.expanduser() for p in a.values]
        if a.out:
            check_out(a.out.expanduser(), target, a.overlays + values, a.force)
        elif a.apply:
            check_writable(target, "target")
        for p in values:
            variables.update(load_values(p))
            if unignored(p, dotfiles):
                print(f"warning: values file {p} is in {dotfiles} and not gitignored; "
                      "`git add` would commit it", file=sys.stderr)
        existed = target.exists()
        current, old_text = load_json(target, missing_ok=True)
        overlays, named = [], []
        for p in a.overlays:
            data, _ = load_json(p.expanduser())
            # From the parsed data, so a JSON escape such as {{DOTFILES}}
            # counts too.
            named += dotfiles_paths(data, variables["DOTFILES"])
            overlays.append(substitute(data, variables, str(p)))
        uses_dotfiles = bool(named)
        # The templates put {{DOTFILES}} inside a double-quoted shell word.
        if uses_dotfiles and set(variables["DOTFILES"]) & set('"$`\\'):
            raise MergeError(f"{{{{DOTFILES}}}} is {variables['DOTFILES']}: a path with "
                             "\", $, ` or \\ would break the status line command; move the "
                             "checkout or pass --dotfiles")
        log: list = []
        override = None if a.override else override_keys
        merged = merge(current, combine(overlays), override, log)
        merged = remove_keys(merged, remove, log)
        changed = [p for act, p, _ in log if act in ("override", "replace", "remove")]
        missing = lost(current, merged, changed)
        if missing:
            raise MergeError("refusing to write: existing values would be lost at "
                             + ", ".join(sorted({fmt(p) for p in missing})[:10]))
        # {{DOTFILES}} paths the result keeps that don't exist: a status line
        # or hook that would point at nothing, e.g. a checkout without them yet.
        text = dumps(merged)
        absent = sorted({p for p in named if p in text and not os.path.exists(p)})
        if absent and a.apply and not a.force:
            raise MergeError("refusing to write: {{DOTFILES}} is " + variables["DOTFILES"]
                             + ", which has no " + ", ".join(absent) + ". Pass --dotfiles "
                             "<a checkout that has them>, or --force to write anyway")
        # Nothing changed: keep the file byte for byte, whatever its layout.
        # Compared as JSON text, because in Python True == 1 and 1 == 1.0.
        new_text = old_text if existed and text == dumps(current) else text
        print(f"merge into {target}:", file=sys.stderr)
        report(log, sys.stderr)
        if not log:
            print("  (overlays add nothing)", file=sys.stderr)
        for p in absent:
            print(f"warning: {p} doesn't exist; the setting that names it would point at "
                  "nothing", file=sys.stderr)
        if uses_dotfiles and linked_worktree(dotfiles) and (a.apply or a.out):
            print(f"warning: {{{{DOTFILES}}}} is {dotfiles}, a linked git worktree; the paths "
                  "break when it is removed. For a lasting --apply, run from the main "
                  "checkout after this branch has merged (so the files exist there), or "
                  "pass --dotfiles <main checkout>.", file=sys.stderr)
        if not a.quiet:
            sys.stdout.writelines(difflib.unified_diff(
                old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                fromfile=str(target), tofile=f"{target} (merged)"))
        if a.out:
            out = a.out.expanduser()
            bak = write_atomic(out, new_text, backup=True)
            print(f"wrote {out}; trial: claude --settings {out}", file=sys.stderr)
            if bak:
                print(f"backup of the file it replaced: {bak}", file=sys.stderr)
        elif a.apply:
            if new_text == old_text:
                print("no changes; target left as is", file=sys.stderr)
                return 0
            bak = write_atomic(target, new_text, old_text if existed else None, backup=True)
            print(f"applied to {target}", file=sys.stderr)
            if bak:
                print(f"backup: {bak}\nroll back: cp '{bak}' '{os.path.realpath(target)}'",
                      file=sys.stderr)
        else:
            print("dry run: nothing written (--apply to write, --out FILE for a trial file)",
                  file=sys.stderr)
        return 0
    except MergeError as e:
        print(f"merge.py: {e}", file=sys.stderr)
        return 2
    except RecursionError:  # parsed, but too deep for merging; nothing written yet
        print("merge.py: an input is nested too deeply to merge; nothing written",
              file=sys.stderr)
        return 2
    except OSError as e:  # e.g. a target folder we may not even stat
        print(f"merge.py: {e.filename or target}: {e.strerror or type(e).__name__}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(run())
