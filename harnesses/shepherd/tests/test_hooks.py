"""Claude Code hooks and git hooks, run through the real entry point."""

import json
import os
import subprocess
import sys
import unittest

from support import ENTRY, Sandbox


class HookCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.repo = self.sb.repo()
        self.a = self.sb.session("sess-a", cwd=self.repo)
        self.b = self.sb.session("sess-b", cwd=self.repo)

    def tearDown(self):
        self.sb.cleanup()

    def enroll(self, mode="enforce"):
        self.sb.ok(self.repo, "init", "--mode", mode)

    def edit(self, env, path, tool="Edit"):
        return self.sb.hook("pre-edit", {"session_id": env["CLAUDE_CODE_SESSION_ID"],
                                         "cwd": str(self.repo), "tool_name": tool,
                                         "tool_input": {"file_path": str(path)}}, env)

    def bash(self, env, cmd, cwd=None):
        return self.sb.hook("pre-bash", {"session_id": env["CLAUDE_CODE_SESSION_ID"],
                                         "cwd": str(cwd or self.repo), "tool_name": "Bash",
                                         "tool_input": {"command": cmd}}, env)


class TestClaudeHooks(HookCase):
    def test_inert_outside_enrolled_repos(self):
        rc, out = self.edit(self.a, self.repo / "src" / "a.py")
        self.assertEqual((rc, out), (0, ""))
        rc, out = self.bash(self.a, "git add -A")
        self.assertEqual((rc, out), (0, ""))
        rc, out = self.sb.hook("pre-edit", {"tool_input": {"file_path": "/etc/hosts"}}, self.a)
        self.assertEqual((rc, out), (0, ""))
        self.assertFalse((self.repo / ".git" / "shepherd").exists())

    def test_garbage_never_blocks(self):
        for payload in ("", "not json", "[1,2]", '{"tool_input": 5}'):
            p = subprocess.run([sys.executable, str(ENTRY), "hook", "pre-edit"], input=payload,
                               capture_output=True, text=True, env=self.a)
            self.assertEqual(p.returncode, 0)
        p = subprocess.run([sys.executable, str(ENTRY), "hook"], input="{}",
                           capture_output=True, text=True, env=self.a)
        self.assertEqual(p.returncode, 0)

    def test_deny_on_protected_branch(self):
        self.enroll()
        rc, out = self.edit(self.a, self.repo / "src" / "a.py")
        self.assertEqual(rc, 0)
        o = json.loads(out)
        hso = o["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "PreToolUse")
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn("shepherd start", hso["permissionDecisionReason"])

    def test_observe_mode_warns_only(self):
        self.enroll("observe")
        rc, out = self.edit(self.a, self.repo / "src" / "a.py")
        o = json.loads(out)
        self.assertNotIn("permissionDecision", o["hookSpecificOutput"])
        self.assertIn("observe", o["hookSpecificOutput"]["additionalContext"])

    def test_track_flow_permissions_and_overlap_warnings(self):
        self.enroll()
        self.sb.ok(self.repo, "start", "a", "--goal", "do a", "--owns", "src/", env=self.a)
        wt_a = self.sb.tmp / "proj-a"
        self.assertTrue(wt_a.is_dir())
        # owner edits in its own worktree: silent, never "allow"
        rc, out = self.edit(self.a, wt_a / "src" / "a.py")
        self.assertEqual(out, "")
        # a second session may not edit in a's worktree
        o = json.loads(self.edit(self.b, wt_a / "src" / "a.py")[1])
        self.assertEqual(o["hookSpecificOutput"]["permissionDecision"], "deny")
        # b's claim on src/ overlaps a's: refused, request logged
        p = self.sb.cli(self.repo, "start", "b", "--goal", "do b", "--owns", "src/b.py", env=self.b)
        self.assertEqual(p.returncode, 1)
        self.assertIn("overlaps", p.stderr)
        self.sb.ok(self.repo, "start", "b", "--goal", "do b", "--owns", "docs/", env=self.b)
        wt_b = self.sb.tmp / "proj-b"
        o = json.loads(self.edit(self.b, wt_b / "src" / "b.py")[1])
        ctx = o["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("permissionDecision", o["hookSpecificOutput"])
        self.assertIn("owned by track `a`", ctx)
        # the same warning isn't repeated within the hour
        self.assertEqual(self.edit(self.b, wt_b / "src" / "b.py")[1], "")
        # activity by a shows up for b on the same file
        self.sb.hook("post-edit", {"session_id": self.a["CLAUDE_CODE_SESSION_ID"],
                                   "tool_name": "Edit",
                                   "tool_input": {"file_path": str(wt_a / "docs" / "x.md")}}, self.a)
        o = json.loads(self.edit(self.b, wt_b / "docs" / "x.md")[1])
        self.assertIn("edited in the last 2 h", o["hookSpecificOutput"]["additionalContext"])

    def test_dead_owner_can_be_resumed(self):
        self.enroll()
        self.sb.ok(self.repo, "start", "a", "--goal", "do a", env=self.a)
        self.sb.end_session(self.a)
        wt_a = self.sb.tmp / "proj-a"
        o = json.loads(self.edit(self.b, wt_a / "src" / "a.py")[1])
        self.assertIn("resume a", o["hookSpecificOutput"]["additionalContext"])
        self.sb.ok(self.repo, "resume", "a", env=self.b)
        self.assertEqual(self.edit(self.b, wt_a / "src" / "a.py")[1], "")

    def test_bash_rules(self):
        self.enroll()
        o = json.loads(self.bash(self.a, "git add -A && git commit -m x")[1])
        self.assertEqual(o["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.bash(self.a, "git status")[1], "")
        o = json.loads(self.bash(self.a, "git push origin trunk")[1])
        self.assertIn("protected", o["hookSpecificOutput"]["permissionDecisionReason"])

    def test_session_start_summary(self):
        self.enroll()
        self.sb.ok(self.repo, "start", "a", "--goal", "do a", env=self.a)
        rc, out = self.sb.hook("session-start", {"session_id": self.b["CLAUDE_CODE_SESSION_ID"],
                                                 "cwd": str(self.repo)}, self.b)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("protected branch `trunk`", ctx)
        self.assertIn("- a [active]", ctx)

    def test_session_start_claims_track_of_ended_owner(self):
        self.enroll()
        self.sb.ok(self.repo, "start", "a", "--goal", "do a", env=self.a)
        self.sb.end_session(self.a)
        wt_a = self.sb.tmp / "proj-a"
        out = self.sb.hook("session-start", {"session_id": self.b["CLAUDE_CODE_SESSION_ID"],
                                             "cwd": str(wt_a)}, self.b)[1]
        self.assertIn("(yours)", json.loads(out)["hookSpecificOutput"]["additionalContext"])


class TestGitHooks(HookCase):
    def test_human_commits_untouched_agent_commits_blocked(self):
        self.enroll()
        p = self.sb.commit(self.repo, "README.md", "human\n", "human edit")
        self.assertEqual(p.returncode, 0, p.stderr)
        msg = self.sb.git(self.repo, "log", "-1", "--format=%B").stdout
        self.assertNotIn("Agent:", msg)
        p = self.sb.commit(self.repo, "README.md", "agent\n", "agent edit", env=self.a)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("protected", p.stderr)

    def test_observe_mode_allows_and_trailers_are_idempotent(self):
        self.enroll("observe")
        self.sb.ok(self.repo, "start", "a", "--goal", "do a", env=self.a)
        wt = self.sb.tmp / "proj-a"
        p = self.sb.commit(wt, "src/a.py", "a = 9\n", "change a", env=self.a)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.sb.git(wt, "commit", "-q", "--amend", "--no-edit", env=self.a)
        msg = self.sb.git(wt, "log", "-1", "--format=%B").stdout
        self.assertEqual(msg.count("Agent: claude-code"), 1)
        self.assertIn("Track: a", msg)
        self.assertIn(f"Agent-Session: {self.a['CLAUDE_CODE_SESSION_ID'][:8]}", msg)

    def test_pre_push_blocks_only_protected(self):
        self.enroll()
        self.sb.ok(self.repo, "start", "a", "--goal", "do a", env=self.a)
        wt = self.sb.tmp / "proj-a"
        self.sb.commit(wt, "src/a.py", "a = 9\n", "change a", env=self.a)
        p = self.sb.git(wt, "push", "-q", "origin", "a", env=self.a, check=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.sb.git(wt, "push", "-q", "origin", "a:trunk", env=self.a, check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("protected", p.stderr)

    def test_existing_hook_is_chained_and_restored(self):
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho ORIGINAL-HOOK >&2\nexit 0\n")
        hook.chmod(0o755)
        self.enroll()
        p = self.sb.commit(self.repo, "README.md", "x\n", "human")
        self.assertIn("ORIGINAL-HOOK", p.stderr)
        self.sb.ok(self.repo, "uninit")
        self.assertIn("ORIGINAL-HOOK", hook.read_text())

    def test_agents_cannot_change_protection(self):
        self.enroll("observe")
        p = self.sb.cli(self.repo, "mode", "enforce", env=self.a)
        self.assertEqual(p.returncode, 1)
        self.assertIn("only the user", p.stderr)
        p = self.sb.cli(self.repo, "uninit", env=self.a)
        self.assertEqual(p.returncode, 1)


if __name__ == "__main__":
    unittest.main()
