"""vault_commit.py: record on PostToolUse, one commit of exactly those paths on
Stop, background push. Real git repos with a bare remote."""

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
import unittest

from support import FAKE, GUARDS, Sandbox, post, stop


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.remote = self.sb.bare()
        self.vault = self.sb.repo("vault", remote=self.remote)
        self.push(False)            # tests that check pushing turn it on and wait for it

    def push(self, on, **extra):
        self.sb.config({"vault_commit": dict({"path": str(self.vault), "push": on}, **extra)})

    def tearDown(self):
        self.sb.cleanup()

    def edit(self, rel, text, sid="sess-1", tool="Write"):
        p = self.vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        rc, out, _ = self.sb.run("vault_commit.py", post(tool, {key: str(p)}, {}, sid=sid))
        self.assertEqual((rc, out), (0, ""))

    def stop(self, sid="sess-1"):
        rc, out, err = self.sb.run("vault_commit.py", stop(sid))
        self.assertEqual(rc, 0, err)
        return out

    def log(self, *args):
        return self.sb.git(self.vault, "log", *args).stdout

    def commits(self):
        return self.log("--oneline").count("\n")

    def committed(self):
        out = self.sb.git(self.vault, "show", "--name-status", "-z", "--format=", "HEAD").stdout
        f = [x for x in out.strip("\n").split("\0") if x]
        return sorted(f"{a}\t{b}" for a, b in zip(f[::2], f[1::2]))

    def status(self):
        """`git status --porcelain` lines with unquoted names."""
        out = self.sb.git(self.vault, "status", "--porcelain", "-z").stdout
        return "".join(e + "\n" for e in out.split("\0") if e)

    def remote_head(self):
        return self.sb.git(self.remote, "rev-parse", "trunk").stdout.strip()

    def wait_pushed(self, timeout=15):
        head = self.sb.git(self.vault, "rev-parse", "HEAD").stdout.strip()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.remote_head() == head:
                return True
            time.sleep(0.1)
        return False

    def push_log(self):
        p = self.sb.state / "vault-push.log"
        return p.read_text() if p.exists() else ""

    def pending(self, sid="sess-1"):
        p = self.sb.state / "vault-pending" / f"{sid}.jsonl"
        return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


class TestVaultCommit(VaultCase):
    def test_commits_exactly_the_edited_paths_once_per_turn(self):
        self.push(True)
        (self.vault / "user-wip.md").write_text("not mine\n")           # untracked, not edited by Claude
        (self.vault / "staged.md").write_text("user staged\n")
        self.sb.git(self.vault, "add", "staged.md")
        self.edit("notes/today.md", "- [ ] one\n")
        self.edit("notes/ideas.md", "hello\n")
        self.edit("notes/today.md", "- [ ] one\n- [ ] two\n")           # same file twice
        before = self.commits()
        self.assertEqual(self.stop(), "")
        self.assertEqual(self.commits(), before + 1)
        self.assertEqual(self.committed(), ["A\tnotes/ideas.md", "A\tnotes/today.md"])
        status = self.status()
        self.assertIn("?? user-wip.md", status)
        self.assertIn("A  staged.md", status)                             # still staged, not committed
        msg = self.log("-1", "--format=%B")
        self.assertTrue(msg.startswith("vault: update 2 files (ideas.md, today.md)"), msg)
        self.assertTrue(self.wait_pushed(), self.push_log())
        self.assertIn("ok: committed 2 path(s)", self.push_log())
        self.assertIn("ok: pushed trunk to origin", self.push_log())
        self.assertEqual(self.stop(), "")                                 # nothing pending: no-op
        self.assertEqual(self.commits(), before + 1)

    def test_names_are_literal_never_globs_or_pathspec_magic(self):
        for name in ("Meeting 1.md", "Meeting 2.md", "notes/a.md", "notes/b.md", "keep.md", "a"):
            (self.vault / name).parent.mkdir(parents=True, exist_ok=True)
            (self.vault / name).write_text("base\n")
        self.sb.git(self.vault, "add", ".")
        self.sb.git(self.vault, "commit", "-q", "-m", "base")
        for name in ("Meeting 1.md", "notes/a.md", "notes/b.md", "keep.md", "a"):
            (self.vault / name).write_text("the user's own unsaved work\n")
        for i, name in enumerate(("Meeting [12].md", "notes/*.md", ":!x.md", "a\nb.md", "qu?.md")):
            with self.subTest(repr(name)):
                self.edit(name, f"claude {i}\n")
                self.stop()
                self.assertEqual(self.committed(), [f"A\t{name}"])
        status = self.status()
        for name in ("Meeting 1.md", "notes/a.md", "notes/b.md", "keep.md", "a"):
            self.assertIn(f" M {name}", status)                       # the user's work stays put

    def test_inert_without_config(self):
        (self.sb.state / "config.json").unlink()
        self.edit("a.md", "x\n")
        self.assertEqual(self.stop(), "")
        self.assertFalse((self.sb.state / "vault-pending").exists())
        self.assertEqual(self.commits(), 1)

    def test_env_turns_it_on_with_defaults(self):
        (self.sb.state / "config.json").unlink()
        env = dict(self.sb.env, CLAUDE_GUARDS_VAULT=str(self.vault))
        (self.vault / "e.md").write_text("e\n")
        self.sb.run("vault_commit.py", post("Edit", {"file_path": str(self.vault / "e.md")}, {}), env=env)
        self.sb.run("vault_commit.py", stop(), env=env)
        self.assertEqual(self.committed(), ["A\te.md"])
        self.assertTrue(self.wait_pushed(), self.push_log())          # push is on by default

    def test_push_false_as_a_string_is_off(self):
        self.push("false")
        self.edit("a.md", "a\n")
        self.stop()
        self.assertEqual(self.committed(), ["A\ta.md"])
        self.assertIn("(push is off)", self.push_log())
        time.sleep(1)
        self.assertNotIn("pushed", self.push_log())

    def test_ignores_paths_outside_the_vault_and_in_git_dir(self):
        other = self.sb.repo("other")
        (other / "o.md").write_text("o\n")
        self.sb.run("vault_commit.py", post("Write", {"file_path": str(other / "o.md")}, {}))
        self.sb.run("vault_commit.py", post("Write", {"file_path": str(self.vault / ".git" / "x")}, {}))
        self.assertFalse((self.sb.state / "vault-pending").exists())

    def test_bash_edit_diff_and_deletions(self):
        (self.vault / "gone.md").write_text("bye\n")
        self.sb.git(self.vault, "add", "gone.md")
        self.sb.git(self.vault, "commit", "-q", "-m", "add gone")
        (self.vault / "gone.md").unlink()
        (self.vault / "new.md").write_text("n\n")
        diff = {"changedFiles": [str(self.vault / "gone.md"), str(self.vault / "new.md")],
                "files": [], "moreFiles": 0}
        self.sb.run("vault_commit.py", post("Bash", {"command": "mv"}, {"stdout": "", "bashEditDiff": diff}))
        shared = dict(diff, changedFiles=[str(self.vault / "shared.md")], shared=True)
        (self.vault / "shared.md").write_text("s\n")
        self.sb.run("vault_commit.py", post("Bash", {"command": "x"}, {"stdout": "", "bashEditDiff": shared}))
        self.stop()
        self.assertEqual(self.committed(), ["A\tnew.md", "D\tgone.md"])

    def test_created_and_deleted_in_one_turn_and_unchanged_files(self):
        self.edit("tmp-note.md", "scratch\n")
        (self.vault / "tmp-note.md").unlink()
        self.edit("README.md", "# test\n")                              # rewritten, same content
        self.edit("kept.md", "k\n")
        self.stop()
        self.assertEqual(self.committed(), ["A\tkept.md"])
        self.assertIn("ok: committed 1 path(s)", self.push_log())
        self.assertIn("(push is off)", self.push_log())

    def test_gitignored_paths_are_skipped(self):
        (self.vault / ".gitignore").write_text("*.tmp\n")
        self.edit("scratch.tmp", "t\n")
        self.edit("brouillon é.tmp", "t\n")                             # git quotes this one without -z
        self.edit("real.md", "r\n")
        self.stop()
        self.assertEqual(self.committed(), ["A\treal.md"])

    def test_other_sessions_lists_wait_unless_orphaned(self):
        self.edit("a.md", "a\n", sid="sess-a")
        self.edit("b.md", "b\n", sid="sess-b")
        self.stop("sess-a")
        self.assertEqual(self.committed(), ["A\ta.md"])
        old = time.time() - 3 * 3600
        os.utime(self.sb.state / "vault-pending" / "sess-b.jsonl", (old, old))
        self.edit("c.md", "c\n", sid="sess-a")
        self.stop("sess-a")
        self.assertEqual(self.committed(), ["A\tb.md", "A\tc.md"])


class TestSweep(VaultCase):
    """`sweep: true`: files changed through Bash without a bashEditDiff are committed too."""

    def bash(self, sid="sess-1", duration_ms=50):
        payload = post("Bash", {"command": "python3 tidy.py"}, {"stdout": "", "stderr": ""}, sid=sid)
        payload["duration_ms"] = duration_ms
        rc, out, _ = self.sb.run("vault_commit.py", payload)
        self.assertEqual((rc, out), (0, ""))

    def test_sweeps_changes_from_this_session_window_only(self):
        (self.vault / "old.md").write_text("tracked\n")
        self.sb.git(self.vault, "add", "old.md")
        self.sb.git(self.vault, "commit", "-q", "-m", "old")
        (self.vault / "user-wip.md").write_text("from before the session\n")
        past = time.time() - 600
        os.utime(self.vault / "user-wip.md", (past, past))
        self.push(False, sweep=True)
        (self.vault / "nested").mkdir()
        self.sb.git(self.vault / "nested", "init", "-q")                     # an embedded repo
        (self.vault / "notes").mkdir()
        (self.vault / "notes" / "by-bash.md").write_text("written by a script\n")
        (self.vault / "old.md").write_text("tracked, edited by a script\n")
        self.bash()
        self.stop()
        self.assertEqual(self.committed(), ["A\tnotes/by-bash.md", "M\told.md"])
        self.assertIn("?? user-wip.md", self.status())
        self.assertIn("?? nested/", self.status())
        (self.vault / "old.md").unlink()                                     # an unrecorded delete
        (self.vault / "next.md").write_text("next turn\n")
        self.bash()
        self.stop()
        self.assertEqual(self.committed(), ["A\tnext.md"])
        self.assertIn(" D old.md", self.status())

    def test_sweep_of_a_vault_that_is_a_subdirectory(self):
        notes = self.vault / "notes"
        notes.mkdir()
        (notes / "b.md").write_text("tracked\n")
        self.sb.git(self.vault, "add", "notes/b.md")
        self.sb.git(self.vault, "commit", "-q", "-m", "b")
        self.sb.config({"vault_commit": {"path": str(notes), "push": False, "sweep": True}})
        self.bash()
        (notes / "b.md").write_text("edited by a script\n")
        (notes / "new.md").write_text("new\n")
        (self.vault / "outside.md").write_text("not in the vault\n")       # same repo, outside it
        self.stop()
        self.assertEqual(self.committed(), ["A\tnotes/new.md", "M\tnotes/b.md"])
        self.assertIn("?? outside.md", self.status())

    def test_off_by_default(self):
        (self.vault / "by-bash.md").write_text("x\n")
        self.bash()
        self.stop()
        self.assertEqual(self.commits(), 1)


class TestVaultFailures(VaultCase):
    def test_busy_repo_keeps_the_paths_for_next_turn(self):
        self.edit("a.md", "a\n")
        merge_head = self.vault / ".git" / "MERGE_HEAD"
        merge_head.write_text(self.sb.git(self.vault, "rev-parse", "HEAD").stdout)
        self.stop()
        self.assertEqual(self.commits(), 1)
        self.assertIn("busy (MERGE_HEAD)", self.push_log())
        merge_head.unlink()
        self.stop()
        self.assertEqual(self.committed(), ["A\ta.md"])

    def test_lock_held_elsewhere(self):
        self.edit("a.md", "a\n")
        with open(self.vault / ".git" / "claude-autocommit.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            self.stop()
            self.assertEqual(self.commits(), 1)
        self.assertIn("holds the lock", self.push_log())
        self.stop()
        self.assertEqual(self.committed(), ["A\ta.md"])

    def test_push_failure_is_logged_and_never_forced(self):
        self.push(True)
        self.sb.git(self.vault, "remote", "set-url", "origin", str(self.sb.tmp / "missing.git"))
        self.edit("a.md", "a\n")
        self.stop()
        self.assertEqual(self.committed(), ["A\ta.md"])
        deadline = time.time() + 15
        while "fail: push" not in self.push_log() and time.time() < deadline:
            time.sleep(0.1)
        self.assertIn("fail: push trunk to origin", self.push_log())

    def test_vault_on_another_branch_than_configured_is_left_alone(self):
        self.push(True, branch="trunk")
        self.sb.git(self.vault, "checkout", "-q", "-b", "draft")
        self.edit("a.md", "a\n")
        out = self.stop()
        self.assertIn("on branch draft", json.loads(out)["systemMessage"])
        self.assertEqual(self.commits(), 1)                               # nothing on draft
        self.assertIn("skip:", self.push_log())
        self.assertIn("config says trunk", self.push_log())
        self.assertNotIn("ok:", self.push_log())
        self.assertIn(str(self.vault / "a.md"), self.pending())
        self.assertEqual(self.stop(), "")                                 # one notice per session
        self.sb.git(self.vault, "checkout", "-q", "trunk")
        self.stop()
        self.assertEqual(self.committed(), ["A\ta.md"])
        self.assertTrue(self.wait_pushed(), self.push_log())

    def test_rejected_push_is_not_rebased(self):
        self.push(True)
        clone = self.sb.tmp / "clone"
        self.sb.git(self.sb.tmp, "clone", "-q", str(self.remote), str(clone))
        (clone / "x.md").write_text("x\n")
        self.sb.git(clone, "add", "x.md")
        self.sb.git(clone, "commit", "-q", "-m", "elsewhere")
        self.sb.git(clone, "push", "-q", "origin", "trunk")
        self.edit("a.md", "a\n")
        self.stop()
        deadline = time.time() + 15
        while "fail: push" not in self.push_log() and time.time() < deadline:
            time.sleep(0.1)
        self.assertIn("remote moved on", self.push_log())
        self.assertNotIn("x.md", self.sb.git(self.vault, "ls-files").stdout)    # nothing pulled

    def test_pre_commit_refusal_is_reported(self):
        rc = subprocess.run([sys.executable, "-I", str(GUARDS / "precommit_scan.py"), "install",
                             str(self.vault)], capture_output=True, text=True, env=self.sb.env)
        self.assertEqual(rc.returncode, 0, rc.stderr)
        k = FAKE.anthropic
        self.edit("leak.md", f"key {k}\n")
        out = self.stop()
        self.assertIn("did not complete", json.loads(out)["systemMessage"])
        self.assertEqual(self.commits(), 1)
        self.assertIn("kept for next turn", self.push_log())
        self.assertNotIn(k, self.push_log())
        self.assertIn(str(self.vault / "leak.md"), self.pending())

    def test_slow_pre_commit_hook_finishes_and_pushes_in_the_background(self):
        self.push(True)
        hook = self.vault / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nsleep 4\nexit 0\n")
        hook.chmod(0o755)
        self.edit("n.md", "note\n")
        t = time.time()
        out = self.stop()
        self.assertLess(time.time() - t, 4.5)
        self.assertIn("still running", json.loads(out)["systemMessage"])
        self.assertIn("finishes in the background and then pushes", self.push_log())
        self.assertIn(str(self.vault / "n.md"), self.pending())             # re-checked next turn
        deadline = time.time() + 20
        while self.commits() < 2 and time.time() < deadline:
            time.sleep(0.2)
        self.assertEqual(self.committed(), ["A\tn.md"])
        self.assertTrue(self.wait_pushed(timeout=20), self.push_log())
        hook.write_text("#!/bin/sh\nexit 0\n")
        self.stop()                                                          # already committed
        self.assertIn("nothing changed", self.push_log())
        self.assertEqual(self.pending(), [])

    def test_async_recorder_errors_leave_the_notice_for_stop(self):
        broken = self.sb.tmp / "broken"
        broken.mkdir()
        for name in ("vault_commit.py", "patterns.py"):
            shutil.copy(GUARDS / name, broken / name)
        text = (GUARDS / "common.py").read_text()
        (broken / "common.py").write_text(text + "\n\ndef vault_commit_cfg(cfg):\n    raise RuntimeError('boom')\n")
        rc, out, err = self.sb.run("vault_commit.py", post("Write", {"file_path": "/x"}, {}),
                                   script_dir=broken)
        self.assertEqual((rc, out), (0, ""))                  # async: a notice would go to Claude
        self.assertIn("internal error", err)
        rc, out, _ = self.sb.run("vault_commit.py", stop(), script_dir=broken)
        self.assertIn("systemMessage", out)                   # the first sync call tells you


if __name__ == "__main__":
    unittest.main()
