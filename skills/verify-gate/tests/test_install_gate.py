"""install_gate.py: dry run by default, idempotent merge, keeps everything it doesn't own."""

import json
import os
import subprocess
import sys
import unittest

from support import GATE, INSTALL, TEMPLATE, Sandbox

EXISTING = {
    "permissions": {"allow": ["Bash(npm test)"]},
    "hooks": {
        "PostToolUse": [{"matcher": "Edit|Write", "hooks": [
            {"type": "command", "command": "python .claude/hooks/after_edit.py"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}],
    },
}


class InstallCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.repo = self.sb.make_repo()
        self.settings = self.repo / ".claude" / "settings.json"
        self.hook = self.repo / ".claude" / "hooks" / "stop_gate.py"

    def tearDown(self):
        self.sb.cleanup()

    def install(self, *args):
        p = subprocess.run([sys.executable, str(INSTALL), str(self.repo), *args],
                           capture_output=True, text=True, env=self.sb.env)
        return p.returncode, p.stdout + p.stderr

    def load(self):
        return json.loads(self.settings.read_text())

    def ours(self, settings, event):
        return [h for g in settings["hooks"].get(event, []) for h in g["hooks"]
                if h["command"].startswith('f="$CLAUDE_PROJECT_DIR/.claude/hooks/stop_gate.py"')]


class TestInstall(InstallCase):
    def test_dry_run_writes_nothing(self):
        rc, out = self.install()
        self.assertEqual(rc, 0, out)
        self.assertIn("dry run", out)
        self.assertIn("create  .claude/hooks/stop_gate.py", out)
        self.assertIn('+++ b/.claude/settings.json', out)
        self.assertFalse((self.repo / ".claude").exists())

    def test_apply_creates_hook_and_settings(self):
        rc, out = self.install("--apply")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.hook.read_bytes(), GATE.read_bytes())
        self.assertTrue(os.access(self.hook, os.X_OK))
        s = self.load()
        stop = self.ours(s, "Stop")
        self.assertEqual(len(stop), 1)
        self.assertEqual(stop[0]["timeout"], 300)
        cmd = stop[0]["command"]
        self.assertIn('[ -r "$f" ] && command -v python3 >/dev/null || exit 0', cmd)
        self.assertIn('python3 -I "$f" --mode fast --timeout 240;', cmd)
        self.assertTrue(cmd.endswith("c=$?; [ $c -eq 2 ] && c=1; exit $c"))
        self.assertEqual(s["hooks"]["SessionStart"][0]["matcher"], "startup|resume|clear|fork")
        self.assertEqual(self.ours(s, "SessionStart")[0]["command"], cmd)

    def test_idempotent(self):
        self.install("--apply")
        before = self.settings.read_bytes()
        rc, out = self.install("--apply")
        self.assertEqual(rc, 0)
        self.assertIn("nothing to change", out)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_keeps_existing_settings_and_hooks(self):
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps(EXISTING, indent=4) + "\n")
        self.install("--apply")
        s = self.load()
        self.assertEqual(s["permissions"], EXISTING["permissions"])
        self.assertEqual(s["hooks"]["PostToolUse"], EXISTING["hooks"]["PostToolUse"])
        self.assertEqual(s["hooks"]["Stop"][0], EXISTING["hooks"]["Stop"][0])
        self.assertEqual(len(self.ours(s, "Stop")), 1)
        self.assertIn('\n    "permissions"', self.settings.read_text())  # indent kept

    def test_a_users_own_stop_gate_handler_is_not_ours(self):
        mine = {"type": "command", "command": "python3 ~/.claude/hooks/stop_gate.py --mine"}
        legacy = {"type": "command", "command": 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/'
                                                'stop_gate.py" --theirs'}
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [mine, legacy]}]}}))
        self.install("--apply")
        stop = self.load()["hooks"]["Stop"]
        self.assertEqual(stop[0]["hooks"], [mine, legacy])
        self.assertEqual(len(stop), 2)
        self.install("--remove", "--apply")
        self.assertEqual(self.load()["hooks"]["Stop"], [{"hooks": [mine, legacy]}])

    def test_reinstall_updates_in_place(self):
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps(EXISTING))
        self.install("--apply")
        s = self.load()
        s["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "after-us"}]})
        self.settings.write_text(json.dumps(s, indent=2))
        rc, out = self.install("--apply", "--mode", "full", "--watch", "src", "--timeout", "100")
        self.assertEqual(rc, 0, out)
        stop = self.load()["hooks"]["Stop"]
        self.assertEqual([g["hooks"][0]["command"][:10] for g in stop],
                         ["notify-sen", 'f="$CLAUDE', "after-us"])
        ours = self.ours(self.load(), "Stop")[0]
        self.assertIn("--mode full --timeout 100 --watch=src;", ours["command"])
        self.assertEqual(ours["timeout"], 160)

    def test_upgrades_the_previous_command_form(self):
        old = ('f="$CLAUDE_PROJECT_DIR/.claude/hooks/stop_gate.py"; [ -f "$f" ] || exit 0; '
               'exec python3 "$f" --mode fast --timeout 240')
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps({"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": old, "timeout": 300}]}]}}))
        self.install("--apply")
        stop = self.load()["hooks"]["Stop"]
        self.assertEqual(len(stop), 1)
        self.assertIn("python3 -I", stop[0]["hooks"][0]["command"])

    def test_no_baseline_and_custom_check(self):
        self.install("--apply", "--no-baseline", "--check", "make check")
        s = self.load()
        self.assertNotIn("SessionStart", s["hooks"])
        self.assertIn("'--check=make check'", self.ours(s, "Stop")[0]["command"])

    def test_updates_a_stale_hook_copy(self):
        self.install("--apply")
        self.hook.write_text("# old\n")
        rc, out = self.install()
        self.assertIn("update  .claude/hooks/stop_gate.py", out)
        self.assertIn("-# old", out)
        self.install("--apply")
        self.assertEqual(self.hook.read_bytes(), GATE.read_bytes())

    def test_invalid_settings_are_left_alone(self):
        self.settings.parent.mkdir()
        self.settings.write_text("{not json")
        rc, out = self.install("--apply")
        self.assertEqual(rc, 1)
        self.assertIn("not valid JSON", out)
        self.assertEqual(self.settings.read_text(), "{not json")
        self.assertFalse(self.hook.exists())
        self.settings.write_text('{"hooks": []}')
        self.assertEqual(self.install("--apply")[0], 1)

    def test_null_hooks(self):
        self.settings.parent.mkdir()
        self.settings.write_text('{"hooks": null, "model": "x"}\n')
        self.assertEqual(self.install("--remove", "--apply")[0], 0)
        self.assertEqual(self.settings.read_text(), '{"hooks": null, "model": "x"}\n')
        self.assertEqual(self.install("--apply")[0], 0)
        self.assertEqual(len(self.ours(self.load(), "Stop")), 1)

    def test_remove_takes_out_only_ours(self):
        self.settings.parent.mkdir()
        self.settings.write_text(json.dumps(EXISTING, indent=2) + "\n")
        self.install("--apply")
        rc, out = self.install("--remove", "--apply")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.load(), EXISTING)
        self.assertFalse(self.hook.exists())
        self.assertFalse(self.hook.parent.exists())  # left empty, so removed

    def test_remove_keeps_a_hooks_dir_with_other_files(self):
        self.install("--apply")
        (self.hook.parent / "after_edit.py").write_text("pass\n")
        self.install("--remove", "--apply")
        self.assertEqual(os.listdir(self.hook.parent), ["after_edit.py"])

    def test_remove_of_what_the_installer_created(self):
        self.install("--apply")
        rc, out = self.install("--remove")
        self.assertIn("delete  .claude/settings.json  (empty without the gate)", out)
        self.assertIn("rmdir   .claude  (left empty)", out)
        rc, out = self.install("--remove", "--apply")
        self.assertEqual(rc, 0, out)
        self.assertFalse((self.repo / ".claude").exists())  # not a stray "{}"
        self.assertIn("nothing to change", self.install("--remove")[1])

    def test_remove_keeps_a_claude_dir_with_other_files(self):
        self.install("--apply")
        local = self.repo / ".claude" / "settings.local.json"
        local.write_text("{}\n")
        self.install("--remove", "--apply")
        self.assertEqual(os.listdir(self.repo / ".claude"), ["settings.local.json"])
        self.settings.write_text("{}\n")  # the user's own empty file is not ours to delete
        self.assertIn("nothing to change", self.install("--remove", "--apply")[1])
        self.assertTrue(self.settings.exists())

    def test_a_symlink_loop(self):
        self.settings.parent.mkdir()
        os.symlink("settings.json", self.settings)
        rc, out = self.install("--apply")
        self.assertEqual(rc, 1)
        self.assertIn("install_gate: .claude/settings.json can't be resolved", out)
        self.assertNotIn("Traceback", out)

    def test_check_template_only_when_missing(self):
        (self.repo / "scripts" / "check").unlink()
        rc, out = self.install()
        self.assertIn("no scripts/check yet", out)
        self.install("--apply", "--check-template")
        check = self.repo / "scripts" / "check"
        self.assertEqual(check.read_bytes(), TEMPLATE.read_bytes())
        self.assertTrue(os.access(check, os.X_OK))
        check.write_text("#!/bin/sh\nexit 0\n")
        self.install("--apply", "--check-template")
        self.assertEqual(check.read_text(), "#!/bin/sh\nexit 0\n")


class TestInputs(InstallCase):
    def test_bad_target(self):
        p = subprocess.run([sys.executable, str(INSTALL), str(self.sb.tmp / "nope")],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("is not a directory", p.stderr)

    def test_usage_errors_exit_1(self):
        rc, out = self.install("--mode", "bogus")
        self.assertEqual(rc, 1)
        self.assertIn("invalid choice", out)

    def test_non_finite_or_non_positive_timeouts(self):
        for value in ("nan", "inf", "1e400", "0", "-5"):
            rc, out = self.install(f"--timeout={value}")
            self.assertEqual(rc, 1, value)
            self.assertNotIn("Traceback", out)
            self.assertIn("must be a positive number", out)

    def test_max_failures_below_2_would_never_block(self):
        for value in ("1", "0"):
            rc, out = self.install("--max-failures", value)
            self.assertEqual(rc, 1, value)
            self.assertIn("must be 2 or more", out)
        self.assertEqual(self.install("--max-failures", "2")[0], 0)

    def test_not_a_git_work_tree_is_refused(self):
        plain = self.sb.tmp / "plain"
        plain.mkdir()
        p = subprocess.run([sys.executable, str(INSTALL), str(plain), "--apply"],
                           capture_output=True, text=True, env=self.sb.env)
        self.assertEqual(p.returncode, 1)
        self.assertIn("not inside a git work tree", p.stderr)
        self.assertFalse((plain / ".claude").exists())

    def test_a_subproject_folder_is_explained(self):
        sub = self.repo / "services" / "api"
        sub.mkdir(parents=True)
        p = subprocess.run([sys.executable, str(INSTALL), str(sub)], capture_output=True,
                           text=True, env=self.sb.env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("is a subfolder of the git work tree", p.stdout)
        self.assertIn("no scripts/check in this folder; the gate runs", p.stdout)

    def test_a_watch_path_that_matches_nothing_is_flagged(self):
        rc, out = self.install("--watch", "scr", "--watch", "src", "--watch", ":!src/gen")
        self.assertEqual(rc, 0, out)
        self.assertIn("--watch 'scr' matches nothing", out)
        self.assertNotIn("'src' matches nothing", out)
        self.assertNotIn("':!src/gen' matches", out)

    def test_watch_paths_must_stay_in_the_repo(self):
        for value in ("../elsewhere", "/abs", "src/../../x", ""):
            rc, out = self.install("--watch", value)
            self.assertEqual(rc, 1, value)
            self.assertIn("must be a path inside the repo", out)
        self.assertEqual(self.install("--watch", "src", "--watch", ":!src/gen")[0], 0)


class TestLinksAndFailures(InstallCase):
    def test_symlinked_settings_inside_the_repo_are_written_through(self):
        shared = self.repo / "config" / "claude.json"
        shared.parent.mkdir()
        shared.write_text('{"model": "x"}\n')
        self.settings.parent.mkdir()
        self.settings.symlink_to("../config/claude.json")
        rc, out = self.install("--apply")
        self.assertEqual(rc, 0, out)
        self.assertIn("via config/claude.json", out)
        self.assertTrue(self.settings.is_symlink())
        self.assertIn("stop_gate.py", shared.read_text())

    def test_settings_linked_outside_the_repo_are_refused(self):
        outside = self.sb.tmp / "shared-settings.json"
        outside.write_text("{}\n")
        self.settings.parent.mkdir()
        self.settings.symlink_to(outside)
        rc, out = self.install("--apply")
        self.assertEqual(rc, 1)
        self.assertIn("outside the repo", out)
        self.assertEqual(outside.read_text(), "{}\n")
        self.assertFalse(self.hook.exists())

    def test_a_scripts_link_outside_the_repo_is_fine_without_the_template(self):
        (self.repo / "scripts" / "check").unlink()
        (self.repo / "scripts").rmdir()
        shared = self.sb.tmp / "shared-scripts"
        shared.mkdir()
        (shared / "check").write_text("#!/bin/sh\necho CHECK PASS 1/1\n")
        os.symlink(shared, self.repo / "scripts")
        rc, out = self.install("--apply")
        self.assertEqual(rc, 0, out)
        rc, out = self.install("--check-template")  # writing through it is still refused
        self.assertEqual(rc, 1)
        self.assertIn("outside the repo", out)

    def test_tabs_crlf_and_the_final_newline_are_kept(self):
        self.settings.parent.mkdir()
        self.settings.write_bytes(b'{\r\n\t"permissions": {\r\n\t\t"allow": ["Bash(ls *)"]\r\n'
                                  b'\t}\r\n}\r\n')
        rc, out = self.install("--apply")
        self.assertEqual(rc, 0, out)
        raw = self.settings.read_bytes()
        self.assertTrue(raw.startswith(b'{\r\n\t"permissions": {\r\n\t\t"allow": ['), raw[:60])
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
        self.assertTrue(raw.endswith(b"}\r\n"))
        self.assertIn("nothing to change", self.install()[1])
        self.settings.write_text('{"model": "x"}')  # no final newline: none added
        self.install("--apply")
        self.assertTrue(self.settings.read_text().endswith("}"))

    def test_duplicate_keys_are_flagged(self):
        self.settings.parent.mkdir()
        self.settings.write_text('{"env": {"A": "1"}, "env": {"B": "2"}}\n')
        rc, out = self.install()
        self.assertEqual(rc, 0, out)
        self.assertIn("repeats the key(s) 'env'", out)

    def test_a_failed_write_leaves_nothing_behind(self):
        self.settings.parent.mkdir()
        self.settings.write_text("{}\n")
        (self.settings.parent / ".settings.json.tmp").mkdir()  # the settings write will fail
        rc, out = self.install("--apply", "--check-template")
        self.assertEqual(rc, 1, out)
        self.assertIn("nothing was changed", out)
        self.assertFalse(self.hook.parent.exists())
        self.assertEqual(self.settings.read_text(), "{}\n")


if __name__ == "__main__":
    unittest.main()
