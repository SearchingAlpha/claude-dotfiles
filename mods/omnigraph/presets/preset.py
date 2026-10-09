#!/usr/bin/env python3
"""Adapter presets for the omnigraph mod: the settings for one producer's build, and a check of a real one.

    python3 mods/omnigraph/presets/preset.py list
    python3 mods/omnigraph/presets/preset.py block <preset> [--plugin ID] [--values FILE]
    python3 mods/omnigraph/presets/preset.py check <preset> [--store DIR] [--values FILE] [--work DIR] [--keep]

A preset is `<name>.json` beside this file (README.md has the format). The
options that belong to one person (the patch owner) are empty there and come
from a gitignored values file, by default `skills-amazon/omnigraph-<name>/private.json`
under the repo root; `<name>.private.example.json` shows its shape.

block  prints the `pluginConfigs` block to merge into ~/.claude/settings.json
       (settings/merge.py previews and applies it). It writes nothing.
check  runs the mod's hooks over a real build with the preset's options: a copy
       of the mod in a new temporary folder gets the build embedded as a test
       fixture and real-store.check.ts as its test, `claude plugin test` runs
       it with no network, and the copy is removed. The build is only read.

Exit: 0 fine, 1 the check failed, 2 bad input or a missing tool.
Python 3.9, standard library only.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOD = HERE.parent
REPO = MOD.parent.parent
PLUGIN_JSON = MOD / ".claude-plugin" / "plugin.json"
CHECK_TEST = HERE / "real-store.check.ts"

PRESET_KEYS = {"about", "private", "options", "samples"}
VALUES_KEYS = {"options", "checkStore", "neverCommit"}
DEFAULT_PLUGIN = "omnigraph@claude-dotfiles"
# The mod reads no store file over 4 MiB, so the check refuses one too.
FILE_MAX = 4 * 1024 * 1024
TYPES = {"string": str, "boolean": bool}
# What `claude plugin test` gets, as mods/check.sh gives it: a minimal
# environment, every request to a closed local port, no telemetry.
PASSED_ENV = ("HOME", "USER", "LOGNAME", "PATH", "SHELL", "TERM", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
              "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR")
CLOSED = "http://127.0.0.1:9"


class PresetError(Exception):
    """Bad input: a preset, a values file or a build the command cannot use."""


def note(text):
    print(f"preset.py: {text}", file=sys.stderr)


def read_json(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise PresetError(f"{path}: {exc}") from None


def is_placeholder(value):
    """An example file's `<...>` value: not filled in yet."""
    return isinstance(value, str) and value.startswith("<")


def preset_names():
    return sorted(p.stem for p in HERE.glob("*.json") if "." not in p.stem)


def example_of(name):
    return HERE / f"{name}.private.example.json"


def values_path(name, given=None):
    return Path(given).expanduser() if given else REPO / "skills-amazon" / f"omnigraph-{name}" / "private.json"


# ------------------------------------------------------------------ presets

def preset_problems(preset, schema):
    """What makes `preset` unusable against the mod's userConfig `schema`; empty when nothing."""
    if not isinstance(preset, dict):
        return ["not a JSON object"]
    problems = []
    if set(preset) != PRESET_KEYS:
        problems.append(f"keys must be {sorted(PRESET_KEYS)}, not {sorted(preset)}")
    if not isinstance(preset.get("about"), str):
        problems.append("about: a string")
    options = preset.get("options")
    if not isinstance(options, dict):
        return problems + ["options: an object"]
    for key, value in options.items():
        kind = TYPES.get(schema.get(key, {}).get("type"))
        if key not in schema:
            problems.append(f"options.{key}: the mod has no such option")
        elif kind is None or type(value) is not kind:
            problems.append(f"options.{key}: a {schema[key].get('type')}")
    private = preset.get("private")
    if not isinstance(private, list) or not all(isinstance(key, str) for key in private):
        problems.append("private: a list of option names")
    else:
        for key in private:
            if options.get(key) != "":
                problems.append(f"private: {key} must be an option left empty in the preset")
    samples = preset.get("samples")
    ops = samples.get("ops") if isinstance(samples, dict) else None
    not_ops = samples.get("notOps") if isinstance(samples, dict) else None
    if not isinstance(samples, dict) or set(samples) != {"ops", "notOps"}:
        problems.append("samples: an object with ops and notOps")
    elif not (isinstance(ops, list) and ops and all(isinstance(op, list) and len(op) == 2 and all(isinstance(s, str) for s in op) for op in ops)):
        problems.append("samples.ops: a non-empty list of [command, op name]")
    elif not (isinstance(not_ops, list) and all(isinstance(s, str) for s in not_ops)):
        problems.append("samples.notOps: a list of commands")
    return problems


def load_preset(name):
    path = HERE / f"{name}.json"
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) or not path.is_file():
        raise PresetError(f"no preset {name!r}; the presets are: {', '.join(preset_names()) or 'none'}")
    preset = read_json(path)
    problems = preset_problems(preset, read_json(PLUGIN_JSON)["userConfig"])
    if problems:
        raise PresetError(f"{path}: " + "; ".join(problems))
    return preset


# ------------------------------------------------------------------ values

def values_problems(values, preset):
    """What makes a values file unusable with `preset`; empty when nothing. A `<...>` option is one."""
    if not isinstance(values, dict):
        return ["not a JSON object"]
    problems = [f"{key}: not a key of a values file (those are {sorted(VALUES_KEYS)})" for key in sorted(set(values) - VALUES_KEYS)]
    options = values.get("options", {})
    if not isinstance(options, dict):
        problems.append("options: an object")
    else:
        for key, value in options.items():
            if key not in preset["private"]:
                problems.append(f"options.{key}: the preset takes only {preset['private']} from a values file")
            elif not isinstance(value, str):
                problems.append(f"options.{key}: a string")
            elif is_placeholder(value):
                problems.append(f"options.{key}: still the example's placeholder; fill it in or leave it out")
    if "checkStore" in values and not isinstance(values["checkStore"], str):
        problems.append("checkStore: a path")
    never = values.get("neverCommit", [])
    if not isinstance(never, list) or not all(isinstance(item, str) for item in never):
        problems.append("neverCommit: a list of strings")
    return problems


def load_values(name, preset, given=None):
    """(the values, their path); (None, path) when the default file is absent. A missing --values file is an error."""
    path = values_path(name, given)
    if not path.is_file():
        if given:
            raise PresetError(f"no values file at {path}")
        return None, path
    values = read_json(path)
    problems = values_problems(values, preset)
    if problems:
        raise PresetError(f"{path}: " + "; ".join(problems))
    return values, path


def options_of(preset, values):
    """The preset's options with the values file's private ones; and the private ones still empty."""
    options = dict(preset["options"])
    options.update((values or {}).get("options", {}))
    return options, [key for key in preset["private"] if options[key] == ""]


def block_of(options, plugin):
    return {"pluginConfigs": {plugin: {"options": options}}}


# ------------------------------------------------------------------ check

def store_texts(store, options):
    """The build's three files as the mod reads them (a byte-order mark kept: the mod drops it)."""
    texts = {}
    for key, option in (("manifest", "manifestFile"), ("nodes", "nodesFile"), ("edges", "edgesFile")):
        path = store / options[option]
        if not path.is_file():
            raise PresetError(f"no {option} at {path}")
        size = path.stat().st_size
        if size > FILE_MAX:
            raise PresetError(f"{path} is {size} bytes: the mod reads no store file over 4 MiB")
        texts[key] = path.read_text(encoding="utf-8")
    return texts


def fixture_text(store, texts, options, samples):
    return (
        f"// Written by presets/preset.py check from {json.dumps(str(store))}, read-only.\n"
        "// A copy of a real build: it lives in a temporary folder and is never committed.\n"
        f"export const OPTIONS = {json.dumps(options, indent=1, ensure_ascii=False)}\n\n"
        f"export const SAMPLES: {{ ops: [string, string][]; notOps: string[] }} = {json.dumps(samples, ensure_ascii=False)}\n\n"
        "export const BUILD = {\n" + "".join(f"  {key}: {json.dumps(texts[key])},\n" for key in ("manifest", "nodes", "edges")) + "}\n"
    )


def lay_copy(copy, fixture):
    """The mod's plugin, hooks and contract, the stub world, and the check as its one test."""
    (copy / ".claude-plugin").mkdir()
    shutil.copy2(PLUGIN_JSON, copy / ".claude-plugin" / "plugin.json")
    if (MOD / ".claude-plugin" / "types").is_dir():
        shutil.copytree(MOD / ".claude-plugin" / "types", copy / ".claude-plugin" / "types")
    for part in ("hooks", "types"):
        shutil.copytree(MOD / part, copy / part)
    shutil.copy2(MOD / "tsconfig.json", copy / "tsconfig.json")
    (copy / "tests").mkdir()
    for name in ("fixtures.ts", "demo-store.ts"):
        shutil.copy2(MOD / "tests" / name, copy / "tests" / name)
    shutil.copy2(CHECK_TEST, copy / "tests" / "real-store.test.ts")
    (copy / "tests" / "real-store.ts").write_text(fixture, encoding="utf-8")


def claude_env():
    env = {key: os.environ[key] for key in PASSED_ENV if os.environ.get(key)}
    env.update({"HTTPS_PROXY": CLOSED, "https_proxy": CLOSED, "HTTP_PROXY": CLOSED, "http_proxy": CLOSED,
                "CLAUDE_CODE_MAX_RETRIES": "0", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
    return env


def check(name, store=None, values_file=None, work=None, keep=False, timeout=180):
    preset = load_preset(name)
    values, path = load_values(name, preset, values_file)
    given = store or (values or {}).get("checkStore")
    if not given or is_placeholder(given):
        raise PresetError(f"no build to check: pass --store DIR, or set checkStore in {path} ({example_of(name).name} has the shape)")
    store = Path(given).expanduser().resolve()
    options, missing = options_of(preset, values)
    if missing:
        note(f"{', '.join(missing)} empty: the export is checked without {'it' if len(missing) == 1 else 'them'}")
    texts = store_texts(store, options)
    claude = shutil.which(os.environ.get("MODS_CLAUDE", "claude"))
    if claude is None:
        raise PresetError("claude not found on PATH (or MODS_CLAUDE)")
    if work is not None:
        Path(work).mkdir(parents=True, exist_ok=True)
    copy = Path(tempfile.mkdtemp(prefix="omnigraph-check-", dir=work))
    try:
        lay_copy(copy, fixture_text(store, texts, options, preset["samples"]))
        note(f"checking {store} with the {name} preset (claude plugin test, a copy in {copy})")
        try:
            code = subprocess.run([claude, "plugin", "test", str(copy)], env=claude_env(), timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            note(f"claude plugin test took over {timeout} s")
            code = 1
    finally:
        if keep:
            note(f"kept {copy}: it holds a copy of the build, remove it when done")
        else:
            shutil.rmtree(copy, ignore_errors=True)
    return 0 if code == 0 else 1


# ------------------------------------------------------------------ CLI

def main(argv=None):
    ap = argparse.ArgumentParser(prog="preset.py", description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="the presets, one per line")
    block = sub.add_parser("block", help="print the pluginConfigs block for ~/.claude/settings.json")
    block.add_argument("preset")
    block.add_argument("--plugin", default=DEFAULT_PLUGIN, help=f"the plugin id (default {DEFAULT_PLUGIN}; omnigraph@inline for a --plugin-dir copy)")
    block.add_argument("--values", help="the values file (default skills-amazon/omnigraph-<preset>/private.json)")
    chk = sub.add_parser("check", help="run the hooks over a real build with the preset")
    chk.add_argument("preset")
    chk.add_argument("--store", help="the build folder (default: checkStore in the values file)")
    chk.add_argument("--values", help="the values file (default skills-amazon/omnigraph-<preset>/private.json)")
    chk.add_argument("--work", help="where the temporary copy goes (default: the system's temporary folder)")
    chk.add_argument("--keep", action="store_true", help="keep the copy (it holds the build) to rerun or type-check it")
    chk.add_argument("--timeout", type=int, default=180, help="seconds claude plugin test may take (default 180)")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "list":
            for name in preset_names():
                print(f"{name}\t{read_json(HERE / f'{name}.json').get('about', '')}")
            return 0
        if a.cmd == "block":
            if not re.fullmatch(r"omnigraph@[\w.-]+", a.plugin):
                raise PresetError(f"--plugin {a.plugin!r}: an omnigraph@<marketplace> id")
            preset = load_preset(a.preset)
            values, path = load_values(a.preset, preset, a.values)
            options, missing = options_of(preset, values)
            if values is None and missing:
                note(f"no values file at {path}: {', '.join(missing)} left empty. Copy {example_of(a.preset)} there and fill it in.")
            elif missing:
                note(f"{path} leaves {', '.join(missing)} empty")
            print(json.dumps(block_of(options, a.plugin), indent=2, ensure_ascii=False))
            return 0
        return check(a.preset, a.store, a.values, a.work, a.keep, a.timeout)
    except PresetError as exc:
        note(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
