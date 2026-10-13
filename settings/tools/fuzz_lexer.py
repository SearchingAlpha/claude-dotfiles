"""Differential fuzz of the push hook's lexer against bash and zsh.

Random commands built from shell-significant pieces; for every one the lexer
accepts (and whose commands are all git, cd, echo, head or tail), the words
must equal what bash and zsh pass. The shells run with no rc files, PATH
holding only cat, and those five defined as functions that print their
arguments to fd 3 in one printf, in a temp directory. Pipeline members run
concurrently, so both sides are compared sorted.

    python3 settings/tools/fuzz_lexer.py [N] [SEED]

A shell that isn't installed (often zsh) is left out.
"""
import importlib.util
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "allow_repo_push.py"
spec = importlib.util.spec_from_file_location("allow_repo_push", HOOK)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

PIECES = ["git", "cd", " ", " ", " ", "a", "b", "-m", "~", "~/", "/", ".", "=", "%", "+", ",", "@",
          ":", "'", "'", '"', '"', "\\", "\n", "\t", "&&", "&&", "&", "$", "`", "(", ")", "{", "}",
          "*", "?", "[", "]", "#", "!", ";", "|", "<", ">", "\"$(cat <<'EOF'\n", "\nEOF\n)\"",
          "EOF", "\n)\"", '\\"', "'\\''", "é", "x y", "--message=", "^", "-", "0",
          "2>&1", " 2>&1", "2>&1 ", "| tail -2", "|head", " | ", "; git ", ";", "echo ", "-n", "3"]
PRELUDE = "exec 3>&1; " + "".join(
    f"{f}() {{ printf '%s\\0' {f} \"$@\" $'\\1' >&3; }}; " for f in ("git", "cd", "echo", "head", "tail"))


def shell_words(exe, flags, command, env, cwd):
    r = subprocess.run([exe, *flags, "-c", PRELUDE + command], capture_output=True, timeout=10,
                       env=env, cwd=cwd)
    out = r.stdout.decode(errors="replace")
    return r.returncode, sorted(p.split("\0")[:-1] for p in out.split("\1\0")[:-1])


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    rng = random.Random(int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    work = tempfile.mkdtemp(prefix="fuzz-lexer-")
    bindir = os.path.join(work, "bin")
    os.mkdir(bindir)
    os.symlink(shutil.which("cat"), os.path.join(bindir, "cat"))
    env = {"PATH": bindir, "HOME": os.environ["HOME"]}
    shells = [(exe, flags) for exe, flags in ((shutil.which("bash"), ["--norc", "--noprofile"]),
                                              (shutil.which("zsh"), ["-f"])) if exe]
    if not shells:
        print("neither bash nor zsh on PATH")
        return 2
    accepted = compared = 0
    bad = []
    def pieces(k):
        return "".join(rng.choice(PIECES) for _ in range(rng.randint(0, k)))

    for i in range(n):
        if i % 2:
            cmd = "git " + pieces(12) or "git"
        else:  # the heredoc form with a random body and tail
            body = "\n".join(rng.choice(["EOF", " EOF", "EOF ", ")\"", "", ")", "EOF)" + pieces(3),
                                         "x)" + pieces(3), pieces(6)])
                             for _ in range(rng.randint(0, 4)))
            cmd = f"git commit -m \"$(cat <<'EOF'\n{body}\nEOF\n)\"{pieces(5)}"
        try:
            parts = hook.split_parts(hook.lex(cmd))
        except hook.NoDecision:
            continue
        accepted += 1
        cmds = [p.words for p in parts] + [p.pipe_to for p in parts if p.pipe_to]
        if any(c[0] not in ("git", "cd", "echo", "head", "tail") for c in cmds):
            continue
        mine = sorted([str(w) for w in c] for c in cmds)
        compared += 1
        for exe, flags in shells:
            rc, theirs = shell_words(exe, flags, cmd, env, work)
            if rc != 0 or theirs != mine:
                bad.append((os.path.basename(exe), cmd, mine, rc, theirs))
    shutil.rmtree(work, ignore_errors=True)
    names = " and ".join(os.path.basename(exe) for exe, _ in shells)
    print(f"{n} random commands, {accepted} accepted by the lexer, {compared} compared with {names}")
    for shell, cmd, mine, rc, theirs in bad[:20]:
        print(f"MISMATCH {shell}: {cmd!r}\n  lexer: {mine}\n  shell rc {rc}: {theirs}")
    print("mismatches:", len(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
