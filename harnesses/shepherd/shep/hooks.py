"""Hook adapters. Claude Code hooks (stdin JSON → stdout JSON) and git hooks
(argv/stdin → exit code). Both are inert outside enrolled repositories, and both
fail open: a crash here must never block the user's work."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Optional

from . import gitx, identity, policy, store

CMD = "shepherd"
RECENT = 2 * 3600       # activity window for "edited recently by another session"
REWARN = 3600           # don't repeat the same warning to a session within this
MAX_LINES = 16          # cap on the SessionStart summary
MAX_CHARS = 9500        # Claude Code moves additionalContext over 10,000 chars to a file
TEXT = 200              # chars quoted of a goal or a log entry; names and paths get less
COMPACT_COMMITS = 5     # commits re-injected after a compaction
PROGRESS = ("note", "request", "decision", "accept")   # log types worth re-injecting
SHA = re.compile(r"[0-9a-f]{7,64}")


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


def _agent(t: dict) -> dict:
    """A track's agent record; {} when missing or hand-edited into something else."""
    a = t.get("agent")
    return a if isinstance(a, dict) and isinstance(a.get("session_id"), (str, type(None))) else {}


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
        owner = _agent(t).get("session_id")
        if owner and owner == sid:
            continue
        others.append({"name": t["name"], "owns": t.get("owns") or [],
                       "changed": changed_on(repo, t, heads),
                       "owner": names.get(owner) or _agent(t).get("name")})
    cutoff = store.now() - RECENT
    recent = [dict(e, who=names.get(e.get("sid")) or e.get("who"))
              for e in store.tail_jsonl(store.activity_path(repo.common_dir))
              if isinstance(e, dict) and e.get("file") == rel and e.get("sid")
              and e.get("sid") != sid and e.get("at", 0) > cutoff]
    d = policy.decide_edit(
        mode=cfg.get("mode", "observe"), rel=rel, branch=repo.branch, protected=protected,
        here=dict(here, agent=_agent(here)) if here else None,
        here_owner_live=identity.agent_live(_agent(here or {})),
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


def _line(x, cap: int = 0) -> str:
    """One line of plain text, cut to `cap` chars if given: goals and log fields are
    free text."""
    s = " ".join(str(x if x is not None else "").split())
    return s if not cap or len(s) <= cap else s[:cap - 1] + "…"


def _sentence(x, cap: int = 0) -> str:
    s = _line(x, cap)
    return s if not s or s[-1] in ".!?…" else s + "."


def _when(at) -> Optional[str]:
    """A log entry's time, or None if `at` isn't a usable timestamp (NaN, 1e300, text)."""
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return None
    try:
        return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(at))
    except (ValueError, OverflowError, OSError):
        return None


def _own_entries(track: dict, log: list[dict]) -> list[dict]:
    """The track's own latest progress entry, plus its latest status line when that
    is newer (say "review opened: <url>"); the status line alone if there is no
    progress entry. Never quoted: "started: <goal>", claim notices, entries without a
    usable time, and messages to the track, which the inbox line counts. A session
    outside any track logs under its session name, so an entry addressed to the track
    itself isn't the track's."""
    name = track["name"]
    own = [e for e in log if e.get("from") == name and e.get("to") != name
           and _when(e.get("at"))]
    p = s = None                                        # positions in `own`
    for i, e in enumerate(own):
        if e.get("type") in PROGRESS:
            p = i
        elif e.get("type") == "status" and not str(e.get("text") or "").startswith("started:"):
            s = i
    if p is not None and s is not None and s < p:
        s = None
    return [own[i] for i in (p, s) if i is not None]


def _commits(repo: gitx.Repo, cfg: dict, track: dict) -> list[str]:
    """The track's own last commits: its branch's first-parent history, minus what its
    recorded base and the base branch's current tip already have. The recorded base
    moves only when `shepherd sync` completes, while a sync stopped on a conflict, a
    `git pull` (a track's upstream is the base branch) or a rebase by hand also bring
    the base branch's commits onto the track."""
    branch, base = track.get("branch"), (track.get("base") or {}).get("sha")
    if not isinstance(branch, str) or not branch:
        return []
    main, remote = cfg.get("main") or "main", cfg.get("remote")
    names = [f"refs/heads/{branch}", f"refs/heads/{main}"]
    if isinstance(remote, str) and remote:
        names.append(f"refs/remotes/{remote}/{main}")
    # literal ref names, shared by every worktree, so this reads the branch from any
    # checkout of the repo; each starts with refs/, and git log gets shas only
    r = gitx.run(["for-each-ref", "--format=%(objectname) %(refname)", *names],
                 repo.toplevel, timeout=1)
    refs = {}
    for ln in r.out.splitlines() if r.ok else []:
        sha, _, ref = ln.partition(" ")
        refs[ref] = sha
    tip = refs.get(names[0])
    if not tip:
        print(f"shepherd: compact commits skipped: "
              f"{r.err.strip()[:200] if not r.ok else 'no ' + names[0]}", file=sys.stderr)
        return []
    up = next((n for n in reversed(names[1:]) if n in refs), None)    # the remote's first
    lower = [refs[n] for n in names[1:] if n in refs]
    since = isinstance(base, str) and bool(SHA.fullmatch(base))
    if since and not up:                # the base names the bound only if it is a commit
        since = gitx.run(["rev-parse", "-q", "--verify", f"{base}^{{commit}}"],
                         repo.toplevel, timeout=1).ok
    lower += [base] if since else []    # --ignore-missing skips a base that doesn't resolve
    # --first-parent: commits merged in from elsewhere by hand aren't the track's
    r = gitx.run(["log", "--oneline", "--no-decorate", "--no-color", "--first-parent",
                  "--ignore-missing", f"-{COMPACT_COMMITS}", tip,
                  *(["--not", *lower] if lower else [])], repo.toplevel, timeout=1)
    if not r.ok:
        print(f"shepherd: compact commits skipped: {r.err.strip()[:200]}", file=sys.stderr)
        return []
    commits = [_line(c, 120) for c in r.out.splitlines() if c.strip()]
    b = _line(branch, 100)
    bound = (f"`{_line(up.split('/', 2)[2], 100)}`" if up
             else f"its base {base[:7]}" if since else "")
    if not commits:
        return [f"No commits on `{b}` beyond {bound}."] if bound else []
    return [f"Last commits on `{b}` beyond {bound}:" if bound else
            f"Last commits on `{b}` (base unknown; may include its base branch):"] + \
        [f"  {c}" for c in commits]


def _after_compact(repo: gitx.Repo, cfg: dict, track: dict, log: list[dict]) -> list[str]:
    """What a session loses in a compaction and needs back: its track's own last
    log entry and the branch's own commits. Each part is best effort; a failure drops
    only its own lines (stderr, which hook.sh appends to the log)."""
    lines = []
    try:
        for n, e in enumerate(_own_entries(track, log)):
            to = f" to `{_line(e['to'], 60)}`" if e.get("to") else ""
            lines.append(f"{'Since then' if n else 'Your last log entry'} ({_when(e['at'])}, "
                         f"{_line(e.get('type'), 20)}{to}): \"{_line(e.get('text'), TEXT)}\"")
    except Exception as err:
        print(f"shepherd: compact log entry skipped: {err!r}", file=sys.stderr)
    try:
        lines += _commits(repo, cfg, track)
    except Exception as err:
        print(f"shepherd: compact commits skipped: {err!r}", file=sys.stderr)
    return lines


def _owned_elsewhere(repo: gitx.Repo, tracks: list[dict], sid: str, here: Optional[dict],
                     protected: bool) -> tuple[Optional[dict], list[str]]:
    """For a session that compacted outside its own worktree: its newest live track,
    where it is instead, and how to get back."""
    def newest(t):
        u = t.get("updated_at")
        return u if isinstance(u, (int, float)) else 0
    owned = sorted((t for t in tracks if t.get("status") in store.LIVE_WORK
                    and _agent(t).get("session_id") == sid), key=newest, reverse=True)
    if not owned:
        return None, []
    own = owned[0]
    name = _line(own["name"], 60)
    wt = own.get("worktree") if isinstance(own.get("worktree"), str) else ""
    where = (f"{repo.toplevel} (track `{_line(here['name'], 60)}`'s worktree)" if here
             else f"{repo.toplevel} on protected branch `{_line(repo.branch, 100)}`" if protected
             else f"{repo.toplevel} (branch `{_line(repo.branch, 100)}`)")
    if wt and os.path.exists(os.path.join(wt, ".git")):
        # EnterWorktree switches from one worktree to another only under .claude/worktrees,
        # and track worktrees are siblings of the main checkout; ExitWorktree is a no-op
        # for a session that didn't enter with EnterWorktree
        go = (f"don't edit here: EnterWorktree(path={wt})" if repo.toplevel == repo.primary
              else f"don't edit here: ExitWorktree(action=\"keep\") if EnterWorktree brought "
                   f"you here, then EnterWorktree(path={wt})")
    elif wt and os.path.lexists(wt):        # `shepherd resume` recreates a missing path only
        go = f"{wt} is not a checkout any more: move it aside, then `{CMD} resume {name}`"
    elif wt:
        go = f"its worktree {wt} is gone: `{CMD} resume {name}` recreates it"
    else:
        go = f"it has no worktree on record: `{CMD} resume {name}` creates one"
    goal = _sentence(own.get("goal"), TEXT)
    lines = [f"You own track `{name}` (branch `{_line(own.get('branch'), 100)}`)"
             f"{': ' + goal if goal else '.'} This session is in {where}, outside its "
             f"worktree; {go}."]
    if len(owned) > 1:
        lines.append("Your other live tracks: " + ", ".join(
            f"`{_line(t['name'], 60)}` ({_line(t.get('worktree'), 300)})"
            for t in owned[1:4]) + ".")
    return own, lines


def session_start(inp: dict) -> str:
    # Claude Code always sends an absolute cwd. Nothing resolves against this process's
    # cwd: the claim below writes shared state, and a probe would aim it at the wrong repo.
    cwd = inp.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return ""
    repo = gitx.repo_of(cwd)
    cfg = _enrolled(repo)
    if not cfg:
        return ""
    sid = inp.get("session_id")
    sid = sid if isinstance(sid, str) and sid else os.environ.get("CLAUDE_CODE_SESSION_ID")
    compact = inp.get("source") == "compact"
    tracks = store.tracks(repo.common_dir)
    live_tracks = [t for t in tracks if t.get("status") in store.ACTIVE]
    here = track_here(repo, tracks)
    protected = repo.branch in (cfg.get("protected") or [])
    log = [e for e in store.read_log(repo.common_dir) if isinstance(e, dict)]  # before a claim
    mine = {t.get("name") for t in tracks if sid and _agent(t).get("session_id") == sid}
    own = None          # the session's own track, for re-injection after a compaction
    prot = _line(", ".join(map(str, cfg.get("protected") or [])), TEXT)
    lines = [f"shepherd: `{repo.name}` is enrolled (mode {_line(cfg.get('mode', 'observe'), 20)}, "
             f"protected: {prot or 'none'}). Follow the claude-dotfiles:parallel-sessions skill."]
    away = []
    # compacted outside its own worktree: in the main checkout or another track's
    if compact and sid and _agent(here or {}).get("session_id") != sid:
        try:
            own, away = _owned_elsewhere(repo, tracks, sid, here, protected)
        except Exception as err:        # odd track data: fall back to the lines below
            own, away = None, []
            print(f"shepherd: compact owned-track lines skipped: {err!r}", file=sys.stderr)
    if away:
        lines += away
    elif here:
        owner = _agent(here).get("session_id")
        live = bool(sid) and owner == sid or identity.agent_live(_agent(here))
        # a new or resumed session takes over a track whose owner has ended; a compacting
        # session didn't just arrive here, so it is told how instead
        if sid and not live and not compact:
            try:
                with store.locked(repo.common_dir, timeout=1):
                    t = store.load_track(repo.common_dir, here["name"]) or here
                    t["agent"] = identity.agent(sid, os.environ.get("CLAUDE_PID"))
                    t["status"] = "active" if t.get("status") == "paused" else t.get("status")
                    store.save_track(repo.common_dir, t)
                    store.add_log(repo.common_dir, "notice", t["name"],
                                  f"claimed by a new session ({sid[:8]}); the "
                                  f"previous owner had ended")
                owner = sid
                mine.add(here["name"])
                live_tracks = [t if x is here else x for x in live_tracks]   # list the new owner
            except Exception:
                pass
        yours = bool(sid) and owner == sid
        hn = _line(here["name"], 60)
        whose = ("yours" if yours else "owned by another live session" if live
                 else f"its owner has ended: `{CMD} resume {hn}` takes it over")
        lines.append(f"You are in track `{hn}` ({whose}): {_line(here.get('goal'), TEXT)}")
        if compact and yours:
            own = here
    elif protected:
        lines.append(f"You are on protected branch `{_line(repo.branch, 100)}`. Before editing, "
                     f"claim a track: `{CMD} start <track> --goal \"…\" --owns <paths>`, then "
                     f"EnterWorktree(path=<printed path>).")
    if own:
        lines += _after_compact(repo, cfg, own, log)
    names = {s["sessionId"]: s for s in identity.registry()}
    listed = []
    for t in live_tracks[:10]:
        try:                            # a hand-edited track drops its own line only
            a = _agent(t)
            s = names.get(a.get("session_id") or "")
            who = (s or {}).get("name") or a.get("name") or "unclaimed"
            alive = "live" if identity.agent_live(a) else "ended"
            r = t.get("review") or {}
            rv = f", review {_line(r.get('state'), 20)}" if r.get("url") else ""
            listed.append(f"- {_line(t['name'], 60)} [{_line(t.get('status'), 20)}{rv}] "
                          f"{_line(who, 60)} ({alive}) — {_line(t.get('goal'), TEXT)}; owns "
                          f"{_line(', '.join(map(str, t.get('owns') or [])), TEXT) or '—'}")
        except Exception as err:
            print(f"shepherd: track line skipped: {err!r}", file=sys.stderr)
    inbox = [e for e in log if isinstance(e.get("to"), str) and e["to"] in mine]
    tail = [f"{len(inbox)} message(s) for your track(s): `{CMD} inbox`."] if inbox else []
    # the track list gives way to the lines above it and the inbox, never the reverse,
    # down to 16 lines and to MAX_CHARS (past 10,000 Claude Code shows only a preview)
    room = MAX_LINES - len(lines) - len(tail)          # lines left for "Tracks:" + list
    body = []
    for keep in range(len(listed), -1, -1) if listed else ():
        more = [f"- … {len(live_tracks) - keep} more: `{CMD} status`"] if keep < len(listed) else []
        body = ["Tracks:"] + listed[:keep] + more
        if len(body) <= room and len("\n".join(lines + body + tail)) <= MAX_CHARS:
            break
    else:
        body = []
    return _context("SessionStart", "\n".join((lines + body + tail)[:MAX_LINES])[:MAX_CHARS])


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
            owner = _agent(here).get("session_id")
            if owner and owner != os.environ.get("CLAUDE_CODE_SESSION_ID") and \
                    identity.agent_live(_agent(here)):
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

