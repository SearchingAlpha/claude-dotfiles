#!/usr/bin/env python3
"""Tests for the adapter presets, preset.py, and what the mod's committed files may hold.

    python3 -m unittest discover -s mods/omnigraph/presets -p 'test_*.py'
    python3 mods/omnigraph/presets/test_presets.py --regen   # rewrite tests/presets.ts

The mod's TypeScript tests read every preset, and a synthetic build in the
SupplyChainAgents shape, through tests/presets.ts. A test here fails when that
file no longer matches the presets, so the options a person merges and the
ones the tests check cannot drift apart.

The boundary tests check the committed files of the mod for confidential
values: shapes (an account id, an email, an internal host, a ticket id)
always, and, when the gitignored values file is there, its own values and the
real build's ids and version. Without it they say so and skip that part.

`check` runs `claude plugin test`: those tests skip when claude is not on PATH.
Python 3.9, standard library only.
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD = HERE.parent
FIXTURE = MOD / "tests" / "presets.ts"
SCRIPT = HERE / "preset.py"

_spec = importlib.util.spec_from_file_location("omnigraph_preset", SCRIPT)
preset = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(preset)

HAS_CLAUDE = shutil.which(os.environ.get("MODS_CLAUDE", "claude")) is not None

# A build in the shape the SupplyChainAgents compile writes: records keyed id,
# label, props; edges from, type, to, props; a manifest with `ov`, counts and
# the open conflicts, which name no node. Every name and id is made up.
SAMPLE_NODES = [
    ("Metric", "metric:fill-rate", "Fill rate", "curated", "docs/metrics.md:12"),
    ("Metric", "metric:dwell-time", "Dwell time", "proposed", "docs/metrics.md:30"),
    ("Def", "def:fill-rate", "Fill rate, as the weekly report counts it", "proposed", "docs/defs.md:4"),
    ("Def", "def:dwell-time", "Dwell time, door to shelf", "proposed", "docs/defs.md:19"),
    ("Rule", "rule:weekly-report/fill-rate-drop", "Flag a fill-rate drop over two points", "proposed", "skills/weekly-report/SKILL.md:40"),
    ("Rule", "rule:weekly-report/dwell-outlier", "Flag dwell outliers", "curated", "skills/weekly-report/SKILL.md:52"),
    ("Skill", "skill:weekly-report", "Weekly report", "curated", "skills/weekly-report/SKILL.md:1"),
    ("Dataset", "dataset:shipments-daily", "Daily shipments", "proposed", "docs/data.md:8"),
    ("Column", "column:shipments-daily/units", "units", "proposed", "docs/data.md:15"),
    ("System", "system:yard", "Yard system", "deprecated", "docs/systems.md:3"),
]
SAMPLE_EDGES = [
    ("rule:weekly-report/fill-rate-drop", "CONCERNS", "metric:fill-rate"),
    ("rule:weekly-report/fill-rate-drop", "READS", "dataset:shipments-daily"),
    ("rule:weekly-report/dwell-outlier", "CONCERNS", "metric:dwell-time"),
    ("rule:weekly-report/dwell-outlier", "READS", "dataset:shipments-daily"),
    ("def:fill-rate", "ON_METRIC", "metric:fill-rate"),
    ("def:dwell-time", "ON_METRIC", "metric:dwell-time"),
    ("dataset:shipments-daily", "USED_BY", "skill:weekly-report"),
    ("column:shipments-daily/units", "COLUMN_OF", "dataset:shipments-daily"),
    ("skill:weekly-report", "RUNS", "rule:weekly-report/fill-rate-drop"),
    ("skill:weekly-report", "RUNS", "rule:weekly-report/dwell-outlier"),
    ("metric:dwell-time", "READS", "system:yard"),
    ("dataset:shipments-daily", "READS", "system:yard"),
]
SAMPLE_CONFLICT = "conflict:def/fill-rate"

# Shapes of confidential values no committed file of the mod holds.
SHAPES = {
    "an account id": re.compile(r"(?<![\w.:/-])\d{12}(?![\w.-])"),
    "an email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
    "an internal host": re.compile(r"[\w-]+\.(?:a2z\.com|aws\.dev|corp\.amazon\.com)\b|[\w-]+\.amazon\.(?:com|lu|de|es|fr|it|co\.uk)\b"),
    "a ticket id": re.compile(r"\b(?:V\d{9,}|CR-\d{5,}|P\d{8,})\b"),
}
# Caches git ignores; the engine's declarations (.claude-plugin/types/) are left out too.
SKIPPED_DIRS = {"__pycache__", "node_modules"}


def sample_build():
    """The synthetic build's three files, as the SupplyChainAgents compile spells them."""
    nodes = "".join(
        json.dumps({"id": id_, "label": label, "props": {"name": name, "status": status, "source": [source], "method": "extracted"}}) + "\n"
        for label, id_, name, status, source in SAMPLE_NODES
    )
    edges = "".join(json.dumps({"from": a, "type": t, "to": b, "props": {}}) + "\n" for a, t, b in SAMPLE_EDGES)

    def counts(items):
        out = {}
        for item in items:
            out[item] = out.get(item, 0) + 1
        return dict(sorted(out.items()))

    manifest = {
        "ov": "5ca1ab1e0001",
        "schema_version": 1,
        "nodes": len(SAMPLE_NODES),
        "edges": len(SAMPLE_EDGES),
        "labels": counts(n[0] for n in SAMPLE_NODES),
        "edge_types": counts(e[1] for e in SAMPLE_EDGES),
        "status": counts(n[3] for n in SAMPLE_NODES),
        "open_conflicts": [SAMPLE_CONFLICT],
        "sha256": hashlib.sha256((nodes + edges).encode()).hexdigest(),
    }
    return {"manifest": json.dumps(manifest, indent=1) + "\n", "nodes": nodes, "edges": edges}


def fixture_text():
    """What tests/presets.ts should hold: every preset as its JSON file has it, and the synthetic build."""
    presets = {name: json.loads((HERE / f"{name}.json").read_text(encoding="utf-8")) for name in preset.preset_names()}
    build = sample_build()
    return (
        "// Generated by `python3 mods/omnigraph/presets/test_presets.py --regen` from\n"
        "// presets/*.json, and a synthetic build in the SupplyChainAgents shape (made-up\n"
        "// names and ids). Do not edit by hand.\n"
        "export type Preset = {\n"
        "  about: string\n"
        "  private: string[]\n"
        "  options: Record<string, string | boolean>\n"
        "  samples: { ops: [string, string][]; notOps: string[] }\n"
        "}\n\n"
        "export const PRESETS: { " + "; ".join(f"readonly {name}: Preset" for name in presets) + " } = {\n"
        + "".join(f"  {name}: {json.dumps(body, ensure_ascii=False)},\n" for name, body in presets.items())
        + "}\n\n"
        f"export const SCA_CONFLICT = {json.dumps(SAMPLE_CONFLICT)}\n\n"
        "export const SCA_BUILD = {\n" + "".join(f"  {key}: {json.dumps(build[key])},\n" for key in ("manifest", "nodes", "edges")) + "}\n"
    )


def write_build(folder, build):
    folder.mkdir(parents=True, exist_ok=True)
    for key, name in (("manifest", "manifest.json"), ("nodes", "nodes.jsonl"), ("edges", "edges.jsonl")):
        (folder / name).write_text(build[key], encoding="utf-8")


def run_preset(*args, timeout=240):
    done = subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True, text=True, timeout=timeout)
    return done.returncode, done.stdout, done.stderr


def committed_files(root=MOD):
    """Every file of the mod that git would commit: the engine's declarations and caches left out."""
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).parts
        if path.is_file() and rel[:2] != (".claude-plugin", "types") and not set(rel) & SKIPPED_DIRS:
            yield path


def strings_in(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)


def private_values(path):
    """What the values file at `path` says no committed file may hold: its own values, and the
    version and the ids (open conflicts too) of the real build its checkStore names."""
    values = json.loads(path.read_text(encoding="utf-8-sig"))
    secrets = {s for s in strings_in([values.get("options", {}), values.get("neverCommit", [])]) if len(s) >= 3 and not preset.is_placeholder(s)}
    ids = set()
    store = values.get("checkStore")
    if isinstance(store, str) and not preset.is_placeholder(store) and Path(store, "nodes.jsonl").is_file():
        manifest = json.loads(Path(store, "manifest.json").read_text(encoding="utf-8-sig"))
        if isinstance(manifest.get("ov"), str):
            secrets.add(manifest["ov"])
        for line in Path(store, "nodes.jsonl").read_text(encoding="utf-8-sig").splitlines():
            with contextlib.suppress(ValueError, TypeError, KeyError):
                ids.add(json.loads(line)["id"])
        ids.update(s for s in manifest.get("open_conflicts", []) if isinstance(s, str))
    return secrets, ids


def shape_hits(root=MOD):
    hits = []
    for path in committed_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        hits += [f"{path.relative_to(root)}: {what}: {m.group(0)}" for what, shape in SHAPES.items() for m in shape.finditer(text)]
    return hits


def value_hits(secrets, ids, root=MOD):
    # An id counts where it stands alone, not inside a longer id, word or path.
    id_shape = re.compile("|".join(r"(?<![\w:/.-])" + re.escape(i) + r"(?![\w/-])" for i in sorted(ids, key=len, reverse=True))) if ids else None
    hits = []
    for path in committed_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        hits += [f"{path.relative_to(root)}: a private value" for s in sorted(secrets) if s in text]
        if id_shape is not None:
            hits += [f"{path.relative_to(root)}: a real build's id: {m.group(0)}" for m in id_shape.finditer(text)]
    return hits


class PresetFiles(unittest.TestCase):
    def test_every_preset_fits_the_mods_options(self):
        self.assertTrue(preset.preset_names())
        for name in preset.preset_names():
            with self.subTest(name):
                body = preset.load_preset(name)
                self.assertEqual(set(body["options"]), set(json.loads(preset.PLUGIN_JSON.read_text())["userConfig"]),
                                 "a preset sets every option, so merging it leaves no old value behind")

    def test_a_preset_that_does_not_fit_is_refused_with_the_reason(self):
        schema = json.loads(preset.PLUGIN_JSON.read_text())["userConfig"]
        good = json.loads((HERE / "sca.json").read_text())
        self.assertEqual(preset.preset_problems(good, schema), [])
        cases = {
            "options.colour: the mod has no such option": lambda p: p["options"].update(colour="red"),
            "options.band: a boolean": lambda p: p["options"].update(band="yes"),
            "private: owner must be an option left empty in the preset": lambda p: p["options"].update(owner="someone"),
            "samples.ops: a non-empty list of [command, op name]": lambda p: p["samples"].update(ops=[]),
        }
        for want, spoil in cases.items():
            with self.subTest(want):
                bad = json.loads(json.dumps(good))
                spoil(bad)
                self.assertIn(want, preset.preset_problems(bad, schema))
        self.assertIn("not a JSON object", preset.preset_problems([], schema))
        with self.assertRaises(preset.PresetError):
            preset.load_preset("../sca")

    def test_each_preset_has_an_example_values_file_of_placeholders_only(self):
        for name in preset.preset_names():
            with self.subTest(name):
                example = json.loads(preset.example_of(name).read_text())
                body = preset.load_preset(name)
                self.assertLessEqual(set(example), preset.VALUES_KEYS)
                self.assertEqual(set(example["options"]), set(body["private"]))
                self.assertTrue(all(preset.is_placeholder(s) for s in strings_in(example)), "the example holds no real value")

    def test_the_mods_fixture_matches_the_presets(self):
        self.assertEqual(FIXTURE.read_text(encoding="utf-8"), fixture_text(), "run: python3 mods/omnigraph/presets/test_presets.py --regen")


class Block(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.values = Path(self.tmp.name) / "private.json"

    def block(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = preset.main(["block", "sca", *args])
        return code, out.getvalue(), err.getvalue()

    def test_without_a_values_file_the_owner_is_left_empty_and_it_says_where_the_file_goes(self):
        repo = preset.REPO
        preset.REPO = Path(self.tmp.name)
        self.addCleanup(setattr, preset, "REPO", repo)
        code, out, err = self.block()
        self.assertEqual(code, 0)
        options = json.loads(out)["pluginConfigs"]["omnigraph@claude-dotfiles"]["options"]
        self.assertEqual(options["owner"], "")
        self.assertEqual(options, json.loads((HERE / "sca.json").read_text())["options"])
        self.assertIn(f"no values file at {Path(self.tmp.name) / 'skills-amazon' / 'omnigraph-sca' / 'private.json'}: owner left empty", err)
        self.assertIn("sca.private.example.json", err)

    def test_the_values_file_fills_the_owner_and_the_plugin_id_is_chosen(self):
        self.values.write_text(json.dumps({"options": {"owner": "curator"}}))
        code, out, err = self.block("--values", str(self.values), "--plugin", "omnigraph@inline")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["pluginConfigs"]["omnigraph@inline"]["options"]["owner"], "curator")

    def test_a_values_file_that_is_missing_unfilled_or_overreaching_is_refused(self):
        cases = {
            "still the example's placeholder": {"options": {"owner": "<your alias>"}},
            "the preset takes only ['owner'] from a values file": {"options": {"cmdRegex": "x"}},
            "not a key of a values file": {"owner": "curator"},
            "neverCommit: a list of strings": {"neverCommit": "x"},
        }
        for want, values in cases.items():
            with self.subTest(want):
                self.values.write_text(json.dumps(values))
                code, out, err = self.block("--values", str(self.values))
                self.assertEqual((code, out), (2, ""))
                self.assertIn(want, err)
        code, out, err = self.block("--values", str(Path(self.tmp.name) / "absent.json"))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no values file at", err)

    def test_only_an_omnigraph_plugin_id_is_taken(self):
        code, out, err = self.block("--plugin", "other@claude-dotfiles")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("an omnigraph@<marketplace> id", err)


class Boundary(unittest.TestCase):
    """No confidential value in a committed file of the mod: not the word, the values."""

    def test_no_file_holds_a_confidential_shape(self):
        self.assertEqual(shape_hits(), [])

    def test_no_file_holds_a_private_value_or_a_real_builds_id(self):
        path = preset.values_path("sca")
        if not path.is_file():
            self.skipTest(f"no values file at {path}: only the shapes are checked")
        secrets, ids = private_values(path)
        print(f"\n  boundary: {len(secrets)} private values and {len(ids)} real ids, over {len(list(committed_files()))} files", file=sys.stderr)
        self.assertEqual(value_hits(secrets, ids), [])

    def test_the_checks_find_what_they_look_for_and_pass_what_is_allowed(self):
        # Spelled in pieces, so this file holds none of them whole.
        email, account, host, ticket = "someone" + "@example-corp.co.uk", "1234" + "56789012", "wiki." + "a2z.com", "V" + "1234567890"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hooks").mkdir()
            (root / "hooks" / "leak.ts").write_text(
                f"// {email}, account {account}, https://{host}/x, {ticket}\n"
                "const owner = 'curator-alias' // rule:weekly-report/fill-rate-drop\n"
            )
            (root / "hooks" / "fine.ts").write_text(
                "// omnigraph@claude-dotfiles, at 1791500000000, rule:weekly-report/fill-rate-drop-extra, a/rule:weekly-report/fill-rate-drop\n"
            )
            (root / ".claude-plugin" / "types").mkdir(parents=True)
            (root / ".claude-plugin" / "types" / "x.d.ts").write_text(f"// {email}: the engine's, gitignored\n")

            self.assertEqual(shape_hits(root), [
                f"hooks/leak.ts: an account id: {account}",
                f"hooks/leak.ts: an email address: {email}",
                f"hooks/leak.ts: an internal host: {host}",
                f"hooks/leak.ts: a ticket id: {ticket}",
            ])
            self.assertEqual(value_hits({"curator-alias"}, {"rule:weekly-report/fill-rate-drop", "def:x"}, root), [
                "hooks/leak.ts: a private value",
                "hooks/leak.ts: a real build's id: rule:weekly-report/fill-rate-drop",
            ])


@unittest.skipUnless(HAS_CLAUDE, "claude is not on PATH: the check runs claude plugin test")
class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.values = self.tmp / "private.json"
        self.values.write_text(json.dumps({"options": {"owner": "curator"}}))

    def check(self, build, *args):
        write_build(self.tmp / "build", build)
        return run_preset("check", "sca", "--store", str(self.tmp / "build"), "--values", str(self.values), "--work", str(self.tmp / "work"), *args)

    def test_a_build_in_the_presets_shape_passes_through_the_hooks(self):
        code, out, err = self.check(sample_build())
        # claude plugin test prints the check's console lines on stderr.
        self.assertEqual(code, 0, out + err)
        self.assertIn('"nodes":10,"edges":12', out + err)
        self.assertIn('"feed":[["compile.py",0,0,0],["compile.py",0,0,1]],"exported":true', out + err)
        self.assertEqual(list((self.tmp / "work").iterdir()), [], "the copy of the build is removed")

    def test_a_build_the_preset_does_not_fit_fails(self):
        build = sample_build()
        build["nodes"] += "not a record\n"
        code, out, err = self.check(build)
        self.assertEqual(code, 1, out + err)
        self.assertIn('"unreadable":1', out + err)
        self.assertIn("1 pass", out + err, "the hooks still read the records that parse")

    def test_no_build_named_says_how_to_name_one(self):
        code, out, err = run_preset("check", "sca", "--values", str(self.values))
        self.assertEqual(code, 2)
        self.assertIn("no build to check: pass --store DIR, or set checkStore in", err)

    def test_the_real_build(self):
        path = preset.values_path("sca")
        if not path.is_file():
            self.skipTest(f"no values file at {path}: the real build is not checked")
        store = json.loads(path.read_text(encoding="utf-8-sig")).get("checkStore")
        if not isinstance(store, str) or preset.is_placeholder(store) or not Path(store).is_dir():
            self.skipTest(f"no checkStore in {path}: the real build is not checked")
        code, out, err = run_preset("check", "sca", "--work", str(self.tmp / "work"))
        self.assertEqual(code, 0, out + err)


if __name__ == "__main__":
    if sys.argv[1:] == ["--regen"]:
        FIXTURE.write_text(fixture_text(), encoding="utf-8")
        print(f"wrote {FIXTURE}")
    else:
        unittest.main()
