"""Command line: `shepherd <command>`. Agents use start/status/log/sync/pr/finish;
the user also runs init/mode/serve/doctor."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import SCHEMA, VERSION, ShepherdError, board, gitx, hooks, identity, review, store, tracks


def _ago(ts) -> str:
    if not ts:
        return "—"
    d = max(0, store.now() - int(ts))
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if d >= n:
            return f"{d // n}{unit} ago"
    return "just now"


def _review_chip(rv) -> str:
    if not rv or not rv.get("id"):
        return "—"
    tick = {"passing": " ✓", "failing": " ✗", "pending": " …"}.get(rv.get("checks"), "")
    ident = f"#{rv['id']}" if str(rv["id"]).isdigit() else str(rv["id"])
    return f"{ident} {rv.get('state')}{tick}"


def render_repo(v: dict) -> str:
    out = [f"{v['name']} — {v.get('mode')} · main {v.get('main')} · protected "
           f"{', '.join(v.get('protected') or [])}"]
    for w in v.get("warnings", []):
        out.append(f"  ⚠ {w}")
    for s in v.get("on_main", []):
        out.append(f"  ⚠ session {s.get('name')} is working in the main checkout")
    if v.get("tracks"):
        rows = [("TRACK", "STATUS", "OWNER", "Δ MAIN", "WIP", "REVIEW", "GOAL")]
        for c in v["tracks"]:
            a = c["agent"]
            owner = a.get("name") or "unclaimed"
            if a.get("session_id"):
                owner += f" ({a.get('status') or 'live'})" if a.get("live") else " (ended)"
            rows.append((c["name"], c["status"], owner, f"↑{c['ahead']} ↓{c['behind']}",
                         str(c["dirty_count"]), _review_chip(c.get("review")),
                         (c.get("goal") or "")[:60]))
        widths = [max(len(r[i]) for r in rows) for i in range(6)]
        for r in rows:
            out.append("  " + "  ".join(r[i].ljust(widths[i]) for i in range(6)) + "  " + r[6])
        for c in v["tracks"]:
            for w in c.get("warnings", []):
                out.append(f"  ⚠ {c['name']}: {w}")
            if c.get("review", {}) and c["review"].get("url"):
                out.append(f"    {c['name']}: {c['review']['url']}")
    else:
        out.append("  no tracks")
    for u in v.get("untracked", []):
        out.append(f"  ? untracked worktree {u['path']} ({u.get('branch')}) → "
                   f"`shepherd adopt --path {u['path']}`")
    for o in v.get("overlaps", []):
        cf = o.get("conflicts")
        tail = f"; predicted conflicts in {', '.join(cf)}" if cf else ""
        out.append(f"  ↔ {o['a']} and {o['b']} both touch {', '.join(o['shared'][:5])}"
                   f"{' …' if len(o['shared']) > 5 else ''}{tail}")
    return "\n".join(out)


def cmd_status(a) -> int:
    sessions = identity.sessions_by_id()
    if a.all:
        snap = board.snapshot(ttl=0)
        if a.json:
            print(json.dumps(snap, indent=2))
            return 0
        if not snap["repos"]:
            print("no enrolled repos (enroll one with `shepherd init` in its checkout)")
        for v in snap["repos"]:
            print(render_repo(v) + "\n")
        if snap["unmanaged"]:
            print("sessions not on any track:")
            for s in snap["unmanaged"]:
                print(f"  {s.get('name')} ({s.get('status')}) in {s.get('cwd')}"
                      f"{' — ' + s['title'] if s.get('title') else ''}")
        return 0
    repo = gitx.repo_of(os.getcwd())
    if repo is None or not store.load_config(repo.common_dir):
        name = repo.name if repo else os.getcwd()
        print(f"`{name}` is not enrolled in shepherd: no tracks, no protection. "
              f"(`shepherd status --all` lists enrolled repos.)")
        return 0
    v = board.repo_view(repo.common_dir, sessions)
    if a.json:
        print(json.dumps(v, indent=2))
        return 0
    print(render_repo(v))
    here = hooks.track_here(repo, store.tracks(repo.common_dir))
    if here:
        print(f"\nyou are in track `{here['name']}`")
    elif repo.branch in (v.get("protected") or []):
        print(f"\nthis checkout is on protected `{repo.branch}`: claim a track before editing "
              f"(`shepherd start <track> --goal … --owns …`)")
    return 0


def cmd_init(a) -> int:
    for n in tracks.init(os.getcwd(), a.mode, a.main, _csv(a.protect), a.test, a.worktree_dir,
                         False if a.no_trailers else None):
        print(n)
    return 0


def _csv(v):
    if not v:
        return None
    return [x.strip() for x in ",".join(v).split(",") if x.strip()]


def cmd_start(a) -> int:
    t = tracks.start(os.getcwd(), a.track, a.goal, _csv(a.owns) or [], _csv(a.avoid) or [],
                     _csv(a.live) or [], a.from_ref, a.branch, a.force)
    if a.json:
        print(json.dumps(t, indent=2))
        return 0
    print(f"track `{t['name']}` started on branch {t['branch']} from {t['base']['ref']} "
          f"({t['base']['sha'][:8]})")
    for n in t.get("_notes", []):
        print("note: " + n)
    print(f"worktree: {t['worktree']}")
    print(f"next: EnterWorktree(path=\"{t['worktree']}\") — then work only there")
    return 0


def cmd_adopt(a) -> int:
    t = tracks.adopt(os.getcwd(), a.path, a.branch, a.track, a.goal or "", _csv(a.owns) or [])
    print(f"adopted `{t['branch']}` as track `{t['name']}`"
          + (f" ({t['worktree']})" if t.get("worktree") else " (no worktree)"))
    return 0


def cmd_resume(a) -> int:
    t = tracks.resume(os.getcwd(), a.track, a.force)
    for n in t.get("_notes", []):
        print(n)
    print(f"track `{t['name']}` is yours; worktree: {t.get('worktree')}")
    return 0


def cmd_pause(a) -> int:
    t = tracks.pause(os.getcwd(), a.track, a.note or "")
    print(f"track `{t['name']}` paused")
    return 0


def cmd_log(a) -> int:
    e = tracks.log_message(os.getcwd(), " ".join(a.text), a.to, a.type)
    print(f"logged {e['type']} {e['id']}" + (f" to {e['to']}" if e.get("to") else ""))
    return 0


def cmd_inbox(a) -> int:
    items = tracks.inbox(os.getcwd())
    if a.json:
        print(json.dumps(items, indent=2))
        return 0
    if not items:
        print("inbox empty")
    for e in items:
        print(f"{e['id']}  {_ago(e.get('at'))}  {e.get('type')} from {e.get('from')}: {e.get('text')}")
    if items:
        print("\naccept one before acting on it: `shepherd accept <id> [--note …]`")
    return 0


def cmd_accept(a) -> int:
    e = tracks.accept(os.getcwd(), a.id, a.note or "")
    print(f"accepted {a.id} (logged {e['id']})")
    return 0


def cmd_sync(a) -> int:
    print(tracks.sync(os.getcwd(), a.track))
    return 0


def cmd_pr(a) -> int:
    r = tracks.pr(os.getcwd(), a.track, True if a.draft else None, a.title, a.body_file,
                  _csv(a.reviewers) or [], a.skip_tests, a.extra or [])
    if a.json:
        print(json.dumps(r, indent=2))
        return 0
    print(f"review {r.get('id')} ({r.get('state')}): {r.get('url')}")
    return 0


def cmd_finish(a) -> int:
    print(tracks.finish(os.getcwd(), a.track, a.abandon, a.keep_branch))
    return 0


def cmd_mode(a) -> int:
    print(tracks.set_mode(os.getcwd(), a.mode))
    return 0


def cmd_uninit(a) -> int:
    print(tracks.uninit(os.getcwd()))
    return 0


def cmd_serve(a) -> int:
    from . import server
    if a.stop:
        print("stopped" if server.stop() else "not running")
        return 0
    if a.daemon:
        print(server.daemon(a.port))
        return 0
    server.serve(a.port, quiet=a.quiet)
    return 0


def cmd_doctor(a) -> int:
    checks = []
    checks.append((sys.version_info >= (3, 9), f"python {sys.version.split()[0]}", "need ≥ 3.9"))
    gv = gitx.out(["--version"], os.getcwd())
    try:
        parts = tuple(int(x) for x in gv.split()[2].split(".")[:2])
    except (IndexError, ValueError):
        parts = (0, 0)
    checks.append((parts >= (2, 38), gv or "git", "need git ≥ 2.38 (merge-tree --write-tree)"))
    me_path = Path(__file__).resolve().parent.parent / "shepherd.py"
    link = Path.home() / ".local" / "bin" / "shepherd"
    ok = link.exists() and link.resolve() == me_path
    checks.append((ok, f"~/.local/bin/shepherd → {link.resolve() if link.exists() else 'missing'}",
                   "run claude-dotfiles/install.sh" if not ok else ""))
    inst = store.read_json(store.claude_dir() / "plugins" / "installed_plugins.json", {}) or {}
    for key, entries in (inst.get("plugins") or {}).items():
        if not key.startswith("claude-dotfiles@"):
            continue
        for e in entries:
            hj = Path(e.get("installPath") or "") / "hooks" / "hooks.json"
            checks.append((hj.exists(), f"plugin {e.get('version')} ({e.get('scope')}"
                           f"{', ' + e['projectPath'] if e.get('projectPath') else ''}) hooks",
                           "" if hj.exists() else "update the plugin: claude plugin marketplace "
                           "update claude-dotfiles && claude plugin update claude-dotfiles"))
    bs = review.backends(reload=True)
    checks.append((True, "review backends: " + ", ".join(getattr(b, "name", "?") for b in bs), ""))
    for err in review.load_errors:
        checks.append((False, "backend load error", err))
    for b in bs:
        try:
            for ok, label, hint in b.doctor():
                checks.append((ok, f"[{b.name}] {label}", hint))
        except Exception as e:
            checks.append((False, f"[{getattr(b, 'name', '?')}] doctor", str(e)))
    for r in store.registered_repos():
        cfg = store.load_config(r["common"])
        name = os.path.basename(r.get("primary") or r["common"])
        if not cfg:
            checks.append((False, f"{name}: registered but not enrolled", "re-run `shepherd init`"))
            continue
        checks.append((hooks.git_hooks_installed(r["common"]), f"{name}: git hooks ({cfg.get('mode')})",
                       "re-run `shepherd init` there"))
        hp = gitx.out(["config", "core.hooksPath"], r["primary"])
        if hp:
            checks.append((False, f"{name}: core.hooksPath={hp}", "shepherd's git hooks are bypassed"))
        for t in store.tracks(r["common"]):
            if t.get("status") in store.LIVE_WORK and not identity.agent_live(t.get("agent")):
                checks.append((False, f"{name}: track {t['name']} has no live owner",
                               f"`shepherd resume {t['name']}` or `shepherd pause`"))
            if t.get("status") == "merged":
                checks.append((False, f"{name}: track {t['name']} is merged",
                               f"`shepherd finish {t['name']}`"))
    bad = 0
    for ok, label, hint in checks:
        bad += 0 if ok else 1
        print(f"{'✓' if ok else '✗'} {label}" + (f" — {hint}" if hint and not ok else ""))
    print(f"\nshepherd {VERSION} (state schema {SCHEMA})")
    return 1 if bad else 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shepherd", description="Tracks, reviews and a live board "
                                "for parallel Claude Code sessions.")
    p.add_argument("--version", action="version", version=f"shepherd {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="tracks in this repo (or --all)")
    s.add_argument("--all", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("init", help="enroll this repo (user)")
    s.add_argument("--mode", choices=("observe", "enforce"), default="observe")
    s.add_argument("--main")
    s.add_argument("--protect", action="append", help="protected branches (default: main)")
    s.add_argument("--test", help="command run by `shepherd pr` before opening a review")
    s.add_argument("--worktree-dir", help="where track worktrees go, relative to the main checkout")
    s.add_argument("--no-trailers", action="store_true", help="don't add Agent/Track commit trailers")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("mode", help="observe or enforce (user)")
    s.add_argument("mode", choices=("observe", "enforce"))
    s.set_defaults(fn=cmd_mode)

    s = sub.add_parser("uninit", help="un-enroll this repo (user)")
    s.set_defaults(fn=cmd_uninit)

    s = sub.add_parser("start", help="claim a new track: branch + worktree")
    s.add_argument("track")
    s.add_argument("--goal", required=True)
    s.add_argument("--owns", action="append", help="paths/globs this track will edit")
    s.add_argument("--avoid", action="append")
    s.add_argument("--live", action="append", help="live resources this track owns (deploys, DBs…)")
    s.add_argument("--from", dest="from_ref", help="base ref (default: <remote>/<main>)")
    s.add_argument("--branch")
    s.add_argument("--force", action="store_true", help="claim despite overlaps (user's OK only)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_start)

    s = sub.add_parser("adopt", help="register an existing worktree or branch as a track")
    s.add_argument("--path")
    s.add_argument("--branch")
    s.add_argument("--track")
    s.add_argument("--goal")
    s.add_argument("--owns", action="append")
    s.set_defaults(fn=cmd_adopt)

    s = sub.add_parser("resume", help="claim a track whose owner ended")
    s.add_argument("track", nargs="?")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_resume)

    s = sub.add_parser("pause", help="pause your track, saying what is left")
    s.add_argument("track", nargs="?")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_pause)

    s = sub.add_parser("log", help="record a note/request/handoff for another track")
    s.add_argument("text", nargs="+")
    s.add_argument("--to")
    s.add_argument("--type", default="note",
                   choices=("note", "request", "handoff", "decision", "notice"))
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("inbox", help="unaccepted messages to your tracks")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("accept", help="accept a request or handoff")
    s.add_argument("id")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_accept)

    s = sub.add_parser("sync", help="rebase your track onto the latest main")
    s.add_argument("track", nargs="?")
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("pr", help="open or update the track's review (a PR, or the repo's equivalent)")
    s.add_argument("track", nargs="?")
    s.add_argument("--draft", action="store_true")
    s.add_argument("--title")
    s.add_argument("--body-file")
    s.add_argument("--reviewers", action="append")
    s.add_argument("--skip-tests", action="store_true")
    s.add_argument("--json", action="store_true")
    s.add_argument("extra", nargs=argparse.REMAINDER, help="-- args passed to the backend tool")
    s.set_defaults(fn=cmd_pr)

    s = sub.add_parser("finish", help="after the review merged: remove worktree + branch")
    s.add_argument("track", nargs="?")
    s.add_argument("--abandon", action="store_true", help="drop the track unmerged (branch kept)")
    s.add_argument("--keep-branch", action="store_true")
    s.set_defaults(fn=cmd_finish)

    s = sub.add_parser("serve", help="the dashboard")
    s.add_argument("--port", type=int, default=7799)
    s.add_argument("--daemon", action="store_true", help="run in the background (reuse if running)")
    s.add_argument("--stop", action="store_true")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("doctor", help="check the installation and enrolled repos")
    s.set_defaults(fn=cmd_doctor)
    return p


def main(argv: list[str]) -> int:
    a = parser().parse_args(argv)
    if getattr(a, "extra", None) and a.extra[:1] == ["--"]:
        a.extra = a.extra[1:]
    try:
        return a.fn(a)
    except ShepherdError as e:
        print(f"shepherd: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
