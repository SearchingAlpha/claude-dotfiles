# guards

Hooks that keep credentials out of the places they leak from — files git can carry, shell commands, and the tool output Claude reads — plus an opt-in batched auto-commit for a notes vault and a git pre-commit scanner for commits made outside Claude Code. Stdlib Python 3.9, registered by the plugin's `hooks/hooks.json`, live wherever the plugin is enabled.

| Piece | Event (matcher) | Does |
| :- | :- | :- |
| `secret_guard.py` | PreToolUse (`Write\|Edit\|NotebookEdit\|Bash\|PowerShell\|Monitor`) | blocks a secret-shaped literal written into a git work tree or a vault dir, any full credential in a shell command, and redaction placeholders written back over real values |
| `redact.py` | PostToolUse (`^(Agent\|Bash\|PowerShell\|Read\|Grep\|WebFetch\|WebSearch\|TaskOutput\|ReadMcpResourceTool\|mcp__.*)$`), PostToolUseFailure (`^(Bash\|PowerShell\|ReadMcpResourceTool\|mcp__.*)$`) | replaces secrets in tool output with `[REDACTED:<kind>]` before Claude sees it; warns when it can't |
| `vault_commit.py` | PostToolUse (`Write\|Edit\|NotebookEdit\|Bash\|PowerShell`, async), Stop | **opt-in**: one commit per turn of the vault files Claude changed, then a background push |
| `precommit_scan.py` | git `pre-commit` (installed per repo by hand) | refuses commits whose added lines hold a secret, or (optional) a known confidential value, whoever makes them |
| `patterns.py` | — | the shared detector |
| `run.sh` | — | the launcher every hook goes through: finds a working Python 3, and never blocks when it or the guard can't run |
| `amazon/` | not registered here | opt-in guards for Amazon-internal tools (Outlook recipients, Redshift SQL, Datanet dry runs), applied to user settings with `amazon/apply.sh`; see [amazon/README.md](amazon/README.md) |

## What counts as a secret

`patterns.py` reports only the kind and line, never the value:

- **Named shapes**, precise enough to apply anywhere: Anthropic `sk-ant-…`, OpenAI `sk-proj-…`, AWS access key ids `AKIA…`/`ASIA…`, Slack `xox?-…` tokens and webhooks, GitHub `gh?_…` and `github_pat_…`, GitLab `glpat-…`, Google `AIza…`, Stripe `sk_live_…`/`rk_live_…`, Hugging Face `hf_…`, npm `npm_…`, PyPI `pypi-…`, SendGrid `SG.….…`, JWTs (`eyJ….eyJ….sig`), and PEM private keys with key material. A private key's finding runs from its `BEGIN` line to its `END` line (or, in truncated output, to the end of the base64 block), in plain or JSON-escaped (`\n`) form, so redaction hides the whole body.
- **Credentials in context**: the password in `scheme://user:password@host`, `sshpass -p <pw>`, `Bearer <random token>`, and `mysql … -p<pw>` when the MySQL client is the command word (`mysql`, `/usr/bin/mysql`, `sudo mysql`, `docker exec db mysql`, `kubectl exec pod -- mysql`, `sh -c 'mysql …'`) and the `-p` is on the same command. `'*mysql*'`, `mysql:8`, `/var/lib/mysql` and an option's value (`--name mysql`) aren't the client, and port maps (`-p3306:3306`) and find/cc flags (`-print`, `-prune`, `-pthread`) belong to other programs. Missed on purpose: a client right after a flag-only option (`sudo -E mysql …`).
- **Assignments** whose name says key/token/secret/password (`API_KEY=…`, `"client_secret": "…"`, `{\"api_key\": …}`, `?api_key=…`, `--token=…`, `env: API_KEY=…`) and whose value has at least 20 characters and is hex (24–128) or random-looking. A name that only ends in `key` (`"key"`, `run_key`) needs a random mixed-case value that isn't a file name (an S3 `"Key": "Report_2026-Q3.xlsx"`), unless it is an UPPER_CASE env name such as `OPENWEATHER_KEY`. Shorter values, ordinary words and most hand-made passwords are not flagged.

Never flagged on their own: hashes (git, sha256, docker digests, `integrity`), UUIDs, base64 blobs, public keys (`MFkw…`, `MIIBIjAN…`), GPG `signingkey` fingerprints, `NextToken`/cursor/`pagination_token` values, `cache_key`/`dedupe_key`/`content_key` identifiers, AWS ARNs (`…:123456789012:secret:Name-AbC123` is a resource name, not a `secret:` assignment), `Sec-WebSocket-Key`, env references (`$TOKEN`, `${PASS}`, `os.environ[...]`), placeholders (`AKIA…EXAMPLE`, `your-key-here`, `xxxx`, ascending runs like `123456`, `Bearer eyJhbGciOi...`), and search patterns such as `grep 'sk-ant-'`. The test suite pins this, and scanning this whole repository finds nothing. A presigned URL's `X-Amz-Security-Token` counts as a credential and is redacted up to the next `&`, so the URL stops working in Claude's copy; pass such URLs through a file or an environment variable.

Measured gaps for assignment values (2,000 random samples each): 7% of 20-character mixed-case alphanumerics, 1–2% of 32–40 character ones and of AWS-style secret keys, and 1% of 32-character lower-case+digit ones don't look random enough and pass. Named shapes have no such gap, apart from about 0.6% of Hugging Face tokens.

A line containing `guards:allow-secret` is skipped by `secret_guard` and the pre-commit scanner — for deliberately fake fixtures only — but not by `redact`: in tool output, one marker on a minified JSON line would let every secret on it through. Better still, build fixtures at run time by concatenation, as `tests/support.py` does.

## secret_guard

```mermaid
flowchart TD
  A[PreToolUse] --> P{redaction placeholder<br/>would replace real values?}
  P -- "shell command carrying one<br/>(not a plain grep / rg / git grep)" --> D([deny])
  P -- "Write / NotebookEdit over the file,<br/>or a new file beside a same-stem one" --> D
  P -- "mv / cp / ln -f / dd, or > / tee fed by<br/>a file holding them, over one holding the values" --> D
  P -- no --> B{secret-shaped literal<br/>in the new text?}
  B -- no --> OK([allow, silent])
  B -- yes --> C{tool}
  C -- Bash / PowerShell / Monitor --> D
  C -- Write / Edit / NotebookEdit --> E{target under a<br/>configured vault dir?}
  E -- yes --> F{value already<br/>in the file?}
  E -- no --> G{inside a git work tree<br/>and not gitignored?}
  G -- no --> OK
  G -- yes --> F
  F -- no --> D
  F -- yes --> W([allow + note to Claude])
```

- A gitignored file (`.env`, `secrets/…`) is the right home for a secret, so writing one there passes; a tracked file that happens to match `.gitignore` is still protected. A file under `.git/` (`.git/config`) is never committed, so it passes too. Without git on PATH a file in a work tree counts as tracked, and the reason says git couldn't be asked.
- An edit that keeps a value the file already holds passes with a note, so you can still edit around a legacy key — or delete it.
- **Placeholders**: Claude never sees a redacted value, only `[REDACTED:<kind>]`. Writing that back would replace the real value, so these are refused: a `Write`/`NotebookEdit` that puts more of some placeholder into an existing file than the file holds; one that creates a new file holding them next to a same-stem file that holds values of those kinds (`conf.yaml.new`, `conf.new.yaml`, `.conf.yaml.swp` beside `conf.yaml`); a `mv`/`cp`/`install`/`rsync`/`ln -f`/`dd of=` of a file holding them over one that holds fewer (`-t DIR` and `--target-directory` included); a `>` redirection or a `tee` (not `>>` or `tee -a`) fed by such a file in the same pipeline (`cat a > b`, `sed … a > b`, `tee b < a`, `cat a | tee b`) over a file that holds real values of those kinds; and any Bash, PowerShell or Monitor command carrying one (a heredoc, `sed -i`, `python -c`, `Set-Content`, a commit message…), except one plain `grep`/`rg`/`git grep`/`git log` with no pipe, redirection, `;` or `$(…)`. Claude is told to change just the lines it needs with `Edit` in the original file, which can't clobber a value it never saw, and how to search. Not caught: a copy made by a program rather than the shell (`python -c 'shutil.copy(…)'`, `git checkout`, `patch`, `sort -o`), and commands whose words the guard can't resolve — run through `bash -c`, `eval`, `xargs` or `find -exec`, or naming files by variable or glob (`cp "$SRC" app.env`, `cp draft.* app.env`). A line with the allow marker doesn't count, for notes about redaction itself.
- The deny reason (shown to Claude, with a one-line notice to you) names the kind and line and says to load the value from the environment or a gitignored file. It is a `permissionDecision: "deny"` on stdout with exit 0, the documented single channel, which blocks in every permission mode, bypass included; if stdout can't be written, it falls back to exit 2. Your notice reads `PreToolUse:Write says: secret_guard blocked …`. Claude's copy of the reason arrives prefixed `PreToolUse:Write hook error:`: Claude Code 2.1.295 labels every hook deny that way, exit 0 or 2 alike (checked live).
- It scans what a tool call carries, not what a command does: a script that writes a key it reads from elsewhere isn't seen (the pre-commit scanner is the backstop). A blocked call's input is still in the session transcript, because Claude already wrote it.

## redact

Walks `tool_response`, rewrites every string leaf that holds a finding, and returns it as `hookSpecificOutput.updatedToolOutput` with the exact shape Claude Code sent (Bash `{stdout, stderr, interrupted, isImage, noOutputExpected}`, Read `{type, file: {content, …}}`, Grep `{mode, content, …}`, MCP content blocks, a foreground subagent's `{status, content: [{type, text}], …}`). Claude Code ignores a replacement that doesn't match a built-in tool's schema, so nothing else is touched; paths are skipped, and so are strings of 512+ characters under `data`/`source`/`image`/`base64` made only of base64 characters (or a `data:…;base64,` URI): images. Minified JSON or a long URL under those keys is scanned. A multi-line finding (a PEM key) keeps its line breaks, so line numbers after it still match the file. An `additionalContext` note tells Claude what was replaced. Prints nothing when nothing matched.

Limits, measured on Claude Code 2.1.294–2.1.295:

- A **failed** call (non-zero Bash exit, MCP error) fires PostToolUseFailure, which can't rewrite output. redact then adds a warning only, and the raw value does reach Claude, the transcript and the `stream-json` log.
- A successful call's replacement is what Claude sees, and for Bash, Read and text-only MCP results also what `stream-json` carries (checked live). An MCP result with `structuredContent` (any server with an `outputSchema`; FastMCP derives one from return annotations) reaches the hook as its text content only, so Claude sees the redacted text while the `stream-json` event's `tool_use_result.structuredContent` keeps the original. Per the docs, OpenTelemetry tool spans capture the original too. Logs of `claude -p --output-format stream-json` therefore need the same care as the tool's own output: keep them private (0600) and rotated, or pipe them through `patterns.redact`. The tool already ran: this is a second layer behind not printing secrets in the first place.
- `stream-json` and the session transcript also keep what a tool call itself carries. An `Edit` or `Write` event's `tool_use_result` holds the whole file before the change (`originalFile`) and the new content, unredacted, though Claude only sees the short "updated successfully" text. Bash output longer than its read-back window (`BASH_MAX_OUTPUT_LENGTH`, 30,000 characters by default) is kept whole and raw in `~/.claude/projects/<project>/<session>/tool-results/<id>.txt`, even with `--no-session-persistence`: the rewrite reaches only the 2 KB preview Claude gets, and a later `Read` of that file is redacted again. Output that fits the window but not the inline limit is saved already redacted. Both checked live on 2.1.295.
- A foreground subagent's answer (the `Agent` result) is redacted before the parent sees it (checked live, default and auto mode); the `stream-json` `task_notification` event's `summary` and the task's output file keep the original. Not covered: background subagents (their result arrives later as a notification, `status: "async_launched"` is all the hook gets), a report a subagent delivers through the `SubagentHandback` tool (per the docs it travels in that tool's input, which no PostToolUse output rewrite reaches; a live `-p --permission-mode auto` run didn't use it), files you `@`-mention, Monitor event lines and other background notifications.
- Claude can't put a redacted value in an `Edit`'s `old_string`; it has to edit around those lines.

## vault_commit (opt-in)

Inert until you name a vault — a git repository of notes — in `~/.claude/guards/config.json`:

```json
{
  "vault_commit": {"path": "/abs/path/to/vault", "remote": "origin", "branch": "main", "push": true, "sweep": false}
}
```

or `export CLAUDE_GUARDS_VAULT=/abs/path/to/vault` (defaults: remote `origin`, the current branch, push on, sweep off; `message_prefix` changes the `vault:` subject prefix). The vault path is then also a protected dir for secret_guard.

- **Record** (PostToolUse, async): each vault path Claude changes with Write, Edit or NotebookEdit goes to `~/.claude/guards/vault-pending/<session>.jsonl`. Bash and PowerShell changes count only when Claude Code lists them in `tool_response.bashEditDiff`, a best-effort beta field: it is filled in when `bashEditDiffEnabled` is on (user or managed settings), or in auto and bypassPermissions mode only when Claude Code directs Claude to edit through Bash; it covers the repository the session works in, not the vault elsewhere, and a live `-p --permission-mode bypassPermissions` run carried none. A diff marked `shared` with a parallel command is ignored.
- **Sweep** (`"sweep": true`): at Stop, also commit the vault files git reports as modified or new whose mtime falls after this session's previous Stop (from just before its first tool call, for the first turn). This catches edits made by shell commands and scripts. It includes changes from anything else that wrote to the vault in that window, another session or you; deletions count only when recorded, and a window with more than 500 changed files is skipped and logged. The vault may be a subdirectory of its repository: only that subdirectory is listed and committed.
- **Commit** (Stop, once per turn): takes `flock <git-dir>/claude-autocommit.lock`, runs `git add -- <paths>` and `git commit --only -- <paths>` with `GIT_LITERAL_PATHSPECS=1` — exactly those paths, so a note named `Meeting [12].md` or `:!x.md` is that file and never a pattern; your staged or untracked work on other paths stays as it was — with a message like `vault: update notes/today.md`. Gitignored paths are skipped, deletions are committed. The repo's own pre-commit hooks run.
- **Push**: a detached `git push <remote> <branch>`, so it survives the end of a `claude -p` run. Never `--force`, never a rebase, pull or stash.
- **Outcomes**: every commit attempt is a line in `~/.claude/guards/vault-push.log` (`ok: committed …`, `ok: pushed …`, `skip: …`, `fail: …`), including pushes rejected because the remote moved on or ssh auth expired. A busy repo (merge/rebase in progress), a held lock, a detached HEAD, a checked-out branch other than the configured `branch` (a commit there would never be pushed; one notice per session), a missing git or a refused commit keeps the paths for the next turn; a refused commit also shows you a one-line notice. A pre-commit hook slower than 3 s leaves the commit to finish in the background: you get a notice, the push waits for it, and the paths are re-checked next turn.
- Lists of sessions that ended mid-turn (interrupted, so no Stop) are picked up by any later Stop after two hours.

Git runs in its own process session, so a hook timeout can't kill it half-way and leave `index.lock` behind. Without `sweep`, vault files changed only through Bash still need a manual commit.

Linux and macOS only: the lock is `fcntl.flock`. On Windows a configured `vault_commit` fails open at every Stop (one notice per session) and commits nothing.

## precommit_scan (git hook)

Claude's hooks never see commits made by cron jobs, editors, Obsidian Git or people. Install the scanner per repository:

```bash
python3 hooks/guards/precommit_scan.py install <repo>              # calls this file where it lives
python3 hooks/guards/precommit_scan.py install <repo> --standalone # copies the scanner into the repo's hooks dir
python3 hooks/guards/precommit_scan.py status <repo>
python3 hooks/guards/precommit_scan.py uninstall <repo>             # restores what it displaced
python3 hooks/guards/precommit_scan.py scan-files <file>...         # audit files already on disk
```

- It writes `<git-common-dir>/hooks/pre-commit`, shared by every worktree. An existing hook is moved to `pre-commit.guards-orig` and run after the scan (a symlinked one is moved as the link, never written through). If `pre-commit` is shepherd's wrapper, the scanner goes in as `pre-commit.shepherd-orig`, which the wrapper runs for every commit (inside Claude Code or not). It refuses a repo that sets `core.hooksPath` and never sets it itself.
- A displaced hook that dispatches on its own file name (husky v4, overcommit: `basename "$0"`) would run as `pre-commit.guards-orig` and silently skip its checks, so `install` refuses it; add the scan line to that hook by hand, or pass `--force` to chain it anyway.
- Run `install` from the main checkout so the hook points at a stable path (it warns when run from a linked worktree), or use `--standalone` (re-run after updating the patterns; `status` says when the copy or the hook script is stale).
- The hook finds Python the way `run.sh` does: `python3`, then `python` 3.7+, then `py -3`, skipping the Windows Store alias. `status` names the interpreter it would use on the current PATH; GUI git clients may run hooks with another one.
- Files git won't diff as text (a `binary` or `-diff` attribute, an early NUL byte) are scanned whole when they decode as UTF-8 and are under 5 MB; real binaries are skipped.
- `.git/hooks` doesn't sync: every clone needs its own install, including the laptop clone a git-syncing notes app commits from (`--standalone`, or copy `pre-commit` plus `guards-scan/`).
- A refused commit lists `path:line  kind`; the value is never printed. A missing scanner, no Python 3 or a crashing scanner prints a warning and lets the commit through; GUI clients such as Obsidian Git may not show that warning, so check `status` once on each clone.

### Confidential values (optional)

Some values must stay out of a repo without looking like secrets: account ids, internal host names, table names, people's addresses, ticket ids. List them in a JSON values file that git never carries (a gitignored file, or one outside the repo), and the scanner refuses a commit that adds one:

```json
{"confidential": {
  "literals": ["123456789012", "your_schema_name"],
  "patterns": [{"name": "internal-host", "regex": "(?i)[a-z0-9-]\\.internal\\.example\\b"}, "\\bTICKET-\\d{6,}\\b"]
}}
```

```bash
git -C <repo> config guards.confidential /abs/path/to/private.json   # once per repo; add more with --add
python3 hooks/guards/precommit_scan.py status <repo>                  # "confidential values: 2 literals, 2 patterns from …"
```

- The file is named per repo by `guards.confidential` in its git config, which every worktree shares. A relative path is taken from the top of the work tree the commit is made in, so a worktree without its own copy isn't checked (it warns): use an absolute path to the main checkout's file. `CLAUDE_GUARDS_CONFIDENTIAL` (paths joined with `:`, `;` on Windows) overrides it, and set but empty turns the check off. Don't set it in the global git config: the values usually belong in other repos (the internal ones they come from).
- `literals` match case-insensitively anywhere, as substrings; one shorter than 4 characters is skipped. `patterns` are Python regexes, a string or `{"name", "regex"}`. Start a pattern with a literal or a single character class (`[a-z0-9-]\.internal\.example`) rather than a repeated one (`[a-z0-9-]+\.internal…`), which can retry every position of a long run. Measured on 4.5 MB of added lines: 18 literals take 6 ms per MB together, a pattern that starts with a literal next to nothing, one that starts with a class 12–20 ms per MB; a file of 18 literals and 8 patterns adds about 100 ms per MB to the secret scan's 35, and 2 ms to a small commit.
- A hit prints `path:line  confidential-literal-3` (the third literal in the file) or `confidential-<name>`, never the value; with several files the kind ends in `@<n>`. `guards:allow-secret` on the line skips it, as for secrets. Nothing else in the file is read, so the same file can hold other sections (the Amazon guards keep their allowlist in it, see [amazon/README.md](amazon/README.md)).
- A values file that is missing, unreadable or not JSON, a too-short literal or an invalid regex is a warning on stderr and in `errors.log` (naming the file and the entry number, never a value), and `status` lists it as a problem; the rest of the scan still runs.
- `scan-files` uses the same setting, so an audit of a whole checkout is `git ls-files -z | xargs -0 python3 hooks/guards/precommit_scan.py scan-files`: one `path:line  kind` per hit, nothing when clean.

## Failing open

A guard that breaks must not stop your work. Bad payloads exit 0 silently. Internal errors exit 0, print to stderr, append a scrubbed line to `~/.claude/guards/errors.log`, and show you one notice per session and guard on the first synchronous hook that fails (an async hook's notice would reach Claude instead of you, so the async recorder only logs). Only a positive detection blocks. Claude Code itself treats a hook timeout (5 s here) as non-blocking, so watch for these notices when you first enable the plugin.

Every hook runs as `sh run.sh <guard>` in shell form, which works under sh and, on Windows, Git Bash. `run.sh` tries `python3`, then `python` (3.7+), then `py -3`, and skips the WindowsApps `python3` alias that only prints "Python was not found". With no usable Python it exits 0, so nothing is blocked, and says once a day that the guards are off.

A missing or unreadable guard script gets the same treatment: exit 0, a line on stderr, a once-a-day notice. That matters because the plugin is read in place from a checkout and a running session keeps the `hooks.json` it started with: after a pull, branch switch or partial checkout that renames a guard, `python3 <missing>.py` would exit 2, and exit 2 blocks every matched PreToolUse call and keeps Claude going at Stop. Guards registered in user settings rather than `hooks.json` (the opt-in `amazon/` ones) go through the same launcher with a path, `sh <checkout>/hooks/guards/run.sh /abs/path/x_guard.py`, for the same reason; only a missing `run.sh` itself is left to the shell, whose exit 127 doesn't block. On Windows without Git Bash, Claude Code runs shell-form hooks in PowerShell, where `sh` doesn't exist, so these hooks fail as non-blocking "hook error" notices, as shepherd's do.

Switches:

- Start Claude Code with `CLAUDE_GUARDS_DISABLE=secret_guard,redact` (or `all`) to switch guards off for that session; `"secret_guard": false`, `"redact": false` in `config.json` to keep them off.
- `CLAUDE_GUARDS_HOME` moves the state dir (tests use it); `CLAUDE_GUARDS_CONFIG` points at another config file.
- `vault_dirs` in `config.json`: extra directories where no secret may be written, gitignored or not (a list, or one path as a string). Booleans accept `true`/`false` or their string forms; a value of the wrong type is logged to `errors.log` and the default used.

## Speed

A typical call takes 35–45 ms wall clock through the launcher, two shells and Python start-up included (budget 100 ms); a detection, which also asks git whether the path is ignored, about 50 ms; a `cp` whose 4.6 MB source is read for placeholders, 45 ms. The detector's cost is linear in its input: ordinary text and base64 (of JSON too) scan at 30–50 ms per MB, input dense in PEM headers or JWTs at about 200 ms per MB, and input that is nothing but secret-named assignments (a finding every 50 bytes) at about 500 ms per MB. Tool output reaches `redact` capped (Bash at 30,000 characters by default, 150,000 at most), so it stays far inside the 5 s timeout; `Write` content and pre-commit diffs have no cap, and 5 MB of nothing but assignments takes about 2.5 s. A test pins the shapes that used to be quadratic: base64 of JSON holds an `eyJ` every few dozen characters, and the JWT rule once retried each of them against the rest of the run (3 s for 90 KB, now 3 ms). These figures are from an idle 8-core machine; under a load of 12 they doubled. With `sweep` on, the Stop hook runs `git status` once per turn: about 80 ms in a 3,000-note vault, 180 ms when it commits.

## Tests

```bash
python3 -m unittest discover -s hooks/guards/tests -t hooks/guards/tests
python3 -m unittest discover -s hooks/guards/amazon/tests -t hooks/guards/amazon/tests   # the opt-in Amazon guards
```

Every test pipes Claude Code's real payload shapes (captured from a 2.1.294 session) through the real entry points, in temp git repos with a bare remote. Covered: each detection and the false-positive suite (ARNs, S3 keys, public keys and other programs' `-p` flags next to the word mysql included), the inputs that used to be quadratic, private keys of every shape leaving no body line, deny/allow/gitignore/`.git/`/vault/already-present paths and the no-git reason, placeholders written back through Write, a same-stem copy, mv/cp/ln/dd, `-t` target dirs, `>` and `tee` (but not `>>`) and shell commands, plain searches for them, redaction keeping the output shape (subagent answers included), long non-base64 strings under blob keys, the allow marker ignored in output, the failure warning, vault commits of exactly the edited paths (glob, pathspec-magic and newline names included) with background push and every failure branch (a vault on another branch than configured included), the sweep (also of a vault that is a subdirectory), pre-commit install/chain/uninstall including behind shepherd's wrapper, over a dangling symlink and in worktrees, its Python discovery and binary-marked files, the confidential-values check (literals, named and unnamed patterns, the allow marker, non-ASCII line numbers, bad entries as warnings, a relative path from a subdirectory, the env override, worktrees, `status`, speed), the launcher with no Python, only a Windows stub, a missing or unreadable guard and a guard given by path, the hooks.json registrations, crash → fail-open with a logged error, and the latency budget.
