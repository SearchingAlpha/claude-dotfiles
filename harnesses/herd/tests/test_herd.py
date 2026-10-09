"""herd against a fake Claude config dir (live and dead sessions, transcripts) and a
private tmux server, so nothing touches the real ~/.claude or your tmux sessions."""

import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "herd.py"
sys.path.insert(0, str(ROOT))

import herd  # noqa: E402

FAKE_CLAUDE = r'''#!/usr/bin/env python3
# Registers itself like a Claude Code session, records how it was started, then sleeps.
import json, os, sys, time, uuid
from pathlib import Path
pid = os.getpid()
start = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()[19]
Path(os.environ["HERD_TEST_ARGV"]).write_text(json.dumps({"argv": sys.argv[1:], "env": sorted(os.environ)}))
now = int(time.time() * 1000)
entry = {"pid": pid, "procStart": start, "sessionId": str(uuid.uuid4()), "cwd": os.getcwd(),
         "name": sys.argv[sys.argv.index("--name") + 1], "nameSource": "user", "kind": "interactive",
         "status": "idle", "startedAt": now, "updatedAt": now, "statusUpdatedAt": now}
(Path(os.environ["CLAUDE_CONFIG_DIR"]) / "sessions" / f"{pid}.json").write_text(json.dumps(entry))
os.execvp("sleep", ["sleep", "600"])
'''


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="herd-"))
        self.claude = self.tmp / "claude"
        (self.claude / "sessions").mkdir(parents=True)
        (self.claude / "projects" / "-proj").mkdir(parents=True)
        self.procs = []
        self.saved = dict(os.environ)
        os.environ.update(CLAUDE_CONFIG_DIR=str(self.claude), TMUX_TMPDIR=str(self.tmp), NO_COLOR="1")
        os.environ.pop("TMUX", None)

    def tearDown(self):
        if shutil.which("tmux"):
            subprocess.run(["tmux", "kill-server"], capture_output=True)
        for p in self.procs:
            p.kill()
            p.wait()
        os.environ.clear()
        os.environ.update(self.saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def session(self, name, status, age=0, **extra) -> dict:
        p = subprocess.Popen(["sleep", "600"])
        self.procs.append(p)
        changed = int((time.time() - age) * 1000)
        entry = {"pid": p.pid, "procStart": herd.proc_start(p.pid), "sessionId": str(uuid.uuid4()),
                 "cwd": str(self.tmp), "name": name, "nameSource": "user", "kind": "interactive",
                 "status": status, "startedAt": changed, "updatedAt": changed,
                 "statusUpdatedAt": changed, **extra}
        (self.claude / "sessions" / f"{p.pid}.json").write_text(json.dumps(entry))
        return entry

    def transcript(self, entry, *records):
        lines = [json.dumps(r) for r in records]
        (self.claude / "projects" / "-proj" / f"{entry['sessionId']}.jsonl").write_text("\n".join(lines) + "\n")

    def herd(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ENTRY), *args], capture_output=True, text=True)

    def rows(self) -> list:
        r = self.herd("ls", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)


class TestList(Sandbox):
    def test_attention_order_and_liveness(self):
        self.session("working-one", "busy", age=600)
        self.session("done-one", "idle", age=30)
        self.session("blocked-new", "waiting", age=5, waitingFor="permission: Bash")
        self.session("blocked-old", "waiting", age=900, waitingFor="input needed")
        self.session("shell-one", "shell", age=1)
        dead = self.session("dead", "waiting")
        self.procs[-1].kill()
        self.procs[-1].wait()
        self.session("reused-pid", "waiting", procStart="1")
        self.session("spare", "idle", kind="bg", spare=True)
        self.session("supervisor", "busy", kind="daemon")
        rows = self.rows()
        self.assertEqual([r["name"] for r in rows],
                         ["blocked-old", "blocked-new", "done-one", "working-one", "shell-one"])
        self.assertEqual([r["state"] for r in rows],
                         ["blocked", "blocked", "done", "working", "working"])
        self.assertEqual(rows[0]["waiting_for"], "input needed")
        self.assertNotIn(dead["sessionId"], {r["session_id"] for r in rows})

    def test_transcript_title_and_last_prompt(self):
        e = self.session("pablomv-6a", "idle", nameSource="derived")
        big = {"type": "assistant", "message": {"content": [{"type": "text", "text": "x" * 300_000}]}}
        self.transcript(e, {"type": "user", "message": {"content": "old"}},
                        {"type": "last-prompt", "lastPrompt": "fix the\nflaky test"},
                        {"type": "ai-title", "aiTitle": "Flaky test fix"}, big)
        kept = self.session("my-name", "idle")
        self.transcript(kept, {"type": "ai-title", "aiTitle": "Ignored title"})
        rows = {r["session_id"]: r for r in self.rows()}
        self.assertEqual(rows[e["sessionId"]]["name"], "Flaky test fix")
        self.assertEqual(rows[e["sessionId"]]["prompt"], "fix the\nflaky test")
        self.assertEqual(rows[kept["sessionId"]]["name"], "my-name")

    def test_plain_table(self):
        self.session("needs-me", "waiting", waitingFor="permission: Bash")
        self.session("busy-bg", "busy", kind="bg")
        r = self.herd()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("\x1b[", r.stdout)
        head, blocked, bg = r.stdout.splitlines()
        self.assertTrue(head.startswith("STATE"))
        self.assertTrue(blocked.startswith("blocked") and blocked.endswith("permission: Bash"))
        self.assertIn("busy-bg (bg)", bg)

    def test_empty(self):
        self.assertEqual(self.herd().stdout, "no live Claude Code sessions\n")

    def test_go_by_session_prefix_outside_tmux(self):
        # The dashboard copies `herd go <8-char prefix>`; outside tmux that must say why, not "no session".
        e = self.session("plain", "idle")
        r = self.herd("go", e["sessionId"][:8])
        self.assertEqual(r.returncode, 1)
        self.assertIn("not running in tmux", r.stderr)
        self.assertIn("no session", self.herd("go", e["sessionId"][:3]).stderr)


class TestHelpers(unittest.TestCase):
    def test_tmux_target_walks_ancestors(self):
        child = subprocess.Popen(["sleep", "30"])
        try:
            self.assertEqual(herd.tmux_target(child.pid, {os.getpid(): "s:0.0"}), "s:0.0")
            self.assertIsNone(herd.tmux_target(child.pid, {}))
        finally:
            child.kill()
            child.wait()

    def test_short_dir(self):
        home = os.path.realpath(Path.home())
        self.assertEqual(herd.short_dir(home, 24), "~")
        self.assertEqual(herd.short_dir(home + "/a/b", 24), "~/a/b")
        self.assertEqual(herd.short_dir("/opt/" + "x" * 30 + "/repo", 12), "…" + ("x" * 30 + "/repo")[-11:])

    def test_ago(self):
        self.assertEqual([herd.ago(s) for s in (5, 125, 7300, 200000)], ["5s", "2m", "2h", "2d"])

    def test_grow_reads_only_new_complete_lines(self):
        tmp = Path(tempfile.mkdtemp(prefix="herd-grow-"))
        try:
            path, cache = tmp / "t.jsonl", {}
            grow = lambda: herd._grow(path, cache, (b"hit",), list, lambda ln, acc: acc.append(ln))  # noqa: E731
            path.write_bytes(b"hit 1\nmiss\nhit 2\nhit 3 half")
            self.assertEqual(grow(), [b"hit 1", b"hit 2"])
            with open(path, "ab") as f:
                f.write(b" done\nhit 4\n")
            self.assertEqual(grow(), [b"hit 1", b"hit 2", b"hit 3 half done", b"hit 4"])
            self.assertEqual(cache[path][1], path.stat().st_size)
            path.write_bytes(b"hit a\n")                         # rewritten shorter: start over
            self.assertEqual(grow(), [b"hit a"])
            other = tmp / "new.jsonl"
            other.write_bytes(b"hit a\nhit b\nhit c\n")
            os.replace(other, path)                              # replaced (new inode): start over
            self.assertEqual(grow(), [b"hit a", b"hit b", b"hit c"])
            self.assertEqual(herd._grow(tmp / "gone", cache, (b"hit",), list, None), [])
            saved, herd.BLOCK = herd.BLOCK, 4            # lines longer than a read block
            try:
                self.assertEqual(herd._grow(path, {}, (b"hit",), list, lambda ln, acc: acc.append(ln)),
                                 [b"hit a", b"hit b", b"hit c"])
            finally:
                herd.BLOCK = saved
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_first_stamp_skips_records_without_one(self):
        tmp = Path(tempfile.mkdtemp(prefix="herd-stamp-"))
        try:
            path = tmp / "agent.jsonl"
            write_jsonl(path, {"type": "fork-context-ref", "agentId": "a"},
                        {"type": "user", "timestamp": "2026-10-08T18:42:41.000Z"})
            self.assertEqual(herd._first_stamp(path), herd._epoch("2026-10-08T18:42:41.000Z"))
            self.assertEqual(herd._first_stamp(tmp / "missing.jsonl"), 0.0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def tool_turn(name="Bash", **inp) -> dict:
    return {"type": "assistant", "message": {"stop_reason": "tool_use", "content": [
        {"type": "text", "text": "…"}, {"type": "tool_use", "name": name, "input": inp}]}}


def text_turn(text="all done", stop="end_turn") -> dict:
    return {"type": "assistant", "message": {"stop_reason": stop, "content": [{"type": "text", "text": text}]}}


def user_text(text) -> dict:
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def write_jsonl(path: Path, *records, age=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    if age:
        t = time.time() - age
        os.utime(path, (t, t))


SCRIPT = """export const meta = {
  name: 'my-flow',
  description: 'Build then verify',
  phases: [
    { title: 'Build', detail: 'two builders' },
    { title: "Verify" },
  ],
}
const x = phase('Build')
"""


class TestWorkflows(Sandbox):
    def setUp(self):
        super().setUp()
        # Started well before the fixtures' file times, or its old runs read as orphaned.
        self.s = self.session("lead", "busy", startedAt=int((time.time() - 3 * 3600) * 1000))
        self.dir = self.claude / "projects" / "-proj" / self.s["sessionId"]

    def detail(self) -> dict:
        return herd.sessions(detail=True)[0]

    def test_running_workflow_from_script_and_journal(self):
        run = "wf_1234abcd-5ef"
        (self.dir / "workflows" / "scripts").mkdir(parents=True)
        (self.dir / "workflows" / "scripts" / f"my-flow-{run}.js").write_text(SCRIPT)
        rd = self.dir / "subagents" / "workflows" / run
        write_jsonl(rd / "journal.jsonl", {"type": "launched"},
                    {"type": "started", "key": "k1", "agentId": "a1", "label": "build:a", "phase": "Build"},
                    {"type": "result", "key": "k1", "agentId": "a1", "result": {"big": "x" * 1000}},
                    {"type": "started", "key": "k2", "agentId": "a2", "label": "build:b", "phase": "Build"},
                    {"type": "started", "key": "k3", "agentId": "a3", "label": "check", "phase": "Verify"},
                    {"type": "failed", "key": "k3", "agentId": "a3"})
        write_jsonl(rd / "agent-a2.jsonl", user_text("go"), tool_turn(description="run the tests"))
        [w] = self.detail()["workflows"]
        self.assertEqual((w["name"], w["description"], w["status"], w["agent_count"]),
                         ("my-flow", "Build then verify", "running", 3))
        build, verify = w["phases"]
        self.assertEqual((build["title"], verify["title"]), ("Build", "Verify"))
        self.assertEqual(build["counts"]["done"], 1)
        b = next(a for a in build["agents"] if a["id"] == "a2")
        self.assertEqual((b["status"], b["doing"]), ("running", "Bash: run the tests"))
        self.assertEqual(verify["agents"][0]["status"], "failed")

    def test_finished_stalled_and_aged_out(self):
        now = time.time()
        old = self.claude / "projects" / "-other" / self.s["sessionId"] / "workflows"
        old.mkdir(parents=True)                 # a second project dir of the same session
        (old / "wf_00000000-001.json").write_text(json.dumps({
            "workflowName": "nightly", "status": "completed", "startTime": int((now - 3600) * 1000),
            "durationMs": 600_000, "totalTokens": 1_500_000, "phases": [{"title": "Only"}]}))
        (old / "wf_00000000-002.json").write_text(json.dumps({
            "workflowName": "ancient", "status": "killed", "startTime": int((now - 3 * 86400) * 1000),
            "durationMs": 1000}))
        rd = self.dir / "subagents" / "workflows" / "wf_00000000-003"
        write_jsonl(rd / "agent-s1.jsonl", tool_turn(), age=1800)
        write_jsonl(rd / "journal.jsonl", {"type": "started", "key": "k", "agentId": "s1",
                                           "label": "slow", "phase": "Work"}, age=1800)
        by_id = {w["id"]: w for w in self.detail()["workflows"]}
        self.assertEqual(set(by_id), {"wf_00000000-001", "wf_00000000-003"})
        nightly = by_id["wf_00000000-001"]
        self.assertEqual((nightly["name"], nightly["status"], nightly["elapsed"], nightly["tokens"]),
                         ("nightly", "completed", 600, 1_500_000))
        stalled = by_id["wf_00000000-003"]
        self.assertEqual((stalled["name"], stalled["status"]), ("", "stalled"))   # never the run id
        self.assertEqual(stalled["phases"][0]["agents"][0]["status"], "stalled")
        self.assertEqual(stalled["problems"], {"stalled": 1, "failed": 0})

    def test_subagent_states(self):
        sub = self.dir / "subagents"
        cases = {
            "done": [text_turn()],
            "quiet": [text_turn(stop=None)],
            "handback": [tool_turn("SubagentHandback"), {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}],
            "stopped": [tool_turn(), user_text("[Request interrupted by user]")],
            "running": [tool_turn(description="lint")],
            "stalled": [tool_turn()],
            "aged": [text_turn()],
        }
        ages = {"stalled": 1800, "aged": 2 * 86400, "quiet": 120}
        for name, turns in cases.items():
            (sub / f"agent-{name}.meta.json").parent.mkdir(parents=True, exist_ok=True)
            (sub / f"agent-{name}.meta.json").write_text(json.dumps({"agentType": "Explore", "description": name}))
            write_jsonl(sub / f"agent-{name}.jsonl", *turns, age=ages.get(name, 0))
        (sub / "agent-wf.meta.json").write_text(json.dumps({"agentType": "workflow-subagent"}))
        got = {a["description"]: a["status"] for a in self.detail()["agents"]}
        self.assertEqual(got, {"done": "done", "quiet": "done", "handback": "done", "stopped": "stopped",
                               "running": "running", "stalled": "stalled"})

    def test_plain_list_skips_detail(self):
        self.assertNotIn("workflows", herd.sessions()[0])


def touch(path: Path, age=0, text="{}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    t = time.time() - age
    os.utime(path, (t, t))


def iso(age=0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - age))


def launch(script_path, age, resume="", tool_id="toolu_1") -> dict:
    inp = {"scriptPath": str(script_path), **({"resumeFromRunId": resume} if resume else {})}
    return {"type": "assistant", "timestamp": iso(age), "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "Workflow", "input": inp}]}}


def launched(run_dir, tool_id="toolu_1") -> dict:
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content":
            f"Workflow launched in background. Task ID: w1\nTranscript dir: {run_dir}\nScript file: x.js"}]}}


PHASES3 = """export const meta = {
  name: 'three',
  description: 'A then B then C',
  phases: [{ title: 'A' }, { title: 'B' }, { title: 'C' }],
}
"""


class TestTruth(Sandbox):
    """The rules that keep the dashboard from showing false problems (SPEC 5)."""

    def setUp(self):
        super().setUp()
        # The session's current process started an hour ago; older writes belong to a previous one.
        self.s = self.session("lead", "busy", startedAt=int((time.time() - 3600) * 1000))
        self.dir = self.claude / "projects" / "-proj" / self.s["sessionId"]

    def detail(self, sid=None) -> dict:
        return next(r for r in herd.sessions(detail=True) if r["session_id"] == (sid or self.s["sessionId"]))

    def wf(self, run, *events, age=0, script=None, name="flow", agents=(), meta_age=None):
        """A run: journal events, agent transcripts {id: (turns, age)}, optional script."""
        rd = self.dir / "subagents" / "workflows" / run
        write_jsonl(rd / "journal.jsonl", *events, age=age)
        for aid, (turns, a_age) in dict(agents).items():
            write_jsonl(rd / f"agent-{aid}.jsonl", *turns, age=a_age)
            touch(rd / f"agent-{aid}.meta.json", meta_age if meta_age is not None else a_age + 60)
        if script:
            touch(self.dir / "workflows" / "scripts" / f"{name}-{run}.js", meta_age or 0, script)
        return rd

    def started(self, key, label, phase, aid=None):
        return {"type": "started", "key": key, "agentId": aid or key, "label": label, "phase": phase}

    def test_orphaned_run_is_interrupted_with_frozen_elapsed(self):
        self.wf("wf_0000aaaa-001", self.started("k1", "build", "Build"), age=7200, script=SCRIPT,
                 name="kit", agents={"k1": ([tool_turn()], 7200)}, meta_age=3 * 3600)
        [w] = self.detail()["workflows"]
        self.assertEqual(w["status"], "interrupted")
        self.assertAlmostEqual(w["elapsed"], 3600, delta=5)      # start → last write, not → now
        self.assertAlmostEqual(w["ended_at"], time.time() - 7200, delta=5)
        self.assertEqual(w["phases"][0]["counts"]["interrupted"], 1)
        self.assertEqual((w["problems"], w["current_phase"]), ({"stalled": 0, "failed": 0}, None))

    def test_resumed_run_gets_name_phases_and_resumed_from(self):
        orig = "wf_0000aaaa-001"
        self.wf(orig, self.started("k1", "build", "Build"), age=7200, script=SCRIPT, name="kit",
                 agents={"k1": ([tool_turn()], 7200)}, meta_age=3 * 3600)
        script = self.dir / "workflows" / "scripts" / f"kit-{orig}.js"
        new = "wf_0000bbbb-002"
        self.wf(new, self.started("k2", "verify", "Verify"), agents={"k2": ([tool_turn()], 0)}, meta_age=58)
        self.transcript(self.s, user_text("resume it"), launch(script, 60, resume=orig))
        by_id = {w["id"]: w for w in self.detail()["workflows"]}
        w = by_id[new]
        self.assertEqual((w["name"], w["resumed_from"], w["description"]), ("kit", orig, "Build then verify"))
        self.assertEqual([p["title"] for p in w["phases"]], ["Build", "Verify"])
        self.assertEqual((w["phase_total"], w["current_phase"], w["status"]), (2, 1, "running"))
        self.assertEqual((by_id[orig]["superseded_by"], by_id[orig]["status"]), (new, "interrupted"))

    def test_relaunch_from_copied_script_links_by_tool_result(self):
        orig = "wf_0000aaaa-001"
        self.wf(orig, self.started("k1", "build", "Build"), age=7200, script=SCRIPT, name="my-flow",
                 agents={"k1": ([tool_turn()], 7200)}, meta_age=3 * 3600)
        copy = self.tmp / "continue.js"
        copy.write_text(SCRIPT)
        new = "wf_0000bbbb-002"
        rd = self.wf(new, self.started("k2", "verify", "Verify"), agents={"k2": ([tool_turn()], 0)}, meta_age=58)
        # A later unlinked call, still before the run started, must not win over the linked one.
        self.transcript(self.s, launch(copy, 60, tool_id="toolu_9"), launched(rd, "toolu_9"),
                        launch(self.tmp / "other-wf_0000cccc-003.js", 59, tool_id="toolu_8"))
        by_id = {w["id"]: w for w in self.detail()["workflows"]}
        self.assertEqual((by_id[new]["name"], by_id[new]["resumed_from"], by_id[new]["phase_total"]),
                         ("my-flow", orig, 2))
        self.assertEqual(by_id[orig]["superseded_by"], new)

    def test_phase_total_null_without_script(self):
        self.wf("wf_0000aaaa-001", self.started("k1", "x", "Work"), agents={"k1": ([tool_turn()], 0)})
        [w] = self.detail()["workflows"]
        self.assertEqual((w["name"], w["phase_total"], w["resumed_from"]), ("", None, ""))
        self.assertEqual(([p["title"] for p in w["phases"]], w["current_phase"]), (["Work"], 0))

    def test_retried_agent_is_not_stalled(self):
        self.wf("wf_0000aaaa-001", self.started("k1", "engine-api", "Service"),
                 self.started("k2", "engine-api", "Service"), {"type": "result", "key": "k2"},
                 self.started("k3", "audit", "Audit"),
                 agents={"k1": ([tool_turn()], 1800), "k3": ([tool_turn()], 0)})
        [w] = self.detail()["workflows"]
        service, audit = w["phases"]
        self.assertEqual({k: v for k, v in service["counts"].items() if v}, {"done": 1, "retried": 1})
        [old] = service["agents"]                   # done agents are counted, not listed
        self.assertEqual((old["id"], old["status"], old["superseded"]), ("k1", "stalled", True))
        self.assertGreater(old["updated_at"], 0)
        self.assertEqual((w["problems"], w["agent_count"], w["current_phase"]),
                         ({"stalled": 0, "failed": 0}, 2, 1))

    def test_current_phase_is_first_at_work_and_passed_failures_are_no_problem(self):
        self.wf("wf_0000aaaa-001",
                 self.started("a1", "a-fail", "A"), {"type": "failed", "key": "a1"},
                 self.started("b1", "b-run", "B"), self.started("b2", "b-fail", "B"), {"type": "failed", "key": "b2"},
                 self.started("c1", "c-run", "C"),
                 script=PHASES3, name="three",
                 agents={"b1": ([tool_turn(description="b")], 0), "c1": ([tool_turn(description="c")], 0)})
        [w] = self.detail()["workflows"]
        self.assertEqual((w["phase_total"], w["current_phase"]), (3, 1))
        self.assertEqual(w["problems"], {"stalled": 0, "failed": 1})       # B's, not A's
        self.assertEqual(w["phases"][0]["counts"]["failed"], 1)
        self.assertEqual(w["phases"][2]["agents"][0]["doing"], "Bash: c")

    def test_finished_run_lists_only_failed_agents(self):
        rd = self.wf("wf_0000aaaa-001", self.started("a1", "ok", "A"), {"type": "result", "key": "a1"},
                      self.started("a2", "bad", "A"), {"type": "failed", "key": "a2"}, script=PHASES3, name="three")
        (self.dir / "workflows" / "wf_0000aaaa-001.json").write_text(json.dumps(
            {"workflowName": "three", "status": "completed", "startTime": int((time.time() - 600) * 1000),
             "durationMs": 60_000, "phases": [{"title": "A"}, {"title": "B"}, {"title": "C"}]}))
        self.assertTrue(rd.is_dir())
        [w] = self.detail()["workflows"]
        self.assertEqual([a["label"] for a in w["phases"][0]["agents"]], ["bad"])
        self.assertEqual((w["phases"][0]["counts"]["done"], w["current_phase"], w["problems"]),
                         (1, None, {"stalled": 0, "failed": 0}))

    def test_parked_husk_is_dropped(self):
        self.session("job", "busy", kind="bg", jobId="2cf53fe7")
        husk = self.session("husk", "busy", parkedJobId="2cf53fe7")
        kept = self.session("parked-elsewhere", "busy", parkedJobId="gone0000")
        ids = {r["session_id"] for r in self.rows()}
        self.assertNotIn(husk["sessionId"], ids)
        self.assertIn(kept["sessionId"], ids)
        self.assertEqual(len(ids), 3)

    def test_one_row_per_session_most_urgent_kept(self):
        a = self.session("twice", "busy")
        self.session("twice", "waiting", sessionId=a["sessionId"], waitingFor="permission prompt")
        mine = [r for r in self.rows() if r["session_id"] == a["sessionId"]]
        self.assertEqual([r["state"] for r in mine], ["blocked"])

    def test_asking_reply_quiet_and_times(self):
        blocked = self.session("asks", "waiting", age=30, waitingFor="permission prompt")
        # A permission prompt shows the command itself, not the tool call's description.
        self.transcript(blocked, user_text("clone it"),
                        tool_turn(command="git clone https://x/y.git", description="Clone the repo"))
        done = self.session("answers", "idle", age=10)
        self.transcript(done, user_text("why?"), text_turn("Because  the\ncache was stale.\n\nDetails follow."))
        write_jsonl(self.claude / "projects" / "-proj" / f"{self.s['sessionId']}.jsonl", user_text("x"), age=1200)
        b, d, w = self.detail(blocked["sessionId"]), self.detail(done["sessionId"]), self.detail()
        self.assertEqual((b["asking"], b["reply"]), ("Bash: git clone https://x/y.git", ""))
        self.assertEqual((d["asking"], d["reply"]), ("", "Because the cache was stale."))
        self.assertEqual((w["asking"], w["reply"], w["quiet"]), ("", "", True))
        self.assertAlmostEqual(w["last_activity"], time.time() - 1200, delta=5)
        self.assertEqual(b["state_changed_at"], blocked["statusUpdatedAt"] // 1000)
        self.assertEqual(w["started_at"], self.s["startedAt"] // 1000)
        self.assertFalse(d["quiet"])
        # A live run keeps a silent session from reading as quiet.
        self.wf("wf_0000aaaa-001", self.started("k1", "x", "Work"), agents={"k1": ([tool_turn()], 0)})
        self.assertFalse(self.detail()["quiet"])

    def test_asking_is_only_a_call_still_pending(self):
        # Blocked by a dialog after the last call already ran: nothing to approve.
        ran = self.session("dialog", "waiting", waitingFor="dialog open")
        call = tool_turn(command="git status --short")
        call["message"]["content"][1]["id"] = "toolu_1"
        result = {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "M x"}]}}
        self.transcript(ran, user_text("status please"), call, result, text_turn("Clean enough."))
        self.assertEqual(self.detail(ran["sessionId"])["asking"], "")
        # Of two parallel calls, the one without a result is the one asking.
        both = self.session("parallel", "waiting", waitingFor="permission prompt")
        second = tool_turn(command="rm -rf build")
        second["message"]["content"][1]["id"] = "toolu_2"
        self.transcript(both, user_text("go"), second, call, result)
        self.assertEqual(self.detail(both["sessionId"])["asking"], "Bash: rm -rf build")


def stamped(rec: dict, age=0) -> dict:
    return {**rec, "timestamp": iso(age), "uuid": str(uuid.uuid4())}


def agent(sub: Path, aid: str, meta: dict, *turns, age=0, start_age=None, meta_age=0) -> Path:
    """A subagent: meta file plus a transcript whose first record is stamped start_age ago."""
    first = stamped(user_text("go"), start_age if start_age is not None else age + 60)
    write_jsonl(sub / f"agent-{aid}.jsonl", first, *turns, age=age)
    touch(sub / f"agent-{aid}.meta.json", meta_age, json.dumps({"agentType": "general-purpose", **meta}))
    return sub / f"agent-{aid}.jsonl"


class TestGraphData(Sandbox):
    """What the graph needs from subagents() and workflows()."""

    def setUp(self):
        super().setUp()
        self.s = self.session("lead", "busy", startedAt=int((time.time() - 3 * 3600) * 1000))
        self.sid = self.s["sessionId"]
        self.sub = self.claude / "projects" / "-proj" / self.sid / "subagents"

    def agents(self, **kw) -> dict:
        return {a["id"]: a for a in herd.subagents(self.sid, True, time.time(), time.time() - 3 * 3600, **kw)}

    def test_nested_agents_forks_and_start_from_transcript(self):
        agent(self.sub, "lead1", {"description": "plan it", "toolUseId": "toolu_1", "model": "sonnet"},
              tool_turn(description="read"), start_age=600)          # meta rewritten just now
        agent(self.sub, "kid1", {"description": "dig", "parentAgentId": "lead1", "spawnDepth": 2},
              text_turn(), age=120, start_age=300, meta_age=120)
        fork = self.sub / "agent-fork1.jsonl"
        write_jsonl(fork, {"type": "fork-context-ref", "agentId": "fork1"}, stamped(user_text("x"), 200),
                    tool_turn(description="fork work"))
        touch(self.sub / "agent-fork1.meta.json", 0, json.dumps({"agentType": "fork", "isFork": True}))
        got = self.agents()
        self.assertEqual([a["id"] for a in herd.subagents(self.sid, True, time.time())], ["lead1", "kid1", "fork1"])
        self.assertAlmostEqual(got["lead1"]["started_at"], time.time() - 600, delta=5)
        self.assertEqual((got["lead1"]["parent"], got["lead1"]["tool_use_id"], got["lead1"]["model"]),
                         ("", "toolu_1", "sonnet"))
        self.assertEqual((got["kid1"]["parent"], got["kid1"]["status"]), ("lead1", "done"))
        self.assertAlmostEqual(got["kid1"]["ended_at"], time.time() - 120, delta=5)
        self.assertEqual((got["fork1"]["fork"], got["fork1"]["status"], got["fork1"]["ended_at"]), (True, "running", 0))

    def test_finished_ancestor_kept_only_for_the_graph(self):
        agent(self.sub, "old", {"description": "long done"}, text_turn(), age=2 * 86400)
        agent(self.sub, "kid", {"description": "still at it", "parentAgentId": "old"}, tool_turn())
        self.assertEqual({k: a["parent"] for k, a in self.agents().items()}, {"kid": ""})
        self.assertEqual({k: a["parent"] for k, a in self.agents(ancestors=True).items()},
                         {"old": "", "kid": "old"})

    def test_copy_in_another_project_dir_counts_once(self):
        agent(self.sub, "dup", {"description": "short copy"}, text_turn())
        other = self.claude / "projects" / "-other" / self.sid / "subagents"
        agent(other, "dup", {"description": "full copy"}, tool_turn(), tool_turn(), text_turn())
        [a] = self.agents().values()
        self.assertEqual(a["description"], "full copy")

    def test_workflow_graph_lists_a_few_of_every_phase(self):
        rd = self.claude / "projects" / "-proj" / self.sid / "subagents" / "workflows" / "wf_0000aaaa-001"
        events = []
        for i in range(20):
            events += [{"type": "started", "key": f"d{i}", "agentId": f"d{i}", "label": f"done-{i}", "phase": "Build"},
                       {"type": "result", "key": f"d{i}"}]
        events += [{"type": "started", "key": "f", "agentId": "f", "label": "broke", "phase": "Build"},
                   {"type": "failed", "key": "f"},
                   {"type": "started", "key": "r", "agentId": "r", "label": "busy", "phase": "Build"}]
        write_jsonl(rd / "journal.jsonl", *events)
        write_jsonl(rd / "agent-r.jsonl", tool_turn(description="build"))
        now, start = time.time(), time.time() - 3 * 3600
        [w] = herd.workflows(self.sid, True, now, start, graph=True)
        [build] = w["phases"]
        self.assertEqual([a["label"] for a in build["agents"]],
                         ["done-16", "done-17", "done-18", "done-19", "broke", "busy"])
        self.assertEqual(build["more"], {"done": 16})
        [plain] = herd.workflows(self.sid, True, now, start)[0]["phases"]
        self.assertEqual([a["label"] for a in plain["agents"]], ["broke", "busy"])
        self.assertNotIn("more", plain)


def send_msg(tool_id, to, summary, age=0) -> dict:
    return stamped({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "SendMessage",
         "input": {"to": to, "summary": summary, "message": "the body is never shown"}}]}}, age)


def send_result(tool_id, msg_id=None, age=0) -> dict:
    res = {"success": True, "msg_id": msg_id} if msg_id else {"message": "Message queued for delivery"}
    return stamped({"type": "user", "toolUseResult": res, "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": [{"type": "text", "text": "ok"}]}]}}, age)


def peer_in(msg_id, age=0, attachment=False, pid=0, proc_start="") -> dict:
    origin = {"kind": "peer", "from": "uds:/run/x.sock", "verifiedPeerPid": pid,
              "verifiedPeerProcStart": proc_start, "msg_id": msg_id, "name": "someone", "body": "hi"}
    if attachment:
        return stamped({"type": "attachment", "attachment": {"type": "queued_command", "prompt": "hi",
                                                             "origin": origin}}, age)
    return stamped({"type": "user", "isMeta": True, "origin": origin,
                    "message": {"content": "Another Claude session sent a message: hi"}}, age)


def handback(aid, age=0) -> dict:
    return stamped({"type": "user", "isMeta": True, "message": {"content": "[Subagent hand-back] done"},
                    "origin": {"kind": "peer", "from": aid, "senderTaskId": aid, "handback": True}}, age)


def continued_in(sid, to, age=0) -> dict:
    return {"type": "continued-in", "timestamp": iso(age), "sessionId": sid, "continuedInSessionId": to}


def task_note(tool_use_id, status, age=0) -> dict:
    text = (f"<task-notification>\n<task-id>a1</task-id>\n<tool-use-id>{tool_use_id}</tool-use-id>\n"
            f"<status>{status}</status>\n<summary>Agent finished</summary>\n</task-notification>")
    return stamped({"type": "user", "origin": {"kind": "task-notification"}, "message": {"content": text}}, age)


class TestGraph(Sandbox):
    """/api/graph: spawn trees per session and the exact joins between agents."""

    def lead(self, name="lead", age=3 * 3600, **extra) -> dict:
        return self.session(name, "busy", startedAt=int((time.time() - age) * 1000), **extra)

    def sub(self, s) -> Path:
        return self.claude / "projects" / "-proj" / s["sessionId"] / "subagents"

    def tx(self, sid, *records, age=0, project="-proj"):
        write_jsonl(self.claude / "projects" / project / f"{sid}.jsonl", *records, age=age)

    def graph(self) -> dict:
        g = herd.graph()
        self.nodes = {n["id"]: n for n in g["nodes"]}
        self.edges = {e["id"]: e for e in g["edges"]}
        return g

    def test_spawn_tree_in_preorder(self):
        s = self.lead()
        sub = self.sub(s)
        agent(sub, "lead1", {"description": "plan"}, tool_turn(), start_age=900)
        agent(sub, "kid1", {"description": "dig", "parentAgentId": "lead1"}, text_turn(), age=60, start_age=600)
        agent(sub, "solo", {"description": "check"}, tool_turn(), start_age=300)
        rd = sub / "workflows" / "wf_0000aaaa-001"
        write_jsonl(rd / "journal.jsonl",
                    {"type": "started", "key": "b1", "agentId": "b1", "label": "build", "phase": "Build"},
                    {"type": "result", "key": "b1"},
                    {"type": "started", "key": "v1", "agentId": "v1", "label": "verify", "phase": "Verify"})
        write_jsonl(rd / "agent-v1.jsonl", tool_turn(description="tests"))
        one = sub / "workflows" / "wf_0000bbbb-002"
        write_jsonl(one / "journal.jsonl", {"type": "started", "key": "o1", "agentId": "o1", "label": "only",
                                            "phase": "Work"})
        write_jsonl(one / "agent-o1.jsonl", tool_turn())
        g = self.graph()
        sid = s["sessionId"]
        got = [(n["id"], n["parent"], n["depth"]) for n in g["nodes"]]
        self.assertEqual(got[:4], [(f"s:{sid}", "", 0), ("a:lead1", f"s:{sid}", 1), ("a:kid1", "a:lead1", 2),
                                   ("a:solo", f"s:{sid}", 1)])
        run = [n["id"] for n in g["nodes"] if n["id"].startswith(("w:", "p:", "g:"))]
        self.assertEqual(run, ["w:wf_0000aaaa-001", "p:wf_0000aaaa-001:0", "g:wf_0000aaaa-001:b1",
                               "p:wf_0000aaaa-001:1", "g:wf_0000aaaa-001:v1",
                               "w:wf_0000bbbb-002", "g:wf_0000bbbb-002:o1"])   # one phase: no phase node
        self.assertEqual(self.nodes["g:wf_0000bbbb-002:o1"]["parent"], "w:wf_0000bbbb-002")
        self.assertEqual(self.nodes["w:wf_0000aaaa-001"]["phases"][0]["counts"]["done"], 1)
        seen = set()
        for n in g["nodes"]:                    # every parent comes before its children
            self.assertTrue(n["parent"] == "" or n["parent"] in seen, n["id"])
            seen.add(n["id"])

    def test_finished_children_fold_into_more(self):
        s = self.lead()
        sub = self.sub(s)
        for i in range(9):
            agent(sub, f"done{i}", {"description": f"d{i}"}, text_turn(), age=3000 - i * 100, start_age=3600 - i * 100)
        agent(sub, "busy", {"description": "still"}, tool_turn(), start_age=4000)
        self.graph()
        kids = [n["id"] for n in self.nodes.values() if n["parent"] == f"s:{s['sessionId']}"]
        self.assertEqual(kids, ["a:busy", "a:done4", "a:done5", "a:done6", "a:done7", "a:done8",
                                f"more:s:{s['sessionId']}"])
        more = self.nodes[f"more:s:{s['sessionId']}"]
        self.assertEqual((more["count"], more["counts"], more["kinds"]), (4, {"done": 4}, {"subagent": 4}))

    def test_messages_join_on_msg_id_not_names(self):
        a, b = self.lead("twin", age=3000), self.lead("bee", age=2000)
        c = self.lead("twin", age=1000)
        self.tx(a["sessionId"], send_msg("t1", "bee", "first"), send_result("t1", "m1"),
                send_msg("t2", "bee", "second"), send_result("t2", "m2"))
        self.tx(b["sessionId"], peer_in("m1", 50), peer_in("m2", 10, attachment=True),
                peer_in("m9", 5, pid=c["pid"], proc_start=c["procStart"]),      # sender's transcript unseen
                peer_in("m1", 1))                                               # the same message twice
        self.graph()
        ab = self.edges[f"m:{a['sessionId']}:{b['sessionId']}"]
        self.assertEqual((ab["count"], ab["summary"], ab["from"], ab["to"]),
                         (2, "second", f"s:{a['sessionId']}", f"s:{b['sessionId']}"))
        self.assertEqual(self.edges[f"m:{c['sessionId']}:{b['sessionId']}"]["count"], 1)
        self.assertEqual(len([e for e in self.edges.values() if e["kind"] == "message"]), 2)

    def test_handbacks_and_coordinator_messages(self):
        s = self.lead()
        sid = s["sessionId"]
        sub = self.sub(s)
        agent(sub, "X", {"description": "lead agent"}, tool_turn(), start_age=600)
        agent(sub, "Y", {"description": "nested", "parentAgentId": "X"}, text_turn(), age=30, start_age=300)
        with open(sub / "agent-X.jsonl", "a") as f:
            f.write(json.dumps(handback("Y", 30)) + "\n")
        self.tx(sid, handback("X", 200), send_msg("t1", "X", "also check B"), send_result("t1"), handback("X", 20))
        self.graph()
        self.assertEqual((self.edges["h:X"]["count"], self.edges["h:X"]["from"], self.edges["h:X"]["to"]),
                         (2, "a:X", f"s:{sid}"))
        self.assertEqual((self.edges["k:X"]["count"], self.edges["k:X"]["from"], self.edges["k:X"]["summary"]),
                         (1, f"s:{sid}", "also check B"))
        self.assertEqual((self.edges["h:Y"]["from"], self.edges["h:Y"]["to"]), ("a:Y", "a:X"))

    def test_bg_handoff_with_copied_history_counts_once(self):
        b = self.lead("job", age=1800, kind="bg")
        a_sid = str(uuid.uuid4())
        p = self.lead("peer", age=600)
        history = [stamped(user_text("start"), 7200), handback("Z", 4000), peer_in("m1", 3000),
                   send_msg("t1", "peer", "ping"), send_result("t1", "m2", 2990)]
        self.tx(a_sid, *history, continued_in(a_sid, b["sessionId"], 1900), age=1900)
        self.tx(b["sessionId"], *history, stamped(user_text("carry on"), 100))
        self.tx(p["sessionId"], peer_in("m2", 2980))
        for owner in (a_sid, b["sessionId"]):                   # the job's folder copies the agent too
            agent(self.claude / "projects" / "-proj" / owner / "subagents", "Z", {"description": "z"},
                  text_turn(), age=3900, start_age=5000)
        g = self.graph()
        roots = [n["id"] for n in g["nodes"] if n["kind"] == "session"]
        self.assertEqual(roots, [f"s:{a_sid}", f"s:{b['sessionId']}", f"s:{p['sessionId']}"])
        a = self.nodes[f"s:{a_sid}"]
        self.assertEqual((a["state"], a["live"], a["handed_off"], a["name"]), ("ended", False, True, "Unnamed session"))
        self.assertTrue(self.nodes[f"s:{b['sessionId']}"]["bg"])
        self.assertIn(f"c:{a_sid}:{b['sessionId']}", self.edges)
        self.assertEqual(self.nodes["a:Z"]["parent"], f"s:{a_sid}")
        self.assertEqual(self.edges["h:Z"]["count"], 1)
        self.assertEqual(self.edges[f"m:{a_sid}:{p['sessionId']}"]["count"], 1)
        self.assertNotIn(f"m:{b['sessionId']}:{p['sessionId']}", self.edges)

    def test_window_and_ended_peers(self):
        s = self.lead()
        recent, old = str(uuid.uuid4()), str(uuid.uuid4())
        self.tx(recent, {"type": "ai-title", "aiTitle": "Recent peer"},
                stamped({"type": "user", "cwd": "/srv/x", "message": {"content": "x"}}, 7300),
                send_msg("t1", "lead", "hello"), send_result("t1", "m1", 7200), age=7200)
        self.tx(old, send_msg("t1", "lead", "old news"), send_result("t1", "m0", 30 * 3600), age=30 * 3600)
        self.tx(s["sessionId"], peer_in("m0", 30 * 3600), peer_in("m1", 7100))
        agent(self.sub(s), "aged", {"description": "long gone"}, text_turn(), age=2 * 86400)
        g = self.graph()
        self.assertEqual([n["id"] for n in g["nodes"] if n["kind"] == "session"],
                         [f"s:{s['sessionId']}", f"s:{recent}"])
        peer = self.nodes[f"s:{recent}"]
        self.assertEqual((peer["name"], peer["state"], peer["cwd"]), ("Recent peer", "ended", "/srv/x"))
        self.assertEqual(self.edges[f"m:{recent}:{s['sessionId']}"]["count"], 1)
        self.assertNotIn("a:aged", self.nodes)

    def test_task_notification_overrides_a_stale_status(self):
        s = self.lead()
        sub = self.sub(s)
        agent(sub, "X", {"description": "x", "toolUseId": "toolu_X"}, tool_turn(), age=120)
        agent(sub, "W", {"description": "w", "toolUseId": "toolu_W"}, tool_turn(), age=0)
        self.tx(s["sessionId"], task_note("toolu_X", "failed", 60), task_note("toolu_W", "completed", 600))
        self.graph()
        self.assertEqual(self.nodes["a:X"]["status"], "failed")
        self.assertAlmostEqual(self.nodes["a:X"]["ended_at"], time.time() - 60, delta=5)
        self.assertEqual(self.nodes["a:W"]["status"], "running")    # it wrote again after the notice

    def test_roots_keep_their_place_when_one_blocks(self):
        first, second = self.lead("first", age=900), self.lead("second", age=300)
        self.session("third", "waiting", startedAt=int((time.time() - 600) * 1000), waitingFor="permission")
        names = [n["name"] for n in self.graph()["nodes"] if n["kind"] == "session"]
        self.assertEqual(names, ["first", "third", "second"])
        self.assertTrue(first and second)

    def test_new_messages_are_read_incrementally(self):
        a, b = self.lead("a", age=900), self.lead("b", age=300)
        self.tx(a["sessionId"], send_msg("t1", "b", "one"), send_result("t1", "m1"))
        self.tx(b["sessionId"], peer_in("m1"))
        self.graph()
        key = f"m:{a['sessionId']}:{b['sessionId']}"
        self.assertEqual(self.edges[key]["count"], 1)
        with open(self.claude / "projects" / "-proj" / f"{a['sessionId']}.jsonl", "a") as f:
            f.write(json.dumps(send_msg("t2", "b", "two")) + "\n" + json.dumps(send_result("t2", "m2")) + "\n")
        with open(self.claude / "projects" / "-proj" / f"{b['sessionId']}.jsonl", "a") as f:
            f.write(json.dumps(peer_in("m2")) + "\n")
        self.graph()
        self.assertEqual((self.edges[key]["count"], self.edges[key]["summary"]), (2, "two"))


class TestServer(Sandbox):
    def setUp(self):
        super().setUp()
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "config")
        self.session("one", "waiting", waitingFor="dialog open")
        self.server = herd.ThreadingHTTPServer(("127.0.0.1", 0), herd.Handler)
        self.server.token = herd.token()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def get(self, path, host="127.0.0.1", cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        headers = {"Host": f"{host}:{self.server.server_address[1]}"}
        if cookie:
            headers["Cookie"] = f"herd={cookie}"
        conn.request("GET", path, headers=headers)
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def test_token_is_private_and_stable(self):
        path = self.tmp / "config" / "herd" / "token"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(herd.token(), self.server.token)

    def test_auth_and_api(self):
        tok = self.server.token
        self.assertEqual(self.get("/")[0], 401)
        self.assertEqual(self.get("/api/sessions", cookie="wrong")[0], 401)
        self.assertEqual(self.get(f"/?t={tok}", host="evil.example")[0], 403)
        status, headers, _ = self.get(f"/?t={tok}")
        self.assertEqual(status, 302)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        status, _, body = self.get("/api/sessions", cookie=tok)
        self.assertEqual(status, 200)
        [s] = json.loads(body)["sessions"]
        self.assertEqual((s["state"], s["waiting_for"], s["workflows"], s["agents"]),
                         ("blocked", "dialog open", [], []))
        self.assertEqual(self.get("/", cookie=tok)[0], 200)
        self.assertEqual(self.get("/static/app.js", cookie=tok)[0], 200)
        self.assertEqual(self.get("/static/..%2fherd.py", cookie=tok)[0], 404)
        self.assertEqual(self.get("/static/../herd.py", cookie=tok)[0], 404)

    def test_graph_endpoint(self):
        tok = self.server.token
        self.assertEqual(self.get("/api/graph")[0], 401)
        self.assertEqual(self.get("/api/graph", host="evil.example", cookie=tok)[0], 403)
        status, headers, body = self.get("/api/graph", cookie=tok)
        self.assertEqual((status, headers["Content-Type"]), (200, "application/json"))
        g = json.loads(body)
        self.assertEqual(set(g), {"now", "host", "window", "nodes", "edges"})
        [n] = g["nodes"]
        self.assertEqual((n["kind"], n["state"], n["waiting_for"], n["parent"]), ("session", "blocked", "dialog open", ""))
        status, headers, body = self.get("/static/graph.js", cookie=tok)
        self.assertEqual((status, headers["Content-Type"]), (200, "text/javascript; charset=utf-8"))
        self.assertIn(b"const graphView", body)


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class TestTmux(Sandbox):
    def setUp(self):
        super().setUp()
        fake = self.tmp / "fake-claude"
        fake.write_text(FAKE_CLAUDE)
        fake.chmod(0o755)
        self.argv_file = self.tmp / "argv.json"
        os.environ.update(HERD_CLAUDE=str(fake), HERD_TEST_ARGV=str(self.argv_file),
                          CLAUDECODE="1", CLAUDE_CODE_SESSION_ID="parent-session")
        self.work = self.tmp / "work"
        self.work.mkdir()

    def wait_for_row(self, name) -> dict:
        for _ in range(100):
            hit = [r for r in self.rows() if r["name"] == name]
            if hit:
                return hit[0]
            time.sleep(0.05)
        self.fail(f"{name} never registered")

    def test_new_starts_claude_in_tmux(self):
        r = self.herd("new", "demo", str(self.work), "--", "--model", "opus")
        self.assertEqual(r.returncode, 0, r.stderr)
        row = self.wait_for_row("demo")
        self.assertEqual(row["tmux"], "demo:0.0")
        self.assertEqual(os.path.realpath(row["cwd"]), os.path.realpath(self.work))
        started = json.loads(self.argv_file.read_text())
        self.assertEqual(started["argv"], ["--name", "demo", "--model", "opus"])
        self.assertNotIn("CLAUDECODE", started["env"])
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", started["env"])
        self.assertEqual(herd.resolve("demo", self.rows()), "demo")
        self.assertEqual(herd.resolve(str(row["pid"]), self.rows()), "demo:0.0")

        again = self.herd("new", "demo", str(self.work))
        self.assertEqual(again.returncode, 1)
        self.assertIn("already exists", again.stderr)

    def test_new_rejects_bad_input(self):
        self.assertEqual(self.herd("new", "a.b").returncode, 2)
        missing = self.herd("new", "ok", str(self.tmp / "nope"))
        self.assertEqual(missing.returncode, 1)
        self.assertIn("no such directory", missing.stderr)

    def test_go_outside_tmux(self):
        self.session("plain", "idle")
        r = self.herd("go", "plain")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not running in tmux", r.stderr)
        self.assertIn("no session", self.herd("go", "ghost").stderr)


if __name__ == "__main__":
    unittest.main()
