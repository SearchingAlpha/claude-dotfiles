"""State on disk. Per repository in <git-common-dir>/shepherd/ (shared by every
worktree, never committed); per machine in ~/.claude/shepherd/.

Writes are atomic (temp file + rename), JSONL appends are a single write, and
read-modify-write of tracks happens under an flock on <state>/.lock."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Iterator, Optional

from . import SCHEMA, ShepherdError

TRACK_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,40}$")
ACTIVE = ("active", "review", "merged", "paused")   # tracks that still exist
LIVE_WORK = ("active", "review")                       # tracks that own paths


def home() -> Path:
    return Path(os.environ.get("SHEPHERD_HOME") or Path.home() / ".claude" / "shepherd")


def claude_dir() -> Path:
    return Path(os.environ.get("SHEPHERD_CLAUDE_DIR") or Path.home() / ".claude")


def state_dir(common_dir: str) -> Path:
    return Path(common_dir) / "shepherd"


def now() -> int:
    return int(time.time())


# --- primitives ---------------------------------------------------------------

def read_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: Path, obj, mode: Optional[int] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True)
            f.write("\n")
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n").encode()
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def tail_jsonl(path: Path, max_bytes: int = 65536) -> list[dict]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            chunk = f.read()
    except OSError:
        return []
    lines = chunk.split(b"\n")
    if size > max_bytes:
        lines = lines[1:]          # first line is probably cut
    res = []
    for line in lines:
        if line.strip():
            try:
                res.append(json.loads(line))
            except ValueError:
                pass
    return res


def rotate(path: Path, max_bytes: int = 2_000_000) -> None:
    with contextlib.suppress(OSError):
        if path.stat().st_size > max_bytes:
            os.replace(path, path.with_suffix(".1.jsonl"))


@contextlib.contextmanager
def locked(common_dir: str, timeout: float = 10) -> Iterator[None]:
    lock = state_dir(common_dir) / ".lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise ShepherdError("shepherd state is locked by another process; retry")
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


# --- per-repo state -----------------------------------------------------------

def load_config(common_dir: str) -> Optional[dict]:
    """The repo's config, or None when the repo is not enrolled."""
    cfg = read_json(state_dir(common_dir) / "config.json")
    return cfg if isinstance(cfg, dict) else None


def save_config(common_dir: str, cfg: dict) -> None:
    cfg["schema"] = SCHEMA
    write_json(state_dir(common_dir) / "config.json", cfg)


def compatible(obj: Optional[dict]) -> bool:
    """Newer schemas are left alone: an old reader must not act on them."""
    return isinstance(obj, dict) and int(obj.get("schema", 1)) <= SCHEMA


def tracks(common_dir: str) -> list[dict]:
    d = state_dir(common_dir) / "tracks"
    res = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        t = read_json(p)
        if compatible(t):
            res.append(t)
    return res


def load_track(common_dir: str, name: str) -> Optional[dict]:
    t = read_json(state_dir(common_dir) / "tracks" / f"{name}.json")
    return t if compatible(t) else None


def save_track(common_dir: str, track: dict) -> None:
    track["schema"] = SCHEMA
    track["updated_at"] = now()
    write_json(state_dir(common_dir) / "tracks" / f"{track['name']}.json", track)


def log_path(common_dir: str) -> Path:
    return state_dir(common_dir) / "log.jsonl"


def activity_path(common_dir: str) -> Path:
    return state_dir(common_dir) / "activity.jsonl"


def add_log(common_dir: str, type_: str, frm: str, text: str,
            to: Optional[str] = None, ref: Optional[str] = None) -> dict:
    entry = {"id": os.urandom(3).hex(), "at": now(), "type": type_, "from": frm,
             "to": to, "text": text, "ref": ref}
    append_jsonl(log_path(common_dir), entry)
    return entry


def read_log(common_dir: str, max_bytes: int = 262144) -> list[dict]:
    return tail_jsonl(log_path(common_dir), max_bytes)


def session_memory(common_dir: str, sid: str) -> dict:
    return read_json(state_dir(common_dir) / "sessions" / f"{sid}.json", {}) or {}


def save_session_memory(common_dir: str, sid: str, mem: dict) -> None:
    write_json(state_dir(common_dir) / "sessions" / f"{sid}.json", mem)


# --- machine registry ---------------------------------------------------------

def registered_repos() -> list[dict]:
    data = read_json(home() / "repos.json", {}) or {}
    return [r for r in data.get("repos", []) if isinstance(r, dict) and r.get("common")]


def register_repo(primary: str, common_dir: str) -> None:
    path = home() / "repos.json"
    data = read_json(path, {}) or {}
    repos = [r for r in data.get("repos", []) if r.get("common") != common_dir]
    repos.append({"primary": primary, "common": common_dir, "added_at": now()})
    write_json(path, {"schema": SCHEMA, "repos": repos})


def unregister_repo(common_dir: str) -> None:
    path = home() / "repos.json"
    data = read_json(path, {}) or {}
    data["repos"] = [r for r in data.get("repos", []) if r.get("common") != common_dir]
    write_json(path, data)


# --- generated ledger view ----------------------------------------------------

LEDGER_HEADER = ("<!-- Generated by shepherd — do not edit. Use `shepherd status`, "
                 "`shepherd log` and `shepherd start`. -->\n")


def _cell(v) -> str:
    if isinstance(v, (list, tuple)):
        v = ", ".join(v) if v else "—"
    return str(v or "—").replace("|", "\\|").replace("\n", " ")


def render_ledger(common_dir: str, sessions_by_id: Optional[dict] = None) -> None:
    """Write <common>/claude-sessions.md from the tracks and the log, so sessions
    that still read the old ledger see the same picture."""
    sessions_by_id = sessions_by_id or {}
    rows = []
    for t in tracks(common_dir):
        if t.get("status") not in ACTIVE:
            continue
        a = t.get("agent") or {}
        sid = a.get("session_id") or ""
        who = (sessions_by_id.get(sid) or {}).get("name") or a.get("name") or "unclaimed"
        session = f"{t['name']} ({who}{', ' + sid[:8] if sid else ''})"
        status = t.get("status", "")
        if t.get("note"):
            status += f": {t['note']}"
        review = t.get("review") or {}
        if review.get("url"):
            status += f" — {review.get('state', '')} {review['url']}"
        rows.append("| " + " | ".join(_cell(x) for x in (
            session, t.get("goal"), t.get("branch"), t.get("worktree"), t.get("owns"), t.get("avoid"),
            t.get("live"), status)) + " |")
    lines = [LEDGER_HEADER,
             "| Session | Goal | Branch | Worktree | Owns | Doesn't touch | Live resources | Status |",
             "|---|---|---|---|---|---|---|---|", *rows, "", "## Log"]
    for e in read_log(common_dir)[-40:]:
        when = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(e.get("at", 0)))
        arrow = f"{e.get('from')} → {e['to']}" if e.get("to") else str(e.get("from"))
        lines.append(f"- {when} {arrow} ({e.get('type')}, {e.get('id')}): {e.get('text')}")
    text = "\n".join(lines) + "\n"

    path = Path(common_dir) / "claude-sessions.md"
    stamp = state_dir(common_dir) / "ledger.sha"
    if path.exists():
        current = path.read_bytes()
        if not current.startswith(LEDGER_HEADER.encode()):
            legacy = Path(common_dir) / "claude-sessions.legacy.md"
            if not legacy.exists():
                os.replace(path, legacy)
            else:
                os.replace(path, Path(common_dir) / f"claude-sessions.{now()}.md")
        elif _sha(current) != _read_text(stamp):
            os.replace(path, Path(common_dir) / f"claude-sessions.{now()}.md")
            add_log(common_dir, "notice", "shepherd",
                    "claude-sessions.md was edited by hand; the edited copy was kept "
                    "next to it. Use `shepherd log` instead.")
    tmp = path.with_name(".claude-sessions.md.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
    state_dir(common_dir).mkdir(parents=True, exist_ok=True)
    stamp.write_text(_sha(text.encode()))


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _read_text(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""
