#!/usr/bin/env python3
"""Opt-in batched auto-commit for one notes vault (a git repo). Inert unless
~/.claude/guards/config.json names `vault_commit.path` or CLAUDE_GUARDS_VAULT is
set.

- PostToolUse (Write|Edit|NotebookEdit, and Bash/PowerShell via
  tool_response.bashEditDiff): appends each changed path under the vault to
  <home>/vault-pending/<session>.jsonl.
- Stop: claims that list (plus, with `sweep`, the vault files git reports as
  changed since this session's previous Stop), takes flock
  <git-dir>/claude-autocommit.lock, runs `git add -- <paths>` and
  `git commit --only -- <paths>` with literal pathspecs (exactly those paths,
  never -A, other staged work untouched), once per turn. Then pushes from a
  detached process that outlives `claude -p`. Never rebases, stashes or forces;
  every attempt is a line in <home>/vault-push.log, and the paths stay pending
  for the next turn when the commit didn't happen.

Every git child runs in its own session, so a hook timeout can't kill git
half-way and leave index.lock behind. Fails open; always exits 0."""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

COMPONENT = "vault_commit"
FILE_KEYS = {"Write": "file_path", "Edit": "file_path", "NotebookEdit": "notebook_path"}
SHELL_TOOLS = ("Bash", "PowerShell")
ORPHAN_AGE = 2 * 3600        # pending lists of sessions idle this long are swept by any Stop
STALE_CLAIM = 600            # a claimed list left behind by a killed run is retried after this
SINCE_TTL = 7 * 86400        # forget sweep markers of sessions idle this long
SWEEP_MAX = 500              # more changed files than this in one window: sweep nothing, log it
LOCK_WAIT = 2.0
GIT_WAIT = 3.0               # seconds the Stop hook waits on one git command (timeout is 5)
PUSH_TIMEOUT = 120
BUSY = ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
        "BISECT_LOG")


def _sid(inp: dict) -> str:
    raw = inp.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or "nosession"
    return "".join(c for c in str(raw) if c.isalnum() or c in "-_")[:80] or "nosession"


def _log(common, line: str) -> None:
    try:
        d = common.home()
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(d / "vault-push.log", "a", encoding="utf-8") as f:
            f.write(f"{stamp} {line}\n")
    except OSError:
        pass


def _git_env(literal: bool = True) -> dict:
    # No prompts: git's own are off, and ssh finds no controlling terminal in the
    # new session, so an expired credential fails fast instead of hanging.
    # Literal pathspecs: a note named "Meeting [12].md" or ":!x.md" is that file,
    # not a glob or pathspec magic that pulls in other paths. (check-ignore
    # refuses the setting; it reads names literally anyway.)
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_LITERAL_PATHSPECS")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    if literal:
        env["GIT_LITERAL_PATHSPECS"] = "1"
    return env


def _git(args, cwd, wait=GIT_WAIT, stdin_data=None, pids=None):
    """(rc, stdout, stderr); rc None when still running after `wait` (left to
    finish). The child's pid is appended to `pids` when given."""
    import subprocess
    env = _git_env(literal="check-ignore" not in args)
    p = subprocess.Popen(["git", *args], cwd=cwd, env=env, start_new_session=True,
                         stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if pids is not None:
        pids.append(p.pid)
    try:
        out, err = p.communicate(stdin_data.encode() if stdin_data is not None else None,
                                 timeout=wait)
    except subprocess.TimeoutExpired:
        return None, "", f"git {args[0]} still running after {wait:.0f}s"
    return p.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def _pending_dir(common):
    return common.home() / "vault-pending"


def _append(path, paths) -> None:
    """One JSON string per line: any file name, newlines included, round-trips."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(p) + "\n" for p in paths))


def _read_list(path):
    out = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            p = json.loads(ln)
        except ValueError:
            continue
        if isinstance(p, str) and p:
            out.append(p)
    return out


# --- PostToolUse: record ---------------------------------------------------------

def _mark_since(common, sid: str, inp: dict) -> None:
    """Start this session's first sweep window just before the tool call ran."""
    f = _pending_dir(common) / f"{sid}.since"
    if f.exists():
        return
    try:
        dur = float(inp.get("duration_ms") or 0) / 1000
    except (TypeError, ValueError):
        dur = 0.0
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(repr(time.time() - dur - 2.0))


def record(inp: dict, vc: dict, common) -> int:
    tool = inp.get("tool_name")
    if vc["sweep"]:
        _mark_since(common, _sid(inp), inp)
    ti = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    paths = []
    if tool in FILE_KEYS:
        p = ti.get(FILE_KEYS[tool])
        if isinstance(p, str) and p:
            paths.append(p)
    elif tool in SHELL_TOOLS:
        resp = inp.get("tool_response")
        diff = resp.get("bashEditDiff") if isinstance(resp, dict) else None
        if isinstance(diff, dict) and not diff.get("shared") and not diff.get("skipped"):
            paths += [p for p in diff.get("changedFiles") or [] if isinstance(p, str)]
    vault = vc["path"]
    keep = []
    for p in paths:
        rp = os.path.realpath(os.path.expanduser(p))
        if common.under(rp, vault) and not common.under(rp, os.path.join(vault, ".git")):
            keep.append(rp)
    if keep:
        _append(_pending_dir(common) / f"{_sid(inp)}.jsonl", keep)
    return 0


# --- Stop: commit ----------------------------------------------------------------

def _claim(common, sid: str):
    """Rename this session's list (plus orphaned and stale-claimed lists) to a
    claim file owned by this process; returns the claimed files."""
    d = _pending_dir(common)
    if not d.is_dir():
        return []
    now = time.time()
    claimed = []
    for f in sorted(d.iterdir()):
        name = f.name
        try:
            age = now - f.stat().st_mtime
        except OSError:
            continue
        mine = name == f"{sid}.jsonl"
        if name.endswith(".jsonl"):
            if not mine and age < ORPHAN_AGE:
                continue
        elif ".claim-" in name:
            if age < STALE_CLAIM:
                continue
        else:
            if name.endswith(".since") and age > SINCE_TTL:
                try:
                    f.unlink()
                except OSError:
                    pass
            continue
        target = d / f"{name.split('.')[0]}.claim-{os.getpid()}-{len(claimed)}"
        try:
            os.rename(f, target)
        except OSError:
            continue                       # another Stop took it first
        claimed.append(target)
    return claimed


def _release(claimed, common, sid: str, paths=None) -> None:
    """Put unfinished paths back into this session's list; drop the claims."""
    if paths:
        _append(_pending_dir(common) / f"{sid}.jsonl", paths)
    for c in claimed:
        try:
            c.unlink()
        except OSError:
            pass


def _since(common, sid: str):
    try:
        return float((_pending_dir(common) / f"{sid}.since").read_text())
    except (OSError, ValueError):
        return None


def _set_since(common, sid: str, t: float) -> None:
    try:
        d = _pending_dir(common)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{sid}.since.tmp"
        tmp.write_text(repr(t))
        os.replace(tmp, d / f"{sid}.since")
    except OSError:
        pass


def _sweep(vault: str, top: str, since: float, common):
    """Vault files git reports as modified or new (not deleted, not ignored) whose
    mtime is in this session's window. Deletions only count when recorded. The
    vault may be a subdirectory of its repository (top): only it is listed, and
    porcelain paths, which are relative to the top, are resolved against that."""
    rc, out, err = _git(["--no-optional-locks", "status", "--porcelain=v1", "-z",
                         "--untracked-files=all", "--", "."], vault)
    if rc != 0:
        _log(common, f"fail: git status in {vault} for the sweep: {(err or 'timed out').strip()[:300]}")
        return []
    items = out.split("\0")
    found, i = [], 0
    while i < len(items):
        entry = items[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, rel = entry[:2], entry[3:]
        if xy[0] in "RC":
            i += 1                                # the rename's source path follows
        if "D" in xy or rel.endswith("/"):        # deletions; a nested repo shows as "dir/"
            continue
        p = os.path.join(top, rel)
        if not common.under(p, vault):
            continue
        try:
            if os.lstat(p).st_mtime >= since:
                found.append(p)
        except OSError:
            pass
    if len(found) > SWEEP_MAX:
        _log(common, f"skip sweep: {len(found)} changed files in {vault} since "
                     f"{time.strftime('%H:%M:%SZ', time.gmtime(since))}, more than {SWEEP_MAX}; "
                     f"commit them by hand")
        return []
    return found


def _git_dirs(vault: str):
    """(git dir, work tree top) of the repository holding the vault, or None."""
    rc, out, _ = _git(["rev-parse", "--absolute-git-dir", "--show-toplevel"], vault)
    lines = out.splitlines() if rc == 0 else []
    if len(lines) != 2 or not all(ln.strip() for ln in lines):
        return None
    return lines[0].strip(), os.path.realpath(lines[1].strip())


def _lock(git_dir: str):
    import fcntl
    fh = open(os.path.join(git_dir, "claude-autocommit.lock"), "w")
    deadline = time.time() + LOCK_WAIT
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except OSError:
            if time.time() > deadline:
                fh.close()
                return None
            time.sleep(0.05)


def _shown(rel: str) -> str:
    return "".join(c if c.isprintable() else "?" for c in rel)


def _message(vc: dict, rel) -> str:
    prefix = vc.get("message_prefix") or "vault:"
    if len(rel) == 1:
        head = f"{prefix} update {_shown(rel[0])}"
    else:
        names = ", ".join(_shown(os.path.basename(r)) for r in rel[:3]) + (", …" if len(rel) > 3 else "")
        head = f"{prefix} update {len(rel)} files ({names})"
    return head + "\n\nCommitted by Claude Code (guards/vault_commit) at the end of a turn.\n\n" + \
        "".join(f"- {_shown(r)}\n" for r in rel)


def commit(inp: dict, vc: dict, common) -> int:
    sid = _sid(inp)
    started = time.time()
    claimed = _claim(common, sid)
    since = _since(common, sid) if vc["sweep"] else None
    if vc["sweep"] and since is None:
        _set_since(common, sid, started)          # no tool call seen yet: next window starts now
    if not claimed and since is None:
        return 0
    vault = vc["path"]
    paths = []
    for c in claimed:
        paths += _read_list(c)
    paths = [p for p in dict.fromkeys(paths) if common.under(p, vault)]
    import shutil
    if not shutil.which("git"):
        _log(common, f"skip: git is not on PATH; {len(paths)} path(s) kept for next turn")
        _release(claimed, common, sid, paths)
        return 0
    dirs = _git_dirs(vault)
    if not dirs:
        if paths:
            _log(common, f"skip: {vault} is not a git repository; {len(paths)} path(s) dropped")
        _release(claimed, common, sid)
        return 0
    git_dir, top = dirs
    if since is not None:
        paths = list(dict.fromkeys(paths + _sweep(vault, top, since, common)))
    if not paths:
        _release(claimed, common, sid)
        if since is not None:
            _set_since(common, sid, started)
        return 0
    busy = [b for b in BUSY if os.path.exists(os.path.join(git_dir, b))]
    if busy:
        _log(common, f"skip: {vault} is busy ({busy[0]}); {len(paths)} path(s) kept for next turn")
        _release(claimed, common, sid, paths)
        return 0
    lock = _lock(git_dir)
    if lock is None:
        _log(common, f"skip: another vault commit holds the lock in {vault}; "
                     f"{len(paths)} path(s) kept for next turn")
        _release(claimed, common, sid, paths)
        return 0
    try:
        done = _commit_locked(vault, vc, paths, claimed, common, sid)
    finally:
        lock.close()
    if done and since is not None:
        _set_since(common, sid, started)
    return 0


def _commit_locked(vault, vc, paths, claimed, common, sid) -> bool:
    """True when the paths are settled (committed or nothing to commit)."""
    rel = sorted({os.path.relpath(p, vault) for p in paths})
    rc, branch, _ = _git(["symbolic-ref", "--quiet", "--short", "HEAD"], vault)
    if rc != 0:
        _log(common, f"skip: {vault} has a detached HEAD; {len(rel)} path(s) kept")
        _release(claimed, common, sid, paths)
        return False
    branch = branch.strip()
    if vc.get("branch") and vc["branch"] != branch:
        # The commit would land on the checked-out branch while the push sends the
        # configured one: nothing would leave the machine.
        _log(common, f"skip: {vault} is on branch {branch}, config says {vc['branch']}; "
                     f"{len(rel)} path(s) kept")
        _release(claimed, common, sid, paths)
        if common.notice_once(f"{COMPONENT}-branch", sid):
            common.emit({"systemMessage": f"vault_commit: not committing in {vault}: it is on "
                                          f"branch {branch}, the config says {vc['branch']}. "
                                          f"Switch back (or change vault_commit.branch); the "
                                          f"changed paths are kept for the next turn."})
        return False
    rc, ignored, _ = _git(["check-ignore", "-z", "--stdin"], vault,
                          stdin_data="".join(r + "\0" for r in rel))
    skip = set(ignored.split("\0")) if rc == 0 else set()
    rel = [r for r in rel if r not in skip]
    present = [r for r in rel if os.path.lexists(os.path.join(vault, r))]
    gone = [r for r in rel if r not in present]
    steps = []
    if present:
        steps.append(["add", "--", *present])
    if gone:
        steps.append(["rm", "--cached", "--quiet", "--ignore-unmatch", "--", *gone])
    for args in steps:
        rc, _, err = _git(args, vault)
        if rc != 0:
            _log(common, f"fail: git {args[0]} in {vault}: {(err or 'timed out').strip()[:300]}; "
                         f"{len(rel)} path(s) kept for next turn")
            _release(claimed, common, sid, paths)
            return False
    if not rel:
        _log(common, f"skip: all {len(paths)} path(s) are gitignored in {vault}")
        _release(claimed, common, sid)
        return True
    # Only paths with a staged change go to the commit: an unchanged file or one
    # created and deleted within the turn is unknown to `commit --only` and would
    # fail the whole commit.
    rc, names, err = _git(["-c", "core.quotePath=false", "diff", "--cached", "--name-only",
                           "--relative", "-z", "--", *rel], vault)
    if rc != 0:
        _log(common, f"fail: git diff in {vault}: {(err or 'timed out').strip()[:300]}; "
                     f"{len(rel)} path(s) kept for next turn")
        _release(claimed, common, sid, paths)
        return False
    rel = sorted(n for n in names.split("\0") if n)
    if not rel:
        _log(common, f"skip: nothing changed in {len(paths)} path(s) in {vault}")
        _release(claimed, common, sid)
        return True
    rc, head, _ = _git(["rev-parse", "-q", "--verify", "HEAD"], vault)
    head = head.strip() if rc == 0 else ""
    pids: list = []
    rc, _, err = _git(["commit", "--quiet", "--only", "-m", _message(vc, rel), "--", *rel], vault,
                      pids=pids)
    push = vc["push"]
    remote, target = vc.get("remote") or "origin", vc.get("branch") or branch
    if rc is None:
        # A slow pre-commit hook: git goes on in its own session. The paths are
        # re-checked next turn (committed ones then show no change), and the push
        # waits for the commit.
        _log(common, f"slow: git commit in {vault} still running after {GIT_WAIT:.0f}s; it "
                     f"finishes in the background{' and then pushes' if push else ''}. "
                     f"{len(rel)} path(s) are re-checked next turn")
        _release(claimed, common, sid, paths)
        if push:
            _spawn_push(vault, remote, target, after=(pids[0], head))
        common.emit({"systemMessage": f"vault_commit: the commit in {vault} is still running "
                                      f"after {GIT_WAIT:.0f}s (a slow pre-commit hook?); it will "
                                      f"finish in the background"
                                      f"{' and then push' if push else ''}."})
        return False
    if rc != 0:
        why = err.strip().splitlines()[-1][:300] if err.strip() else f"rc={rc}"
        _log(common, f"fail: commit in {vault} ({len(rel)} path(s), kept for next turn): {why}")
        _release(claimed, common, sid, paths)
        common.emit({"systemMessage": f"vault_commit: the commit in {vault} did not complete "
                                      f"({why}); see {common.home() / 'vault-push.log'}"})
        return False
    _release(claimed, common, sid)
    rc, sha, _ = _git(["rev-parse", "--short", "HEAD"], vault)
    _log(common, f"ok: committed {len(rel)} path(s) in {vault} as {sha.strip() or '?'}"
                 + ("" if push else " (push is off)"))
    if push:
        _spawn_push(vault, remote, target)
    return True


def _spawn_push(vault, remote, branch, after=None) -> None:
    import subprocess
    extra = ["--after", str(after[0]), after[1] or "-"] if after else []
    subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "--push", vault, remote,
                      branch, *extra], start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                     env=dict(os.environ))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    try:                                       # a zombie nobody reaped yet is done
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def push(vault: str, remote: str, branch: str, common, after=None) -> int:
    """Detached pusher: plain `git push <remote> <branch>`, outcome to the log.
    With after=(pid, head), first waits for that background commit and pushes
    only if HEAD moved."""
    if after:
        pid, head = after
        deadline = time.time() + PUSH_TIMEOUT
        while _alive(pid) and time.time() < deadline:
            time.sleep(0.2)
        rc, now, _ = _git(["rev-parse", "-q", "--verify", "HEAD"], vault)
        if rc != 0 or now.strip() == head:
            _log(common, f"skip push: the background commit in {vault} did not land (a hook "
                         f"refused it?); its paths are retried next turn")
            return 0
    rc, out, err = _git(["push", "--quiet", remote, branch], vault, wait=PUSH_TIMEOUT)
    if rc == 0:
        _log(common, f"ok: pushed {branch} to {remote} from {vault}")
        return 0
    text = (err or out).strip()
    hint = ""
    if "non-fast-forward" in text or "fetch first" in text or "rejected" in text:
        hint = " (remote moved on: pull and merge or rebase by hand; nothing was forced)"
    elif "Permission denied" in text or "publickey" in text or "certificate" in text.lower():
        hint = " (ssh auth failed: refresh your credentials, then push by hand)"
    last = text.splitlines()[-1][:300] if text else "no output"
    _log(common, f"fail: push {branch} to {remote} from {vault}: {last}{hint}"
         if rc is not None else f"fail: push {branch} to {remote} from {vault} timed out")
    return 0


def run(inp: dict, common) -> int:
    cfg = common.config()
    vc = common.vault_commit_cfg(cfg)
    if not vc or common.disabled(COMPONENT, cfg):
        return 0
    event = inp.get("hook_event_name")
    if event == "PostToolUse":
        return record(inp, vc, common)
    if event == "Stop":
        return commit(inp, vc, common)
    return 0


def main() -> int:
    inp = None
    try:
        import common
    except Exception as e:
        print(f"guards/{COMPONENT}: cannot load helpers ({type(e).__name__}); inactive",
              file=sys.stderr)
        return 0
    try:
        argv = sys.argv[1:]
        if len(argv) in (4, 7) and argv[0] == "--push":
            after = (int(argv[5]), argv[6]) if len(argv) == 7 and argv[4] == "--after" else None
            return push(argv[1], argv[2], argv[3], common, after)
        inp = common.read_payload()
        return run(inp, common) if inp is not None else 0
    except Exception as e:
        # The PostToolUse recorder is async: its notice would reach Claude, not you.
        return common.fail_open(COMPONENT, e, inp,
                                notify=(inp or {}).get("hook_event_name") != "PostToolUse")


if __name__ == "__main__":
    sys.exit(main())
