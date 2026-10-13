"""The committed templates: valid JSON, documented keys only, well-formed rules."""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import merge  # noqa: E402

USER = ROOT / "user.template.json"
GATES = ROOT / "gates.template.json"

# Every key below is documented at https://code.claude.com/docs/en/settings-reference
# (checked against Claude Code 2.1.294). A new key needs the same check first.
DOCUMENTED = {
    "$schema", "statusLine", "subagentStatusLine", "modelSettings", "effortLevel",
    "autoCompactWindow", "promptCacheTtl", "bashEditDiffEnabled", "env", "permissions",
}
DOCUMENTED_ENV = {"DISABLE_AUTOUPDATER"}


def load(path: Path) -> dict:
    return merge.substitute(merge.load_json(path)[0], {"DOTFILES": str(ROOT.parent)}, path.name)


class UserTemplate(unittest.TestCase):
    def setUp(self):
        self.t = load(USER)

    def test_only_documented_keys(self):
        self.assertLessEqual(set(self.t), DOCUMENTED)
        self.assertLessEqual(set(self.t.get("env", {})), DOCUMENTED_ENV)

    def test_value_shapes_match_the_reference(self):
        self.assertEqual(self.t["statusLine"]["type"], "command")
        self.assertGreaterEqual(self.t["statusLine"]["refreshInterval"], 1)
        self.assertEqual(self.t["subagentStatusLine"]["type"], "command")
        for model, conf in self.t["modelSettings"].items():
            self.assertRegex(model, r"^claude-[a-z]+-\d+(-\d+)?$")  # canonical name
            self.assertIn(conf["effortLevel"], {"low", "medium", "high", "xhigh"})
        self.assertIn(self.t["effortLevel"], {"low", "medium", "high", "xhigh"})
        self.assertTrue(100000 <= self.t["autoCompactWindow"] <= 1000000)
        self.assertIn(self.t["promptCacheTtl"], {"5m", "1h"})
        self.assertTrue(all(isinstance(v, str) for v in self.t["env"].values()))

    def test_status_line_commands_point_at_executable_scripts(self):
        for key in ("statusLine", "subagentStatusLine"):
            m = re.match(r'^"([^"]+)"', self.t[key]["command"])
            self.assertIsNotNone(m, key)
            self.assertTrue(os.access(m.group(1), os.X_OK), m.group(1))

    def test_compact_window_arg_matches_auto_compact_window(self):
        m = re.search(r"--compact-window (\d+)", self.t["statusLine"]["command"])
        self.assertEqual(int(m.group(1)), self.t["autoCompactWindow"])


class GatesTemplate(unittest.TestCase):
    def setUp(self):
        self.p = load(GATES)["permissions"]

    def rules(self):
        return [r for k in ("allow", "ask", "deny") for r in self.p.get(k, [])]

    def test_rule_syntax(self):
        for r in self.rules():
            self.assertRegex(r, r"^[A-Za-z_*]+(\(.+\))?$", r)
            # Claude Code skips any mcp__ rule written with parentheses.
            if r.startswith("mcp__"):
                self.assertNotIn("(", r)

    def test_read_rules_are_anchored_for_user_settings(self):
        # In ~/.claude/settings.json a single leading "/" anchors at ~/.claude
        # and a bare path at the cwd, so user-wide path rules need ~/ or //.
        for r in self.rules():
            m = re.match(r"^(Read|Edit)\((.+)\)$", r)
            if m:
                self.assertTrue(m.group(2).startswith(("~/", "//")), r)

    def test_push_asks_and_force_push_is_denied(self):
        self.assertIn("Bash(git push *)", self.p["ask"])
        for r in ("Bash(git push --force*)", "Bash(git push * --force*)", "Bash(git push -f*)",
                  "Bash(git filter-branch *)"):
            self.assertIn(r, self.p["deny"])

    def test_no_duplicates(self):
        for k, v in self.p.items():
            self.assertEqual(len(v), len(set(v)), k)

    def decide(self, command: str) -> str:
        """deny, then ask, then allow; the first list with a match decides."""
        for kind in ("deny", "ask", "allow"):
            for r in self.p.get(kind, []):
                m = re.match(r"^Bash\((.+)\)$", r)
                if m and bash_rule_matches(m.group(1), command):
                    return kind
        return "none"

    def test_force_pushes_and_history_rewrites_are_denied(self):
        for cmd in ("git push --force", "git push --force origin main",
                    "git push origin main --force", "git push --force-with-lease",
                    "git push -f", "git push -f origin main", "git push origin main -f",
                    "git push origin +main", "git push origin +HEAD:refs/heads/x",
                    "git push --mirror", "git push origin --mirror",
                    # git takes any unique prefix of --mirror, down to --m
                    "git push --m origin", "git push --mirr origin", "git push origin --mi",
                    # a whole flag or refspec in quotes
                    "git push origin '+main'", 'git push origin "+main"', "git push origin main '-f'",
                    'git push "-f" origin main', "git push '--force'",
                    'git push origin "--force-with-lease"', "git push '--mirror' origin",
                    "git filter-branch --tree-filter 'rm x' HEAD", "git filter-repo --path x"):
            self.assertEqual(self.decide(cmd), "deny", cmd)

    def test_ordinary_pushes_ask(self):
        for cmd in ("git push", "git push origin main", "git push -u origin feature",
                    "git push --follow-tags origin main", "git push --set-upstream origin fix-f",
                    "git push origin feature-flag", "git -C /repo push origin main",
                    "git -C /repo push", "git -c push.default=current push origin x",
                    "git --no-pager push origin main", "git -P push", "git --git-dir=.git push",
                    "git push origin 'feature-x'", 'git push origin "fix-f"',
                    "git push -u origin \"main\"", "git push --set-upstream origin 'my-f'",
                    "git push origin 'a+b'", "git push -o 'ci.skip' origin main"):
            self.assertEqual(self.decide(cmd), "ask", cmd)

    def test_documented_gaps_ask_instead_of_deny(self):
        # Force pushes the deny rules can't see: combined short flags (a
        # `git push -*f*` rule would also deny --follow-tags), anything after
        # a git global option, and flags only partly in quotes. The ask rules
        # still catch them; the guard hooks, which parse argv, are the firm
        # boundary.
        for cmd in ("git push -uf origin main", "git push -vf origin main",
                    "git -C /repo push --force", "git -C /repo push -f origin x",
                    "git -C /repo push origin +x", "git -c color.ui=never push --force origin main",
                    "git --no-pager push -f origin main", "git --git-dir=.git push -f",
                    "git push origin main -'f'", "git push --'force' origin main",
                    'git push "-"f origin main', "git push origin \\+main"):
            self.assertEqual(self.decide(cmd), "ask", cmd)

    def test_other_spellings_match_no_rule(self):
        # The program by path or through env: no Bash rule sees these, so the
        # mode and allow rules decide (default mode prompts unless an allow
        # rule matches). Documented under "What the rules don't do".
        for cmd in ("/usr/bin/git push --force", "env git push --force", "git 'push' --force"):
            self.assertEqual(self.decide(cmd), "none", cmd)

    def test_commit_messages_never_hit_a_deny(self):
        # Deny rules are anchored at `git push`, so text after another git
        # subcommand can't reach them, however it mentions push.
        for cmd in ('git -C /r commit -m "fix: handle push -f in the guard"',
                    'git -C /r commit -m "note: push x +1"',
                    'git -C ~/repo commit -m "note: never push --force"',
                    'git -C /r commit -m "push --mirror is denied"',
                    'git commit -m "fix: handle push -f in the guard"'):
            self.assertNotEqual(self.decide(cmd), "deny", cmd)

    def test_word_push_after_a_global_option_asks(self):
        # The documented false positive of `git -* push *`: a git command that
        # starts with a global option and has `push` as a word anywhere.
        for cmd in ('git -C /r commit -m "docs: explain why every push asks"',
                    "git -C /r log --oneline --grep push", "git -C /r stash push -m wip"):
            self.assertEqual(self.decide(cmd), "ask", cmd)

    def test_other_git_is_untouched(self):
        for cmd in ("git status", "git pull --ff-only", "git fetch -f", "git log --oneline",
                    'git commit -m "explain why every push asks"', "git stash push -m wip",
                    "git -C ~/projects/push-service status", "git -C . log --oneline --grep=push",
                    "git -C . diff -- src/push.py", 'git -C /r commit -m "pushed the fix"',
                    "git -C /r remote set-url --push origin x"):
            self.assertEqual(self.decide(cmd), "none", cmd)


def bash_rule_matches(pattern: str, command: str) -> bool:
    """The documented Bash rule matching (permissions.md, Wildcard patterns):
    `*` matches any text including spaces, the rest is literal, and a trailing
    ` *` that is the rule's only wildcard also matches the bare command. A
    model of Claude Code's matcher for checking our rule text, not a copy."""
    rx = ".*".join(re.escape(part) for part in pattern.split("*"))
    if re.fullmatch(rx, command, re.S):
        return True
    return (pattern.endswith(" *") and pattern.count("*") == 1
            and command == pattern[:-2])


class TemplatesMergeIntoEachOther(unittest.TestCase):
    def test_both_templates_merge_without_conflicts(self):
        log = []
        merge.merge({}, merge.combine([load(USER), load(GATES)]), (), log)
        self.assertFalse([e for e in log if e[0] in ("keep", "override")])


if __name__ == "__main__":
    unittest.main()
