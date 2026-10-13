"""tree_hash: what counts as a change, what doesn't, and what it costs to find out."""

import os
import subprocess
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from support import Sandbox
import stop_gate as g


class HashCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.repo = self.sb.make_repo()
        env = mock.patch.dict(os.environ, self.sb.env, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.base = g.head_tree(self.repo)
        self.place = self.at(self.repo)

    def tearDown(self):
        self.sb.cleanup()

    @staticmethod
    def at(root, sub=""):
        return g.Place(root, sub, g.repo_info(str(root))[1])

    def h(self, watch=(), base=None, place=None):
        return g.tree_hash(place or self.place, list(watch), "label", base or self.base)

    def touch(self, rel, ahead=100):
        t = time.time() + ahead
        os.utime(self.repo / rel, (t, t))


class TestWhatCounts(HashCase):
    def test_edits_new_files_deletes_and_modes_count(self):
        seen = {self.h().digest}
        self.sb.write("src/app.py", "x = 2\n")
        seen.add(self.h().digest)
        self.sb.write("src/new.py", "y\n")
        seen.add(self.h().digest)
        os.chmod(self.repo / "src" / "new.py", 0o755)
        seen.add(self.h().digest)
        (self.repo / "verdict.txt").unlink()
        seen.add(self.h().digest)
        os.symlink("app.py", self.repo / "src" / "link")
        seen.add(self.h().digest)
        self.assertEqual(len(seen), 6)

    def test_same_bytes_same_hash_whatever_the_index_says(self):
        clean = self.h()
        self.touch("src/app.py")  # stat-dirty, same bytes
        self.assertEqual(self.h(), clean)
        self.sb.write("src/app.py", "x = 2\n")
        edited = self.h()
        self.sb.git("add", "src/app.py")  # staged: diff-index now reports the index's blob id
        self.assertEqual(self.h().digest, edited.digest)
        self.sb.git("commit", "-qm", "c")  # committed: still the same bytes on disk
        self.assertEqual(self.h().digest, edited.digest)
        self.assertEqual(self.h(base=g.head_tree(self.repo)).changed, ())

    def test_a_new_file_hashes_the_same_untracked_or_added(self):
        self.sb.write("src/new.py", "y\n")
        untracked = self.h()
        self.sb.git("add", "src/new.py")
        added = self.h()
        self.assertEqual(added.digest, untracked.digest)
        self.assertNotEqual(added.untracked, untracked.untracked)

    def test_watch_limits_both_parts(self):
        clean = self.h(["src"])
        self.sb.write("README.md", "docs\n")
        self.sb.write("verdict.txt", "BAD\n")
        self.assertEqual(self.h(["src"]), clean)
        self.sb.write("src/app.py", "x = 3\n")
        self.assertNotEqual(self.h(["src"]).digest, clean.digest)

    def test_big_files_count_by_size_mtime_ctime_and_inode(self):
        big = self.repo / "data.bin"
        big.write_bytes(b"\0" * (g.FILE_MAX + 10))
        self.sb.git("add", "data.bin")
        self.sb.git("commit", "-qm", "big")
        base = g.head_tree(self.repo)
        clean = self.h(base=base)
        with big.open("r+b") as f:
            f.seek(5)
            f.write(b"XXXX")
        changed = self.h(base=base)
        self.assertNotEqual(changed.digest, clean.digest)
        st = big.stat()
        time.sleep(0.02)
        with big.open("r+b") as f:  # same size, mtime put back: ctime still moves
            f.seek(5)
            f.write(b"YYYY")
        os.utime(big, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertNotEqual(self.h(base=base).digest, changed.digest)

    def test_tracked_and_untracked_parts(self):
        clean = self.h()
        self.sb.write("cache/x.pyc", "1\n")  # untracked only
        cache = self.h()
        self.assertEqual(cache.tracked, clean.tracked)
        self.assertNotEqual(cache.untracked, clean.untracked)
        self.sb.write("src/app.py", "x = 2\n")
        edit = self.h()
        self.assertNotEqual(edit.tracked, cache.tracked)
        self.assertEqual(edit.untracked, cache.untracked)
        self.assertEqual(edit.changed, ((b"src/app.py", b"100644"),))
        self.assertEqual(edit.others, (b"cache/x.pyc",))

    def test_from_a_subfolder_the_whole_repo_counts(self):
        sub = self.at(self.repo, "src")
        clean = self.h(place=sub)
        self.sb.write("README.md", "docs\n")  # outside the subfolder
        self.assertNotEqual(self.h(place=sub).digest, clean.digest)
        self.assertEqual(self.h(place=sub).others, (b"README.md",))  # paths from the top
        watched = self.h(["lib"], place=sub)  # --watch is relative to the subfolder
        self.sb.write("src/lib/new.py", "y\n")
        self.sb.write("lib/elsewhere.py", "z\n")
        self.assertEqual(self.h(["lib"], place=sub).others, (b"src/lib/new.py",))
        self.assertNotEqual(self.h(["lib"], place=sub).digest, watched.digest)

    def test_read_budget_falls_back_to_metadata(self):
        for i in range(30):
            self.sb.write(f"gen/f{i}.txt", f"{i}\n")
        with mock.patch.object(g, "READ_FILES", 10):
            first = self.h()
            self.assertEqual(self.h(), first)  # deterministic
            self.touch("gen/f25.txt")  # past the budget: mtime counts
            self.assertNotEqual(self.h().digest, first.digest)

    def test_merge_conflicts_hash_and_resolving_counts(self):
        self.sb.git("checkout", "-qb", "side")
        self.sb.write("src/app.py", "x = 'side'\n")
        self.sb.git("commit", "-qam", "side")
        self.sb.git("checkout", "-q", "trunk")
        self.sb.write("src/app.py", "x = 'trunk'\n")
        self.sb.git("commit", "-qam", "trunk")
        p = subprocess.run(["git", "merge", "-q", "side"], cwd=str(self.repo), env=self.sb.env,
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)  # conflicted: unmerged index entries
        conflicted = self.h()
        self.sb.write("src/app.py", "x = 'both'\n")
        self.assertNotEqual(self.h().digest, conflicted.digest)

    def test_a_base_that_is_gone_falls_back_to_head(self):
        rs = {"base": "0" * 40}
        cfg = SimpleNamespace(watch=[])
        tree = g.session_tree(self.place, cfg, "label", rs)
        self.assertEqual(tree.base, self.base)
        self.assertEqual(rs["base"], self.base)

    def test_unborn_branch(self):
        fresh = self.sb.make_repo(self.sb.tmp / "fresh", commit=False)
        a = g.tree_hash(self.at(fresh), [], "label")
        self.sb.write("src/app.py", "x = 9\n", root=fresh)
        self.assertNotEqual(g.tree_hash(self.at(fresh), [], "label").digest, a.digest)

    def test_work_trees_beyond_eight_drop_the_least_recent(self):
        state = {}
        for i in range(12):
            g.root_state(state, f"/r{i}")["base"] = str(i)
        g.root_state(state, "/r5")
        g.root_state(state, "/new")
        self.assertEqual(len(state["roots"]), g.ROOTS_KEEP)
        self.assertIn("/r5", state["roots"])
        self.assertNotIn("/r4", state["roots"])


class TestPredatesSession(HashCase):
    """The ctime rule: a tree whose every difference from HEAD is older than the session."""

    def check(self, start):
        state = {"start": start}
        rs = {}
        tree = g.session_tree(self.place, SimpleNamespace(watch=[]), "label", rs)
        return g.predates_session(self.place, SimpleNamespace(watch=[]), state, tree)

    def test_old_changes_qualify_new_ones_do_not(self):
        self.sb.write("src/app.py", "x = 'old wip'\n")
        self.sb.write("notes.txt", "old\n")
        time.sleep(g.SLACK + 0.1)
        start = time.time()  # the session starts after those writes
        self.assertIs(self.check(start), True)  # uncommitted tracked changes: dirty
        self.sb.write("src/new.py", "y\n")
        self.assertIsNone(self.check(start))  # written in the session
        os.utime(self.repo / "src" / "new.py", (1, 1))  # an old mtime doesn't help: ctime counts
        self.assertIsNone(self.check(start))
        (self.repo / "src" / "new.py").unlink()
        self.assertIs(self.check(start), True)

    def test_a_deletion_or_a_new_commit_counts_as_new(self):
        (self.repo / "verdict.txt").unlink()
        self.assertIsNone(self.check(time.time() + 5))
        self.sb.git("checkout", "-q", "--", "verdict.txt")
        self.assertIs(self.check(time.time() + 5), False)  # clean tree, old commit
        self.sb.git("commit", "-q", "--allow-empty", "-m", "now")
        self.assertIsNone(self.check(time.time() - 5))  # HEAD made in the session

    def test_without_a_session_start_nothing_qualifies(self):
        state = {}
        tree = g.session_tree(self.place, SimpleNamespace(watch=[]), "label", {})
        self.assertIsNone(g.predates_session(self.place, SimpleNamespace(watch=[]), state, tree))


class TestNestedRepositories(HashCase):
    def add_submodule(self):
        lib = self.sb.make_repo(self.sb.tmp / "libsrc", check=None)
        self.sb.git("submodule", "add", "-q", str(lib), "lib")
        self.sb.git("commit", "-qm", "add lib")
        return g.head_tree(self.repo)

    def test_every_edit_inside_a_dirty_submodule_counts(self):
        base = self.add_submodule()
        seen = {self.h(base=base).digest}
        self.sb.write("lib/src/app.py", "x = 2\n")
        seen.add(self.h(base=base).digest)
        self.sb.write("lib/verdict.txt", "BAD\n")  # a second edit: the submodule was dirty
        seen.add(self.h(base=base).digest)
        self.sb.write("lib/new_untracked.py", "boom\n")
        seen.add(self.h(base=base).digest)
        self.assertEqual(len(seen), 4)

    def test_submodule_ignore_config_is_overridden(self):
        base = self.add_submodule()
        self.sb.git("config", "submodule.lib.ignore", "all")
        clean = self.h(base=base)
        self.sb.write("lib/verdict.txt", "BAD\n")
        self.assertNotEqual(self.h(base=base).digest, clean.digest)

    def test_edits_inside_an_untracked_nested_repo_count(self):
        nested = self.sb.make_repo(self.repo / "vendor" / "tool", check=None)
        first = self.h()
        self.sb.write("verdict.txt", "BAD\n", root=nested)
        self.assertNotEqual(self.h().digest, first.digest)

    def test_a_broken_nested_repo_does_not_break_the_hash(self):
        broken = self.repo / "copied"
        broken.mkdir()
        (broken / ".git").write_text("gitdir: /nonexistent/worktrees/x\n")
        (broken / "f.txt").write_text("x\n")
        self.assertTrue(self.h().digest)

    def test_a_worktree_of_this_repo_inside_it_is_not_part_of_it(self):
        clean = self.h()
        wt = self.repo / ".claude" / "worktrees" / "t"
        self.sb.git("worktree", "add", "-q", "-b", "t", str(wt))
        self.assertEqual(self.h(), clean)
        self.sb.write("verdict.txt", "BAD\n", root=wt)  # gated in its own root, not here
        self.sb.write("new.py", "z\n", root=wt)
        self.assertEqual(self.h(), clean)
        other = self.sb.make_repo(self.repo / "vendor" / "other", check=None)  # its own repo
        self.assertNotEqual(self.h().digest, clean.digest)
        self.assertEqual(self.h().others, (b"vendor/other",))
        self.assertTrue(other.exists())


if __name__ == "__main__":
    unittest.main()
