"""allow_repo_push.py, run as Claude Code runs a PermissionRequest hook: the
payload on stdin, a decision (or nothing) on stdout. Temp repos: a "vault" on
branch mainline tracking a bare remote, another repo, and a repo nested in the
vault. The lexer is also checked against what bash and zsh actually pass."""

import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "allow_repo_push.py"
IDENT = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
HD = "\"$(cat <<'EOF'\n{}\nEOF\n)\""  # Claude Code's commit message form


def git(*args, cwd=None):
    subprocess.run(["git", *IDENT, *args], cwd=cwd, check=True, capture_output=True)


def load_hook():
    spec = importlib.util.spec_from_file_location("allow_repo_push", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AllowRepoPush(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="allow-push-"))
        cls.remote = cls.tmp / "remote.git"
        cls.vault = cls.tmp / "vault"
        cls.other = cls.tmp / "other"
        git("init", "-q", "--bare", str(cls.remote))
        for repo in (cls.vault, cls.other):
            git("init", "-q", "-b", "mainline", str(repo))
            (repo / "a.md").write_text("a\n")
            git("add", "a.md", cwd=repo)
            git("commit", "-qm", "init", cwd=repo)
        git("remote", "add", "origin", str(cls.remote), cwd=cls.vault)
        git("push", "-q", "-u", "origin", "mainline", cwd=cls.vault)
        git("branch", "side", cwd=cls.vault)
        (cls.vault / "notes").mkdir()
        (cls.vault / "notes" / "n.md").write_text("n\n")
        cls.nested = cls.vault / "nested"
        git("init", "-q", "-b", "mainline", str(cls.nested))
        # Same remote URL as the vault: only the top-level check tells them apart.
        git("remote", "add", "origin", str(cls.remote), cwd=cls.nested)
        cls.home = cls.tmp / "home"
        cls.secret = cls.tmp / "id_rsa"
        cls.secret.write_text("not a key\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_hook(self, command, cwd=None, tool="Bash", raw=None, extra=()):
        payload = raw if raw is not None else json.dumps({
            "session_id": "s", "hook_event_name": "PermissionRequest",
            "permission_mode": "default", "cwd": str(cwd or self.tmp), "tool_name": tool,
            "tool_input": {"command": command, "description": "push"}})
        r = subprocess.run([sys.executable, "-I", str(HOOK), "--repo", str(self.vault),
                            "--url", str(self.remote), "--remote", "origin",
                            "--branch", "mainline", *extra],
                           input=payload, capture_output=True, text=True, timeout=10,
                           env=dict(os.environ, CLAUDE_GATES_HOME=str(self.home)))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def allowed(self, command, cwd=None):
        out = self.run_hook(command, cwd).stdout
        if not out.strip():
            return False
        d = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(d["hookEventName"], "PermissionRequest")
        return d["decision"]["behavior"] == "allow"

    def assert_no_answer(self, cases):
        for cmd, cwd in cases:
            self.assertFalse(self.allowed(cmd, cwd), cmd.replace(str(self.vault), "$V"))

    def test_plain_pushes_of_the_vault_are_allowed(self):
        v = self.vault
        msg = HD.format("vault: add x\n\nWhy: \"quotes\", 'ticks', $HOME, `cmd`, (parens); EOF.\n\n"
                        "Co-Authored-By: Someone <someone@example.invalid>")
        for cmd, cwd in (("git push", v), ("git push origin", v), ("git push -q origin mainline", v),
                         (f"git -C {v} push origin mainline", None),
                         (f"git -C {v} push origin HEAD", None),
                         (f"git -C {v} push origin HEAD:mainline", None),
                         (f"git -C {v} push origin refs/heads/mainline:refs/heads/mainline", None),
                         (f"git -C {v}/notes push", None),
                         (f"cd {v} && git push", None), (f"cd {v}/ && git push", None),
                         ("cd ./notes && git push", v), ("cd . && git push", v),
                         (f"git -C {v} add notes/x.md && git -C {v} commit -m 'vault: add x; y' "
                          f"&& git -C {v} push", None),
                         (f"git -C {v} add -- ./a.md notes/n.md {v}/notes/new.md "
                          f"&& git -C {v} commit -qm \"vault: \\\"x\\\" & y\" && git -C {v} push", None),
                         (f"git -C {v} commit -m 'cost $5, `x`' && git -C {v} push", None),
                         (f"git -C {v} commit --message=wip --quiet && git -C {v} push", None),
                         (f"git -C {v} commit --message wip -mq -m -a && git -C {v} push", None),
                         (f"git -C {v} add notes/n.md && git -C {v} commit -m {msg} "
                          f"&& git -C {v} push origin mainline", None),
                         (f"git -C {v} commit -m {HD.format('one line')}&& git -C {v} push", None),
                         (f"git -C {v} status --short && git -C {v} pull --rebase -q origin mainline "
                          f"&& git -C {v} push -q", None),
                         ("git pull --rebase && git push", v)):
            self.assertTrue(self.allowed(cmd, cwd), cmd)

    def test_anything_else_gets_no_answer(self):
        v, o, n = self.vault, self.other, self.nested
        self.assert_no_answer((
            (f"git -C {o} push origin mainline", None),       # another repo
            (f"git -C {n} push origin mainline", None),       # a repo nested in the vault
            ("git push --force", v), ("git push -f origin mainline", v),
            ("git push origin +mainline", v), ("git push --mirror", v), ("git push --m", v),
            ("git push origin other", v), ("git push upstream mainline", v),
            ("git push origin mainline:other", v), ("git push --all", v),
            ("git push origin :mainline", v), ("git push origin mainline:", v),
            ("git push -u origin mainline", v), ("git push origin mainline extra", v),
            (f"git -c core.sshCommand=x -C {v} push", None),  # other global options
            ("git push $(echo origin)", v), ("git push `echo origin`", v),
            ('git push "$REMOTE"', v), ("git push ${R}", v),
            ("git push; rm -rf x", v), ("rm -rf x && git push", v),
            ("git push | cat", v), ("git push > log", v), ("git push &", v),
            ("git push || true", v), ("git push # ; rm x", v), ("(git push)", v),
            ("git commit -m x", v),                           # no push: not this hook's job
            ("git push 'origin", v), ('git push "origin', v),  # unbalanced quotes
            ("git push\nrm -rf x", v), ("git push \\\nrm", v),
            ("cd /tmp && git push", v),
            ("git push", self.tmp),                           # not a repo at all
            (f"cd {v} &&", None), (f"&& cd {v}", None),
            # A diff with a path outside the work tree is `diff --no-index`:
            # it would print that file (the review's fixture commands).
            (f"git diff {self.secret} /dev/null && git push -q origin mainline", v),
            (f"git -C {v} diff -- {self.secret} /dev/null && git -C {v} push -q origin mainline",
             None),
            (f"git push -q origin mainline | tail -2; git diff /dev/null {self.secret}", v),
            (f"git diff --stat {self.secret} /dev/null && git push", v),
            (f"git diff HEAD -- {self.secret} && git push", v),
            ("git diff ../id_rsa a.md && git push", v), ("git diff ~/.netrc a.md && git push", v),
            (f"git -C {v}/notes diff ../../id_rsa n.md && git push", v),
            (f"git diff {v}x/id_rsa a.md && git push", v),     # a sibling that starts the same
            ("git push && git diff ..", v), (f"git log -- {self.secret} && git push", v),
            (f"git log --oneline {self.secret} && git push", v),
        ))

    def test_reads_of_paths_in_the_repo_are_allowed(self):
        v = self.vault
        # (An unquoted ~ mid-word is a glob operator in zsh's extended_glob.)
        for cmd, cwd in (("git diff --stat 'HEAD~1' -- notes/n.md && git push", v),
                         (f"git -C {v} diff -- {v}/a.md notes/n.md && git -C {v} push", None),
                         (f"git -C {v}/notes diff ../a.md n.md && git push", v),
                         ("git diff HEAD..origin/mainline && git push", v),
                         ("git log --oneline -n 3 origin/mainline..HEAD -- notes && git push", v)):
            self.assertTrue(self.allowed(cmd, cwd), cmd)

    def test_paths_are_read_the_way_git_reads_them(self):
        # ~ is often a symlink (/home/u -> /local/home/u). git takes an
        # absolute path whose leading part resolves to the top level as
        # inside the repo, so the hook does too; a relative path through the
        # link is outside for git, and for the hook.
        v, link = self.vault, self.tmp / "home-link"
        if not link.exists():
            link.symlink_to(self.tmp)
        via = link / "vault"
        self.assertTrue(self.allowed(f"git diff -- {via}/a.md {via}/notes/n.md && git push", v))
        self.assertTrue(self.allowed(f"git add {via}/notes/n.md && git commit -q -m x && git push", v))
        for cmd in (f"git diff ../home-link/vault/a.md notes/n.md && git push",
                    f"git diff {link}/id_rsa a.md && git push",
                    f"git add {via} && git commit -q -m x && git push"):    # the top level
            self.assertFalse(self.allowed(cmd, v), cmd)
        # What git does with the same words: pathspecs (no output, nothing
        # changed) for the allowed form, a --no-index file diff for the other.
        def diff(*words):
            return subprocess.run(["git", "diff", *words], cwd=v, capture_output=True,
                                  text=True).stdout
        self.assertEqual(diff(f"{via}/a.md", f"{via}/notes/n.md"), "")
        self.assertIn("-a\n+n", diff("../home-link/vault/a.md", "notes/n.md"))

    def test_git_option_spellings_that_mean_more_get_no_answer(self):
        # Bundled short flags, long-option prefixes and pathspecs that git
        # accepts and that mean "everything" or "skip the checks".
        v, push = self.vault, f" && git -C {self.vault} push origin mainline"
        self.assert_no_answer(tuple((f"git -C {v} {c}{push}", None) for c in (
            "commit -am wip", "commit -qam wip", "commit -a -m x", "commit --all -m x",
            "commit -m wip .", "commit -m wip :/", "commit -m wip -- a.md", "commit -m wip a.md",
            "commit -nm wip", "commit --no-verify -m x", "commit --no-verif -m wip",
            "commit --amend --no-edit", "commit --amen -m wip", "commit --mess=x",
            f"commit -F {self.secret}", f"commit --file={self.secret}", f"commit --file {self.secret}",
            "commit -C HEAD", "commit -c HEAD", "commit --reuse-message=HEAD", "commit -t x",
            "commit --template=x", "commit --pathspec-from-file=x", "commit --allow-empty -m x",
            "commit", "commit -q", "commit -m", "commit --message", "commit -qm",
            "add -A", "add --all", "add -vA", "add --al", "add .", "add ./", "add :/",
            f"add {v}", f"add {v}/", "add notes", "add notes/", "add -- .", "add -fv x.md",
            "add -f x.md", "add -u", "add -p", "add -N x.md", "add --pathspec-from-file=x",
            "add '*.md'", "add *", "add 'notes/[n].md'", "add ':(glob)**'", "add ../x.md",
            "add ''", "add", "add x.md -A",
            "pull --rebase=interactive", "pull other mainline", "pull origin side",
            "pull --no-rebase", "status $(id)",
        )))

    def test_shell_tricks_get_no_answer(self):
        v = self.vault
        push = f"git -C {v} push origin mainline"
        self.assert_no_answer((
            (f"git -C {v} add {{x.md,-A}} && {push}", None),            # brace expansion
            (f"git -C {v} add x.md,-A}} && {push}", None),
            (f"git -C {v} add ~+/x.md && {push}", None),                # ~+ is $PWD
            (f"=git -C {v} push origin mainline", None),                # zsh =cmd
            (f"git -C {v} add =a.md && {push}", None),
            (f"git -C {v} add ''=a.md && {push}", None),                # zsh expands this too
            (f"git -C {v} commit -m x \"&&\" {push}", None),            # quoted && is a word
            (f"git -C {v} add !x && {push}", None),
            (f"git -C {v} add a=~/x.md && {push}", None),              # bash expands that ~
            ("cd notes && git push", v),                                # CDPATH could move it
            (f"cd {v}/notes/.. && git push", None),                     # logical vs physical
            ("cd - && git push", v), ("cd && git push", v), (f"cd -P {v} && git push", None),
            (f"cd '~/x' && {push}", None),
            # heredoc text only as a commit message, and only the exact form
            (f"git -C {v} add {HD.format('-A')} && {push}", None),
            (f"git -C {v} push origin {HD.format('mainline')}", None),
            (f"git -C {HD.format(v)} push origin mainline", None),
            (f"cd {HD.format(v)} && git push", None),
            (f"git -C {v} commit -m \"$(cat <<'EOF'\nx\nEOF\nrm -rf y\nEOF\n)\" && {push}", None),
            (f"git -C {v} commit -m \"$(cat <<'EOF'\nx\nEOF\n)\"\n&& {push}", None),
            (f"git -C {v} commit -m \"$(cat <<'EOF'\nx\nEOF)\" && {push}", None),
            # bash 5.2 ends the $(...) at the `)` of a line that starts with
            # the delimiter, so `touch` would run there (zsh reads it as text)
            (f"git -C {v} commit -m \"$(cat <<'EOF'\nx\nEOF)\" && touch {self.tmp}/pwned && echo \"\n"
             f"EOF\n)\" && {push}", None),
            (f"git -C {v} commit -m {HD.format('x' + chr(10) + 'EOFX')} && {push}", None),
            (f"git -C {v} commit -m \"$(cat <<EOF\n$(id)\nEOF\n)\" && {push}", None),
            (f"git -C {v} commit -m \"$(cat <<-'EOF'\nx\n\tEOF\n)\" && {push}", None),
            (f"git -C {v} commit -m \"$(cat <<\"EOF\"\nx\nEOF\n)\" && {push}", None),
            (f"git -C {v} commit -m $(cat <<'EOF'\nx\nEOF\n) && {push}", None),
            (f"git -C {v} commit -m \"x$(cat <<'EOF'\nx\nEOF\n)\" && {push}", None),
            (f"git -C {v} commit -m \"a \"$(cat <<'EOF'\nx y\nEOF\n)\" && {push}", None),
            (f"git -C {v} commit -qm{HD.format('x')} && {push}", None),  # bundled: prompts
        ))

    def test_read_only_suffixes_claude_code_appends_are_allowed(self):
        # The shapes of real vault pushes in transcripts (2026-10): output
        # trimmed with `2>&1 | tail`, then `;` and a log or status to show
        # the result.
        v = self.vault
        msg = '"notes: fix a path\n\nCo-Authored-By: A <a@example.invalid>"'
        for cmd, cwd in (
                (f"git add notes/n.md && git commit -q -m {msg} && git push -q origin HEAD:mainline "
                 "2>&1 | tail -2; git log --oneline -1; git status -sb | head -1", v),
                (f"cd {v} && git pull --ff-only 2>&1 | tail -2 && git diff --stat && git add a.md "
                 f"&& git commit -q -m x && git push 2>&1 | tail -2; git log -1 --format='%h %s'; "
                 "git status --porcelain | head -3", None),
                (f"git -C {v} push -q origin mainline 2>&1 | tail -n 3; git -C {v} log --oneline -n 2 "
                 "HEAD..origin/mainline -- notes", None),
                ("git push 2>&1&& git log --stat --max-count=2 --no-merges", v),
                ("echo start && git push 2>&1 | head && echo PUSHED; git diff --cached --name-only", v),
                ("git commit -m 'a | b; c 2>&1' && git push", v),     # quoted: words, not syntax
                ("git push;git log -3 --pretty=oneline", v)):
            self.assertTrue(self.allowed(cmd, cwd), cmd)

    def test_only_reads_after_a_semicolon_and_cd_needs_and(self):
        v, o = self.vault, self.other
        self.assert_no_answer((
            ("git status; git push", v), ("git add a.md; git push", v),
            ("git log -1; git pull && git push", v),
            (f"cd {v}; git push", o), (f"cd {v} | cat && git push", o),
            (f"cd {v} 2>&1 && git push", o), (f"git push && cd {v}", o),
            (f"git -C {v} push; cd {o} && git status", None),        # cd after ; still needs &&
            # pipes and redirections outside the one accepted form
            ("git push | tail -f", v), ("git push | tail -2 log", v), ("git push | head -c 5", v),
            ("git push | tail -2 | head -1", v), ("git push | sh", v), ("git push |& tail", v),
            ("git push | tail -2 2>&1", v), ("git push 2>/dev/null", v),
            ("git push >/dev/null 2>&1", v), ("git push 2>&1 >log", v), ("2>&1 git push", v),
            ("git push | 2>&1 tail", v), ("git push 1>&2", v), ("git push 2>&1&", v),
            ("git push 2>&12", v), ("git push x2>&1", v), ("git push \"2\">&1", v),
            ("git push origin2>&1", v), ("git push origin mainline2>&1", v),  # remote "origin2"
            ("git status 2>&1 -s && git push", v), ("git push origin 2>&1 mainline", v),
            ("git push;", v), ("; git push", v), ("git push ;; git log", v),
            ("git push & git log", v), ("git push\ngit log", v), ("cd . && | git push", v),
            # reads that write files or run programs
            ("git push && git log --output=x", v), ("git push && git log --format=%G?", v),
            ("git push && git log --pretty=format:%GK", v), ("git push && git log -p", v),
            ("git push && git log --ext-diff", v), ("git push && git log -n", v),
            ("git push && git diff --output=x", v), ("git push && git diff --ext-diff", v),
            ("git push && git diff --no-index a b", v), ("git push && echo $(id)", v),
            ("git push && echo " + HD.format("x"), v),
            ("echo x && git log -1", v),                              # no push
        ))

    def test_a_redirected_remote_gets_no_answer(self):
        # Pushes are approved only to --url, as git resolves it.
        v, evil = self.vault, self.tmp / "evil.git"
        git("init", "-q", "--bare", str(evil))
        cases = (
            (["config", "remote.origin.pushurl", str(evil)], ["config", "--unset", "remote.origin.pushurl"]),
            (["config", "--add", "remote.origin.pushurl", str(self.remote)],
             ["config", "--unset-all", "remote.origin.pushurl"]),  # one pushurl, == url: fine
            (["remote", "set-url", "origin", str(evil)], ["remote", "set-url", "origin", str(self.remote)]),
            (["config", "--add", "remote.origin.url", str(evil)],
             ["config", "--unset", "remote.origin.url", str(evil)]),  # two URLs: pushes to both
            (["config", f"url.{evil}.pushInsteadOf", str(self.remote)],
             ["config", "--remove-section", f"url.{evil}"]),
            (["config", f"url.{evil}.insteadOf", str(self.remote)],
             ["config", "--remove-section", f"url.{evil}"]),
        )
        for setup, undo in cases:
            git(*setup, cwd=v)
            try:
                fine = setup[1] == "--add" and setup[2] == "remote.origin.pushurl"
                for cmd in ("git push origin mainline", "git push", f"git -C {v} push origin HEAD"):
                    self.assertEqual(self.allowed(cmd, v), fine, (setup, cmd))
            finally:
                git(*undo, cwd=v)
        self.assertTrue(self.allowed("git push origin mainline", v))
        r = subprocess.run([sys.executable, "-I", str(HOOK), "--repo", str(v), "--branch", "x"],
                           input="{}", capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)  # --url is required
        self.assertIn("--url", r.stderr)

    def test_head_and_bare_pushes_need_head_on_the_branch(self):
        git("checkout", "-q", "side", cwd=self.vault)
        try:
            for cmd in ("git push", "git push origin", "git push origin HEAD",
                        "git push origin HEAD:mainline", "git push origin HEAD:refs/heads/mainline"):
                self.assertFalse(self.allowed(cmd, self.vault), cmd)
            self.assertTrue(self.allowed("git push origin mainline", self.vault))  # explicit
        finally:
            git("checkout", "-q", "mainline", cwd=self.vault)

    def test_push_config_that_redirects_a_push_gets_no_answer(self):
        v = self.vault
        for key, value, refused in (
                ("push.default", "matching", ("git push", "git push origin")),
                ("branch.mainline.pushRemote", "elsewhere", ("git push",)),
                ("remote.pushDefault", "elsewhere", ("git push",)),
                ("remote.origin.push", "refs/heads/mainline:refs/heads/other",
                 ("git push", "git push origin mainline", "git push origin HEAD")),
                ("remote.origin.mirror", "true", ("git push", "git push origin mainline"))):
            git("config", key, value, cwd=v)
            try:
                for cmd in refused:
                    self.assertFalse(self.allowed(cmd, v), f"{key}={value}: {cmd}")
                if key in ("branch.mainline.pushRemote", "remote.pushDefault"):
                    self.assertTrue(self.allowed("git push origin mainline", v), key)
            finally:
                git("config", "--unset", key, cwd=v)
        self.assertTrue(self.allowed("git push", v))

    def test_upstream_mapping_and_submodule_pushes_get_no_answer(self):
        v = self.vault

        def with_config(pairs, cmds, allowed):
            for key, value in pairs:
                git("config", key, value, cwd=v)
            try:
                for cmd in cmds:
                    self.assertEqual(self.allowed(cmd, v), allowed, (pairs, cmd))
            finally:
                git("config", "branch.mainline.merge", "refs/heads/mainline", cwd=v)
                for key, _ in pairs:
                    if key != "branch.mainline.merge":
                        git("config", "--unset", key, cwd=v)

        # push.default=upstream sends a refspec without :<dst> to the
        # upstream branch: `git push origin mainline` would land on `other`.
        upstream = (("push.default", "upstream"), ("branch.mainline.merge", "refs/heads/other"))
        with_config(upstream, ("git push origin mainline", "git push origin refs/heads/mainline",
                               "git push origin HEAD", "git push"), False)
        with_config(upstream, ("git push origin mainline:mainline", "git push origin HEAD:mainline"),
                    True)
        with_config((("push.default", "tracking"), ("branch.mainline.merge", "refs/heads/other")),
                    ("git push origin mainline",), False)
        with_config((("push.default", "upstream"),), ("git push origin mainline", "git push"), True)
        # receivepack runs a program of its own choosing; submodules have their own URLs.
        for pairs in ((("remote.origin.receivepack", "git-receive-pack"),),
                      (("push.recurseSubmodules", "on-demand"),), (("push.recurseSubmodules", "only"),),
                      (("submodule.recurse", "true"),)):
            with_config(pairs, ("git push origin mainline", "git push"), False)
        for pairs in ((("push.recurseSubmodules", "check"),), (("push.recurseSubmodules", "no"),),
                      (("submodule.recurse", "true"), ("push.recurseSubmodules", "no")),
                      (("submodule.recurse", "false"),)):
            with_config(pairs, ("git push origin mainline",), True)

    def test_an_allowed_push_lands_on_the_branch(self):
        # End to end on the fixture: push.default=upstream with a matching
        # merge ref, then a push the hook approves, and the remote has it.
        v = self.vault
        git("config", "push.default", "upstream", cwd=v)
        try:
            (v / "notes" / "landed.md").write_text("x\n")
            cmd = "git add notes/landed.md && git commit -q -m landed && git push -q origin mainline"
            self.assertTrue(self.allowed(cmd, v))
            subprocess.run(["bash", "-c", cmd], cwd=v, check=True, capture_output=True,
                           env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                                    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x"))
            refs = subprocess.run(["git", "for-each-ref", "--format=%(refname) %(subject)"],
                                  cwd=self.remote, check=True, capture_output=True, text=True)
            self.assertEqual(refs.stdout.split("\n")[0], "refs/heads/mainline landed")
            self.assertEqual(len(refs.stdout.split()), 2, refs.stdout)
        finally:
            git("config", "--unset", "push.default", cwd=v)

    def test_other_tools_and_bad_payloads_get_no_answer(self):
        self.assertEqual(self.run_hook("git push", self.vault, tool="Write").stdout, "")
        for raw in ("", "not json", "[]", '{"tool_name": "Bash"}',
                    '{"tool_name": "Bash", "cwd": "rel", "tool_input": {"command": "git push"}}'):
            self.assertEqual(self.run_hook(None, raw=raw).stdout, "", raw)

    def test_internal_error_fails_open_and_is_logged(self):
        # tool_input as a list makes decide() raise: the hook must still exit
        # 0 with no decision and leave a log line.
        r = self.run_hook(None, raw='{"tool_name": "Bash", "cwd": "/", "tool_input": ["x"]}')
        self.assertEqual(r.stdout, "")
        self.assertIn("internal error", r.stderr)
        self.assertIn("internal error", (self.home / "allow_repo_push.log").read_text())

    def test_fast_enough_for_a_permission_prompt(self):
        v = self.vault
        for cmd in (f"git -C {v} push origin mainline",
                    f"git -C {v} add notes/n.md && git -C {v} commit -m {HD.format('x')} "
                    f"&& git -C {v} push"):
            times = []
            for _ in range(7):
                t = time.perf_counter()
                self.assertTrue(self.allowed(cmd))
                times.append((time.perf_counter() - t) * 1000)
            self.assertLess(statistics.median(times), 150, times)


BIG_HEREDOC = (
    "git commit -m \"$(cat <<'EOF'\nvault: add x\n\nBody with \"quotes\", 'ticks', $(id), `id`, ) and ( "
    "and EOF.\n EOF\n)\"\n)\" && x\nx)\n\nCo-Authored-By: A <a@example.invalid>\nEOF\n)\" && git push")

# Words the lexer accepts must be exactly the words the shell passes. Each
# part's first word is git or cd, which the shell runs as a function that
# prints its arguments; PATH holds only cat (for the heredoc form).
ACCEPTED = [
    "git push",
    "git -C /tmp/a b push origin mainline",
    "git commit -m 'a b; c && d | e' && git push",
    'git commit -m "say \\"hi\\" \\\\ \\$5 \\` (x) & y" && git push',
    "git commit -m 'it'\\''s' && git push",
    "git commit -m a'b'\"c\"d\\ e && git push",
    "git commit -m \"line one\nline two\" -m 'p\nq' && git push",
    'git commit -m "join \\\nme" && git push',
    "git add ~/x.md ~ && cd ~/y && git push",
    "git add x.md\\ y && git push",
    "git commit -m \"\" '' && git push",
    "git commit --message=café -m 'ünï' && git push",
    "git push origin 50%+1,@x:y",
    BIG_HEREDOC,
    "git commit -m \"$(cat <<'EOF'\n\n\ntrailing newlines go\n\n\nEOF\n)\"tail && git push",
    "git commit -m \"$(cat <<'EOF'\nEOF\n)\" && git push",
    "git commit -m \"$(cat <<'MSG'\nx\nEOF\nMSG\n)\"&&git push",
    # separators, the 2>&1 | head/tail suffix, echo
    "git push -q origin HEAD:mainline 2>&1 | tail -2; git log --oneline -1; git status -sb | head -1",
    "echo 'a  b' \"c|d\" x\\;y && git push 2>&1&& git log;git status|head -n 3",
    "git commit -m 'x | y; z 2>&1' && git push 2>&1|tail -n2",
]


def shell_words(shell: str, command: str, bindir: str) -> list:
    """Each simple command the shell runs, as its words. The stand-ins write
    one record per call, in one printf, to fd 3 (the real stdout), so pipes
    and 2>&1 don't redirect them; pipeline members run concurrently, so the
    records come back sorted."""
    prelude = "exec 3>&1; " + "".join(
        f"{f}() {{ printf '%s\\0' {f} \"$@\" $'\\1' >&3; }}; "
        for f in ("git", "cd", "echo", "head", "tail"))
    exe = shutil.which(shell)
    argv = [exe, "--norc", "--noprofile", "-c"] if shell == "bash" else [exe, "-f", "-c"]
    # As bytes: in a C locale without Python's UTF-8 mode, a str argv must be ASCII.
    r = subprocess.run([*argv, (prelude + command).encode("utf-8")], capture_output=True,
                       timeout=10, env={"PATH": bindir, "HOME": os.environ.get("HOME", "/")})
    if r.returncode != 0:
        raise AssertionError(f"{shell} failed on {command!r}: {r.stderr!r}")
    out = r.stdout.decode()
    return sorted(p.split("\0")[:-1] for p in out.split("\1\0")[:-1])


class LexerMatchesTheShell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hook = load_hook()
        cls.bindir = tempfile.mkdtemp(prefix="allow-push-bin-")
        os.symlink(shutil.which("cat"), os.path.join(cls.bindir, "cat"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.bindir, ignore_errors=True)

    def commands(self, cmd):
        out = []
        for part in self.hook.split_parts(self.hook.lex(cmd)):
            out.append([str(w) for w in part.words])
            if part.pipe_to:
                out.append([str(w) for w in part.pipe_to])
        return sorted(out)

    def check(self, shell):
        if not shutil.which(shell):
            self.skipTest(f"{shell} not installed")
        for cmd in ACCEPTED:
            self.assertEqual(self.commands(cmd), shell_words(shell, cmd, self.bindir), cmd)

    def test_bash(self):
        self.check("bash")

    def test_zsh(self):
        self.check("zsh")

    def test_heredoc_words_are_marked(self):
        parts = self.hook.split_parts(self.hook.lex(BIG_HEREDOC))
        self.assertEqual([w.heredoc for w in parts[0].words], [False, False, False, True])
        self.assertFalse(any(w.heredoc for w in parts[1].words))

    def test_parts_record_operators_pipes_and_redirects(self):
        parts = self.hook.split_parts(self.hook.lex(
            "cd /v && git push 2>&1 | tail -2; git log -1 2>&1;git status | head"))
        self.assertEqual([(p.words[0], p.stderr_to_stdout, p.pipe_to, p.then) for p in parts],
                         [("cd", False, None, "&&"), ("git", True, ["tail", "-2"], ";"),
                          ("git", True, None, ";"), ("git", False, ["head"], None)])
        # A quoted operator is a word.
        parts = self.hook.split_parts(self.hook.lex("git commit -m '|' \"&&\" ';' '2>&1'"))
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].words[3:], ["|", "&&", ";", "2>&1"])
        self.assertFalse(parts[0].stderr_to_stdout)


if __name__ == "__main__":
    unittest.main()
