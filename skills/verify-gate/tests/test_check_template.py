"""templates/check.sh honours the output contract the gate and Claude rely on."""

import re
import subprocess
import unittest

from support import TEMPLATE, Sandbox


class TemplateCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.repo = self.sb.make_repo(check=None)

    def tearDown(self):
        self.sb.cleanup()

    def check(self, *flags, **slots):
        text = TEMPLATE.read_text()
        for name, value in slots.items():
            text, n = re.subn(rf'^{name}=""', f"{name}={value!r}", text, flags=re.M)
            self.assertEqual(n, 1, name)
        self.sb.write("scripts/check", text, mode=0o755)
        p = subprocess.run([str(self.repo / "scripts" / "check"), *flags], cwd=str(self.sb.tmp),
                           capture_output=True, text=True, env=self.sb.env, timeout=60)
        return p.returncode, p.stdout.splitlines()


class TestTemplate(TemplateCase):
    def test_unconfigured_cannot_judge(self):
        rc, lines = self.check()
        self.assertEqual(rc, 127)
        self.assertTrue(lines[-1].startswith("ERROR no steps configured"))

    def test_pass(self):
        rc, lines = self.check(LINT="true", TESTS="test -f verdict.txt")  # runs in the repo root
        self.assertEqual(rc, 0)
        self.assertEqual(lines[-1], "CHECK PASS 2/2")

    def test_fail_stops_early_in_fast_and_keeps_20_lines(self):
        noisy = "for i in $(seq 1 50); do echo out $i; done; exit 3"
        rc, lines = self.check(LINT=noisy, TESTS="true")
        self.assertEqual(rc, 1)
        self.assertEqual(lines[-1], "ERROR lint failed (exit 3); 0/1 steps passed")
        self.assertLessEqual(len(lines) - 1, 20)
        self.assertIn("out 50", lines)
        self.assertTrue(lines[0].startswith("FAIL  lint"))

    def test_full_runs_everything_and_uses_tests_full(self):
        rc, lines = self.check("--full", LINT="false", TESTS="false", TESTS_FULL="true",
                               EXTRA_FULL="true")
        self.assertEqual(rc, 1)
        self.assertEqual(lines[-1], "ERROR lint failed (exit 1); 2/3 steps passed")
        rc, lines = self.check("--full", TESTS="true")
        self.assertEqual((rc, lines[-1]), (0, "CHECK PASS 1/1"))

    def test_missing_tool_is_127(self):
        rc, lines = self.check(TESTS="no-such-tool-xyz")
        self.assertEqual(rc, 127)
        self.assertIn("command not found", lines[-1])

    def test_steps_do_not_eat_stdin(self):
        rc, lines = self.check(LINT="cat", TESTS="true")
        self.assertEqual((rc, lines[-1]), (0, "CHECK PASS 2/2"))

    def test_slots_are_shell_commands(self):
        rc, lines = self.check(LINT="echo a | grep -q a && test -d scripts", TESTS="true || false")
        self.assertEqual((rc, lines[-1]), (0, "CHECK PASS 2/2"))

    def test_unknown_flag(self):
        rc, lines = self.check("--bogus", TESTS="true")
        self.assertEqual(rc, 127)
        self.assertTrue(lines[-1].startswith("ERROR unknown option"))

    def test_a_failure_inside_a_pipe_fails_the_step(self):
        for cmd in ("python3 -c 'raise SystemExit(1)' 2>&1 | tail -n 5",
                    "sh -c 'echo 1 failed; exit 1' | tee /dev/null"):
            rc, lines = self.check(TESTS=cmd)
            self.assertEqual((rc, lines[-1]), (1, "ERROR tests failed (exit 1); 0/1 steps passed"),
                             cmd)

    def test_help_prints_the_header(self):
        rc, lines = self.check("--help", TESTS="true")
        self.assertEqual(rc, 0)
        self.assertTrue(lines[0].startswith("# scripts/check"))
        self.assertIn("run the check again", lines[-1])


class TestWithTheGate(TemplateCase):
    def test_a_noisy_failure_keeps_its_status_line(self):
        noisy = "for i in $(seq 1 60); do echo lint-problem-$i; done; exit 1"
        rc, lines = self.check("--full", LINT=noisy, TYPES="true", TESTS="true")
        self.assertEqual((rc, len(lines)), (1, 21))  # 20 summary lines, then the verdict
        self.sb.write("src/app.py", "x = 2\n")
        r = self.sb.stop(args=["--mode", "full"])
        self.assertEqual(r[0], 2)
        tail = r.reason.split("\n\n")[-1].strip().splitlines()
        timed = re.compile(r"\d+s\b")  # each step's seconds differ from run to run
        self.assertEqual([timed.sub("Ns", x) for x in tail], [timed.sub("Ns", x) for x in lines])
        self.assertTrue(tail[0].startswith("FAIL  lint"))  # Claude sees all of it


if __name__ == "__main__":
    unittest.main()
