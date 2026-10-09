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
