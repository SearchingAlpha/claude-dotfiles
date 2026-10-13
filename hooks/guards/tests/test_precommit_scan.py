"""precommit_scan.py: install/chain/uninstall in real repos, and real commits."""

import json
import shutil
import subprocess
import sys
import time
import unittest

from support import FAKE, GUARDS, Sandbox, rnd

import precommit_scan as PS

SHEPHERD = GUARDS.parent.parent / "harnesses" / "shepherd"


def shepherd_stub(name="pre-commit"):
    """shepherd's real git-hook wrapper, when this checkout has it."""
    sys.path.insert(0, str(SHEPHERD))
    try:
        from shep import hooks as sh
        return sh.STUB.format(mark=sh.STUB_MARK, name=name, block=sh.BLOCK)
    except Exception:
        return None


class ScanCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.repo = self.sb.repo()
        self.hooks = self.repo / ".git" / "hooks"

    def tearDown(self):
        self.sb.cleanup()

    def cli(self, *args, cwd=None):
        p = subprocess.run([sys.executable, "-I", str(GUARDS / "precommit_scan.py"), *args],
                           capture_output=True, text=True, env=self.sb.env, cwd=str(cwd or self.repo))
        return p.returncode, p.stdout, p.stderr

    def commit(self, rel, text):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        self.sb.git(self.repo, "add", rel)
        return self.sb.git(self.repo, "commit", "-q", "-m", f"add {rel}", check=False)


class TestDiffParsing(unittest.TestCase):
    def test_added_lines_map_to_new_line_numbers(self):
        k = FAKE.github
        diff = ("diff --git a/a.py b/a.py\nindex 1..2 100644\n--- a/a.py\n+++ b/a.py\n"
                "@@ -3,0 +4,2 @@ def f():\n+x = 1\n+tok = '" + k + "'\n"
                "@@ -10 +12 @@\n-old\n+++ looks like a header but is content\n"
                "diff --git a/gone.txt b/gone.txt\n--- a/gone.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-" + k + "\n")
        blocks = PS.added_blocks(diff)
        self.assertEqual([(p, n) for p, n, _ in blocks], [("a.py", 4), ("a.py", 12)])
        self.assertEqual(PS.findings_in(blocks), [("a.py", 5, "github-token")])

    def test_names_with_spaces_lose_gits_trailing_tab(self):
        diff = "diff --git a/my note.md b/my note.md\n+++ b/my note.md\t\n@@ -0,0 +1 @@\n+" + FAKE.github + "\n"
        self.assertEqual(PS.findings_in(PS.added_blocks(diff)), [("my note.md", 1, "github-token")])

    def test_many_findings_stay_linear(self):
        text = "".join(f"token={rnd(40)}\n" for _ in range(30000))
        t = time.perf_counter()
        found = PS.findings_in([("big.txt", 1, text)])
        ms = (time.perf_counter() - t) * 1000
        lines = [n for _, n, _ in found]
        self.assertGreater(len(lines), 29000)              # ~1% of random values look tame
        self.assertEqual(lines, sorted(set(lines)))
        self.assertTrue(all(text.split("\n")[n - 1].startswith("token=") for n in lines[::997]))
        self.assertLess(ms, 3000, f"30k findings took {ms:.0f} ms")


class TestScanner(ScanCase):
    def test_clean_and_dirty_commits(self):
        self.assertEqual(self.cli("install", str(self.repo))[0], 0)
        self.assertEqual(self.commit("ok.py", "token = os.environ['TOKEN']\n").returncode, 0)
        k = FAKE.anthropic
        r = self.commit("cfg/app.py", f"import os\n\nKEY = '{k}'\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cfg/app.py:3  anthropic-api-key", r.stderr)
        self.assertNotIn(k, r.stderr + r.stdout)
        self.assertEqual(self.sb.git(self.repo, "log", "--oneline").stdout.count("\n"), 2)

    def test_marker_and_removed_lines_pass(self):
        self.cli("install", str(self.repo))
        k = FAKE.aws_id
        self.assertEqual(self.commit("t.py", f"FAKE = '{k}'  # guards:allow-secret\n").returncode, 0)
        (self.repo / "t.py").write_text("FAKE = None\n")                     # removing a value is fine
        self.sb.git(self.repo, "add", "t.py")
        self.assertEqual(self.sb.git(self.repo, "commit", "-q", "-m", "rm", check=False).returncode, 0)

    def test_scan_files(self):
        (self.repo / "dump.jsonl").write_text('{"url": "x?api_key=' + FAKE.hex64 + '"}\n')
        rc, out, _ = self.cli("scan-files", str(self.repo / "dump.jsonl"), str(self.repo / "README.md"))
        self.assertEqual(rc, PS.BLOCK)
        self.assertIn("dump.jsonl:1  hex-secret", out)

    def test_files_git_treats_as_binary(self):
        self.cli("install", str(self.repo))
        (self.repo / ".gitattributes").write_text("*.jsonl binary\n*.dat -diff\n")
        self.sb.git(self.repo, "add", ".gitattributes")
        self.sb.git(self.repo, "commit", "-q", "-m", "attrs")
        for rel, data in (("log.jsonl", '{"url": "x?api_key=' + FAKE.hex64 + '"}\n'),
                          ("dump.dat", "x = 1\nK = '" + FAKE.github + "'\n"),
                          ("nul.log", "\0header\n" + FAKE.github + "\n")):
            with self.subTest(rel):
                r = self.commit(rel, data)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(f"{rel}:", r.stderr)
                self.sb.git(self.repo, "rm", "-q", "--cached", rel)
        blob = bytes(range(256)) * 64 + FAKE.github.encode()          # really binary: not scanned
        (self.repo / "img.bin").write_bytes(blob)
        self.sb.git(self.repo, "add", "img.bin")
        self.assertEqual(self.sb.git(self.repo, "commit", "-q", "-m", "bin", check=False).returncode, 0)

    def test_hook_finds_python_like_the_launcher(self):
        self.cli("install", str(self.repo))
        bin_ = self.sb.tmp / "bin"
        bin_.mkdir()
        for tool in ("git", "dirname"):
            (bin_ / tool).symlink_to(shutil.which(tool))
        env = dict(self.sb.env, PATH=str(bin_))
        k = FAKE.github

        def commit(rel):
            (self.repo / rel).write_text(f"K = '{k}'\n")
            subprocess.run(["git", "add", rel], cwd=str(self.repo), env=env, check=True)
            return subprocess.run(["git", "commit", "-q", "-m", rel], cwd=str(self.repo), env=env,
                                  capture_output=True, text=True)

        r = commit("none.py")                                           # no Python at all
        self.assertEqual(r.returncode, 0)
        self.assertIn("no Python 3 found", r.stderr)
        stub = bin_ / "python3"                                         # the WindowsApps alias
        stub.write_text('#!/bin/sh\necho "Python was not found" >&2\nexit 49\n')
        stub.chmod(0o755)
        (bin_ / "python").symlink_to(sys.executable)
        r = commit("stub.py")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("stub.py:1  github-token", r.stderr)
        p = subprocess.run([sys.executable, "-I", str(GUARDS / "precommit_scan.py"), "status", str(self.repo)],
                           capture_output=True, text=True, env=env)
        self.assertIn(f"python: {bin_ / 'python'}", p.stdout)
        self.assertNotIn("differs", p.stdout)

    def test_missing_scanner_lets_the_commit_through(self):
        self.cli("install", str(self.repo))
        hook = self.hooks / "pre-commit"
        hook.write_text(hook.read_text().replace(str(GUARDS / "precommit_scan.py"), "/nonexistent/x.py"))
        r = self.commit("a.py", f"K = '{FAKE.github}'\n")
        self.assertEqual(r.returncode, 0)
        self.assertIn("commit not scanned", r.stderr)


ACCOUNT = "123456789012"                       # placeholders shaped like confidential values
HOST = "reports.internal.example"


class TestConfidential(ScanCase):
    """The optional confidential-values check: values come from a JSON file kept
    out of git, named by $CLAUDE_GUARDS_CONFIDENTIAL or `git config guards.confidential`."""

    def values(self, name="private.json", **sec):
        sec = sec or {"literals": [ACCOUNT, "Team_Schema"],
                      "patterns": [{"name": "internal-host", "regex": r"[a-z0-9-]\.internal\.example\b"},
                                   r"\bTICKET-\d{6,}\b"]}
        p = self.sb.tmp / name
        p.write_text(json.dumps({"confidential": sec, "other": {"allow": ["x@example.com"]}}))
        return p

    def test_literals_and_patterns_are_found_never_printed(self):
        conf = PS.load_confidential([str(self.values())])
        self.assertEqual(conf.problems, [])
        text = (f"a\nacct = {ACCOUNT} and again {ACCOUNT}\nselect * from TEAM_SCHEMA.t\n"
                f"see https://{HOST}/x\nTICKET-1234567  # guards:allow-secret\nTICKET-7654321\n")
        self.assertEqual(PS.findings_in([("n.md", 10, text)], conf), [
            ("n.md", 11, "confidential-literal-1"), ("n.md", 12, "confidential-literal-2"),
            ("n.md", 13, "confidential-internal-host"), ("n.md", 15, "confidential-pattern-2")])
        self.assertEqual(PS.findings_in([("n.md", 1, "nothing here, *.internal.example\n")], conf), [])

    def test_non_ascii_text_keeps_line_numbers(self):
        conf = PS.load_confidential([str(self.values())])
        text = "İstanbul İİİ\nok\n" + ACCOUNT + "\n"              # lower() grows İ
        self.assertNotEqual(len(text.lower()), len(text))
        self.assertEqual(PS.findings_in([("u.md", 1, text)], conf), [("u.md", 3, "confidential-literal-1")])

    def test_bad_entries_are_problems_not_errors(self):
        bad = self.values(literals=["abc", 7, "long-enough"], patterns=["(", {"name": "n"}, "ok\\d"])
        (self.sb.tmp / "broken.json").write_text("{nope")
        conf = PS.load_confidential([str(bad), str(self.sb.tmp / "broken.json"), str(self.sb.tmp / "gone.json")])
        self.assertEqual(sorted(conf.kinds.values()), ["confidential-literal-3@1"])
        self.assertEqual([k for k, _ in conf.patterns], ["confidential-pattern-3@1"])
        joined = "\n".join(conf.problems)
        for what in ("literal 1 skipped", "literal 2 skipped", "pattern 1 skipped (invalid regex",
                     "pattern 2 skipped", "broken.json is unreadable or not JSON", "gone.json not found"):
            self.assertIn(what, joined)
        self.assertNotIn("abc", joined)                                       # no value in a problem
        (self.sb.tmp / "nosec.json").write_text('{"recipient_guard": {}}')
        self.assertIn('no "confidential" object', PS.load_confidential([str(self.sb.tmp / "nosec.json")]).problems[0])

    def test_a_commit_with_a_confidential_value_is_refused(self):
        self.cli("install", str(self.repo))
        values = self.values()
        self.sb.git(self.repo, "config", "guards.confidential", str(values))
        self.assertEqual(self.commit("ok.md", "nothing to see\n").returncode, 0)
        r = self.commit("docs/setup.md", f"# setup\n\nThe account is {ACCOUNT}.\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("docs/setup.md:3  confidential-literal-1", r.stderr)
        self.assertIn(str(values), r.stderr)
        self.assertIn("*.example", r.stderr)
        self.assertNotIn(ACCOUNT, r.stderr + r.stdout)
        self.assertIn("2 literals, 2 patterns", self.cli("status", str(self.repo))[1])

    def test_relative_path_env_override_and_a_missing_file(self):
        self.cli("install", str(self.repo))
        self.sb.git(self.repo, "config", "guards.confidential", "secret-values/v.json")   # from the top
        (self.repo / "secret-values").mkdir()
        (self.repo / "secret-values" / "v.json").write_text(self.values().read_text())
        sub = self.repo / "sub"
        sub.mkdir()
        rc, out, _ = self.cli("scan-files", str(sub / ".." / "README.md"), cwd=sub)
        self.assertEqual(rc, 0)
        (sub / "n.md").write_text(f"{HOST}\n")
        self.assertEqual(self.cli("scan-files", "n.md", cwd=sub)[:2], (PS.BLOCK, "n.md:1  confidential-internal-host\n"))
        self.sb.git(self.repo, "config", "guards.confidential", "secret-values/missing.json")
        r = self.commit("a.md", f"{ACCOUNT}\n")
        self.assertEqual(r.returncode, 0)                                     # fail open on its own setup
        self.assertIn("missing.json not found; not checked", r.stderr)
        self.assertIn("problem:", self.cli("status", str(self.repo))[1])
        self.sb.env["CLAUDE_GUARDS_CONFIDENTIAL"] = str(self.values("env.json", literals=["Only_This_One"]))
        self.assertNotEqual(self.commit("b.md", "only_this_one\n").returncode, 0)
        self.sb.env["CLAUDE_GUARDS_CONFIDENTIAL"] = ""                        # set but empty: none
        self.assertEqual(self.commit("c.md", "only_this_one\n").returncode, 0)

    def test_worktrees_share_the_setting(self):
        self.cli("install", str(self.repo))
        self.sb.git(self.repo, "config", "guards.confidential", str(self.values()))
        wt = self.sb.tmp / "wt"
        self.sb.git(self.repo, "worktree", "add", "-q", "-b", "side", str(wt))
        (wt / "w.md").write_text(f"https://{HOST}/\n")
        self.sb.git(wt, "add", "w.md")
        r = self.sb.git(wt, "commit", "-q", "-m", "w", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("w.md:1  confidential-internal-host", r.stderr)

    def test_unconfigured_is_silent(self):
        self.cli("install", str(self.repo))
        r = self.commit("a.md", f"{ACCOUNT}\n")
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertIn("confidential values: none", self.cli("status", str(self.repo))[1])

    def test_speed_on_a_large_diff(self):
        conf = PS.load_confidential([str(self.values(
            literals=[rnd(12, "0123456789") for _ in range(20)],
            patterns=[r"[a-z0-9-]\.internal\.example\b", r"(?i)@corp\.example\.com\b", r"\bTICKET-\d{6,}\b"]))])
        text = "".join(f"line {i} select col_{i} from t where x = '{rnd(20)}'\n" for i in range(20000))
        t = time.perf_counter()
        PS.findings_in([("big.sql", 1, text)], conf)
        ms = (time.perf_counter() - t) * 1000
        self.assertLess(ms, 1500, f"{len(text) // 1000} KB took {ms:.0f} ms")


class TestInstall(ScanCase):
    def test_fresh_install_is_idempotent_and_uninstalls(self):
        self.cli("install", str(self.repo))
        first = (self.hooks / "pre-commit").read_text()
        rc, out, _ = self.cli("install", str(self.repo))
        self.assertEqual(rc, 0)
        self.assertEqual((self.hooks / "pre-commit").read_text(), first)
        self.assertFalse((self.hooks / PS.ORIG).exists())
        self.assertIn("installed:", self.cli("status", str(self.repo))[1])
        self.assertNotIn("differs", self.cli("status", str(self.repo))[1])
        old = first.replace('"$py" $flag -I', "python3 -I")                  # an older version's stub
        (self.hooks / "pre-commit").write_text(old)
        self.assertIn("re-run install", self.cli("status", str(self.repo))[1])
        self.cli("uninstall", str(self.repo))
        self.assertFalse((self.hooks / "pre-commit").exists())

    def test_existing_hook_is_kept_and_chained(self):
        marker = self.sb.tmp / "orig-ran"
        (self.hooks / "pre-commit").write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
        (self.hooks / "pre-commit").chmod(0o755)
        rc, out, _ = self.cli("install", str(self.repo))
        self.assertIn("kept the existing pre-commit", out)
        self.assertEqual(self.commit("a.md", "a\n").returncode, 0)
        self.assertTrue(marker.exists())
        marker.unlink()
        self.assertNotEqual(self.commit("b.md", FAKE.slack + "\n").returncode, 0)
        self.assertFalse(marker.exists())                       # refused before the chain
        self.cli("uninstall", str(self.repo))
        self.assertIn("touch", (self.hooks / "pre-commit").read_text())

    def test_a_symlinked_hook_is_moved_as_a_link(self):
        tools = self.repo / "tools" / "hooks"
        (self.hooks / "pre-commit").symlink_to("../../tools/hooks/pre-commit")   # dangling for now
        rc, out, err = self.cli("install", str(self.repo))
        self.assertEqual(rc, 0, err)
        self.assertIn("dangles", out)
        self.assertFalse((self.hooks / "pre-commit").is_symlink())
        self.assertTrue((self.hooks / PS.ORIG).is_symlink())
        self.assertFalse(tools.exists())                                         # nothing written through it
        self.assertEqual(self.commit("a.md", "a\n").returncode, 0)
        self.cli("uninstall", str(self.repo))
        self.assertTrue((self.hooks / "pre-commit").is_symlink())

    def test_a_hook_that_reads_its_own_name_needs_force(self):
        hook = self.hooks / "pre-commit"
        hook.write_text('#!/bin/sh\nname=$(basename "$0")\n[ "$name" = pre-commit ] || exit 0\nexit 1\n')
        hook.chmod(0o755)
        rc, _, err = self.cli("install", str(self.repo))
        self.assertEqual(rc, 1)
        self.assertIn("own file name", err)
        self.assertIn("basename", hook.read_text())                        # left untouched
        self.assertFalse((self.hooks / PS.ORIG).exists())
        rc, out, _ = self.cli("install", str(self.repo), "--force")
        self.assertEqual(rc, 0)
        self.assertIn("kept the existing pre-commit", out)

    def test_behind_shepherds_wrapper(self):
        stub = shepherd_stub()
        if stub is None:
            self.skipTest("shepherd not in this checkout")
        (self.hooks / "pre-commit").write_text(stub)
        (self.hooks / "pre-commit").chmod(0o755)
        rc, out, _ = self.cli("install", str(self.repo))
        self.assertIn("behind shepherd's wrapper", out)
        self.assertIn("shepherd-hook", (self.hooks / "pre-commit").read_text())    # wrapper untouched
        self.assertIn(PS.MARK, (self.hooks / "pre-commit.shepherd-orig").read_text())
        self.assertNotEqual(self.commit("a.py", f"K = '{FAKE.github}'\n").returncode, 0)
        self.sb.git(self.repo, "rm", "-q", "--cached", "a.py")             # unstage the refused file
        self.assertEqual(self.commit("b.py", "K = os.environ['K']\n").returncode, 0)

    def test_behind_shepherd_with_an_older_original(self):
        stub = shepherd_stub()
        if stub is None:
            self.skipTest("shepherd not in this checkout")
        (self.hooks / "pre-commit").write_text(stub)
        (self.hooks / "pre-commit.shepherd-orig").write_text("#!/bin/sh\nexit 0\n")
        rc, out, _ = self.cli("install", str(self.repo))
        self.assertEqual(rc, 0)
        self.assertTrue((self.hooks / PS.ORIG).exists())
        self.assertIn(PS.MARK, (self.hooks / "pre-commit.shepherd-orig").read_text())

    def test_refuses_core_hooks_path_and_double_backup(self):
        self.sb.git(self.repo, "config", "core.hooksPath", ".githooks")
        rc, _, err = self.cli("install", str(self.repo))
        self.assertEqual(rc, 1)
        self.assertIn("core.hooksPath", err)
        self.sb.git(self.repo, "config", "--unset", "core.hooksPath")
        (self.hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
        (self.hooks / PS.ORIG).write_text("#!/bin/sh\nexit 0\n")
        rc, _, err = self.cli("install", str(self.repo))
        self.assertEqual(rc, 1)
        self.assertIn("resolve by hand", err)

    def test_worktrees_share_the_hook(self):
        self.cli("install", str(self.repo))
        wt = self.sb.tmp / "wt"
        self.sb.git(self.repo, "worktree", "add", "-q", "-b", "side", str(wt))
        (wt / "s.py").write_text(f"K = '{FAKE.anthropic}'\n")
        self.sb.git(wt, "add", "s.py")
        self.assertNotEqual(self.sb.git(wt, "commit", "-q", "-m", "s", check=False).returncode, 0)

    def test_standalone_copy(self):
        rc, out, _ = self.cli("install", str(self.repo), "--standalone")
        self.assertEqual(rc, 0)
        sd = self.hooks / PS.STANDALONE_DIR
        self.assertTrue((sd / "patterns.py").exists())
        self.assertNotIn(str(GUARDS), (self.hooks / "pre-commit").read_text())
        self.assertNotEqual(self.commit("a.py", f"K = '{FAKE.github}'\n").returncode, 0)
        self.assertNotIn("differs", self.cli("status", str(self.repo))[1])
        self.cli("uninstall", str(self.repo))
        self.assertFalse(sd.exists())


if __name__ == "__main__":
    unittest.main()
