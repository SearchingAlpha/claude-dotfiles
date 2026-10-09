"""Latency budget: the guards run on every tool call. Timed the way hooks.json
runs them (sh -c → run.sh → python3), process starts included. Medians are
printed so a slow machine shows where the time goes."""

import json
import sys
import unittest

from support import FAKE, Sandbox, bash_out, post, pre, read_out, rnd, stop

BUDGET_MS = 100                  # typical call, wall clock, shells and python start included
DETECT_BUDGET_MS = 200           # a detection also asks git whether the path is ignored


class TestLatency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sb = Sandbox()
        cls.repo = cls.sb.repo()
        code = "".join(f"def f{i}(x):\n    return os.environ.get('TOKEN_{i}', x) * {i}\n" for i in range(600))
        cls.cases = {
            "secret_guard Bash": ("secret_guard", pre("Bash", {"command": "git status && pytest -q tests/"})),
            "secret_guard Write 30KB": ("secret_guard", pre("Write", {"file_path": str(cls.repo / "m.py"),
                                                                      "content": code})),
            "redact Bash 30k chars": ("redact", post("Bash", {"command": "make"},
                                                     bash_out((rnd(70) + " ok\n") * 400))),
            "redact Read 120KB": ("redact", post("Read", {"file_path": "/m.py"},
                                                 read_out("/m.py", code * 4))),
            "vault_commit inert": ("vault_commit", post("Edit", {"file_path": "/x/a.md"}, {})),
            "vault_commit Stop inert": ("vault_commit", stop()),
        }

    @classmethod
    def tearDownClass(cls):
        cls.sb.cleanup()

    def test_typical_calls(self):
        for name, (guard, payload) in self.cases.items():
            ms = self.sb.timed(guard, payload)
            sys.stderr.write(f"\n  {name}: {ms:.0f} ms")
            with self.subTest(name):
                self.assertLess(ms, BUDGET_MS)

    def test_detection(self):
        payload = pre("Write", {"file_path": str(self.repo / "c.py"), "content": f"K = '{FAKE.anthropic}'"})
        rc, out, _ = self.sb.launch("secret_guard", payload)
        self.assertEqual((rc, json.loads(out)["hookSpecificOutput"]["permissionDecision"]), (0, "deny"))
        ms = self.sb.timed("secret_guard", payload, n=9)
        sys.stderr.write(f"\n  secret_guard deny (git check-ignore): {ms:.0f} ms\n")
        self.assertLess(ms, DETECT_BUDGET_MS)


if __name__ == "__main__":
    unittest.main()
