"""Hook adapters. Claude Code hooks (stdin JSON → stdout JSON) and git hooks
(argv/stdin → exit code). Both are inert outside enrolled repositories, and both
fail open: a crash here must never block the user's work."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Optional

from . import gitx, identity, policy, store

CMD = "shepherd"
RECENT = 2 * 3600       # activity window for "edited recently by another session"
REWARN = 3600           # don't repeat the same warning to a session within this


def _deny(reason: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "permissionDecision": "deny",
                                              "permissionDecisionReason": reason},
                       "systemMessage": reason.split(". ")[0]})


def _context(event: str, text: str, notice: Optional[str] = None) -> str:
    out = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    if notice:
        out["systemMessage"] = notice
    return json.dumps(out)


def _enrolled(repo: Optional[gitx.Repo]) -> Optional[dict]:
    if repo is None:
        return None
    cfg = store.load_config(repo.common_dir)
    return cfg if store.compatible(cfg) else None


def track_here(repo: gitx.Repo, tracks: list[dict]) -> Optional[dict]:
    """The track living in this checkout (by worktree path, else by branch)."""
    for t in tracks:
        if t.get("status") in store.ACTIVE and t.get("worktree") == repo.toplevel:
            return t
    for t in tracks:
        if t.get("status") in store.ACTIVE and repo.branch and t.get("branch") == repo.branch:
            return t
    return None


def changed_on(repo: gitx.Repo, track: dict, heads: dict) -> list[str]:
    """Files committed on a track's branch since its base, cached by sha."""
    sha = heads.get(track.get("branch") or "")
    base = (track.get("base") or {}).get("sha")
    if not sha or not base:
        return []
    cache_path = store.state_dir(repo.common_dir) / "cache" / "changed.json"
    cache = store.read_json(cache_path, {}) or {}
    key = f"{base}..{sha}"
    if key not in cache:
        cache = {k: v for k, v in cache.items() if k.split("..")[0] != base} if len(cache) > 200 else cache
        cache[key] = gitx.changed_files(repo.primary, base, sha)
        try:
            store.write_json(cache_path, cache)
        except OSError:
            pass
    return cache[key]


# --- Claude Code hooks ----------------------------------------------------------

def _target(inp: dict) -> Optional[str]:
    ti = inp.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path") or ti.get("path")
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.join(inp.get("cwd") or os.getcwd(), path)
    return gitx.realpath_any(path)


def pre_edit(inp: dict) -> str:
    path = _target(inp)
    repo = gitx.repo_of(path) if path else None
    cfg = _enrolled(repo)
    if not cfg:
        return ""
    rel = os.path.relpath(path, repo.toplevel)
    sid = inp.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    tracks = store.tracks(repo.common_dir)
    here = track_here(repo, tracks)
    protected = here is None and repo.branch in (cfg.get("protected") or [])
    names = {s["sessionId"]: s.get("name") for s in identity.registry()}

    others = []
    candidates = [t for t in tracks if t is not here and t.get("status") in store.LIVE_WORK]
    heads = gitx.branch_heads(repo.primary) if candidates else {}
    for t in candidates:
        owner = (t.get("agent") or {}).get("session_id")
        if owner and owner == sid:
            continue
        others.append({"name": t["name"], "owns": t.get("owns") or [],
                       "changed": changed_on(repo, t, heads),
                       "owner": names.get(owner) or (t.get("agent") or {}).get("name")})
    cutoff = store.now() - RECENT
    recent = [dict(e, who=names.get(e.get("sid")) or e.get("who"))
              for e in store.tail_jsonl(store.activity_path(repo.common_dir))
              if e.get("file") == rel and e.get("sid") and e.get("sid") != sid
              and e.get("at", 0) > cutoff]
    d = policy.decide_edit(
        mode=cfg.get("mode", "observe"), rel=rel, branch=repo.branch, protected=protected,
        here=here, here_owner_live=identity.agent_live((here or {}).get("agent")),
        me_sid=sid, others=others, recent_by_others=recent, cmd_hint=CMD)
    if d.deny:
        return _deny(d.deny)
    fresh = _unwarned(repo.common_dir, sid, d.warnings)
    if not fresh:
        return ""
    return _context("PreToolUse", "\n".join(fresh), fresh[0][:200])


def _unwarned(common: str, sid: Optional[str], warnings: list[str]) -> list[str]:
    if not warnings or not sid:
        return warnings
    mem = store.session_memory(common, sid)
    warned = mem.get("warned") or {}
    now = store.now()
    fresh = []
    for w in warnings:
        k = hashlib.sha1(w.encode()).hexdigest()[:12]
        if now - warned.get(k, 0) >= REWARN:
            fresh.append(w)
            warned[k] = now
    if fresh:
        mem["warned"] = {k: v for k, v in warned.items() if now - v < 86400}
        try:
            store.save_session_memory(common, sid, mem)
        except OSError:
            pass
    return fresh


def pre_bash(inp: dict) -> str:
    cmd = (inp.get("tool_input") or {}).get("command") or ""
    if "git" not in cmd and "SHEPHERD_ALLOW_MAIN" not in cmd:
        return ""
    cwd = inp.get("cwd") or os.getcwd()
    calls, _ = policy.git_calls(cmd)
    findings: list[tuple[str, str]] = []      # (mode, reason)
    if "SHEPHERD_ALLOW_MAIN" in cmd:
        cfg = _enrolled(gitx.repo_of(cwd))
        if cfg:
            findings.append((cfg.get("mode", "observe"),
                             "SHEPHERD_ALLOW_MAIN is reserved for the user"))
    for c in calls:
        where = os.path.expanduser(c.cwd) if c.cwd else cwd
        where = where if os.path.isabs(where) else os.path.join(cwd, where)
        repo = gitx.repo_of(where)
        cfg = _enrolled(repo)
        if not cfg:
            continue
        why = policy.classify_git(c, cfg.get("protected") or [], repo.branch)
        if why:
            findings.append((cfg.get("mode", "observe"), why))
    if not findings:
        return ""
    enforce = [w for m, w in findings if m == "enforce"]
    if enforce:
        return _deny(f"shepherd: {enforce[0]}. This repo is enrolled in shepherd; see the "
                     f"claude-dotfiles:parallel-sessions skill. Don't work around this — "
                     f"tell the user if you need it.")
    return _context("PreToolUse", "shepherd (observe mode, not blocked): "
                    + "; ".join(w for _, w in findings))


def post_edit(inp: dict) -> str:
    path = _target(inp)
    repo = gitx.repo_of(path) if path else None
    if not _enrolled(repo):
        return ""
    sid = inp.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    here = track_here(repo, store.tracks(repo.common_dir))
    ap = store.activity_path(repo.common_dir)
    store.rotate(ap)
    store.append_jsonl(ap, {"at": store.now(), "sid": sid, "tool": inp.get("tool_name"),
                            "file": os.path.relpath(path, repo.toplevel),
                            "wt": repo.toplevel, "branch": repo.branch,
                            "track": here["name"] if here else None})
    return ""


def session_start(inp: dict) -> str:
    cwd = inp.get("cwd") or os.getcwd()
    repo = gitx.repo_of(cwd)
    cfg = _enrolled(repo)
    if not cfg:
        return ""
    sid = inp.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    tracks = store.tracks(repo.common_dir)
    here = track_here(repo, tracks)
    lines = [f"shepherd: `{repo.name}` is enrolled (mode {cfg.get('mode')}, protected: "
             f"{', '.join(cfg.get('protected') or []) or 'none'}). Follow the "
             f"claude-dotfiles:parallel-sessions skill."]
    if here:
        owner = (here.get("agent") or {}).get("session_id")
        if owner != sid and not identity.agent_live(here.get("agent")):
            try:
                with store.locked(repo.common_dir, timeout=1):
                    t = store.load_track(repo.common_dir, here["name"]) or here
                    t["agent"] = identity.agent(sid, os.environ.get("CLAUDE_PID"))
                    t["status"] = "active" if t.get("status") == "paused" else t.get("status")
                    store.save_track(repo.common_dir, t)
                    store.add_log(repo.common_dir, "notice", t["name"],
                                  f"claimed by a new session ({(sid or '')[:8]}); the "
                                  f"previous owner had ended")
                owner = sid
            except Exception:
                pass
        whose = "yours" if owner == sid else "owned by another live session"
        lines.append(f"You are in track `{here['name']}` ({whose}): {here.get('goal') or ''}")
    elif repo.branch in (cfg.get("protected") or []):
        lines.append(f"You are on protected branch `{repo.branch}`. Before editing, claim a "
                     f"track: `{CMD} start <track> --goal \"…\" --owns <paths>`, then "
                     f"EnterWorktree(path=<printed path>).")
    names = {s["sessionId"]: s for s in identity.registry()}
    live_tracks = [t for t in tracks if t.get("status") in store.ACTIVE]
    if live_tracks:
        lines.append("Tracks:")
    for t in live_tracks[:10]:
        a = t.get("agent") or {}
        s = names.get(a.get("session_id") or "")
        who = (s or {}).get("name") or a.get("name") or "unclaimed"
        alive = "live" if identity.agent_live(a) else "ended"
        r = t.get("review") or {}
        rv = f", review {r.get('state')}" if r.get("url") else ""
        lines.append(f"- {t['name']} [{t.get('status')}{rv}] {who} ({alive}) — "
                     f"{t.get('goal') or ''}; owns {', '.join(t.get('owns') or []) or '—'}")
    mine = {t["name"] for t in tracks if (t.get("agent") or {}).get("session_id") == sid}
    inbox = [e for e in store.read_log(repo.common_dir) if e.get("to") in mine]
    if inbox:
        lines.append(f"{len(inbox)} message(s) for your track(s): `{CMD} inbox`.")
    return _context("SessionStart", "\n".join(lines[:16]))


CLAUDE_EVENTS = {"pre-edit": pre_edit, "pre-bash": pre_bash, "post-edit": post_edit,
                 "session-start": session_start}


def run_claude_hook(event: str, raw: str) -> str:
    try:
        inp = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return ""
    fn = CLAUDE_EVENTS.get(event)
    return fn(inp) if fn and isinstance(inp, dict) else ""


# --- git hooks --------------------------------------------------------------------

GIT_HOOKS = ("pre-commit", "pre-push", "prepare-commit-msg")
STUB_MARK = "# shepherd-hook v1"
BLOCK = 3       # exit code meaning "refuse"; a crash (1) or a missing python (127) must not block

STUB = """#!/bin/sh
{mark} ({name})
# Installed by `shepherd init`. It acts only inside Claude Code sessions
# (CLAUDE_CODE_SESSION_ID set); everyone else goes straight to the repo's own
# hook, kept as {name}.shepherd-orig. Remove with `shepherd uninit`.
name={name}
orig="$0.shepherd-orig"
input=""
[ "$name" = pre-push ] && input=$(cat)
if [ -n "$CLAUDE_CODE_SESSION_ID" ]; then
  bin="$HOME/.local/bin/shepherd"
  [ -x "$bin" ] || bin=$(command -v shepherd 2>/dev/null)
  if [ -n "$bin" ]; then
    printf '%s' "$input" | "$bin" githook "$name" "$@"
    [ $? -eq {block} ] && exit 1     # any other failure of shepherd itself lets git go on
  fi
fi
if [ -x "$orig" ]; then
  if [ "$name" = pre-push ]; then printf '%s\\n' "$input" | "$orig" "$@"; else "$orig" "$@"; fi
  exit $?
fi
exit 0
"""


def hooks_dir(common: str) -> Path:
    return Path(common) / "hooks"


def install_git_hooks(common: str) -> list[str]:
    d = hooks_dir(common)
    d.mkdir(parents=True, exist_ok=True)
    done = []
    for name in GIT_HOOKS:
        p = d / name
        if p.exists() and STUB_MARK not in p.read_text(errors="replace"):
            orig = d / f"{name}.shepherd-orig"
            if orig.exists():
                raise RuntimeError(f"{p} exists and so does {orig}; resolve by hand")
            os.replace(p, orig)
            done.append(f"{name} (kept existing hook as {orig.name})")
        else:
            done.append(name)
        p.write_text(STUB.format(mark=STUB_MARK, name=name, block=BLOCK))
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return done


def uninstall_git_hooks(common: str) -> None:
    d = hooks_dir(common)
    for name in GIT_HOOKS:
        p, orig = d / name, d / f"{name}.shepherd-orig"
        if p.exists() and STUB_MARK in p.read_text(errors="replace"):
            p.unlink()
            if orig.exists():
                os.replace(orig, p)


def git_hooks_installed(common: str) -> bool:
    d = hooks_dir(common)
    return all((d / n).exists() and STUB_MARK in (d / n).read_text(errors="replace")
               for n in GIT_HOOKS)


def run_git_hook(name: str, args: list[str], stdin: str) -> int:
    repo = gitx.repo_of(os.getcwd())
    cfg = _enrolled(repo)
    if not cfg or not identity.in_agent():
        return 0
    protected = cfg.get("protected") or []
    enforce = cfg.get("mode") == "enforce"
    if name == "pre-commit":
        tracks = store.tracks(repo.common_dir)
        here = track_here(repo, tracks)
        if here is None and repo.branch in protected:
            msg = (f"shepherd: `{repo.branch}` is protected; commit on a track branch "
                   f"(`{CMD} start <track> --goal …`) and land it with `{CMD} pr`.")
            if enforce:
                print(msg, file=sys.stderr)
                return BLOCK
            print(msg + " (observe mode: allowed)", file=sys.stderr)
        if here:
            owner = (here.get("agent") or {}).get("session_id")
            if owner and owner != os.environ.get("CLAUDE_CODE_SESSION_ID") and \
                    identity.agent_live(here.get("agent")):
                print(f"shepherd: warning — track `{here['name']}` belongs to another live "
                      f"session.", file=sys.stderr)
        return 0
    if name == "pre-push":
        for line in stdin.splitlines():
            parts = line.split()
            if len(parts) != 4:
                continue
            remote_ref = parts[2]
            if remote_ref.startswith("refs/heads/") and remote_ref[11:] in protected:
                msg = (f"shepherd: `{remote_ref[11:]}` is protected; it only changes through "
                       f"a reviewed PR (`{CMD} pr`).")
                if enforce:
                    print(msg, file=sys.stderr)
                    return BLOCK
                print(msg + " (observe mode: allowed)", file=sys.stderr)
        return 0
    if name == "prepare-commit-msg":
        if not args or (len(args) > 1 and args[1] in ("merge", "squash")):
            return 0
        if not cfg.get("trailers", True):
            return 0
        sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
        here = track_here(repo, store.tracks(repo.common_dir))
        trailers = ["Agent: claude-code", f"Agent-Session: {sid[:8]}"]
        if here:
            trailers.append(f"Track: {here['name']}")
        cmd = ["interpret-trailers", "--in-place", "--if-exists", "addIfDifferent"]
        for t in trailers:
            cmd += ["--trailer", t]
        gitx.run(cmd + [args[0]], repo.toplevel)
        return 0
    return 0

