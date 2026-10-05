"""Track lifecycle: init → start/adopt → (sync) → pr → finish.

A track is one agent's unit of work: a branch, its own worktree, the paths it
claims, the session that owns it and the review it ends in."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import ShepherdError, gitx, hooks, identity, policy, review, store

CMD = "shepherd"


@dataclass
class Ctx:
    repo: gitx.Repo
    cfg: dict

    @property
    def common(self) -> str:
        return self.repo.common_dir

    @property
    def primary(self) -> str:
        return self.repo.primary

    @property
    def remote(self) -> Optional[str]:
        return self.cfg.get("remote")

    @property
    def main(self) -> str:
        return self.cfg.get("main") or "main"

    def base(self) -> str:
        return gitx.base_ref(self.primary, self.remote, self.main)


def repo_here(cwd: str) -> gitx.Repo:
    repo = gitx.repo_of(cwd)
    if repo is None:
        raise ShepherdError(f"not inside a git checkout: {cwd}")
    return repo


def ctx_here(cwd: str) -> Ctx:
    repo = repo_here(cwd)
    cfg = store.load_config(repo.common_dir)
    if not cfg:
        raise ShepherdError(f"`{repo.name}` is not enrolled in shepherd. The user can enroll "
                            f"it with `{CMD} init` (agents must not).")
    if not store.compatible(cfg):
        raise ShepherdError("this repo's shepherd state was written by a newer shepherd; "
                            "update claude-dotfiles")
    return Ctx(repo, cfg)


def _refresh_ledger(c: Ctx) -> None:
    try:
        with store.locked(c.common, timeout=3):    # concurrent renders would look like hand edits
            store.render_ledger(c.common, identity.sessions_by_id())
    except (OSError, ShepherdError):
        pass


def _who(c: Ctx, me: Optional[dict]) -> str:
    """Log identity: my track in this repo, else my session name, else 'user'."""
    if me:
        mine = [t for t in store.tracks(c.common) if t.get("status") in store.ACTIVE
                and (t.get("agent") or {}).get("session_id") == me["session_id"]]
        here = hooks.track_here(c.repo, mine)
        if here or mine:
            return (here or mine[0])["name"]
        return me.get("name") or me["session_id"][:8]
    return "user"


def _require_human(what: str) -> None:
    if identity.in_agent():
        raise ShepherdError(f"`{CMD} {what}` changes how this repo is protected, so only the "
                            f"user may run it. Ask the user to run it in a terminal.")


def _check_owner(t: dict, me: Optional[dict], action: str) -> None:
    a = t.get("agent") or {}
    if me and a.get("session_id") and a["session_id"] != me["session_id"] and identity.agent_live(a):
        raise ShepherdError(f"track `{t['name']}` belongs to another live session "
                            f"({a.get('name') or a['session_id'][:8]}); you can't {action} it. "
                            f"Message it: `{CMD} log --to {t['name']} --type request \"…\"`.")


def find_track(c: Ctx, name: Optional[str], me: Optional[dict] = None) -> dict:
    if name:
        t = store.load_track(c.common, name)
        if not t:
            raise ShepherdError(f"no track named `{name}` (see `{CMD} status`)")
        return t
    tracks = [t for t in store.tracks(c.common) if t.get("status") in store.ACTIVE]
    here = hooks.track_here(c.repo, tracks)
    if here:
        return here
    if me:
        mine = [t for t in tracks if (t.get("agent") or {}).get("session_id") == me["session_id"]]
        if len(mine) == 1:
            return mine[0]
    raise ShepherdError("which track? Run this inside the track's worktree or pass its name")


# --- enrollment -----------------------------------------------------------------

def init(cwd: str, mode: str = "observe", main: Optional[str] = None,
         protect: Optional[list[str]] = None, test_cmd: Optional[str] = None,
         worktree_dir: Optional[str] = None, trailers: Optional[bool] = None) -> list[str]:
    if mode == "enforce":
        _require_human("init --mode enforce")
    repo = repo_here(cwd)
    hp = gitx.out(["config", "core.hooksPath"], repo.primary)
    if hp:
        raise ShepherdError(f"core.hooksPath is set ({hp}); shepherd installs its git hooks "
                            f"in {repo.common_dir}/hooks. Unset it or wire the stubs in by hand.")
    old = store.load_config(repo.common_dir) or {}
    remote = old.get("remote") or gitx.detect_remote(repo.primary, main)
    main = main or old.get("main") or gitx.detect_main(repo.primary, remote)
    url = gitx.remote_url(repo.primary, remote) if remote else ""
    defaults = {}
    try:
        b = review.select(url) if url else None
        if b is not None and hasattr(b, "config_defaults"):
            defaults = b.config_defaults(url) or {}
    except Exception:
        pass
    cfg = {"mode": mode, "remote": remote, "main": main,
           "protected": protect or old.get("protected") or [main],
           "worktree_dir": worktree_dir or old.get("worktree_dir") or "..",
           "test_cmd": test_cmd if test_cmd is not None else old.get("test_cmd"),
           "test_timeout": old.get("test_timeout", 900),
           "trailers": trailers if trailers is not None else old.get("trailers", defaults.get("trailers", True)),
           "pr_draft": old.get("pr_draft", defaults.get("pr_draft", False)),
           "review_backend": old.get("review_backend"),
           "created_at": old.get("created_at") or store.now()}
    for k, v in old.items():
        cfg.setdefault(k, v)
    store.save_config(repo.common_dir, cfg)
    installed = hooks.install_git_hooks(repo.common_dir)
    store.register_repo(repo.primary, repo.common_dir)
    c = Ctx(repo, cfg)
    _refresh_ledger(c)

    notes = [f"enrolled `{repo.name}`: mode {mode}, main `{main}`, protected "
             f"{', '.join(cfg['protected'])}, remote {remote or '(none)'}",
             f"git hooks: {', '.join(installed)}"]
    b = None
    try:
        b = review.select(url) if url else None
    except ShepherdError:
        pass
    notes.append(f"review backend: {getattr(b, 'name', None) or 'none for ' + (review.host_of(url) if url else 'this repo')}")
    if remote and gitx.rev(repo.primary, f"refs/remotes/{remote}/{main}") and gitx.rev(repo.primary, main):
        ahead, _ = gitx.ahead_behind(repo.primary, main, f"{remote}/{main}")
        if ahead:
            notes.append(f"local `{main}` is {ahead} commit(s) ahead of {remote}/{main}. Land them "
                         f"through a review before enforcing: `git branch backlog {main}` then "
                         f"`{CMD} adopt --branch backlog --goal \"…\"` and `{CMD} pr backlog`.")
    tracked = {t.get("worktree") for t in store.tracks(repo.common_dir)}
    for w in gitx.worktrees(repo.primary)[1:]:
        if w["path"] not in tracked and w.get("branch"):
            notes.append(f"adoptable worktree: {w['path']} ({w['branch']}) → "
                         f"`{CMD} adopt --path {w['path']}`")
    return notes


def set_mode(cwd: str, mode: str) -> str:
    _require_human(f"mode {mode}")
    c = ctx_here(cwd)
    c.cfg["mode"] = mode
    store.save_config(c.common, c.cfg)
    store.add_log(c.common, "decision", "user", f"mode set to {mode}")
    _refresh_ledger(c)
    return f"`{c.repo.name}` is now in {mode} mode"


def uninit(cwd: str) -> str:
    _require_human("uninit")
    c = ctx_here(cwd)
    hooks.uninstall_git_hooks(c.common)
    sd = store.state_dir(c.common)
    os.replace(sd / "config.json", sd / "config.disabled.json")
    store.unregister_repo(c.common)
    return f"`{c.repo.name}` is no longer enrolled (tracks and log kept in {sd})"


# --- tracks -------------------------------------------------------------------------

def _validate_name(name: str) -> None:
    if not store.TRACK_NAME.match(name or ""):
        raise ShepherdError(f"invalid track name `{name}`: use lowercase letters, digits, "
                            f"`.`, `_` or `-` (max 41 chars)")


def _archive_finished(c: Ctx, name: str) -> None:
    t = store.load_track(c.common, name)
    if t is None:
        return
    if t.get("status") in store.ACTIVE:
        raise ShepherdError(f"track `{name}` already exists ({t.get('status')})")
    src = store.state_dir(c.common) / "tracks" / f"{name}.json"
    dst = store.state_dir(c.common) / "tracks" / "archive" / f"{name}-{t.get('updated_at', store.now())}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)


def _claim_conflicts(c: Ctx, name: str, owns: list[str], me: Optional[dict]) -> list[str]:
    msgs = []
    for t in store.tracks(c.common):
        if t.get("status") not in store.LIVE_WORK or t["name"] == name:
            continue
        pairs = policy.owns_overlap(owns, t.get("owns") or [])
        if pairs:
            msgs.append(f"`{t['name']}` already owns " + ", ".join(f"`{b}`" for _, b in pairs))
    return msgs


def start(cwd: str, name: str, goal: str, owns: list[str], avoid: list[str] = (),
          live: list[str] = (), from_ref: Optional[str] = None, branch: Optional[str] = None,
          force: bool = False) -> dict:
    c = ctx_here(cwd)
    _validate_name(name)
    if not goal.strip():
        raise ShepherdError("--goal is required: one line saying what this track delivers")
    me = identity.me()
    branch = branch or name
    if branch in (c.cfg.get("protected") or []):
        raise ShepherdError(f"`{branch}` is protected; a track needs its own branch")
    fetched = gitx.fetch(c.primary, c.remote, c.main)
    with store.locked(c.common):
        _archive_finished(c, name)
        clash = _claim_conflicts(c, name, list(owns), me)
        if clash and not force:
            entry = store.add_log(c.common, "request", _who(c, me),
                                  f"wants to start `{name}` ({goal}) owning "
                                  f"{', '.join(owns)}; overlaps: {'; '.join(clash)}")
            raise ShepherdError("claim overlaps an active track: " + "; ".join(clash) +
                                f". Logged request {entry['id']}. Coordinate with the owner, "
                                f"narrow --owns, or ask the user before using --force.")
        base = from_ref or c.base()
        if not from_ref and c.remote and gitx.rev(c.primary, c.main) and \
                gitx.rev(c.primary, f"refs/remotes/{c.remote}/{c.main}"):
            ahead, _ = gitx.ahead_behind(c.primary, c.main, f"{c.remote}/{c.main}")
            if ahead:
                raise ShepherdError(
                    f"local `{c.main}` has {ahead} commit(s) not on {c.remote}/{c.main}. A track "
                    f"from {c.remote}/{c.main} would not contain them. Either build on them "
                    f"(`--from {c.main}`; your review will include them) or ask the user to "
                    f"land them first.")
        base_sha = gitx.rev(c.primary, base)
        if not base_sha:
            raise ShepherdError(f"base `{base}` does not resolve to a commit")
        parent = Path(os.path.realpath(os.path.join(c.primary, c.cfg.get("worktree_dir") or "..")))
        path = parent / f"{c.repo.name}-{name}"
        if path.exists():
            raise ShepherdError(f"{path} already exists; pick another track name")
        r = gitx.run(["worktree", "add", "-b", branch, str(path), base_sha], c.primary, timeout=120)
        if not r.ok:
            raise ShepherdError(f"git worktree add failed: {r.err.strip()}")
        if c.remote and gitx.rev(c.primary, f"refs/remotes/{c.remote}/{c.main}"):
            gitx.run(["branch", f"--set-upstream-to={c.remote}/{c.main}", branch], str(path))
        t = {"name": name, "goal": goal.strip(), "branch": branch,
             "worktree": os.path.realpath(str(path)), "base": {"ref": base, "sha": base_sha},
             "agent": me, "owns": list(owns), "avoid": list(avoid), "live": list(live),
             "status": "active", "note": None, "review": None, "created_at": store.now()}
        store.save_track(c.common, t)
        store.add_log(c.common, "status", name, f"started: {goal.strip()}"
                      + (f" (forced over: {'; '.join(clash)})" if clash else ""))
    _refresh_ledger(c)
    t["_notes"] = [] if fetched.ok else [f"fetch failed ({fetched.err.strip()[:120]}); based on "
                                         f"the last fetched {base}"]
    return t


def _derive_name(branch: str) -> str:
    n = branch[len("worktree-"):] if branch.startswith("worktree-") else branch
    n = n.replace("+", "-").replace("/", "-").lower()
    n = re.sub(r"[^a-z0-9._-]+", "-", n).strip("-._")[:41]
    return n or "track"


def adopt(cwd: str, path: Optional[str] = None, branch: Optional[str] = None,
          name: Optional[str] = None, goal: str = "", owns: list[str] = ()) -> dict:
    c = ctx_here(cwd)
    me = identity.me()
    wts = gitx.worktrees(c.primary)
    wt = None
    if path:
        rp = os.path.realpath(path)
        wt = next((w for w in wts if w["path"] == rp), None)
        if wt is None:
            raise ShepherdError(f"{path} is not a worktree of `{c.repo.name}`")
        branch = wt.get("branch")
    elif branch:
        if not gitx.rev(c.primary, f"refs/heads/{branch}"):
            raise ShepherdError(f"no local branch `{branch}`")
        wt = next((w for w in wts if w.get("branch") == branch), None)
    else:
        here = os.path.realpath(c.repo.toplevel)
        wt = next((w for w in wts if w["path"] == here), None)
        branch = c.repo.branch
    if not branch:
        raise ShepherdError("that checkout has no branch (detached HEAD)")
    if branch in (c.cfg.get("protected") or []):
        raise ShepherdError(f"`{branch}` is protected and can't be a track; for unpushed work "
                            f"on it: `git branch backlog {branch}` then adopt `backlog`")
    name = name or _derive_name(branch)
    _validate_name(name)
    with store.locked(c.common):
        for t in store.tracks(c.common):
            if t.get("status") in store.ACTIVE and t.get("branch") == branch:
                raise ShepherdError(f"branch `{branch}` is already track `{t['name']}`")
        _archive_finished(c, name)
        if wt is None:      # a bare branch: give it a worktree so it can be reviewed
            path = os.path.realpath(os.path.join(c.primary, c.cfg.get("worktree_dir") or "..",
                                                 f"{c.repo.name}-{name}"))
            if os.path.exists(path):
                raise ShepherdError(f"{path} already exists; pass --track with another name")
            r = gitx.run(["worktree", "add", path, branch], c.primary, timeout=120)
            if not r.ok:
                raise ShepherdError(f"git worktree add failed: {r.err.strip()}")
            wt = {"path": path}
            if c.remote and gitx.rev(c.primary, f"refs/remotes/{c.remote}/{c.main}"):
                gitx.run(["branch", f"--set-upstream-to={c.remote}/{c.main}", branch], path)
        base = c.base()
        base_sha = gitx.merge_base(c.primary, base, branch) or gitx.rev(c.primary, base)
        t = {"name": name, "goal": goal.strip() or f"(adopted branch {branch})", "branch": branch,
             "worktree": wt["path"] if wt else None, "base": {"ref": base, "sha": base_sha},
             "agent": me, "owns": list(owns), "avoid": [], "live": [], "status": "active",
             "note": None, "review": None, "created_at": store.now()}
        store.save_track(c.common, t)
        store.add_log(c.common, "status", name, f"adopted branch {branch}"
                      + (f" ({wt['path']})" if wt else " (no worktree)"))
    _refresh_ledger(c)
    return t


def resume(cwd: str, name: Optional[str], force: bool = False) -> dict:
    c = ctx_here(cwd)
    me = identity.me()
    with store.locked(c.common):
        t = find_track(c, name, me)
        a = t.get("agent") or {}
        if me and a.get("session_id") not in (None, me["session_id"]) and \
                identity.agent_live(a) and not force:
            raise ShepherdError(f"`{t['name']}` is owned by a live session "
                                f"({a.get('name') or a['session_id'][:8]}); ask the user "
                                f"before taking it over (--force)")
        notes = []
        wt = t.get("worktree")
        if not wt or not os.path.isdir(wt):
            gitx.run(["worktree", "prune"], c.primary)
            wt = wt or os.path.realpath(os.path.join(
                c.primary, c.cfg.get("worktree_dir") or "..", f"{c.repo.name}-{t['name']}"))
            r = gitx.run(["worktree", "add", wt, t["branch"]], c.primary, timeout=120)
            if not r.ok:
                raise ShepherdError(f"could not recreate the worktree: {r.err.strip()}")
            t["worktree"] = os.path.realpath(wt)
            notes.append(f"recreated worktree {wt}")
        prev = a.get("name") or (a.get("session_id") or "")[:8] or "nobody"
        t["agent"] = me
        if t.get("status") in ("paused", "done", "abandoned"):
            t["status"] = "active"
        t["note"] = None
        store.save_track(c.common, t)
        store.add_log(c.common, "notice", t["name"],
                      f"resumed by {(me or {}).get('name') or 'user'} (was {prev})")
    _refresh_ledger(c)
    t["_notes"] = notes
    return t


def pause(cwd: str, name: Optional[str], note: str) -> dict:
    c = ctx_here(cwd)
    me = identity.me()
    with store.locked(c.common):
        t = find_track(c, name, me)
        _check_owner(t, me, "pause")
        t["status"], t["note"] = "paused", note or None
        store.save_track(c.common, t)
        store.add_log(c.common, "status", t["name"], f"paused: {note or 'no note'}")
    _refresh_ledger(c)
    return t


# --- coordination log -----------------------------------------------------------

def log_message(cwd: str, text: str, to: Optional[str] = None, type_: str = "note") -> dict:
    c = ctx_here(cwd)
    if to and not store.load_track(c.common, to) and to != "user":
        raise ShepherdError(f"no track `{to}` (or use --to user)")
    e = store.add_log(c.common, type_, _who(c, identity.me()), text, to=to)
    _refresh_ledger(c)
    return e


def inbox(cwd: str) -> list[dict]:
    c = ctx_here(cwd)
    me = identity.me()
    entries = store.read_log(c.common)
    accepted = {e.get("ref") for e in entries if e.get("type") == "accept"}
    if me:
        mine = {t["name"] for t in store.tracks(c.common)
                if (t.get("agent") or {}).get("session_id") == me["session_id"]}
    else:
        mine = {"user"}
    return [e for e in entries if e.get("to") in mine and e.get("type") != "accept"
            and e.get("id") not in accepted]


def accept(cwd: str, entry_id: str, note: str = "") -> dict:
    c = ctx_here(cwd)
    entries = store.read_log(c.common)
    orig = next((e for e in entries if e.get("id") == entry_id), None)
    if not orig:
        raise ShepherdError(f"no log entry `{entry_id}`")
    e = store.add_log(c.common, "accept", _who(c, identity.me()), note or "accepted",
                      to=orig.get("from"), ref=entry_id)
    _refresh_ledger(c)
    return e


# --- integration ------------------------------------------------------------------

def _worktree_of(t: dict) -> str:
    wt = t.get("worktree")
    if not wt or not os.path.isdir(wt):
        raise ShepherdError(f"track `{t['name']}` has no worktree on disk; `{CMD} resume "
                            f"{t['name']}` recreates it")
    return wt


def sync(cwd: str, name: Optional[str]) -> str:
    c = ctx_here(cwd)
    me = identity.me()
    t = find_track(c, name, me)
    _check_owner(t, me, "sync")
    wt = _worktree_of(t)
    if gitx.dirty(wt):
        raise ShepherdError("commit (by name) or discard your own changes before syncing")
    f = gitx.fetch(wt, c.remote, c.main)
    base = c.base()
    r = gitx.run(["rebase", base], wt, timeout=300)
    if not r.ok:
        return (f"rebase onto {base} stopped on conflicts:\n{(r.out + r.err).strip()[-1500:]}\n"
                f"Resolve them in {wt}, then `git rebase --continue` (or `git rebase --abort`).")
    with store.locked(c.common):
        t = store.load_track(c.common, t["name"])
        t["base"] = {"ref": base, "sha": gitx.rev(wt, base)}
        store.save_track(c.common, t)
    warn = "" if f.ok else f" (fetch failed: {f.err.strip()[:100]}; used last fetched {base})"
    return f"`{t['name']}` rebased onto {base}{warn}"


def _run_tests(cmd: str, wt: str, timeout: int) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, shell=True, cwd=wt, capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    except subprocess.TimeoutExpired:
        return False, f"tests timed out after {timeout}s"


def pr(cwd: str, name: Optional[str], draft: Optional[bool] = None, title: Optional[str] = None,
       body_file: Optional[str] = None, reviewers: list[str] = (), skip_tests: bool = False,
       extra: list[str] = (), log=print) -> dict:
    c = ctx_here(cwd)
    me = identity.me()
    t = find_track(c, name, me)
    _check_owner(t, me, "open a review for")
    wt = _worktree_of(t)
    if gitx.repo_of(wt).branch != t["branch"]:
        raise ShepherdError(f"{wt} is not on branch `{t['branch']}`")
    d = gitx.dirty(wt)
    if d:
        raise ShepherdError(f"uncommitted changes in the track: {', '.join(d[:8])}"
                            f"{' …' if len(d) > 8 else ''}. Commit them by name first.")
    if not c.remote:
        raise ShepherdError("this repo has no remote, so there is nowhere to open a review")
    url = gitx.remote_url(c.primary, c.remote)
    backend = review.select(url, c.cfg)
    if backend is None:
        raise ShepherdError(f"no review backend for {review.host_of(url)}. Install one in "
                            f"{store.home() / 'backends'} (see harnesses/shepherd/README.md).")
    f = gitx.fetch(wt, c.remote, c.main)
    if not f.ok:
        log(f"warning: fetch failed ({f.err.strip()[:100]}); checking against the last fetched main")
    base = c.base()
    ahead, behind = gitx.ahead_behind(wt, "HEAD", base)
    if ahead == 0:
        raise ShepherdError(f"`{t['branch']}` has no commits beyond {base}; nothing to review")
    if behind:
        raise ShepherdError(f"`{t['branch']}` is {behind} commit(s) behind {base}. Run "
                            f"`{CMD} sync`, re-run the tests, then `{CMD} pr` again.")
    conflicts = gitx.predict_conflicts(wt, base, "HEAD")
    if conflicts:
        raise ShepherdError(f"would conflict with {base} in: {', '.join(conflicts)}. Run "
                            f"`{CMD} sync` and resolve.")
    if c.cfg.get("test_cmd") and not skip_tests:
        log(f"running tests: {c.cfg['test_cmd']}")
        ok, output = _run_tests(c.cfg["test_cmd"], wt, int(c.cfg.get("test_timeout") or 900))
        if not ok:
            raise ShepherdError(f"tests failed; fix them before opening the review:\n{output}")
    warnings = []
    heads = gitx.branch_heads(c.primary)
    for o in store.tracks(c.common):
        if o["name"] == t["name"] or o.get("status") not in store.LIVE_WORK or \
                o.get("branch") not in heads:
            continue
        cf = gitx.predict_conflicts(wt, "HEAD", heads[o["branch"]])
        if cf:
            warnings.append(f"conflicts with track `{o['name']}` in {', '.join(cf)}")
    for w in warnings:
        log("warning: " + w)

    commits = gitx.log(wt, f"{base}..HEAD")
    files = gitx.changed_files(wt, base, "HEAD")
    if body_file:
        body = Path(body_file).read_text()
    else:
        body = review.build_body(t, commits, files, warnings, store.read_log(c.common))
    the_title = title or (commits[0]["subject"] if len(commits) == 1 else t.get("goal") or t["name"])
    rctx = review.Ctx(repo_root=c.primary, worktree=wt, remote=c.remote, remote_url=url,
                      main=c.main, base_sha=gitx.rev(wt, base), branch=t["branch"],
                      title=the_title, title_explicit=bool(title), body=body,
                      draft=c.cfg.get("pr_draft", False) if draft is None else draft,
                      reviewers=list(reviewers), extra_args=list(extra),
                      head_sha=gitx.rev(wt, "HEAD"), log=log)
    prev = t.get("review") or {}
    if prev.get("id") and prev.get("state") not in review.TERMINAL and \
            prev.get("backend") == backend.name:
        r = backend.update(t, rctx)
    else:
        r = backend.submit(t, rctx)
    r = review.normalize(r, backend.name)
    with store.locked(c.common):
        t = store.load_track(c.common, t["name"])
        if r.get("id"):
            t["review"] = r
            t["status"] = "review"
            t["note"] = None
        store.save_track(c.common, t)
        if r.get("id"):
            store.add_log(c.common, "status", t["name"],
                          f"review {'updated' if prev.get('id') == r.get('id') else 'opened'}: "
                          f"{r.get('url')}")
    _refresh_ledger(c)
    if r.get("error"):
        raise ShepherdError(r["error"] + (f" — {r['hint']}" if r.get("hint") else ""))
    r["_warnings"] = warnings
    return r


def finish(cwd: str, name: Optional[str], abandon: bool = False, keep_branch: bool = False,
           log=print) -> str:
    c = ctx_here(cwd)
    me = identity.me()
    t = find_track(c, name, me)
    _check_owner(t, me, "finish")
    wt = t.get("worktree")
    here = os.path.realpath(cwd)
    if wt and (here == wt or here.startswith(wt + os.sep)):
        raise ShepherdError(f"you are inside the track's worktree; leave it first "
                            f"(ExitWorktree, or cd {c.primary}) and run `{CMD} finish {t['name']}`")
    if wt and os.path.isdir(wt):
        d = gitx.dirty(wt)
        if d:
            raise ShepherdError(f"the worktree has uncommitted changes: {', '.join(d[:8])}")
    merged = False
    if not abandon:
        rv = t.get("review") or {}
        if rv.get("id") and c.remote:
            b = review.select(gitx.remote_url(c.primary, c.remote), c.cfg)
            if b is not None:
                st = b.status(rv, _status_ctx(c, t))
                merged = st.get("state") == "merged"
                if st.get("state") == "closed":
                    raise ShepherdError(f"the review was closed without merging; use "
                                        f"`{CMD} finish --abandon` to drop the track")
        if not merged:
            gitx.fetch(c.primary, c.remote, c.main)
            merged = gitx.merged_into(c.primary, t["branch"], c.base())
        if not merged:
            raise ShepherdError(f"`{t['branch']}` is not merged into {c.base()} yet "
                                f"(review: {(t.get('review') or {}).get('state') or 'none'}). "
                                f"Use --abandon to drop it unmerged.")
    if wt and os.path.isdir(wt):
        r = gitx.run(["worktree", "remove", wt], c.primary, timeout=60)
        if not r.ok:
            raise ShepherdError(f"git worktree remove failed: {r.err.strip()}")
    else:
        gitx.run(["worktree", "prune"], c.primary)
    notes = []
    if merged and not keep_branch:
        r = gitx.run(["branch", "-D", t["branch"]], c.primary)
        notes.append(f"deleted branch {t['branch']}" if r.ok else
                     f"kept branch {t['branch']} ({r.err.strip()})")
    elif abandon:
        notes.append(f"kept branch {t['branch']} (delete it with `git branch -D {t['branch']}` "
                     f"if you don't need it)")
    with store.locked(c.common):
        t = store.load_track(c.common, t["name"])
        t["status"] = "done" if merged else "abandoned"
        store.save_track(c.common, t)
        store.add_log(c.common, "status", t["name"], "finished (merged)" if merged else "abandoned")
    _refresh_ledger(c)
    if merged and c.remote:
        primary_branch = gitx.repo_of(c.primary).branch
        if primary_branch == c.main:
            _, behind = gitx.ahead_behind(c.primary, c.main, c.base())
            if behind:
                notes.append(f"the main checkout is {behind} commit(s) behind {c.base()}; update "
                             f"it when nobody is working there: `git -C {c.primary} merge "
                             f"--ff-only {c.base()}`")
    return f"`{t['name']}` {'finished' if merged else 'abandoned'}; worktree removed" + \
        "".join(f"\n- {n}" for n in notes)


def _status_ctx(c: Ctx, t: dict) -> review.Ctx:
    wt = t.get("worktree") if t.get("worktree") and os.path.isdir(t["worktree"]) else c.primary
    return review.Ctx(repo_root=c.primary, worktree=wt, remote=c.remote,
                      remote_url=gitx.remote_url(c.primary, c.remote) if c.remote else "",
                      main=c.main, base_sha=(t.get("base") or {}).get("sha"),
                      branch=t.get("branch") or "", log=lambda *_: None)


def refresh_review(common: str, primary: str, cfg: dict, t: dict) -> Optional[dict]:
    """Poll a track's review once and store the result (dashboard poller)."""
    remote = cfg.get("remote")
    if not remote:
        return None
    url = gitx.remote_url(primary, remote)
    try:
        b = review.select(url, cfg)
    except ShepherdError:
        return None
    if b is None:
        return None
    repo = gitx.repo_of(primary)
    c = Ctx(repo, cfg)
    rv = t.get("review") or {}
    if rv.get("id"):
        try:
            st = b.status(rv, _status_ctx(c, t))
        except Exception as e:
            st = dict(rv, error=f"{type(e).__name__}: {e}", stale=True)
        st = review.normalize(st, b.name)
        st["pushed_sha"] = rv.get("pushed_sha")
    elif hasattr(b, "discover"):
        try:
            st = b.discover(t, _status_ctx(c, t))
        except Exception:
            st = None
        if not st:
            return None
        st = review.normalize(st, b.name)
    else:
        return None
    with store.locked(common, timeout=5):
        cur = store.load_track(common, t["name"])
        if not cur:
            return None
        cur["review"] = st
        if st.get("state") == "merged" and cur.get("status") in ("active", "review"):
            cur["status"] = "merged"
            store.add_log(common, "status", cur["name"], f"review merged: {st.get('url')} — "
                          f"run `{CMD} finish {cur['name']}`")
        elif st.get("id") and cur.get("status") == "active" and not rv.get("id"):
            cur["status"] = "review"
        store.save_track(common, cur)
    return st
