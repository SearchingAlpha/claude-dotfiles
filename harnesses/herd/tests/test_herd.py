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
        self.s = self.session("lead", "busy")
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
        by_name = {w["name"]: w for w in self.detail()["workflows"]}
        self.assertEqual(set(by_name), {"nightly", "wf_00000000-003"})
        self.assertEqual((by_name["nightly"]["status"], by_name["nightly"]["elapsed"],
                          by_name["nightly"]["tokens"]), ("completed", 600, 1_500_000))
        stalled = by_name["wf_00000000-003"]
        self.assertEqual(stalled["status"], "stalled")
        self.assertEqual(stalled["phases"][0]["agents"][0]["status"], "stalled")

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
