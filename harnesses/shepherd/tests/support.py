"""Test sandbox: a private HOME, temp git remotes (main branch `trunk`, so no
name is assumed), fake live Claude sessions and a fake `gh` on PATH."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "shepherd.py"
sys.path.insert(0, str(ROOT))

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as f:
    f.write(json.dumps(args) + "\n")
state_path = os.environ["FAKE_GH_STATE"]
state = json.load(open(state_path)) if os.path.exists(state_path) else {}
if args[:2] == ["auth", "status"]:
    sys.exit(0)
if args[:2] == ["pr", "list"]:
    print(json.dumps(state.get("list", [])))
    sys.exit(0)
if args[:2] == ["pr", "create"]:
    body = sys.stdin.read()
    state["created_body"] = body
    state["list"] = [{"number": 7, "url": "https://github.com/o/r/pull/7"}]
    json.dump(state, open(state_path, "w"))
    print("https://github.com/o/r/pull/7")
    sys.exit(0)
if args[:2] == ["pr", "view"]:
    print(json.dumps(state.get("view", {"state": "OPEN", "isDraft": False, "reviewDecision": "",
                                         "statusCheckRollup": [], "url": "https://github.com/o/r/pull/7",
                                         "title": "t", "updatedAt": "", "comments": [], "reviewRequests": []})))
    sys.exit(0)
sys.exit(1)
'''


def proc_start(pid: int) -> str:
    stat = Path(f"/proc/{pid}/stat").read_text()
    return stat.rsplit(")", 1)[-1].split()[19]


class Sandbox:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shepherd-test-"))
        self.home = self.tmp / "home"
        self.claude = self.home / ".claude"
        (self.claude / "sessions").mkdir(parents=True)
        (self.claude / "projects").mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.home / ".local" / "bin").mkdir(parents=True)
        os.symlink(ENTRY, self.home / ".local" / "bin" / "shepherd")
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.gh_log = self.tmp / "gh.log"
        self.gh_state = self.tmp / "gh.json"
        self._procs: list[subprocess.Popen] = []
        base = {k: v for k, v in os.environ.items()
                if not k.startswith(("CLAUDE", "GIT_", "SHEPHERD"))}
        self.env = dict(base, HOME=str(self.home), SHEPHERD_HOME=str(self.claude / "shepherd"),
                        SHEPHERD_CLAUDE_DIR=str(self.claude), PATH=f"{self.bin}:{base['PATH']}",
                        GIT_CONFIG_GLOBAL=str(self.home / ".gitconfig"), GIT_CONFIG_NOSYSTEM="1",
                        GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com",
                        GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.com",
                        FAKE_GH_LOG=str(self.gh_log), FAKE_GH_STATE=str(self.gh_state))
        (self.home / ".gitconfig").write_text("[init]\n\tdefaultBranch = trunk\n[advice]\n"
                                              "\tdetachedHead = false\n")

    def apply_env(self):
        """Point the in-process modules (store/identity) at the sandbox too."""
        self._saved = {k: os.environ.get(k) for k in self.env}
        for k in [k for k in os.environ if k.startswith(("CLAUDE", "SHEPHERD"))]:
            self._saved.setdefault(k, os.environ[k])
            del os.environ[k]
        os.environ.update(self.env)

    def restore_env(self):
        for k, v in getattr(self, "_saved", {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def cleanup(self):
        for p in self._procs:
            p.kill()
            p.wait()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- git ---------------------------------------------------------------------

    def git(self, cwd, *args, env=None, check=True) -> subprocess.CompletedProcess:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                           env=env or self.env)
        if check and p.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {p.stderr}")
        return p

    def repo(self, name="proj", github=True) -> Path:
        """A clone of a fresh bare remote with one commit on `trunk`. With github,
        the remote URL is https://github.com/o/<name>, rewritten to the bare repo."""
        origin = self.tmp / f"{name}-origin.git"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "trunk", str(origin))
        clone = self.tmp / name
        self.git(self.tmp, "clone", "-q", str(origin), str(clone))
        (clone / "README.md").write_text("hello\n")
        (clone / "src").mkdir()
        (clone / "src" / "a.py").write_text("a = 1\n")
        (clone / "src" / "b.py").write_text("b = 1\n")
        self.git(clone, "add", "README.md", "src")
        self.git(clone, "commit", "-qm", "initial")
        self.git(clone, "push", "-q", "origin", "trunk")
        self.git(clone, "remote", "set-head", "origin", "trunk")
        if github:
            url = f"https://github.com/o/{name}.git"
            self.git(clone, "config", f"url.{origin}.insteadOf", url)
            self.git(clone, "remote", "set-url", "origin", url)
        return clone

    def commit(self, cwd, path, text, msg, env=None) -> subprocess.CompletedProcess:
        p = Path(cwd) / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        self.git(cwd, "add", path, env=env)
        return self.git(cwd, "commit", "-qm", msg, env=env, check=False)

    # --- sessions ------------------------------------------------------------------

    def session(self, name="sess-a", cwd="/") -> dict:
        """A live fake Claude session (a sleeping process + registry entry);
        returns the env a tool subprocess of that session would have."""
        p = subprocess.Popen(["sleep", "600"])
        self._procs.append(p)
        time.sleep(0.02)
        sid = str(uuid.uuid4())
        entry = {"pid": p.pid, "sessionId": sid, "cwd": str(cwd), "name": name, "status": "busy",
                 "procStart": proc_start(p.pid), "startedAt": int(time.time() * 1000),
                 "updatedAt": int(time.time() * 1000), "kind": "interactive"}
        (self.claude / "sessions" / f"{p.pid}.json").write_text(json.dumps(entry))
        return dict(self.env, CLAUDE_CODE_SESSION_ID=sid, CLAUDE_PID=str(p.pid),
                    _SESSION_NAME=name)

    def end_session(self, env: dict):
        pid = int(env["CLAUDE_PID"])
        for p in self._procs:
            if p.pid == pid:
                p.kill()
                p.wait()

    # --- shepherd ------------------------------------------------------------------

    def cli(self, cwd, *args, env=None, input=None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ENTRY), *args], cwd=str(cwd),
                              capture_output=True, text=True, env=env or self.env, input=input)

    def ok(self, cwd, *args, env=None) -> str:
        p = self.cli(cwd, *args, env=env)
        if p.returncode != 0:
            raise AssertionError(f"shepherd {' '.join(args)} failed ({p.returncode}): "
                                 f"{p.stderr}{p.stdout}")
        return p.stdout

    def hook(self, event, payload: dict, env=None) -> tuple[int, str]:
        p = subprocess.run([sys.executable, str(ENTRY), "hook", event], input=json.dumps(payload),
                           capture_output=True, text=True, env=env or self.env)
        return p.returncode, p.stdout

    def gh_calls(self) -> list[list[str]]:
        if not self.gh_log.exists():
            return []
        return [json.loads(l) for l in self.gh_log.read_text().splitlines() if l]

    def set_gh(self, **state):
        cur = json.loads(self.gh_state.read_text()) if self.gh_state.exists() else {}
        cur.update(state)
        self.gh_state.write_text(json.dumps(cur))
