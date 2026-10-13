import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from support import Sandbox


def write_store(d: Path, manifest=None, nodes=(), edges=(), raw_nodes=None, raw_edges=None):
    d.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (d / "manifest.json").write_text(json.dumps(manifest))
    (d / "nodes.jsonl").write_text(raw_nodes if raw_nodes is not None else "".join(json.dumps(n) + "\n" for n in nodes))
    (d / "edges.jsonl").write_text(raw_edges if raw_edges is not None else "".join(json.dumps(e) + "\n" for e in edges))


def op(t, name, session="s1", store="dev.omni", **kw):
    return dict({"t": t, "op": name, "source": "bash", "store": store, "ok": True, "ms": 5, "dNodes": 0,
                 "dEdges": 0, "touched": [], "focus": None, "error": None, "version": None, "session": session}, **kw)


class TestGraphs(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.apply_env()
        from shep import graphs
        self.graphs = graphs

    def tearDown(self):
        self.sb.restore_env()
        self.sb.cleanup()

    def only(self):
        stores = self.graphs.listing()["stores"]
        self.assertEqual(len(stores), 1, stores)
        return stores[0]

    def test_store_in_a_session_folder_is_read_like_the_mod(self):
        root = self.sb.tmp / "demo"
        nodes = "\ufeff" + "\n".join([
            json.dumps({"id": "a", "label": "Epic", "props": {"name": "Sync\u0007 work", "status": "proposed",
                                                             "source": ["docs/x.md:3", "docs/x.md:9"]}}),
            json.dumps({"id": "b", "label": "Issue", "props": {"title": "Fix it"}}),
            json.dumps({"id": 7, "label": "Issue", "props": {}}),
            "not json",
            json.dumps({"label": "Issue"}),
        ]) + "\n"
        edges = "\n".join([json.dumps({"from": "a", "type": "contains", "to": "b"})] * 2
                          + [json.dumps({"from": "a", "type": "contains", "to": "7"}), json.dumps({"from": "a"})]) + "\n"
        write_store(root / "graphs" / "dev.omni", {"name": "dev.omni", "version": "v1", "open_conflicts": ["a"]},
                    raw_nodes=nodes, raw_edges=edges)
        self.sb.session("sess-demo", cwd=root)
        st = self.only()
        self.assertEqual((st["name"], st["version"], st["nodes"], st["edges"]), ("dev.omni", "v1", 3, 2))
        self.assertEqual((st["queued"], st["conflicts"], st["preset"]), (1, 1, None))
        self.assertEqual(st["problem"], "3 lines not read")
        self.assertIsNone(st["branch"])
        g = self.graphs.graph(st["id"])
        by = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(by["a"]["name"], "Sync  work")
        self.assertEqual(by["a"]["source"], ["docs/x.md:3", "docs/x.md:9"])
        self.assertEqual((by["b"]["name"], by["7"]["name"]), ("Fix it", "7"))
        self.assertEqual([list(e) for e in g["edges"]], [["a", "contains", "b"], ["a", "contains", "7"]])
        self.assertEqual(g["labels"], ["Epic", "Issue"])
        self.assertIsNone(self.graphs.graph("nope"))

    def test_configured_options_rename_the_store_and_its_keys(self):
        root = self.sb.tmp / "proj"
        (self.sb.claude / "settings.json").write_text(json.dumps({"pluginConfigs": {"omnigraph@claude-dotfiles": {"options": {
            "store": "data/g", "nodeKeys": "uid kind .", "edgeKeys": "src rel dst", "nameKeys": "title",
            "versionKey": "rev", "storeLabel": "mine"}}}}))
        write_store(root / "data" / "g", {"rev": 42}, nodes=[{"uid": "n1", "kind": "Doc", "title": "First"}],
                    edges=[{"src": "n1", "rel": "self", "dst": "n1"}])
        write_store(root / "graphs" / "dev.omni", {"version": "x"}, nodes=[{"id": "z", "label": "L", "props": {}}])
        self.sb.session("s", cwd=root)
        st = self.only()
        self.assertEqual((st["name"], st["version"], st["nodes"], st["edges"]), ("mine", "42", 1, 1))
        g = self.graphs.graph(st["id"])
        self.assertEqual((g["nodes"][0]["name"], g["nodes"][0]["label"]), ("First", "Doc"))

    def test_preset_store_in_an_enrolled_repo_and_its_tracks(self):
        repo = self.sb.repo()
        self.sb.ok(repo, "init")
        env = self.sb.session("sess-a", cwd=repo)
        self.sb.ok(repo, "start", "a", "--goal", "g", "--owns", "src/", env=env)
        self.sb.end_session(env)
        from shep import store
        track = next(t for r in store.registered_repos() for t in store.tracks(r["common"]))
        node = [{"id": "m1", "label": "Metric", "props": {"name": "NVF", "status": "curated"}}]
        write_store(repo / "ontology" / "build", {"ov": "abc123", "name": "ontology"}, nodes=node)
        write_store(Path(track["worktree"]) / "ontology" / "build", {"ov": "def456"}, nodes=node)
        stores = self.graphs.listing()["stores"]
        self.assertEqual([(s["preset"], s["version"]) for s in stores], [("sca", "abc123"), ("sca", "def456")])
        self.assertEqual(stores[0]["root"], str(repo))
        self.assertEqual(stores[0]["branch"], "trunk")
        self.assertEqual(stores[1]["branch"], "a")

    def test_feed_merges_the_mods_store_files(self):
        root = self.sb.tmp / "demo"
        write_store(root / "graphs" / "dev.omni", {"name": "dev.omni", "version": "v1"},
                    nodes=[{"id": "a", "label": "Epic", "props": {}}])
        sess = self.sb.session("sess-named", cwd="/")
        sid = sess["CLAUDE_CODE_SESSION_ID"]
        store_dir = self.sb.claude / "plugins" / "store"
        store_dir.mkdir(parents=True)
        many = [op(1000 + i, f"query q{i}") for i in range(60)]
        (store_dir / "omnigraph_claude-dotfiles-1.json").write_text(json.dumps({
            f"feed:{root}": many + [{"t": "bad"}, op(5000, "mutate create_issue", session=sid,
                                                    focus={"center": "a", "selected": "a"}, touched=["a", 3])],
            "other-key": {"x": 1}}))
        (store_dir / "omnigraph_inline-2.json").write_text(json.dumps({
            f"feed:{root}": [op(1059, "query q59"), op(4000, "init", store="elsewhere"), op(4500, "init", store=None)]}))
        st = self.only()
        feed = st["feed"]
        # 63 distinct ops on the root, the last 50 kept, then the other store's one dropped
        self.assertEqual(len(feed), 49)
        self.assertEqual([o["op"] for o in feed[-3:]], ["query q59", "init", "mutate create_issue"])
        self.assertEqual(feed[-1]["who"], "sess-named")
        self.assertEqual(feed[-1]["touched"], ["a"])
        self.assertEqual(feed[-1]["focus"], {"center": "a", "selected": "a"})
        self.assertEqual(feed[-3]["who"], "s1")
        self.assertEqual(st["last_op_at"], 5000)

    def test_json_is_read_as_javascript_reads_it_and_rev_follows_content(self):
        root = self.sb.tmp / "demo"
        d = root / "graphs" / "dev.omni"
        write_store(d, {"version": 1}, raw_nodes='{"id": 1.0, "label": "A", "props": {"name": "one"}}\n'
                    '{"id": "n", "label": "A", "props": {"w": NaN}}\n', raw_edges='{"from": 1.0, "type": "t", "to": 1}\n')
        self.sb.session("s", cwd=root)
        st = self.only()
        self.assertEqual((st["nodes"], st["edges"], st["problem"]), (1, 1, "1 line not read"))
        g = self.graphs.graph(st["id"])
        self.assertEqual(([n["id"] for n in g["nodes"]], [list(e) for e in g["edges"]]), (["1"], [["1", "t", "1"]]))
        json.dumps(g, allow_nan=False)
        (d / "nodes.jsonl").write_text('{"id": 1, "label": "A", "props": {"name": "one again"}}\n'
                                      '{"id": 2, "label": "A", "props": {}}\n')
        again = self.only()
        self.assertEqual((again["version"], again["nodes"]), ("1", 2))
        self.assertNotEqual(again["rev"], st["rev"])

    def test_a_file_past_the_cap_is_not_read(self):
        root = self.sb.tmp / "big"
        d = root / "graphs" / "dev.omni"
        write_store(d, {"version": "v"}, nodes=[])
        with open(d / "nodes.jsonl", "w") as f:
            f.write(" " * (4 * 1024 * 1024 + 1))
        self.sb.session("s", cwd=root)
        st = self.only()
        self.assertEqual(st["nodes"], 0)
        self.assertIn("nodes.jsonl is over 4 MiB", st["problem"])


class TestGraphRoutes(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.apply_env()
        root = self.sb.tmp / "demo"
        write_store(root / "graphs" / "dev.omni", {"name": "dev.omni", "version": "v1"},
                    nodes=[{"id": "a", "label": "Epic", "props": {"name": "A"}}])
        self.sb.session("s", cwd=root)
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

    def get(self, path, cookie="shepherd=tok"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}", "Cookie": cookie})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def test_routes(self):
        self.assertEqual(self.get("/api/graphs", cookie="")[0], 401)
        status, _, body = self.get("/api/graphs")
        self.assertEqual(status, 200)
        st = json.loads(body)["stores"][0]
        status, _, body = self.get(f"/api/graph?id={st['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["nodes"][0]["name"], "A")
        self.assertEqual(self.get("/api/graph?id=../../etc")[0], 404)
        for name, ctype in (("graph.js", "text/javascript"), ("graph.css", "text/css")):
            status, headers, _ = self.get(f"/static/{name}")
            self.assertEqual(status, 200)
            self.assertTrue(headers["Content-Type"].startswith(ctype))


if __name__ == "__main__":
    unittest.main()
