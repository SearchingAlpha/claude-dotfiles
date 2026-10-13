#!/usr/bin/env python3
"""The contract between herd and the herd mod: what `herd snapshot --json` prints, over a
fake machine (one live session, a fake shepherd, a stream log and a text log), is a
snapshot the mod draws (its own snapshotOf, under Node), and every row has the fields of
the mod's test fixture, no more and no fewer. A change to either side fails here first.

    python3 mods/herd/tests/test_contract.py

Needs Node 22.6 or later (type stripping) and is skipped without it. Python 3.9,
standard library only. `mods/check.sh herd` does not run it.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
MOD = TESTS.parent
REPO = MOD.parent.parent
HERD = REPO / "harnesses" / "herd"
sys.path.insert(0, str(HERD))
sys.path.insert(0, str(HERD / "tests"))

import herd  # noqa: E402
import runs_fixtures as F  # noqa: E402

BRIDGE = r"""
import { readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

const [herdTs, fixturesTs, snapshotPath] = process.argv.slice(1)
const { snapshotOf } = await import(pathToFileURL(herdTs).href)
const { NIGHT } = await import(pathToFileURL(fixturesTs).href)
const real = JSON.parse(readFileSync(snapshotPath, 'utf8'))
const keys = o => (o === null || o === undefined ? null : Object.keys(o).sort())
const shape = s => ({
  top: keys(s),
  repo: keys(s.repos[0]),
  track: keys(s.tracks[0]),
  session: keys(s.sessions[0]),
  run: keys(s.runs.find(r => r.kind === 'stream')),
  facts: keys(s.runs.find(r => r.kind === 'stream')?.facts),
  subagents: keys(s.runs.find(r => r.kind === 'stream')?.facts?.subagents),
  line: keys(s.runs.find(r => r.kind === 'line')),
  source: keys(s.sources[0]),
})
process.stdout.write(JSON.stringify({ drawn: snapshotOf(real) !== null, real: shape(real), fixture: shape(NIGHT) }))
"""

FAKE_SHEPHERD = "#!/bin/sh\ncat <<'EOF'\n%s\nEOF\n"


def node_or_skip(case) -> str:
    exe = shutil.which("node")
    if exe is None:
        case.skipTest("node not found: the contract was not checked")
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    got = re.match(r"v(\d+)\.(\d+)", out)
    if got is None or (int(got.group(1)), int(got.group(2))) < (22, 6):
        case.skipTest(f"node {out or '?'} cannot strip types (22.6 or later)")
    return exe


class TestContract(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="herd-contract-"))
        self.proc = subprocess.Popen(["sleep", "600"])
        claude = self.tmp / "claude"
        (claude / "sessions").mkdir(parents=True)
        now = int(time.time() * 1000)
        entry = {"pid": self.proc.pid, "procStart": herd.proc_start(self.proc.pid),
                 "sessionId": "aaaaaaaa-0000-4000-8000-000000000001", "cwd": str(self.tmp), "name": "demo-1",
                 "nameSource": "user", "kind": "interactive", "status": "waiting", "waitingFor": "permission prompt",
                 "startedAt": now - 3_600_000, "updatedAt": now, "statusUpdatedAt": now}
        (claude / "sessions" / f"{self.proc.pid}.json").write_text(json.dumps(entry))
        shepherd = self.tmp / "shepherd"
        shepherd.write_text(FAKE_SHEPHERD % json.dumps(F.STATUS))
        shepherd.chmod(0o755)
        logs = self.tmp / "logs"
        logs.mkdir()
        (logs / "nightly-deep-dive-2026-10-09.jsonl").write_text(F.LOG_UNFINISHED)
        (logs / "weekly-2026-10-09.log").write_text(F.WEEKLY_OK)
        sources = self.tmp / "sources.json"
        sources.write_text(json.dumps({"runs": [{"label": "Nightly", "glob": "~/logs/nightly-*.jsonl"}],
                                       "lines": [{**F.SOURCES_FILE["lines"][0], "glob": "~/logs/weekly-*.log"}]}))
        self.env = dict(os.environ, HOME=str(self.tmp), CLAUDE_CONFIG_DIR=str(claude), HERD_SHEPHERD=str(shepherd),
                        HERD_SOURCES=str(sources), XDG_STATE_HOME=str(self.tmp / "state"))

    def tearDown(self):
        self.proc.kill()
        self.proc.wait()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_what_herd_prints_is_what_the_mod_draws(self):
        exe = node_or_skip(self)
        r = subprocess.run([sys.executable, str(HERD / "herd.py"), "snapshot", "--json"], capture_output=True,
                           text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        snap = self.tmp / "snapshot.json"
        snap.write_text(r.stdout)
        out = subprocess.run([exe, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", BRIDGE,
                              str(MOD / "hooks" / "herd.ts"), str(TESTS / "fixtures.ts"), str(snap)],
                             capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout)
        self.assertTrue(got["drawn"], "the mod's snapshotOf refuses what herd snapshot prints")
        for part, fields in got["fixture"].items():
            with self.subTest(part=part):
                self.assertIsNotNone(got["real"][part], f"herd printed no {part} row to compare")
                self.assertEqual(got["real"][part], fields)


if __name__ == "__main__":
    unittest.main()
