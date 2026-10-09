"""The gate end to end: real Stop / SessionStart payloads piped into stop_gate.py, run
against a temp git repo with a real scripts/check."""

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from support import CHECK, GATE, INSTALL, Sandbox

SUBAGENT = {"id": "a1", "type": "subagent", "status": "running", "description": "refactor",
            "agent_type": "general-purpose"}
DEV_SERVER = {"id": "t1", "type": "shell", "status": "running", "description": "dev server",
              "command": "npm run dev"}


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


class GateCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.make_repo()

    def tearDown(self):
        self.sb.cleanup()

    def red(self, n=1):
        self.sb.write("verdict.txt", f"BAD {n}\n")

    def green(self):
        self.sb.write("verdict.txt", "GOOD\n")


class TestProtocol(GateCase):
    def test_a_block_is_json_feedback_with_exit_0(self):
        self.red()
        r = self.sb.stop()
        self.assertEqual((r.rc, r.err), (0, ""))  # exit 2 would show a hook error
        hso = r.reply["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "Stop")
        self.assertIn("ERROR tests failed", hso["additionalContext"])
        self.assertNotIn("systemMessage", r.reply)

    def test_never_exits_nonzero(self):
        self.red()
        for args in ((), ("--mode", "bogus"), ("--timeout", "nan")):
            self.assertEqual(self.sb.stop(args=args).rc, 0, args)

    def test_odd_strings_in_the_payload(self):
        self.red()
        for field in ("session_id", "prompt_id", "hook_event_name", "cwd"):
            payload = {"session_id": "s1", "cwd": str(self.sb.repo), "prompt_id": "pz",
                       "hook_event_name": "Stop"}
            payload[field] = "\udcff" + payload[field]
            raw = json.dumps(payload)  # ensure_ascii: a lone surrogate escape, valid JSON
            r = self.sb.run_gate(None, raw=raw)
            self.assertEqual(r.rc, 0, (field, r.err))
            if field in ("session_id", "prompt_id"):
                self.assertEqual(r[0], 2, field)  # the decision survives
        self.assertIn("\\udcff", self.sb.log())


class TestAllowWithoutRunning(GateCase):
    def test_no_git_repo(self):
        plain = self.sb.tmp / "plain"
        plain.mkdir()
        r = self.sb.stop(cwd=plain, env={"CLAUDE_PROJECT_DIR": str(plain)})
        self.assertEqual(r, (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)

    def test_no_check_command(self):
        (self.sb.repo / "scripts" / "check").unlink()
        self.red()
        self.assertEqual(self.sb.stop(), (0, "", ""))

    def test_stop_gate_0_disables(self):
        self.red()
        for value in ("0", "off"):
            self.assertEqual(self.sb.stop(env={"STOP_GATE": value}), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)
        self.assertIn("Stop session=s1 disabled allow", self.sb.log())

    def test_not_a_stop_event(self):
        self.red()
        for event in ("SubagentStop", "PostToolUse"):
            self.assertEqual(self.sb.stop(event=event), (0, "", ""))
        self.assertEqual(self.sb.stop(mode="plan")[0], 0)
        self.assertEqual(self.sb.run_count(), 0)

    def test_garbage_input_never_blocks(self):
        self.red()
        for raw in ("", "not json", "[1, 2]", "null", '"str"'):
            self.assertEqual(self.sb.run_gate(None, raw=raw), (0, "", ""), raw)
        self.assertEqual(self.sb.run_count(), 0)

    def test_bad_arguments_fail_open(self):
        self.red()
        r = self.sb.stop(args=["--mode", "bogus"])
        self.assertEqual(r[0], 0)  # argparse would have exited 2, i.e. "block"
        self.assertIn("internal error", r.message)


class TestBackground(GateCase):
    def test_a_long_running_shell_task_does_not_hold_the_gate(self):
        self.red()
        for prompt in ("p1", "p2"):  # a dev server never ends: every stop is checked
            r = self.sb.stop(prompt=prompt, background=[DEV_SERVER])
            self.assertEqual(r[0], 2 if prompt == "p1" else 0, prompt)
        self.assertEqual(self.sb.run_count(), 1)  # p2: the still-red notice, no re-run
        monitor = {"id": "m1", "type": "monitor", "status": "running", "server": "x"}
        self.red(2)
        self.assertEqual(self.sb.stop(prompt="p3", background=[monitor])[0], 2)

    def test_a_running_subagent_defers_once(self):
        self.red()
        r = self.sb.stop(background=[SUBAGENT])
        self.assertEqual(r[0], 0)
        self.assertIn("1 background task (subagent) still running", r.message)
        self.assertEqual(self.sb.run_count(), 0)
        r = self.sb.stop(active=False, background=[SUBAGENT])  # stopped again, same tree
        self.assertEqual(r[0], 2)
        self.red(2)  # the same agent changed the tree: checked, not waited for again
        self.assertEqual(self.sb.stop(prompt="p2", background=[SUBAGENT])[0], 2)
        self.red(3)
        self.assertEqual(self.sb.stop(prompt="p3")[0], 2)  # the agent finished: checked
        self.assertEqual(self.sb.run_count(), 3)

    def test_a_long_running_workflow_gets_one_wait(self):
        workflow = {"id": "w1", "type": "workflow", "status": "running", "name": "review"}
        self.sb.session_start()
        codes = []
        for n, prompt in enumerate(("p1", "p2", "p3"), 1):
            self.red(n)
            codes.append(self.sb.stop(prompt=prompt, background=[workflow])[0])
        self.assertEqual(codes, [0, 2, 2])  # each red edit is checked in its own turn
        r = self.sb.stop(prompt="p4", background=[workflow])  # a question, nothing changed
        self.assertEqual(r[0], 0)
        self.assertIn("unchanged since a failing check", r.message)
        self.assertEqual(self.sb.run_count(), 2)

    def test_a_new_agent_gets_its_own_wait_but_only_once_per_prompt(self):
        second = dict(SUBAGENT, id="a2")
        self.red(1)
        self.assertEqual(self.sb.stop(prompt="p1", background=[SUBAGENT])[0], 0)
        self.red(2)  # same prompt (another Stop hook blocked), a new agent: no second wait
        self.assertEqual(self.sb.stop(prompt="p1", active=True, background=[second])[0], 2)
        self.red(3)
        self.assertEqual(self.sb.stop(prompt="p2", background=[dict(SUBAGENT, id="a3")])[0], 0)

    def test_unchanged_tree_with_an_agent_running_is_silent(self):
        self.assertEqual(self.sb.stop(), (0, "", ""))
        self.assertEqual(self.sb.stop(prompt="p2", background=[SUBAGENT]), (0, "", ""))

    def test_finished_tasks_do_not_count(self):
        self.red()
        self.assertEqual(self.sb.stop(background=[dict(SUBAGENT, status="completed")])[0], 2)

    def test_a_block_while_agents_run_says_so_and_comes_once_per_prompt(self):
        workflow = {"id": "w1", "type": "workflow", "status": "running", "name": "build"}
        self.assertEqual(self.sb.stop(prompt="p1", background=[SUBAGENT])[0], 0)  # the wait
        self.red(1)  # the agents are mid-edit when a wake-up from another task arrives
        r = self.sb.stop(prompt="wake1", background=[SUBAGENT, workflow])
        self.assertEqual(r[0], 0)  # the workflow's own one wait
        self.red(2)
        r = self.sb.stop(prompt="wake2", background=[SUBAGENT, workflow])
        self.assertEqual(r[0], 2)
        self.assertIn("2 background agents (subagent, workflow) are still running and may be "
                      "mid-edit. If the failure is in files they are changing, wait instead "
                      "of editing", r.reason)
        self.red(3)  # still mid-edit after Claude said it waits
        r = self.sb.stop(prompt="wake2", active=True, background=[SUBAGENT, workflow])
        self.assertEqual(r[0], 0)
        self.assertIn("checked again once no agent runs", r.message)
        r = self.sb.stop(prompt="wake3")  # the last agent finished, the tree as it left it
        self.assertEqual(r[0], 2)
        self.assertNotIn("background agent", r.reason)
        self.assertEqual(self.sb.run_count(), 3)


class TestGreen(GateCase):
    def test_green_runs_once_then_cached(self):
        self.sb.write("src/new.py", "y = 2\n")  # untracked change
        self.assertEqual(self.sb.stop(), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 1)
        for prompt in ("p1", "p2", "p3"):  # Q&A turns on the same tree cost nothing
            self.assertEqual(self.sb.stop(prompt=prompt)[0], 0)
        self.assertEqual(self.sb.run_count(), 1)
        self.sb.write("src/new.py", "y = 3\n")
        self.assertEqual(self.sb.stop()[0], 0)
        self.assertEqual(self.sb.run_count(), 2)

    def test_a_commit_does_not_change_the_hash(self):
        self.sb.write("src/app.py", "x = 2\n")
        self.sb.write("src/new.py", "y = 2\n")
        self.sb.stop()
        self.sb.git("add", ".")
        self.sb.git("commit", "-qm", "change")
        self.sb.stop()
        self.sb.git("commit", "-q", "--allow-empty", "-m", "empty")
        self.sb.stop()
        self.assertEqual(self.sb.run_count(), 1)

    def test_touching_files_does_not_change_the_hash(self):
        self.sb.stop()
        t = time.time() + 50
        for rel in ("verdict.txt", "src/app.py"):
            os.utime(self.sb.repo / rel, (t, t))
        self.sb.write("src/new.py", "y\n")
        self.sb.stop()
        os.utime(self.sb.repo / "src" / "new.py", (t, t))
        self.sb.stop()
        self.assertEqual(self.sb.run_count(), 2)

    def test_files_the_check_writes_do_not_retrigger_it(self):
        artifact = 'mkdir -p __pycache__; date +%s%N > __pycache__/run.$$\n'
        self.sb.write("scripts/check", CHECK.replace('echo run >> "$RUNS"\n',
                                                     'echo run >> "$RUNS"\n' + artifact), mode=0o755)
        self.sb.write("src/app.py", "x = 3\n")
        self.assertEqual(self.sb.stop()[0], 0)
        self.assertEqual(self.sb.stop()[0], 0)
        self.assertEqual(self.sb.run_count(), 1)
        self.red()
        self.assertEqual(self.sb.stop()[0], 2)
        r = self.sb.stop(active=True)  # only the check's own output changed
        self.assertEqual(r[0], 0)
        self.assertIn("nothing changed since the last failing check", r.message)
        self.assertEqual(self.sb.run_count(), 2)

    def test_watch_limits_what_counts(self):
        args = ["--watch", "src"]
        self.sb.stop(args=args)
        self.sb.write("README.md", "docs\n")
        self.sb.stop(args=args)
        self.assertEqual(self.sb.run_count(), 1)
        self.sb.write("src/app.py", "x = 5\n")
        self.sb.stop(args=args)
        self.assertEqual(self.sb.run_count(), 2)


class TestRed(GateCase):
    def test_red_blocks_with_a_short_tail(self):
        self.red()
        r = self.sb.stop()
        self.assertEqual(r[0], 2)
        self.assertIn("`./scripts/check --fast` fails on the current tree (failure 1", r.reason)
        tail = r.reason.split("\n\n", 1)[1].strip().splitlines()
        self.assertLessEqual(len(tail), 21)  # the contract's 20 summary lines + its last line
        self.assertEqual(tail[-1], "ERROR tests failed (exit 1)")
        self.assertIn("FAILED tests/test_x.py::test_verdict", tail)

    def test_no_progress_in_the_same_prompt_gives_up(self):
        self.red()
        self.assertEqual(self.sb.stop()[0], 2)
        r = self.sb.stop(active=True)
        self.assertEqual(r[0], 0)
        self.assertIn("nothing changed since the last failing check", r.message)
        self.assertEqual(self.sb.run_count(), 1)

    def test_gives_up_after_three_failures_in_a_prompt(self):
        codes = []
        for n in (1, 2, 3):
            self.red(n)
            r = self.sb.stop(active=n > 1)
            codes.append(r[0])
        self.assertEqual(codes, [2, 2, 0])
        self.assertIn("still failing after 3 attempts", r.message)
        self.assertIn("ERROR tests failed", r.message)
        self.red(4)  # a new prompt starts a new count
        self.assertEqual(self.sb.stop(prompt="p2")[0], 2)

    def test_max_failures_is_configurable(self):
        args = ["--max-failures", "2"]
        self.red(1)
        self.assertEqual(self.sb.stop(args=args)[0], 2)
        self.red(2)
        r = self.sb.stop(args=args, active=True)
        self.assertEqual(r[0], 0)
        self.assertIn("after 2 attempts", r.message)
        r = self.sb.stop(args=["--max-failures", "1"], prompt="p2")  # would never block
        self.assertEqual(r[0], 0)
        self.assertIn("must be 2 or more", r.message)

    def test_green_resets_the_count(self):
        codes = []
        for verdict in ("BAD 1", "BAD 2", "GOOD", "BAD 3", "BAD 4"):
            self.sb.write("verdict.txt", verdict + "\n")
            codes.append(self.sb.stop(active=True)[0])
        self.assertEqual(codes, [2, 2, 0, 2, 2])

    def test_unchanged_red_tree_in_a_later_prompt_warns_once(self):
        self.red()
        self.assertEqual(self.sb.stop(prompt="p1")[0], 2)
        r = self.sb.stop(prompt="p2")
        self.assertEqual(r[0], 0)
        self.assertIn("unchanged since a failing check", r.message)
        self.assertEqual(self.sb.stop(prompt="p3"), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 1)

    def test_without_prompt_id_stop_hook_active_marks_the_same_prompt(self):
        self.red(1)
        self.assertEqual(self.sb.stop(prompt=None, active=False)[0], 2)
        self.red(2)
        self.assertEqual(self.sb.stop(prompt=None, active=True)[0], 2)
        self.red(3)
        self.assertEqual(self.sb.stop(prompt=None, active=True)[0], 0)  # third failure
        self.red(4)
        self.assertEqual(self.sb.stop(prompt=None, active=False)[0], 2)  # next prompt

    def test_sessions_are_independent(self):
        self.red()
        self.assertEqual(self.sb.stop(session="a")[0], 2)
        self.assertEqual(self.sb.stop(session="b")[0], 2)

    def test_a_dirty_submodule_edit_after_a_green_one_is_seen(self):
        lib = self.sb.make_repo(self.sb.tmp / "libsrc", check=None)
        self.sb.write("scripts/check", CHECK.replace("verdict.txt", "lib/verdict.txt"),
                      mode=0o755)
        self.sb.git("submodule", "add", "-q", str(lib), "lib")
        self.sb.git("add", ".")
        self.sb.git("commit", "-qm", "add lib", old=True)
        self.sb.session_start()
        self.sb.write("lib/src/app.py", "x = 2\n")
        self.assertEqual(self.sb.stop(prompt="p1")[0], 0)
        self.sb.write("lib/verdict.txt", "BAD\n")
        self.assertEqual(self.sb.stop(prompt="p2")[0], 2)
        self.assertEqual(self.sb.run_count(), 2)


class TestBaseline(GateCase):
    def test_inherited_red_tree_is_not_claudes(self):
        self.red()  # the user's uncommitted, failing work
        self.assertEqual(self.sb.session_start(), (0, "", ""))
        self.assertEqual(self.sb.stop(), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)
        self.sb.write("src/app.py", "x = 9\n")  # Claude changes something: gated again
        r = self.sb.stop()
        self.assertEqual(r[0], 2)
        self.assertIn("this work tree has changes you didn't make", r.reason)

    def test_committing_the_inherited_work_is_not_a_change(self):
        self.red()
        self.sb.session_start()
        self.sb.git("commit", "-qam", "wip")  # "please commit my work"
        self.assertEqual(self.sb.stop(), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)

    def test_checking_out_an_existing_red_branch(self):
        self.sb.git("checkout", "-qb", "feature")
        self.red()
        self.sb.git("commit", "-qam", "teammate's red commit", old=True)
        self.sb.git("checkout", "-q", "trunk")
        self.sb.session_start()
        self.sb.git("checkout", "-q", "feature")  # Claude only reads the branch
        self.assertEqual(self.sb.stop(), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)
        self.assertIn("existing-work", self.sb.log())
        self.sb.write("src/app.py", "x = 8\n")  # an edit on top of it is gated
        r = self.sb.stop(prompt="p2")
        self.assertEqual(r[0], 2)
        self.assertNotIn("you didn't make", r.reason)

    def test_pulling_a_red_upstream(self):
        up = self.sb.make_repo(self.sb.tmp / "upstream")
        self.sb.git("remote", "add", "origin", str(up))
        self.sb.git("fetch", "-q", "origin")
        self.sb.git("reset", "-q", "--hard", "origin/trunk")
        self.sb.write("verdict.txt", "BAD upstream\n", root=up)
        self.sb.git("commit", "-qam", "upstream red", cwd=up, old=True)
        self.sb.session_start()
        self.sb.git("pull", "-q", "--ff-only", "origin", "trunk")
        self.assertEqual(self.sb.stop(), (0, "", ""))

    def test_claudes_own_red_commit_is_gated(self):
        self.sb.session_start()
        self.red()
        self.sb.git("commit", "-qam", "red work")  # edit + commit in one turn
        self.assertEqual(self.sb.stop()[0], 2)

    def test_a_new_untracked_file_rules_out_the_checkout_shortcut(self):
        self.sb.git("checkout", "-qb", "feature")
        self.red()
        self.sb.git("commit", "-qam", "red", old=True)
        self.sb.git("checkout", "-q", "trunk")
        self.sb.session_start()
        self.sb.git("checkout", "-q", "feature")
        self.sb.write("src/brand_new.py", "z = 1\n")
        self.assertEqual(self.sb.stop()[0], 2)

    def test_compact_keeps_the_baseline(self):
        self.red(1)
        self.sb.session_start("startup")
        self.red(2)
        self.sb.session_start("compact")
        self.assertEqual(self.sb.stop()[0], 2)
        self.sb.session_start("resume")  # what the session judged stands
        r = self.sb.stop(prompt="p2")
        self.assertEqual(r[0], 0)
        self.assertIn("unchanged since a failing check in an earlier prompt", r.message)
        self.assertEqual(self.sb.run_count(), 1)

    def test_stash_and_pop_of_the_users_work(self):
        self.red()
        self.sb.session_start()
        self.sb.shift_start(5)
        self.sb.git("stash", "-q")
        self.assertEqual(self.sb.stop(prompt="p1"), (0, "", ""))  # a clean old commit
        self.sb.git("stash", "pop", "-q")  # rewritten now, but the start tree again
        self.assertEqual(self.sb.stop(prompt="p2"), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)


class TestResumeAndFork(GateCase):
    """A turn cut off before its Stop (Esc, a crash, a killed run) and then resumed, and a
    conversation copied into a new session (/fork, /branch, /background, --fork-session)."""

    def claudes_cut_off_red_edit(self, to=None):
        self.sb.said(self.sb.user("p1", "rename the helper"), to=to)
        self.sb.said(*self.sb.tool("Edit", "p1", file_path=str(self.sb.repo / "verdict.txt")),
                     to=to)
        self.red()  # Claude's edit; the turn never reaches its Stop

    def question(self, prompt, to=None):
        self.sb.said(self.sb.user(prompt, "status?"), to=to)
        self.sb.said(*self.sb.tool("Read", prompt, file_path=str(self.sb.repo / "verdict.txt")),
                     to=to)
        self.sb.said(self.sb.text("All done."), to=to)

    def test_resume_after_a_cut_off_turn_checks_its_edits(self):
        self.sb.session_start()
        self.sb.shift_start(-5)
        start = json.loads(self.sb.state_path().read_text())["start"]
        self.claudes_cut_off_red_edit()
        self.assertEqual(self.sb.session_start("resume"), (0, "", ""))
        self.assertIn("SessionStart session=s1 resumed allow", self.sb.log())
        self.assertEqual(json.loads(self.sb.state_path().read_text())["start"], start)
        self.question("p2")
        r = self.sb.stop(prompt="p2", final="All done.")
        self.assertEqual(r[0], 2)
        self.assertEqual(self.sb.run_count(), 1)

    def test_resume_in_another_work_tree_keeps_the_start_time(self):
        self.sb.session_start()
        self.sb.shift_start(-5)
        self.claudes_cut_off_red_edit()
        wt = self.sb.tmp / "wt"
        self.sb.git("worktree", "add", "-q", "-b", "side", str(wt))
        time.sleep(1.1)  # resumed later: the edit is over a second older than a new start
        self.sb.session_start("resume", cwd=wt)  # a tree this session hasn't judged: a baseline
        self.question("p2")
        r = self.sb.stop(prompt="p2", final="All done.")  # back in the first tree
        self.assertEqual(r[0], 2)  # with a new start time, the edit would look old

    def test_resume_keeps_the_users_baseline(self):
        self.red()  # the user's WIP, the session's start tree
        self.sb.session_start()
        self.sb.session_start("resume")
        self.question("p1")
        self.assertEqual(self.sb.stop(final="All done."), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)

    def test_resume_of_a_session_the_gate_never_saw_starts_from_its_tree(self):
        self.red()
        self.sb.session_start("resume")  # no state: e.g. the gate was installed since
        self.assertEqual(self.sb.stop(), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)

    def test_a_fork_checks_claudes_edits_from_before_the_copy(self):
        self.sb.session_start()
        self.claudes_cut_off_red_edit()
        copy = self.sb.tmp / "copy.jsonl"  # SessionStart runs before the copy is written
        self.assertEqual(self.sb.session_start("fork", session="s2", transcript=copy),
                         (0, "", ""))
        shutil.copy(str(self.sb.transcript), str(copy))
        self.question("p2", to=copy)
        r = self.sb.stop(prompt="p2", session="s2", transcript=copy, final="All done.")
        self.assertEqual(r[0], 2)
        self.assertIn("SessionStart session=s2 fork allow", self.sb.log())

    def test_a_copy_written_before_session_start_is_read_from_its_top(self):  # /branch
        self.sb.session_start()
        self.claudes_cut_off_red_edit()
        copy = self.sb.tmp / "branch.jsonl"
        shutil.copy(str(self.sb.transcript), str(copy))
        self.sb.session_start("fork", session="s2", transcript=copy)
        self.question("p2", to=copy)
        self.assertEqual(self.sb.stop(prompt="p2", session="s2", transcript=copy,
                                      final="All done.")[0], 2)

    def test_a_fork_has_no_start_time_so_old_looking_edits_are_not_adopted(self):
        self.sb.session_start()
        self.red()
        copy = self.sb.tmp / "copy.jsonl"
        self.sb.session_start("fork", session="s2", transcript=copy)
        self.assertNotIn("start", json.loads(self.sb.state_path("s2").read_text()))
        self.sb.said(self.sb.user("p2"), *self.sb.tool("Bash", "p2", command="ls"),
                     self.sb.text("ok"), to=copy)
        self.assertEqual(self.sb.stop(prompt="p2", session="s2", transcript=copy,
                                      final="ok")[0], 2)

    def test_a_fork_of_a_conversation_that_only_read_is_not_checked(self):
        self.red()  # the user's WIP
        self.sb.session_start()
        self.question("p1")
        self.sb.stop(final="All done.")
        copy = self.sb.tmp / "copy.jsonl"
        self.sb.session_start("fork", session="s2", transcript=copy)
        shutil.copy(str(self.sb.transcript), str(copy))
        self.question("p2", to=copy)
        self.assertEqual(self.sb.stop(prompt="p2", session="s2", transcript=copy,
                                      final="All done."), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)


class TestWorktrees(GateCase):
    """A work tree first seen mid-session: EnterWorktree, a shepherd track, git worktree add."""

    def red_branch(self, name="track"):
        self.sb.git("checkout", "-qb", name)
        self.red()
        self.sb.git("commit", "-qam", "teammate's red work", old=True)
        self.sb.git("checkout", "-q", "trunk")

    def test_a_fresh_worktree_of_an_old_red_branch_is_not_checked(self):
        self.red_branch()
        self.sb.session_start()
        wt = self.sb.repo / ".claude" / "worktrees" / "t"
        self.sb.git("worktree", "add", "-q", str(wt), "track")
        self.assertEqual(self.sb.stop(prompt="p1"), (0, "", ""))  # main: the worktree isn't in it
        self.assertEqual(self.sb.stop(prompt="p2", cwd=wt), (0, "", ""))  # read only
        self.assertEqual(self.sb.run_count(), 0)
        self.assertIn("existing-work", self.sb.log())
        self.sb.write("src/app.py", "x = 3\n", root=wt)  # Claude's edit there is gated
        r = self.sb.stop(prompt="p3", cwd=wt)
        self.assertEqual(r[0], 2)
        self.assertNotIn("you didn't make", r.reason)
        self.assertEqual(self.sb.stop(prompt="p4"), (0, "", ""))  # main is still clean

    def test_a_green_fresh_worktree_costs_no_check_and_a_new_file_does(self):
        self.sb.session_start()
        wt = self.sb.tmp / "wt"
        self.sb.git("worktree", "add", "-q", "-b", "side", str(wt))
        self.assertEqual(self.sb.stop(cwd=wt), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)
        self.sb.write("src/feature.py", "y = 1\n", root=wt)  # new, untracked: Claude's work
        self.assertEqual(self.sb.stop(prompt="p2", cwd=wt)[0], 0)
        self.assertEqual(self.sb.run_count(), 1)

    def test_old_work_in_an_existing_worktree_is_not_claudes(self):
        wt = self.sb.tmp / "wt"
        self.sb.git("worktree", "add", "-q", "-b", "side", str(wt))
        self.sb.write("verdict.txt", "BAD wip\n", root=wt)  # another session's red WIP
        self.sb.session_start()
        self.sb.shift_start(5)  # ... from before this session
        self.assertEqual(self.sb.stop(cwd=wt), (0, "", ""))
        self.sb.shift_start(-5)  # back to now, so the next write is new
        self.sb.write("src/app.py", "x = 4\n", root=wt)
        r = self.sb.stop(prompt="p2", cwd=wt)
        self.assertEqual(r[0], 2)
        self.assertIn("changes you didn't make", r.reason)


class TestSubproject(GateCase):
    """install_gate.py <repo>/services/api: Claude Code started there, in a monorepo."""

    def setUp(self):
        super().setUp()
        self.sub = self.sb.repo / "services" / "api"
        self.sb.write("services/api/scripts/check", CHECK, mode=0o755)
        self.sb.write("services/api/verdict.txt", "GOOD\n")
        self.sb.git("add", ".")
        self.sb.git("commit", "-qm", "api", old=True)
        self.env = {"CLAUDE_PROJECT_DIR": str(self.sub)}

    def test_the_subprojects_own_check_runs(self):
        self.sb.session_start(env=self.env, cwd=self.sub)
        self.sb.write("services/api/verdict.txt", "BAD\n")  # the top-level check stays green
        r = self.sb.stop(cwd=self.sub, env=self.env)
        self.assertEqual(r[0], 2)
        self.assertIn("`services/api/scripts/check --fast` fails", r.reason)
        self.assertIn("sub=services/api", self.sb.log())

    def test_a_change_anywhere_in_the_repo_counts(self):
        self.sb.session_start(env=self.env, cwd=self.sub)
        self.sb.write("lib/shared.py", "z = 1\n")
        self.assertEqual(self.sb.stop(cwd=self.sub, env=self.env)[0], 0)
        self.assertEqual(self.sb.run_count(), 1)

    def test_watch_paths_are_relative_to_the_subproject(self):
        args = ["--watch", "src"]
        self.sb.session_start(env=self.env, cwd=self.sub, args=args)
        self.sb.write("src/app.py", "x = 7\n")  # the repo's src/, not the subproject's
        self.assertEqual(self.sb.stop(cwd=self.sub, env=self.env, args=args), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)
        self.sb.write("services/api/src/handler.py", "h = 1\n")
        self.sb.stop(cwd=self.sub, env=self.env, args=args, prompt="p2")
        self.assertEqual(self.sb.run_count(), 1)


class TestNotClaudes(GateCase):
    """Files written by something else while Claude only reads: the transcript shows no tool
    call that can write since the last judged stop, so the check doesn't run."""

    def setUp(self):
        super().setUp()
        self.red()  # the user's uncommitted, failing work
        self.sb.session_start()

    def turn(self, prompt, *entries, final, at=None):
        self.sb.said(self.sb.user(prompt), at=at)  # `at`: when the user sent the prompt
        self.sb.said(*entries)
        if final is not None:
            self.sb.said(self.sb.text(final))

    def test_question_turns_while_a_dev_server_writes_its_log(self):
        for i, prompt in enumerate(("q1", "q2", "q3")):
            self.sb.write("dev.log", "GET /\n" * (i + 1))
            self.turn(prompt, *self.sb.tool("Read", prompt, file_path="x"), final=f"answer {i}")
            r = self.sb.stop(prompt=prompt, background=[DEV_SERVER], final=f"answer {i}")
            self.assertEqual(r, (0, "", ""), prompt)
        self.assertEqual(self.sb.run_count(), 0)
        self.assertEqual(self.sb.log().count("not-claudes allow"), 3)

    def test_a_turn_that_ran_bash_blocks_once(self):
        self.sb.write("dev.log", "GET /\n")
        self.turn("q1", *self.sb.tool("Bash", "q1", command="git log"), final=None)
        r = self.sb.stop(prompt="q1", background=[DEV_SERVER], final="here is the log")
        self.assertEqual(r[0], 2)
        self.assertIn("changes you didn't make", r.reason)
        self.sb.write("dev.log", "GET /\nGET /\n")
        self.sb.said(self.sb.text("here is the log"),  # the last turn's reply, written late
                     self.sb.text("The failure predates my work."))
        r = self.sb.stop(prompt="q1", active=True, background=[DEV_SERVER],
                         final="The failure predates my work.")
        self.assertEqual(r, (0, "", ""))
        self.assertEqual(self.sb.run_count(), 1)

    def test_a_background_job_that_changes_a_tracked_file_is_checked(self):
        self.sb.write("src/app.py", "x = 'generated'\n")
        self.turn("q1", final="ok")
        self.assertEqual(self.sb.stop(prompt="q1", background=[DEV_SERVER], final="ok")[0], 2)

    def test_the_users_own_edit_between_prompts(self):
        self.sb.write("src/app.py", "x = 'edited in the editor'\n")
        self.turn("q1", final="ok")
        self.assertEqual(self.sb.stop(prompt="q1", final="ok"), (0, "", ""))

    def test_a_running_agent_rules_it_out(self):
        self.sb.write("dev.log", "x\n")
        self.turn("q1", final="ok")
        self.sb.stop(prompt="q1", background=[SUBAGENT], final="ok")
        self.assertNotIn("not-claudes", self.sb.log())

    def test_edits_before_a_deferred_stop_are_still_seen(self):
        self.sb.write("src/app.py", "x = 'claude'\n")
        self.turn("q1", *self.sb.tool("Edit", "q1", file_path="src/app.py"),
                  *self.sb.tool("Agent", "q1"), final="started a helper")
        self.assertEqual(self.sb.stop(prompt="q1", background=[SUBAGENT],
                                      final="started a helper")[0], 0)  # waited for it
        self.turn("q2", final="the helper is done")
        self.assertEqual(self.sb.stop(prompt="q2", final="the helper is done")[0], 2)

    def test_the_final_message_is_awaited(self):
        self.sb.write("dev.log", "x\n")
        self.turn("q1", final=None)
        late = threading.Timer(0.3, self.sb.said, [self.sb.text("late answer")])
        late.start()
        r = self.sb.stop(prompt="q1", final="late answer")
        late.join()
        self.assertEqual(r, (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)

    def test_without_the_final_message_the_check_runs(self):
        self.sb.write("dev.log", "x\n")
        self.turn("q1", final=None)
        t0 = time.monotonic()
        self.assertEqual(self.sb.stop(prompt="q1", final="never written")[0], 2)
        self.assertGreaterEqual(time.monotonic() - t0, 0.9)  # waited a second first

    def test_without_a_transcript_the_check_runs(self):  # e.g. --no-session-persistence
        self.sb.write("dev.log", "x\n")
        self.assertEqual(self.sb.stop(prompt="q1", final="ok")[0], 2)

    def changed_at(self, rel, text):
        """Write rel and return its ctime: when the gate dates the change. File times lag the
        clock by up to a kernel tick, so a save within one of the last stop's end looks like
        it came during that stop; a user can't save that fast."""
        time.sleep(0.05)
        return os.lstat(self.sb.write(rel, text)).st_ctime

    def test_the_users_edit_between_prompts_on_a_turn_that_ran_bash(self):
        # On Linux and macOS, Claude searches with grep/find through Bash, so a question turn
        # often has a Bash call. The edit came before the prompt that led to it.
        t = self.changed_at("src/app.py", "x = 'edited in the editor'\n")
        self.turn("q1", *self.sb.tool("Bash", "q1", command="grep -rn x src"),
                  final="It sets x.", at=t + 1.5)
        r = self.sb.stop(prompt="q1", final="It sets x.")
        self.assertEqual(r, (0, "", ""), r.reason.split("\n\n")[0][-420:] + "\n" + self.sb.log())
        self.assertIn("not-claudes allow", self.sb.log())
        self.assertIn("by=time", self.sb.log())
        self.assertEqual(self.sb.run_count(), 0)

    def test_a_change_made_after_claudes_turn_began_is_checked(self):
        self.sb.said(self.sb.user("q1"), at=time.time())
        self.changed_at("src/app.py", "x = 'by a shell command'\n")  # after the prompt
        self.sb.said(*self.sb.tool("Bash", "q1", command="sed -i ..."), self.sb.text("done"))
        r = self.sb.stop(prompt="q1", final="done")
        self.assertEqual(r[0], 2)
        self.assertIn("Changed since the last check: src/app.py.", r.reason)
        self.assertIn("None of them went through your Edit or Write calls", r.reason)
        self.assertNotIn("Not yours", r.reason)

    def test_the_block_names_what_changed_before_claudes_first_call(self):
        t = self.changed_at("verdict.txt", "BAD from the user\n")
        while time.time() < t + 0.6:  # Claude's write must come after (prompt time - 1 s)
            time.sleep(0.05)
        self.sb.said(self.sb.user("q1"), at=t + 1.55)
        app = self.sb.write("src/app.py", "x = 'claude'\n")
        self.sb.said(*self.sb.tool("Edit", "q1", file_path=str(app)), self.sb.text("done"))
        r = self.sb.stop(prompt="q1", final="done")
        self.assertEqual(r[0], 2)
        self.assertIn("Changed since the last check: src/app.py, verdict.txt.", r.reason)
        self.assertIn("Not yours, as they changed before your first tool call since then: "
                      "verdict.txt.", r.reason)
        self.assertNotIn("None of them went through", r.reason)

    def test_an_old_tracked_change_with_a_background_job_running_is_checked(self):
        t = self.changed_at("src/app.py", "x = 'generated'\n")  # maybe by the job Claude started
        self.turn("q1", *self.sb.tool("Bash", "q1", command="ls"), final="ok", at=t + 1.5)
        self.assertEqual(self.sb.stop(prompt="q1", background=[DEV_SERVER], final="ok")[0], 2)


class TestBackgroundJobs(GateCase):
    """A job Claude starts with run_in_background (or Ctrl+B) writes a new file after the turn
    that started it has ended; Claude Code wakes Claude when it finishes. What the job wrote
    is Claude's work, though no tool call in the wake-up turn wrote it."""
    CHECK = CHECK.replace('echo run >> "$RUNS"\n', 'echo run >> "$RUNS"\n'
                          'if [ -e gen.py ]; then echo "ERROR gen.py does not compile"; exit 1; fi\n')
    JOB = {"id": "b7", "type": "shell", "status": "running", "description": "codegen",
           "command": "python3 tools/gen.py"}

    def setUp(self):
        super().setUp()
        self.sb.write("scripts/check", self.CHECK, mode=0o755)
        self.sb.git("commit", "-qam", "codegen check", old=True)
        self.red()  # the user's WIP, so a tree taken as someone else's would pass silently
        self.sb.session_start()
        self.sb.shift_start(-5)
        self.sb.said(self.sb.user("p1", "generate the client"), at=time.time())
        self.sb.said(*self.sb.tool("Bash", "p1", command="python3 tools/gen.py",
                                   run_in_background=True))
        self.sb.said(self.sb.text("Started it."))
        self.assertEqual(self.sb.stop(prompt="p1", background=[self.JOB], final="Started it."),
                         (0, "", ""))  # the start tree: nothing to judge yet
        time.sleep(0.05)

    def wake_up(self, tool, prompt="p2", **inputs):
        self.sb.said(self.sb.user(prompt, "<task-notification><task-id>b7</task-id>"
                                          "<status>completed</status></task-notification>"),
                     at=time.time())
        self.sb.said(*self.sb.tool(tool, prompt, **inputs), self.sb.text("It wrote gen.py."))
        return self.sb.stop(prompt=prompt, final="It wrote gen.py.")

    def test_its_output_is_checked_at_the_wake_up(self):
        self.sb.write("gen.py", "def broken(\n")
        r = self.wake_up("TaskOutput", task_id="b7")
        self.assertEqual(r[0], 2)
        self.assertIn("ERROR gen.py does not compile", r.reason)
        self.assertIn("A background job you started finished since then "
                      "(`python3 tools/gen.py`): what it wrote is your work.", r.reason)
        self.assertNotIn("someone else's work in progress", r.reason)
        self.assertIn("jobs_ended=1", self.sb.log())

    def test_also_when_the_wake_up_runs_bash_well_after_the_write(self):
        self.sb.write("gen.py", "def broken(\n")
        time.sleep(1.2)  # the time rule alone would take it as written before the prompt
        r = self.wake_up("Bash", command="tail -5 gen.log")
        self.assertEqual(r[0], 2)
        self.assertNotIn("Not yours", r.reason)

    def test_a_tree_taken_while_it_ran_is_checked_once_it_ends(self):
        self.sb.write("gen.py", "def broken(\n")
        self.sb.said(self.sb.user("q1", "is it done?"), *self.sb.tool("Read", "q1"),
                     self.sb.text("Still running."))
        self.assertEqual(self.sb.stop(prompt="q1", background=[self.JOB],
                                      final="Still running."), (0, "", ""))
        self.assertIn("not-claudes allow", self.sb.log())  # a dev server's log looks the same
        r = self.wake_up("TaskOutput", prompt="p3", task_id="b7")
        self.assertEqual(r[0], 2)
        self.assertEqual(self.sb.run_count(), 1)

    def test_a_job_that_only_left_caches_changes_nothing(self):
        self.sb.write("src/__pycache__/app.cpython-39.pyc", "cache")
        self.assertEqual(self.wake_up("TaskOutput", task_id="b7"), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)
        self.assertIn("by=transcript", self.sb.log())

    def test_a_job_still_running_keeps_the_dev_server_tolerance(self):
        self.sb.write("dev.log", "GET /\n")
        self.sb.said(self.sb.user("q1"), *self.sb.tool("Read", "q1"), self.sb.text("ok"))
        self.assertEqual(self.sb.stop(prompt="q1", background=[self.JOB], final="ok"),
                         (0, "", ""))
        self.assertEqual(self.sb.run_count(), 0)


class TestCheckOutputs(GateCase):
    """A passing check that writes a file it doesn't gitignore (pytest --junitxml, coverage
    xml, a test-results/ dir): the tree it leaves behind is unjudged, so the next stop on it
    runs the check again, which writes the file again."""

    def check_writing(self, line):
        self.sb.write("scripts/check", CHECK.replace('echo run >> "$RUNS"\n',
                                                     f'echo run >> "$RUNS"\n{line}\n'), mode=0o755)
        self.sb.git("commit", "-qam", "check with a report", old=True)
        self.sb.session_start()

    def question(self, prompt):
        self.sb.said(self.sb.user(prompt), *self.sb.tool("Read", prompt), self.sb.text(prompt))
        return self.sb.stop(prompt=prompt, final=prompt)

    def edit(self, prompt, text):
        self.sb.write("src/app.py", text)
        self.sb.said(self.sb.user(prompt), *self.sb.tool("Edit", prompt), self.sb.text(prompt))
        return self.sb.stop(prompt=prompt, final=prompt)

    def test_a_file_rewritten_on_every_run_is_learned_after_two(self):
        self.check_writing("date +%s%N > junit.xml")
        self.assertEqual(self.edit("p1", "x = 2\n"), (0, "", ""))
        r = self.question("q1")  # runs it once more: junit.xml changed again
        self.assertEqual(r[0], 0)
        self.assertIn("rewrites junit.xml on every run", r.message)
        self.assertIn("Gitignore it.", r.message)
        for prompt in ("q2", "q3", "q4"):
            self.assertEqual(self.question(prompt), (0, "", ""), prompt)
        self.assertEqual(self.sb.run_count(), 2)
        self.assertEqual(self.edit("p5", "x = 3\n"), (0, "", ""))  # covered from now on
        self.assertEqual(self.question("q6"), (0, "", ""))
        self.assertEqual(self.sb.run_count(), 3)

    def test_a_new_module_written_during_a_check_is_still_caught_after_that(self):
        self.sb.write("scripts/check", '#!/bin/sh\nred=no; [ -e NEWRED.py ] && red=yes\n'
                      'echo run >> "$RUNS"\ndate +%s%N > junit.xml\nsleep 0.5\n'
                      '[ $red = no ] || { echo "ERROR NEWRED.py is broken"; exit 1; }\n'
                      'echo "CHECK PASS 1/1"\n', mode=0o755)
        self.sb.git("commit", "-qam", "check with a report", old=True)
        self.sb.session_start()
        self.edit("p1", "x = 2\n")
        self.assertIn("rewrites junit.xml", self.question("q1").message)

        def write_once_the_third_run_has_looked():
            deadline = time.monotonic() + 30
            while self.sb.run_count() < 3 and time.monotonic() < deadline:
                time.sleep(0.005)
            self.sb.write("NEWRED.py", "syntax error(\n")
        writer = threading.Thread(target=write_once_the_third_run_has_looked)
        writer.start()
        self.assertEqual(self.edit("p2", "x = 3\n")[0], 0)  # the third run didn't see it
        writer.join()
        r = self.question("q3")
        self.assertEqual(r[0], 2)
        self.assertIn("ERROR NEWRED.py is broken", r.reason)

    def test_new_names_on_every_run_get_one_notice(self):
        self.check_writing("mkdir -p test-results; date +%s%N > test-results/run-$$.xml")
        self.edit("p1", "x = 2\n")
        notices = [self.question(f"q{i}").message for i in range(1, 4)]
        self.assertEqual(self.sb.run_count(), 4)  # each stop still runs it ...
        self.assertIn("on two runs in a row (test-results/run-", notices[0])
        self.assertEqual(notices[1:], ["", ""])  # ... but the user is told once


class TestConcurrentEdits(GateCase):
    # Each check reads the tree, then logs a run (the writer's cue), then takes a second.
    SLOW = ('#!/bin/sh\nv=$(cat verdict.txt)\necho run >> "$RUNS"\nsleep 1\n'
            'case "$v" in *GOOD*) echo "CHECK PASS 1/1"; exit 0;; esac\necho "ERROR red"; exit 1\n')

    def mid_check(self, write):
        """A writer (another agent, an editor) that acts once the running check has read."""
        runs = self.sb.run_count()

        def wait_then_write():
            deadline = time.monotonic() + 30
            while self.sb.run_count() == runs and time.monotonic() < deadline:
                time.sleep(0.01)
            write()
        writer = threading.Thread(target=wait_then_write)
        writer.start()
        return writer

    def test_a_tracked_edit_made_while_the_check_runs_is_checked_next_time(self):
        self.sb.write("scripts/check", self.SLOW, mode=0o755)
        self.sb.git("commit", "-qam", "slow check", old=True)
        self.sb.session_start()
        self.sb.write("src/app.py", "x = 2\n")
        writer = self.mid_check(self.red)
        self.assertEqual(self.sb.stop(prompt="p1")[0], 0)  # the check read GOOD
        writer.join()
        self.assertIn("tracked_changed_meanwhile=1", self.sb.log())
        self.assertEqual(self.sb.stop(prompt="p2")[0], 2)  # not cached as green
        self.assertEqual(self.sb.run_count(), 2)

    def test_a_new_file_written_while_a_passing_check_runs_is_checked_next_time(self):
        self.sb.write("scripts/check", '#!/bin/sh\n'
                      'if [ -e NEWRED.py ]; then echo "ERROR NEWRED.py is broken"; exit 1; fi\n'
                      'echo run >> "$RUNS"\nsleep 1\necho "CHECK PASS 1/1"\n', mode=0o755)
        self.sb.git("commit", "-qam", "slow check", old=True)
        self.sb.session_start()
        self.sb.write("src/app.py", "x = 2\n")
        self.sb.said(self.sb.user("p1"), *self.sb.tool("Edit", "p1"), self.sb.text("done"))
        writer = self.mid_check(lambda: self.sb.write("NEWRED.py", "syntax error(\n"))
        self.assertEqual(self.sb.stop(prompt="p1")[0], 0)  # the check didn't see it
        writer.join()
        self.sb.said(self.sb.user("p2"), *self.sb.tool("Read", "p2"), self.sb.text("answer"))
        r = self.sb.stop(prompt="p2", final="answer")  # even a read-only turn checks it
        self.assertEqual(r[0], 2)
        self.assertIn("ERROR NEWRED.py is broken", r.reason)
        self.assertIn("unjudged=1", self.sb.log())

    def test_caches_written_while_a_background_task_runs_are_checked_next_time(self):
        cache = 'mkdir -p __pycache__; date +%s%N > __pycache__/run.$$\n'
        self.sb.write("scripts/check", CHECK.replace('echo run >> "$RUNS"\n',
                                                     'echo run >> "$RUNS"\n' + cache), mode=0o755)
        self.sb.write("src/app.py", "x = 3\n")
        self.assertEqual(self.sb.stop(background=[DEV_SERVER])[0], 0)
        self.assertEqual(self.sb.stop(background=[DEV_SERVER])[0], 0)  # its pycache: re-checked
        self.assertEqual(self.sb.run_count(), 2)
        self.assertEqual(self.sb.stop()[0], 0)  # no task now: the new pycache is covered
        self.assertEqual(self.sb.stop()[0], 0)
        self.assertEqual(self.sb.run_count(), 3)

    def test_a_check_that_reformats_is_checked_again_not_taken_as_someone_elses(self):
        self.sb.write("scripts/check", CHECK.replace(
            'echo run >> "$RUNS"\n', 'echo run >> "$RUNS"\nsed -i "s/  */ /g" src/app.py\n'),
            mode=0o755)
        self.sb.git("commit", "-qam", "formatting check", old=True)
        self.sb.session_start()
        self.sb.write("src/app.py", "x  =   2\n")
        self.sb.said(self.sb.user("p1"), *self.sb.tool("Edit", "p1"), self.sb.text("done"))
        self.assertEqual(self.sb.stop(prompt="p1")[0], 0)
        self.sb.said(self.sb.user("p2"), *self.sb.tool("Read", "p2"), self.sb.text("answer"))
        self.assertEqual(self.sb.stop(prompt="p2", final="answer")[0], 0)
        self.assertEqual(self.sb.run_count(), 2)  # the formatted tree was checked, not adopted
        self.assertNotIn("not-claudes", self.sb.log())
        self.red()  # then Claude's own red edit: nothing says it may not be Claude's
        self.sb.said(self.sb.user("p3"), *self.sb.tool("Edit", "p3"), self.sb.text("edited"))
        r = self.sb.stop(prompt="p3", final="edited")
        self.assertEqual(r[0], 2)
        self.assertNotIn("changes you didn't make", r.reason)


class TestCannotJudge(GateCase):
    def test_timeout_fails_open_and_kills_the_check(self):
        pidfile = self.sb.tmp / "child.pid"
        self.sb.write("scripts/check", f"#!/bin/sh\nsleep 30 &\necho $! > {pidfile}\nwait\n",
                      mode=0o755)
        t0 = time.monotonic()
        r = self.sb.stop(args=["--timeout", "1"])
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(r[0], 0)
        self.assertIn("timed out after 1s", r.message)
        child = int(pidfile.read_text())
        for _ in range(30):  # killed with its process group; give init a moment to reap it
            if not alive(child):
                break
            time.sleep(0.1)
        else:
            self.fail(f"check child {child} still running after the timeout")
        self.assertEqual(self.sb.stop(args=["--timeout", "1"])[0], 0)  # cached: no rerun

    def test_missing_tool(self):
        self.sb.write("scripts/check", "#!/bin/sh\nno-such-tool-xyz\n", mode=0o755)
        r = self.sb.stop()
        self.assertEqual(r[0], 0)
        self.assertIn("could not run (exit 127)", r.message)

    def test_not_executable(self):
        self.sb.write("scripts/check", "#!/bin/sh\nexit 1\n", mode=0o644)
        r = self.sb.stop()
        self.assertEqual(r[0], 0)
        self.assertIn("chmod +x scripts/check", r.message)

    def test_a_check_that_crashes_has_failed(self):
        crash = (f"{sys.executable} -c 'import os, signal; "
                 f"os.kill(os.getpid(), signal.SIGSEGV)'")
        r = self.sb.stop(env={"HARNESS_CHECK_CMD": crash})
        self.assertEqual(r[0], 2)
        self.assertIn("ERROR check killed by SIGSEGV", r.reason)

    def test_a_check_that_ran_and_then_cannot_run_has_failed(self):
        self.sb.write("scripts/check", '#!/bin/sh\necho run >> "$RUNS"\n'
                      './run_tests.sh || exit $?\necho "CHECK PASS 1/1"\n', mode=0o755)
        self.sb.write("run_tests.sh", "#!/bin/sh\ngrep -q GOOD verdict.txt\n", mode=0o755)
        self.sb.git("add", ".")
        self.sb.git("commit", "-qm", "tests", old=True)
        self.sb.session_start()
        self.sb.write("src/app.py", "x = 2\n")
        self.assertEqual(self.sb.stop(prompt="p1")[0], 0)  # it runs
        os.chmod(self.sb.repo / "run_tests.sh", 0o644)  # then a change breaks the entry point
        r = self.sb.stop(prompt="p2")
        self.assertEqual(r[0], 2)
        self.assertIn("exit 126", r.reason)
        self.assertIn("it ran earlier in this session", r.reason)
        os.chmod(self.sb.repo / "run_tests.sh", 0o755)
        os.chmod(self.sb.repo / "scripts" / "check", 0o644)
        r = self.sb.stop(prompt="p3")
        self.assertEqual(r[0], 2)
        self.assertIn("chmod +x scripts/check", r.reason)


class TestCancelled(GateCase):
    """Claude Code cancelling the gate (hook timeout, interrupt) takes the check down too."""

    def cancel(self, sig, body="sleep 30\n", tree_wide=False):
        pidfile = self.sb.tmp / "check.pid"
        if pidfile.exists():
            pidfile.unlink()
        self.sb.write("scripts/check", f"#!/bin/sh\necho $$ > {pidfile}\n{body}", mode=0o755)
        self.sb.write("src/app.py", "x = 42\n")
        payload = {"session_id": "s1", "cwd": str(self.sb.repo), "prompt_id": "p1",
                   "hook_event_name": "Stop", "stop_hook_active": False, "background_tasks": []}
        proc = subprocess.Popen([sys.executable, str(GATE)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=self.sb.env, cwd=str(self.sb.tmp), start_new_session=True)
        proc.stdin.write(json.dumps(payload).encode())
        proc.stdin.close()
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            time.sleep(0.05)
        check = int(pidfile.read_text())
        for _ in range(100):  # the check and its watchdog are both up (a cancel during start-up
            kids = subprocess.run(["ps", "-o", "pid=", "--ppid", str(proc.pid)],  # has its own
                                  capture_output=True, text=True).stdout.split()  # test)
            if len(kids) == 2:
                break
            time.sleep(0.05)
        targets = [proc.pid]
        if tree_wide:  # Claude Code signals every process under the hook, watchdog included
            targets += [int(k) for k in kids if int(k) != check]
            self.assertEqual(len(targets), 2, kids)  # the gate and its watchdog
        for pid in targets:
            os.kill(pid, sig)
        rc = proc.wait(timeout=10)
        out = proc.stdout.read().decode()
        proc.stdout.close()
        proc.stderr.close()
        for _ in range(30):
            if not alive(check):
                break
            time.sleep(0.1)
        still = alive(check)
        if still:
            os.killpg(check, signal.SIGKILL)
        return rc, out, still

    def test_term_int_and_hup(self):
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            rc, out, still = self.cancel(sig)
            self.assertEqual((rc, out, still), (0, "", False), sig.name)
            self.assertIn("cancelled allow", self.sb.log())
            self.assertIn(f"signal={sig.name}", self.sb.log())

    def test_sigkill_does_not_orphan_the_check(self):  # the watchdog takes it down
        rc, out, still = self.cancel(signal.SIGKILL)
        self.assertEqual((rc, still), (-9, False))

    def test_the_check_gets_a_moment_to_clean_up(self):
        marker = self.sb.tmp / "cleaned"
        trap = f"trap 'echo restored > {marker}; exit 1' TERM\nsleep 30 &\nwait $!\n"
        rc, out, still = self.cancel(signal.SIGTERM, body=trap)
        self.assertEqual((rc, still), (0, False))
        self.assertEqual(marker.read_text(), "restored\n")  # e.g. a stash popped back

    def test_a_tree_wide_cancel_is_logged_once(self):
        rc, out, still = self.cancel(signal.SIGTERM, tree_wide=True)
        self.assertEqual((rc, still), (0, False))
        self.assertEqual(self.sb.log().count("cancelled allow"), 1)

    def test_a_cancel_right_after_the_check_starts_still_takes_it_down(self):
        # Deterministic stand-in for a SIGTERM landing between the check's exec and Popen
        # returning: the gate signals itself as soon as the real Popen is done.
        pidfile = self.sb.tmp / "check.pid"
        self.sb.write("scripts/check", "#!/bin/sh\nsleep 30\n", mode=0o755)
        self.sb.write("src/app.py", "x = 43\n")
        driver = ("import os, signal, subprocess, sys\n"
                  f"sys.path.insert(0, {str(GATE.parent)!r})\n"
                  "import stop_gate\n"
                  "real = subprocess.Popen\n"
                  "def popen(*a, **k):\n"
                  "    p = real(*a, **k)\n"
                  "    if k.get('start_new_session'):  # the check, not a git call\n"
                  f"        open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
                  "        os.kill(os.getpid(), signal.SIGTERM)\n"
                  "    return p\n"
                  "stop_gate.subprocess.Popen = popen\n"
                  "sys.exit(stop_gate.main([]))\n")
        p = subprocess.run([sys.executable, "-c", driver], input=json.dumps(self.sb.payload()),
                           capture_output=True, text=True, env=self.sb.env, cwd=str(self.sb.tmp),
                           timeout=30)
        self.assertEqual((p.returncode, p.stdout), (0, ""), p.stderr)
        check = int(pidfile.read_text())
        for _ in range(30):
            if not alive(check):
                break
            time.sleep(0.1)
        else:
            os.killpg(check, signal.SIGKILL)
            self.fail("the check outlived a cancel that came right after it started")
        self.assertIn("cancelled allow", self.sb.log())

    def test_a_finished_check_releases_its_watchdog(self):
        gate = self.sb.tmp / "gate-copy-for-this-test.py"  # a path no other run uses
        shutil.copy(GATE, gate)
        self.sb.write("src/app.py", "x = 3\n")
        t0 = time.monotonic()
        self.assertEqual(self.sb.stop(gate=gate), (0, "", ""))  # would hang if stdout were held
        self.assertLess(time.monotonic() - t0, 10)
        time.sleep(0.3)
        ps = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True)
        self.assertNotIn(str(gate), ps.stdout)  # no watchdog left behind


class TestRobustness(GateCase):
    def test_a_closed_stdout_still_exits_0(self):
        self.red()
        r, w = os.pipe()
        os.close(r)  # the reader is gone before the gate prints its block
        p = subprocess.Popen([sys.executable, str(GATE)], stdin=subprocess.PIPE, stdout=w,
                             stderr=subprocess.PIPE, env=self.sb.env, cwd=str(self.sb.tmp))
        os.close(w)
        _, err = p.communicate(json.dumps(self.sb.payload()).encode(), timeout=60)
        self.assertEqual(p.returncode, 0, err)

    def test_a_state_file_of_the_wrong_shape_is_replaced(self):
        self.sb.state_path().parent.mkdir(parents=True)
        self.sb.state_path().write_text(json.dumps(
            {"v": 3, "green": "abc", "fail": [1], "roots": [], "inconclusive": 5}))
        self.red()
        for prompt in ("p1", "p2"):
            r = self.sb.stop(prompt=prompt)
            self.assertNotIn("internal error", r.message)
        self.assertEqual(self.sb.stop(prompt="p3", active=True)[0], 0)  # loop guard works

    def test_a_state_file_that_cannot_be_written_does_not_run_the_check(self):
        self.red()
        self.sb.state_path().mkdir(parents=True)  # os.replace onto it fails
        for i in range(3):
            r = self.sb.stop(active=i > 0)
            self.assertEqual(r[0], 0)
            self.assertIn("can't write its state (IsADirectoryError", r.message)
        self.assertEqual(self.sb.run_count(), 0)

    def test_a_block_that_cannot_be_recorded_is_let_through(self):
        # The state can be written before the check but not after it (the disk filled up).
        self.sb.write("scripts/check", CHECK.replace(
            'echo run >> "$RUNS"\n',
            'echo run >> "$RUNS"\nrm -f "$GATE_STATE"; mkdir -p "$GATE_STATE"\n'), mode=0o755)
        self.red()
        env = {"GATE_STATE": str(self.sb.state_path())}
        r = self.sb.stop(env=env)
        self.assertEqual(r[0], 0)  # without its record, the next stop would block again
        self.assertIn("fails, but the gate can't write its state", r.message)
        self.assertIn("ERROR tests failed", r.message)
        self.assertIn("state-unwritable allow", self.sb.log())
        self.assertEqual(self.sb.stop(env=env, active=True)[0], 0)
        self.assertEqual(self.sb.run_count(), 1)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no FIFOs")
    def test_a_transcript_that_is_not_a_regular_file_is_not_read(self):
        fifo = self.sb.tmp / "fifo"
        os.mkfifo(str(fifo))
        for n, path in enumerate((fifo, Path("/dev/zero")), 1):
            t0 = time.monotonic()
            self.assertEqual(self.sb.session_start(transcript=path, session=f"s{n}"),
                             (0, "", ""))
            self.sb.write("dev.log", f"{n}\n")
            self.assertEqual(self.sb.stop(transcript=path, session=f"s{n}"), (0, "", ""))
            self.assertLess(time.monotonic() - t0, 5)  # open() on a FIFO would wait forever
            self.assertEqual(self.sb.run_count(), n)  # no transcript to go by: checked

    def test_an_unwritable_state_dir_does_not_run_the_check(self):
        ro = self.sb.tmp / "ro"
        ro.mkdir()
        ro.chmod(0o555)
        try:
            if os.access(ro, os.W_OK):
                self.skipTest("running as root")
            self.red()
            env = {"CLAUDE_CONFIG_DIR": str(ro / "cfg")}
            r = self.sb.session_start(env=env)
            self.assertEqual((r.rc, r.out, r.err), (0, "", ""))  # no "internal error" notice
            r = self.sb.stop(env=env)
            self.assertEqual(r[0], 0)
            self.assertIn("can't write its state", r.message)
            self.assertEqual(self.sb.run_count(), 0)
        finally:
            ro.chmod(0o755)


class TestWhichTree(GateCase):
    def test_harness_check_cmd_overrides(self):
        r = self.sb.stop(env={"HARNESS_CHECK_CMD": "echo ERROR custom; exit 1"})
        self.assertEqual(r[0], 2)
        self.assertIn("ERROR custom", r.reason)

    def test_worktree_cwd_is_gated(self):
        wt = self.sb.tmp / "wt"
        self.sb.git("worktree", "add", "-q", str(wt), "-b", "side")
        self.sb.write("verdict.txt", "BAD wt\n", root=wt)
        self.assertEqual(self.sb.stop(cwd=wt)[0], 2)
        self.assertEqual(self.sb.stop(cwd=self.sb.repo, prompt="p2")[0], 0)

    def test_cwd_in_another_repo_gates_the_project(self):
        other = self.sb.make_repo(self.sb.tmp / "other", check=None)
        self.red()
        r = self.sb.stop(cwd=other)
        self.assertEqual(r[0], 2)
        self.assertIn("ERROR tests failed", r.reason)

    def test_unborn_branch(self):
        fresh = self.sb.make_repo(self.sb.tmp / "fresh", commit=False)
        self.sb.write("verdict.txt", "BAD\n", root=fresh)
        env = {"CLAUDE_PROJECT_DIR": str(fresh)}
        self.assertEqual(self.sb.stop(cwd=fresh, env=env)[0], 2)

    def test_a_repo_path_that_is_not_utf8(self):
        odd = self.sb.make_repo(self.sb.tmp / os.fsdecode(b"r\xffoot"))
        self.sb.write("verdict.txt", "BAD\n", root=odd)
        r = self.sb.stop(cwd=odd, env={"CLAUDE_PROJECT_DIR": str(odd)})
        self.assertEqual(r[0], 2, self.sb.log())
        self.assertIn("ERROR tests failed", r.reason)


class TestInstalledCommand(GateCase):
    """The handler install_gate.py writes, run the way Claude Code runs shell-form hooks."""

    PAYLOAD = {"session_id": "s1", "prompt_id": "p1", "hook_event_name": "Stop",
               "stop_hook_active": False, "background_tasks": [], "session_crons": []}

    def handler(self, event, *args):
        subprocess.run([sys.executable, str(INSTALL), str(self.sb.repo), "--apply", *args],
                       check=True, capture_output=True, env=self.sb.env)
        settings = json.loads((self.sb.repo / ".claude" / "settings.json").read_text())
        return settings["hooks"][event][0]["hooks"][0]["command"]

    def test_values_that_start_with_a_dash(self):
        cmd = self.handler("Stop", "--check=-not-a-flag", "--watch=-odd-dir")
        self.red()
        p = self.sh(cmd)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("internal error", p.stdout)  # parsed as values, not options

    def sh(self, command, payload=None, env=None):
        payload = dict(self.PAYLOAD, cwd=str(self.sb.repo)) if payload is None else payload
        return subprocess.run(["/bin/sh", "-c", command], input=json.dumps(payload), text=True,
                              capture_output=True, env=dict(self.sb.env, **(env or {})),
                              cwd=str(self.sb.repo))

    @property
    def hook(self):
        return self.sb.repo / ".claude" / "hooks" / "stop_gate.py"

    def test_registered_command_gates(self):
        cmd = self.handler("Stop")
        self.red()
        p = self.sh(cmd)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("ERROR tests failed",
                      json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_missing_or_unreadable_script_does_not_block(self):
        cmd = self.handler("Stop")
        self.red()
        self.hook.chmod(0)
        if not os.access(self.hook, os.R_OK):  # root can read it anyway
            self.assertEqual(self.sh(cmd).returncode, 0)
        self.hook.unlink()
        p = self.sh(cmd, {"session_id": "s1", "hook_event_name": "Stop"})
        self.assertEqual((p.returncode, p.stdout), (0, ""))  # bare python3 would exit 2

    def test_no_python3_on_path_does_not_block(self):
        cmd = self.handler("Stop")
        self.red()
        bindir = self.sb.tmp / "bin"
        bindir.mkdir()
        p = self.sh(cmd, env={"PATH": str(bindir)})
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_an_exit_2_from_python_itself_is_not_a_block(self):
        cmd = self.handler("Stop")
        self.hook.write_text("import sys\nsys.exit(2)\n")
        self.assertEqual(self.sh(cmd).returncode, 1)  # a visible, non-blocking hook error

    def test_sibling_hooks_and_pythonpath_cannot_shadow_the_stdlib(self):
        cmd = self.handler("Stop")
        self.red()
        shadow = "import sys\nprint('shadowed')\nsys.exit(0)\n"
        for name in ("json", "hashlib", "signal", "subprocess", "argparse", "tempfile"):
            sibling = self.hook.parent / f"{name}.py"
            sibling.write_text(shadow)
            p = self.sh(cmd, dict(self.PAYLOAD, cwd=str(self.sb.repo), session_id=name))
            sibling.unlink()
            self.assertNotIn("shadowed", p.stdout, name)
            self.assertIn("additionalContext", p.stdout, name)
        pp = self.sb.tmp / "pp"
        pp.mkdir()
        (pp / "hashlib.py").write_text(shadow)
        p = self.sh(cmd, env={"PYTHONPATH": str(pp)})
        self.assertIn("additionalContext", p.stdout)

    def test_direct_run_ignores_sibling_modules_too(self):
        self.handler("Stop")
        self.red()
        (self.hook.parent / "json.py").write_text("raise SystemExit(2)\n")
        p = subprocess.run([sys.executable, str(self.hook)], text=True, capture_output=True,
                           input=json.dumps(dict(self.PAYLOAD, cwd=str(self.sb.repo))),
                           env=self.sb.env, cwd=str(self.sb.repo))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("additionalContext", p.stdout)

    def test_session_start_handler_prints_nothing(self):
        cmd = self.handler("SessionStart")
        self.red()
        p = self.sh(cmd, {"session_id": "s1", "cwd": str(self.sb.repo),
                          "hook_event_name": "SessionStart", "source": "startup"})
        self.assertEqual((p.returncode, p.stdout), (0, ""))  # stdout would become context
        self.assertEqual(self.sh(self.handler("Stop")).returncode, 0)


if __name__ == "__main__":
    unittest.main()
