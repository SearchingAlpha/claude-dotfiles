#!/usr/bin/env python3
"""Checks for the fleet presets: the sources preset loads through the mod's own
parser and judges sample lines as its README says, and no confidential value
sits in a file of the mod that git would commit.

    python3 mods/fleet/presets/test_presets.py

The preset checks run the mod's hooks/fleet.ts under Node (type stripping,
22.6 or later) and are skipped without it. The deny-list check reads the
confidential values from skills-amazon/fleet/private.json (gitignored; another
path with FLEET_PRIVATE) and is skipped without it: committed checks never need
the values themselves. Python 3.9, standard library only.
"""

import json
import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(MOD))
PRESET = os.path.join(HERE, "sources.json")
EXAMPLE = os.path.join(HERE, "private.example.json")
PRIVATE = os.environ.get("FLEET_PRIVATE") or os.path.join(REPO, "skills-amazon", "fleet", "private.json")

# The mod's own sourcesOf, globParts and verdictOfLines, so the preset is judged
# by the code that reads it, not by a copy of its rules.
BRIDGE = r"""
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

const [fleet, preset, samplesJson] = process.argv.slice(1)
const { globParts, sourcesOf, verdictOfLines } = await import(pathToFileURL(fleet).href)
const got = sourcesOf([], JSON.parse(readFileSync(preset, 'utf8')))
const errors = [...got.errors]
const now = Date.now()

for (const s of [...got.runs, ...got.lines]) {
  try {
    globParts(s.glob, '/home/someone')
  } catch (error) {
    errors.push(error.message)
  }
}

const verdicts = JSON.parse(samplesJson).map(([glob, text, ageMinutes]) => {
  const s = got.lines.find(l => l.glob === glob)

  return s === undefined ? null : verdictOfLines(text, s.match, s.ok, now - ageMinutes * 60_000, now, s.runningMinutes, s.start).verdict
})

console.log(JSON.stringify({ errors, runs: got.runs.map(s => s.label), lines: got.lines.map(s => s.label), verdicts }))
"""

WBR_WEEKLY = "~/.claude/logs/wbr-weekly-*.log"
SHORTLOAD = "~/shortload-weekly.log"
MANIFEST = "~/projects/agentic-marketplace/site/ops/weekly-run.txt"
DAY = 24 * 60

# (glob, log text, minutes since its last write, verdict): the formats the
# preset README describes, one line per case that matters.
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


def node_or_skip(case):
    """The node binary when it can strip types (22.6+), else skips the test."""
    node = shutil.which("node")
    if node is None:
        case.skipTest("node not found: the preset was not parsed by the mod's own code")
    out = subprocess.run([node, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    got = re.match(r"v(\d+)\.(\d+)", out)
    if got is None or (int(got.group(1)), int(got.group(2))) < (22, 6):
        case.skipTest(f"node {out or '?'} cannot strip types (22.6 or later): the preset was not parsed")
    return node


class TestPreset(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(PRESET, encoding="utf-8") as f:
            cls.preset = json.load(f)

    def bridge(self):
        node = node_or_skip(self)
        samples = json.dumps([[glob, text, age] for glob, text, age, _ in SAMPLES])
        out = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e",
                              BRIDGE, os.path.join(MOD, "hooks", "fleet.ts"), PRESET, samples],
                             capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_every_entry_loads_through_the_mod(self):
        got = self.bridge()
        self.assertEqual(got["errors"], [])
        self.assertEqual(got["runs"], [e["label"] for e in self.preset["runs"]])
        self.assertEqual(got["lines"], [e["label"] for e in self.preset["lines"]])

    def test_sample_lines_read_as_the_readme_says(self):
        got = self.bridge()["verdicts"]
        for (glob, text, age, want), verdict in zip(SAMPLES, got):
            with self.subTest(glob=glob, text=text.strip().splitlines()[-1], age=age):
                self.assertEqual(verdict, want)

    def test_paths_are_under_home(self):
        # `~/` keeps the preset working on another desktop with the same layout.
        for entry in self.preset["runs"] + self.preset["lines"]:
            with self.subTest(glob=entry["glob"]):
                self.assertTrue(entry["glob"].startswith("~/"))


# A confidential value has a shape a pattern can catch, or it is listed in the
# private deny list. Words (a tool, a server, a project name) are fine to commit.
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


def committed_files():
    """The mod's files git would commit: tracked, or untracked and not ignored."""
    try:
        out = subprocess.run(["git", "-C", REPO, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                              "--", os.path.relpath(MOD, REPO)], capture_output=True, check=False)
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
    def test_the_scan_sees_the_mod(self):
        files = committed_files()
        if files is None:
            self.skipTest(f"{REPO} is not a git checkout")
        names = {os.path.basename(p) for p in files}
        self.assertTrue({"register.ts", "sources.json", "private.example.json"} <= names, sorted(names))
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
                  "docs.aws" + ".amazon.com", "fleet@claude-dotfiles", "typescript@6.0.3", "ISO-8601"]
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
                found += [f"{rel}: {what}: {m.group(0)}" for m in pattern.finditer(text) if m.group(0) not in ALLOWED]
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
