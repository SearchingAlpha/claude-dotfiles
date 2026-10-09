"""run.sh (the launcher hooks.json calls) and the hooks.json registrations."""

import json
import os
import re
import shutil
import subprocess
import sys
import unittest

from support import FAKE, GUARDS, Sandbox, pre

HOOKS_JSON = GUARDS.parent / "hooks.json"


class TestLauncher(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.bin = self.sb.tmp / "bin"                  # the few tools run.sh needs, no Python
        self.bin.mkdir()
        for tool in ("sh", "dirname", "find", "mkdir"):
            (self.bin / tool).symlink_to(shutil.which(tool))
        self.payload = pre("Bash", {"command": f"export K={FAKE.github}"})

    def tearDown(self):
        self.sb.cleanup()

    def path(self, *dirs):
        return dict(self.sb.env, PATH=":".join(str(d) for d in (*dirs, self.bin)))

    def script(self, d, name, body):
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text("#!/bin/sh\n" + body)
        (d / name).chmod(0o755)

    def test_runs_the_guard_with_python3(self):
        rc, out, _ = self.sb.launch("secret_guard", self.payload)
        self.assertEqual((rc, json.loads(out)["hookSpecificOutput"]["permissionDecision"]), (0, "deny"))

    def test_no_python_exits_0_and_says_so_once_a_day(self):
        rc, out, err = self.sb.launch("secret_guard", self.payload, env=self.path())
        self.assertEqual(rc, 0)
        self.assertIn("no Python 3 found", json.loads(out)["systemMessage"])
        self.assertIn("secret_guard did not run", err)
        rc, out, _ = self.sb.launch("redact", "{}", env=self.path())
        self.assertEqual((rc, out), (0, ""))

    def launch_copy(self, guard, files=(), env=None):
        """run.sh copied into a dir holding only `files`: a pull or checkout that
        dropped or renamed a guard. Python exits 2 on a script it can't open, which
        would block every tool call (and keep Claude going on Stop)."""
        d = self.sb.tmp / "partial"
        d.mkdir(exist_ok=True)
        shutil.copy(GUARDS / "run.sh", d / "run.sh")
        for f in files:
            shutil.copy(GUARDS / f, d / f)
        p = subprocess.run(["/bin/sh", "-c", f'sh "{d}/run.sh" {guard}'], input=json.dumps(self.payload),
                           capture_output=True, text=True, env=env or self.sb.env, timeout=30)
        return d, p.returncode, p.stdout, p.stderr

    def test_a_missing_guard_exits_0_and_says_so_once_a_day(self):
        for guard in ("secret_guard", "vault_commit", "secret_gaurd"):
            with self.subTest(guard):
                _, rc, out, err = self.launch_copy(guard)
                self.assertEqual(rc, 0)
                self.assertIn(f"{guard} did not run", err)
                self.assertIn(f"{guard}.py is missing", json.loads(out)["systemMessage"])
        _, rc, out, _ = self.launch_copy("secret_guard")
        self.assertEqual((rc, out), (0, ""))                                # once a day

    def test_an_unreadable_guard_exits_0(self):
        if os.geteuid() == 0:
            self.skipTest("root reads any file")
        d, _, _, _ = self.launch_copy("redact", files=("redact.py",))
        (d / "redact.py").chmod(0)
        p = subprocess.run(["/bin/sh", "-c", f'sh "{d}/run.sh" redact'], input="{}",
                           capture_output=True, text=True, env=self.sb.env, timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertIn("missing or unreadable", p.stderr)

    def test_a_guard_given_by_path(self):
        rc, out, _ = self.sb.launch(str(GUARDS / "secret_guard.py"), self.payload)
        self.assertEqual((rc, json.loads(out)["hookSpecificOutput"]["permissionDecision"]), (0, "deny"))
        rc, out, err = self.sb.launch(str(self.sb.tmp / "gone" / "sql_guard.py"), self.payload)
        self.assertEqual(rc, 0)
        self.assertIn("sql_guard did not run", err)

    def test_skips_a_windows_store_stub_and_python2_then_tries_py(self):
        stubs = self.sb.tmp / "WindowsApps"
        self.script(stubs, "python3", 'echo "Python was not found" >&2\nexit 49\n')
        self.script(self.sb.tmp / "py2", "python", "exit 1\n")              # fails the version check
        self.script(self.sb.tmp / "launcher", "py", f'[ "$1" = -3 ] && shift\nexec "{sys.executable}" "$@"\n')
        env = self.path(stubs, self.sb.tmp / "py2", self.sb.tmp / "launcher")
        rc, out, _ = self.sb.launch("secret_guard", self.payload, env=env)
        self.assertEqual((rc, json.loads(out)["hookSpecificOutput"]["permissionDecision"]), (0, "deny"))


class TestHooksJson(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads(HOOKS_JSON.read_text())["hooks"]

    def guards(self):
        for event, groups in self.cfg.items():
            for g in groups:
                for h in g["hooks"]:
                    if "hooks/guards/" in h["command"]:
                        yield event, g.get("matcher", ""), h

    def test_every_guard_goes_through_the_launcher(self):
        seen = set()
        for event, matcher, h in self.guards():
            m = re.fullmatch(r'sh "\$\{CLAUDE_PLUGIN_ROOT\}/hooks/guards/run\.sh" (\w+)', h["command"])
            self.assertIsNotNone(m, h["command"])
            self.assertTrue((GUARDS / f"{m.group(1)}.py").is_file(), m.group(1))
            self.assertEqual(h["timeout"], 5)
            self.assertNotIn("args", h)                    # shell form: works under Git Bash too
            seen.add((event, m.group(1)))
        self.assertEqual(seen, {("PreToolUse", "secret_guard"), ("PostToolUse", "redact"),
                                ("PostToolUse", "vault_commit"), ("PostToolUseFailure", "redact"),
                                ("Stop", "vault_commit")})

    def test_matchers_cover_the_tools(self):
        def tools(event, guard):
            for e, matcher, h in self.guards():
                if e == event and h["command"].endswith(" " + guard):
                    return matcher
        def hits(matcher, name):
            if re.fullmatch(r"[\w|]+", matcher):                       # exact names
                return name in matcher.split("|")
            return re.search(matcher, name) is not None
        expect = {
            ("PreToolUse", "secret_guard"): (["Write", "Edit", "NotebookEdit", "Bash", "PowerShell", "Monitor"],
                                             ["Read", "Grep", "MultiEditX"]),
            ("PostToolUse", "redact"): (["Agent", "Bash", "PowerShell", "Read", "Grep", "WebFetch", "WebSearch",
                                         "TaskOutput", "ReadMcpResourceTool", "mcp__db__query"],
                                        ["Write", "Edit", "AgentX", "SubagentHandback"]),
            ("PostToolUse", "vault_commit"): (["Write", "Edit", "NotebookEdit", "Bash", "PowerShell"], ["Read"]),
        }
        for (event, guard), (yes, no) in expect.items():
            m = tools(event, guard)
            for name in yes:
                self.assertTrue(hits(m, name), f"{event} {guard} misses {name}")
            for name in no:
                self.assertFalse(hits(m, name), f"{event} {guard} matches {name}")

    def test_shepherd_entries_are_untouched_and_first(self):
        pre_cmds = [h["command"] for g in self.cfg["PreToolUse"] for h in g["hooks"]]
        self.assertIn("shepherd/hook.sh", pre_cmds[0])
        self.assertIn("hooks/guards/run.sh", pre_cmds[-1])
        vault = [h for e, _, h in self.guards() if e == "PostToolUse" and h["command"].endswith("vault_commit")]
        self.assertIs(vault[0].get("async"), True)


if __name__ == "__main__":
    unittest.main()
