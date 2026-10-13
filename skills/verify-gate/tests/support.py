"""Test sandbox: a private HOME and state dir, a temp git repo whose scripts/check verdict
comes from a tracked file, and helpers that pipe real hook payloads (the shape Claude Code
2.1.294+ sends) through the real stop_gate.py entry point."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts" / "stop_gate.py"
INSTALL = ROOT / "scripts" / "install_gate.py"
TEMPLATE = ROOT / "templates" / "check.sh"
sys.path.insert(0, str(ROOT / "scripts"))

# Counts its runs in $RUNS (outside the repo, so counting doesn't change the tree hash).
CHECK = """#!/bin/sh
echo run >> "$RUNS"
[ "$1" = "--fast" ] || [ "$1" = "--full" ] || { echo "ERROR bad flag $1"; exit 127; }
if grep -q GOOD verdict.txt; then echo "CHECK PASS 1/1"; exit 0; fi
i=1; while [ $i -le 30 ]; do echo "noise line $i"; i=$((i+1)); done
echo "FAILED tests/test_x.py::test_verdict"
echo "ERROR tests failed (exit 1)"
exit 1
"""

OLD = "2001-01-01T00:00:00 +0000"  # a commit date well before any session in the tests


def stamp(t: float) -> str:
    """A transcript timestamp, as Claude Code writes them: 2026-10-09T09:15:22.516Z."""
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Ran(tuple):
    """(code, stdout, stderr) of one gate run. code is 2 when the gate blocked, which it does
    with a JSON reply and exit 0; .rc is the real exit status."""

    def __new__(cls, rc, out, err):
        reply = json.loads(out) if out.strip() else {}
        context = (reply.get("hookSpecificOutput") or {}).get("additionalContext", "")
        self = super().__new__(cls, (2 if context and rc == 0 else rc, out, err))
        self.rc, self.out, self.err, self.reply, self.reason = rc, out, err, reply, context
        self.message = reply.get("systemMessage", "")
        return self


class Sandbox:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="verify-gate-test-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.repo = self.tmp / "repo"
        self.runs = self.tmp / "runs.log"
        self.state = self.home / ".claude" / "verify-gate" / "state"
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("CLAUDE", "GIT_", "STOP_GATE", "HARNESS_", "XDG_"))}
        self.env = dict(base, HOME=str(self.home),
                        CLAUDE_PROJECT_DIR=str(self.repo), RUNS=str(self.runs),
                        GIT_CONFIG_GLOBAL=str(self.home / ".gitconfig"), GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com",
                        GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.com")
        (self.home / ".gitconfig").write_text("[init]\n\tdefaultBranch = trunk\n"
                                              "[protocol \"file\"]\n\tallow = always\n")

    def cleanup(self):
        if not os.environ.get("VERIFY_GATE_KEEP_SANDBOX"):  # set it to inspect a failed run
            shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args, cwd=None, old=False) -> str:
        """old=True dates the commit before the session, like a teammate's or upstream's."""
        env = dict(self.env, GIT_COMMITTER_DATE=OLD, GIT_AUTHOR_DATE=OLD) if old else self.env
        p = subprocess.run(["git", *args], cwd=str(cwd or self.repo), env=env,
                           capture_output=True, text=True)
        if p.returncode:
            raise AssertionError(f"git {args}: {p.stderr}")
        return p.stdout

    def write(self, rel, text, root=None, mode=None) -> Path:
        path = (root or self.repo) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if mode is not None:
            path.chmod(mode)
        return path

    def make_repo(self, root=None, check=CHECK, commit=True) -> Path:
        root = root or self.repo
        root.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", cwd=root)
        self.write("verdict.txt", "GOOD\n", root)
        self.write("src/app.py", "x = 1\n", root)
        if check is not None:
            self.write("scripts/check", check, root, mode=0o755)
        if commit:
            self.git("add", ".", cwd=root)
            self.git("commit", "-q", "-m", "init", cwd=root, old=True)
        return root

    @property
    def transcript(self) -> Path:
        return self.tmp / "t.jsonl"

    def run_gate(self, payload, args=(), env=None, raw=None, gate=GATE) -> Ran:
        p = subprocess.run([sys.executable, str(gate), *args],
                           input=raw if raw is not None else json.dumps(payload),
                           capture_output=True, text=True, env=dict(self.env, **(env or {})),
                           cwd=str(self.tmp), timeout=60)
        return Ran(p.returncode, p.stdout, p.stderr)

    def payload(self, prompt="p1", active=False, background=(), session="s1", cwd=None,
                event="Stop", mode="auto", final="done", transcript=None) -> dict:
        payload = {"session_id": session, "transcript_path": str(transcript or self.transcript),
                   "cwd": str(cwd or self.repo), "prompt_id": prompt, "permission_mode": mode,
                   "effort": {"level": "medium"}, "hook_event_name": event,
                   "stop_hook_active": active, "last_assistant_message": final,
                   "background_tasks": list(background), "session_crons": []}
        if prompt is None:
            del payload["prompt_id"]
        return payload

    def stop(self, prompt="p1", active=False, background=(), session="s1", cwd=None,
             args=(), env=None, event="Stop", mode="auto", final="done", gate=GATE,
             transcript=None) -> Ran:
        payload = self.payload(prompt, active, background, session, cwd, event, mode, final,
                               transcript)
        return self.run_gate(payload, args, env, gate=gate)

    def session_start(self, source="startup", session="s1", args=(), env=None, cwd=None,
                      transcript=None) -> Ran:
        payload = {"session_id": session, "cwd": str(cwd or self.repo),
                   "transcript_path": str(transcript or self.transcript),
                   "hook_event_name": "SessionStart", "source": source}
        return self.run_gate(payload, args, env)

    def state_path(self, session="s1") -> Path:
        return self.state / f"{session}.json"

    def shift_start(self, seconds, session="s1"):
        """Pretend the session started `seconds` later, so files written just before it in a
        test predate it (the gate allows a second of slack)."""
        path = self.state_path(session)
        state = json.loads(path.read_text())
        state["start"] += seconds
        path.write_text(json.dumps(state))

    # A transcript in the shape Claude Code 2.1.295 writes: one JSON entry per line, one
    # content block per assistant entry, promptId on user entries. Real entries also carry a
    # "timestamp"; `at` (seconds since the epoch) adds it to those that have none.
    def said(self, *entries, at=None, to=None):
        with (to or self.transcript).open("a") as f:
            for entry in entries:
                if at is not None and isinstance(entry, dict) and "timestamp" not in entry:
                    entry = dict(entry, timestamp=stamp(at))
                f.write(json.dumps(entry) + "\n")

    @staticmethod
    def user(prompt, text="a question"):
        return {"type": "user", "promptId": prompt, "isSidechain": False,
                "message": {"role": "user", "content": text}}

    @staticmethod
    def text(text):
        return {"type": "assistant", "isSidechain": False,
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}

    @staticmethod
    def tool(name, prompt, **inputs):
        return [{"type": "assistant", "isSidechain": False,
                 "message": {"role": "assistant", "content": [
                     {"type": "tool_use", "id": "toolu_1", "name": name, "input": inputs}]}},
                {"type": "user", "promptId": prompt, "isSidechain": False,
                 "message": {"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]}}]

    def run_count(self) -> int:
        return len(self.runs.read_text().splitlines()) if self.runs.exists() else 0

    def log(self) -> str:
        path = self.home / ".claude" / "verify-gate" / "stop_gate.log"
        return path.read_text() if path.exists() else ""

    @staticmethod
    def message(stdout: str) -> str:
        return json.loads(stdout).get("systemMessage", "") if stdout.strip() else ""
