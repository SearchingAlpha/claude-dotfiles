"""Decision logic of stop_gate.py, without git or subprocesses."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import Sandbox  # also puts scripts/ on sys.path
import stop_gate as g

CFG = SimpleNamespace(max_failures=3)
LABEL = "./scripts/check --fast"


def fail(error="ERROR tests failed (exit 1)"):
    return g.Result("fail", 1, "line\n" + error, error, 0.1)


PASS = g.Result("pass", 0, "CHECK PASS 1/1", "", 0.1)


class TestBackgroundTasks(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(g.pending_tasks({}), [])
        self.assertEqual(g.pending_tasks({"background_tasks": "x"}), [])
        self.assertEqual(g.pending_tasks({"background_tasks": []}), [])
        running = {"id": "1", "type": "subagent", "status": "running"}
        self.assertEqual(g.pending_tasks({"background_tasks": [running]}), [running])

    def test_terminal_statuses_and_unknown_entries(self):
        tasks = [{"status": "completed"}, {"status": "Failed"}, {"status": "killed"},
                 {"status": "pending"}, {"type": "shell"}, "odd"]
        self.assertEqual(len(g.pending_tasks({"background_tasks": tasks})), 2)

    def test_only_agents_that_end_and_edit_defer(self):
        types = ["shell", "monitor", "MCP task", "cloud session", "dream", "auto-mode scan",
                 "subagent", "workflow", "teammate"]
        tasks = [{"type": t, "status": "running"} for t in types]
        self.assertEqual([t["type"] for t in g.agents_running(tasks)],
                         ["subagent", "workflow", "teammate"])
        self.assertEqual([t["type"] for t in g.jobs_running(tasks)],  # commands run here
                         ["shell", "monitor", "MCP task", "dream", "auto-mode scan"])

    def test_defer_once_per_agent_prompt_and_tree(self):
        state = {}
        a1 = [{"id": "a1", "type": "subagent", "status": "running"}]
        first = g.defer(state, "T", "p1", a1)
        self.assertEqual((first.block, first.reason), (False, "background-1"))
        self.assertIn("1 background task (subagent) still running", first.message)
        self.assertIsNone(g.defer(state, "T", "p2", a1 + [{"id": "a2", "type": "workflow"}]))
        self.assertIsNone(g.defer(state, "T2", "p1", [{"id": "a2", "type": "workflow"}]))
        self.assertIsNone(g.defer(state, "T2", "p2", a1))  # waited for a1 once already
        self.assertIsNotNone(g.defer(state, "T3", "p3", a1 + [{"id": "a2", "type": "x"}]))
        self.assertEqual(state["deferred"]["tasks"], ["a1", "a2"])
        self.assertIsNone(g.defer(state, "T4", "p4", []))

    def test_task_key_without_an_id(self):
        self.assertEqual(g.task_key({"id": "t9"}), "t9")
        self.assertEqual(g.task_key({"type": "workflow", "name": "review"}), "workflow:review")


class TestPromptKey(unittest.TestCase):
    def test_prompt_id_wins(self):
        self.assertEqual(g.prompt_key({"prompt_id": "abc", "stop_hook_active": True},
                                      {"prompt": "old"}, 1.0), "abc")

    def test_fallback_on_stop_hook_active(self):
        self.assertEqual(g.prompt_key({"stop_hook_active": True}, {"prompt": "old"}, 1.0), "old")
        self.assertNotEqual(g.prompt_key({"stop_hook_active": False}, {"prompt": "old"}, 1.0),
                            "old")
        self.assertNotEqual(g.prompt_key({"stop_hook_active": True}, {}, 1.0), "")


class TestOutput(unittest.TestCase):
    def test_clean_lines(self):
        text = "\x1b[31mred\x1b[0m\nprogress 10%\rprogress 100%\n" + "x" * 400 + "\n\n\n"
        lines = g.clean_lines(text)
        self.assertEqual(lines[:2], ["red", "progress 100%"])
        self.assertEqual(len(lines), 3)
        self.assertLessEqual(len(lines[2]), g.LINE_MAX)

    def test_error_line_prefers_the_contract(self):
        self.assertEqual(g.error_line(["a", "ERROR lint failed", "trailing"], 1),
                         "ERROR lint failed")
        self.assertEqual(g.error_line(["CHECK FAIL", "x"], 1), "CHECK FAIL")  # agent-harness
        self.assertEqual(g.error_line(["boom", ""], 3), "ERROR exit 3: boom")
        self.assertEqual(g.error_line([], 1), "ERROR exit 1")

    def test_classify(self):
        many = "\n".join(f"l{i}" for i in range(50)) + "\nERROR x\n"
        r = g.classify(1, many, 0.5)
        self.assertEqual((r.status, r.error), ("fail", "ERROR x"))
        self.assertEqual(len(r.tail.splitlines()), g.TAIL_LINES)
        self.assertEqual(g.classify(0, "CHECK PASS 3/3", 0).status, "pass")
        self.assertEqual(g.classify(2, "pytest usage error", 0).status, "fail")
        for code in (126, 127):
            self.assertEqual(g.classify(code, "", 0).status, "inconclusive", code)

    def test_a_check_killed_by_a_signal_failed(self):
        r = g.classify(-11, "Segmentation fault", 0)
        self.assertEqual((r.status, r.error), ("fail", "ERROR check killed by SIGSEGV"))
        self.assertEqual(g.classify(-99, "", 0).error, "ERROR check killed by signal 99")

    def test_block_message_does_not_assume_the_cause(self):
        msg = g.block_message(LABEL, 1, 3, fail())
        self.assertIn("`./scripts/check --fast` fails on the current tree (failure 1", msg)
        self.assertIn("checkout or pull", msg)
        self.assertNotIn("your change broke", msg)
        self.assertNotIn("you didn't make", msg)
        self.assertIn("this work tree has changes you didn't make",
                      g.block_message(LABEL, 1, 3, fail(), True))


class TestDecisions(unittest.TestCase):
    def test_cached_trees_skip_the_check(self):
        state = {"green": ["A"], "baselines": ["B0", "B"], "inconclusive": ["C"]}
        self.assertEqual(g.before_check(state, "A", "p").reason, "green-cached")
        self.assertEqual(g.before_check(state, "B", "p").reason, "baseline-cached")
        self.assertEqual(g.before_check(state, "C", "p").reason, "inconclusive-cached")
        self.assertIsNone(g.before_check(state, "D", "p"))

    def test_fail_then_no_progress(self):
        state = {}
        self.assertIsNone(g.before_check(state, "T", "p"))
        out = g.record(state, "T", "p", fail(), CFG, LABEL)
        self.assertTrue(out.block)
        self.assertIn("ERROR tests failed", out.context)
        again = g.before_check(state, "T", "p")
        self.assertEqual((again.block, again.reason), (False, "no-progress"))
        self.assertIn("ERROR tests failed", again.message)

    def test_gives_up_at_max_failures_and_new_prompt_resets(self):
        state = {}
        blocks = []
        for tree in ("T1", "T2", "T3", "T4"):
            self.assertIsNone(g.before_check(state, tree, "p"))
            blocks.append(g.record(state, tree, "p", fail(), CFG, LABEL).block)
        self.assertEqual(blocks, [True, True, False, False])
        self.assertIsNone(g.before_check(state, "T5", "q"))
        self.assertEqual(state["failures"], 0)
        self.assertTrue(g.record(state, "T5", "q", fail(), CFG, LABEL).block)

    def test_pass_caches_green_and_clears_failures(self):
        state = {}
        g.before_check(state, "T1", "p")
        g.record(state, "T1", "p", fail(), CFG, LABEL)
        out = g.record(state, "T2", "p", PASS, CFG, LABEL)
        self.assertEqual((out.block, out.message), (False, ""))
        self.assertEqual((state["green"], state["failures"], "fail" in state), (["T2"], 0, False))
        for i in range(20):
            g.record(state, f"G{i}", "p", PASS, CFG, LABEL)
        self.assertEqual(len(state["green"]), g.GREEN_KEEP)
        self.assertEqual(state["green"][-1], "G19")

    def test_returning_to_a_green_tree_ends_the_streak(self):
        state = {}
        g.before_check(state, "G", "p")
        g.record(state, "G", "p", PASS, CFG, LABEL)
        for tree in ("T1", "T2"):
            g.before_check(state, tree, "p")
            g.record(state, tree, "p", fail(), CFG, LABEL)
        self.assertEqual(g.before_check(state, "G", "p").reason, "green-cached")  # reverted
        self.assertIsNone(g.before_check(state, "T3", "p"))
        out = g.record(state, "T3", "p", fail(), CFG, LABEL)
        self.assertTrue(out.block)
        self.assertIn("(failure 1;", out.context)  # 1st, not 3rd

    def test_kill_group_without_process_groups(self):  # Windows has no os.killpg
        class NoKillpg:
            def __getattr__(self, name):
                if name == "killpg":
                    raise AttributeError(name)
                return getattr(os, name)

        proc = mock.Mock()
        with mock.patch.object(g, "os", NoKillpg()):
            g._kill_group(proc)
        proc.kill.assert_called_once_with()

    def test_tree_left_by_the_check_shares_its_verdict(self):
        state = {}
        g.record(state, "T", "p", PASS, CFG, LABEL, after="T+pyc")
        self.assertEqual(g.before_check(state, "T+pyc", "p").reason, "green-cached")
        g.record(state, "R", "p", fail(), CFG, LABEL, after="R+pyc")
        self.assertEqual(g.before_check(state, "R+pyc", "p").reason, "no-progress")
        self.assertEqual(g.before_check(state, "R", "p").reason, "no-progress")
        g.record(state, "S", "p", PASS, CFG, LABEL, after="S")
        self.assertEqual(state["green"][-3:], ["T", "T+pyc", "S"])

    def test_earlier_prompt_failure_warns_once(self):
        state = {}
        g.before_check(state, "T", "p1")
        g.record(state, "T", "p1", fail(), CFG, LABEL)
        first = g.before_check(state, "T", "p2")
        self.assertEqual((first.block, first.reason), (False, "still-red"))
        self.assertTrue(first.message)
        second = g.before_check(state, "T", "p3")
        self.assertEqual((second.block, second.message), (False, ""))

    def test_inconclusive_allows_with_a_message(self):
        state = {}
        res = g.Result("inconclusive", -1, "", "`x` timed out after 5s", 5.0)
        out = g.record(state, "T", "p", res, CFG, LABEL)
        self.assertFalse(out.block)
        self.assertIn("timed out", out.message)
        self.assertEqual(state["inconclusive"], ["T"])

    def test_inherited_changes_add_the_hint(self):
        out = g.record({}, "T", "p", fail(), CFG, LABEL, inherited=True)
        self.assertIn("changes you didn't make", out.context)

    def test_unrunnable_after_a_run_is_a_failure(self):
        res = g.classify(127, "run_tests.sh: not found\nERROR tests could not run", 0)
        self.assertEqual(g.escalate(res, False).status, "inconclusive")
        broke = g.escalate(res, True)
        self.assertEqual(broke.status, "fail")
        self.assertIn("it ran earlier in this session", broke.error)
        timeout = g.Result("inconclusive", -1, "", "timed out", 5.0)
        self.assertEqual(g.escalate(timeout, True).status, "inconclusive")

    def test_baselines_are_capped(self):
        state = {}
        for i in range(40):
            g.add_baseline(state, f"B{i}")
        g.add_baseline(state, "B30")
        self.assertEqual(len(state["baselines"]), g.BASELINES_KEEP)
        self.assertEqual(state["baselines"][-1], "B30")


class TestArgs(unittest.TestCase):
    def test_defaults(self):
        cfg = g.parse_args([])
        self.assertEqual((cfg.mode, cfg.timeout, cfg.max_failures, cfg.watch, cfg.check),
                         ("fast", 240.0, 3, [], ""))

    def test_errors_raise_instead_of_exiting_2(self):
        for argv in (["--mode", "x"], ["--timeout", "abc"], ["--nope"], ["--timeout", "0"],
                     ["--timeout", "nan"], ["--timeout", "inf"], ["--watch", "../x"],
                     ["--watch", "/abs"], ["--watch", ""], ["--max-failures", "1"],
                     ["--max-failures", "0"]):
            with self.assertRaises(ValueError, msg=argv):
                g.parse_args(argv)
        self.assertEqual(g.parse_args(["--watch", "src/../lib"]).watch, ["src/../lib"])
        self.assertEqual(g.parse_args(["--check=-x", "--watch=-y"]).check, "-x")


class TestStateShapes(unittest.TestCase):
    def test_wrong_types_mean_a_fresh_state(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.json"
            good = {"v": g.STATE_VERSION, "green": ["a"], "fail": {"trees": ["t"], "noted": True},
                    "roots": {"/r": {"base": "x", "t": 1.0, "mark": {"path": "/t", "offset": 3},
                                     "jobs": {"b7": {"src": "s", "held": ["T"]}},
                                     "outputs": ["junit.xml"], "left": ["a"], "unjudged": "U"}}}
            path.write_text(json.dumps(good))
            self.assertEqual(g.load_state(path), good)
            for bad in ({"inconclusive": 5}, {"green": "abc"}, {"fail": [1]}, {"roots": []},
                        {"roots": {"/r": {"base": None}}}, {"failures": "2"}, {"start": "now"},
                        {"roots": {"/r": {"mark": {"path": "/t", "offset": "3"}}}},
                        {"deferred": {"tasks": [1]}}, {"fail": {"error": 7}},
                        {"roots": {"/r": {"jobs": []}}}, {"roots": {"/r": {"jobs": {"b": {}}}}},
                        {"roots": {"/r": {"jobs": {"b": {"src": "s", "held": [1]}}}}},
                        {"roots": {"/r": {"outputs": "junit.xml"}}},
                        {"roots": {"/r": {"left": [None]}}}, {"roots": {"/r": {"unjudged": 1}}}):
                path.write_text(json.dumps(dict(bad, v=g.STATE_VERSION)))
                self.assertEqual(g.load_state(path), {"v": g.STATE_VERSION}, bad)
            path.write_text(json.dumps(dict(good, v=2)))  # an older gate's file
            self.assertEqual(g.load_state(path), {"v": g.STATE_VERSION})


class TestTranscriptEntries(unittest.TestCase):
    FINAL = g._text_id("done")

    def seen(self, prev=""):
        return {"final": False, "prev": prev, "wrote": False, "user": None, "since": None,
                "edited": set()}

    def scan(self, *entries, seen=None, final=FINAL):
        seen = seen if seen is not None else self.seen()
        for entry in entries:
            g._scan(entry, final, seen)
        return seen

    def test_read_only_tools_and_text(self):
        seen = self.scan(*Sandbox.tool("Read", "p"), *Sandbox.tool("Grep", "p"),
                         Sandbox.text("done"))
        self.assertEqual((seen["wrote"], seen["final"]), (False, True))
        self.scan({"type": "attachment"}, seen=seen)
        self.assertTrue(seen["final"])
        self.scan(Sandbox.tool("Read", "p")[0], seen=seen)
        self.assertFalse(seen["final"])  # a tool call after it: that text wasn't the final one

    def test_anything_that_can_write(self):
        thinking = {"type": "assistant", "message": {"content": [{"type": "thinking"}]}}
        self.assertFalse(self.scan(thinking)["wrote"])
        for entry in (Sandbox.tool("Bash", "p")[0], Sandbox.tool("Edit", "p")[0],
                      Sandbox.tool("mcp__srv__tool", "p")[0], Sandbox.tool("Agent", "p")[0],
                      Sandbox.tool("Skill", "p")[0], dict(Sandbox.text("x"), isSidechain=True),
                      {"type": "assistant", "message": {"content": [{"type": "server_tool_use"}]}},
                      {"type": "assistant", "message": {}}, "not a dict"):
            self.assertTrue(self.scan(entry)["wrote"], entry)

    def test_the_previous_stops_final_is_skipped_once(self):
        same = g._text_id("same")
        seen = self.scan(Sandbox.text("same"), seen=self.seen(prev=same), final=same)
        self.assertFalse(seen["final"])  # written after the last mark: the previous message
        self.scan(Sandbox.text("same"), seen=seen, final=same)
        self.assertTrue(seen["final"])

    def test_since_is_the_user_entry_before_the_first_write(self):
        prompt = dict(Sandbox.user("p"), timestamp="2026-10-09T09:15:22.516Z")
        result = dict(Sandbox.tool("Read", "p")[1], timestamp="2026-10-09T09:15:30.000Z")
        later = dict(Sandbox.user("p"), timestamp="2026-10-09T09:16:00.000Z")
        seen = self.scan(prompt, Sandbox.tool("Read", "p")[0], result,
                         *Sandbox.tool("Bash", "p", command="git log"), later)
        self.assertTrue(seen["wrote"])
        self.assertAlmostEqual(seen["since"], 1791537330.0, places=3)  # the Read's result
        self.assertIsNone(self.scan(*Sandbox.tool("Bash", "p"))["since"])  # no user entry yet
        self.assertIsNone(self.scan(prompt, "not a dict")["since"])  # can't be dated
        self.assertIsNone(self.scan(dict(prompt, timestamp="yesterday"),
                                    Sandbox.tool("Bash", "p")[0])["since"])

    def test_edit_tools_name_their_files(self):
        seen = self.scan(*Sandbox.tool("Edit", "p", file_path="/r/src/a.py"),
                         *Sandbox.tool("Write", "p", file_path="/r/b.py"),
                         *Sandbox.tool("NotebookEdit", "p", notebook_path="/r/n.ipynb"),
                         *Sandbox.tool("Bash", "p", command="echo > c.py"))
        self.assertEqual(seen["edited"], {b"/r/src/a.py", b"/r/b.py", b"/r/n.ipynb"})

    def test_timestamps(self):
        self.assertAlmostEqual(g._when({"timestamp": "2026-10-09T09:15:22.516Z"}),
                               1791537322.516, places=3)
        self.assertAlmostEqual(g._when({"timestamp": "2026-10-09T11:15:22.516+02:00"}),
                               1791537322.516, places=3)
        for bad in ({}, {"timestamp": 5}, {"timestamp": "2026-10-09T09:15:22"}, {"timestamp": ""}):
            self.assertIsNone(g._when(bad), bad)  # a time without a zone can't be compared


class TestBlockContext(unittest.TestCase):
    ROOT = Path("/r")
    SINCE = 1000.0

    def test_the_verdict_survives_the_size_cap(self):
        tail = "\n".join(["E   AssertionError: " + "x" * 279] * 20 + ["ERROR tests failed"])
        result = g.classify(1, tail, 1.0)
        agents = ({"type": "subagent", "id": "a"}, {"type": "workflow", "id": "b"})
        for length in (60, 140, 400, 4000):
            changes = [((f"services/api/{i:02d}_" + "n" * length)[:length] + ".py").encode()
                       for i in range(12)]
            note = g.changes_note([(c, 900.0) for c in changes], self.turn(), self.ROOT)
            msg = g.block_message(LABEL, 1, 3, result, True, agents, note)
            self.assertLessEqual(len(msg), g.CONTEXT_MAX, length)
            self.assertTrue(msg.endswith("ERROR tests failed\n"), length)
            if length > g.NAME_MAX:  # each name is cut to its last 100 characters
                self.assertIn("Changed since the last check: …", msg)
        long_check = "HARNESS_X=1 " * 100 + "make check"  # a long --check command
        msg = g.block_message(long_check, 1, 3, result, True, agents, note)
        self.assertLessEqual(len(msg), g.CONTEXT_MAX)
        self.assertTrue(msg.endswith("ERROR tests failed\n"))
        self.assertIn("lines cut]", msg)
        short = g.block_message(LABEL, 1, 3, fail(), note="NOTE")
        self.assertNotIn("lines cut", short)
        self.assertTrue(short.endswith("line\nERROR tests failed (exit 1)\n"))

    def test_fit_cuts_the_middle_of_the_tail_then_the_note(self):
        body = "\n".join(f"line {i:02d} " + "y" * 90 for i in range(21))
        text = g.fit("HEAD.", "NOTE " * 100, body, limit=1500)
        self.assertLessEqual(len(text), 1500)
        self.assertTrue(text.startswith("HEAD. NOTE"))
        self.assertIn("line 00", text)
        self.assertIn("line 20", text)
        self.assertRegex(text, r"\[… \d+ lines cut\]")
        tiny = g.fit("HEAD.", "NOTE " * 100, body, limit=300)
        self.assertLessEqual(len(tiny), 300)
        self.assertNotIn("NOTE", tiny)  # the note goes before the verdict would
        self.assertIn("line 20", tiny)
        huge = g.fit("H" * 500, "", "ERROR x", limit=100)
        self.assertEqual((len(huge) <= 100, huge.endswith("\nERROR x\n")), (True, True))

    def turn(self, edited=(), since=SINCE):
        return g.Turn(True, True, since, frozenset(edited))

    def test_lists_what_changed_and_what_isnt_claudes(self):
        changes = [(b"src/a.py", 900.0), (b"src/b.py", 1005.0), (b"gone.py", None)]
        note = g.changes_note(changes, self.turn([b"/r/src/b.py"]), self.ROOT)
        self.assertIn("Changed since the last check: src/a.py, src/b.py, gone.py.", note)
        self.assertIn("Not yours, as they changed before your first tool call since then: "
                      "src/a.py.", note)
        self.assertNotIn("None of them went through your Edit", note)

    def test_nothing_through_claudes_edits(self):
        note = g.changes_note([(b"calc.py", 1005.0)], self.turn(), self.ROOT)
        self.assertIn("None of them went through your Edit or Write calls", note)
        self.assertNotIn("Not yours", note)
        many = [(f"f{i}".encode(), 1005.0) for i in range(12)]
        self.assertIn("f7 and 4 more.", g.changes_note(many, self.turn(), self.ROOT))
        self.assertEqual(g.changes_note([], self.turn(), self.ROOT), "")
        self.assertEqual(g.changes_note(None, self.turn(), self.ROOT), "")

    def test_running_agents_are_named_and_cap_the_blocks(self):
        agents = ({"id": "a1", "type": "subagent"}, {"id": "a2", "type": "workflow"})
        state = {}
        out = g.record(state, "T1", "p", fail(), CFG, LABEL, agents=agents, note="NOTE")
        self.assertTrue(out.block)
        self.assertIn("(failure 1; the gate stops blocking at 2)", out.context)
        self.assertIn("2 background agents (subagent, workflow) are still running and may be "
                      "mid-edit. If the failure is in files they are changing, wait instead of "
                      "editing", out.context)
        self.assertIn(" NOTE\n\n", out.context)
        again = g.record(state, "T2", "p", fail(), CFG, LABEL, agents=agents[:1])
        self.assertEqual((again.block, again.reason), (False, "gave-up"))
        self.assertIn("still fails while 1 background agent (subagent) run", again.message)
        self.assertNotIn("background agent", g.block_message(LABEL, 1, 3, fail()))

    def test_a_failure_seen_while_agents_ran_is_judged_again_once_they_are_done(self):
        state = {}
        agents = ({"id": "a1", "type": "subagent"},)
        g.before_check(state, "T", "p1", True)
        g.record(state, "T", "p1", fail(), CFG, LABEL, agents=agents)
        self.assertEqual(g.before_check(state, "T", "p1", True).reason, "no-progress")
        self.assertIsNone(g.before_check(state, "T", "p2", False))  # agents done: check it
        g.record(state, "T", "p2", fail(), CFG, LABEL)
        self.assertEqual(g.before_check(state, "T", "p3", False).reason, "still-red")


class TestNotClaudes(unittest.TestCase):
    TREE = g.Tree("d", "tracked", "u", "base", (), (), ())
    MARK = {"path": "/t", "offset": 0, "tracked": "tracked", "t0": 890.0, "t1": 900.0}

    def why(self, turn, changes=(), mark=None, tasks=()):
        with mock.patch.object(g, "changes_since", return_value=list(changes)):
            return g.not_claudes(None, mark or self.MARK, self.TREE, list(tasks), lambda: turn)

    def test_no_write_up_to_the_final_message(self):
        self.assertEqual(self.why(g.Turn(True, False, None, frozenset())), "transcript")
        self.assertEqual(self.why(g.Turn(False, False, None, frozenset())), "")  # not the end
        self.assertEqual(self.why(None), "")

    def test_every_change_between_the_last_stop_and_claudes_first_call(self):
        wrote = g.Turn(False, True, 1000.0, frozenset())
        self.assertEqual(self.why(wrote, [(b"a", 950.0), (b"b", 998.9)]), "time")
        for changes in ([(b"a", 999.5)],                   # within a second of the prompt
                        [(b"a", 950.0), (b"b", 1003.0)],   # after it: maybe Claude's
                        [(b"a", 895.0)],                   # while the last check ran
                        [(b"a", None)],                    # can't be dated
                        []):
            self.assertEqual(self.why(wrote, changes), "", changes)
        self.assertEqual(self.why(wrote._replace(since=None), [(b"a", 950.0)]), "")
        self.assertEqual(self.why(wrote, [(b"a", 950.0)], dict(self.MARK, t1=None)), "")

    def test_agents_and_background_jobs_rule_it_out(self):
        idle = g.Turn(True, False, None, frozenset())
        agent = [{"id": "a", "type": "subagent"}]
        job = [{"id": "j", "type": "shell"}]
        self.assertEqual(self.why(idle, tasks=agent), "")
        self.assertEqual(self.why(idle, mark=dict(self.MARK, agents=True)), "")
        self.assertEqual(self.why(idle, tasks=job), "transcript")  # tracked part unchanged
        self.assertEqual(self.why(idle, tasks=job, mark=dict(self.MARK, tracked="x")), "")
        self.assertEqual(self.why(idle, mark=dict(self.MARK, tasks=True, tracked="x")), "")

    def test_a_finished_jobs_output_rules_it_out(self):
        idle = g.Turn(True, False, None, frozenset())
        with mock.patch.object(g, "changes_since", return_value=[(b"gen.py", 950.0)]):
            self.assertEqual(g.not_claudes(None, self.MARK, self.TREE, [], lambda: idle,
                                           job_output=True), "")
            wrote = g.Turn(False, True, 1000.0, frozenset())
            self.assertEqual(g.not_claudes(None, self.MARK, self.TREE, [], lambda: wrote,
                                           job_output=True), "")
            self.assertEqual(g.not_claudes(None, self.MARK, self.TREE, [], lambda: wrote), "time")


class TestByproducts(unittest.TestCase):
    def test_caches_and_coverage_only(self):
        for rel in (b"src/__pycache__/app.cpython-39.pyc", b"x.pyc", b".pytest_cache/v/c",
                    b".mypy_cache/3.9/a.json", b".ruff_cache/x", b".coverage",
                    b".coverage.host.123", b"web/node_modules/.cache/babel/x", b".hypothesis/e"):
            self.assertTrue(g.byproduct(rel), rel)
        for rel in (b"NEWRED.py", b"tests/test_new.py", b"__pycache__", b"node_modules/.cache",
                    b"coverage.py", b"dev.log", b"src/cache/x.py"):
            self.assertFalse(g.byproduct(rel), rel)

    def test_only_byproducts(self):
        def tree(*entries):
            return g.Tree("d", "t", "u", "b", (), tuple(e[0] for e in entries), entries)
        pyc, new = (b"a/__pycache__/m.pyc", b"100644", b"1"), (b"NEWRED.py", b"100644", b"2")
        self.assertTrue(g.only_byproducts(tree(), tree(pyc)))
        self.assertFalse(g.only_byproducts(tree(), tree(pyc, new)))
        self.assertFalse(g.only_byproducts(tree(new), tree()))  # one went away
        self.assertFalse(g.only_byproducts(tree(new), tree((b"NEWRED.py", b"100644", b"3"))))
        self.assertTrue(g.only_byproducts(tree(new), tree((b"NEWRED.py", b"100644", b"3")),
                                          {b"NEWRED.py"}))  # a file the check rewrites

    def test_untracked_source_leaves_caches_out(self):
        def tree(*entries):
            return g.Tree("d", "t", "u", "b", (), tuple(e[0] for e in entries), entries)
        pyc, log = (b"a/__pycache__/m.pyc", b"100644", b"1"), (b"dev.log", b"100644", b"2")
        self.assertEqual(g.untracked_source(tree()), g.untracked_source(tree(pyc)))
        self.assertNotEqual(g.untracked_source(tree(pyc)), g.untracked_source(tree(pyc, log)))
        self.assertNotEqual(g.untracked_source(tree(log)),
                            g.untracked_source(tree((b"dev.log", b"100644", b"3"))))


class TestStatePaths(unittest.TestCase):
    def test_state_file_location_and_sanitising(self):
        env = {"CLAUDE_CONFIG_DIR": "/c", "HOME": "/h"}
        self.assertEqual(str(g.state_file("abc-1", env)), "/c/verify-gate/state/abc-1.json")
        self.assertEqual(str(g.state_file("../x/y", env)), "/c/verify-gate/state/.._x_y.json")
        self.assertEqual(str(g.state_file(None, {"HOME": "/h"})),
                         "/h/.claude/verify-gate/state/no-session.json")
        self.assertEqual(g.state_file("\udcff-s", env).name, "_-s.json")

    def test_log_file(self):
        self.assertEqual(str(g.log_file({"HOME": "/h"})), "/h/.claude/verify-gate/stop_gate.log")
        self.assertEqual(str(g.log_file({"CLAUDE_CONFIG_DIR": "/c", "HOME": "/h"})),
                         "/c/verify-gate/stop_gate.log")


if __name__ == "__main__":
    unittest.main()
