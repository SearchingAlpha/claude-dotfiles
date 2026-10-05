import json
import os
import time
import unittest

from support import Sandbox


def turn(kind, stop=None, tools=()):
    content = [{"type": "text", "text": "…"}] + [
        {"type": "tool_use", "name": n, "input": i} for n, i in tools]
    return {"type": kind, "message": {"role": kind, "stop_reason": stop, "content": content}}


class TestAgents(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.apply_env()
        self.repo = self.sb.repo()
        self.sb.ok(self.repo, "init")
        self.env = self.sb.session("lead", cwd=self.repo)
        self.sid = self.env["CLAUDE_CODE_SESSION_ID"]
        self.sb.ok(self.repo, "start", "a", "--goal", "goal a", "--owns", "src/", env=self.env)
        self.sb.ok(self.repo, "start", "b", "--goal", "goal b", "--owns", "lib/", env=self.env)
        self.sub = self.sb.claude / "projects" / "-proj" / self.sid / "subagents"
        self.sub.mkdir(parents=True)

    def tearDown(self):
        self.sb.restore_env()
        self.sb.cleanup()

    def agent(self, aid, turns, age=0, **meta):
        (self.sub / f"agent-{aid}.meta.json").write_text(json.dumps(
            dict({"agentType": "Explore", "description": f"job {aid}", "spawnDepth": 1,
                  "requestShape": "background"}, **meta)))
        tx = self.sub / f"agent-{aid}.jsonl"
        tx.write_text("".join(json.dumps(t) + "\n" for t in turns) + '{"type":"ai-title"}\n')
        if age:
            t = time.time() - age
            os.utime(tx, (t, t))

    def test_tree_and_statuses(self):
        from shep import agents
        self.agent("done1", [turn("user"), turn("assistant", "end_turn")])
        self.agent("run1", [turn("assistant", "tool_use", [("Bash", {"description": "run tests"})]),
                            turn("user")])
        self.agent("kid1", [turn("assistant", "tool_use", [("Read", {"file_path": "/x/y/app.css"})])],
                   parentAgentId="run1", spawnDepth=2)
        self.agent("quiet", [turn("assistant", "tool_use", [("Bash", {})])], age=agents.QUIET + 60)
        self.agent("halt", [turn("user")], stoppedByUser=True)
        self.agent("old", [turn("assistant", "end_turn")], age=agents.WINDOW + 60)
        got = {a["id"]: a for a in agents.subagents(self.sid, True)}
        self.assertEqual(set(got), {"done1", "run1", "kid1", "quiet", "halt"})
        self.assertEqual(got["done1"]["status"], "done")
        self.assertEqual(got["run1"]["status"], "running")
        self.assertEqual(got["run1"]["doing"], "Bash: run tests")
        self.assertEqual(got["kid1"]["parent"], "run1")
        self.assertEqual(got["kid1"]["doing"], "Read: app.css")
        self.assertEqual(got["quiet"]["status"], "stalled")
        self.assertEqual(got["halt"]["status"], "stopped")
        ended = {a["id"]: a["status"] for a in agents.subagents(self.sid, False)}
        self.assertEqual(ended["run1"], "interrupted")
        self.assertEqual(ended["done1"], "done")

    def test_graph_links_and_card_summary(self):
        from shep import board
        self.agent("run1", [turn("assistant", "tool_use", [("Bash", {})])])
        wt_b = os.path.join(os.path.dirname(self.repo), f"{os.path.basename(self.repo)}-b")
        self.sb.ok(wt_b, "log", "--to", "a", "--type", "request", "please rebase", env=self.env)
        board._cache.clear()
        g = board.agents_graph()
        lead = next(s for s in g["sessions"] if s["session_id"] == self.sid)
        self.assertEqual(sorted(t["name"] for t in lead["tracks"]), ["a", "b"])
        self.assertEqual([a["id"] for a in lead["agents"]], ["run1"])
        msg = [l for l in g["links"] if l["kind"] == "message"]
        self.assertEqual([(l["from"], l["to"], l["type"]) for l in msg], [("b", "a", "request")])
        card = board.snapshot()["repos"][0]["tracks"][0]
        self.assertEqual(card["subagents"], {"running": 1})


if __name__ == "__main__":
    unittest.main()
