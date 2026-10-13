"""sql_guard, recipient_guard and datanet_guard through their real entry points,
with the tool shapes of redshift-mcp-server 0.8.1.173 and aws-outlook-mcp 0.4.0.
A block is a permissionDecision "deny" with exit 0 (see hooks/guards/common.py).

The guards' tests use placeholder values only. TestPrivateValues checks the
real values file (skills-amazon/guards/private.json, kept out of git) when this
checkout has one, and that no value in it appears in the component's committed
files; it is skipped, with the reason, when the file is absent."""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
AMAZON = HERE.parent                                   # hooks/guards/amazon
GUARDS = AMAZON.parent                                 # hooks/guards
REPO = GUARDS.parents[1]
PRIVATE = REPO / "skills-amazon" / "guards" / "private.json"
EXAMPLE = AMAZON / "private.example.json"
sys.path.insert(0, str(GUARDS / "tests"))

from support import Sandbox, post, pre  # noqa: E402

import precommit_scan as PS  # noqa: E402

OUTLOOK = "mcp__aws-outlook-mcp__"
DENY = (0, "deny")
ALLOW = (0, None)


class AmazonCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.values = self.sb.tmp / "private.json"
        self.values.write_text(json.dumps({"recipient_guard": {"allow": ["me@example.com",
                                                                         " Me.Too@Example.com "]}}))
        self.sb.env["CLAUDE_GUARDS_PRIVATE"] = str(self.values)

    def tearDown(self):
        self.sb.cleanup()

    def hook(self, script, payload, env=None):
        rc, out, err = self.sb.run(script, payload, env=env, script_dir=AMAZON)
        o = json.loads(out) if out else {}
        return rc, o.get("hookSpecificOutput", {}), err

    def decision(self, script, payload, env=None):
        rc, hso, _ = self.hook(script, payload, env)
        return rc, hso.get("permissionDecision")


class TestSqlGuard(AmazonCase):
    def q(self, sql, tool="mcp__redshift-mcp__execute_query"):
        return self.decision("sql_guard.py", pre(tool, {"sql": sql}))

    def test_leading_comments_and_parens_are_denied(self):
        for sql in ("-- weekly NVF\nSELECT 1", "  /* why */ SELECT 1", "\n--x\nWITH a AS (SELECT 1) SELECT * FROM a",
                    "(SELECT 1) UNION (SELECT 2)"):
            with self.subTest(sql[:8]):
                self.assertEqual(self.q(sql), DENY)
        for server in ("redshift-mcp-server", "aaisd-redshift-mcp"):
            for tool in ("execute_query", "explain_query"):
                with self.subTest(f"{server} {tool}"):
                    self.assertEqual(self.q("-- c\nSELECT 1", f"mcp__{server}__{tool}"), DENY)

    def test_comments_inside_and_other_statements_are_fine(self):
        for sql in ("SELECT /* why */ 1", "select 1 -- trailing", "WITH a AS (\n-- step\nSELECT 1) SELECT 1",
                    "SHOW TABLES", "CREATE TEMP TABLE t AS SELECT 1", "INSERT INTO x VALUES (1)"):
            with self.subTest(sql[:8]):
                self.assertEqual(self.q(sql), ALLOW)
        self.assertEqual(self.decision("sql_guard.py", pre("mcp__other__execute_query", {"sql": "-- x"})), ALLOW)


class TestRecipientGuard(AmazonCase):
    def mail(self, op, **ti):
        return self.decision("recipient_guard.py", pre(OUTLOOK + op, ti))

    def test_send_needs_every_address_allowlisted(self):
        self.assertEqual(self.mail("email_send", to=["me@example.com"], cc=["ME.TOO@example.com"],
                                   subject="s", body="<html>"), ALLOW)
        self.assertEqual(self.mail("email_send", to=["Me <me@example.com>"], subject="s", body="b"), ALLOW)
        rc, hso, err = self.hook("recipient_guard.py", pre(OUTLOOK + "email_send", {
            "to": ["me@example.com"], "bcc": ["boss@example.org"], "subject": "s", "body": "b"}))
        self.assertEqual((rc, hso["permissionDecision"]), DENY)
        self.assertIn("boss@example.org", hso["permissionDecisionReason"])
        self.assertIn("draft", hso["permissionDecisionReason"])

    def test_forward_and_reply_all(self):
        self.assertEqual(self.mail("email_forward", itemId="i", itemChangeKey="k", to=["x@example.org"]), DENY)
        self.assertEqual(self.mail("email_forward", itemId="i", itemChangeKey="k", to=["me@example.com"]), ALLOW)
        self.assertEqual(self.mail("email_forward", itemId="i", itemChangeKey="k", to=["me@example.com"],
                                   cc=["x@example.org"]), DENY)
        self.assertEqual(self.mail("email_reply", itemId="i", itemChangeKey="k", body="b", replyAll=True), DENY)
        self.assertEqual(self.mail("email_reply", itemId="i", itemChangeKey="k", body="b", replyAll=True,
                                   saveAsDraft=True), ALLOW)

    def test_plain_reply_asks_by_default(self):
        self.assertEqual(self.mail("email_reply", itemId="i", itemChangeKey="k", body="b"), (0, "ask"))
        env = dict(self.sb.env, RECIPIENT_GUARD_UNVERIFIED="deny")
        self.assertEqual(self.decision("recipient_guard.py", pre(OUTLOOK + "email_reply", {
            "itemId": "i", "itemChangeKey": "k", "body": "b"}), env=env), DENY)
        self.assertEqual(self.mail("email_reply", itemId="i", itemChangeKey="k", body="b",
                                   cc=["stranger@example.org"]), DENY)

    def test_drafts(self):
        self.assertEqual(self.mail("email_draft", operation="create", to=["anyone@example.org"],
                                   subject="s", body="b"), ALLOW)
        self.assertEqual(self.mail("email_draft", operation="update", draftId="d", draftChangeKey="c",
                                   send=True), DENY)
        self.assertEqual(self.mail("email_draft", operation="update", draftId="d", draftChangeKey="c",
                                   body="new"), ALLOW)

    def test_meeting_invitations(self):
        meet = dict(subject="s", start="2026-10-12T10:00:00.000", end="2026-10-12T10:30:00.000",
                    body="<p>agenda</p>")
        self.assertEqual(self.mail("calendar_meeting", operation="create", attendees=["someone@external.example"],
                                   **meet), DENY)
        self.assertEqual(self.mail("calendar_meeting", operation="create", attendees=["me@example.com"],
                                   optionalAttendees=["x@example.org"], **meet), DENY)
        self.assertEqual(self.mail("calendar_meeting", operation="create", attendees=["me@example.com"],
                                   resources=["room-1@example.org"], **meet), DENY)
        self.assertEqual(self.mail("calendar_meeting", operation="create", attendees=["Me <me@example.com>"],
                                   **meet), ALLOW)
        self.assertEqual(self.mail("calendar_meeting", operation="update", meetingId="m", meetingChangeKey="c",
                                   attendees=["x@example.org"]), DENY)
        for extra in ({"body": "<p>new</p>"}, {"rsvpResponse": "accept"}):
            with self.subTest(extra):
                self.assertEqual(self.mail("calendar_meeting", operation="update", meetingId="m",
                                           meetingChangeKey="c", **extra), (0, "ask"))
        for ti in ({"operation": "update", "meetingId": "m", "categories": ["Red"], "reminderMinutes": 5},
                   {"operation": "read", "meetingId": "m"}, {"operation": "delete", "meetingId": "m"}):
            with self.subTest(ti):
                self.assertEqual(self.mail("calendar_meeting", **ti), ALLOW)

    def send(self, sid="sess-1"):
        return self.sb.run("recipient_guard.py", pre(OUTLOOK + "email_send", {
            "to": ["x@example.org"], "subject": "s", "body": "b"}, sid=sid), script_dir=AMAZON)

    def test_no_values_file_lets_mail_through_and_says_how_to_set_it_up(self):
        self.values.unlink()
        rc, out, err = self.send()
        self.assertEqual(rc, 0)
        msg = json.loads(out)["systemMessage"]
        self.assertIn("not checking recipients", msg)
        self.assertIn(str(EXAMPLE), msg)
        self.assertIn(str(self.values), msg)
        self.assertNotIn("permissionDecision", out)
        self.assertIn("not enforced", err)
        self.assertIn("recipient_guard", (self.sb.state / "errors.log").read_text())
        self.assertEqual(self.send()[1], "")                                   # one notice per session
        self.assertEqual(self.mail("email_draft", operation="update", draftId="d", draftChangeKey="c",
                                   send=True), DENY)                           # needs no allowlist

    def test_an_empty_or_invalid_values_file_is_not_a_block(self):
        for i, text in enumerate(('{"recipient_guard": {"allow": []}}', '{"other": 1}', "[1]")):
            with self.subTest(text):
                self.values.write_text(text)
                rc, out, _ = self.send(sid=f"s{i}")
                self.assertEqual(rc, 0)
                self.assertIn("has no recipient_guard.allow addresses", json.loads(out)["systemMessage"])
        self.values.write_text("{not json")
        rc, out, _ = self.send(sid="s-bad")
        self.assertEqual(rc, 0)
        self.assertIn("internal error", json.loads(out)["systemMessage"])

    def test_default_values_file_is_the_ignored_one(self):
        sys.path.insert(0, str(AMAZON))
        import recipient_guard as r
        self.assertEqual(r.VALUES, PRIVATE)
        self.assertEqual(r.EXAMPLE, EXAMPLE)
        if not shutil.which("git"):
            self.skipTest("git not on PATH")
        p = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", str(PRIVATE)],
                           capture_output=True, timeout=10)
        if p.returncode == 128:
            self.skipTest("not a git checkout")
        self.assertEqual(p.returncode, 0, f"{PRIVATE} is not gitignored")


class TestDatanetGuard(AmazonCase):
    def setUp(self):
        super().setUp()
        self.kit = self.sb.tmp / "datanet-api"
        self.kit.mkdir()
        (self.kit / "create_pipeline.py").write_text("print('x')\n")
        (self.kit / "job.sql").write_text("select 1;\n")
        self.cwd = str(self.sb.tmp)

    def bash(self, cmd, event="PreToolUse", cwd=None):
        payload = (pre if event == "PreToolUse" else post)("Bash", {"command": cmd}, *(
            [] if event == "PreToolUse" else [{"stdout": "ok", "stderr": ""}]), cwd=cwd or self.cwd)
        return self.hook("datanet_guard.py", payload)

    def d(self, cmd, cwd=None):
        rc, hso, _ = self.bash(cmd, cwd=cwd)
        return rc, hso.get("permissionDecision")

    def dry_then_live(self, dry_cmd, live_cmd, cwd=None):
        self.assertEqual(self.d(dry_cmd, cwd=cwd), ALLOW)
        self.bash(dry_cmd, "PostToolUse", cwd=cwd)
        return self.bash(live_cmd, cwd=cwd)

    def test_live_without_dry_run_is_denied(self):
        rc, hso, err = self.bash("python3 datanet-api/create_pipeline.py --sql-file datanet-api/job.sql")
        self.assertEqual((rc, hso["permissionDecision"]), DENY)
        self.assertIn("DRY_RUN=true first", hso["permissionDecisionReason"])

    def test_dry_run_then_identical_live_run(self):
        args = "datanet-api/create_pipeline.py --name n --sql-file datanet-api/job.sql"
        rc, hso, _ = self.dry_then_live(f"DRY_RUN=true python3 {args} 2>&1 | tail -40", f"python3 {args}")
        self.assertEqual(rc, 0)
        self.assertIn("matching dry run", hso["additionalContext"])

    def test_multiline_command_with_continuations(self):
        args = "datanet-api/create_pipeline.py \\\n  --name n \\\n  --sql-file datanet-api/job.sql"
        rc, hso, _ = self.dry_then_live(f"DRY_RUN=TRUE python3 {args}", f"python3 {args}")
        self.assertEqual((rc, hso.get("permissionDecision")), ALLOW)

    def test_dry_run_1_is_live(self):
        rc, hso, _ = self.bash("DRY_RUN=1 python3 datanet-api/create_pipeline.py --name n")
        self.assertEqual(hso["permissionDecision"], "deny")
        self.assertIn("DRY_RUN=1 runs LIVE", hso["permissionDecisionReason"])
        self.bash("DRY_RUN=1 python3 datanet-api/create_pipeline.py --name n", "PostToolUse")
        self.assertEqual(self.d("python3 datanet-api/create_pipeline.py --name n"), DENY)   # never stamped
        self.assertEqual(self.d("DRY_RUN=true; python3 datanet-api/create_pipeline.py --name n"), DENY)

    def test_unset_after_set_is_live(self):
        t = "datanet-api/create_pipeline.py --name u"
        for cmd in (f"DRY_RUN=true env -u DRY_RUN python3 {t}", f"DRY_RUN=true env --unset=DRY_RUN python3 {t}",
                    f"DRY_RUN=true env -i PATH=/usr/bin python3 {t}",
                    f"export DRY_RUN=true; unset DRY_RUN; python3 {t}",
                    f"export DRY_RUN=true && export -n DRY_RUN && python3 {t}"):
            with self.subTest(cmd):
                self.assertEqual(self.d(cmd), DENY)
                self.bash(cmd, "PostToolUse")
                self.assertFalse((self.sb.state / "datanet-dryruns.jsonl").exists())   # never stamped
        self.assertEqual(self.d(f"env -u OTHER DRY_RUN=true python3 {t}"), ALLOW)

    def test_parallel_handlers_stamp_once(self):
        cmd = "DRY_RUN=true python3 datanet-api/create_pipeline.py --name p"
        payload = self.sb.tmp / "payload.json"
        payload.write_text(json.dumps(post("Bash", {"command": cmd}, {"stdout": "ok", "stderr": ""}, cwd=self.cwd)))
        procs = []
        for _ in range(11):                                   # all 11 `if` handlers at once
            with open(payload) as stdin:
                procs.append(subprocess.Popen([sys.executable, "-I", str(AMAZON / "datanet_guard.py")],
                                              stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              env=self.sb.env, text=True))
        results = [(*p.communicate(timeout=30), p.returncode) for p in procs]
        self.assertEqual(results, [("", "", 0)] * 11)
        lines = (self.sb.state / "datanet-dryruns.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(self.d("python3 datanet-api/create_pipeline.py --name p"), ALLOW)

    def test_changed_args_or_files_need_a_new_dry_run(self):
        base = "datanet-api/create_pipeline.py --sql-file datanet-api/job.sql"
        self.bash(f"DRY_RUN=true python3 {base}", "PostToolUse")
        self.assertEqual(self.d(f"python3 {base} --extra"), DENY)
        (self.kit / "job.sql").write_text("select 2;\n")
        self.assertEqual(self.d(f"python3 {base}"), DENY)

    def test_cd_export_and_env_forms(self):
        rc, hso, _ = self.dry_then_live("cd datanet-api && export DRY_RUN=true && python3 create_pipeline.py --name n",
                                        "cd datanet-api && python3 create_pipeline.py --name n")
        self.assertEqual((rc, hso.get("permissionDecision")), ALLOW)
        rc, hso, _ = self.dry_then_live("env DRY_RUN=true python3 datanet-api/create_pipeline.py --name e",
                                        "python3 datanet-api/create_pipeline.py --name e")
        self.assertEqual((rc, hso.get("permissionDecision")), ALLOW)
        kit_cwd = str(self.kit)                                  # cwd already in the toolkit dir
        self.assertEqual(self.d("python3 create_pipeline.py --name k", cwd=kit_cwd), DENY)

    def test_wrappers_keywords_and_subshells_are_followed(self):
        t = "datanet-api/create_pipeline.py"
        for cmd in (f"timeout 600 python3 {t} --job 1", f"nohup python3 {t} --job 1 &",
                    f"time python3 {t} --job 1", f"bash -c 'python3 {t} --job 1'",
                    f"sh -c 'DRY_RUN=false python3 {t} --job 1'",
                    f"(cd datanet-api && python3 create_pipeline.py --job 1)",
                    f"python3 -X dev {t} --job 1", f"python3 -W ignore {t} --job 1",
                    f"/usr/bin/env python3 {t} --job 1", "cd datanet-api && python3 -m create_pipeline --job 1",
                    f"uv run python {t} --job 1", f"for j in 1 2; do python3 {t} --job $j; done",
                    f"if true; then python3 {t} --job 1; fi", f"out=$(python3 {t} --job 1)",
                    f"DRY_RUN=' true' python3 {t} --job 1", f"sudo -u svc nice -n 5 python3 {t} --job 1",
                    f"./{t} --job 1"):
            with self.subTest(cmd):
                self.assertEqual(self.d(cmd), DENY)                 # live, no stamp
        rc, hso, _ = self.dry_then_live(f"DRY_RUN=true timeout 600 nohup python3 {t} --job 7",
                                        f"timeout 900 python3 {t} --job 7")
        self.assertEqual((rc, hso.get("permissionDecision")), ALLOW)  # same script and args
        self.assertEqual(self.d(f"bash -lc 'DRY_RUN=true python3 {t} --job 8'"), ALLOW)

    def test_forms_it_cannot_follow_are_refused_with_the_plain_form(self):
        t = "datanet-api/create_pipeline.py"
        for cmd in (f"echo 1 | xargs -I{{}} python3 {t} --job {{}}", f"watch -n 60 python3 {t} --job 1",
                    f"python3 -c 'import runpy; runpy.run_path(\"{t}\")'", f"bash run_all.sh {t}",
                    f"DRY_RUN=true python3 {t} --name 'unterminated",
                    "cd datanet-api && python3 - --job 1 < create_pipeline.py",
                    f"cat {t} | python3 - --job 1", f"python3 <(cat {t}) --job 1", f"python3 <{t}",
                    f"python3 -u /dev/stdin --job 1 < {t}", f"S={t}; python3 $S --job 1",
                    f"PYTHONSTARTUP={t} python3 -i -c pass"):
            with self.subTest(cmd):
                rc, hso, _ = self.bash(cmd)
                self.assertEqual((rc, hso.get("permissionDecision")), DENY)
                self.assertIn("plain command", hso["permissionDecisionReason"])

    def test_reading_and_editing_the_scripts_pass(self):
        t = "datanet-api/create_pipeline.py"
        for cmd in (f"cat {t}", f"sed -n 1,40p {t}", f"grep -n DRY_RUN {t}", f"git diff -- {t}",
                    f"python3 -m py_compile {t}", f"wc -l {t} && head -5 {t}", f"vim {t}",
                    "python3 run_job.py --x", "grep -n create_pipeline datanet-api/*.py",
                    "python3 datanet-api/get_job_schedule.py 123", "cat datanet-api/README.md",
                    f"cat {t} | python3 -c 'import sys; print(len(sys.stdin.read()))'",
                    "cd datanet-api && python3 - <<'EOF'\nprint(1)\nEOF"):
            with self.subTest(cmd):
                self.assertEqual(self.d(cmd), ALLOW)

    def test_stale_stamp_expires(self):
        self.bash("DRY_RUN=true python3 datanet-api/create_pipeline.py --name n", "PostToolUse")
        stamps = self.sb.state / "datanet-dryruns.jsonl"
        s = json.loads(stamps.read_text().strip())
        s["at"] = time.time() - 25 * 3600
        stamps.write_text(json.dumps(s) + "\n")
        self.assertEqual(self.d("python3 datanet-api/create_pipeline.py --name n"), DENY)




class TestApply(unittest.TestCase):
    def apply(self, *args, env=None):
        return subprocess.run(["sh", str(AMAZON / "apply.sh"), *args], capture_output=True, text=True,
                              env=env)

    def test_fragment_is_valid_and_leaves_recipient_out_by_default(self):
        p = self.apply("--print")
        self.assertEqual(p.returncode, 0, p.stderr)
        frag = json.loads(p.stdout)
        pre_m = [g["matcher"] for g in frag["hooks"]["PreToolUse"]]
        self.assertFalse(any("outlook" in m for m in pre_m))
        sql = re.compile(pre_m[0])
        for tool in ("mcp__redshift-mcp__execute_query", "mcp__aaisd-redshift-mcp__explain_query",
                     "mcp__redshift-mcp-server__execute_query"):
            self.assertTrue(sql.search(tool), tool)
        self.assertFalse(sql.search("mcp__redshift-mcp__list_schemas"))
        frag = json.loads(self.apply("--print", "--with-recipient").stdout)
        handlers = [h for g in frag["hooks"]["PreToolUse"] for h in g["hooks"]]
        mail = re.compile(frag["hooks"]["PreToolUse"][-1]["matcher"])
        for tool in ("email_send", "email_reply", "email_forward", "email_draft", "calendar_meeting"):
            self.assertTrue(mail.search(OUTLOOK + tool), tool)
        self.assertFalse(mail.search(OUTLOOK + "calendar_view"))
        self.assertTrue(any(h["args"][1].endswith("recipient_guard.py") for h in handlers))
        for h in handlers:
            self.assertEqual((h["type"], h["command"], h["args"][0], h["timeout"]),
                             ("command", "sh", str((GUARDS / "run.sh").resolve()), 5))
            self.assertTrue(os.path.isfile(h["args"][1]), h["args"][1])
            self.assertEqual(Path(h["args"][1]).parent, AMAZON.resolve())

    def test_hooks_json_never_registers_them(self):
        text = (GUARDS.parent / "hooks.json").read_text()
        for name in ("sql_guard", "datanet_guard", "recipient_guard", "amazon/"):
            self.assertNotIn(name, text)

    def test_with_recipient_needs_the_allowlist(self):
        sb = Sandbox()
        try:
            cfg = sb.tmp / "cfg"
            env = dict(sb.env, CLAUDE_CONFIG_DIR=str(cfg), CLAUDE_GUARDS_PRIVATE=str(sb.tmp / "none.json"))
            p = self.apply("--with-recipient", "--apply", "--force-worktree", env=env)
            self.assertEqual(p.returncode, 1, p.stderr)
            self.assertIn("not found", p.stderr)
            self.assertIn("private.example.json", p.stderr)
            self.assertFalse(cfg.exists())                                    # nothing written
            p = self.apply("--with-recipient", env=env)                       # a dry run only warns
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("recipient_guard.allow", p.stderr)
            (sb.tmp / "v.json").write_text(json.dumps({"recipient_guard": {"allow": ["me@example.com"]}}))
            env["CLAUDE_GUARDS_PRIVATE"] = str(sb.tmp / "v.json")
            p = self.apply("--with-recipient", env=env)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("1 allowed addresses", p.stderr)
            self.assertIn("recipient_guard.py", p.stdout)                      # merge.py's diff
            self.assertFalse(cfg.exists())
        finally:
            sb.cleanup()

    def test_a_missing_guard_script_does_not_block(self):
        """The registered form, with the script gone (worktree removed, files not
        copied): exit 0, where `python3 <missing.py>` would exit 2 and block."""
        frag = json.loads(self.apply("--print").stdout)
        h = frag["hooks"]["PreToolUse"][0]["hooks"][0]
        sb = Sandbox()
        try:
            gone = str(sb.tmp / "gone" / "sql_guard.py")
            p = subprocess.run([h["command"], h["args"][0], gone], capture_output=True, text=True, env=sb.env,
                               input=json.dumps(pre("mcp__redshift-mcp__execute_query", {"sql": "-- x\nSELECT 1"})))
            self.assertEqual(p.returncode, 0)
            self.assertIn("sql_guard did not run", p.stderr)
            p = subprocess.run([h["command"], *h["args"]], capture_output=True, text=True, env=sb.env,
                               input=json.dumps(pre("mcp__redshift-mcp__execute_query", {"sql": "-- x\nSELECT 1"})))
            self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        finally:
            sb.cleanup()

    def test_apply_refuses_a_linked_worktree(self):
        sb = Sandbox()
        try:
            main = sb.repo("main")
            wt = sb.tmp / "wt"
            sb.git(main, "worktree", "add", "-q", "-b", "side", str(wt))
            (wt / "hooks" / "guards" / "amazon").mkdir(parents=True)
            for rel in ("hooks/guards/amazon/apply.sh", "hooks/guards/run.sh"):
                (wt / rel).write_text((REPO / rel).read_text())
            env = dict(sb.env, CLAUDE_CONFIG_DIR=str(sb.tmp / "cfg"))
            p = subprocess.run(["sh", str(wt / "hooks/guards/amazon/apply.sh"), "--apply"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 1)
            self.assertIn("refusing --apply from a linked worktree", p.stderr)
            self.assertFalse((sb.tmp / "cfg").exists())
        finally:
            sb.cleanup()


def _values_file_problems(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    allow = data.get("recipient_guard", {}).get("allow")
    if not (isinstance(allow, list) and allow and all(isinstance(a, str) and "@" in a for a in allow)):
        problems.append("recipient_guard.allow is not a non-empty list of addresses")
    conf = PS.load_confidential([str(path)])
    problems += conf.problems
    if not (conf.kinds or conf.patterns):
        problems.append("confidential has no literals or patterns")
    return problems


class TestExample(unittest.TestCase):
    """The committed example: valid, placeholders only, and usable as-is."""

    def test_example_is_valid_and_holds_placeholders(self):
        self.assertEqual(_values_file_problems(EXAMPLE), [])
        data = json.loads(EXAMPLE.read_text())
        for a in data["recipient_guard"]["allow"]:
            self.assertRegex(a, r"@example\.(com|org)$")
        conf = PS.load_confidential([str(EXAMPLE)])
        sample = "\n".join(data["confidential"]["literals"]) + "\nmail ann@corp.example.com\nTICKET-1234567\n"
        kinds = {k for _, _, k in PS.findings_in([("s.md", 1, sample)], conf)}
        self.assertEqual(kinds, {"confidential-literal-1", "confidential-literal-2", "confidential-literal-3",
                                 "confidential-literal-4", "confidential-work-email",
                                 "confidential-pattern-3"})

    def test_recipient_guard_reads_the_example_shape(self):
        sb = Sandbox()
        try:
            env = dict(sb.env, CLAUDE_GUARDS_PRIVATE=str(EXAMPLE))
            for to, want in (("you@example.com", None), ("x@example.net", "deny")):
                rc, out, _ = sb.run("recipient_guard.py", pre(OUTLOOK + "email_send", {
                    "to": [to], "subject": "s", "body": "b"}), env=env, script_dir=AMAZON)
                got = json.loads(out)["hookSpecificOutput"]["permissionDecision"] if out else None
                self.assertEqual((rc, got), (0, want), to)
        finally:
            sb.cleanup()


def component_files():
    """Every file of the guards component that git may carry."""
    files = [GUARDS.parent / "hooks.json"]
    files += [p for p in sorted(GUARDS.rglob("*"))
              if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    return files


@unittest.skipUnless(PRIVATE.is_file(), f"{PRIVATE} not in this checkout (the confidential values "
                                        f"are kept out of git); copy it here to run these checks")
class TestPrivateValues(unittest.TestCase):
    """The real values file, and no value from it in the committed files."""

    def setUp(self):
        self.conf = PS.load_confidential([str(PRIVATE)])

    def test_values_file_is_valid(self):
        self.assertEqual(_values_file_problems(PRIVATE), [])
        self.assertEqual(set(json.loads(PRIVATE.read_text())) - {"_readme"},
                         set(json.loads(EXAMPLE.read_text())) - {"_readme"})

    def test_every_allowed_address_counts_as_confidential(self):
        for a in json.loads(PRIVATE.read_text())["recipient_guard"]["allow"]:
            found = [k for _, _, k in PS.findings_in([("x.md", 1, f"to: {a}\n")], self.conf)]
            self.assertTrue(found, "an allowlisted address is not in the confidential check")

    def test_no_confidential_value_in_the_committed_files(self):
        """Values only, never the word: the files may name Amazon tools and hosts'
        domains, but not an account id, an address, a host, a table or a ticket."""
        hits = []
        for f in component_files():
            hits += PS.findings_in([(str(f.relative_to(REPO)), 1, f.read_text(errors="replace"))], self.conf)
        self.assertEqual(hits, [])                       # (path, line, kind): never the value


if __name__ == "__main__":
    unittest.main()
