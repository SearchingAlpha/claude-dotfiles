"""merge.py: deep-merge semantics, dry run, atomic apply, refusals."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import merge  # noqa: E402


def run(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = merge.run([str(a) for a in argv])
    return rc, out.getvalue(), err.getvalue()


class MergeSemantics(unittest.TestCase):
    def test_objects_merge_recursively_and_new_keys_are_added(self):
        log = []
        got = merge.merge({"a": {"x": 1}}, {"a": {"y": 2}, "b": True}, (), log)
        self.assertEqual(got, {"a": {"x": 1, "y": 2}, "b": True})
        self.assertEqual([(a, p) for a, p, _ in log], [("add", ("a", "y")), ("add", ("b",))])

    def test_arrays_union_keeps_existing_order_and_appends_new(self):
        got = merge.merge({"l": ["b", "a"]}, {"l": ["a", "c", "b", "d"]})
        self.assertEqual(got["l"], ["b", "a", "c", "d"])

    def test_array_union_compares_objects_by_value(self):
        hook = {"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}
        same = json.loads(json.dumps(hook))
        got = merge.merge({"h": [hook]}, {"h": [same, {"matcher": "Edit"}]})
        self.assertEqual(got["h"], [hook, {"matcher": "Edit"}])

    def test_existing_scalar_wins_by_default(self):
        log = []
        got = merge.merge({"s": {"command": "old"}}, {"s": {"command": "new"}}, (), log)
        self.assertEqual(got["s"]["command"], "old")
        self.assertEqual(log[0][:2], ("keep", ("s", "command")))

    def test_override_all(self):
        got = merge.merge({"a": 1, "b": {"c": 2}}, {"a": 9, "b": {"c": 8}}, None)
        self.assertEqual(got, {"a": 9, "b": {"c": 8}})

    def test_override_key_is_a_dotted_prefix(self):
        got = merge.merge({"s": {"command": "old", "pad": 0}, "t": 1},
                          {"s": {"command": "new", "pad": 2}, "t": 2}, [("s",)])
        self.assertEqual(got, {"s": {"command": "new", "pad": 2}, "t": 1})
        got = merge.merge({"s": {"command": "old", "pad": 0}},
                          {"s": {"command": "new", "pad": 2}}, [("s", "command")])
        self.assertEqual(got["s"], {"command": "new", "pad": 0})
        # "s" must not match a sibling that merely starts with the same letters.
        got = merge.merge({"sx": 1}, {"sx": 2}, [("s",)])
        self.assertEqual(got["sx"], 1)

    def test_override_key_replaces_an_array_whole(self):
        # An upgraded hook or rule must replace the old one, not sit next to
        # it: the old, looser hook would keep answering.
        old_hook = {"matcher": "Bash", "hooks": [{"command": "h", "args": ["--repo", "/v"]}]}
        new_hook = {"matcher": "Bash", "hooks": [{"command": "h", "args": ["--repo", "/v",
                                                                         "--url", "u"]}]}
        target = {"permissions": {"ask": ["Bash(old *)"], "deny": ["Read(x)"]},
                  "hooks": {"PermissionRequest": [old_hook], "Stop": [{"hooks": []}]}}
        overlay = {"permissions": {"ask": ["Bash(new *)"], "deny": ["Read(y)"]},
                   "hooks": {"PermissionRequest": [new_hook]}}
        log = []
        got = merge.merge(target, overlay, [("permissions", "ask"), ("hooks", "PermissionRequest")], log)
        self.assertEqual(got["permissions"]["ask"], ["Bash(new *)"])
        self.assertEqual(got["permissions"]["deny"], ["Read(x)", "Read(y)"])  # not named: union
        self.assertEqual(got["hooks"], {"PermissionRequest": [new_hook], "Stop": [{"hooks": []}]})
        self.assertIn(("replace", ("hooks", "PermissionRequest")), [(a, p) for a, p, _ in log])
        # A parent path covers the arrays below it.
        got = merge.merge(target, overlay, [("permissions",)])
        self.assertEqual(got["permissions"], overlay["permissions"])
        # --override (None) is about scalars: arrays still union.
        got = merge.merge(target, overlay, None)
        self.assertEqual(got["permissions"]["ask"], ["Bash(old *)", "Bash(new *)"])
        self.assertEqual(len(got["hooks"]["PermissionRequest"]), 2)
        # An identical array is no change at all.
        log = []
        merge.merge({"l": [1, 2]}, {"l": [1, 2]}, [("l",)], log)
        self.assertEqual(log, [])

    def test_type_mismatch_is_a_conflict(self):
        self.assertEqual(merge.merge({"a": [1]}, {"a": {"x": 1}}), {"a": [1]})
        self.assertEqual(merge.merge({"a": [1]}, {"a": {"x": 1}}, None), {"a": {"x": 1}})

    def test_later_overlay_wins_over_earlier(self):
        got = merge.combine([{"a": 1, "l": [1], "o": {"x": 1}}, {"a": 2, "l": [2], "o": {"y": 2}}])
        self.assertEqual(got, {"a": 2, "l": [1, 2], "o": {"x": 1, "y": 2}})

    def test_status_line_is_kept_or_replaced_whole(self):
        old = {"type": "command", "command": "old.sh", "padding": 2}
        new = {"type": "command", "command": "new.sh", "refreshInterval": 1}
        log = []
        got = merge.merge({"statusLine": old}, {"statusLine": new}, (), log)
        self.assertEqual(got["statusLine"], old)  # no refreshInterval on the old command
        self.assertEqual([(a, p) for a, p, _ in log], [("keep", ("statusLine",))])
        for key in (("statusLine",), ("statusLine", "command")):
            got = merge.merge({"statusLine": old}, {"statusLine": new}, [key])
            self.assertEqual(got["statusLine"], new, key)  # no padding from the old one
        got = merge.merge({}, {"subagentStatusLine": new})
        self.assertEqual(got["subagentStatusLine"], new)

    def test_marketplace_entries_are_atomic_but_new_ones_are_added(self):
        target = {"extraKnownMarketplaces": {"m": {"source": {"source": "github", "repo": "a/b"}}}}
        overlay = {"extraKnownMarketplaces": {
            "m": {"source": {"source": "directory", "path": "/x"}},
            "n": {"source": {"source": "directory", "path": "/y"}}}}
        got = merge.merge(target, overlay)
        self.assertEqual(got["extraKnownMarketplaces"]["m"], target["extraKnownMarketplaces"]["m"])
        self.assertEqual(got["extraKnownMarketplaces"]["n"]["source"]["path"], "/y")

    def test_later_overlay_replaces_an_atomic_value_whole(self):
        got = merge.combine([{"statusLine": {"command": "a", "padding": 1}, "fallbackModel": ["x", "y"]},
                             {"statusLine": {"command": "b"}, "fallbackModel": ["z"]}])
        self.assertEqual(got, {"statusLine": {"command": "b"}, "fallbackModel": ["z"]})

    def test_remove_key(self):
        log = []
        got = merge.remove_keys({"env": {"A": "1", "B": "2"}}, [("env", "A"), ("env", "Z"), ("nope", "x")], log)
        self.assertEqual(got, {"env": {"B": "2"}})
        self.assertEqual([a for a, _, _ in log], ["remove", "absent", "absent"])

    def test_lost_detects_dropped_values_only(self):
        before = {"a": 1, "l": ["x"], "o": {"k": "v"}}
        self.assertEqual(merge.lost(before, {"a": 1, "l": ["x", "y"], "o": {"k": "v", "n": 1}}, []), [])
        self.assertEqual(merge.lost(before, {"a": 2, "l": [], "o": {}}, []),
                         [("a",), ("l", merge.ITEM), ("o", "k")])
        self.assertEqual(merge.lost(before, {"a": 2, "l": ["x"]}, [("a",), ("o",)]), [])

    def test_substitute_dotfiles_and_reject_unknown_tokens(self):
        got = merge.substitute({"c": "{{DOTFILES}}/x.sh", "l": ["{{DOTFILES}}"], "n": 1},
                               {"DOTFILES": "/r"}, "t")
        self.assertEqual(got, {"c": "/r/x.sh", "l": ["/r"], "n": 1})
        with self.assertRaises(merge.MergeError):
            merge.substitute({"c": "{{NOPE}}"}, {"DOTFILES": "/r"}, "t")
        # Single braces (prose in autoMode rules) are left alone.
        self.assertEqual(merge.substitute("{COMPANY}", {}, "t"), "{COMPANY}")

    def test_paths_are_dotted_with_escapes_or_json_pointers(self):
        # Marketplace and plugin names may contain dots, so a key is never
        # split on one unless PATH says so.
        for text, want in (("env.FOO", ("env", "FOO")), ("statusLine", ("statusLine",)),
                           ("modelSettings.claude-opus-4\\.8", ("modelSettings", "claude-opus-4.8")),
                           ("a\\\\.b", ("a\\", "b")), ("a\\x.b", ("a\\x", "b")),
                           ("/extraKnownMarketplaces/my.market", ("extraKnownMarketplaces", "my.market")),
                           ("/a~1b/c~0d/~01", ("a/b", "c~d", "~1")), ("/", ("",))):
            self.assertEqual(merge.parse_path(text), want, text)
        for bad in ("", "a..b", ".a", "a."):
            with self.assertRaises(merge.MergeError, msg=bad):
                merge.parse_path(bad)
        for path in (("env", "FOO"), ("m", "a.b"), ("x/y", "z"), ("a\\b",), ("", "k"), ("t~1",)):
            self.assertEqual(merge.parse_path(merge.fmt(path)), path, path)
        self.assertEqual(merge.fmt(("env", "FOO")), "env.FOO")
        self.assertEqual(merge.fmt(("m", "a.b")), "/m/a.b")
        self.assertEqual(merge.fmt(("l", merge.ITEM)), "l[]")

    def test_a_key_with_a_dot_stays_one_key(self):
        # An atomic marketplace entry named with a dot is kept or replaced
        # whole, never mixed field by field.
        old = {"source": {"source": "github", "repo": "a/b"}}
        new = {"source": {"source": "directory", "path": "/x"}}
        target = {"extraKnownMarketplaces": {"my.market": old}}
        overlay = {"extraKnownMarketplaces": {"my.market": new}}
        log = []
        got = merge.merge(target, overlay, (), log)
        self.assertEqual(got, target)
        self.assertEqual([(a, p) for a, p, _ in log],
                         [("keep", ("extraKnownMarketplaces", "my.market"))])
        for key in (("extraKnownMarketplaces", "my.market"),
                    ("extraKnownMarketplaces", "my.market", "source")):
            got = merge.merge(target, overlay, [key])
            self.assertEqual(got["extraKnownMarketplaces"]["my.market"], new, key)
        # Only a dot that is a separator splits: "my" is not an entry.
        got = merge.merge(target, overlay, [("extraKnownMarketplaces", "my", "market")])
        self.assertEqual(got, target)


class MergeCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="settings-merge-"))
        self.target = self.tmp / "settings.json"
        self.overlay = self.tmp / "overlay.json"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, path: Path, data) -> str:
        text = data if isinstance(data, str) else json.dumps(data, indent=2) + "\n"
        path.write_text(text, encoding="utf-8")
        return text

    def backups(self):
        return sorted(p.name for p in self.tmp.iterdir() if ".bak-" in p.name)

    def test_dry_run_prints_diff_and_writes_nothing(self):
        before = self.write(self.target, {"model": "m", "env": {"A": "1"}})
        self.write(self.overlay, {"env": {"B": "2"}, "model": "other"})
        rc, out, err = run("--target", self.target, self.overlay)
        self.assertEqual(rc, 0)
        self.assertIn('+    "B": "2"', out)
        self.assertIn("kept existing", err)
        self.assertIn("dry run", err)
        self.assertEqual(self.target.read_text(), before)
        self.assertEqual(self.backups(), [])

    def test_no_op_merge_has_empty_diff(self):
        # json.dumps escapes "é" as é, unlike Claude Code: a merge that
        # changes nothing must not rewrite the layout either.
        self.write(self.target, {"a": {"b": [1, 2]}, "c": "é"})
        self.write(self.overlay, {"a": {"b": [2]}})
        rc, out, _ = run("--target", self.target, self.overlay)
        self.assertEqual((rc, out), (0, ""))

    def test_no_op_apply_keeps_a_foreign_layout(self):
        before = self.write(self.target, '{\n    "a": 1\n}')
        self.write(self.overlay, {"a": 1})
        rc, _, err = run("--target", self.target, "--apply", self.overlay)
        self.assertEqual(rc, 0)
        self.assertIn("no changes", err)
        self.assertEqual(self.target.read_text(), before)

    def test_diff_uses_claude_code_layout(self):
        # Claude Code writes 2-space indent, raw UTF-8 and a final newline;
        # a file already in that layout diffs only on the lines that change.
        before = merge.dumps({"a": "é", "l": ["x"]})
        self.write(self.target, before)
        self.write(self.overlay, {"l": ["y"]})
        rc, out, _ = run("--target", self.target, self.overlay)
        self.assertEqual(rc, 0)
        changed = [l for l in out.splitlines()
                   if l[:1] in "+-" and not l.startswith(("+++", "---"))]
        self.assertEqual(changed, ['-    "x"', '+    "x",', '+    "y"'])

    def test_apply_writes_atomically_with_backup_and_keeps_mode(self):
        before = self.write(self.target, {"keep": 1, "l": ["x"]})
        os.chmod(self.target, 0o640)
        self.write(self.overlay, {"l": ["y"], "new": True})
        rc, _, err = run("--target", self.target, "--apply", self.overlay)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(self.target.read_text()), {"keep": 1, "l": ["x", "y"], "new": True})
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o640)
        baks = self.backups()
        self.assertEqual(len(baks), 1)
        self.assertEqual((self.tmp / baks[0]).read_text(), before)
        self.assertIn("roll back: cp", err)
        self.assertFalse([p for p in self.tmp.iterdir() if p.name.endswith(".tmp")])

    def test_apply_without_changes_leaves_file_and_makes_no_backup(self):
        self.write(self.target, {"a": 1})
        self.write(self.overlay, {"a": 1})
        rc, _, err = run("--target", self.target, "--apply", self.overlay)
        self.assertEqual(rc, 0)
        self.assertIn("no changes", err)
        self.assertEqual(self.backups(), [])

    def test_apply_creates_missing_target(self):
        self.write(self.overlay, {"a": 1})
        rc, _, _ = run("--target", self.target, "--apply", self.overlay)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(self.target.read_text()), {"a": 1})
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)

    def test_apply_follows_a_symlinked_target(self):
        real = self.tmp / "real.json"
        self.write(real, {"a": 1})
        self.target.symlink_to(real)
        self.write(self.overlay, {"b": 2})
        rc, _, _ = run("--target", self.target, "--apply", self.overlay)
        self.assertEqual(rc, 0)
        self.assertTrue(self.target.is_symlink())
        self.assertEqual(json.loads(real.read_text()), {"a": 1, "b": 2})

    def test_override_flags(self):
        self.write(self.target, {"s": {"command": "old"}, "m": "keep"})
        self.write(self.overlay, {"s": {"command": "new"}, "m": "new"})
        run("--target", self.target, "--apply", "--override-key", "s.command", self.overlay)
        self.assertEqual(json.loads(self.target.read_text()), {"s": {"command": "new"}, "m": "keep"})
        run("--target", self.target, "--apply", "--override", self.overlay)
        self.assertEqual(json.loads(self.target.read_text())["m"], "new")

    def test_override_key_on_an_array_through_the_cli(self):
        self.write(self.target, {"permissions": {"ask": ["Bash(old *)"]},
                                 "hooks": {"PermissionRequest": [{"matcher": "Bash", "v": 1}]}})
        self.write(self.overlay, {"permissions": {"ask": ["Bash(new *)"]},
                                  "hooks": {"PermissionRequest": [{"matcher": "Bash", "v": 2}]}})
        rc, _, err = run("--target", self.target, "--apply", "--override-key", "permissions.ask",
                         "--override-key", "hooks.PermissionRequest", self.overlay)
        self.assertEqual(rc, 0, err)  # lost() accepts what the flag replaced
        self.assertEqual(json.loads(self.target.read_text()),
                         {"permissions": {"ask": ["Bash(new *)"]},
                          "hooks": {"PermissionRequest": [{"matcher": "Bash", "v": 2}]}})
        self.assertIn("array replaced, 1 -> 1 item(s); dropped 1", err)
        self.assertIn('- "Bash(old *)"', err)

    def test_remove_key_without_an_overlay(self):
        # The documented single-key rollback: no templates needed.
        self.write(self.target, {"promptCacheTtl": "1h", "env": {"A": "1", "B": "2"}})
        rc, _, err = run("--target", self.target, "--remove-key", "promptCacheTtl",
                         "--remove-key", "env.A", "--apply")
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(self.target.read_text()), {"env": {"B": "2"}})
        self.assertEqual(len(self.backups()), 1)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as cm:
                merge.run(["--target", str(self.target), "--apply"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("at least one OVERLAY", err.getvalue())

    def test_remove_key_flag(self):
        self.write(self.target, {"env": {"OLD": "1", "KEEP": "2"}})
        self.write(self.overlay, {"env": {"NEW": "3"}})
        rc, _, err = run("--target", self.target, "--apply", "--remove-key", "env.OLD", self.overlay)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(self.target.read_text()), {"env": {"KEEP": "2", "NEW": "3"}})

    def test_out_writes_trial_file_and_leaves_target(self):
        before = self.write(self.target, {"a": 1})
        self.write(self.overlay, {"b": 2})
        trial = self.tmp / "trial" / "merged.json"
        rc, _, err = run("--target", self.target, "--out", trial, self.overlay)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(trial.read_text()), {"a": 1, "b": 2})
        self.assertEqual(self.target.read_text(), before)
        rc, _, err = run("--target", self.target, "--out", self.target, self.overlay)
        self.assertEqual(rc, 2)
        self.assertIn("use --apply", err)

    def test_refuses_invalid_json_anywhere(self):
        self.write(self.target, '{"a": 1,}')
        self.write(self.overlay, {"b": 2})
        rc, _, err = run("--target", self.target, "--apply", self.overlay)
        self.assertEqual(rc, 2)
        self.assertIn("invalid JSON", err)
        self.assertEqual(self.target.read_text(), '{"a": 1,}')
        self.write(self.target, {"a": 1})
        self.write(self.overlay, "[1, 2]")
        rc, _, err = run("--target", self.target, "--apply", self.overlay)
        self.assertEqual(rc, 2)
        self.assertIn("must be a JSON object", err)
        self.assertEqual(self.backups(), [])

    def test_refuses_when_target_changes_mid_merge(self):
        self.write(self.target, {"a": 1})
        with self.assertRaises(merge.MergeError):
            merge.write_atomic(self.target, "{}\n", expect='{"other": 1}\n', backup=True)
        self.assertEqual(json.loads(self.target.read_text()), {"a": 1})
        self.assertEqual(self.backups(), [])

    def test_a_change_after_the_backup_is_still_caught(self):
        # Claude Code writes settings.json while the temp file is being
        # written: the check before the rename sees it, the backup and temp
        # file are removed, and Claude Code's write survives.
        before = self.write(self.target, {"a": 1})
        real_fsync = os.fsync

        def claude_code_writes(fd):
            real_fsync(fd)
            self.write(self.target, {"a": 1, "effortLevel": "high"})

        merge.os.fsync = claude_code_writes
        try:
            with self.assertRaises(merge.MergeError):
                merge.write_atomic(self.target, "{}\n", expect=before, backup=True)
        finally:
            merge.os.fsync = real_fsync
        self.assertEqual(json.loads(self.target.read_text()), {"a": 1, "effortLevel": "high"})
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["settings.json"])

    def test_refuses_nan_and_infinity(self):
        # Python's json reads them; JSON.parse doesn't, so writing one would
        # break the whole settings file.
        for text in ('{"x": NaN}', '{"y": Infinity}', '{"z": [-Infinity]}'):
            for target_text, overlay_text in ((text, '{"b": 2}'), ('{"b": 2}', text)):
                self.write(self.target, target_text)
                self.write(self.overlay, overlay_text)
                rc, _, err = run("--target", self.target, "--apply", self.overlay)
                self.assertEqual(rc, 2, err)
                self.assertIn("is not a JSON value", err)
                self.assertEqual(self.target.read_text(), target_text)
        self.assertEqual(self.backups(), [])
        with self.assertRaises(ValueError):
            merge.dumps({"x": float("nan")})

    def test_out_of_range_numbers_and_deep_nesting_get_the_clean_error(self):
        deep = "[" * 5000 + "]" * 5000
        for target_text, overlay_text, why in (
                ('{"x": 1e400}', '{"b": 2}', "out of range"),
                ('{"b": 2}', '{"x": [-1e400]}', "out of range"),
                ('{"x": %s}' % deep, '{"b": 2}', "nested too deeply"),
                ('{"b": 2}', '{"x": %s}' % deep, "nested too deeply")):
            self.write(self.target, target_text)
            self.write(self.overlay, overlay_text)
            rc, _, err = run("--target", self.target, "--apply", self.overlay)
            self.assertEqual(rc, 2, err)
            self.assertIn(why, err)
            self.assertNotIn("Traceback", err)
            self.assertEqual(self.target.read_text(), target_text)
        self.assertEqual(self.backups(), [])
        # Parsed, but too deep for the recursive merge (where that happens
        # depends on the Python version): still a clean refusal.
        self.write(self.target, {"a": {"b": 1}})
        self.write(self.overlay, {"a": {"c": 2}})
        with mock.patch.object(merge, "merge", side_effect=RecursionError):
            rc, _, err = run("--target", self.target, "--apply", self.overlay)
        self.assertEqual(rc, 2, err)
        self.assertIn("nested too deeply to merge; nothing written", err)
        self.assertEqual(json.loads(self.target.read_text()), {"a": {"b": 1}})

    def test_refuses_a_dangling_symlink_target(self):
        self.target.symlink_to(self.tmp / "gone" / "deeper" / "settings.json")
        self.write(self.overlay, {"a": 1})
        for argv in (("--target", self.target, "--apply", self.overlay),
                     ("--target", self.tmp / "x.json", "--out", self.target, "--force", self.overlay)):
            rc, _, err = run(*argv)
            self.assertEqual(rc, 2, err)
            self.assertIn("symlink to a missing file", err)
        self.assertFalse((self.tmp / "gone").exists())

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores file modes")
    def test_refuses_a_read_only_target(self):
        kept = self.write(self.target, {"a": 1})
        os.chmod(self.target, 0o444)
        self.write(self.overlay, {"b": 2})
        for argv in (("--target", self.target, "--apply", self.overlay),
                     ("--target", self.tmp / "x.json", "--out", self.target, "--force", self.overlay)):
            rc, _, err = run(*argv)
            self.assertEqual(rc, 2, err)
            self.assertIn("read-only (mode 0444)", err)
        self.assertEqual(self.target.read_text(), kept)
        self.assertEqual(self.backups(), [])
        rc, _, err = run("--target", self.target, self.overlay)  # a dry run is fine
        self.assertEqual(rc, 0, err)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores file modes")
    def test_write_failures_get_the_clean_error(self):
        ro = self.tmp / "ro"
        ro.mkdir()
        target = ro / "settings.json"
        kept = self.write(target, {"a": 1})
        self.write(self.overlay, {"b": 2})
        ro.chmod(0o555)
        try:
            for argv in (("--target", target, "--apply", self.overlay),
                         ("--target", self.target, "--out", ro / "new" / "t.json", self.overlay),
                         ("--target", self.target, "--out", ro / "t.json", self.overlay)):
                rc, _, err = run(*argv)
                self.assertEqual(rc, 2, err)
                self.assertIn("cannot write (Permission denied); nothing written", err)
                self.assertNotIn("Traceback", err)
            self.assertEqual(sorted(p.name for p in ro.iterdir()), ["settings.json"])
            self.assertEqual(target.read_text(), kept)
        finally:
            ro.chmod(0o755)

    def test_dotfiles_placeholder_defaults_to_repo_root(self):
        self.write(self.overlay, {"statusLine": {"command": "{{DOTFILES}}/statusline/statusline.sh"}})
        trial = self.tmp / "t.json"
        run("--target", self.target, "--out", trial, self.overlay)
        cmd = json.loads(trial.read_text())["statusLine"]["command"]
        self.assertEqual(cmd, ROOT.parent.as_posix() + "/statusline/statusline.sh")

    def test_refuses_a_dotfiles_path_that_breaks_the_quoted_command(self):
        self.write(self.overlay, {"c": "\"{{DOTFILES}}/x.sh\""})
        for name in ('we"ird', "cost$5", "back`tick"):
            d = self.tmp / name
            d.mkdir()
            rc, _, err = run("--target", self.target, "--dotfiles", d, self.overlay)
            self.assertEqual(rc, 2, err)
            self.assertIn("would break the status line command", err)
        self.write(self.overlay, {"c": "no placeholder"})  # no {{DOTFILES}}: no check
        rc, _, err = run("--target", self.target, "--dotfiles", self.tmp / 'we"ird', self.overlay)
        self.assertEqual(rc, 0, err)

    def test_warns_when_dotfiles_is_a_linked_worktree(self):
        wt = self.tmp / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
        self.write(self.overlay, {"c": "{{DOTFILES}}/x.sh"})
        trial = self.tmp / "t.json"
        rc, _, err = run("--target", self.target, "--out", trial, "--dotfiles", wt, self.overlay)
        self.assertEqual(rc, 0)
        self.assertIn("linked git worktree", err)
        (wt / ".git").unlink()
        (wt / ".git").mkdir()
        rc, _, err = run("--target", self.target, "--out", trial, "--force", "--dotfiles", wt,
                         self.overlay)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("linked git worktree", err)

    def test_no_op_check_tells_json_types_apart(self):
        # In Python True == 1 and 400000 == 400000.0; in JSON they differ.
        for before, after in (({"x": 1}, {"x": True}),
                              ({"autoCompactWindow": 400000}, {"autoCompactWindow": 400000.0})):
            self.write(self.target, before)
            self.write(self.overlay, after)
            rc, _, err = run("--target", self.target, "--apply", "--override", self.overlay)
            self.assertEqual(rc, 0, err)
            self.assertNotIn("no changes", err)
            self.assertEqual(self.target.read_text(), merge.dumps(after))

    def test_unreadable_inputs_get_the_clean_error(self):
        self.write(self.overlay, {"a": 1})
        folder = self.tmp / "dir.json"
        folder.mkdir()
        latin = self.tmp / "latin.json"
        latin.write_bytes('{"a": "café"}'.encode("latin-1"))
        for argv, why in ((("--target", folder, self.overlay), "cannot read"),
                          (("--target", self.target, folder), "cannot read"),
                          (("--target", latin, "--apply", self.overlay), "not UTF-8")):
            rc, _, err = run(*argv)
            self.assertEqual(rc, 2, err)
            self.assertIn(why, err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(latin.read_bytes(), '{"a": "café"}'.encode("latin-1"))

    def test_a_utf8_bom_is_accepted_and_not_written_back(self):
        bom = b"\xef\xbb\xbf" + b'{"a": 1}\n'
        self.target.write_bytes(bom)
        self.write(self.overlay, {"a": 1})
        rc, _, err = run("--target", self.target, "--apply", self.overlay)
        self.assertEqual((rc, self.target.read_bytes()), (0, bom), err)  # no-op: byte for byte
        self.write(self.overlay, {"b": 2})
        rc, _, err = run("--target", self.target, "--apply", self.overlay)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.target.read_bytes(), merge.dumps({"a": 1, "b": 2}).encode())

    def test_out_never_overwrites_an_overlay_or_an_existing_file_silently(self):
        self.write(self.target, {"a": 1})
        ov = self.write(self.overlay, {"b": 2})
        rc, _, err = run("--target", self.target, "--out", self.overlay, self.overlay)
        self.assertEqual(rc, 2)
        self.assertIn("one of the inputs", err)
        self.assertEqual(self.overlay.read_text(), ov)
        values = self.tmp / "private.json"
        vals = self.write(values, {"X": "1"})
        rc, _, err = run("--target", self.target, "--out", values, "--force", "--values", values,
                         self.overlay)
        self.assertEqual(rc, 2)
        self.assertIn("one of the inputs", err)
        self.assertEqual(values.read_text(), vals)
        precious = self.tmp / "settings.local.json"
        kept = self.write(precious, {"permissions": {"allow": ["Bash(x)"]}})
        rc, _, err = run("--target", self.target, "--out", precious, self.overlay)
        self.assertEqual(rc, 2)
        self.assertIn("--force", err)
        self.assertEqual(precious.read_text(), kept)
        rc, _, err = run("--target", self.target, "--out", precious, "--force", self.overlay)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(precious.read_text()), {"a": 1, "b": 2})
        baks = self.backups()
        self.assertEqual(len(baks), 1)
        self.assertEqual((self.tmp / baks[0]).read_text(), kept)

    def test_dotted_names_through_the_cli(self):
        old = {"source": {"source": "github", "repo": "a/b"}}
        new = {"source": {"source": "directory", "path": "/x"}}
        self.write(self.target, {"extraKnownMarketplaces": {"my.market": old},
                                 "modelSettings": {"claude-opus-4.8": {"effortLevel": "high"}}})
        self.write(self.overlay, {"extraKnownMarketplaces": {"my.market": new}})
        trial = self.tmp / "t.json"
        rc, _, err = run("--target", self.target, "--out", trial, self.overlay)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(trial.read_text())["extraKnownMarketplaces"]["my.market"], old)
        # The decision names a PATH that works as typed.
        self.assertIn("--override-key /extraKnownMarketplaces/my.market to replace", err)
        for key in ("/extraKnownMarketplaces/my.market", "extraKnownMarketplaces.my\\.market"):
            rc, _, err = run("--target", self.target, "--out", trial, "--force",
                             "--override-key", key, self.overlay)
            self.assertEqual(rc, 0, err)
            self.assertEqual(json.loads(trial.read_text())["extraKnownMarketplaces"]["my.market"],
                             new, key)
        for key in ("modelSettings.claude-opus-4\\.8", "/modelSettings/claude-opus-4.8"):
            rc, _, err = run("--target", self.target, "--out", trial, "--force", "--remove-key", key)
            self.assertEqual(rc, 0, err)
            self.assertEqual(json.loads(trial.read_text())["modelSettings"], {}, key)
        rc, _, err = run("--target", self.target, "--remove-key", "a..b")
        self.assertEqual(rc, 2)
        self.assertIn("empty key", err)
        # The unescaped dot splits the key; the decision says what to type.
        rc, _, err = run("--target", self.target, "--remove-key", "modelSettings.claude-opus-4.8")
        self.assertEqual(rc, 0, err)
        self.assertIn("not present, nothing to remove; for the key with a dot in its name, "
                      "--remove-key /modelSettings/claude-opus-4.8", err)

    def test_removing_a_list_says_it_removes_all_of_it(self):
        self.write(self.target, {"permissions": {"deny": ["Read(a)", "Read(b)", "Read(c)"]}})
        rc, _, err = run("--target", self.target, "--remove-key", "permissions.deny")
        self.assertEqual(rc, 0, err)
        self.assertIn("- permissions.deny: the whole list, 3 item(s)", err)

    def test_dotfiles_paths_that_dont_exist(self):
        # A checkout without the scripts yet (or a mistyped --dotfiles): the
        # status line or hook would point at nothing. Dry run and --out warn,
        # --apply refuses unless --force.
        root = self.tmp / "checkout"
        (root / "statusline").mkdir(parents=True)
        self.write(self.overlay, {
            "statusLine": {"type": "command",
                           "command": "\"{{DOTFILES}}/statusline/statusline.sh\" --x 1"},
            "hooks": {"PermissionRequest": [{"hooks": [{"type": "command", "command": "python3",
                                                        "args": ["-I", "{{DOTFILES}}/h/a.py"]}]}]}})
        want = [f"{root.as_posix()}/statusline/statusline.sh", f"{root.as_posix()}/h/a.py"]
        trial = self.tmp / "t.json"
        for args in ((), ("--out", trial)):
            rc, _, err = run("--target", self.target, "--dotfiles", root, *args, self.overlay)
            self.assertEqual(rc, 0, err)
            for p in want:
                self.assertIn(f"warning: {p} doesn't exist", err)
        rc, _, err = run("--target", self.target, "--dotfiles", root, "--apply", self.overlay)
        self.assertEqual(rc, 2)
        self.assertIn("refusing to write", err)
        self.assertFalse(self.target.exists())
        rc, _, err = run("--target", self.target, "--dotfiles", root, "--apply", "--force",
                         self.overlay)
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.target.exists())
        # Once both exist: no warning. A path the result doesn't keep (the
        # target's own status line wins) isn't checked.
        (root / "statusline" / "statusline.sh").write_text("")
        (root / "h").mkdir()
        (root / "h" / "a.py").write_text("")
        rc, _, err = run("--target", self.target, "--dotfiles", root, self.overlay)
        self.assertNotIn("doesn't exist", err)
        self.write(self.target, {"statusLine": {"type": "command", "command": "mine.sh"}})
        (root / "statusline" / "statusline.sh").unlink()
        self.write(self.overlay, {"statusLine": {"type": "command",
                                                 "command": "{{DOTFILES}}/statusline/statusline.sh"}})
        rc, _, err = run("--target", self.target, "--dotfiles", root, "--apply", self.overlay)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("doesn't exist", err)

    def test_an_escaped_placeholder_gets_the_same_checks(self):
        # {{DOTFILES}} written with a JSON escape is still substituted, so the
        # quote check and the path check must see it too.
        self.overlay.write_text('{"statusLine": {"type": "command", '
                                '"command": "\\"\\u007b{DOTFILES}}/x.sh\\""}}', encoding="utf-8")
        d = self.tmp / 'we"ird'
        d.mkdir()
        rc, _, err = run("--target", self.target, "--dotfiles", d, self.overlay)
        self.assertEqual(rc, 2, err)
        self.assertIn("would break the status line command", err)
        rc, _, err = run("--target", self.target, "--dotfiles", self.tmp, self.overlay)
        self.assertIn(f"warning: {self.tmp.as_posix()}/x.sh doesn't exist", err)

    def write_values(self, data) -> Path:
        p = self.tmp / f"values-{len(list(self.tmp.iterdir()))}.json"
        self.write(p, data)
        return p

    def test_values_fill_placeholders_and_a_later_file_wins(self):
        self.write(self.overlay, {
            "env": {"ACCOUNT": "{{ACCOUNT_ID}}"},
            "autoMode": {"environment": ["Account {{ACCOUNT_ID}} in {{REGION}}."]},
            "footerLinksRegexes": [{"url": "{{TICKETS}}{tt}", "label": "{tt}"}]})
        a = self.write_values({"$comment": "ignored", "ACCOUNT_ID": "111", "REGION": "r1",
                               "TICKETS": "https://t.example/"})
        b = self.write_values({"REGION": "r2"})
        trial = self.tmp / "t.json"
        rc, _, err = run("--target", self.target, "--values", a, "--values", b,
                         "--out", trial, self.overlay)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(trial.read_text()), {
            "env": {"ACCOUNT": "111"}, "autoMode": {"environment": ["Account 111 in r2."]},
            "footerLinksRegexes": [{"url": "https://t.example/{tt}", "label": "{tt}"}]})
        self.assertNotIn("not gitignored", err)  # the temp dir isn't in the checkout

    def test_a_missing_value_is_refused_with_a_pointer_to_values(self):
        self.write(self.overlay, {"env": {"A": "{{ACCOUNT_ID}}"}})
        for extra in ((), ("--values", self.write_values({"OTHER": "x"}))):
            rc, _, err = run("--target", self.target, *extra, "--apply", self.overlay)
            self.assertEqual(rc, 2, err)
            self.assertIn("no value for {{ACCOUNT_ID}}; pass --values FILE", err)
            self.assertFalse(self.target.exists())
        rc, _, err = run("--target", self.target, "--values", self.tmp / "nope.json", self.overlay)
        self.assertEqual(rc, 2)
        self.assertIn("nope.json: no such file", err)

    def test_bad_values_files_are_refused(self):
        self.write(self.overlay, {"env": {"A": "{{ACCOUNT_ID}}"}})
        for data, msg in (({"ACCOUNT_ID": "<12-digit AWS account id>"}, "still the example's"),
                          ({"ACCOUNT_ID": " <fill me> "}, "still the example's"),
                          ({"ACCOUNT_ID": ""}, "non-empty string"),
                          ({"ACCOUNT_ID": "  "}, "non-empty string"),
                          ({"ACCOUNT_ID": 123}, "non-empty string"),
                          ({"account_id": "1"}, "not a placeholder name"),
                          ({"A-B": "1"}, "not a placeholder name"),
                          ({"DOTFILES": "/x"}, "--dotfiles"),
                          ([], "top level must be a JSON object")):
            rc, _, err = run("--target", self.target, "--values", self.write_values(data),
                             "--apply", self.overlay)
            self.assertEqual(rc, 2, data)
            self.assertIn(msg, err, data)
            self.assertFalse(self.target.exists())
        # A value is text: tokens in it aren't expanded again.
        rc, _, err = run("--target", self.target, "--values",
                         self.write_values({"ACCOUNT_ID": "{{DOTFILES}}<x>"}), "--apply",
                         self.overlay)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(self.target.read_text()), {"env": {"A": "{{DOTFILES}}<x>"}})

    def test_warns_when_a_values_file_in_the_checkout_is_not_gitignored(self):
        if not shutil.which("git"):
            self.skipTest("git not on PATH")
        root = self.tmp / "checkout"
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / ".gitignore").write_text("private/\n")
        (root / "private").mkdir()
        self.write(self.overlay, {"env": {"A": "{{X}}"}})
        for rel, warned in (("private/v.json", False), ("v.json", True)):
            p = root / rel
            self.write(p, {"X": "1"})
            rc, _, err = run("--target", self.target, "--dotfiles", root, "--values", p,
                             self.overlay)
            self.assertEqual(rc, 0, err)
            self.assertEqual("not gitignored" in err, warned, rel)

    def test_a_console_that_cant_encode_the_diff(self):
        # LC_ALL=C without Python's UTF-8 mode: stdout is ASCII. The diff is
        # escaped instead of crashing halfway.
        self.write(self.target, {"env": {"X": "café │"}})
        self.write(self.overlay, {"env": {"Y": "ü"}})
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("LC_", "LANG", "PYTHONUTF8", "PYTHONIOENCODING"))}
        env.update(LC_ALL="C", LANG="C", PYTHONUTF8="0")
        r = subprocess.run([sys.executable, str(ROOT / "merge.py"), "--target", str(self.target),
                            str(self.overlay), "--out", str(self.tmp / "t.json")],
                           capture_output=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b'+    "Y": "\\xfc"', r.stdout)
        self.assertEqual(json.loads((self.tmp / "t.json").read_text(encoding="utf-8"))["env"],
                         {"X": "café │", "Y": "ü"})


if __name__ == "__main__":
    unittest.main()
