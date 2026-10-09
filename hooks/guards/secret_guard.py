#!/usr/bin/env python3
"""PreToolUse guard: block secret-shaped literals before they are written.

- Write / Edit / NotebookEdit: the new text is scanned. A finding is blocked
  when the target file is inside a git work tree (not gitignored, not under
  .git/) or under a configured vault dir (always). Elsewhere, and for values
  the file already holds, the write goes on.
- Bash / PowerShell / Monitor: a full credential literal in the command is
  blocked anywhere (it would land in shell history, process lists and logs).
  Short search patterns such as `grep 'sk-ant-'` are not credentials and pass.
- redact.py's `[REDACTED:<kind>]` placeholders: a Write / NotebookEdit that
  puts more of them into an existing file than it holds (or into a new file
  next to a same-stem file holding the real values: conf.yaml.new beside
  conf.yaml), a shell command carrying one (plain grep/rg/git grep/git log
  searches excepted), and a mv/cp/install/rsync/ln -f/dd, `>` redirection or
  tee of a file holding them over one that doesn't, are blocked anywhere.
  Claude saw those values redacted; writing the placeholder back would replace
  the real value.

The block reason names the kind and line, never the value. A block is a
`permissionDecision: "deny"` on stdout with exit 0 (exit 2 only if stdout
fails). Internal errors fail open (see common.py); only a detection blocks."""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

COMPONENT = "secret_guard"
FILE_TOOLS = {"Write": "content", "Edit": "new_string", "NotebookEdit": "new_source"}
SHELL_TOOLS = ("Bash", "PowerShell", "Monitor")
REWRITE_TOOLS = ("Write", "NotebookEdit")    # replace a whole file / cell
READ_CAP = 5_000_000         # bytes read back from a target file for the "already there" check
SIBLING_MAX = 2000           # directory entries looked at for a same-stem file
PLACEHOLDER = re.compile(r"\[REDACTED:([a-z0-9-]+)\]")   # what redact.py puts in tool output
# One plain search command (no chaining, pipes, redirection or substitution): it
# writes nothing, so a placeholder in its pattern can't replace anything.
SEARCH_ONLY = re.compile(r"[ \t]*(?:grep|egrep|fgrep|rg|ag|ack|git[ \t]+(?:grep|log))[ \t][^;&|<>`$\n]*")
MOVE = re.compile(r"(?:^|[\s;&|(])(?:mv|cp|install|rsync|ln|dd|tee)[ \t]|>")
HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
OPERATOR = frozenset(";&|()<>")
TRUNCATE = (">", ">|", "&>", ">&")          # redirections that replace the file (not >>, <>)


def _git_root(path: str):
    """The work tree root holding path (a .git dir or file), walking up from the
    nearest existing ancestor; None outside git."""
    d = os.path.dirname(path)
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _gitignored(root: str, path: str):
    """True when git positively says the path is ignored (and untracked), False
    when it says it isn't, None when git couldn't be run to ask."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", root, "check-ignore", "-q", "--", path],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=2,
                           env={k: v for k, v in os.environ.items()
                                if k != "GIT_LITERAL_PATHSPECS"})    # check-ignore refuses it
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(r.returncode)


def _read(path: str):
    """The file's text, or None when it is missing, unreadable or too big."""
    try:
        if os.path.getsize(path) > READ_CAP:
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _already_there(path: str, text: str, found) -> bool:
    """Every finding's literal is already in the file on disk (an edit that keeps
    an existing value, not one that adds it)."""
    current = _read(path)
    return current is not None and all(text[f.start:f.end] in current for f in found)


def _marks(text: str) -> dict:
    """{placeholder: its kind} for the placeholders in text, lines carrying the
    allow marker left out (docs or tests about redaction itself)."""
    import patterns
    if patterns.ALLOW_MARKER in text:
        text = "\n".join(ln for ln in text.split("\n") if patterns.ALLOW_MARKER not in ln)
    return {m.group(0): m.group(1) for m in PLACEHOLDER.finditer(text)}


def _loses(new: str, marks: dict, current: str, need_kind: bool = False) -> bool:
    """new holds more of some placeholder than current does: current holds real
    values there. With need_kind, current must also hold a value of that kind (a
    same-stem neighbour, not the file itself)."""
    more = [k for m, k in marks.items() if new.count(m) > current.count(m)]
    if not more or not need_kind:
        return bool(more)
    import patterns
    return bool(set(more) & {f.kind for f in patterns.scan(current, honor_marker=False)})


def _same_stem(path: str, new: str, marks: dict) -> str:
    """A file beside the new file path, with the same stem (conf.yaml for
    conf.yaml.new, conf.new.yaml or .conf.yaml.swp), that holds the real values
    the new file's placeholders stand for: write-a-copy-then-mv. Else ''."""
    d, base = os.path.split(path)
    stem = base.lstrip(".").split(".")[0]
    if not stem:
        return ""
    try:
        it = os.scandir(d or ".")
    except OSError:
        return ""
    with it:
        for n, e in enumerate(it):
            if n >= SIBLING_MAX:
                break
            if e.name == base or e.name.lstrip(".").split(".")[0] != stem:
                continue
            try:
                if not e.is_file():
                    continue
            except OSError:
                continue
            current = _read(e.path)
            if current is not None and _loses(new, marks, current, need_kind=True):
                return e.path
    return ""


def _placeholder_overwrite(ti: dict, text: str):
    """(file, why) when a Write / NotebookEdit would clobber real values with
    redact.py placeholders, else None: the new text holds more of some
    placeholder than the existing file does (those are values Claude saw
    redacted), or the file is new and a same-stem neighbour holds the values."""
    raw = ti.get("file_path") or ti.get("notebook_path")
    if not isinstance(raw, str) or not raw:
        return None
    marks = _marks(text)
    if not marks:
        return None
    path = os.path.realpath(os.path.expanduser(raw))
    current = _read(path)
    if current is not None:
        return (raw, "where the file holds the real values") if _loses(text, marks, current) else None
    if os.path.exists(path):
        return None                        # too big or unreadable: can't tell
    near = _same_stem(path, text, marks)
    return (raw, f"while {near} holds the real values (a copy moved over it would replace them)") \
        if near else None


def _without_heredocs(command: str) -> str:
    """command with heredoc bodies cut out: their lines are data, not words."""
    if "<<" not in command:
        return command
    lines, out, i = command.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in HEREDOC.finditer(line):
            if m.start() and line[m.start() - 1] == "<":
                continue                                  # <<< here-string
            tabs = m.group(1) == "-"
            while i < len(lines) and (lines[i].lstrip("\t") if tabs else lines[i]) != m.group(3):
                i += 1
            i += 1
    return "\n".join(out)


def _segments(command: str):
    """[(words, redirects, ends_pipeline)] for each simple command of a shell line;
    redirects are (operator, target) pairs. None when it can't be parsed."""
    import shlex
    try:
        line = _without_heredocs(command).replace("\\\n", " ").replace("\n", " ; ")
        lex = shlex.shlex(line, posix=True, punctuation_chars=";&|()<>")
        lex.whitespace_split = True
        lex.commenters = ""                      # shlex would end `url#frag` at the #
        toks = list(lex)
    except ValueError:
        return None
    out, words, redirs, i = [], [], [], 0
    toks.append(";")
    while i < len(toks):
        tok = toks[i]
        i += 1
        if tok and set(tok) <= OPERATOR:
            if ("<" in tok or ">" in tok) and "(" not in tok and ")" not in tok:
                if words and words[-1].isdigit():
                    words.pop()                           # the fd of 2>err
                target = toks[i] if not set(toks[i]) <= OPERATOR else ""
                i += 1 if target else 0
                if tok in ("<<", "<<-", "<<<") or (tok.endswith("&") and (target.isdigit() or target == "-")):
                    continue                              # heredoc tag, here-string, 2>&1
                redirs.append((tok, target))
                continue
            if words or redirs:
                out.append((words, redirs, tok not in ("|", "|&")))
            words, redirs = [], []
        else:
            words.append(tok)
    return out


def _target_dir(args):
    """(-t DIR / --target-directory=DIR or None, the other arguments)."""
    rest, target, i = [], None, 0
    while i < len(args):
        a = args[i]
        if a in ("-t", "--target-directory") and i + 1 < len(args):
            target, i = args[i + 1], i + 2
            continue
        if a.startswith("--target-directory="):
            target = a.split("=", 1)[1]
        elif a.startswith("-t") and len(a) > 2 and not a.startswith("--"):
            target = a[2:]
        else:
            rest.append(a)
        i += 1
    return target, rest


def _shell_moves(command: str, cwd: str):
    """(source, target, need_kind) for each file a command copies over another:
    mv / cp / install / rsync / ln -f / dd of=, and a `>` redirection or `tee`
    fed by a file read in the same pipeline (cat a > b, tee b < a). Relative names
    are resolved against cwd (and `cd` on the way). need_kind: the target is
    produced by an arbitrary program, so it must also hold a real value of the
    placeholder's kind. [] when the line can't be parsed."""
    segs = _segments(command)
    if not segs:
        return []
    out, sources, dests = [], [], []

    def path(p):
        return os.path.join(cwd, os.path.expanduser(p))

    for words, redirs, ends in segs:
        while words and (ASSIGN.match(words[0]) or words[0] == "sudo"):
            words = words[1:]
        word = os.path.basename(words[0]) if words else ""
        opts = [a for a in words[1:] if a.startswith("-")]
        args = [a for a in words[1:] if not a.startswith("-")]
        sources += [path(t) for op, t in redirs if op == "<" and t]
        dests += [path(t) for op, t in redirs if op in TRUNCATE and t]
        if word == "cd" and args:
            cwd = path(args[0])
        elif word in ("mv", "cp", "install", "rsync", "ln"):
            target, rest = _target_dir(words[1:]) if word != "rsync" else (None, words[1:])
            rest = [a for a in rest if not a.startswith("-")]
            force = any(o == "--force" or (not o.startswith("--") and "f" in o) for o in opts)
            if word == "ln" and not force:
                pass                                       # ln without -f refuses an existing name
            elif target is not None or len(rest) >= 2:
                dest = path(target if target is not None else rest[-1])
                no_dir = "-T" in opts or "--no-target-directory" in opts
                for src in (rest if target is not None else rest[:-1]):
                    s = path(src)
                    if word == "ln" and not os.path.isabs(os.path.expanduser(src)) and \
                            any(o == "--symbolic" or (not o.startswith("--") and "s" in o) for o in opts):
                        base = dest if os.path.isdir(dest) and not no_dir else os.path.dirname(dest)
                        s = os.path.join(base, src)        # a symlink's target is relative to the link
                    into = os.path.isdir(dest) and not no_dir
                    out.append((s, os.path.join(dest, os.path.basename(src)) if into else dest, False))
        elif word == "dd":
            ins = [path(a[3:]) for a in args if a.startswith("if=")]
            outs = [path(a[3:]) for a in args if a.startswith("of=")]
            out += [(s, d, False) for s in ins for d in outs]
        elif word == "tee":
            if not any(o in ("-a", "--append") or (not o.startswith("--") and "a" in o) for o in opts):
                dests += [path(a) for a in args]
        elif word:
            sources += [path(a) for a in args]
        if ends:
            out += [(s, d, True) for s in sources for d in dests if s != d]
            sources, dests = [], []
    return out


def _placeholder_move(command: str, cwd: str):
    """(source, target) of a copy that would put a file holding placeholders
    over one holding the real values, else None."""
    held = {}
    for src, dest, need_kind in _shell_moves(command, cwd):
        if not os.path.isfile(dest) or not os.path.isfile(src):
            continue
        if src not in held:
            new = _read(src)
            held[src] = (new, _marks(new)) if new and "[REDACTED:" in new else None
        if not held[src]:
            continue
        new, marks = held[src]
        current = _read(dest)
        if current is not None and _loses(new, marks, current, need_kind=need_kind):
            return src, dest
    return None


def _protection(path: str, cfg: dict, common) -> str:
    """Why writing a secret to path is refused ('' when it isn't)."""
    for v in common.vault_dirs(cfg):
        if common.under(path, v):
            return "a vault directory that is synced and must never hold secrets"
    root = _git_root(path)
    if root and not common.under(path, os.path.join(root, ".git")):    # .git/config is never committed
        ignored = _gitignored(root, path)
        if ignored is None:
            return (f"the git work tree {root} (git could not say whether the file is gitignored: "
                    f"is git on PATH? So it is treated as a file that can be committed)")
        if not ignored:
            return f"the git work tree {root} (the file is not gitignored, so it can be committed)"
    return ""


def _deny(common, reason: str, notice: str) -> int:
    return common.decide("deny", reason, notice)


_EDIT_AROUND = ("Change only the lines you need with Edit in the original file, keeping the "
                "redacted lines out of old_string and new_string.")


def _placeholders(tool: str, ti: dict, text: str, inp: dict, common) -> int:
    """Refuse writing redact.py's placeholders back over real values; None to go on."""
    if tool in SHELL_TOOLS:
        if "[REDACTED:" in text and PLACEHOLDER.search(text):
            if SEARCH_ONLY.fullmatch(text) or common.disabled(COMPONENT, common.config()):
                return None
            return _deny(common,
                         f"secret_guard: blocked a {tool} command holding [REDACTED:<kind>] "
                         f"placeholders from redacted tool output. Written into a file they "
                         f"replace the real values it holds; passed to a program they send the "
                         f"placeholder, not the value. {_EDIT_AROUND} To search for the "
                         f"placeholder text, run grep, rg or git grep on its own (no pipe, "
                         f"redirection or `;`), or leave out the brackets: grep -n 'REDACTED:' FILE.",
                         f"secret_guard blocked a {tool} command carrying redaction placeholders")
        if MOVE.search(text):
            moved = _placeholder_move(text, str(inp.get("cwd") or os.getcwd()))
            if moved and not common.disabled(COMPONENT, common.config()):
                src, dest = moved
                return _deny(common,
                             f"secret_guard: blocked a {tool} command that puts {src} over {dest}: "
                             f"{os.path.basename(src)} holds [REDACTED:<kind>] placeholders where "
                             f"{os.path.basename(dest)} holds the real values, which it would "
                             f"replace. {_EDIT_AROUND}",
                             f"secret_guard blocked a {tool} command: it would overwrite redacted "
                             f"values in {os.path.basename(dest)}")
        return None
    if tool in REWRITE_TOOLS and "[REDACTED:" in text:
        lost = _placeholder_overwrite(ti, text)
        if lost and not common.disabled(COMPONENT, common.config()):
            raw, why = lost
            return _deny(common,
                         f"secret_guard: blocked {tool} on {raw}: the new content holds "
                         f"[REDACTED:<kind>] placeholders from redacted tool output {why}. "
                         f"Writing it would replace them with the placeholder. {_EDIT_AROUND}",
                         f"secret_guard blocked {tool} on {os.path.basename(raw)}: it would "
                         f"overwrite redacted values")
    return None


def run(inp: dict, common) -> int:
    import patterns
    tool = inp.get("tool_name")
    ti = inp.get("tool_input")
    if not isinstance(ti, dict) or (tool not in FILE_TOOLS and tool not in SHELL_TOOLS):
        return 0
    text = ti.get("command") if tool in SHELL_TOOLS else ti.get(FILE_TOOLS[tool])
    if not isinstance(text, str) or not text:
        return 0
    rc = _placeholders(tool, ti, text, inp, common)
    if rc is not None:
        return rc
    found = patterns.scan(text)
    if not found:
        return 0
    cfg = common.config()
    if common.disabled(COMPONENT, cfg):
        return 0
    what = patterns.describe(text, found)
    fixture = (f" If this is a deliberately fake test fixture, put `{patterns.ALLOW_MARKER}` "
               f"on that line.")
    if tool in SHELL_TOOLS:
        return _deny(common,
                     f"secret_guard: blocked a {tool} command holding a credential literal "
                     f"({what}; the value is not shown). A key in a command lands in shell "
                     f"history, process lists and logs. Reference it from the environment "
                     f"(\"$NAME\") or read it from a secret file at run time instead." + fixture,
                     f"secret_guard blocked a {tool} command: {what}")
    raw = ti.get("file_path") or ti.get("notebook_path")
    if not isinstance(raw, str) or not raw:
        return 0
    path = os.path.realpath(os.path.expanduser(raw))
    why = _protection(path, cfg, common)
    if not why:
        return 0
    if _already_there(path, text, found):
        common.emit({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (f"secret_guard: this {tool} keeps secret-shaped values that "
                                  f"{raw} already holds ({what}). They were let through because "
                                  f"they are not new, but the file is in {why}; moving them to an "
                                  f"environment variable or a gitignored file is safer.")}})
        return 0
    return _deny(common,
                 f"secret_guard: blocked {tool} on {raw}: it would write a secret-shaped "
                 f"literal ({what}; the value is not shown) into {why}. Load the value at run "
                 f"time from an environment variable (os.environ['NAME'], process.env.NAME, "
                 f"\"$NAME\") or a gitignored secret file instead, and write only that "
                 f"reference." + fixture,
                 f"secret_guard blocked {tool} on {os.path.basename(raw)}: {what}")


def main() -> int:
    inp = None
    try:
        import common
    except Exception as e:
        print(f"guards/{COMPONENT}: cannot load helpers ({type(e).__name__}); not enforced",
              file=sys.stderr)
        return 0
    try:
        inp = common.read_payload()
        return run(inp, common) if inp is not None else 0
    except Exception as e:
        return common.fail_open(COMPONENT, e, inp)


if __name__ == "__main__":
    sys.exit(main())
