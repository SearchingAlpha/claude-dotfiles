"""secret_guard.py through its real entry point, with Claude Code's payload shapes."""

import json
import os
import shutil
import subprocess
import sys
import unittest

from support import FAKE, GUARDS, Sandbox, pre


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.repo = self.sb.repo()

    def tearDown(self):
        self.sb.cleanup()

    def guard(self, tool, tool_input):
        return self.sb.run("secret_guard.py", pre(tool, tool_input, cwd=str(self.repo)))

    def assertDenied(self, rc, out, err, secret=None, says="environment"):
        self.assertEqual(rc, 0)                         # deny JSON alone, exit 0
        o = json.loads(out)
        hso = o["hookSpecificOutput"]
        self.assertEqual((hso["hookEventName"], hso["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertIn(says, hso["permissionDecisionReason"])
        self.assertIn("secret_guard blocked", o["systemMessage"])
        if secret:
            for text in (out, err):
                self.assertNotIn(secret, text)

    def assertAllowed(self, rc, out):
        self.assertEqual(rc, 0)
        if out:
            self.assertNotEqual(json.loads(out).get("hookSpecificOutput", {})
                                .get("permissionDecision"), "deny")


class TestFileTools(GuardCase):
    def test_write_in_git_tree_is_denied(self):
        k = FAKE.anthropic
        rc, out, err = self.guard("Write", {"file_path": str(self.repo / "app" / "cfg.py"),
                                            "content": f"import os\nKEY = '{k}'\n"})
        self.assertDenied(rc, out, err, k)
        self.assertIn("anthropic-api-key on line 2", out)

    def test_edit_and_notebook_edit(self):
        (self.repo / "a.py").write_text("x = 1\n")
        k = FAKE.github
        rc, out, err = self.guard("Edit", {"file_path": str(self.repo / "a.py"), "old_string": "x = 1",
                                           "new_string": f"x = '{k}'", "replace_all": False})
        self.assertDenied(rc, out, err, k)
        k = FAKE.aws_id
        rc, out, err = self.guard("NotebookEdit", {"notebook_path": str(self.repo / "n.ipynb"),
                                                   "new_source": f"key = '{k}'", "cell_id": "c1"})
        self.assertDenied(rc, out, err, k)

    def test_clean_content_passes_silently(self):
        rc, out, err = self.guard("Write", {"file_path": str(self.repo / "ok.py"),
                                            "content": "token = os.environ['API_TOKEN']\n"})
        self.assertEqual((rc, out), (0, ""))

    def test_outside_git_is_allowed(self):
        rc, out, _ = self.guard("Write", {"file_path": str(self.sb.tmp / "loose" / "creds.txt"),
                                          "content": FAKE.anthropic})
        self.assertEqual((rc, out), (0, ""))

    def test_gitignored_file_is_the_right_place(self):
        (self.repo / ".gitignore").write_text(".env\nsecrets/\n")
        for rel in (".env", "secrets/prod.json", "secrets/new/dir/x.json"):
            with self.subTest(rel):
                rc, out, _ = self.guard("Write", {"file_path": str(self.repo / rel),
                                                  "content": f"KEY={FAKE.anthropic}\n"})
                self.assertEqual((rc, out), (0, ""))

    def test_without_git_the_reason_says_so(self):
        (self.repo / ".gitignore").write_text(".env\n")
        empty = self.sb.tmp / "nobin"
        empty.mkdir()
        k = FAKE.anthropic
        rc, out, err = self.sb.run("secret_guard.py", pre("Write", {"file_path": str(self.repo / ".env"),
                                                                    "content": f"K={k}\n"}),
                                   env=dict(self.sb.env, PATH=str(empty)))
        self.assertDenied(rc, out, err, k, says="git on PATH")

    def test_files_under_dot_git_are_never_committed(self):
        k = FAKE.github
        for rel, text in ((".git/config", f'[remote "origin"]\n\turl = https://x:{k}@example.com/o/r.git\n'),
                          (".git/info/creds", f"T={k}\n")):
            with self.subTest(rel):
                rc, out, _ = self.guard("Write", {"file_path": str(self.repo / rel), "content": text})
                self.assertEqual((rc, out), (0, ""))

    def test_tracked_file_matching_gitignore_is_still_protected(self):
        (self.repo / ".env").write_text("A=1\n")
        self.sb.git(self.repo, "add", ".env")
        self.sb.git(self.repo, "commit", "-q", "-m", "env")
        (self.repo / ".gitignore").write_text(".env\n")
        k = FAKE.anthropic
        rc, out, err = self.guard("Write", {"file_path": str(self.repo / ".env"), "content": f"K={k}\n"})
        self.assertDenied(rc, out, err, k)

    def test_vault_dir_is_protected_even_when_ignored(self):
        vault = self.sb.tmp / "vault"
        vault.mkdir()
        self.sb.config({"vault_dirs": [str(vault)]})
        k = FAKE.slack
        rc, out, err = self.guard("Write", {"file_path": str(vault / "journal.md"), "content": f"slack {k}"})
        self.assertDenied(rc, out, err, k)
        self.assertIn("vault", out)

    def test_vault_dirs_as_a_single_string(self):
        vault, plain = self.sb.tmp / "vault", self.sb.tmp / "plain"
        vault.mkdir()
        plain.mkdir()
        self.sb.config({"vault_dirs": str(vault)})                 # a typo for a one-item list
        k = FAKE.github
        rc, out, err = self.guard("Write", {"file_path": str(vault / "a.md"), "content": k})
        self.assertDenied(rc, out, err, k)
        rc, out, _ = self.guard("Write", {"file_path": str(plain / "a.md"), "content": k})
        self.assertEqual((rc, out), (0, ""))                       # not "/" protected by accident
        self.sb.config({"vault_dirs": {"oops": 1}})
        rc, out, _ = self.guard("Write", {"file_path": str(plain / "a.md"), "content": k})
        self.assertEqual((rc, out), (0, ""))
        self.assertIn("vault_dirs should be a list", (self.sb.state / "errors.log").read_text())

    def test_value_already_in_the_file_is_let_through_with_a_note(self):
        k = FAKE.anthropic
        (self.repo / "legacy.py").write_text(f"KEY = '{k}'\nx = 1\n")
        rc, out, _ = self.guard("Edit", {"file_path": str(self.repo / "legacy.py"),
                                         "old_string": "x = 1", "new_string": f"KEY2 = '{k}'"})
        self.assertEqual(rc, 0)
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("already holds", ctx)
        self.assertNotIn(k, out)

    def test_allow_marker_for_fixtures(self):
        rc, out, _ = self.guard("Write", {"file_path": str(self.repo / "t.py"),
                                          "content": f"FAKE = '{FAKE.anthropic}'  # guards:allow-secret\n"})
        self.assertEqual((rc, out), (0, ""))

    def test_symlinked_path_resolves(self):
        link = self.sb.tmp / "link"
        link.symlink_to(self.repo)
        k = FAKE.github
        rc, out, err = self.guard("Write", {"file_path": str(link / "x.txt"), "content": k})
        self.assertDenied(rc, out, err, k)


class TestPlaceholders(GuardCase):
    """Writing redact.py's placeholders back over a file would lose its real values."""

    def setUp(self):
        super().setUp()
        self.k = FAKE.github
        self.env_file = self.sb.tmp / "app.env"                    # outside git: still protected
        self.env_file.write_text(f"GH={self.k}\nDEBUG=0\n")
        self.seen = "GH=[REDACTED:github-token]\nDEBUG=1\n"        # what Claude read back

    def test_full_rewrite_is_refused(self):
        rc, out, err = self.guard("Write", {"file_path": str(self.env_file), "content": self.seen})
        self.assertDenied(rc, out, err, says="placeholder")
        self.assertIn("Edit", json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"])

    def test_new_files_edits_and_files_that_already_say_it_pass(self):
        rc, out, _ = self.guard("Write", {"file_path": str(self.sb.tmp / "new.md"), "content": self.seen})
        self.assertEqual((rc, out), (0, ""))                        # a new file loses nothing
        doc = self.sb.tmp / "doc.md"
        doc.write_text("Output looks like [REDACTED:github-token].\n")
        rc, out, _ = self.guard("Write", {"file_path": str(doc), "content": "See [REDACTED:github-token]\n"})
        self.assertEqual((rc, out), (0, ""))                        # the file already says it
        rc, out, _ = self.guard("Edit", {"file_path": str(self.env_file), "old_string": "DEBUG=0",
                                         "new_string": "DEBUG=1 # was [REDACTED:github-token]"})
        self.assertEqual((rc, out), (0, ""))                        # Edit can't clobber the value

    def test_one_placeholder_already_in_the_file_does_not_cover_others(self):
        aws = FAKE.aws_id
        doc = self.sb.tmp / "runbook.md"
        doc.write_text(f"Redacted output looks like [REDACTED:github-token].\naws_access_key_id = {aws}\n")
        new = ("Redacted output looks like [REDACTED:github-token].\n"
               "aws_access_key_id = [REDACTED:aws-access-key-id]\nnew line\n")
        rc, out, err = self.guard("Write", {"file_path": str(doc), "content": new})
        self.assertDenied(rc, out, err, says="placeholder")
        doc.write_text(f"Looks like [REDACTED:github-token].\nGH = {self.k}\n")
        rc, out, err = self.guard("Write", {"file_path": str(doc), "content":
                                            "Looks like [REDACTED:github-token].\nGH = [REDACTED:github-token]\n"})
        self.assertDenied(rc, out, err, says="placeholder")        # same kind, one more of it

    def test_shell_rewrites_are_refused(self):
        for tool, cmd in (
                ("Bash", f"cat > {self.env_file} <<'EOF'\n{self.seen}EOF"),
                ("Bash", f"python3 -c \"open('{self.env_file}','w').write('''{self.seen}''')\""),
                ("Bash", f"sed -i 's/DEBUG=0/DEBUG=1/; s/^GH=.*/GH=[REDACTED:github-token]/' {self.env_file}"),
                ("PowerShell", f"Set-Content -Path {self.env_file} -Value 'GH=[REDACTED:github-token]'"),
                ("Monitor", "tail -f log | grep -v '[REDACTED:github-token]'")):
            with self.subTest(f"{tool}: {cmd[:30]}"):
                rc, out, err = self.guard(tool, {"command": cmd, "description": "d"})
                self.assertDenied(rc, out, err, says="leave out the brackets")
        for cmd in ("grep -rn '\\[REDACTED:' .", "grep -n 'REDACTED:github-token' app.env",
                    "grep -rnF '[REDACTED:github-token]' .", "rg -n '[REDACTED:jwt]' src",
                    "git log -S'[REDACTED:github-token]' --oneline", "git grep -n '[REDACTED:jwt]'"):
            with self.subTest(cmd):
                rc, out, _ = self.guard("Bash", {"command": cmd})
                self.assertEqual((rc, out), (0, ""))                # a search writes nothing
        for cmd in ("grep -rlF '[REDACTED:jwt]' . | xargs sed -i 's/x/y/'",
                    "grep -F '[REDACTED:jwt]' a > b", "grep x f; echo '[REDACTED:jwt]' >> f",
                    "grep -F \"$(echo '[REDACTED:jwt]')\" f"):
            with self.subTest(cmd):
                self.assertDenied(*self.guard("Bash", {"command": cmd}), says="placeholder")

    def test_a_copy_then_move_is_refused(self):
        rc, out, err = self.guard("Write", {"file_path": str(self.env_file) + ".new", "content": self.seen})
        self.assertDenied(rc, out, err, says=str(self.env_file))   # names the file holding the values
        stage = self.sb.tmp / "stage"
        stage.mkdir()
        (stage / "copy.env").write_text(self.seen)                 # e.g. written under /tmp first
        (stage / "app.env").write_text(self.seen)
        for cmd in ("mv stage/copy.env app.env", f"cd {stage} && cp -f copy.env ../app.env",
                    "install -m 600 stage/copy.env app.env", f"mv -f stage/app.env {self.sb.tmp}/"):
            with self.subTest(cmd):
                rc, out, err = self.sb.run("secret_guard.py", pre("Bash", {"command": cmd}, cwd=str(self.sb.tmp)))
                self.assertDenied(rc, out, err, says="real values")
        for cmd in ("mv stage/copy.env fresh.env", "cp app.env stage/copy.env", "scp copy.env host:"):
            with self.subTest(cmd):
                rc, out, _ = self.sb.run("secret_guard.py", pre("Bash", {"command": cmd}, cwd=str(self.sb.tmp)))
                self.assertEqual((rc, out), (0, ""))

    def test_redirections_tee_dd_ln_and_target_dirs(self):
        stage, live = self.sb.tmp / "stage", self.sb.tmp / "live"
        stage.mkdir()
        live.mkdir()
        (stage / "copy.env").write_text(self.seen)
        (stage / "app.env").write_text(self.seen)
        (live / "app.env").write_text(f"GH={self.k}\n")
        (self.sb.tmp / "hits.txt").write_text("old hits\n")
        for cmd in ("cat stage/copy.env > app.env", "tee app.env < stage/copy.env",
                    "cat stage/copy.env | sudo tee app.env > /dev/null",
                    "sed 's/DEBUG=1/DEBUG=0/' stage/copy.env >| app.env",
                    "dd if=stage/copy.env of=app.env status=none", "ln -sf stage/copy.env app.env",
                    "mv -t live stage/app.env", "cp --target-directory=live stage/app.env",
                    "cd stage && cat copy.env > ../app.env 2>/dev/null",
                    "cat > notes.md <<'EOF'\nnot > app.env\nEOF\ncat stage/copy.env > app.env"):
            with self.subTest(cmd):
                rc, out, err = self.sb.run("secret_guard.py", pre("Bash", {"command": cmd}, cwd=str(self.sb.tmp)))
                self.assertDenied(rc, out, err, says="real values")
        for cmd in ("cat stage/copy.env >> app.env", "tee -a app.env < stage/copy.env",
                    "grep -n REDACTED stage/copy.env > hits.txt",       # hits.txt holds no secret
                    "ln -s stage/copy.env app.env", "mv -t live stage/copy.env",
                    "cat > app.env.bak <<'EOF'\nGH=x > app.env\nEOF", "make > build.log 2>&1",
                    "git status 2>&1 | head -5"):
            with self.subTest(cmd):
                rc, out, _ = self.sb.run("secret_guard.py", pre("Bash", {"command": cmd}, cwd=str(self.sb.tmp)))
                self.assertEqual((rc, out), (0, ""))
        doc = f"Redacted output looks like [REDACTED:github-token].  # {'guards:allow-secret'}\n"
        rc, out, _ = self.guard("Write", {"file_path": str(self.env_file) + ".md", "content": doc})
        self.assertEqual((rc, out), (0, ""))                        # marked: about redaction itself


class TestShell(GuardCase):
    def test_full_key_in_command_is_denied(self):
        k = FAKE.anthropic
        for cmd in (f"export ANTHROPIC_API_KEY={k}", f"curl -H 'x-api-key: {k}' https://api.x",
                    f"echo '{k}' >> notes.md"):
            with self.subTest(cmd[:12]):
                rc, out, err = self.guard("Bash", {"command": cmd, "description": "d"})
                self.assertDenied(rc, out, err, k)
        rc, out, err = self.guard("PowerShell", {"command": f"$env:K = '{k}'"})
        self.assertDenied(rc, out, err, k)
        rc, out, err = self.guard("Monitor", {"command": f"curl -sN -H 'x-api-key: {k}' https://x/events",
                                              "description": "watch"})
        self.assertDenied(rc, out, err, k)

    def test_ordinary_commands_pass(self):
        for cmd in ("grep -rn 'sk-ant-' .", "git log --oneline -20", "rg 'AKIA[0-9A-Z]{16}'",
                    "sha256sum dist/*.tar.gz", 'curl -H "Authorization: Bearer $TOKEN" https://x',
                    "python3 -m unittest discover -s tests", "cat ~/.aws/credentials | wc -l",
                    "aws resourcegroupstaggingapi get-resources --pagination-token=" + "Ab3d" * 15):
            with self.subTest(cmd[:40]):
                rc, out, _ = self.guard("Bash", {"command": cmd})
                self.assertEqual((rc, out), (0, ""))

    def test_other_tools_are_ignored(self):
        rc, out, _ = self.guard("Read", {"file_path": str(self.repo / "README.md")})
        self.assertEqual((rc, out), (0, ""))


class TestFailOpen(GuardCase):
    def test_garbage_never_blocks(self):
        for payload in ("", "not json", "[1,2]", '{"tool_input": 5}', '{"tool_name": "Write"}',
                        '{"tool_name": "Bash", "tool_input": {"command": 5}}'):
            with self.subTest(payload):
                rc, out, _ = self.sb.run("secret_guard.py", payload)
                self.assertEqual((rc, out), (0, ""))

    def test_crash_fails_open_and_is_logged(self):
        broken = self.sb.tmp / "broken"
        broken.mkdir()
        for name in ("secret_guard.py", "common.py"):
            shutil.copy(GUARDS / name, broken / name)
        (broken / "patterns.py").write_text("def scan(*a, **k):\n    raise RuntimeError('boom')\n")
        k = FAKE.anthropic
        payload = pre("Bash", {"command": f"export K={k}"})
        rc, out, err = self.sb.run("secret_guard.py", payload, script_dir=broken)
        self.assertEqual(rc, 0)
        self.assertIn("internal error", err)
        self.assertIn("systemMessage", out)                       # first failure: one notice
        log = (self.sb.state / "errors.log").read_text()
        self.assertIn("secret_guard", log)
        self.assertIn("RuntimeError: boom", log)
        rc, out, _ = self.sb.run("secret_guard.py", payload, script_dir=broken)
        self.assertEqual((rc, out), (0, ""))                      # same session: no repeat

    def test_bad_config_is_ignored(self):
        (self.sb.state / "config.json").write_text("{nope")
        k = FAKE.anthropic
        rc, out, err = self.guard("Bash", {"command": f"export K={k}"})
        self.assertDenied(rc, out, err, k)
        self.assertIn("invalid JSON", (self.sb.state / "errors.log").read_text())

    def test_can_be_disabled(self):
        env = dict(self.sb.env, CLAUDE_GUARDS_DISABLE="secret_guard")
        rc, out, _ = self.sb.run("secret_guard.py", pre("Bash", {"command": f"export K={FAKE.github}"}),
                                 env=env)
        self.assertEqual((rc, out), (0, ""))
        for off in (False, "false"):
            self.sb.config({"secret_guard": off})
            rc, out, _ = self.guard("Bash", {"command": f"export K={FAKE.github}"})
            self.assertEqual((rc, out), (0, ""))

    def test_unwritable_stdout_still_blocks_with_exit_2(self):
        r, w = os.pipe()
        os.close(r)                                               # writing to stdout now fails
        try:
            p = subprocess.run([sys.executable, "-I", str(GUARDS / "secret_guard.py")],
                               input=json.dumps(pre("Bash", {"command": f"export K={FAKE.github}"})).encode(),
                               stdout=w, stderr=subprocess.PIPE, env=self.sb.env, timeout=30)
        finally:
            os.close(w)
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"credential literal", p.stderr)


if __name__ == "__main__":
    unittest.main()
