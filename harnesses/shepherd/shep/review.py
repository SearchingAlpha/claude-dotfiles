"""Review backends: how a track becomes a PR (or the repo's equivalent).

The built-in backend is GitHub. Others are plain Python files dropped into
~/.claude/shepherd/backends/ (or any directory in SHEPHERD_BACKENDS) that define

    SHEPHERD_API = 1
    BACKEND = <object with the methods below>

    name: str
    matches(remote_url) -> bool
    submit(track, ctx) -> Review      # first time: create the review
    update(track, ctx) -> Review      # later: publish new commits to it
    status(review, ctx) -> Review     # never raises; put failures in error/hint
    doctor() -> [(ok, label, hint)]
    discover(track, ctx) -> Review|None      (optional: find a review made elsewhere)
    config_defaults(remote_url) -> dict      (optional: merged into config at init)

A Review is a dict; see `normalize`. Hooks never load backends."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import ShepherdError, store

API = 1
STATES = ("draft", "open", "approved", "changes_requested", "merged", "closed", "unknown")
CHECKS = ("passing", "failing", "pending", "none", "unknown")
TERMINAL = ("merged", "closed")

load_errors: list[str] = []


def normalize(r: Optional[dict], backend: str) -> dict:
    r = dict(r or {})
    out = {
        "backend": backend, "id": r.get("id"), "url": r.get("url"), "title": r.get("title"),
        "state": r.get("state") if r.get("state") in STATES else "unknown",
        "raw_state": r.get("raw_state"),
        "checks": r.get("checks") if r.get("checks") in CHECKS else "unknown",
        "comments": int(r.get("comments") or 0), "reviewers": list(r.get("reviewers") or []),
        "updated_at": r.get("updated_at"), "checked_at": store.now(),
        "pushed_sha": r.get("pushed_sha"), "error": r.get("error"), "hint": r.get("hint"),
        "stale": bool(r.get("stale")),
    }
    return out


@dataclass
class Ctx:
    """What a backend gets to work with. `run` executes a command (list) and
    returns (rc, stdout, stderr); it never raises."""
    repo_root: str
    worktree: str
    remote: Optional[str]
    remote_url: str
    main: str
    base_sha: Optional[str]
    branch: str
    title: str = ""
    title_explicit: bool = False
    body: str = ""
    draft: bool = False
    reviewers: list = field(default_factory=list)
    extra_args: list = field(default_factory=list)
    head_sha: Optional[str] = None
    log: Callable[[str], None] = print

    def run(self, cmd: list[str], cwd: Optional[str] = None, timeout: float = 120,
            input: Optional[str] = None) -> tuple[int, str, str]:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1")
        try:
            p = subprocess.run(cmd, cwd=cwd or self.worktree, capture_output=True, text=True,
                               timeout=timeout, input=input, env=env,
                               stdin=None if input is not None else subprocess.DEVNULL)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"{cmd[0]}: timed out after {timeout:.0f}s"
        except OSError as e:
            return 127, "", f"{cmd[0]}: {e}"


def _backend_dirs() -> list[Path]:
    dirs = [Path(p) for p in os.environ.get("SHEPHERD_BACKENDS", "").split(os.pathsep) if p]
    dirs.append(store.home() / "backends")
    return dirs


_cache: Optional[list] = None


def backends(reload: bool = False) -> list:
    """External backends first (so a private host can't be claimed by a
    built-in), then the built-in ones."""
    global _cache
    if _cache is not None and not reload:
        return _cache
    load_errors.clear()
    found = []
    for d in _backend_dirs():
        for f in sorted(d.glob("*.py")) if d.is_dir() else []:
            try:
                spec = importlib.util.spec_from_file_location(f"shepherd_backend_{f.stem}", f)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if getattr(mod, "SHEPHERD_API", None) != API or not hasattr(mod, "BACKEND"):
                    load_errors.append(f"{f}: not a shepherd backend (SHEPHERD_API={API} and "
                                       f"BACKEND required)")
                    continue
                found.append(mod.BACKEND)
            except Exception as e:      # a broken private backend must not break shepherd
                load_errors.append(f"{f}: {type(e).__name__}: {e}")
    from .backends import github
    found.append(github.BACKEND)
    _cache = found
    return found


def select(remote_url: str, cfg: Optional[dict] = None):
    name = (cfg or {}).get("review_backend")
    for b in backends():
        if name and getattr(b, "name", None) == name:
            return b
    if name:
        raise ShepherdError(f"review backend `{name}` (set in config) is not installed")
    for b in backends():
        try:
            if b.matches(remote_url):
                return b
        except Exception:
            continue
    return None


def host_of(url: str) -> str:
    u = url.split("://", 1)[-1]
    u = u.split("@", 1)[-1]
    return u.split("/", 1)[0].split(":", 1)[0] or url


def build_body(track: dict, commits: list[dict], files: list[str], warnings: list[str],
               log_entries: list[dict]) -> str:
    a = track.get("agent") or {}
    lines = [track.get("goal") or "", "",
             f"**Track:** `{track['name']}` · **Agent:** Claude Code session "
             f"{a.get('name') or ''} (`{(a.get('session_id') or '')[:8]}`)"]
    if track.get("owns"):
        lines.append(f"**Owns:** {', '.join(f'`{o}`' for o in track['owns'])}")
    lines += ["", f"### Commits ({len(commits)})"]
    lines += [f"- {c['short']} {c['subject']}" for c in commits[:30]]
    if len(commits) > 30:
        lines.append(f"- … and {len(commits) - 30} more")
    lines += ["", f"### Files ({len(files)})"]
    lines += [f"- `{f}`" for f in files[:40]]
    if len(files) > 40:
        lines.append(f"- … and {len(files) - 40} more")
    if warnings:
        lines += ["", "### Heads-up"] + [f"- {w}" for w in warnings]
    notes = [e for e in log_entries if e.get("from") == track["name"] or e.get("to") == track["name"]]
    if notes:
        lines += ["", "### Coordination log"]
        lines += [f"- {e.get('type')}: {e.get('text')}" for e in notes[-10:]]
    lines += ["", "_Opened by [shepherd](https://github.com/SearchingAlpha/claude-dotfiles)._"]
    return "\n".join(lines)
