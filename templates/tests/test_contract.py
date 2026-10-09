"""Checks for the operating-contract templates and the work overlay's private values.

    python3 -m unittest discover -s templates/tests

The private values file is optional: skills-amazon/contract/private.md (gitignored),
or the path in CONTRACT_PRIVATE. Without it the tests that need its values skip.
"""

import os
import re
import unittest
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent
GENERIC = TEMPLATES / "operating-contract.md"
WORK = TEMPLATES / "operating-contract.work.md"
EXAMPLE = TEMPLATES / "operating-contract.private.md.example"
README = TEMPLATES / "README.md"
PRIVATE_REL = "skills-amazon/contract/private.md"
PRIVATE = Path(os.environ.get("CONTRACT_PRIVATE") or TEMPLATES.parent / PRIVATE_REL)
HEADING = "# Operating contract: private values"
GENERIC_SECTIONS = ["Definition of done", "Evidence", "Ask-first list", "Landing work",
                    "Delegation brief", "Compact Instructions"]

PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
ENTRY = re.compile(r"^- ([a-z][a-z0-9_]*): (.+)$")
LITERAL = re.compile(r"`([^`]+)`")

# Shapes of confidential data, checked in every committed file whether or not the
# private values are present. Placeholders from the AWS and RFC 2606 docs are allowed.
SHAPES = [
    ("email address", re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)"),
     lambda m: re.search(r"(^|\.)example\.(com|org|net)$", m.group(1), re.I)),
    ("AWS account id", re.compile(r"(?<![\w.-])\d{12}(?![\w-])"),
     lambda m: m.group(0) in ("123456789012", "111122223333")),
    ("SharePoint host", re.compile(r"\b([\w-]+)\.sharepoint\.com\b", re.I),
     lambda m: m.group(1).lower() == "example"),
    ("internal host", re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:a2z\.com|aws\.dev|amazon\.dev|amazon\.com)\b",
                                 re.I),
     lambda m: m.group(0).lower() in ("git.amazon.com", "code.amazon.com")),
]


def parse_values(path):
    """{name: [literal, ...]} from a private values file: `- name: text` lines,
    where every backticked span in the text is a confidential literal."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ENTRY.match(line)
        if not m:
            continue
        if m.group(1) in values:
            raise AssertionError(f"{path}: {m.group(1)} is defined twice")
        values[m.group(1)] = LITERAL.findall(m.group(2))
    return values


def committed_files():
    """Every file under templates/ that git would pick up (only bytecode is ignored)."""
    for p in sorted(TEMPLATES.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            yield p


class TestGenericContract(unittest.TestCase):
    def setUp(self):
        self.text = GENERIC.read_text(encoding="utf-8")

    def test_stays_short(self):
        self.assertLessEqual(len(self.text.splitlines()), 40)

    def test_has_every_section(self):
        headings = re.findall(r"^## (.+)$", self.text, re.M)
        for name in GENERIC_SECTIONS:
            self.assertTrue(any(h.startswith(name) for h in headings), name)

    def test_stands_alone(self):
        self.assertEqual(PLACEHOLDER.findall(self.text), [])


class TestWorkOverlay(unittest.TestCase):
    def setUp(self):
        self.text = WORK.read_text(encoding="utf-8")
        self.example = parse_values(EXAMPLE)

    def test_every_placeholder_has_an_example_and_back(self):
        self.assertEqual(set(PLACEHOLDER.findall(self.text)), set(self.example))

    def test_example_marks_each_value(self):
        self.assertEqual(EXAMPLE.read_text(encoding="utf-8").splitlines()[0], HEADING)
        for name, literals in self.example.items():
            self.assertTrue(literals, f"{name}: no backticked value")

    def test_names_the_private_heading(self):
        self.assertIn(HEADING.lstrip("# "), self.text)

    def test_sections_extend_generic_ones(self):
        for h in re.findall(r"^## (.+)$", self.text, re.M):
            self.assertTrue(any(h.startswith(name) for name in GENERIC_SECTIONS), h)


class TestCommittedFiles(unittest.TestCase):
    def test_no_confidential_shapes(self):
        hits = []
        for p in committed_files():
            text = p.read_text(encoding="utf-8", errors="replace")
            for label, rx, allowed in SHAPES:
                hits += [f"{p.relative_to(TEMPLATES)}: {label} {m.group(0)}"
                         for m in rx.finditer(text) if not allowed(m)]
        self.assertEqual(hits, [])

    def test_readme_imports_existing_files(self):
        text = README.read_text(encoding="utf-8")
        names = re.findall(r"^\s*@~/claude-dotfiles/templates/(\S+)$", text, re.M)
        self.assertIn("operating-contract.md", names)
        self.assertIn("operating-contract.work.md", names)
        for name in names:
            self.assertTrue((TEMPLATES / name).is_file(), name)
        self.assertRegex(text, r"(?m)^\s*@~/claude-dotfiles/" + re.escape(PRIVATE_REL) + "$")


@unittest.skipUnless(PRIVATE.is_file(), f"no private values at {PRIVATE}: copy {EXAMPLE.name} "
                     f"there and fill it in, or set CONTRACT_PRIVATE")
class TestPrivateValues(unittest.TestCase):
    def setUp(self):
        self.values = parse_values(PRIVATE)
        self.example = parse_values(EXAMPLE)

    def test_defines_every_placeholder(self):
        self.assertEqual(PRIVATE.read_text(encoding="utf-8").splitlines()[0], HEADING)
        self.assertEqual(set(self.values), set(self.example))
        for name, literals in self.values.items():
            self.assertTrue(literals, f"{name}: no backticked value")
            self.assertFalse(set(literals) & set(self.example[name]), f"{name}: still the example")

    def test_no_value_committed(self):
        # A trailing glob is dropped so `x_*` also catches `x_daily`; shorter
        # stems are too generic to search for.
        needles = {lit.rstrip("*").lower() for lits in self.values.values() for lit in lits}
        needles = {n for n in needles if len(n) >= 4}
        hits = []
        for p in committed_files():
            text = p.read_text(encoding="utf-8", errors="replace").lower()
            hits += [f"{p.relative_to(TEMPLATES)}: a private value from {PRIVATE.name}"
                     for n in sorted(needles) if n in text]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
