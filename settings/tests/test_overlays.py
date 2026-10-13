"""The host overlays in overlays/: valid JSON, documented keys only, rules
that load, and every confidential value behind a {{NAME}} the example values
file lists. Needs no private values: tests substitute fakes."""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import merge  # noqa: E402

OVERLAYS = ROOT / "overlays"
AMAZON = OVERLAYS / "amazon.json"
GATES = OVERLAYS / "amazon-gates.json"
EXAMPLE = OVERLAYS / "private.example.json"
TEMPLATES = [ROOT / "user.template.json", ROOT / "gates.template.json"]

# From https://code.claude.com/docs/en/settings-reference (Claude Code 2.1.295);
# a new key needs the same check first.
DOCUMENTED = {
    "env", "awsAuthRefresh", "timeZone", "disableDeepLinkRegistration", "footerLinksRegexes",
    "extraKnownMarketplaces", "enabledPlugins", "permissions", "hooks", "skillOverrides", "autoMode",
}
# env-vars and amazon-bedrock pages, plus STATUSLINE_TZ, which this repo's
# status line reads.
DOCUMENTED_ENV = {
    "CLAUDE_CODE_USE_BEDROCK", "AWS_REGION", "AWS_PROFILE", "DISABLE_TELEMETRY",
    "DISABLE_ERROR_REPORTING", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_FABLE_MODEL", "STATUSLINE_TZ",
}


def example_names() -> list[str]:
    return [k for k in json.loads(EXAMPLE.read_text()) if not k.startswith("$")]


def fakes() -> dict[str, str]:
    """A stand-in for every private value, shaped well enough for the checks."""
    return {k: f"https://{k.lower().replace('_', '-')}.example/" if k.endswith("_URL")
            else f"fake-{k.lower()}" for k in example_names()}


def load(path: Path) -> dict:
    return merge.substitute(merge.load_json(path)[0],
                            {"DOTFILES": ROOT.parent.as_posix(), **fakes()}, path.name)


class Overlays(unittest.TestCase):
    def setUp(self):
        self.amazon, self.gates = load(AMAZON), load(GATES)

    def test_only_documented_keys(self):
        for o in (self.amazon, self.gates):
            self.assertLessEqual(set(o), DOCUMENTED)
            self.assertLessEqual(set(o.get("env", {})), DOCUMENTED_ENV)
            self.assertTrue(all(isinstance(v, str) for v in o.get("env", {}).values()))

    def test_placeholders_are_exactly_the_example_names(self):
        used = set()
        for path in (AMAZON, GATES):
            used |= set(merge.TOKEN.findall(path.read_text())) - {"DOTFILES"}
        self.assertEqual(used, set(example_names()))

    def test_the_example_values_cant_be_merged_as_they_are(self):
        for k, v in json.loads(EXAMPLE.read_text()).items():
            if not k.startswith("$"):
                self.assertRegex(v, merge.UNFILLED.pattern, k)
        with self.assertRaises(merge.MergeError):
            merge.load_values(EXAMPLE)

    def test_model_pins_are_inference_profile_ids(self):
        pins = {k: v for k, v in self.amazon["env"].items() if k.startswith("ANTHROPIC_DEFAULT_")}
        self.assertEqual(len(pins), 4)
        for k, v in pins.items():
            self.assertRegex(v, r"^(us|eu|apac|global)\.anthropic\.claude-[a-z]+-\d+(-\d+)?(\[1m\])?$", k)

    def test_footer_links(self):
        # JavaScript named groups, (?<name>...); Python spells them (?P<name>...).
        for e in self.amazon["footerLinksRegexes"]:
            self.assertEqual(e["type"], "regex")
            rx = re.compile(e["pattern"].replace("(?<", "(?P<"))
            for field in ("url", "label"):
                self.assertLessEqual(set(re.findall(r"\{(\w+)\}", e[field])), set(rx.groupindex))
            self.assertTrue(e["url"].startswith("https://"), e["url"])
            # No nested quantifiers: the pattern runs on the UI thread.
            self.assertNotRegex(e["pattern"], r"\)[+*]", e["pattern"])

    def test_marketplaces_and_plugins(self):
        markets = self.amazon["extraKnownMarketplaces"]
        for name, m in markets.items():
            self.assertEqual(m["source"]["source"], "directory", name)
            self.assertTrue(m["source"]["path"].startswith("/"), name)
        for plugin, on in self.amazon["enabledPlugins"].items():
            self.assertIn(plugin.rpartition("@")[2], markets, plugin)
            self.assertIs(on, True)

    def rules(self):
        p = self.gates["permissions"]
        return [r for k in ("allow", "ask", "deny") for r in p.get(k, [])]

    def test_rule_syntax(self):
        for r in self.rules():
            self.assertRegex(r, r"^[A-Za-z0-9_*-]+(\(.+\))?$", r)
            if r.startswith("mcp__"):  # Claude Code skips an mcp__ rule with parentheses
                self.assertNotIn("(", r)
            m = re.match(r"^(Read|Edit)\((.+)\)$", r)
            if m:  # user-wide path rules need ~/ or //
                self.assertTrue(m.group(2).startswith(("~/", "//")), r)
        for k, v in self.gates["permissions"].items():
            self.assertEqual(len(v), len(set(v)), k)

    def test_the_push_hook_entry(self):
        entry, = self.gates["hooks"]["PermissionRequest"]
        self.assertEqual(entry["matcher"], "Bash")
        hook, = entry["hooks"]
        self.assertEqual((hook["type"], hook["if"], hook["command"]),
                         ("command", "Bash(git *)", "python3"))
        self.assertLessEqual(hook["timeout"], 10)
        raw = merge.load_json(GATES)[0]["hooks"]["PermissionRequest"][0]["hooks"][0]["args"]
        self.assertEqual(raw[:2], ["-I", "{{DOTFILES}}/settings/hooks/allow_repo_push.py"])
        self.assertTrue((ROOT / "hooks" / "allow_repo_push.py").is_file())
        opts = dict(zip(raw[2::2], raw[3::2]))
        self.assertEqual(set(opts), {"--repo", "--url", "--remote", "--branch"})
        self.assertTrue(opts["--repo"].startswith("/"))
        self.assertEqual(opts["--url"], "{{VAULT_PUSH_URL}}")

    def test_auto_mode_keeps_the_defaults(self):
        am = self.gates["autoMode"]
        self.assertIsInstance(am["classifyAllShell"], bool)
        for k in ("environment", "soft_deny", "hard_deny"):
            self.assertEqual(am[k][0], "$defaults", k)
            self.assertEqual(len(am[k]), len(set(am[k])), k)

    def test_everything_merges_without_conflicts(self):
        log = []
        templates = [merge.substitute(merge.load_json(p)[0], {"DOTFILES": "/r"}, p.name)
                     for p in TEMPLATES]
        merge.merge({}, merge.combine(templates + [self.amazon, self.gates]), (), log)
        self.assertFalse([e for e in log if e[0] in ("keep", "override")])


class OverlaysThroughTheCli(unittest.TestCase):
    """The documented command, with a values file and without one."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="settings-overlays-"))
        self.addCleanup(shutil.rmtree, self.tmp)

    def run_merge(self, *argv) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = merge.run([str(a) for a in argv])
        return rc, err.getvalue()

    def test_values_fill_every_placeholder(self):
        values = self.tmp / "private.json"
        values.write_text(json.dumps(fakes()))
        out = self.tmp / "trial.json"
        rc, err = self.run_merge("--target", self.tmp / "settings.json", *TEMPLATES, AMAZON, GATES,
                                 "--values", values, "--out", out)
        self.assertEqual(rc, 0, err)
        text = out.read_text()
        self.assertNotIn("{{", text)
        for v in fakes().values():
            self.assertIn(v, text)

    def test_without_values_nothing_is_written(self):
        target = self.tmp / "settings.json"
        for overlay in (AMAZON, GATES):
            rc, err = self.run_merge("--target", target, *TEMPLATES, overlay, "--apply")
            self.assertEqual(rc, 2, err)
            self.assertIn("pass --values FILE", err)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
