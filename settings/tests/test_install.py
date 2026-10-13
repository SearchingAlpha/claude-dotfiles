"""install.sh and install.ps1: the commands step in both modes.

Default: commands/*.md are linked (sh) or copied (ps1) into ~/.claude/commands,
so the short names (/autobuild, /post …) keep working. With
--plugin-commands-only / -PluginCommandsOnly nothing is linked and only the
repo's own entries are removed.

The functions are cut out of the scripts by name and run against temp dirs,
and the whole install.sh runs inside a fake repo with HOME and a stub `claude`
in a temp dir, so nothing here installs anything for real."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
INSTALL = REPO / "install.sh"
BASH = shutil.which("bash") or "/bin/bash"
IDENT = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.autocrlf=false"]
# What install.sh may only print, never run.
PRINTED_ONLY = ("--apply", "merge.py", "precommit_scan.py", "apply.sh")


def function_source(name: str) -> str:
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", INSTALL.read_text(encoding="utf-8"),
                  re.S | re.M)
    assert m, f"{name} not found in install.sh"
    return m.group(0)


def git(*args, cwd):
    subprocess.run(["git", *IDENT, *args], cwd=cwd, check=True, capture_output=True)


def code_lines(text: str, comment: str = "#") -> str:
    """The script without comment lines and without the lines that only print."""
    keep = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(comment) or re.match(r'^(echo|Write-Host|Write-Warning)\b', s) \
                or re.match(r'^"[^"]*"$', s):     # an echo's continuation line
            continue
        keep.append(line)
    return "\n".join(keep)


class InstallScript(unittest.TestCase):
    def test_syntax(self):
        subprocess.run([BASH, "-n", str(INSTALL)], check=True)

    def test_links_commands_by_default_and_only_prints_the_hints(self):
        text = INSTALL.read_text(encoding="utf-8")
        self.assertIn('ln -sfn "$file" "$dest"', function_source("link_repo_commands"))
        self.assertRegex(text, r'(?m)^else\n\s+link_repo_commands "\$source_dir" "\$target_dir"\n')
        code = code_lines(text)
        for word in PRINTED_ONLY:
            self.assertIn(word, text, word)          # the hint is there …
            self.assertNotIn(word, code, word)       # … and only in an echo
        self.assertIn("precommit_scan.py\\\" install <repo>", text)
        self.assertIn("precommit_scan.py\\\" status <repo>", text)
        self.assertIn('sh \\"$repo_root/hooks/guards/amazon/apply.sh\\"', text)
        self.assertNotIn("mods/check.sh", text)      # no mods pre-commit stub

    def test_help_says_what_the_flag_trades(self):
        r = subprocess.run([BASH, str(INSTALL), "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--plugin-commands-only", r.stdout)
        self.assertIn("no short names", r.stdout)
        self.assertIn("listed once", r.stdout)

    def test_unknown_option_is_refused_before_anything_runs(self):
        home = Path(tempfile.mkdtemp(prefix="install-badopt-"))
        try:
            r = subprocess.run([BASH, str(INSTALL), "--plugin-command-only"], capture_output=True,
                               text=True, env=dict(os.environ, HOME=str(home)))
            self.assertEqual(r.returncode, 2)
            self.assertIn("unknown option: --plugin-command-only", r.stderr)
            self.assertEqual(list(home.iterdir()), [])
        finally:
            shutil.rmtree(home)


class FunctionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-fn-"))
        self.repo = self.tmp / "repo"
        (self.repo / "commands").mkdir(parents=True)
        for n in ("a.md", "b.md", "c.md"):
            (self.repo / "commands" / n).write_text(n)
        self.other = self.tmp / "other"
        self.other.mkdir()
        (self.other / "x.md").write_text("x")
        self.target = self.tmp / "home" / ".claude" / "commands"
        self.target.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def call(self, name, *args, ok=True) -> subprocess.CompletedProcess:
        script = function_source(name) + f'{name} "$@"\n'
        r = subprocess.run([BASH, "-c", "set -euo pipefail\n" + script, "x", *map(str, args)],
                           capture_output=True, text=True)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r


class LinkRepoCommands(FunctionTest):
    def test_links_every_command_and_is_idempotent(self):
        (self.target / "a.md").symlink_to(self.other / "x.md")   # an old link is repointed
        for _ in range(2):
            out = self.call("link_repo_commands", self.repo / "commands", self.target).stdout
            self.assertIn(f"Linked: b.md -> {self.target / 'b.md'}", out)
            self.assertIn(f"Done. 3 command(s) linked to {self.target}", out)
        for n in ("a.md", "b.md", "c.md"):
            self.assertEqual(os.readlink(self.target / n), str(self.repo / "commands" / n))

    def test_creates_the_target_dir(self):
        shutil.rmtree(self.target)
        self.call("link_repo_commands", self.repo / "commands", self.target)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ["a.md", "b.md", "c.md"])

    def test_missing_source_dir_fails(self):
        r = self.call("link_repo_commands", self.tmp / "nope", self.target, ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Source directory not found", r.stderr)


class UnlinkRepoCommands(FunctionTest):
    def unlink(self, *roots) -> str:
        return self.call("unlink_repo_commands", self.target, *roots).stdout

    def test_removes_only_links_into_repo_commands(self):
        t = self.target
        (t / "a.md").symlink_to(self.repo / "commands" / "a.md")             # absolute
        (t / "b.md").symlink_to(os.path.relpath(self.repo / "commands" / "b.md", t))  # relative
        (t / "gone.md").symlink_to(self.repo / "commands" / "gone.md")       # stale (dangling)
        (t / "x.md").symlink_to(self.other / "x.md")                         # elsewhere
        (t / "mine.md").write_text("user file")                              # regular file
        (t / "deep.md").symlink_to(self.repo / "README.md")                  # repo, not commands/
        out = self.unlink(self.repo)
        left = sorted(p.name for p in t.iterdir())
        self.assertEqual(left, ["deep.md", "mine.md", "x.md"])
        self.assertIn("3 link(s) removed", out)
        self.assertIn("Short names are gone: type /claude-dotfiles:<name>", out)  # the trade, said out loud

    def test_matches_through_a_symlinked_repo_path(self):
        alias = self.tmp / "alias"
        alias.symlink_to(self.repo)
        (self.target / "a.md").symlink_to(alias / "commands" / "a.md")
        self.unlink(self.repo.resolve())
        self.assertEqual(list(self.target.iterdir()), [])

    def test_nothing_to_remove_says_so(self):
        (self.target / "mine.md").write_text("user file")
        self.assertIn("No links into this repo's commands/", self.unlink(self.repo))
        self.assertEqual([p.name for p in self.target.iterdir()], ["mine.md"])

    def test_missing_dir_and_empty_roots_are_fine(self):
        shutil.rmtree(self.target)
        self.assertEqual(self.unlink(self.repo, ""), "")


class WholeInstallSh(unittest.TestCase):
    """install.sh copied into a fake repo with a linked worktree, run with a
    temp HOME and stub `claude`, `python3` and `python` first on PATH."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-run-"))
        self.main = self.tmp / "main"
        (self.main / "commands").mkdir(parents=True)
        git("init", "-q", "-b", "main", str(self.main), cwd=self.tmp)
        for n in ("autobuild.md", "post.md", "hs-demo.md"):
            (self.main / "commands" / n).write_text(n + "\n")
        shutil.copy2(INSTALL, self.main / "install.sh")
        git("add", "commands", "install.sh", cwd=self.main)
        git("commit", "-qm", "v1", cwd=self.main)
        self.wt = self.tmp / "wt"
        git("worktree", "add", "-q", "-b", "track", str(self.wt), cwd=self.main)
        self.home = self.tmp / "home"
        self.cmds = self.home / ".claude" / "commands"
        self.cmds.mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "calls.log"
        for name in ("claude", "python3", "python"):
            stub = self.bin / name
            stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{self.log}"\n')
            stub.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_install(self, root: Path, *args) -> str:
        env = dict(os.environ, HOME=str(self.home),
                   PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}")
        r = subprocess.run([BASH, str(root / "install.sh"), *args], capture_output=True,
                           text=True, env=env, cwd=str(self.tmp), timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def calls(self) -> list:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_default_links_commands_and_runs_only_claude(self):
        (self.cmds / "mine.md").write_text("user file\n")
        out = self.run_install(self.main)
        links = {p.name: os.readlink(p) for p in self.cmds.iterdir() if p.is_symlink()}
        self.assertEqual(links, {n: str(self.main / "commands" / n)
                                 for n in ("autobuild.md", "post.md", "hs-demo.md")})
        self.assertEqual((self.cmds / "mine.md").read_text(), "user file\n")
        self.assertIn("Done. 3 command(s) linked to", out)
        self.assertEqual(os.readlink(self.home / ".local" / "bin" / "shepherd"),
                         str(self.main / "harnesses" / "shepherd" / "shepherd.py"))
        self.assertTrue(self.calls())
        self.assertTrue(all(x.startswith("claude plugin ") for x in self.calls()), self.calls())
        for hint in ("settings/merge.py", "precommit_scan.py\" install <repo>",
                     "precommit_scan.py\" status <repo>", "hooks/guards/amazon/apply.sh",
                     "install.sh --plugin-commands-only"):
            self.assertIn(hint, out)

    def test_flag_removes_only_repo_links_from_either_checkout(self):
        c = self.cmds
        (c / "autobuild.md").symlink_to(self.main / "commands" / "autobuild.md")  # main checkout
        (c / "post.md").symlink_to(self.wt / "commands" / "post.md")              # this worktree
        (c / "gone.md").symlink_to(self.main / "commands" / "gone.md")            # stale
        (c / "hs-demo.md").write_text("hs-demo.md\n")                             # a copy: a file, kept
        (c / "x.md").symlink_to(self.tmp / "elsewhere.md")                        # elsewhere
        (c / "mine.md").write_text("user file\n")
        out = self.run_install(self.wt, "--plugin-commands-only")
        self.assertEqual(sorted(p.name for p in c.iterdir()), ["hs-demo.md", "mine.md", "x.md"])
        self.assertIn("3 link(s) removed", out)
        self.assertNotIn("Linked: ", out.replace("Linked: shepherd", ""))
        self.assertNotIn("install.sh --plugin-commands-only (no short names)", out)
        self.assertTrue(all(x.startswith("claude plugin ") for x in self.calls()), self.calls())
        # Run again: nothing left to remove, and still nothing linked.
        out = self.run_install(self.wt, "--plugin-commands-only")
        self.assertIn("No links into this repo's commands/", out)
        self.assertEqual(sorted(p.name for p in c.iterdir()), ["hs-demo.md", "mine.md", "x.md"])

    def test_flag_then_default_links_again(self):
        self.run_install(self.main, "--plugin-commands-only")
        self.assertEqual(list(self.cmds.iterdir()), [])
        self.run_install(self.main)
        self.assertEqual(sorted(p.name for p in self.cmds.iterdir()),
                         ["autobuild.md", "hs-demo.md", "post.md"])


PS1 = REPO / "install.ps1"
PWSH = os.environ.get("PWSH") or shutil.which("pwsh")


def ps1_function(name: str) -> str:
    m = re.search(rf"^function {name} \{{\n.*?^\}}\n", PS1.read_text(encoding="utf-8"),
                  re.S | re.M)
    assert m, f"function {name} not found in install.ps1"
    return m.group(0)


class InstallPs1Text(unittest.TestCase):
    """What can be checked without pwsh."""

    def test_default_copies_and_flag_removes(self):
        text = PS1.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^param\(\n(?:\s+#.*\n)*\s+\[switch\]\$PluginCommandsOnly\n\)")
        self.assertRegex(text, r"(?ms)^if \(\$PluginCommandsOnly\) \{\n.*?Remove-RepoCommands\n"
                               r"\} else \{\n\s+Copy-RepoCommands\n\}")
        self.assertIn("Copy-Item -Path $file.FullName -Destination $dest -Force",
                      ps1_function("Copy-RepoCommands"))
        self.assertNotIn("Copy-Item", ps1_function("Remove-RepoCommands"))
        code = code_lines(text)
        for word in ("--apply", "merge.py", "precommit_scan.py", "apply.sh"):
            self.assertNotIn(word, code.replace("'settings\\merge.py'", "")
                             .replace("'hooks\\guards\\precommit_scan.py'", "")
                             .replace("'hooks\\guards\\amazon\\apply.sh'", ""), word)
        self.assertIn('"$scan`" install <repo>', text)
        self.assertIn('"$scan`" status <repo>', text)
        self.assertNotIn("check.sh", text)            # no mods pre-commit stub


@unittest.skipUnless(PWSH, "pwsh not installed (set PWSH=/path/to/pwsh)")
class InstallPs1(unittest.TestCase):
    """The two command functions run by pwsh against a fake repo, a linked
    worktree of it and a fake ~/.claude/commands. Paths are POSIX here; the
    logic under test (links, hashes, git history) is the same as on Windows."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-ps1-"))
        self.main = self.tmp / "main"
        cmds = self.main / "commands"
        cmds.mkdir(parents=True)
        git("init", "-q", "-b", "main", str(self.main), cwd=self.tmp)
        (cmds / "a.md").write_text("a v1\nline\n")
        (cmds / "b.md").write_text("b\n")
        (cmds / "c.md").write_text("c\n")
        (cmds / "d.md").write_text("d\n")
        git("add", "commands", cwd=self.main)
        git("commit", "-qm", "v1", cwd=self.main)
        (cmds / "a.md").write_text("a v2\n")
        git("commit", "-qam", "v2", cwd=self.main)
        self.wt = self.tmp / "wt"
        git("worktree", "add", "-q", "-b", "track", str(self.wt), cwd=self.main)
        self.target = self.tmp / "home" / "commands"
        self.target.mkdir(parents=True)
        (self.tmp / "elsewhere").mkdir()
        (self.tmp / "elsewhere" / "e.md").write_text("e\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pwsh(self, repo_root: Path, call: str, no_git=False) -> subprocess.CompletedProcess:
        source = ps1_function("Invoke-Git") + ps1_function(call)
        if no_git:
            source = source.replace("Get-Command git", "Get-Command no-such-git-here")
        script = ("$ErrorActionPreference = 'Stop'\n"
                  f"$repoRoot = '{repo_root}'\n"
                  "$sourceDir = Join-Path $repoRoot 'commands'\n"
                  f"$targetDir = '{self.target}'\n" + source + call + "\n")
        path = self.tmp / f"{call}.ps1"
        path.write_text(script, encoding="utf-8")
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(path)],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def test_default_copies_every_command(self):
        (self.target / "a.md").write_text("an old copy\n")
        (self.target / "mine.md").write_text("user file\n")
        r = self.pwsh(self.wt, "Copy-RepoCommands")
        for n in ("a.md", "b.md", "c.md", "d.md"):
            self.assertEqual((self.target / n).read_bytes(), (self.wt / "commands" / n).read_bytes())
        self.assertEqual((self.target / "mine.md").read_text(), "user file\n")
        self.assertIn("4 command(s) installed", r.stdout)

    def test_flag_removes_old_copies_and_links_into_either_checkout(self):
        # Git for Windows' default: the checkout the installer copied from
        # had CRLF, and hash-object --path turns it back into LF.
        git("config", "core.autocrlf", "true", cwd=self.main)
        t = self.target
        (t / "a.md").write_bytes(b"a v1\r\nline\r\n")             # an old version, CRLF
        (t / "b.md").write_text("b\n")                            # the current version
        (t / "c.md").write_text("c, edited by the user\n")         # matches no version
        (t / "d.md").symlink_to(self.main / "commands" / "d.md")  # into the main checkout
        (t / "gone.md").symlink_to(self.main / "commands" / "gone.md")  # stale link
        (t / "e.md").symlink_to(self.tmp / "elsewhere" / "e.md")  # a link elsewhere
        (t / "mine.md").write_text("not a repo command\n")
        r = self.pwsh(self.wt, "Remove-RepoCommands")
        self.assertEqual(sorted(p.name for p in t.iterdir()), ["c.md", "e.md", "mine.md"])
        self.assertIn("4 command file(s) removed", r.stdout)
        self.assertIn("Short names are gone", r.stdout)
        self.assertIn("c.md: matches no version of the repo file", r.stdout + r.stderr)

    def test_flag_without_git_only_identical_copies_go(self):
        t = self.target
        (t / "a.md").write_text("a v1\nline\n")   # old version: needs git history
        (t / "b.md").write_text("b\n")
        self.pwsh(self.wt, "Remove-RepoCommands", no_git=True)
        self.assertEqual(sorted(p.name for p in t.iterdir()), ["a.md"])

    def test_whole_script_parses(self):
        cmd = ("$e = $null; [System.Management.Automation.Language.Parser]::ParseFile("
               f"'{PS1}', [ref]$null, [ref]$e) | Out-Null; $e | ForEach-Object {{ $_.Message }}; "
               "exit $e.Count")
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""), r.stderr)


if __name__ == "__main__":
    unittest.main()
