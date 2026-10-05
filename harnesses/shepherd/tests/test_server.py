import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from support import Sandbox


class TestServer(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.apply_env()
        repo = self.sb.repo()
        self.sb.ok(repo, "init")
        a = self.sb.session("sess-a", cwd=repo)
        self.sb.ok(repo, "start", "a", "--goal", "goal a", "--owns", "src/", env=a)
        from shep import server
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.token = "tok"
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.sb.restore_env()
        self.sb.cleanup()

    def get(self, path, host=None, cookie=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if cookie:
            headers["Cookie"] = cookie
        c.request("GET", path, headers=headers)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def test_auth_and_host_checks(self):
        self.assertEqual(self.get("/api/board")[0], 401)
        self.assertEqual(self.get("/api/board", host="evil.example:80", cookie="shepherd=tok")[0], 403)
        status, headers, _ = self.get("/?t=tok")
        self.assertEqual(status, 302)
        self.assertIn("shepherd=tok", headers["Set-Cookie"])
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertEqual(self.get("/api/ping")[0], 200)
        self.assertEqual(self.get("/static/../shep/server.py", cookie="shepherd=tok")[0], 404)

    def test_board_shape(self):
        status, _, body = self.get("/api/board", host=f"localhost:{self.port}", cookie="shepherd=tok")
        self.assertEqual(status, 200)
        data = json.loads(body)
        repo = data["repos"][0]
        card = repo["tracks"][0]
        self.assertEqual(card["name"], "a")
        self.assertEqual(card["agent"]["name"], "sess-a")
        self.assertTrue(card["agent"]["live"])
        self.assertEqual(card["goal"], "goal a")
        status, _, body = self.get(f"/api/track?repo={repo['common']}&track=a", cookie="shepherd=tok")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["subagents"], [])
        status, _, raw = self.get("/api/agents", cookie="shepherd=tok")
        self.assertEqual(status, 200)
        graph = json.loads(raw)
        self.assertEqual([t["name"] for s in graph["sessions"] for t in s["tracks"]], ["a"])
        self.assertEqual(json.loads(body)["track"]["name"], "a")
        self.assertEqual(self.get("/api/track?repo=/nope&track=a", cookie="shepherd=tok")[0], 404)


if __name__ == "__main__":
    unittest.main()
