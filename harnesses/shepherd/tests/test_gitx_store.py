import json
import multiprocessing
import os
import unittest
from pathlib import Path

from support import Sandbox
from shep import gitx, store


class TestGitx(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.apply_env()
        self.repo = self.sb.repo()

    def tearDown(self):
        self.sb.restore_env()
        self.sb.cleanup()

    def test_repo_of_main_and_worktree(self):
        r = gitx.repo_of(self.repo / "src" / "new-file.py")
        self.assertEqual(r.toplevel, os.path.realpath(self.repo))
        self.assertEqual(r.branch, "trunk")
        wt = self.sb.tmp / "proj-x"
        self.sb.git(self.repo, "worktree", "add", "-q", "-b", "x", str(wt))
        r2 = gitx.repo_of(wt / "deep" / "missing.txt")
        self.assertEqual(r2.toplevel, os.path.realpath(wt))
        self.assertEqual(r2.common_dir, r.common_dir)
        self.assertEqual(r2.branch, "x")
        self.assertEqual(r2.primary, os.path.realpath(self.repo))
        self.assertIsNone(gitx.repo_of(self.sb.tmp))
        self.assertIsNone(gitx.repo_of(self.repo / ".git" / "config"))

    def test_detect_main_without_assuming_names(self):
        self.assertEqual(gitx.detect_main(str(self.repo), "origin"), "trunk")

    def test_rebase_in_progress_keeps_branch(self):
        self.sb.git(self.repo, "checkout", "-q", "-b", "feat")
        self.sb.commit(self.repo, "README.md", "feat\n", "feat")
        self.sb.git(self.repo, "checkout", "-q", "trunk")
        self.sb.commit(self.repo, "README.md", "trunk\n", "trunk change")
        self.sb.git(self.repo, "checkout", "-q", "feat")
        self.sb.git(self.repo, "rebase", "trunk", check=False)     # conflicts
        r = gitx.repo_of(self.repo)
        self.assertEqual(r.branch, "feat")
        self.sb.git(self.repo, "rebase", "--abort")

    def test_merged_detection(self):
        s = self.sb
        for kind in ("ff", "squash", "rebase"):
            with self.subTest(kind=kind):
                s.git(self.repo, "checkout", "-q", "trunk")
                s.git(self.repo, "checkout", "-q", "-b", f"t-{kind}")
                s.commit(self.repo, f"{kind}.txt", "1\n", f"{kind} 1")
                s.commit(self.repo, f"{kind}.txt", "2\n", f"{kind} 2")
                s.git(self.repo, "checkout", "-q", "trunk")
                s.commit(self.repo, f"other-{kind}.txt", "x\n", f"unrelated {kind}")
                self.assertFalse(gitx.merged_into(str(self.repo), f"t-{kind}", "trunk"))
                if kind == "ff":
                    s.git(self.repo, "merge", "-q", "--no-edit", f"t-{kind}")
                elif kind == "squash":
                    s.git(self.repo, "merge", "-q", "--squash", f"t-{kind}")
                    s.git(self.repo, "commit", "-qm", "squashed")
                else:
                    s.git(self.repo, "cherry-pick", f"trunk..t-{kind}")
                self.assertTrue(gitx.merged_into(str(self.repo), f"t-{kind}", "trunk"))

    def test_predict_conflicts(self):
        s = self.sb
        s.git(self.repo, "checkout", "-q", "-b", "a")
        s.commit(self.repo, "src/a.py", "a = 2\n", "a")
        s.git(self.repo, "checkout", "-q", "trunk")
        s.git(self.repo, "checkout", "-q", "-b", "b")
        s.commit(self.repo, "src/a.py", "a = 3\n", "b")
        s.git(self.repo, "checkout", "-q", "trunk")
        s.git(self.repo, "checkout", "-q", "-b", "c")
        s.commit(self.repo, "src/b.py", "b = 3\n", "c")
        self.assertEqual(gitx.predict_conflicts(str(self.repo), "a", "b"), ["src/a.py"])
        self.assertEqual(gitx.predict_conflicts(str(self.repo), "a", "c"), [])


def _append_many(path, n, tag):
    for i in range(n):
        store.append_jsonl(Path(path), {"tag": tag, "i": i, "pad": "x" * 200})


class TestStore(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.sb.apply_env()

    def tearDown(self):
        self.sb.restore_env()
        self.sb.cleanup()

    def test_concurrent_appends_stay_parseable(self):
        path = self.sb.tmp / "x.jsonl"
        procs = [multiprocessing.Process(target=_append_many, args=(str(path), 200, t))
                 for t in range(8)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        lines = path.read_text().splitlines()
        self.assertEqual(len(lines), 1600)
        for l in lines:
            json.loads(l)

    def test_tail_skips_cut_line(self):
        path = self.sb.tmp / "t.jsonl"
        for i in range(100):
            store.append_jsonl(path, {"i": i})
        tail = store.tail_jsonl(path, 200)
        self.assertEqual(tail[-1]["i"], 99)
        self.assertTrue(all("i" in e for e in tail))

    def test_unknown_keys_survive_and_newer_schema_is_ignored(self):
        common = str(self.sb.tmp / "c")
        store.save_track(common, {"name": "a", "future_field": 1})
        t = store.load_track(common, "a")
        self.assertEqual(t["future_field"], 1)
        store.write_json(store.state_dir(common) / "tracks" / "b.json", {"name": "b", "schema": 99})
        self.assertIsNone(store.load_track(common, "b"))
        self.assertEqual([t["name"] for t in store.tracks(common)], ["a"])


if __name__ == "__main__":
    unittest.main()
