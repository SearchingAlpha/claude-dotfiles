#!/usr/bin/env python3
"""Checks for herd's run-source presets: the sources preset loads through herd's own parser
and judges sample lines as its README says, and no confidential value sits in a file of
herd (or of the herd mod) that git would commit.

    python3 harnesses/herd/presets/test_presets.py

The deny-list check reads the confidential values from skills-amazon/herd/private.json
(gitignored; another path with HERD_PRIVATE) and is skipped without it: committed checks
never need the values themselves. Python 3.9, standard library only.
"""

import json
import os
import re
import subprocess
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HERD = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(HERD))
PRESET = os.path.join(HERE, "sources.json")
EXAMPLE = os.path.join(HERE, "private.example.json")
PRIVATE = os.environ.get("HERD_PRIVATE") or os.path.join(REPO, "skills-amazon", "herd", "private.json")
# What the scan covers: herd, and the mod that draws its snapshot (under its old name until it moves).
SCANNED = [os.path.relpath(p, REPO) for p in (HERD, os.path.join(REPO, "mods", "herd"), os.path.join(REPO, "mods", "fleet"))
           if os.path.isdir(p)]

sys.path.insert(0, HERD)
import herd  # noqa: E402

WBR_WEEKLY = "~/.claude/logs/wbr-weekly-*.log"
SHORTLOAD = "~/shortload-weekly.log"
MANIFEST = "~/projects/agentic-marketplace/site/ops/weekly-run.txt"
DAY = 24 * 60

# (glob, log text, minutes since its last write, verdict): the formats the preset README
# describes, one line per case that matters.
SAMPLES = [
    (WBR_WEEKLY, "[09:43:53][wbr-weekly] === Weekly run done; wbr_rc=0 retail_rc=0 d2d_rc=0 ===\n", DAY, "ok"),
    (WBR_WEEKLY, "[09:43:53][wbr-weekly] === Weekly run done; wbr_rc=0 retail_rc=1 d2d_rc=0 ===\n", DAY, "failed"),
    (WBR_WEEKLY, "[09:30:01][wbr-weekly] SKIP: cookie stale (171401s old) — not running.\n", DAY, "failed"),
    (WBR_WEEKLY, "[09:30:01][wbr-weekly] === Weekly run start; target week=2026-41 ===\n", 5, "running"),
    (SHORTLOAD, "=== pull_leads ===\nstep 0\n=== watchdog ===\nwatchdog: todo en orden\n", DAY, "ok"),
    (SHORTLOAD, "=== pull_leads ===\n=== watchdog ===\nwatchdog: PROBLEMAS DETECTADOS\n", DAY, "failed"),
    # Last week's verdict before this week's start is not this week's.
    (SHORTLOAD, "=== pull_leads ===\nwatchdog: todo en orden\n=== pull_leads ===\nstep 0\n", 30, "running"),
    (SHORTLOAD, "=== pull_leads ===\nwatchdog: todo en orden\n=== pull_leads ===\nstep 0\n", DAY, "no line"),
    (MANIFEST, "ts 2026-10-05T07:00:02+00:00\npull_leads 0\n", DAY, "ok"),
]


class TestPreset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PRESET, encoding="utf-8") as f:
            cls.preset = json.load(f)
        cls.got = herd.sources_of([], cls.preset)

    def test_every_entry_loads(self):
        self.assertEqual(self.got["errors"], [])
        self.assertEqual([s["label"] for s in self.got["runs"]], [e["label"] for e in self.preset["runs"]])
        self.assertEqual([s["label"] for s in self.got["lines"]], [e["label"] for e in self.preset["lines"]])
        for s in self.got["runs"] + self.got["lines"]:
            with self.subTest(glob=s["glob"]):
                herd.glob_parts(s["glob"], "/home/someone")

    def test_sample_lines_read_as_the_readme_says(self):
        now = time.time() * 1000
        for glob, text, age, want in SAMPLES:
            s = next(x for x in self.got["lines"] if x["glob"] == glob)
            with self.subTest(glob=glob, text=text.strip().splitlines()[-1], age=age):
                got = herd.verdict_of_lines(text, s["match"], s["ok"], now - age * 60_000, now, s["runningMinutes"], s["start"])
                self.assertEqual(got["verdict"], want)

    def test_paths_are_under_home(self):
        # `~/` keeps the preset working on another desktop with the same layout.
        for entry in self.preset["runs"] + self.preset["lines"]:
            with self.subTest(glob=entry["glob"]):
                self.assertTrue(entry["glob"].startswith("~/"))


# A confidential value has a shape a pattern can catch, or it is listed in the private
# deny list. Words (a tool, a server, a project name) are fine to commit.
PATTERNS = [
    ("an account id", re.compile(r"(?<![\w-])\d{12}(?![\w-])")),
    ("an email address", re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")),
    ("an internal host", re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:a2z\.com|aws\.dev|amazon\.dev)\b", re.I)),
    # A host under amazon.com other than the plain domain words and public AWS.
    ("an amazon.com host", re.compile(r"\b(?!(?:code|git|www)\.amazon\.com\b)(?![\w.-]*\baws\.amazon\.com\b)"
                                      r"[\w-]+(?:\.[\w-]+)*\.amazon\.com\b", re.I)),
    ("a SharePoint site", re.compile(r"\b[\w-]+\.sharepoint\.com\b", re.I)),
    ("a ticket or review id", re.compile(r"\b(?:V\d{10}|CR-\d{6,})\b")),
]
ALLOWED = {"noreply@anthropic.com"}
# Addresses at the domains reserved for examples (RFC 2606), as the tests use them.
RESERVED = re.compile(r"@(?:[\w-]+\.)*example\.(?:com|org|net)$", re.I)


def committed_files():
    """The scanned files git would commit: tracked, or untracked and not ignored."""
    try:
        out = subprocess.run(["git", "-C", REPO, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                              "--", *SCANNED], capture_output=True, check=False)
    except OSError:
        return None
    if out.returncode != 0:
        return None
    return sorted({p for p in out.stdout.decode("utf-8").split("\0") if p})


def texts():
    files = committed_files()
    if files is None:
        raise unittest.SkipTest(f"{REPO} is not a git checkout: nothing to scan")
    for rel in files:
        try:
            with open(os.path.join(REPO, rel), encoding="utf-8") as f:
                yield rel, f.read()
        except (UnicodeDecodeError, FileNotFoundError):
            continue


def deny_list(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    values = data.get("deny") if isinstance(data, dict) else None
    if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
        raise ValueError(f'{path}: needs {{"deny": ["<value>", ...]}}')
    return [v.strip() for v in values]


class TestNothingConfidentialIsCommitted(unittest.TestCase):
    def test_the_scan_sees_herd(self):
        files = committed_files()
        if files is None:
            self.skipTest(f"{REPO} is not a git checkout")
        names = {os.path.basename(p) for p in files}
        self.assertTrue({"herd.py", "sources.json", "private.example.json"} <= names, sorted(names))
        self.assertFalse([p for p in files if "/.claude-plugin/types/" in p], "engine declarations are not ignored")

    def test_each_pattern_catches_what_it_names(self):
        # Built at run time, so this file never holds a value the scan would find.
        caught = {
            "an account id": ["1234" + "56789012"],
            "an email address": ["someone" + "@corp.example"],
            "an internal host": ["app.beta" + ".a2z.com", "x" + ".aws.dev"],
            "an amazon.com host": ["tool.corp" + ".amazon.com"],
            "a SharePoint site": ["team" + ".sharepoint.com"],
            "a ticket or review id": ["V12345" + "67890", "CR-" + "1234567"],
        }
        passed = ["11111111-1111-4111-8111-" + "1" * 12, "17600000" + "00000", "code" + ".amazon.com",
                  "docs.aws" + ".amazon.com", "herd@claude-dotfiles", "typescript@6.0.3", "ISO-8601"]
        for what, pattern in PATTERNS:
            for value in caught[what]:
                with self.subTest(what=what, value=value):
                    self.assertIsNotNone(pattern.search(f"see {value} here"))
            for value in passed:
                with self.subTest(what=what, passes=value):
                    self.assertIsNone(pattern.search(f"see {value} here"))

    def test_no_value_with_a_confidential_shape(self):
        found = []
        for rel, text in texts():
            for what, pattern in PATTERNS:
                found += [f"{rel}: {what}: {m.group(0)}" for m in pattern.finditer(text)
                          if m.group(0) not in ALLOWED and not RESERVED.search(m.group(0))]
        self.assertEqual(found, [])

    def test_no_value_from_the_private_deny_list(self):
        if not os.path.exists(PRIVATE):
            self.skipTest(f"no {PRIVATE}: the deny list was not checked (copy {os.path.relpath(EXAMPLE, REPO)}"
                          " there and list the values)")
        values = deny_list(PRIVATE)
        self.assertFalse([v for v in values if v.startswith("<")], "the private deny list still holds placeholders")
        found = []
        for rel, text in texts():
            lower = text.lower()
            # Say where, never what: the value itself stays out of the output.
            found += [f"{rel}: deny-list entry {i + 1}" for i, v in enumerate(values) if v.lower() in lower]
        self.assertEqual(found, [])

    def test_the_example_holds_placeholders_only(self):
        values = deny_list(EXAMPLE)
        self.assertTrue(values)
        self.assertEqual([v for v in values if not re.fullmatch(r"<[^<>]+>", v)], [])


if __name__ == "__main__":
    unittest.main()
