"""herd's headless-run verdicts, run sources, snapshot and acks.

The verdict code is a port of the fleet mod's (mods/fleet/hooks/fleet.ts at 3aa5437).
Two checks hold it to that code:

- TestGolden compares herd with runs_golden.json, what the TypeScript said for a few
  hundred generated logs, questions, file names, globs and text logs. No Node needed.
- TestParity runs the TypeScript itself under Node (22.6 or later, type stripping) on
  the same inputs and on the fixtures, when it finds it: $HERD_FLEET_TS, else
  mods/fleet/hooks/fleet.ts. With $HERD_PARITY_LOGS (a folder of real stream-json logs,
  ~/.claude/logs say) it also judges every *.jsonl there both ways, and prints only
  file names and verdicts.

After a change to the cases below, write the golden file again from the TypeScript:

    git show 3aa5437:mods/fleet/hooks/fleet.ts > <dir>/hooks/fleet.ts
    git show 3aa5437:mods/fleet/tests/fixtures.ts > <dir>/tests/fixtures.ts
    HERD_FLEET_TS=<dir>/hooks/fleet.ts python3 harnesses/herd/tests/test_runs.py --regen
"""

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent.parent
ENTRY = ROOT / "herd.py"
GOLDEN = Path(__file__).resolve().parent / "runs_golden.json"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import herd  # noqa: E402
import runs_fixtures as F  # noqa: E402
from runs_fixtures import HOUR, MIN, NOW  # noqa: E402


def judge(text: str, age: int = 2 * HOUR, running: float = 15) -> dict:
    return herd.verdict_of_stream(herd.facts_of_stream(text), NOW - age, NOW, running)


def rx(src):
    return None if src is None else re.compile(src, re.ASCII)


class TestStreamVerdicts(unittest.TestCase):
    def test_ok_with_turns_cost_and_denials(self):
        self.assertEqual(judge(F.LOG_OK), {"verdict": "ok", "tone": "ok", "why": "ok",
                                           "detail": "success · 7 turns · ≈$0.48 · 0 denied"})

    def test_success_but_a_background_subagent_was_killed_is_unfinished(self):
        f = herd.facts_of_stream(F.LOG_UNFINISHED)
        self.assertEqual(f["stats"], {"spawned": 1, "completed": 0, "failed": 0, "killed": 1, "stopped": 0})
        self.assertEqual(f["tasks"], {"spawned": 1, "completed": 0, "failed": 0, "killed": 1, "stopped": 0})
        self.assertTrue(f["isTerminated"])
        self.assertEqual(judge(F.LOG_UNFINISHED), {
            "verdict": "unfinished", "tone": "fail", "why": "unfinished: 1 subagent killed",
            "detail": "success · 20 turns · ≈$2.42 · 0 denied · subagents 0/1 done · 1 killed"})

    def test_a_subagent_the_model_stopped_is_not_unfinished_work(self):
        f = herd.facts_of_stream(F.LOG_STOPPED_BY_MODEL)
        self.assertEqual(f["stats"], {"spawned": 2, "completed": 1, "failed": 0, "killed": 0, "stopped": 1})
        self.assertEqual((f["tasks"]["spawned"], f["tasks"]["completed"], f["tasks"]["killed"]), (2, 1, 1))
        self.assertEqual(judge(F.LOG_STOPPED_BY_MODEL)["detail"],
                         "success · 9 turns · ≈$0.48 · 0 denied · subagents 1/2 done · 1 stopped by the model")
        self.assertEqual(judge(F.LOG_STOPPED_BY_MODEL)["verdict"], "ok")
        self.assertEqual(judge(F.LOG_STOPPED_BY_OTHER)["why"], "unfinished: 1 subagent killed")
        one_number = F.LOG_STOPPED_BY_MODEL.replace('"killed":{"parent":1,"user":0,"system":0}', '"killed":1')
        self.assertEqual((herd.facts_of_stream(one_number)["stats"]["killed"],
                          herd.facts_of_stream(one_number)["stats"]["stopped"]), (1, 0))

    def test_no_cost_is_unknown_not_zero(self):
        no_cost = F.LOG_OK.replace('"total_cost_usd":0.48,', "")
        self.assertIsNone(herd.facts_of_stream(no_cost)["cost"])
        self.assertEqual(judge(no_cost)["detail"], "success · 7 turns · 0 denied")

    def test_cost_rounds_halves_up_as_javascript_does(self):
        self.assertEqual(judge(F.LOG_OK.replace("0.48", "0.125"))["detail"], "success · 7 turns · ≈$0.13 · 0 denied")
        self.assertEqual(judge(F.LOG_OK.replace("0.48", "1.005"))["detail"], "success · 7 turns · ≈$1.00 · 0 denied")

    def test_a_result_still_waiting_on_background_work(self):
        self.assertEqual(judge(F.LOG_WAITING, 2 * MIN)["why"], "waiting on 1 background subagent")
        self.assertEqual(judge(F.LOG_WAITING, 2 * HOUR)["why"], "unfinished: 1 of 1 subagent not finished")

    def test_denials(self):
        self.assertEqual(herd.facts_of_stream(F.LOG_DENIED)["denied"], 1)
        self.assertEqual(judge(F.LOG_DENIED)["why"], "1 permission denial")

    def test_no_result_running_then_quiet_then_dead(self):
        self.assertEqual(judge(F.LOG_KILLED, 3 * MIN)["verdict"], "running")
        self.assertEqual(judge(F.LOG_KILLED, 20 * MIN),
                         {"verdict": "quiet", "tone": "warn", "why": "no result, no write for 20m: a long tool call, or dead",
                          "detail": "last write 20m ago"})
        self.assertEqual(judge(F.LOG_KILLED, 3 * HOUR), {"verdict": "no result", "tone": "fail",
                                                         "why": "no result event: killed or crashed",
                                                         "detail": "last write 3h ago"})

    def test_an_error_names_its_first_error(self):
        v = judge(F.LOG_FAILED)
        self.assertEqual(v["why"], "failed: Reached maximum number of turns (40)")
        self.assertIn("max_turns", v["detail"])

    def test_two_results(self):
        f = herd.facts_of_stream(F.LOG_TWO_RESULTS)
        self.assertEqual((f["results"], f["turns"], f["cost"]), (2, 32, 4.41))
        self.assertEqual(judge(F.LOG_TWO_RESULTS)["detail"],
                         "success · 32 turns · ≈$4.41 · 1 denied · subagents 1/1 done · 2 results")

    def test_without_stats_the_task_events_then_the_words(self):
        self.assertEqual(herd.facts_of_stream(F.LOG_NO_STATS)["tasks"]["spawned"], 1)
        self.assertEqual(judge(F.LOG_NO_STATS)["why"], "unfinished: 1 of 1 subagent not finished")
        self.assertEqual(judge(F.LOG_SAYS_WAITING)["why"], 'unfinished: says "Now waiting for the review agent to finish."')

    def test_the_words_fallback_only_for_waiting_on_background_work(self):
        def says(text):
            return judge(F.LOG_SAYS_WAITING.replace("Built the page.\\nNow waiting for the review agent to finish.", text))["verdict"]
        for text in ("No longer waiting for the upstream data.", "Waiting for the upstream data to land before next week.",
                     "I'm not waiting on the review agent any more.",
                     "All 3 agents finished; nothing left waiting for the subagents.",
                     "The pipeline is no longer blocked: nobody is waiting on the agents now.",
                     "Done. I was waiting for the background agent, and it has now returned its report.",
                     "Note: the cron is waiting for 2 jobs upstream before Tuesday (not ours)."):
            with self.subTest(text=text):
                self.assertEqual(says(text), "ok")
        for text in ("Started both; still waiting on 2 background tasks.", "I'm still waiting for the review agent."):
            with self.subTest(text=text):
                self.assertEqual(says(text), "unfinished")

    def test_a_failed_subagent_is_unfinished_beside_a_completed_one(self):
        f = herd.facts_of_stream(F.LOG_SUBAGENT_FAILED)
        self.assertEqual(f["stats"], {"spawned": 2, "completed": 1, "failed": 1, "killed": 0, "stopped": 0})
        self.assertEqual(judge(F.LOG_SUBAGENT_FAILED)["why"], "unfinished: 1 subagent failed")

    def test_spaced_json_and_quoted_events(self):
        self.assertIn('"type": "result"', F.LOG_OK_SPACED)
        self.assertEqual(judge(F.LOG_OK_SPACED)["verdict"], "ok")
        self.assertEqual(herd.facts_of_stream(F.LOG_KILLED)["results"], 0)
        self.assertEqual(herd.facts_of_stream('{"type":"resu' + F.LOG_OK)["results"], 1)


class TestAsking(unittest.TestCase):
    def test_questions_the_run_needed_answered(self):
        self.assertEqual(judge(F.LOG_ASKED)["why"], "ended on a question: Which do you want?")
        for text in ("Two queries returned no rows.\n\n1. Publish anyway\n2. Wait\n\n**Shall I go ahead?**",
                     "The data is late. Should I publish with the old data or wait?",
                     "Email drafted. Should I send it now or keep it as a draft?"):
            with self.subTest(text=text):
                self.assertTrue(herd.is_asking(text))

    def test_offers_pleasantries_and_summaries_are_not(self):
        for text in ("Report written to output/report.md. Want me to also email it?",
                     "Report written to output/report.md.\n\nShall I send it?", "Done.",
                     "Root cause still open: is it the late feed or the routing change?",
                     "Report published. Any questions or concerns?",
                     "Done. Would you like me to dig deeper into any of these, or is this sufficient?",
                     "All pages republished. Let me know if anything looks off or needs changes?",
                     "Summary above. Is there anything else you need, or shall I close out?",
                     "Done.\n\n1. Sales report refreshed\n2. Stock report refreshed\n3. Returns report refreshed\n\nAnything else?",
                     "Finished the report.\n\nNext steps you might consider:\n1. Review the outliers\n2. Rerun on Tuesday\n\n"
                     "Want me to schedule that?",
                     "Done.\n\n1. Sales report refreshed\n2. Stock report refreshed\n3. ✅ Returns\n\nShall I send it?"):
            with self.subTest(text=text):
                self.assertFalse(herd.is_asking(text))


class TestTextLogs(unittest.TestCase):
    MATCH, OK = rx("=== Weekly run done|SKIP:"), rx("done; (?:\\w+_rc=0 ?)+===")

    def test_the_last_matching_line_decides(self):
        self.assertEqual(herd.verdict_of_lines(F.WEEKLY_OK, self.MATCH, self.OK, NOW - HOUR, NOW, 15)["verdict"], "ok")
        self.assertEqual(herd.verdict_of_lines(F.WEEKLY_SKIP, self.MATCH, self.OK, NOW - HOUR, NOW, 15),
                         {"verdict": "failed", "tone": "fail", "why": "[09:30] SKIP: login stale, not running", "detail": ""})

    def test_no_matching_line(self):
        started = "[09:30] === Weekly run start ===\n"
        self.assertEqual(herd.verdict_of_lines(started, self.MATCH, self.OK, NOW - 2 * MIN, NOW, 15)["verdict"], "running")
        self.assertEqual(herd.verdict_of_lines(started, self.MATCH, self.OK, NOW - HOUR, NOW, 15)["verdict"], "no line")

    def test_with_start_only_the_verdict_after_the_last_run_start_counts(self):
        watchdog, fine, start = rx("^watchdog: (todo en orden|PROBLEMAS)"), rx("todo en orden"), rx("^=== pull_leads ===$")
        v = herd.verdict_of_lines
        self.assertEqual(v(F.SHARED_LOG_NEXT_RUN, watchdog, fine, NOW - HOUR, NOW, 15)["verdict"], "ok")
        self.assertEqual(v(F.SHARED_LOG_NEXT_RUN, watchdog, fine, NOW - 5 * MIN, NOW, 15, start)["verdict"], "running")
        self.assertEqual(v(F.SHARED_LOG_NEXT_RUN, watchdog, fine, NOW - HOUR, NOW, 15, start)["why"],
                         "no line matching /^watchdog: (todo en orden|PROBLEMAS)/ after the last run start")
        self.assertEqual(v(F.SHARED_LOG_OK, watchdog, fine, NOW - HOUR, NOW, 15, start)["why"], "watchdog: todo en orden")
        self.assertEqual(v("watchdog: todo en orden\n", watchdog, fine, NOW - HOUR, NOW, 15, start)["why"],
                         "no run start matching /^=== pull_leads ===$/")


class TestSources(unittest.TestCase):
    def test_globs_and_a_file(self):
        got = herd.sources_of(["Nightly=~/logs/n-*.jsonl", "/var/log/x-*.jsonl", "  "], F.SOURCES_FILE)
        self.assertEqual([(r["label"], r["glob"], r["expectEveryHours"]) for r in got["runs"]],
                         [("Nightly", "~/logs/n-*.jsonl", None), ("runs", "/var/log/x-*.jsonl", None),
                          ("Nightly", f"{F.LOGS}/nightly-*.jsonl", 30)])
        self.assertEqual([(ln["label"], ln["match"].pattern) for ln in got["lines"]], [("Weekly", "=== Weekly run done|SKIP:")])
        self.assertEqual(got["errors"], [])

    def test_a_malformed_entry_is_named_and_skipped(self):
        got = herd.sources_of([], {"runs": [{"glob": "/x/*.jsonl"}, {"label": "A", "glob": "/a/*.jsonl"}],
                                   "lines": [{"label": "B", "glob": "/b/*.log", "match": "("}]})
        self.assertEqual([r["label"] for r in got["runs"]], ["A"])
        self.assertEqual(got["lines"], [])
        self.assertRegex("\n".join(got["errors"]), r"runs\[0\] needs a label and a glob")
        self.assertRegex("\n".join(got["errors"]), r"lines\[0\]\.match: bad pattern")
        self.assertEqual(herd.sources_of([], [1, 2])["errors"], ["sources file: not a JSON object"])

    def test_a_bad_ok_or_start_skips_its_entry(self):
        got = herd.sources_of([], {"lines": [{"label": "A", "glob": "/a/*.log", "match": "done", "ok": "("},
                                             {"label": "B", "glob": "/b/*.log", "match": "done", "start": "["},
                                             {"label": "C", "glob": "/c/*.log", "match": "done", "start": "^=== start"}]})
        self.assertEqual([(ln["label"], ln["start"].pattern) for ln in got["lines"]], [("C", "^=== start")])

    def test_glob_parts(self):
        folder, pattern = herd.glob_parts("~/logs/nightly-*.jsonl", F.HOME)
        self.assertEqual(folder, f"{F.HOME}/logs")
        self.assertTrue(pattern.search("nightly-a-2026-10-05.jsonl"))
        self.assertFalse(pattern.search("nightly-a-2026-10-05.log"))
        with self.assertRaisesRegex(ValueError, "absolute"):
            herd.glob_parts("logs/*.jsonl", F.HOME)
        with self.assertRaisesRegex(ValueError, "wildcards only in the file name"):
            herd.glob_parts("/logs/*/x.jsonl", F.HOME)

    def test_run_names_and_picking(self):
        for name, want in (("nightly-retail-2026-10-05.jsonl", "nightly-retail"), ("nightly_20261005T0300.jsonl", "nightly"),
                           ("plain.jsonl", "plain"), ("run-2026-10-09-2.jsonl", "run"), ("nightly_20261009_0930.jsonl", "nightly"),
                           ("run-1728468000.jsonl", "run-1728468000"), ("build-12345678.jsonl", "build-12345678"),
                           ("v20261009.jsonl", "v20261009"), ("2026-10-09.jsonl", "2026-10-09")):
            with self.subTest(name=name):
                self.assertEqual(herd.run_name_of(name), want)
        files = [{"name": "a-2026-10-09.jsonl", "mtimeMs": NOW - HOUR}, {"name": "a-2026-10-08.jsonl", "mtimeMs": NOW - 25 * HOUR},
                 {"name": "b-2026-10-01.jsonl", "mtimeMs": NOW - 9 * 24 * HOUR}, {"name": "c-2026-10-09.jsonl", "mtimeMs": NOW - 2 * HOUR}]
        jsonl = re.compile(r"\.jsonl$")
        self.assertEqual([f["name"] for f in herd.pick_logs(files, jsonl, NOW, 192, 12)], ["a-2026-10-09.jsonl", "c-2026-10-09.jsonl"])
        self.assertEqual([f["name"] for f in herd.pick_logs(files, jsonl, NOW, 192, 1)], ["a-2026-10-09.jsonl"])

    def test_sources_path(self):
        saved = dict(os.environ)
        tmp = Path(tempfile.mkdtemp(prefix="herd-src-"))
        try:
            os.environ.pop("HERD_SOURCES", None)
            os.environ["XDG_CONFIG_HOME"] = str(tmp)
            self.assertIsNone(herd.sources_path())
            (tmp / "herd").mkdir()
            (tmp / "herd" / "sources.json").write_text("{}")
            self.assertEqual(herd.sources_path(), str(tmp / "herd" / "sources.json"))
            os.environ["HERD_SOURCES"] = ""
            self.assertIsNone(herd.sources_path())
            self.assertEqual(herd.sources_path("/x.json"), "/x.json")
        finally:
            os.environ.clear()
            os.environ.update(saved)
            shutil.rmtree(tmp, ignore_errors=True)


class TestTracks(unittest.TestCase):
    def test_tracks_joined_to_their_owners(self):
        repos, tracks = herd.tracks_of(F.STATUS, NOW, 24 * HOUR)
        self.assertEqual(repos, [{"name": "demo-repo", "primary": "/work/demo-repo"}])
        rows = [{"sessionId": "aaaaaaaa-0000-4000-8000-000000000001", "isBlocked": True, "waitingFor": "permission prompt"},
                {"sessionId": "aaaaaaaa-0000-4000-8000-000000000002", "isBlocked": False, "waitingFor": None}]
        tracks, rows = herd.joined(tracks, rows)
        self.assertEqual([(t["name"], t["status"], t["isBlocked"], t["isStale"]) for t in tracks],
                         [("parser-fix", "active", True, False), ("docs", "review", False, False), ("old-idea", "active", False, True)])
        self.assertEqual(tracks[0]["waitingFor"], "permission prompt")
        self.assertEqual((tracks[1]["reviewId"], tracks[1]["reviewState"], tracks[1]["checks"], tracks[1]["checkedAt"]),
                         ("6", "open", "passing", NOW - 3 * HOUR))
        self.assertEqual(rows[1]["track"], "demo-repo/docs")
        with self.assertRaisesRegex(ValueError, "not shepherd status JSON"):
            herd.tracks_of({"nope": 1}, NOW, HOUR)

    def test_conflicts_count_tracks(self):
        status = json.loads(json.dumps(F.STATUS))
        status["repos"][0]["overlaps"] = [{"a": "parser-fix", "b": "docs", "conflicts": ["src/x.ts"]},
                                          {"a": "parser-fix", "b": "old-idea", "conflicts": ["src/y.ts"]},
                                          {"a": "docs", "b": "old-idea", "conflicts": []}]
        self.assertEqual([t["conflicts"] for t in herd.tracks_of(status, NOW, HOUR)[1]], [2, 1, 1])


class Disk(unittest.TestCase):
    """Real files under a temporary HOME, with mtimes set relative to now."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="herd-runs-"))
        self.saved = dict(os.environ)
        herd._log_cache.clear()
        os.environ.update(HOME=str(self.tmp), XDG_STATE_HOME=str(self.tmp / "state"),
                          XDG_CONFIG_HOME=str(self.tmp / "config"), NO_COLOR="1")
        self.now = time.time() * 1000

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def file(self, rel: str, text: str, age_ms: float) -> Path:
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        t = (self.now - age_ms) / 1000
        os.utime(p, (t, t))
        return p

    def sources(self, data) -> str:
        return str(self.file("sources.json", json.dumps(data), 0))


class TestReading(Disk):
    def test_each_source_judged_and_a_quiet_schedule_is_late(self):
        src = self.sources({"runs": [{"label": "Nightly", "glob": "~/logs/nightly-*.jsonl", "expectEveryHours": 30}],
                            "lines": [{**F.SOURCES_FILE["lines"][0], "glob": "~/logs/weekly-*.log"}]})
        self.file("logs/nightly-report-2026-10-07.jsonl", F.LOG_OK, 50 * HOUR)
        self.file("logs/weekly-2026-10-05.log", F.WEEKLY_SKIP, 96 * HOUR)
        got = herd.read_runs(src, self.now)
        self.assertIsNone(got["sourcesError"])
        self.assertEqual([(r["source"], r["kind"], r["name"], r["verdict"]) for r in got["runs"]],
                         [("Nightly", "stream", "nightly-report", "ok"), ("Nightly", "late", "Nightly", "late"),
                          ("Weekly", "line", "weekly", "failed")])
        self.assertEqual(got["runs"][1]["why"], "no run for 2d (expected every 30h)")
        self.assertEqual(got["runs"][0]["facts"], {"results": 1, "turns": 7, "cost": 0.48, "denied": 0,
                                                   "subagents": {"spawned": 0, "completed": 0, "failed": 0, "killed": 0,
                                                                 "stopped": 0, "pending": 0}})

    def test_a_log_is_read_once_per_version_and_appends_are_read_from_where_it_stopped(self):
        src = self.sources({"runs": [{"label": "N", "glob": "~/logs/n-*.jsonl"}]})
        path = self.file("logs/n-2026-10-09.jsonl", F.INIT + "\n", 20 * MIN)
        self.assertEqual(herd.read_runs(src, self.now)["runs"][0]["verdict"], "quiet")
        with open(path, "a") as f:                      # the run finishes: its result is appended
            f.write(F.LOG_UNFINISHED.split("\n", 1)[1])
        got = herd.read_runs(src, self.now)["runs"][0]
        self.assertEqual((got["verdict"], got["why"]), ("unfinished", "unfinished: 1 subagent killed"))
        path.write_text(F.LOG_OK + "padding\n" * 300)   # rewritten in place, longer than before
        self.assertEqual(herd.read_runs(src, self.now)["runs"][0]["verdict"], "ok")

    def test_a_last_line_without_its_newline_counts_until_it_is_whole(self):
        src = self.sources({"runs": [{"label": "N", "glob": "~/logs/n-*.jsonl"}]})
        path = self.file("logs/n-1.jsonl", F.LOG_OK.rstrip("\n"), HOUR)
        self.assertEqual(herd.read_runs(src, self.now)["runs"][0]["verdict"], "ok")
        with open(path, "a") as f:
            f.write("\n" + F.result(subtype="error_max_turns", is_error=True) + "\n")
        self.assertEqual(herd.read_runs(src, self.now)["runs"][0]["verdict"], "failed")

    def test_a_text_log_several_jobs_append_to(self):
        src = self.sources({"lines": [{"label": "S", "glob": "~/shared.log", "start": "^=== pull_leads ===$",
                                       "match": "^watchdog: (todo en orden|PROBLEMAS)", "ok": "todo en orden"}]})
        path = self.file("shared.log", F.SHARED_LOG_OK, HOUR)
        self.assertEqual(herd.read_runs(src, self.now)["runs"][0]["why"], "watchdog: todo en orden")
        with open(path, "a") as f:
            f.write("=== pull_leads ===\nok\n")
        os.utime(path, None)
        self.assertEqual(herd.read_runs(src, time.time() * 1000)["runs"][0]["verdict"], "running")
        with open(path, "a") as f:
            f.write("watchdog: PROBLEMAS DETECTADOS\n")
        self.assertEqual(herd.read_runs(src, time.time() * 1000)["runs"][0]["verdict"], "failed")

    def test_a_missing_folder_or_sources_file_is_named(self):
        src = self.sources({"runs": [{"label": "runs", "glob": "/nowhere/*.jsonl"}]})
        got = herd.read_runs(src, self.now)
        self.assertEqual(got["sources"], [{"label": "runs", "kind": "stream", "glob": "/nowhere/*.jsonl",
                                           "error": "No such file or directory: /nowhere", "count": 0}])
        self.assertRegex(herd.read_runs("/nope.json", self.now)["sourcesError"], r"^sources file /nope\.json: ")

    def test_a_log_two_sources_name_is_one_run(self):
        src = self.sources({"runs": [{"label": "N", "glob": "~/logs/n-*.jsonl"}, {"label": "N", "glob": "~/logs/n-*"},
                                     {"label": "N", "glob": "~/other/n-*.jsonl", "expectEveryHours": 30},
                                     {"label": "N", "glob": "~/third/n-*.jsonl", "expectEveryHours": 30}]})
        self.file("logs/n-a-2026-10-09.jsonl", F.LOG_FAILED, HOUR)
        self.file("other/x.txt", "", 0)
        self.file("third/x.txt", "", 0)
        got = herd.read_runs(src, self.now)
        self.assertEqual([r["verdict"] for r in got["runs"]], ["failed", "late", "late"])
        self.assertEqual(len({r["key"] for r in got["runs"]}), 3)


FAKE_SHEPHERD = "#!/bin/sh\ncat <<'EOF'\n%s\nEOF\n"


class TestSnapshotAndAcks(Disk):
    def herd(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ENTRY), *args], capture_output=True, text=True)

    def setUp(self):
        super().setUp()
        self.claude = self.tmp / "claude"
        (self.claude / "sessions").mkdir(parents=True)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude)
        fake = self.tmp / "fake-shepherd"
        fake.write_text(FAKE_SHEPHERD % json.dumps(F.STATUS))
        fake.chmod(0o755)
        os.environ["HERD_SHEPHERD"] = str(fake)
        self.proc = subprocess.Popen(["sleep", "600"])
        entry = {"pid": self.proc.pid, "procStart": herd.proc_start(self.proc.pid),
                 "sessionId": "aaaaaaaa-0000-4000-8000-000000000001", "cwd": str(self.tmp), "name": "demo-1",
                 "nameSource": "user", "kind": "interactive", "status": "waiting", "waitingFor": "permission prompt",
                 "startedAt": int(self.now - HOUR), "updatedAt": int(self.now), "statusUpdatedAt": int(self.now)}
        (self.claude / "sessions" / f"{self.proc.pid}.json").write_text(json.dumps(entry))
        self.src = self.sources({"runs": [{"label": "Nightly", "glob": "~/logs/n-*.jsonl"}]})
        self.file("logs/n-deep-2026-10-09.jsonl", F.LOG_UNFINISHED, 5 * HOUR)
        os.environ["HERD_SOURCES"] = self.src

    def tearDown(self):
        self.proc.kill()
        self.proc.wait()
        super().tearDown()

    def test_snapshot_joins_tracks_sessions_and_runs(self):
        r = self.herd("snapshot", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        snap = json.loads(r.stdout)
        self.assertEqual(snap["schema"], 3)
        self.assertEqual((snap["tracksError"], snap["tracksMissing"], snap["sessionsError"], snap["sourcesError"]),
                         (None, False, None, None))
        self.assertEqual([(t["name"], t["isBlocked"]) for t in snap["tracks"]],
                         [("parser-fix", True), ("docs", False), ("old-idea", False)])
        [s] = snap["sessions"]
        self.assertEqual((s["name"], s["kind"], s["state"], s["status"], s["isBlocked"], s["track"]),
                         ("demo-1", "interactive", "blocked", "waiting", True, "demo-repo/parser-fix"))
        [run] = snap["runs"]
        self.assertEqual((run["verdict"], run["facts"]["subagents"]["killed"]), ("unfinished", 1))
        self.assertEqual(snap["acked"], [])
        self.assertEqual(set(snap), {"schema", "at", "tookMs", "host", "tracksOn", "repos", "tracks", "tracksError",
                                     "tracksMissing", "sessionsOn", "sessions", "sessionsError", "sessionsMissing",
                                     "runs", "sources", "sourcesFile", "sourcesError", "acked"})

    def test_each_part_fails_on_its_own(self):
        os.environ["HERD_SHEPHERD"] = ""
        snap = json.loads(self.herd("snapshot", "--no-sessions").stdout)
        self.assertEqual((snap["tracksMissing"], snap["tracks"], snap["sessionsOn"], snap["sessions"]), (True, [], False, []))
        self.assertEqual(snap["runs"][0]["verdict"], "unfinished")
        broken = self.tmp / "broken-shepherd"
        broken.write_text("#!/bin/sh\necho 'FileNotFoundError: repos.json' >&2\nexit 1\n")
        broken.chmod(0o755)
        os.environ["HERD_SHEPHERD"] = str(broken)
        snap = json.loads(self.herd("snapshot").stdout)
        self.assertEqual((snap["tracksMissing"], snap["tracksError"]),
                         (False, "shepherd status exited 1: FileNotFoundError: repos.json"))
        self.assertEqual(len(snap["sessions"]), 1)

    def test_acks_are_shared_and_kept_to_the_newest(self):
        self.assertEqual(self.herd("ack").returncode, 2)
        r = self.herd("ack", "--failed")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "acked 1 run"))
        snap = json.loads(self.herd("snapshot").stdout)
        self.assertEqual(snap["acked"], [snap["runs"][0]["key"]])
        self.assertIn("acked · unfinished", self.herd("runs").stdout)
        herd.ack([f"k{i}" for i in range(herd.ACKED_MAX + 10)])
        keys = herd.acked()
        self.assertEqual((len(keys), keys[-1]), (herd.ACKED_MAX, f"k{herd.ACKED_MAX + 9}"))
        self.assertEqual(oct((self.tmp / "state" / "herd" / "acked.json").stat().st_mode & 0o777), "0o600")

    def test_runs_lists_each_run_with_its_numbers(self):
        out = self.herd("runs").stdout
        self.assertIn("unfinished: 1 subagent killed", out)
        self.assertIn("success · 20 turns · ≈$2.42", out)
        self.assertIn("1 failure not acked: herd ack --failed", out)
        os.environ["HERD_SOURCES"] = ""
        self.assertIn("no run sources", self.herd("runs").stdout)


# --- the TypeScript, as reference ---------------------------------------------------------

TEXTS = [
    "Report written to output/report.md.", "Done.", "", "Two queries returned no rows.\n\n1. Publish anyway\n2. Wait\n\nWhich do you want?",
    "The data is late. Should I publish with the old data or wait?", "Want me to also email it?",
    "Done.\n\n1. Sales report refreshed\n2. Stock report refreshed\n\nShall I send it?",
    "Now waiting for the deep-dive agent to finish.", "Started both; still waiting on 2 background tasks.",
    "No longer waiting for the upstream data.", "I'm still waiting for the review agent.",
    "Note: the cron is waiting for 2 jobs upstream before Tuesday (not ours).",
    "Done ✅ shipped 🚀", "Which version 🙂 do you want?", "Report done.  ",
    "1. Publish now\n2. Wait\n\nShould I go ahead?", "a) Publish\nb) Wait\n(c) Skip\nPick one?",
    "Now waiting for the review agent " + "🚀" * 25 + " to finish.",
    "Which do you want: " + "é🚀" * 30 + "?", "x" * 450 + "\nWhich one?", "  \n\n  ", "**Shall we publish or hold?**  ",
    "Done.\n\n1. Report built\n2. Email sent\n3. Pages up to date\n\nWhich next?",
    "We're waiting on the subagents.", "I am just waiting for 3 workers", "The jobs have since finished.",
    42,
]


def _stats(rnd, n_tasks: int):
    kind = rnd.choice(["undef", "null", "full", "full", "full", "number", "old"])
    if kind == "undef":
        return F.UNDEF
    if kind == "null":
        return None
    spawned = rnd.choice([0, n_tasks, n_tasks, n_tasks + 1, 3])
    done = rnd.randint(0, spawned)
    st = F.stats(spawned, done, rnd.choice([0, 0, 1]), rnd.choice([0, 0, 1]), rnd.choice([0, 0, 1]))
    st["failed"] = rnd.choice([0, 0, 0, 1])
    if kind == "number":
        st["killed"] = rnd.choice([0, 1, 2])
    if kind == "old":
        st = {"started_in_background": spawned, "completed": done, "killed": {"system": rnd.choice([0, 1])}}
    return st


def _stream_case(rnd) -> dict:
    lines = [F.INIT]
    if rnd.random() < 0.3:
        lines.append(F.ASSISTANT)
    tasks = [f"t{i}" for i in range(rnd.choice([0, 0, 1, 1, 2, 3]))]
    for t in tasks:
        lines.append(F.j({"type": "system", "subtype": "task_started", "task_id": t, "is_backgrounded": rnd.random() < 0.85,
                          "task_type": rnd.choice(["local_agent", "local_agent", "local_agent", "local_bash"])}))
    if rnd.random() < 0.15:
        lines.append(F.TERMINATING)
    for t in tasks:
        end = rnd.choice([None, "completed", "completed", "failed", "killed", "stopped", "kill-notice", "updated"])
        if end in ("completed", "failed", "stopped"):
            lines.append(F.j({"type": "system", "subtype": "task_notification", "task_id": t, "status": end}))
        elif end == "killed":
            lines.append(F.j({"type": "system", "subtype": "task_updated", "task_id": t, "patch": {"status": "killed"}}))
        elif end == "kill-notice":
            lines.append(F.j({"type": "system", "subtype": "task_updated", "task_id": t, "patch": {"status": "killed"}}))
            lines.append(F.j({"type": "system", "subtype": "task_notification", "task_id": t, "status": "completed"}))
        elif end == "updated":
            lines.append(F.j({"type": "system", "subtype": "task_updated", "task_id": t, "patch": {"status": "completed"}}))
    for _ in range(rnd.choice([0, 0, 1, 2])):
        lines.append(F.j({"type": "system", "subtype": "permission_denied", "tool_name": "Bash"}))
    for _ in range(rnd.choice([0, 1, 1, 1, 1, 2])):
        sub = rnd.choice(["success"] * 7 + ["error_max_turns", "error_during_execution"])
        fields = {"subtype": sub, "is_error": sub != "success" or rnd.random() < 0.05,
                  "terminal_reason": rnd.choice(["completed"] * 6 + [F.UNDEF, None, "max_turns", "aborted_tools"]),
                  "num_turns": rnd.choice([0, 1, 7, 23, 3.7, F.UNDEF, "9"]),
                  "total_cost_usd": rnd.choice([F.UNDEF, 0.125, 0.48, 2.425, 1.005, 0.0, 12.3456, 0.375, 7, -1]),
                  "permission_denials": [{"tool_name": "Bash"}] * rnd.choice([0, 0, 0, 1, 2]),
                  "subagent_stats": _stats(rnd, len(tasks)), "result": rnd.choice(TEXTS)}
        if rnd.random() < 0.2:
            fields["errors"] = rnd.choice([[], ["Reached maximum number of turns (40)"], ["line one\nline two " + "y" * 120], [5, "x"]])
        lines.append(F.result(**fields))
    text = "\n".join(lines) + ("\n" if rnd.random() < 0.9 else "")
    if rnd.random() < 0.1:
        text = text.replace('":', '": ')
    return {"text": text, "ageMs": rnd.choice([0, 1, 10, 16, 25, 31, 40, 120, 1440]) * MIN, "running": rnd.choice([15, 20, 30])}


LINE_PATTERNS = [("=== Weekly run done|SKIP:", "done; (?:\\w+_rc=0 ?)+===", None),
                 ("^watchdog: (todo en orden|PROBLEMAS)", "todo en orden", "^=== pull_leads ===$"),
                 ("^watchdog: (todo en orden|PROBLEMAS)", "todo en orden", None),
                 ("^ts ", None, None), ("a/b|done", None, "^start")]
LINE_TEXTS = [F.WEEKLY_OK, F.WEEKLY_SKIP, F.SHARED_LOG_OK, F.SHARED_LOG_NEXT_RUN, "[09:30] === Weekly run start ===\n",
              "watchdog: todo en orden\n", "", "\n\n", "ts 2026-10-05T07:00:02+00:00\npull_leads 0\n",
              "[09:43:53][wbr-weekly] === Weekly run done; wbr_rc=0 retail_rc=1 d2d_rc=0 ===\n",
              "start\nx a/b y\n", "  done  \r\n", "start\n", "x " + "🚀" * 70 + " done\n"]
NAMES = ["nightly-retail-2026-10-05.jsonl", "nightly_20261005T0300.jsonl", "plain.jsonl", "run-2026-10-09-2.jsonl",
         "nightly_20261009_0930.jsonl", "run-1728468000.jsonl", "build-12345678.jsonl", "v20261009.jsonl", "2026-10-09.jsonl",
         "wbr-wbr-inbound-2026-10-12.jsonl", "wbr-weekly-2026-10-12.log", "shortload-weekly.log", "weekly-run.txt", ".hidden",
         "noext", "a.b.c.jsonl", "run-2026-13-01.jsonl", "x_2026-02-30.jsonl", "r-20261009T0930-12.jsonl",
         "r-2026-10-09-1234.jsonl", "r.2026-10-09.07-30-15.log", "r-1999-12-31.jsonl", "r-2100-01-01.jsonl"]
GLOB_NAMES = ["nightly-a-2026-10-05.jsonl", "nightly-a-2026-10-05.log", "x-1.log", "x-12.log", "(x)+[1].log",
              "q.{a,b}", "x.a", "a$b.log"]
GLOBS = ["~/logs/nightly-*.jsonl", "/var/log/x-?.log", "logs/*.jsonl", "/logs/*/x.jsonl", "/logs/", "~",
         "/a.b/(x)+[1].log", "/x/q.{a,b}", "/x/a$b.log", "/x/*", "  /x/x-*.log  "]
SOURCE_FILES = [
    {"globs": ["Nightly=~/logs/n-*.jsonl", "/var/log/x-*.jsonl", "  "], "file": F.SOURCES_FILE},
    {"globs": [], "file": {"runs": [{"glob": "/x/*.jsonl"}, {"label": "A", "glob": "/a/*.jsonl"}],
                           "lines": [{"label": "B", "glob": "/b/*.log", "match": "("}]}},
    {"globs": [], "file": [1, 2]},
    {"globs": [], "file": {"lines": [{"label": "A", "glob": "/a/*.log", "match": "done", "ok": "("},
                                     {"label": "B", "glob": "/b/*.log", "match": "done", "start": "["},
                                     {"label": "C", "glob": "/c/*.log", "match": "done", "start": "^=== start"}]}},
    {"globs": [], "file": json.loads((ROOT / "presets" / "sources.json").read_text())},
    {"globs": [], "file": {"runs": [{"label": "A", "glob": "/a/*", "latest": 0.5, "maxAgeHours": -1,
                                     "runningMinutes": "9", "expectEveryHours": 0}, "x", {"label": "", "glob": "/b"}]}},
    {"globs": ["=x", "a=b=c", "~x=/y", "L =/z/*.log"], "file": None},
]
PICK_FILES = [{"name": "a-2026-10-09.jsonl", "size": 1, "mtimeMs": NOW - HOUR},
              {"name": "a-2026-10-08.jsonl", "size": 1, "mtimeMs": NOW - 25 * HOUR},
              {"name": "b-2026-10-01.jsonl", "size": 1, "mtimeMs": NOW - 9 * 24 * HOUR},
              {"name": "c-2026-10-09.jsonl", "size": 1, "mtimeMs": NOW - 2 * HOUR},
              {"name": "c-2026-10-09.log", "size": 1, "mtimeMs": NOW - 3 * HOUR}]


def cases() -> dict:
    """Every input the golden file holds the TypeScript's answer for; deterministic."""
    rnd = random.Random(41)
    stream = [{"text": text, "ageMs": age, "running": 15} for _, text in sorted(F.LOGS_BY_NAME.items())
              for age in (0, 10 * MIN, 25 * MIN, 40 * MIN, 24 * HOUR)]
    stream += [_stream_case(rnd) for _ in range(240)]
    return {
        "now": NOW, "home": F.HOME, "stream": stream,
        "asking": [t for t in TEXTS if isinstance(t, str)],
        "names": NAMES,
        "lines": [{"text": t, "match": m, "ok": ok, "start": st, "ageMs": age, "running": 15}
                  for m, ok, st in LINE_PATTERNS for t in LINE_TEXTS for age in (5 * MIN, HOUR)],
        "late": [{"source": "Nightly", "glob": "/x/*.jsonl", "kind": "stream", "newest": None, "expect": 30},
                 {"source": "W", "glob": "/x/*.log", "kind": "line", "newest": None, "expect": 30},
                 {"source": "N", "glob": "/g", "kind": "stream", "newest": NOW - 50 * HOUR, "expect": 30},
                 {"source": "N", "glob": "/g", "kind": "stream", "newest": NOW - 10 * HOUR, "expect": 30},
                 {"source": "N", "glob": "/g", "kind": "stream", "newest": NOW - 200 * HOUR, "expect": 7.5},
                 {"source": "N", "glob": "/g", "kind": "stream", "newest": NOW - 5 * HOUR, "expect": None},
                 {"source": "N", "glob": "/g", "kind": "line", "newest": 0, "expect": 30}],
        "cuts": [[t, n] for t in [x for x in TEXTS if isinstance(x, str)] + ["🚀" * 60, "ab🚀cd", " ﻿x　"]
                 for n in (1, 2, 3, 60, 70, 100)],
        "agos": [0, 499, 500, 1499, 1500, 59_499, 59_500, 3_599_499, 3_599_500, 86_399_500, 2 * 86_400_000 + 1, -5000],
        "globs": [[g, GLOB_NAMES] for g in GLOBS],
        "sources": SOURCE_FILES,
        "picks": [{"files": PICK_FILES, "pattern": "\\.jsonl$", "maxAge": a, "latest": n}
                  for a, n in ((192, 12), (192, 1), (192, 0.5), (24, 12), (0.5, 12))],
    }


def _bad_pattern(errors: list) -> list:
    """Error lines with the engine's own words about a bad pattern cut off."""
    return [re.sub(r'(bad pattern "(?:[^"\\]|\\.)*").*', r"\1", e) for e in errors]


def _without_facts(run):
    return None if run is None else {k: v for k, v in run.items() if k != "facts"}


COUNTS = ("spawned", "completed", "failed", "killed", "stopped")


def _facts_view(f: dict) -> list:
    """The counts the verdicts and the Runs view are built on, as a short row."""
    return [f["results"], f["turns"], f["cost"], f["denied"], f["isTerminated"],
            None if f["stats"] is None else [f["stats"][k] for k in COUNTS], [f["tasks"][k] for k in COUNTS]]


def python_outputs(c: dict) -> dict:
    """What herd says for every case, in the bridge's shape."""
    def source_view(got):
        def pat(p):
            return None if p is None else herd._js_source(p)
        return {"runs": got["runs"], "errors": _bad_pattern(got["errors"]),
                "lines": [{**ln, "match": pat(ln["match"]), "start": pat(ln["start"]), "ok": pat(ln["ok"])} for ln in got["lines"]]}

    def glob_view(g, names):
        try:
            folder, pattern = herd.glob_parts(g, c["home"])
        except ValueError:
            return {"error": True}
        return {"dir": folder, "hits": [bool(pattern.search(n)) for n in names]}

    now = c["now"]
    return {
        "stream": [herd.verdict_of_stream(herd.facts_of_stream(x["text"]), now - x["ageMs"], now, x["running"]) for x in c["stream"]],
        "facts": [_facts_view(herd.facts_of_stream(x["text"])) for x in c["stream"]],
        "asking": [herd.is_asking(t) for t in c["asking"]],
        "names": [herd.run_name_of(n) for n in c["names"]],
        "lines": [herd.verdict_of_lines(x["text"], rx(x["match"]), rx(x["ok"]), now - x["ageMs"], now, x["running"], rx(x["start"]))
                  for x in c["lines"]],
        "late": [_without_facts(herd.late_run(x["source"], x["glob"], x["kind"], x["newest"], now, x["expect"])) for x in c["late"]],
        "first": [herd.first_line_of(t, n) for t, n in c["cuts"]],
        "last": [herd.last_line_of(t, n) for t, n in c["cuts"]],
        "agos": [herd.ago_ms(now - d, now) for d in c["agos"]],
        "globs": [glob_view(g, names) for g, names in c["globs"]],
        "sources": [source_view(herd.sources_of(x["globs"], x["file"])) for x in c["sources"]],
        "picks": [[f["name"] for f in herd.pick_logs(x["files"], re.compile(x["pattern"]), now, x["maxAge"], x["latest"])]
                  for x in c["picks"]],
    }


BRIDGE = r"""
import { readFileSync, existsSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const [fleetPath, payloadPath] = process.argv.slice(1)
const F = await import(pathToFileURL(fleetPath).href)
const c = JSON.parse(readFileSync(payloadPath, 'utf8'))
const now = c.now
const re = s => (s === null ? null : new RegExp(s))
const pat = p => (p === null ? null : p.source)
const COUNTS = ['spawned', 'completed', 'failed', 'killed', 'stopped']
const facts = f => [f.results, f.turns, f.cost, f.denied, f.isTerminated, f.stats === null ? null : COUNTS.map(k => f.stats[k]),
  COUNTS.map(k => f.tasks[k])]
const out = {
  stream: c.stream.map(x => F.verdictOfStream(F.factsOfStream(x.text), now - x.ageMs, now, x.running)),
  facts: c.stream.map(x => facts(F.factsOfStream(x.text))),
  asking: c.asking.map(t => F.isAsking(t)),
  names: c.names.map(n => F.runNameOf(n)),
  lines: c.lines.map(x => F.verdictOfLines(x.text, re(x.match), re(x.ok), now - x.ageMs, now, x.running, re(x.start))),
  late: c.late.map(x => F.lateRun(x.source, x.glob, x.kind, x.newest, now, x.expect)),
  first: c.cuts.map(([t, n]) => F.firstLineOf(t, n)),
  last: c.cuts.map(([t, n]) => F.lastLineOf(t, n)),
  agos: c.agos.map(d => F.agoOf(now - d, now)),
  globs: c.globs.map(([g, names]) => {
    try {
      const { dir, pattern } = F.globParts(g, c.home)
      return { dir, hits: names.map(n => pattern.test(n)) }
    } catch {
      return { error: true }
    }
  }),
  sources: c.sources.map(x => {
    const s = F.sourcesOf(x.globs, x.file)
    return { runs: s.runs, errors: s.errors.map(e => e.replace(/(bad pattern "(?:[^"\\]|\\.)*").*/, '$1')),
      lines: s.lines.map(l => ({ ...l, match: pat(l.match), start: pat(l.start), ok: pat(l.ok) })) }
  }),
  picks: c.picks.map(x => F.pickLogs(x.files, new RegExp(x.pattern), now, x.maxAge, x.latest).map(f => f.name)),
  real: (c.real ?? []).map(x => F.verdictOfStream(F.factsOfStream(readFileSync(x.path, 'utf8')), x.mtimeMs, x.now, x.running)),
}
const fixtures = join(dirname(fleetPath), '..', 'tests', 'fixtures.ts')
if (existsSync(fixtures)) {
  const X = await import(pathToFileURL(fixtures).href)
  out.fixtures = Object.fromEntries(Object.entries(X).filter(([k]) => /^(LOG_|WEEKLY_|SHARED_LOG_)/.test(k)))
  out.fixtures.SOURCES_FILE = X.SOURCES_FILE
  out.fixtures.STATUS = X.STATUS
  out.fixtures.NOW = X.NOW
  out.fixtures.long = X.logWithLongResult(50)
}
process.stdout.write(JSON.stringify(out))
"""


def fleet_ts() -> Path:
    return Path(os.environ.get("HERD_FLEET_TS") or REPO / "mods" / "fleet" / "hooks" / "fleet.ts")


def node() -> str:
    """Node that can strip types (22.6+); SkipTest without it."""
    exe = shutil.which("node")
    if exe is None:
        raise unittest.SkipTest("node not found: herd was not compared with the TypeScript it was ported from")
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    got = re.match(r"v(\d+)\.(\d+)", out)
    if got is None or (int(got.group(1)), int(got.group(2))) < (22, 6):
        raise unittest.SkipTest(f"node {out or '?'} cannot strip types (22.6 or later)")
    return exe


def bridge(payload: dict) -> dict:
    exe, ts = node(), fleet_ts()
    if not ts.is_file():
        raise unittest.SkipTest(f"no {ts}: set HERD_FLEET_TS to fleet.ts (git show 3aa5437:mods/fleet/hooks/fleet.ts)")
    with tempfile.TemporaryDirectory(prefix="herd-bridge-") as d:
        p = Path(d) / "payload.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run([exe, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", BRIDGE,
                            str(ts), str(p)], capture_output=True, text=True, timeout=120, check=False)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


def inputs_sha(c: dict) -> str:
    return hashlib.sha256(json.dumps(c, ensure_ascii=False, sort_keys=True).encode("utf-8", "surrogatepass")).hexdigest()


SECTIONS = ("stream", "facts", "asking", "names", "lines", "late", "first", "last", "agos", "globs", "sources", "picks")


class Compare(unittest.TestCase):
    maxDiff = None

    def compare(self, want: dict, got: dict, c: dict):
        for section in SECTIONS:
            for i, (w, g) in enumerate(zip(want[section], got[section])):
                if w != g:
                    shown = c[section][i] if section not in ("facts", "first", "last") else \
                        (c["stream"][i] if section == "facts" else c["cuts"][i])
                    self.fail(f"{section}[{i}] differs\ninput: {json.dumps(shown, ensure_ascii=False)[:600]}\n"
                              f"want: {json.dumps(w, ensure_ascii=False)}\n got: {json.dumps(g, ensure_ascii=False)}")
            self.assertEqual(len(want[section]), len(got[section]), section)


class TestGolden(Compare):
    def test_herd_says_what_the_typescript_said(self):
        if not GOLDEN.exists():
            self.fail(f"no {GOLDEN.name}: write it with --regen (see this file's docstring)")
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        c = cases()
        self.assertEqual(golden["inputs_sha"], inputs_sha(c), "the cases changed: write the golden file again (--regen)")
        self.compare(golden["outputs"], python_outputs(c), c)


class TestParity(Compare):
    def test_against_the_typescript_itself(self):
        c = cases()
        ts = bridge(c)
        self.compare(ts, python_outputs(c), c)

    def test_the_fixtures_are_the_typescript_ones(self):
        ts = bridge({**cases(), "stream": [], "lines": []}).get("fixtures")
        if ts is None:
            self.skipTest("no tests/fixtures.ts beside the fleet.ts")
        ours = {**{k: v for k, v in vars(F).items() if re.match(r"^(LOG_|WEEKLY_|SHARED_LOG_)", k) and isinstance(v, str)},
                "SOURCES_FILE": F.SOURCES_FILE, "STATUS": F.STATUS, "NOW": F.NOW, "long": F.log_with_long_result(50)}
        self.assertEqual(sorted(ts), sorted(ours))
        for k in ts:
            with self.subTest(fixture=k):
                self.assertEqual(ts[k], ours[k])

    def test_real_logs_both_ways(self):
        folder = os.environ.get("HERD_PARITY_LOGS")
        if not folder:
            self.skipTest("set HERD_PARITY_LOGS to a folder of stream-json logs (~/.claude/logs) to judge them both ways")
        now = time.time() * 1000
        real = []
        for p in sorted(Path(os.path.expanduser(folder)).glob("*.jsonl")):
            st = p.stat()
            if st.st_size <= 3_500_000:             # the mod read bigger logs from their tail only
                real.append({"path": str(p), "mtimeMs": st.st_mtime_ns // 1_000_000, "now": now, "running": 20})
        ts = bridge({**cases(), "stream": [], "lines": [], "real": real})["real"]
        herd._log_cache.clear()
        mismatches = []
        for x, want in zip(real, ts):
            acc = herd.fold_log(x["path"], (x["path"], "stream"), herd._STREAM_NEEDLES, herd.fresh_facts, herd.fact_line)
            got = herd.verdict_of_stream(herd.facts_of(acc), x["mtimeMs"], now, 20)
            print(f"  {Path(x['path']).name}: {want['verdict']} / {got['verdict']}", file=sys.stderr)
            if want != got:
                mismatches.append(Path(x["path"]).name)
        self.assertEqual(mismatches, [])
        self.assertTrue(real, f"no *.jsonl under {folder}")


def regen() -> int:
    c = cases()
    ts = bridge(c)
    # One entry per line, so a change reads as a diff; ASCII, since a cut can leave half a
    # surrogate pair, as JavaScript's does.
    def compact(v) -> str:
        return json.dumps(v, separators=(",", ":"))
    head = f'{{"from":{compact("mods/fleet/hooks/fleet.ts at 3aa5437")},"inputs_sha":{compact(inputs_sha(c))},"outputs":{{\n'
    body = ",\n".join(f"{compact(k)}:[\n" + ",\n".join(compact(x) for x in ts[k]) + "\n]" for k in SECTIONS)
    GOLDEN.write_text(head + body + "\n}}\n", encoding="utf-8")
    print(f"wrote {GOLDEN} ({GOLDEN.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--regen"]:
        sys.exit(regen())
    unittest.main()
