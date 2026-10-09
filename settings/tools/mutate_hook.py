"""Mutation check for the push hook's tests: each mutation of a copy of
allow_repo_push.py must make at least one test fail. The real file is never
touched; the copies live in a temp directory.

    python3 settings/tools/mutate_hook.py
"""
import importlib.util
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS = HERE.parent
HOOK = SETTINGS / "hooks" / "allow_repo_push.py"
TESTS = SETTINGS / "tests" / "test_allow_repo_push.py"

MUTATIONS = {
    "heredoc keeps trailing newlines": ('return command[start:pos].rstrip("\\n"), nl + 3',
                                        'return command[start:pos], nl + 3'),
    "double quotes ignore backslash": ("""if c == "\\\\" and i + 1 < n and command[i + 1] in '"\\\\$`\\n':""",
                                       """if False:"""),
    "commit bundles ignore unknown flags": ('raise NoDecision(f"git commit option -{ch}")', "continue"),
    "commit allows pathspecs": ('raise NoDecision(f"git commit option or pathspec {a}")', "pass"),
    "add allows directories": ("or os.path.isdir(full)", "or False"),
    "add allows options": ('raise NoDecision(f"git add option {a}")', "paths.append(a)"),
    "add allows magic": ('if not p or p.startswith(":") or', 'if not p or'),
    "push HEAD skips the HEAD check": ('        if src != "HEAD":\n            return\n', ""),
    "cd allows bare names": ('if not (d.startswith(("/", "./")) or d == ".") or ".." in d.split("/"):',
                             'if False:'),
    "lexer allows braces": ('PLAIN = set("_./:@%+,=-")', 'PLAIN = set("_./:@%+,=-{}")'),
    "lexer allows = first": ('(c in PLAIN and (c != "=" or any(buf)))', "(c in PLAIN)"),
    "lexer allows = after empty quotes": ('(c in PLAIN and (c != "=" or any(buf)))',
                                          '(c in PLAIN and (c != "=" or started))'),
    "lexer allows ~ anywhere": ('elif c == "~" and not started and', 'elif c == "~" and'),
    "heredoc ends at the last delimiter": (
        "        if line == delim:\n            break\n",
        "        if line == delim and command.startswith(')\"', nl + 1):\n            break\n"
        "        if line == delim:\n            pos = nl + 1\n            continue\n"),
    "heredoc allows lines starting with the delimiter": ("        if line.startswith(delim):\n",
                                                         "        if False:\n"),
    "heredoc allowed anywhere": ("    if word.heredoc:\n", "    if False:\n"),
    "remote push config ignored": ('if f"remote.{remote}.push" in cfg or', "if"),
    "@{push} not checked": ('    if git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{push}"],',
                            '    if False and git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{push}"],'),
    "nested repo allowed": ("if toplevel[where] != repo:", "if not toplevel[where].startswith(repo):"),
    # round 4: push URL pin, separators, pipes, reads
    "push URL not checked": ('    if git(["remote", "get-url", "--push", "--all", remote], where).splitlines() != [url]:',
                             "    if False:"),
    "push URL: first of several": (".splitlines() != [url]", ".splitlines()[:1] != [url]"),
    "write after ; allowed": ("if sub in WRITES and after_semicolon:", "if False:"),
    "cd before ; allowed": ('part.then != "&&"', 'part.then not in ("&&", ";")'),
    "cd may be piped": ('if part.stderr_to_stdout or part.pipe_to or part.then != "&&":',
                        'if part.then != "&&":'),
    "pipe into anything": ('    if args[0] not in ("head", "tail"):', "    if False:"),
    "head/tail args unchecked": ("    if not (rest == [] or", "    if False and (rest == [] or"),
    "log/diff flags unchecked": ('raise NoDecision(f"git {sub} option {a}")', "pass"),
    "%G allowed in log formats": (' and "%G" not in a', ""),
    "2>&1 anywhere in a part": ("if not seg or any(isinstance(t, Op) for t in seg):", "if not seg:"),
    "quoted | is a pipe": ('if isinstance(t, Op) and t == "|"', 'if t == "|"'),
    "|| lexed as a pipe": ('command[i + 1:i + 2] not in ("|", "&")', "True"),
    "2>&1 glued to a word": ("(not started and command.startswith(\"2>&1\", i)",
                             "(command.startswith(\"2>&1\", i)"),
    # round 5: implicit --no-index reads, upstream mapping, receivepack, submodules
    "log/diff paths outside the repo": ("            if not inside(a, where, repo):\n",
                                        "            if False:\n"),
    "outside check only before --": ("            if not inside(a, where, repo):\n",
                                     "            if not paths and not inside(a, where, repo):\n"),
    "inside() is a string prefix": ("    if full == repo or full.startswith(repo + os.sep):\n        return True\n",
                                    "    if full.startswith(repo):\n        return True\n"),
    "symlinked prefix for relative words too": ("    if not os.path.isabs(word):\n        return False\n", ""),
    "no symlinked prefix": ("    parts = full.split(os.sep)\n    return any(", "    parts = []\n    return any("),
    "upstream mapping ignored": ("    if upstream and merge != [", "    if False and merge != ["),
    "upstream: explicit dst also refused": ('and not (len(pos) == 2 and ":" in pos[1]):', ":"),
    "receivepack ignored": ('    if f"remote.{remote}.receivepack" in cfg:\n', "    if False:\n"),
    "push.recurseSubmodules ignored": ('    if recurse is None or recurse.lower() not in ("no", "check") + FALSE:',
                                       "    if False:"),
    "submodule.recurse ignored": ('recurse = "on-demand" if on is None or', 'recurse = "on-demand" if False and'),
}


def run_tests(hook_path: Path) -> unittest.TestResult:
    spec = importlib.util.spec_from_file_location("t_mut", TESTS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.HOOK = hook_path
    suite = unittest.defaultTestLoader.loadTestsFromModule(mod)
    return unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="mutants-"))
    src = HOOK.read_text()
    survived = []
    for name, (old, new) in MUTATIONS.items():
        if src.count(old) != 1:
            print(f"SKIP  {name}: pattern found {src.count(old)} times")
            survived.append(name)
            continue
        mutant = out / "allow_repo_push.py"
        mutant.write_text(src.replace(old, new))
        r = run_tests(mutant)
        bad = len(r.failures) + len(r.errors)
        print(f"{'caught' if bad else 'SURVIVED':8s} {name} ({bad} failing)")
        if not bad:
            survived.append(name)
    shutil.rmtree(out)
    clean = run_tests(HOOK)
    print("unmutated:", "OK" if clean.wasSuccessful() else "FAILED", f"({clean.testsRun} tests)")
    print(f"{len(MUTATIONS) - len(survived)} of {len(MUTATIONS)} mutations caught")
    return 1 if survived or not clean.wasSuccessful() else 0


if __name__ == "__main__":
    sys.exit(main())
