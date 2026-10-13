"""No confidential value in a committed file of settings/ or statusline/.

Amazon tool and server names, model ids, personal paths and plain domain
words (code.amazon.com, *.a2z.com) may be committed; account ids, specific
internal hosts and URLs, schema names, emails and ticket or review ids may
not. Those live in the gitignored values file (overlays/private.example.json
lists them). Two checks: shapes that are never fine to commit, which needs
nothing private, and the private values themselves, skipped when the values
file isn't on this machine."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent
PRIVATE = REPO / "skills-amazon" / "settings" / "private.json"
SCANNED = (ROOT, REPO / "statusline")

# Plain domain words the boundary rule allows; any other host on these
# domains is a specific internal host.
ALLOWED_HOSTS = {"code.amazon.com", "git.amazon.com"}
INTERNAL_HOST = re.compile(r"(?<![\w*.-])((?:[a-z0-9-]+\.)+(?:amazon\.com|a2z\.com|aws\.dev|"
                           r"amazon\.dev|amazon\.lu|sharepoint\.com))", re.I)
# A name ending in a letter TLD, so `@playwright/mcp@0.0.83` isn't one.
EMAIL = re.compile(r"[\w.+-]+@((?:[\w-]+\.)+[A-Za-z]{2,})\b")
ALLOWED_EMAIL_DOMAINS = {"example.invalid", "example.com", "anthropic.com", "github.com"}
SHAPES = {
    "an AWS account id": re.compile(r"(?<![\w.-])\d{12}(?![\w-])"),
    "a URL path on an internal code host": re.compile(r"\b(?:code|git)\.amazon\.com[/:]\w", re.I),
    "a code review id": re.compile(r"\bCR-\d{8,10}\b"),
    "a ticket id": re.compile(r"\b[DPV]\d{9,10}\b"),
}


def committed_files() -> list[Path]:
    """The component's files git would commit: tracked, or untracked and not
    ignored. Without git, every file outside caches."""
    if shutil.which("git"):
        r = subprocess.run(["git", "-C", str(REPO), "ls-files", "-co", "--exclude-standard", "-z",
                            "--", *(str(p) for p in SCANNED)], capture_output=True, timeout=30)
        if r.returncode == 0:
            return [REPO / n for n in r.stdout.decode().split("\0") if n and (REPO / n).is_file()]
    return [p for d in SCANNED for p in d.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts]


def texts():
    for p in committed_files():
        try:
            yield p.relative_to(REPO), p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


class NoConfidentialShapes(unittest.TestCase):
    def test_no_specific_internal_host(self):
        for rel, text in texts():
            hosts = {h.lower() for h in INTERNAL_HOST.findall(text)} - ALLOWED_HOSTS
            self.assertFalse(hosts, f"{rel}: {sorted(hosts)}")

    def test_no_email_address_but_placeholders(self):
        for rel, text in texts():
            domains = {d.lower() for d in EMAIL.findall(text)} - ALLOWED_EMAIL_DOMAINS
            self.assertFalse(domains, f"{rel}: {sorted(domains)}")

    def test_no_ids_or_internal_urls(self):
        for rel, text in texts():
            for what, rx in SHAPES.items():
                m = rx.search(text)
                self.assertIsNone(m, f"{rel}: {what}: {m.group(0) if m else ''}")

    def test_the_scan_sees_the_component(self):
        names = {p.name for p in committed_files()}
        self.assertLessEqual({"merge.py", "amazon-gates.json", "statusline.sh"}, names)

    def test_the_patterns_tell_values_from_words(self):
        # The positive samples are glued at run time: this file is scanned too.
        def hosts(s):
            return {h.lower() for h in INTERNAL_HOST.findall(s)} - ALLOWED_HOSTS

        j = "".join
        self.assertEqual(hosts(j(["see app.beta.example-team", ".a2z.com and t.corp", ".amazon.com, ",
                                  "team", ".sharepoint", ".com/sites/x"])),
                         {j(["app.beta.example-team", ".a2z.com"]), j(["t.corp", ".amazon.com"]),
                          j(["team", ".sharepoint.com"])})
        self.assertEqual(hosts("*.amazon.com, *.a2z.com, *.aws.dev; code.amazon.com (reviews), "
                               "git.amazon.com and *.d.codeartifact.us-west-2.amazonaws.com"), set())
        for what, sample in (("an AWS account id", j(["account 123456", "789012 (us-east-1)"])),
                             ("a URL path on an internal code host", j(["git.amazon", ".com/pkg/X"])),
                             ("a URL path on an internal code host", j(["code.amazon", ".com/x/"])),
                             ("a code review id", j(["see CR-", "123456789"])),
                             ("a ticket id", j(["filed V", "1234567890"]))):
            self.assertRegex(sample, SHAPES[what])
        for sample in ("bak-20261009-110839", "pattern [DPV]\\\\d{9,10}", "CR-\\\\d{8,10}",
                       "a code.amazon.com review", "1234567890123"):
            self.assertFalse([w for w, rx in SHAPES.items() if rx.search(sample)], sample)
        self.assertEqual(set(EMAIL.findall(j(["t@example.invalid, x@", "corp.example.lu, ",
                                              "npx -y @playwright/mcp@0.0.83"])))
                         - ALLOWED_EMAIL_DOMAINS, {"corp.example.lu"})


class NoPrivateValue(unittest.TestCase):
    def setUp(self):
        if not PRIVATE.exists():
            self.skipTest(f"{PRIVATE.relative_to(REPO)} not on this machine; the shape checks "
                          "still ran")
        data = json.loads(PRIVATE.read_text(encoding="utf-8-sig"))
        self.values = {k: v for k, v in data.items() if not k.startswith("$")}

    def test_no_private_value_in_a_committed_file(self):
        # Also the value without its scheme and trailing slash: a doc would
        # write a URL that way.
        needles = {}
        for k, v in self.values.items():
            for n in (v, re.sub(r"^[a-z]+://", "", v).rstrip("/")):
                if len(n) >= 6:
                    needles[n] = k
        for rel, text in texts():
            for n, k in needles.items():
                self.assertNotIn(n, text, f"{rel} holds the value of {k}")

    def test_the_values_file_is_gitignored(self):
        if not shutil.which("git"):
            self.skipTest("git not on PATH")
        r = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", str(PRIVATE)],
                           capture_output=True, timeout=10)
        if r.returncode == 128:
            self.skipTest("not a git checkout")
        self.assertEqual(r.returncode, 0, f"{PRIVATE} is not gitignored")


if __name__ == "__main__":
    unittest.main()
