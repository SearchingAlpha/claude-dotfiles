"""Test sandbox for the guards: a private state dir and HOME, temp git repos, and
fake secrets built at run time by concatenation, so no file in this repo holds
a literal credential (the pre-commit scanner would refuse it)."""

from __future__ import annotations

import json
import os
import random
import shutil
import string
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

GUARDS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GUARDS))

_rng = random.Random(20261008)
_B62 = string.ascii_letters + string.digits
B64 = _B62 + "+/"


def rnd(n: int, alphabet: str = _B62, check=None) -> str:
    """Random filler shaped like a real secret: no ascending runs and, for a
    mixed-case alphanumeric alphabet, one that passes looks_random (about 2% of
    truly random 20-40 char strings don't). Re-rolled until it does, so a test
    never depends on which values the shared generator happens to produce."""
    import patterns
    mixed = all(any(f(c) for c in alphabet) for f in (str.isupper, str.islower, str.isdigit))
    while True:
        s = "".join(_rng.choice(alphabet) for _ in range(n))
        if patterns._sequential(s) or (mixed and n >= 16 and not patterns.looks_random(s)):
            continue
        if check is None or check(s):
            return s


def hexs(n: int) -> str:
    return rnd(n, "0123456789abcdef")


def pem(label: str = "RSA PRIVATE KEY", width: int = 64, lines: int = 3, headers: str = ""):
    """(PEM text, its body lines): a fake key shaped like openssl / ssh-keygen output,
    the last body line shorter, as in a real one."""
    body = [rnd(width, B64) for _ in range(lines - 1)] + [rnd(20, B64) + "=="]
    text = ("-----BEGIN " + label + "-----\n" + (headers + "\n" if headers else "")
            + "\n".join(body) + "\n-----END " + label + "-----")
    return text, body


class Fake:
    """One fresh fake credential per attribute access."""

    @property
    def anthropic(self):
        return "sk-" + "ant-" + "api03-" + rnd(40, _B62 + "_-")

    @property
    def aws_id(self):
        return "AK" + "IA" + rnd(16, string.ascii_uppercase + string.digits)

    @property
    def slack(self):
        return "xo" + "xb-" + rnd(11, string.digits) + "-" + rnd(12, string.digits) + "-" + rnd(24)

    @property
    def github(self):
        return "gh" + "p_" + rnd(36)

    @property
    def pem(self):
        return pem()[0]

    @property
    def hex64(self):
        return hexs(64)

    @property
    def jwt(self):
        return "ey" + "J" + rnd(20) + ".ey" + "J" + rnd(30) + "." + rnd(43, _B62 + "_-")


FAKE = Fake()


class Sandbox:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="guards-test-"))
        self.home = self.tmp / "home"
        self.state = self.home / ".claude" / "guards"
        self.state.mkdir(parents=True)
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("CLAUDE", "GIT_"))}
        self.env = dict(base, HOME=str(self.home), CLAUDE_GUARDS_HOME=str(self.state),
                        GIT_CONFIG_GLOBAL=str(self.home / ".gitconfig"), GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com",
                        GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.com")
        (self.home / ".gitconfig").write_text("[init]\n\tdefaultBranch = trunk\n")

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- git -----------------------------------------------------------------------

    def git(self, cwd, *args, check=True) -> subprocess.CompletedProcess:
        p = subprocess.run(["git", *args], cwd=str(cwd), env=self.env, capture_output=True,
                           text=True)
        if check and p.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}")
        return p

    def repo(self, name="repo", remote: Optional[Path] = None) -> Path:
        r = self.tmp / name
        r.mkdir()
        self.git(r, "init", "-q")
        (r / "README.md").write_text("# test\n")
        self.git(r, "add", "README.md")
        self.git(r, "commit", "-q", "-m", "init")
        if remote is not None:
            self.git(r, "remote", "add", "origin", str(remote))
            self.git(r, "push", "-q", "origin", "trunk")
        return r

    def bare(self, name="remote.git") -> Path:
        b = self.tmp / name
        self.git(self.tmp, "init", "-q", "--bare", str(b))
        return b

    # --- hooks -----------------------------------------------------------------------

    def config(self, cfg: dict) -> None:
        (self.state / "config.json").write_text(json.dumps(cfg))

    def run(self, script: str, payload, env: Optional[dict] = None, args=(),
            script_dir: Optional[Path] = None, cwd=None) -> Tuple[int, str, str]:
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        path = (script_dir or GUARDS) / script
        p = subprocess.run([sys.executable, "-I", str(path), *args], input=raw,
                           capture_output=True, text=True, env=env or self.env,
                           cwd=str(cwd or self.tmp), timeout=30)
        return p.returncode, p.stdout, p.stderr

    def launch(self, guard: str, payload, env: Optional[dict] = None) -> Tuple[int, str, str]:
        """The way hooks.json runs a guard: `sh -c 'sh run.sh <guard>'` (shell form)."""
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        cmd = f'sh "{GUARDS / "run.sh"}" {guard}'
        p = subprocess.run(["/bin/sh", "-c", cmd], input=raw, capture_output=True, text=True,
                           env=env or self.env, cwd=str(self.tmp), timeout=30)
        return p.returncode, p.stdout, p.stderr

    def timed(self, guard: str, payload, n: int = 15) -> float:
        """Median wall time in ms for one hook call through the launcher, shells and
        Python start included."""
        times = []
        for _ in range(n):
            t = time.perf_counter()
            self.launch(guard, payload)
            times.append((time.perf_counter() - t) * 1000)
        times.sort()
        return times[len(times) // 2]


def pre(tool: str, tool_input: dict, sid: str = "sess-1", cwd: str = "/") -> dict:
    return {"session_id": sid, "cwd": cwd, "hook_event_name": "PreToolUse",
            "tool_name": tool, "tool_input": tool_input, "tool_use_id": "toolu_1",
            "permission_mode": "default"}


def post(tool: str, tool_input: dict, tool_response, sid: str = "sess-1", cwd: str = "/") -> dict:
    return {"session_id": sid, "cwd": cwd, "hook_event_name": "PostToolUse",
            "tool_name": tool, "tool_input": tool_input, "tool_response": tool_response,
            "tool_use_id": "toolu_1", "duration_ms": 5, "permission_mode": "default"}


def bash_out(stdout: str, stderr: str = "") -> dict:
    """The Bash tool_response shape seen in a live 2.1.294 session."""
    return {"stdout": stdout, "stderr": stderr, "interrupted": False, "isImage": False,
            "noOutputExpected": False}


def read_out(path: str, content: str) -> dict:
    """The Read tool_response shape seen in a live 2.1.294 session."""
    return {"type": "text", "file": {"filePath": path, "content": content,
                                     "numLines": content.count("\n") + 1, "startLine": 1,
                                     "totalLines": content.count("\n") + 1}}


def stop(sid: str = "sess-1") -> dict:
    return {"session_id": sid, "cwd": "/", "hook_event_name": "Stop", "stop_hook_active": False,
            "last_assistant_message": "done", "background_tasks": [], "session_crons": []}
