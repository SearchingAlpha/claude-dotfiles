"""statusline.sh and subagent-statusline.sh, run as Claude Code runs them:
sample JSON on stdin, a controlled PATH, output on stdout.

Each test gets a sandbox: a bin/ dir holding only the tools that test allows
(so "no jq" or "no shepherd" is real), a fake `shepherd` that serves canned
`status --json` output and counts its calls, a git repo to stand in for the
session's cwd, and its own cache dir."""

import datetime as dt
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

HERE = Path(__file__).resolve().parent.parent
STATUSLINE = HERE / "statusline.sh"
SUBAGENT = HERE / "subagent-statusline.sh"
BASH = shutil.which("bash")
TOOLS = ("bash", "jq", "python3", "git", "date", "mkdir", "mv", "find", "rm",
         "setsid", "nohup", "timeout", "cat", "ln")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
# The wide ranges of dwidth() in statusline.sh and of subagent-statusline.sh.
WIDE = ((0x1100, 0x115F), (0x231A, 0x231B), (0x2329, 0x232A), (0x23E9, 0x23F3),
        (0x25FD, 0x25FE), (0x2600, 0x27BF), (0x2B1B, 0x2B55), (0x2E80, 0xA4CF),
        (0xA960, 0xA97F), (0xAC00, 0xD7A3), (0xF900, 0xFAFF), (0xFE10, 0xFE19),
        (0xFE30, 0xFE6F), (0xFF00, 0xFF60), (0xFFE0, 0xFFE6), (0x16FE0, 0x1B2FF),
        (0x1F000, 0x1FAFF), (0x20000, 0x3FFFD))
# Each range's edges and the code points just outside them.
EDGES = "".join(chr(c) for lo, hi in WIDE for c in (lo - 1, lo, hi, hi + 1))
CONTROLS = "\x1b]52;c;ZXZpbA==\x07\x1b[2J\x7f\u0085\u009b2J\n\t"


def plain(s: str) -> str:
    return ANSI.sub("", s)


def width(s: str) -> int:
    return sum(2 if any(lo <= ord(ch) <= hi for lo, hi in WIDE) else 1 for ch in s)


class Sandbox:
    def __init__(self, exclude=(), shepherd=None):
        self.root = Path(tempfile.mkdtemp(prefix="statusline-"))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for tool in TOOLS:
            path = shutil.which(tool)
            if path and tool not in exclude:
                (self.bin / tool).symlink_to(path)
        self.calls = self.root / "shepherd-calls"
        if shepherd is not None:
            (self.root / "shepherd.json").write_text(json.dumps(shepherd))
            fake = self.bin / "shepherd"
            # Each call logs the GIT_OPTIONAL_LOCKS it was given.
            fake.write_text(f"#!{BASH}\necho \"x ${{GIT_OPTIONAL_LOCKS:-unset}}\" >>'{self.calls}'\n"
                            f"cat '{self.root / 'shepherd.json'}'\n")
            fake.chmod(0o755)
        self.launches = self.root / "launches"
        real_setsid = shutil.which("setsid")
        if real_setsid and "setsid" not in exclude:
            # Count refresher launches on the way through.
            (self.bin / "setsid").unlink()
            (self.bin / "setsid").write_text(f"#!{BASH}\necho x >>'{self.launches}'\n"
                                             f"exec '{real_setsid}' \"$@\"\n")
            (self.bin / "setsid").chmod(0o755)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git = shutil.which("git")
        subprocess.run([git, "init", "-q", "-b", "feat-x", str(self.repo)], check=True)
        self.cache = self.root / "cache"

    def env(self, **extra):
        env = {"PATH": str(self.bin), "HOME": str(self.root), "COLUMNS": "200",
               "STATUSLINE_CACHE_DIR": str(self.cache), "LANG": "C.UTF-8"}
        env.update(extra)
        return env

    def run(self, script, data, *args, pipe=False, **env):
        """Run like Claude Code does: stdin is one end of a socketpair (Node's
        stdio), which /dev/stdin can't reopen. pipe=True uses a plain pipe."""
        text = (data if isinstance(data, str) else json.dumps(data)).encode()
        argv = [BASH, str(script), *args]
        if pipe:
            r = subprocess.run(argv, input=text, capture_output=True, env=self.env(**env),
                               timeout=10)
            return r.returncode, r.stdout.decode(), r.stderr.decode()
        parent, child = socket.socketpair()
        try:
            p = subprocess.Popen(argv, stdin=child, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=self.env(**env))
            child.close()
            parent.sendall(text)
            parent.shutdown(socket.SHUT_WR)
            out, err = p.communicate(timeout=10)
        finally:
            parent.close()
        return p.returncode, out.decode(), err.decode()

    def shepherd_calls(self) -> int:
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def launch_count(self) -> int:
        return len(self.launches.read_text().splitlines()) if self.launches.exists() else 0

    def close(self):
        # Let a detached refresher finish before removing its files.
        lock = list(self.cache.glob("*.lock")) if self.cache.exists() else []
        for _ in range(50):
            if not any(p.exists() for p in lock):
                break
            time.sleep(0.05)
        shutil.rmtree(self.root, ignore_errors=True)


def session(repo, **over):
    d = {
        "session_id": "sess-1",
        "cwd": str(repo),
        "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
        "workspace": {"current_dir": str(repo), "project_dir": str(repo)},
        "effort": {"level": "xhigh"},
        "context_window": {"total_input_tokens": 120000, "context_window_size": 1000000,
                           "used_percentage": 12},
        "prompt_cache": {"warm": True, "ttl": "1h", "hit_ratio": 0.912},
        "cost": {"total_cost_usd": 3.4567},
    }
    for k, v in over.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    return d


class StatusLine(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.close()

    def line(self, data, *args, **env):
        rc, out, err = self.sb.run(STATUSLINE, data, *args, **env)
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.count("\n"), 1, out)
        return out.rstrip("\n")

    def test_syntax(self):
        for script in (STATUSLINE, SUBAGENT):
            subprocess.run([BASH, "-n", str(script)], check=True)

    def test_full_session_shows_every_segment(self):
        out = plain(self.line(session(self.sb.repo)))
        self.assertTrue(out.startswith("Opus 5.5 xhigh │ ctx 12% │ cache 91% 1h │ "
                                       "~$3.46 est │ ⎇ feat-x │ "), out)
        self.assertRegex(out, r"│ \d\d:\d\d:\d\d$")

    def test_reads_stdin_from_a_pipe_and_without_cat(self):
        sock = plain(self.line(session(self.sb.repo)))
        rc, out, _ = self.sb.run(STATUSLINE, session(self.sb.repo), pipe=True)
        strip_clock = lambda s: re.sub(r"\d\d:\d\d:\d\d", "T", s)
        self.assertEqual(strip_clock(plain(out.rstrip("\n"))), strip_clock(sock))
        nocat = Sandbox(exclude=("cat",))  # falls back to the read builtin
        try:
            rc, out, _ = nocat.run(STATUSLINE, session(nocat.repo))
            self.assertEqual(rc, 0)
            self.assertTrue(plain(out).startswith("Opus 5.5 xhigh │ ctx 12%"), out)
            rc, out, _ = nocat.run(SUBAGENT, SubagentStatusLine.TASKS)
            self.assertEqual(len(out.splitlines()), 2)
        finally:
            nocat.close()

    def test_context_colour_thresholds(self):
        for pct, colour in ((59, None), (60, "\x1b[33m"), (79, "\x1b[33m"), (80, "\x1b[31m")):
            out = self.line(session(self.sb.repo, context_window={"used_percentage": pct}))
            seg = re.search(r"(\x1b\[3[13]m)?ctx %d%%" % pct, out)
            self.assertIsNotNone(seg, out)
            self.assertEqual(seg.group(1), colour, (pct, out))

    def test_no_color(self):
        out = self.line(session(self.sb.repo, context_window={"used_percentage": 90}), NO_COLOR="1")
        self.assertNotIn("\x1b", out)
        self.assertIn("ctx 90%", out)

    def test_compact_window_rescales_context(self):
        # used_percentage counts against the full 1M window; compaction runs at
        # the auto-compact window, so 262k of 400k is the number that matters.
        data = session(self.sb.repo, context_window={
            "total_input_tokens": 262000, "context_window_size": 1000000, "used_percentage": 26})
        out = self.line(data, "--compact-window", "400000")
        self.assertIn("\x1b[33mctx 65%/400k", out)
        out = self.line(data, CLAUDE_CODE_AUTO_COMPACT_WINDOW="300000")
        self.assertIn("\x1b[31mctx 87%/300k", out)
        # A model window smaller than the compact window caps it.
        data["context_window"].update(total_input_tokens=150000, context_window_size=200000)
        self.assertIn("ctx 75%/200k", plain(self.line(data, "--compact-window", "400000")))

    def test_env_compact_window_wins_over_the_flag_like_in_claude_code(self):
        data = session(self.sb.repo, context_window={
            "total_input_tokens": 190000, "context_window_size": 1000000, "used_percentage": 19})
        out = plain(self.line(data, "--compact-window", "400000",
                              CLAUDE_CODE_AUTO_COMPACT_WINDOW="200000"))
        self.assertIn("ctx 95%/200k", out)
        # Claude Code reads "500k" as 500 and clamps it to the 100K minimum.
        data["context_window"]["total_input_tokens"] = 50000
        out = plain(self.line(data, "--compact-window", "400000",
                              CLAUDE_CODE_AUTO_COMPACT_WINDOW="500k"))
        self.assertIn("ctx 50%/100k", out)
        self.assertIn("ctx 12%/400k", plain(self.line(data, "--compact-window", "400000",
                                                      CLAUDE_CODE_AUTO_COMPACT_WINDOW="")))

    def test_clock_format(self):
        self.assertRegex(plain(self.line(session(self.sb.repo), "--clock", "%H:%M")),
                         r"│ \d\d:\d\d$")

    def test_null_and_missing_fields(self):
        early = session(self.sb.repo, context_window={"used_percentage": None,
                                                      "total_input_tokens": 0,
                                                      "context_window_size": 1000000},
                        prompt_cache=None, cost=None, effort=None)
        out = plain(self.line(early))
        self.assertTrue(out.startswith("Opus 5.5 │ ctx 0% │ ⎇ feat-x │ "), out)
        self.assertNotIn("cache", out)
        self.assertNotIn("est", out)
        out = plain(self.line({}))
        self.assertRegex(out, r"^\? │ \d\d:\d\d:\d\d$")

    def test_bad_and_empty_input_still_print_and_log(self):
        self.assertEqual(self.line("not json"), "claude")
        log = (self.sb.cache / "statusline.log").read_text()
        self.assertIn("unparseable input", log)
        self.assertEqual(self.line(""), "claude")

    def test_python_fallback_matches_jq(self):
        data = session(self.sb.repo, pr={"number": 12, "review_state": "approved"})
        with_jq = self.line(data)
        nojq = Sandbox(exclude=("jq",))
        try:
            data = session(nojq.repo, pr={"number": 12, "review_state": "approved"})
            rc, out, err = nojq.run(STATUSLINE, data)
        finally:
            nojq.close()
        self.assertEqual(rc, 0, err)
        strip_clock = lambda s: re.sub(r"\d\d:\d\d:\d\d", "T", s)
        self.assertEqual(strip_clock(out.rstrip("\n")), strip_clock(with_jq))
        self.assertIn("PR #12 approved", plain(with_jq))

    def test_parsers_agree_on_odd_input(self):
        # No cwd that exists, so the two sandboxes see the same input.
        gone = "/nonexistent-statusline-dir"
        cases = [
            {"model": {"display_name": "", "id": "claude-x"}, "cwd": gone},
            {"model": {"display_name": False, "id": "claude-x"}, "cwd": gone},
            {"model": {"display_name": 0}, "effort": {"level": 5}, "cwd": gone},
            {"model": {"display_name": {"a": [1, 2]}}, "cwd": gone},
            {"workspace": {"current_dir": ""}, "cwd": gone, "worktree": {"branch": "w\nx"}},
            {"context_window": {"used_percentage": 1e20}, "cost": {"total_cost_usd": -0.5}},
            {"context_window": {"used_percentage": "12", "total_input_tokens": 10 ** 22,
                                "context_window_size": 1000000}},
            {"cost": {"total_cost_usd": 1e18}, "prompt_cache": {"warm": False, "hit_ratio": 1e300}},
            {"pr": {"number": "12", "kind": "mr"}, "model": {"display_name": "Op\nus\x1f5"}},
            {"pr": {"number": 42.0}, "effort": {"level": 5.0}, "cwd": gone},
            "null", "[]", "[1]", '"str"', "5", "{} junk",
            '{"model": {"display_name": "A"}} junk',
            '{"model": {"display_name": "A"}}{"model": {"display_name": "B"}}',
            '{"context_window": {"used_percentage": NaN, "total_input_tokens": 1e400}}',
        ]
        nojq = Sandbox(exclude=("jq",))
        strip_clock = lambda s: re.sub(r"\d\d:\d\d:\d\d", "T", s)
        try:
            for data in cases:
                got = []
                for sb in (self.sb, nojq):
                    rc, out, err = sb.run(STATUSLINE, data, "--compact-window", "400000")
                    self.assertEqual((rc, err), (0, ""), (data, sb is nojq))
                    self.assertEqual(out.count("\n"), 1, data)
                    got.append(strip_clock(out))
                self.assertEqual(got[0], got[1], data)
        finally:
            nojq.close()

    def test_no_parser_shows_model_name(self):
        bare = Sandbox(exclude=("jq", "python3"))
        try:
            rc, out, _ = bare.run(STATUSLINE, session(bare.repo))
            self.assertEqual((rc, out), (0, "Opus 5.5\n"))
            rc, out, _ = bare.run(STATUSLINE, "{}")
            self.assertEqual((rc, out), (0, "claude\n"))
        finally:
            bare.close()

    def test_no_git_drops_branch_only(self):
        nogit = Sandbox(exclude=("git",))
        try:
            rc, out, _ = nogit.run(STATUSLINE, session(nogit.repo))
            self.assertEqual(rc, 0)
            self.assertNotIn("⎇", out)
            self.assertIn("ctx 12%", out)
        finally:
            nogit.close()

    def test_worktree_session_branch_wins(self):
        data = session(self.sb.repo, worktree={"name": "w", "branch": "worktree-w"},
                       workspace={"current_dir": str(self.sb.repo), "git_worktree": "w"})
        self.assertIn("⎇ worktree-w [wt]", plain(self.line(data)))

    def test_gitlab_merge_request(self):
        data = session(self.sb.repo, pr={"number": 7, "kind": "mr", "review_state": "pending"})
        self.assertIn("MR !7 pending", plain(self.line(data)))

    def test_narrow_terminal_drops_low_priority_segments_first(self):
        out = plain(self.line(session(self.sb.repo, pr={"number": 3}), COLUMNS="40"))
        self.assertEqual(out, "Opus 5.5 xhigh │ ctx 12% │ ⎇ feat-x")
        self.assertLessEqual(len(out), 40 - 4)  # the default margin
        # Narrower still: the effort word goes, the model and context stay.
        out = plain(self.line(session(self.sb.repo), COLUMNS="24"))
        self.assertEqual(out, "Opus 5.5 │ ctx 12%")
        out = plain(self.line(session(self.sb.repo), COLUMNS="5"))
        self.assertEqual(out, "Opus 5.5 │ ctx 12%")  # never dropped, even past the edge

    def test_margin_leaves_room_for_the_row_indent(self):
        # Claude Code indents the row by 2 and cuts a line that reaches the
        # edge with an ellipsis, so the line stops --margin columns short.
        data = session(self.sb.repo, pr={"number": 3})
        for cols in range(30, 130, 7):
            for margin, args in ((4, ()), (0, ("--margin", "0")), (12, ("--margin", "12"))):
                out = plain(self.line(data, *args, COLUMNS=str(cols)))
                self.assertLessEqual(len(out), cols - margin, (cols, margin, out))
        full = plain(self.line(data, COLUMNS="200"))
        self.assertEqual(plain(self.line(data, COLUMNS=str(len(full) + 4))), full)
        self.assertNotEqual(plain(self.line(data, COLUMNS=str(len(full) + 3))), full)

    def test_width_is_the_same_without_a_utf8_locale(self):
        # In C or POSIX, ${#s} counts the 3-byte │ and ⎇ as 3 each.
        data = session(self.sb.repo, pr={"number": 3})
        strip_clock = lambda s: re.sub(r"\d\d:\d\d:\d\d", "T", s)
        for cols in ("60", "75", "90", "110"):
            want = strip_clock(plain(self.line(data, COLUMNS=cols)))
            for env in ({"LANG": "C"}, {"LANG": "", "LC_ALL": "C"}, {"LANG": "POSIX"}):
                got = strip_clock(plain(self.line(data, COLUMNS=cols, **env)))
                self.assertEqual(got, want, (cols, env))

    def test_out_of_range_numbers_drop_their_segment(self):
        repo = self.sb.repo
        cases = (
            (dict(cost={"total_cost_usd": -0.5}), "est"),
            (dict(cost={"total_cost_usd": 1e18}), "est"),
            (dict(cost={"total_cost_usd": "3.10"}), "est"),
            (dict(prompt_cache={"hit_ratio": -2}), "%"),
        )
        for over, gone in cases:
            rc, out, err = self.sb.run(STATUSLINE, session(repo, **over))
            self.assertEqual((rc, err), (0, ""), over)
            self.assertNotIn(" │  │ ", out, over)
            segment = [s for s in plain(out).split(" │ ") if s.startswith(("~$", "cache"))]
            self.assertFalse([s for s in segment if gone in s], (over, out))
        # Tokens too big for shell arithmetic: fall back to used_percentage.
        data = session(repo, context_window={"total_input_tokens": 1e20,
                                             "context_window_size": 1000000, "used_percentage": 12})
        rc, out, err = self.sb.run(STATUSLINE, data, "--compact-window", "400000")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("│ ctx 12% │", plain(out))

    def test_a_newline_in_a_field_shifts_nothing(self):
        data = {"model": {"display_name": "Op\nus"}, "effort": {"level": "high"},
                "context_window": {"used_percentage": 70}, "cwd": str(self.sb.repo)}
        out = plain(self.line(data))
        self.assertTrue(out.startswith("Op us high │ ctx 70% │ ⎇ feat-x │ "), out)
        data["model"]["display_name"] = "Opus\x1b[2J\x1f5.5"
        self.assertTrue(plain(self.line(data)).startswith("Opus [2J 5.5 high │ ctx 70%"))

    def test_no_control_character_reaches_the_terminal(self):
        # Claude Code prints the line raw, so ESC, BEL, DEL and C1 controls
        # (U+0080-009F, such as U+009B, a one-character CSI) become spaces in
        # every field, with either parser, without one, and in a git branch.
        def controls(out):
            return [hex(ord(c)) for c in out.rstrip("\n") if ord(c) < 32 or 127 <= ord(c) < 160]
        data = session(self.sb.repo, model={"display_name": "Op" + CONTROLS + "us"},
                       effort={"level": "x" + CONTROLS}, worktree={"branch": "b" + CONTROLS},
                       pr={"number": 3, "review_state": "s" + CONTROLS})
        nojq, bare = Sandbox(exclude=("jq",)), Sandbox(exclude=("jq", "python3"))
        try:
            for sb in (self.sb, nojq):
                rc, out, err = sb.run(STATUSLINE, data, NO_COLOR="1")
                self.assertEqual((rc, err, controls(out)), (0, "", []), (sb is nojq, out))
                self.assertIn("Op ]52;c;ZXZpbA==  [2J   2J  us", out)
            # No parser: the model name comes from the raw text, where JSON
            # allows C1 characters unescaped (an ESC makes it invalid JSON,
            # but the fallback reads it anyway).
            raw = '{"model": {"display_name": "A%sB"}}' % CONTROLS.replace("\n", "")
            for sb in (self.sb, bare):
                rc, out, err = sb.run(STATUSLINE, raw, NO_COLOR="1")
                self.assertEqual((rc, err, controls(out)), (0, "", []), out)
                self.assertTrue(out.startswith("A ]52;c;ZXZpbA==  [2J   2J B"), out)
        finally:
            nojq.close()
            bare.close()
        # git takes C1 characters in a branch name.
        subprocess.run([shutil.which("git"), "-C", str(self.sb.repo), "symbolic-ref", "HEAD",
                        "refs/heads/x\u009b2Jy".encode("utf-8")], check=True)  # bytes: any locale
        out = self.line(session(self.sb.repo), NO_COLOR="1")
        self.assertEqual(controls(out), [])
        self.assertIn("⎇ x 2Jy", out)

    def test_wide_characters_count_two_columns(self):
        # CJK and emoji take two columns, in any locale: the line still stops
        # --margin short of the edge, and a line that fits exactly isn't cut.
        cases = (dict(worktree={"branch": "功能分支测试" * 2}),
                 dict(model={"display_name": "Opus 5.5 🚀"}),
                 dict(worktree={"branch": "x" + EDGES}))
        for over in cases:
            data = session(self.sb.repo, pr={"number": 3}, **over)
            for env in ({}, {"LANG": "C", "LC_ALL": "C"}):
                show = lambda cols: plain(self.line(data, "--clock", "CLK", COLUMNS=str(cols), **env))
                full, minimal = show(1000), show(1)  # minimal: model and context only
                w = width(full)
                self.assertEqual(show(w + 4), full, (over, env))
                self.assertNotEqual(show(w + 3), full, (over, env))
                for cols in range(20, w + 4, 9):
                    out = show(cols)
                    self.assertTrue(width(out) <= cols - 4 or out == minimal, (over, env, cols, out))

    @unittest.skipIf(ZoneInfo is None, "zoneinfo needs Python 3.9")
    def test_clock_zone(self):
        zone = "Asia/Kathmandu"  # +05:45, so hours and minutes both differ from UTC
        def expect():
            return {(dt.datetime.now(ZoneInfo(zone)) + dt.timedelta(seconds=s)).strftime("%H:%M")
                    for s in (-1, 0, 1)}
        before = expect()
        out = self.line(session(self.sb.repo), "--tz", zone)
        got = re.search(r"(\d\d:\d\d):\d\d", out).group(1)
        self.assertIn(got, before | expect())
        out = self.line(session(self.sb.repo), STATUSLINE_TZ=zone)
        self.assertIn(re.search(r"(\d\d:\d\d):\d\d", out).group(1), before | expect())

    def test_no_shepherd_means_no_cache(self):
        self.line(session(self.sb.repo))
        self.assertFalse(list(self.sb.cache.glob("shepherd-*")) if self.sb.cache.exists() else [])


class ShepherdSegment(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox(shepherd={})
        # The fake serves a track whose worktree is this sandbox's repo.
        status = {"protected": ["main"], "tracks": [
            {"name": "feat-x", "branch": "feat-x", "worktree": str(self.sb.repo),
             "review": {"id": "42", "state": "open"}},
        ]}
        (self.sb.root / "shepherd.json").write_text(json.dumps(status))

    def tearDown(self):
        self.sb.close()

    def wait_for_cache(self, sid="sess-1"):
        cache = self.sb.cache / f"shepherd-{sid}"
        for _ in range(100):
            if cache.exists() and not (self.sb.cache / f"shepherd-{sid}.lock").exists():
                return cache
            time.sleep(0.05)
        self.fail("the refresher never wrote the cache")

    def test_track_and_review_come_from_the_cache(self):
        rc, first, _ = self.sb.run(STATUSLINE, session(self.sb.repo))
        self.assertEqual(rc, 0)
        self.assertNotIn("(track)", first)  # the refresh runs detached, after this render
        self.wait_for_cache()
        rc, second, _ = self.sb.run(STATUSLINE, session(self.sb.repo))
        self.assertIn("⎇ feat-x (track) #42", plain(second))
        self.assertEqual(self.sb.shepherd_calls(), 1)

    def test_control_characters_in_shepherd_fields_become_spaces(self):
        # The refresher cleans the fields it caches; C1 included.
        status = {"tracks": [{"name": "t" + CONTROLS, "branch": "other", "worktree": str(self.sb.repo),
                              "review": {"id": "r" + CONTROLS, "state": "s" + CONTROLS}}]}
        (self.sb.root / "shepherd.json").write_text(json.dumps(status))
        self.sb.run(STATUSLINE, session(self.sb.repo))
        self.wait_for_cache()
        rc, out, _ = self.sb.run(STATUSLINE, session(self.sb.repo), NO_COLOR="1")
        self.assertEqual(rc, 0)
        self.assertIn("track t ]52;c;ZXZpbA==", out)
        self.assertEqual([hex(ord(c)) for c in out.rstrip("\n")
                          if ord(c) < 32 or 127 <= ord(c) < 160], [], out)

    def test_cache_is_reused_within_ttl_and_refreshed_after(self):
        self.sb.run(STATUSLINE, session(self.sb.repo))
        cache = self.wait_for_cache()
        for _ in range(5):
            self.sb.run(STATUSLINE, session(self.sb.repo))
        self.assertEqual(self.sb.shepherd_calls(), 1)
        # Age the cache past the TTL (line 2 is the epoch it was written).
        lines = cache.read_text().split("\n")
        lines[1] = str(int(lines[1]) - 31)
        cache.write_text("\n".join(lines))
        self.sb.run(STATUSLINE, session(self.sb.repo))
        self.wait_for_cache()
        for _ in range(40):
            if self.sb.shepherd_calls() == 2:
                break
            time.sleep(0.05)
        self.assertEqual(self.sb.shepherd_calls(), 2)

    def test_caches_are_per_session(self):
        self.sb.run(STATUSLINE, session(self.sb.repo, session_id="a"))
        self.sb.run(STATUSLINE, session(self.sb.repo, session_id="b"))
        self.wait_for_cache("a")
        self.wait_for_cache("b")
        self.assertEqual(self.sb.shepherd_calls(), 2)

    def test_protected_branch_without_track_warns(self):
        status = {"protected": ["feat-x"], "tracks": []}
        (self.sb.root / "shepherd.json").write_text(json.dumps(status))
        self.sb.run(STATUSLINE, session(self.sb.repo))
        self.wait_for_cache()
        rc, out, _ = self.sb.run(STATUSLINE, session(self.sb.repo))
        self.assertIn("protected: claim a track", plain(out))

    def test_not_enrolled_prints_text_not_json(self):
        fake = self.sb.bin / "shepherd"
        fake.write_text(f"#!{BASH}\necho 'not enrolled: run shepherd init'\n")
        self.sb.run(STATUSLINE, session(self.sb.repo))
        self.wait_for_cache()
        rc, out, _ = self.sb.run(STATUSLINE, session(self.sb.repo))
        self.assertEqual(rc, 0)
        self.assertIn("⎇ feat-x │", plain(out))
        self.assertNotIn("track", plain(out))

    def test_shepherd_runs_without_optional_git_locks(self):
        # Otherwise its `git status` calls take index.lock in every track
        # worktree, and a `git add` in another session can fail on it.
        self.sb.run(STATUSLINE, session(self.sb.repo))
        self.wait_for_cache()
        self.assertEqual(self.sb.calls.read_text().split(), ["x", "0"])

    def test_missing_cwd_starts_no_refresher(self):
        gone = str(self.sb.root / "gone")
        data = session(self.sb.repo, workspace={"current_dir": gone}, cwd=gone)
        for _ in range(3):
            rc, out, _ = self.sb.run(STATUSLINE, data)
            self.assertEqual(rc, 0)
            self.assertNotIn("track", out)
        self.assertEqual((self.sb.launch_count(), self.sb.shepherd_calls()), (0, 0))

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root can enter any dir")
    def test_cwd_that_cannot_be_entered_is_cached_as_empty(self):
        locked = self.sb.root / "locked"
        locked.mkdir()
        locked.chmod(0)
        try:
            data = session(self.sb.repo, workspace={"current_dir": str(locked)}, cwd=str(locked))
            self.sb.run(STATUSLINE, data)
            self.wait_for_cache()
            for _ in range(3):
                self.sb.run(STATUSLINE, data)
            self.assertEqual((self.sb.launch_count(), self.sb.shepherd_calls()), (1, 0))
        finally:
            locked.chmod(0o755)

    def test_live_lock_blocks_a_launch_and_a_dead_one_is_taken_over(self):
        self.sb.cache.mkdir(parents=True)
        lock = self.sb.cache / "shepherd-sess-1.lock"
        lock.write_text(f"{int(time.time())}\n")
        self.sb.run(STATUSLINE, session(self.sb.repo))
        self.assertEqual(self.sb.launch_count(), 0)
        lock.write_text(f"{int(time.time()) - 61}\n")  # its refresher died a minute ago
        self.sb.run(STATUSLINE, session(self.sb.repo))
        self.wait_for_cache()
        self.assertEqual((self.sb.launch_count(), self.sb.shepherd_calls()), (1, 1))
        self.assertFalse(lock.exists())

    def test_parallel_cold_renders_make_one_shepherd_call(self):
        data = json.dumps(session(self.sb.repo)).encode()
        procs = [subprocess.Popen([BASH, str(STATUSLINE)], stdin=subprocess.PIPE,
                                  stdout=subprocess.DEVNULL, env=self.sb.env())
                 for _ in range(12)]
        for p in procs:
            p.communicate(data, timeout=10)
        self.wait_for_cache()
        time.sleep(0.3)  # let every launched refresher finish or give up
        self.assertEqual(self.sb.shepherd_calls(), 1)
        self.assertFalse(list(self.sb.cache.glob("*.tmp.*")))

    def test_refresher_skips_a_cache_another_refresher_just_wrote(self):
        self.sb.cache.mkdir(parents=True)
        cache = self.sb.cache / "shepherd-sess-1"
        cache.write_text(f"{self.sb.repo}\n{int(time.time())}\nfeat-x\x1f\x1f\x1f\n")
        r = subprocess.run([BASH, str(STATUSLINE), "--refresh-shepherd", str(self.sb.repo),
                            str(cache)], env=self.sb.env(), timeout=10)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.sb.shepherd_calls(), 0)
        self.assertFalse((self.sb.cache / "shepherd-sess-1.lock").exists())

    def test_refresher_deletes_caches_older_than_a_day(self):
        self.sb.cache.mkdir(parents=True)
        now = time.time()
        ages = {"shepherd-old23h": 23, "shepherd-old25h": 25, "shepherd-old49h": 49,
                "other-file": 49}
        for name, hours in ages.items():
            p = self.sb.cache / name
            p.write_text("x\n")
            os.utime(p, (now - hours * 3600, now - hours * 3600))
        cache = self.sb.cache / "shepherd-sess-1"
        r = subprocess.run([BASH, str(STATUSLINE), "--refresh-shepherd", str(self.sb.repo),
                            str(cache)], env=self.sb.env(), timeout=10)
        self.assertEqual(r.returncode, 0)
        left = sorted(p.name for p in self.sb.cache.iterdir() if p.name != "statusline.log")
        self.assertEqual(left, ["other-file", "shepherd-old23h", "shepherd-sess-1"])

    def test_refresh_works_without_hard_links(self):
        noln = Sandbox(exclude=("ln",), shepherd={"tracks": []})
        try:
            noln.run(STATUSLINE, session(noln.repo))
            cache = noln.cache / "shepherd-sess-1"
            for _ in range(100):
                if cache.exists() and not (noln.cache / "shepherd-sess-1.lock").exists():
                    break
                time.sleep(0.05)
            self.assertEqual(noln.shepherd_calls(), 1)
        finally:
            noln.close()

    def test_render_stays_under_100ms(self):
        self.sb.run(STATUSLINE, session(self.sb.repo))
        self.wait_for_cache()
        data = session(self.sb.repo)
        times = []
        for _ in range(15):
            t = time.perf_counter()
            rc, _, _ = self.sb.run(STATUSLINE, data, "--compact-window", "400000")
            times.append((time.perf_counter() - t) * 1000)
            self.assertEqual(rc, 0)
        self.assertLess(statistics.median(times), 100, times)


class SubagentStatusLine(unittest.TestCase):
    TASKS = {"columns": 80, "tasks": [
        {"id": "t1", "name": "verifier", "type": "local_agent", "agentType": "general-purpose",
         "status": "running", "description": "Check the docs", "label": "Reading docs",
         "model": "us.anthropic.claude-opus-5-5[1m]", "effort": "xhigh",
         "contextWindowSize": 1000000, "tokenCount": 48213,
         "tokenSamples": [1000, 5000, 12000, 30000, 48213]},
        {"id": "t2", "type": "local_agent", "agentType": "Explore", "status": "running",
         "description": "Scan repo", "label": "Scan repo", "tokenCount": 900,
         "tokenSamples": [900]},
        {"type": "local_agent", "tokenCount": 1},  # no id: left to the default row
    ]}

    def setUp(self):
        self.sb = Sandbox()

    def tearDown(self):
        self.sb.close()

    def rows(self, sb, data):
        rc, out, err = sb.run(SUBAGENT, data)
        self.assertEqual(rc, 0, err)
        return [json.loads(l) for l in out.splitlines()]

    def test_one_row_per_task_with_an_id(self):
        rows = self.rows(self.sb, self.TASKS)
        self.assertEqual([r["id"] for r in rows], ["t1", "t2"])
        self.assertEqual(rows[0]["content"],
                         "verifier ▁▁▂▅█ 48k/1000k 4% opus-5-5 xhigh · Reading docs")
        self.assertEqual(rows[1]["content"], "Explore 900 · Scan repo")

    def test_python_fallback_matches_jq(self):
        nojq = Sandbox(exclude=("jq",))
        try:
            self.assertEqual(self.rows(nojq, self.TASKS), self.rows(self.sb, self.TASKS))
        finally:
            nojq.close()

    def test_rows_fit_columns(self):
        data = dict(self.TASKS, columns=20)
        for row in self.rows(self.sb, data):
            self.assertLessEqual(len(row["content"]), 20)
            self.assertTrue(row["content"].endswith("…") or len(row["content"]) < 20)

    def test_rows_are_cut_by_display_width(self):
        # CJK and emoji count 2 columns; both parsers cut at the same place.
        tasks = [dict(self.TASKS["tasks"][0], label="功能分支测试" * 4 + "🚀"),
                 dict(self.TASKS["tasks"][1], label="x" + EDGES)]
        nojq = Sandbox(exclude=("jq",))
        try:
            full = {r["id"]: r["content"] for r in self.rows(self.sb, {"tasks": tasks})}
            for cols in (20, 37, 60, 61, 90, 120):
                data = {"columns": cols, "tasks": tasks}
                rows = self.rows(self.sb, data)
                self.assertEqual(rows, self.rows(nojq, data), cols)
                for r in rows:
                    w = width(r["content"])
                    if r["content"] == full[r["id"]]:
                        self.assertLessEqual(w, cols, (cols, r))
                    else:  # cut as late as the width allows (a wide char may not fit)
                        self.assertTrue(r["content"].endswith("…"), r)
                        self.assertIn(w, (cols - 1, cols), (cols, r))
            for rid, content in full.items():  # exactly as wide as the row: not cut
                for sb in (self.sb, nojq):
                    rows = self.rows(sb, {"columns": width(content), "tasks": tasks})
                    self.assertIn(content, [r["content"] for r in rows], rid)
        finally:
            nojq.close()

    def test_control_characters_become_spaces(self):
        # `content` is rendered as-is, and the text comes from the model or an
        # agent definition: no ESC, BEL, DEL, C1 or newline may pass through.
        task = dict(self.TASKS["tasks"][0], name="v" + CONTROLS, label="l" + CONTROLS,
                    model="m" + CONTROLS, effort="x" + CONTROLS)
        data = {"columns": 400, "tasks": [task, {"id": "t3", "description": "d" + CONTROLS,
                                                 "agentType": "a" + CONTROLS, "tokenCount": 1}]}
        nojq = Sandbox(exclude=("jq",))
        try:
            rows = self.rows(self.sb, data)
            self.assertEqual(rows, self.rows(nojq, data))
        finally:
            nojq.close()
        for r in rows:
            self.assertEqual([hex(ord(c)) for c in r["content"]
                              if ord(c) < 32 or 127 <= ord(c) < 160], [], r)
        self.assertIn("· l ]52;c;ZXZpbA==  [2J   2J  ", rows[0]["content"])
        self.assertTrue(rows[1]["content"].startswith("a ]52;c;"), rows[1])

    def test_a_malformed_task_loses_only_its_own_row(self):
        tasks = [dict(self.TASKS["tasks"][0]), {"id": "bad", "tokenSamples": "oops"},
                 dict(self.TASKS["tasks"][1])]
        data = {"columns": 80, "tasks": tasks}
        nojq = Sandbox(exclude=("jq",))
        try:
            for sb in (self.sb, nojq):
                ids = [r["id"] for r in self.rows(sb, data)]
                self.assertIn("t1", ids)
                self.assertIn("t2", ids)
        finally:
            nojq.close()

    def test_bad_input_prints_nothing(self):
        for data in ("nope", "", "[]", '{"tasks": "x"}'):
            rc, out, _ = self.sb.run(SUBAGENT, data)
            self.assertEqual((rc, out), (0, ""), data)
        bare = Sandbox(exclude=("jq", "python3"))
        try:
            self.assertEqual(bare.run(SUBAGENT, self.TASKS)[:2], (0, ""))
        finally:
            bare.close()


if __name__ == "__main__":
    unittest.main()
