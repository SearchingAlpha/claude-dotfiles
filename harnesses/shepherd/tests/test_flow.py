"""End to end: init → start → commit → pr (fake gh) → merge on the remote → finish."""

import json
import os
import textwrap
import unittest

from support import Sandbox


class TestFlow(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.repo = self.sb.repo()
        self.a = self.sb.session("sess-a", cwd=self.repo)
        self.sb.ok(self.repo, "init", "--mode", "enforce")
        self.wt = self.sb.tmp / "proj-a"

    def tearDown(self):
        self.sb.cleanup()

    def start_and_commit(self):
        self.sb.ok(self.repo, "start", "a", "--goal", "Make a better", "--owns", "src/a.py",
                   env=self.a)
        p = self.sb.commit(self.wt, "src/a.py", "a = 2\n", "improve a", env=self.a)
        self.assertEqual(p.returncode, 0, p.stderr)

    def merge_on_remote(self, squash=True):
        """Someone merges the PR on the host: squash `a` into trunk and push."""
        other = self.sb.tmp / "other"
        self.sb.git(self.sb.tmp, "clone", "-q", str(self.sb.tmp / "proj-origin.git"), str(other))
        self.sb.git(other, "fetch", "-q", "origin", "a")
        if squash:
            self.sb.git(other, "merge", "-q", "--squash", "FETCH_HEAD")
            self.sb.git(other, "commit", "-qm", "Make a better (#7)")
        else:
            self.sb.git(other, "merge", "-q", "--no-edit", "FETCH_HEAD")
        self.sb.git(other, "push", "-q", "origin", "trunk")

    def test_full_cycle_with_squash_merge(self):
        self.start_and_commit()
        out = self.sb.ok(self.wt, "pr", env=self.a)
        self.assertIn("https://github.com/o/r/pull/7", out)
        create = [c for c in self.sb.gh_calls() if c[:2] == ["pr", "create"]][0]
        self.assertIn("--base", create)
        self.assertEqual(create[create.index("--base") + 1], "trunk")
        self.assertEqual(create[create.index("--repo") + 1], "o/proj")
        body = json.loads(self.sb.gh_state.read_text())["created_body"]
        self.assertIn("Make a better", body)
        self.assertIn("src/a.py", body)
        status = json.loads(self.sb.ok(self.repo, "status", "--json"))
        card = status["tracks"][0]
        self.assertEqual(card["status"], "review")
        self.assertEqual(card["review"]["id"], "7")

        # second pr after a new commit updates the same review
        self.sb.commit(self.wt, "src/a.py", "a = 3\n", "more a", env=self.a)
        self.sb.ok(self.wt, "pr", env=self.a)
        self.assertEqual(len([c for c in self.sb.gh_calls() if c[:2] == ["pr", "create"]]), 1)

        p = self.sb.cli(self.repo, "finish", "a", env=self.a)
        self.assertEqual(p.returncode, 1)
        self.assertIn("not merged", p.stderr)

        self.merge_on_remote(squash=True)
        self.sb.set_gh(view={"state": "MERGED", "isDraft": False, "reviewDecision": "APPROVED",
                             "statusCheckRollup": [], "url": "https://github.com/o/r/pull/7",
                             "comments": [], "reviewRequests": []})
        p = self.sb.cli(self.wt, "finish", "a", env=self.a)
        self.assertEqual(p.returncode, 1)
        self.assertIn("inside the track's worktree", p.stderr)
        out = self.sb.ok(self.repo, "finish", "a", env=self.a)
        self.assertIn("finished", out)
        self.assertFalse(self.wt.exists())
        self.assertNotIn("a", self.sb.git(self.repo, "branch", "--format=%(refname:short)").stdout.split())
        status = json.loads(self.sb.ok(self.repo, "status", "--json"))
        self.assertEqual(status["tracks"], [])

    def test_finish_detects_squash_merge_without_backend(self):
        self.start_and_commit()
        self.sb.git(self.wt, "push", "-q", "origin", "a", env=self.a)
        self.merge_on_remote(squash=True)
        out = self.sb.ok(self.repo, "finish", "a", env=self.a)
        self.assertIn("finished", out)

    def test_pr_preflight_refusals(self):
        self.sb.ok(self.repo, "start", "a", "--goal", "g", env=self.a)
        p = self.sb.cli(self.wt, "pr", env=self.a)
        self.assertIn("no commits", p.stderr)
        (self.wt / "src" / "a.py").write_text("dirty\n")
        p = self.sb.cli(self.wt, "pr", env=self.a)
        self.assertIn("uncommitted", p.stderr)
        self.sb.git(self.wt, "add", "src/a.py")
        self.sb.git(self.wt, "commit", "-qm", "x", env=self.a)
        # main moves on: the track is behind
        self.sb.commit(self.repo, "README.md", "moved\n", "human change")
        self.sb.git(self.repo, "push", "-q", "origin", "trunk")
        p = self.sb.cli(self.wt, "pr", env=self.a)
        self.assertIn("behind", p.stderr)
        self.assertIn("rebased", self.sb.ok(self.wt, "sync", env=self.a))
        # failing tests block the review
        cfg = self.repo / ".git" / "shepherd" / "config.json"
        c = json.loads(cfg.read_text())
        c["test_cmd"] = "exit 3"
        cfg.write_text(json.dumps(c))
        p = self.sb.cli(self.wt, "pr", env=self.a)
        self.assertIn("tests failed", p.stderr)
        self.assertEqual(self.sb.gh_calls(), [])

    def test_no_backend_for_unknown_host(self):
        repo = self.sb.repo("plain", github=False)
        self.sb.ok(repo, "init")
        self.sb.ok(repo, "start", "x", "--goal", "g", env=self.a)
        wt = self.sb.tmp / "plain-x"
        self.sb.commit(wt, "README.md", "x\n", "x", env=self.a)
        p = self.sb.cli(wt, "pr", env=self.a)
        self.assertEqual(p.returncode, 1)
        self.assertIn("no review backend for", p.stderr)

    def test_start_refuses_when_local_main_has_unpushed_commits(self):
        self.sb.commit(self.repo, "README.md", "local only\n", "unpushed")
        p = self.sb.cli(self.repo, "start", "a", "--goal", "g", env=self.a)
        self.assertEqual(p.returncode, 1)
        self.assertIn("not on origin/trunk", p.stderr)
        self.sb.ok(self.repo, "start", "a", "--goal", "g", "--from", "trunk", env=self.a)

    def test_adopt_backlog_branch_and_ledger_view(self):
        self.sb.commit(self.repo, "README.md", "local only\n", "unpushed")
        self.sb.git(self.repo, "branch", "backlog", "trunk")
        self.sb.ok(self.repo, "adopt", "--branch", "backlog", "--goal", "land the backlog")
        self.assertTrue((self.sb.tmp / "proj-backlog").is_dir())
        ledger = (self.repo / ".git" / "claude-sessions.md").read_text()
        self.assertIn("Generated by shepherd", ledger)
        self.assertIn("land the backlog", ledger)

    def test_legacy_ledger_is_kept(self):
        repo = self.sb.repo("legacy")
        (repo / ".git" / "claude-sessions.md").write_text("| Session | … |\nold notes\n")
        self.sb.ok(repo, "init")
        self.assertIn("old notes", (repo / ".git" / "claude-sessions.legacy.md").read_text())

    def test_log_inbox_accept(self):
        b = self.sb.session("sess-b", cwd=self.repo)
        self.sb.ok(self.repo, "start", "a", "--goal", "g", "--owns", "src/", env=self.a)
        self.sb.ok(self.repo, "start", "b", "--goal", "g", "--owns", "docs/", env=b)
        self.sb.ok(self.sb.tmp / "proj-b", "log", "--to", "a", "--type", "request",
                   "please", "export", "f()", env=b)
        inbox = json.loads(self.sb.ok(self.wt, "inbox", "--json", env=self.a))
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["from"], "b")
        self.sb.ok(self.wt, "accept", inbox[0]["id"], env=self.a)
        self.assertEqual(json.loads(self.sb.ok(self.wt, "inbox", "--json", env=self.a)), [])


class TestBackends(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.apply_env()

    def tearDown(self):
        self.sb.restore_env()
        self.sb.cleanup()

    def test_external_backend_loaded_first_and_broken_ones_reported(self):
        d = self.sb.tmp / "backends"
        d.mkdir()
        (d / "fake.py").write_text(textwrap.dedent('''
            SHEPHERD_API = 1
            class B:
                name = "fake"
                def matches(self, url): return "example.internal" in url or "github.com" in url
                def doctor(self): return [(True, "fake ok", "")]
            BACKEND = B()
        '''))
        (d / "broken.py").write_text("raise RuntimeError('boom')\n")
        os.environ["SHEPHERD_BACKENDS"] = str(d)
        from shep import review
        review.backends(reload=True)
        self.assertEqual(review.select("ssh://example.internal/x").name, "fake")
        self.assertEqual(review.select("https://github.com/a/b").name, "fake")
        self.assertEqual(review.select("https://github.com/a/b", {"review_backend": "github"}).name,
                         "github")
        self.assertIsNone(review.select("ssh://elsewhere/x"))
        self.assertTrue(any("boom" in e for e in review.load_errors))
        del os.environ["SHEPHERD_BACKENDS"]
        review.backends(reload=True)

    def test_github_status_mapping(self):
        from shep.backends import github
        self.assertEqual(github._checks([]), "none")
        self.assertEqual(github._checks([{"conclusion": "SUCCESS"}, {"conclusion": "SKIPPED"}]), "passing")
        self.assertEqual(github._checks([{"conclusion": "FAILURE"}]), "failing")
        self.assertEqual(github._checks([{"status": "IN_PROGRESS", "conclusion": ""}]), "pending")


if __name__ == "__main__":
    unittest.main()
