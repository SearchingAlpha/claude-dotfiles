import unittest

from support import ROOT  # noqa: F401  (puts shep on sys.path)
from shep import policy

PROT = ["trunk"]

BASH = [
    # (command, current branch, denied?)
    ("git status", "trunk", False),
    ("git add src/a.py tests/test_a.py", "x", False),
    ("git add -A", "x", True),
    ("git add --all", "x", True),
    ("git add .", "x", True),
    ("git add -u", "x", True),
    ("cd sub && git add .", "x", True),
    ("git -C ../other add -A", "x", True),
    ("git commit -m 'fix'", "x", False),
    ("git commit -m '-a looks like a flag'", "x", False),
    ("git commit -am 'wip'", "x", True),
    ("git commit -a -m wip", "x", True),
    ("git commit --all -m wip", "x", True),
    ("git commit -m msg --no-verify", "x", True),
    ("git commit -nm msg", "x", True),
    ("git stash", "x", True),
    ("git stash push -m x", "x", True),
    ("git stash pop", "x", True),
    ("git stash list", "x", False),
    ("git stash show -p", "x", False),
    ("git reset --hard HEAD~1", "x", True),
    ("git reset --soft HEAD~1", "x", False),
    ("git clean -fdx", "x", True),
    ("git clean -n", "x", False),
    ("git checkout -- .", "x", True),
    ("git checkout -- src/a.py", "x", False),
    ("git checkout -b feature", "x", False),
    ("git restore .", "x", True),
    ("git restore --staged .", "x", False),
    ("git restore src/a.py", "x", False),
    ("git push", "trunk", True),
    ("git push", "feature", False),
    ("git push origin trunk", "feature", True),
    ("git push origin HEAD:trunk", "feature", True),
    ("git push origin HEAD:refs/heads/trunk", "feature", True),
    ("git push origin +feature:trunk", "feature", True),
    ("git push -u origin feature", "feature", False),
    ("git push --all", "feature", True),
    ("git -c core.hooksPath=/dev/null commit -m x", "x", True),
    ("git config core.hooksPath /tmp/x", "x", True),
    ("SHEPHERD_ALLOW_MAIN=1 git commit -m x", "trunk", True),
    ("export SHEPHERD_ALLOW_MAIN=1", "trunk", True),
    ("echo git add -A", "x", False),
    ("ls && git log --oneline | head", "x", False),
    ("make test; git diff --stat", "x", False),
]


class TestBash(unittest.TestCase):
    def test_table(self):
        for cmd, branch, denied in BASH:
            with self.subTest(cmd=cmd, branch=branch):
                why = policy.classify_bash(cmd, PROT, branch)
                self.assertEqual(bool(why), denied, f"{cmd!r} → {why!r}")

    def test_git_c_and_cd_resolve_directories(self):
        calls, _ = policy.git_calls("cd /repo && git -C sub status")
        self.assertEqual(calls[0].cwd, "/repo/sub")


class TestOwns(unittest.TestCase):
    def test_match(self):
        self.assertEqual(policy.owns_match("src/a.py", ["src/"]), "src/")
        self.assertEqual(policy.owns_match("src/a.py", ["src"]), "src")
        self.assertEqual(policy.owns_match("src/a.py", ["*.py"]), "*.py")
        self.assertIsNone(policy.owns_match("srcx/a.py", ["src"]))
        self.assertIsNone(policy.owns_match("docs/a.md", ["src/", "tests/"]))

    def test_overlap(self):
        self.assertTrue(policy.owns_overlap(["src/"], ["src/a.py"]))
        self.assertTrue(policy.owns_overlap(["src/a.py"], ["src"]))
        self.assertFalse(policy.owns_overlap(["src/"], ["docs/"]))


def edit(**kw):
    base = dict(mode="enforce", rel="src/a.py", branch="feature", protected=False, here=None,
                here_owner_live=False, me_sid="me", others=[], recent_by_others=[],
                cmd_hint="shepherd")
    base.update(kw)
    return policy.decide_edit(**base)


class TestDecideEdit(unittest.TestCase):
    def test_protected_enforce_denies(self):
        d = edit(protected=True, branch="trunk")
        self.assertIn("protected", d.deny)
        self.assertIn("shepherd start", d.deny)

    def test_protected_observe_warns(self):
        d = edit(protected=True, branch="trunk", mode="observe")
        self.assertIsNone(d.deny)
        self.assertTrue(any("observe" in w for w in d.warnings))

    def test_other_live_owner_denies(self):
        t = {"name": "a", "agent": {"session_id": "other"}}
        self.assertIn("belongs to track `a`", edit(here=t, here_owner_live=True).deny)

    def test_other_dead_owner_warns_resume(self):
        t = {"name": "a", "agent": {"session_id": "other"}}
        d = edit(here=t, here_owner_live=False)
        self.assertIsNone(d.deny)
        self.assertTrue(any("resume a" in w for w in d.warnings))

    def test_own_track_is_silent(self):
        t = {"name": "a", "agent": {"session_id": "me"}}
        d = edit(here=t)
        self.assertEqual((d.deny, d.warnings), (None, []))

    def test_untracked_branch_suggests_adopt(self):
        self.assertTrue(any("adopt" in w for w in edit().warnings))

    def test_overlap_warnings(self):
        t = {"name": "a", "agent": {"session_id": "me"}}
        others = [{"name": "b", "owns": ["src/"], "changed": [], "owner": "sess-b"},
                  {"name": "c", "owns": [], "changed": ["src/a.py"], "owner": "sess-c"}]
        d = edit(here=t, others=others, recent_by_others=[{"who": "sess-d"}])
        text = "\n".join(d.warnings)
        self.assertIsNone(d.deny)
        self.assertIn("owned by track `b`", text)
        self.assertIn("also changed on track `c`", text)
        self.assertIn("sess-d", text)


if __name__ == "__main__":
    unittest.main()
