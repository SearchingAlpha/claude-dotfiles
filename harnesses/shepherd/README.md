# shepherd

Tracks, reviews and a live board for several Claude Code sessions working at once —
often in the same repository. Stdlib only, Python 3.9+, git ≥ 2.38.

- A **track** is one session's unit of work: its own branch in its own worktree,
  the paths it owns, the session that owns it and the review it ends in.
- Work reaches the main branch only through a **review**: a GitHub PR (built in)
  or whatever a private backend opens.
- **Hooks** block a session from editing the protected branch or another live
  session's worktree, and warn when it touches files another track owns or changed.
- The **dashboard** shows every track: goal, session, live status, ahead/behind,
  uncommitted work, review state and checks, overlaps and predicted conflicts.

Agents follow `skills/parallel-sessions`. The user enrolls repos and runs the board.

## Layout

```
shepherd.py          entry point (CLI; `hook` / `githook` paths fail open, never exit 2)
hook.sh              Claude Code hook launcher (prefers ~/.local/bin/shepherd → same code as the CLI)
shep/
  gitx.py            repo resolution in pure Python (hot path), worktrees, merge-tree, merged checks
  store.py           state files, locking, JSONL, generated claude-sessions.md
  identity.py        the session behind a process (CLAUDE_CODE_SESSION_ID / CLAUDE_PID → ~/.claude/sessions)
  policy.py          pure rules: may this edit happen? is this shell command allowed?
  hooks.py           Claude hook adapters + git hook stubs and handlers
  tracks.py          init / start / adopt / resume / pause / log / sync / pr / finish
  review.py          backend protocol + loader; backends/github.py
  board.py           snapshot for `status` and the dashboard
  server.py          127.0.0.1 HTTP server (token cookie, read-only API) + review poller
  cli.py             argparse front end, `doctor`
static/              dashboard (vanilla JS, no build step, no CDN)
tests/               unittest; temp repos, fake sessions, fake gh
../../hooks/hooks.json   plugin hook wiring
```

## State

Per repository, in `<git-common-dir>/shepherd/` — shared by all worktrees, never committed:

| File | What |
|---|---|
| `config.json` | `mode` (observe/enforce), `remote`, `main`, `protected`, `worktree_dir`, `test_cmd`, `trailers`, `pr_draft`, `review_backend` |
| `tracks/<name>.json` | one track: goal, branch, worktree, base, agent, owns/avoid/live, status, review |
| `log.jsonl` | notes, requests, handoffs, decisions, accepts, status changes |
| `activity.jsonl` | one line per Edit/Write by a session (PostToolUse hook) |
| `sessions/<sid>.json` | per-session hook memory (warnings already shown) |
| `cache/` | changed files per branch sha, merge-tree verdicts per sha pair |

`<git-common-dir>/claude-sessions.md` is regenerated from these; a hand-written
ledger found at `init` is kept as `claude-sessions.legacy.md`.

Per machine, in `~/.claude/shepherd/` (`SHEPHERD_HOME` overrides): `repos.json`
(enrolled repos), `server.json` (dashboard pid/port/token, mode 0600), logs,
`backends/`.

## Protection

`shepherd init` installs `pre-commit`, `pre-push` and `prepare-commit-msg` stubs
in `<git-common-dir>/hooks/` (an existing hook is kept as `<name>.shepherd-orig`
and still runs). The stubs act **only when `CLAUDE_CODE_SESSION_ID` is set** — the
user's own commits are never blocked or rewritten — and let git proceed if
shepherd itself fails. In enforce mode they refuse agent commits on protected
branches and agent pushes to them; with `trailers` on, agent commits get
`Agent:`, `Agent-Session:` and `Track:` trailers.

The Claude Code hooks (`hooks/hooks.json`) are inert outside enrolled repos
(≈40 ms per edit, ≈20 ms per shell command). In enrolled repos:

| Hook | Does |
|---|---|
| PreToolUse Edit/Write/MultiEdit/NotebookEdit | deny on a protected branch or in a live other session's worktree (enforce); otherwise warn on owned/changed/recently-edited files, untracked branches, ended owners — each warning once per hour |
| PreToolUse Bash | deny `git add -A/./-u`, `commit -a`, `stash`, `reset --hard`, `clean -f`, `checkout -- .`, `restore .`, `--no-verify`, hooksPath overrides, pushes to protected branches (enforce; warn in observe) |
| PostToolUse edits (async) | append to `activity.jsonl` |
| SessionStart | a short summary of the repo's tracks and the session's inbox |

Protection is a guardrail for cooperative agents, not a security boundary. Only
the user (no `CLAUDE_CODE_SESSION_ID`) may run `init --mode enforce`, `mode` and `uninit`.

## Review backends

The built-in backend is GitHub (`gh` CLI). Private ones are Python files in
`~/.claude/shepherd/backends/` or any directory in `SHEPHERD_BACKENDS`:

```python
SHEPHERD_API = 1

class MyHost:
    name = "myhost"
    def matches(self, remote_url): ...            # does this backend own the remote?
    def submit(self, track, ctx): ...             # open the review → Review
    def update(self, track, ctx): ...             # publish new commits → Review
    def status(self, review, ctx): ...            # never raise: set error/hint instead
    def doctor(self): return [(ok, label, hint)]
    def discover(self, track, ctx): ...           # optional: find a review opened elsewhere
    def config_defaults(self, remote_url): ...    # optional: merged into config at init

BACKEND = MyHost()
```

`ctx` (`shep.review.Ctx`) carries `worktree, remote, remote_url, main, base_sha,
branch, head_sha, title, title_explicit, body, draft, reviewers, extra_args`, a
`run(cmd, cwd=None, timeout=120, input=None) -> (rc, out, err)` helper and `log()`.
A Review is a dict: `id, url, title, state` (draft · open · approved ·
changes_requested · merged · closed · unknown), `raw_state, checks` (passing ·
failing · pending · none · unknown), `comments, reviewers, updated_at, pushed_sha,
error, hint, stale`. The dashboard shows `error` and `hint` verbatim, so a backend
can tell the user exactly how to recover (e.g. re-authenticate).

External backends are tried before built-in ones; `config.review_backend` pins one
by name. A backend that fails to import shows up in `shepherd doctor`, never as a crash.

## Tests

```bash
python3 -m unittest discover -s harnesses/shepherd/tests -t harnesses/shepherd/tests
```

Each test builds a sandbox: private `HOME`/`SHEPHERD_HOME`, a bare remote whose
main branch is `trunk` (nothing may assume a branch name), fake live sessions
(a sleeping process plus a `~/.claude/sessions` entry) and a fake `gh` on `PATH`.
