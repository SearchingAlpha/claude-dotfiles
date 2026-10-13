# Amazon guards (opt-in)

Three PreToolUse guards for Amazon-internal tools, built on the plugin's [`../common.py`](../common.py): same fail-open rule (own errors → stderr + `~/.claude/guards/errors.log` + one notice; only a positive detection blocks), same block form (`permissionDecision: "deny"` JSON, exit 0), same `CLAUDE_GUARDS_DISABLE=<name>` switch. The plugin's `hooks/hooks.json` never registers them: `apply.sh` adds them to `~/.claude/settings.json` (user scope, so they also run in the WBR `claude -p` cron jobs), and only when you run it with `--apply`.

| Guard | Fires on | Blocks |
| :- | :- | :- |
| `sql_guard.py` | `^mcp__.*redshift.*__(execute\|explain)_query$` (redshift-mcp, redshift-mcp-server, the planned aaisd-redshift-mcp) | SQL that **starts** with a `--` or `/* */` comment or a `(` |
| `datanet_guard.py` | Bash running a Datanet write script (11 `if`-filtered handlers) | a LIVE run with no matching successful `DRY_RUN=true` run in the last 24 h, and a write-script call it can't follow |
| `recipient_guard.py` | `^mcp__aws-outlook-mcp__(email_(send\|reply\|forward\|draft)\|calendar_meeting)$` | email or a meeting invitation to any address not on the allowlist |

Each handler is `{"command": "sh", "args": ["<checkout>/hooks/guards/run.sh", "<checkout>/hooks/guards/amazon/<guard>.py"]}`: the launcher exits 0 when the guard script is missing. Registering `python3 <guard>.py` directly would not be safe: Python exits 2 on a script it can't open, and exit 2 on PreToolUse blocks the call, so a removed worktree or a checkout of a branch without this folder would stop every redshift query, including the WBR cron's, and every Datanet run.

## Code here, values elsewhere

Everything in this folder is committed: the guards, their tests, `apply.sh` and the tool and server names they match. The values that must not reach the repo — your work addresses, account ids, internal hosts, table names, ticket ids — live in `skills-amazon/guards/private.json` at the checkout's root, which `.gitignore` keeps out of git. [`private.example.json`](private.example.json) shows its shape with placeholders:

- `recipient_guard.allow`: the addresses mail and invitations may go to (case-insensitive).
- `confidential.literals` and `confidential.patterns`: the values the pre-commit scanner refuses to commit (see [Confidential values](../README.md#confidential-values-optional)). Every allowlisted address belongs here too, so the scanner catches it.

`CLAUDE_GUARDS_PRIVATE` points the guards at another file. Without a usable file (missing, no `recipient_guard.allow`), `recipient_guard` lets mail through and says, once per session, which file to create; the `apply.sh --with-recipient --apply` check refuses to register it until the file is there. `sql_guard` and `datanet_guard` need no values.

The check that matters is that no confidential **value** is committed, not that the word "amazon" is absent: these files name Amazon tools on purpose. `tests/` checks it against the real values when the checkout has them, and the pre-commit scanner checks every commit once configured (below).

## When one blocks you

- **sql_guard** — in read-only mode (the default; `MCP_READ_ONLY_MODE` is unset here) redshift-mcp-server passes SQL through only when it begins with `SELECT/WITH/SHOW/DESCRIBE/EXPLAIN` or is a temp-table operation; anything else is wrapped in `BEGIN READ ONLY; …; END;` (no rows back) and `explain_query` rejects it. This corrects the old "use `/* */`" advice: a leading `/* */` fails the same way, and so does `(SELECT …) UNION (SELECT …)`. Start with the keyword; put comments inside (`SELECT /* why */ …`) or on later lines. Writes are left to the server's own read-only error.
- **datanet_guard** — run the identical command with `DRY_RUN=true` first (same args; same argument files — editing the SQL/payload file after the dry run needs a new one), read its output, then run it live. `DRY_RUN=1`, `yes` or anything but `true` runs **LIVE** in this toolkit, and so does a bare `DRY_RUN=true;` (not exported), or one cleared again by `env -u DRY_RUN`, `env -i`, `unset DRY_RUN` or `export -n DRY_RUN`; the message says so. Prefix commands are followed and don't change the stamp (`DRY_RUN=true timeout 600 python3 …` then `timeout 900 python3 …` matches): timeout, nohup, time, nice, stdbuf, sudo, env, `uv run`, python options (`-u`, `-X dev`, `-m <script>`), if/then/for/do, `( … )`, `$( … )` and `bash -c '…'`. Forms it can't follow — xargs, watch, `python -c` importing a script, python reading the script from stdin, a pipe, `<( )` or a variable (`python3 - < run_job.py`, `cat run_job.py | python3 -`), `PYTHONSTARTUP`, a wrapper script — are refused with the plain form to use. Reading or editing a script (cat, grep, sed, git, an editor, `python3 -m py_compile`) passes. Stamps: `~/.claude/guards/datanet-dryruns.jsonl`. This is the technical check the 2026-10-06 pre-authorization keeps, not an approval gate: nobody is asked anything.
- **recipient_guard** — draft instead (`email_draft` create, or `email_reply` with `saveAsDraft: true`) and let the user send, or ask the user to add the address to `recipient_guard.allow`. Reply-all and sending an existing draft (`update` + `send: true`) are always refused, because their recipients aren't in the call. A plain reply goes to the original sender, which the hook can't see: `RECIPIENT_GUARD_UNVERIFIED=ask` (default) prompts you interactively and is refused in `claude -p`; `deny`/`allow` are the alternatives. `email_forward` cc/bcc are checked too, though 0.4.0 has them commented out of its schema. `calendar_meeting` create/update (writes are on here: `OUTLOOK_MCP_ENABLE_WRITES=true`) sends invitations with an HTML body: every `attendees`, `optionalAttendees` and `resources` address must be allowlisted (rooms too). An update that changes the meeting (body, subject, time, location …) also goes to its existing attendees, and an RSVP to the organizer; the guard can't see them, so `RECIPIENT_GUARD_UNVERIFIED` decides, as for a plain reply. Changing only categories, reminder or showAs, read and delete pass. The server's own `OUTLOOK_MCP_ALLOWED_CALENDAR_DOMAINS` check covers external domains, not people.

## Sequencing: recipient guard last

Turn on `recipient_guard` only after the WBR cron is switched to drafts (wbr-pipeline-hardening, urgent subset), otherwise the Monday 09:30Z run can be blocked mid-way. Before `--with-recipient`, check all of:

1. `grep -n 'email_send' ~/.claude/scripts/*.sh` — the WBR, retail and D2D jobs create drafts (`email_draft`), or every address they send to is allowlisted. Today `report-wbr.sh` drafts but `report-retail.sh` and `report-d2d.sh` still `email_send`, and `ALLOWED_WBR` still lists `email_send`.
2. `RECIPIENTS` in `~/.claude/scripts/wbr-weekly-lib.sh` is in `recipient_guard.allow`. The values file is seeded with both of your addresses, the one memory gives as the standing answer for the combined WBR email and the one `RECIPIENTS` uses; settle the default in one place and keep both until then.
3. Nothing else sends mail headless (`crontab -l`).

`sql_guard` and `datanet_guard` have no such dependency and can go first.

## Enable

From the main checkout, once this folder is merged there. `skills-amazon/` is gitignored, so `private.json` exists only in the checkout where it was written: copy it from the track's worktree **before** `shepherd finish`, which removes the worktree and its ignored files.

```bash
mkdir -p ~/claude-dotfiles/skills-amazon/guards
cp ~/claude-dotfiles-cc-operating-kit/skills-amazon/guards/private.json ~/claude-dotfiles/skills-amazon/guards/
cd ~/claude-dotfiles
sh hooks/guards/amazon/apply.sh                          # dry-run diff of ~/.claude/settings.json; nothing written
sh hooks/guards/amazon/apply.sh --apply                  # sql_guard + datanet_guard (settings/merge.py backs the file up first)
sh hooks/guards/amazon/apply.sh --with-recipient --apply # later, after the sequencing checks
```

`apply.sh` warns when run from a linked worktree and refuses `--apply` there unless you add `--force-worktree`: the registered paths stop working (without blocking) when the worktree is removed. Re-running is idempotent; it only appends entries that aren't there. To remove a guard, delete its entries from `~/.claude/settings.json`.

## This machine: the plugin guards' one-off setup (yours to run)

- **Pre-commit scanner** in every repo that commits outside Claude (the Sev-2.5 key came in through an Obsidian Git commit on the laptop):

  ```bash
  S=~/claude-dotfiles/hooks/guards/precommit_scan.py
  python3 $S install ~/obsidian-vaults/vault
  python3 $S install ~/projects/agentic-marketplace      # weekly.sh commits from cron
  python3 $S install ~/claude-dotfiles                   # goes behind shepherd's wrapper
  ```

  Laptop clone of the vault: `python3 precommit_scan.py install <clone> --standalone` there (or copy `.git/hooks/pre-commit` and `.git/hooks/guards-scan/`); `.git/hooks` doesn't sync, and Obsidian Git runs git hooks. On Windows the hook finds `python` or `py -3` when `python3` is only the Store alias; run `precommit_scan.py status <clone>` there once and check its `python:` line, because Obsidian Git doesn't show the hook's "commit not scanned" warning. If a repo's existing pre-commit reads its own name (husky v4, overcommit), `install` refuses; see the README.
- **Confidential values in claude-dotfiles** (it goes to GitHub): after the install above, point the scanner at the values file, by absolute path so every worktree uses the main checkout's copy:

  ```bash
  git -C ~/claude-dotfiles config guards.confidential /local/home/pablomv/claude-dotfiles/skills-amazon/guards/private.json
  python3 $S status ~/claude-dotfiles                    # "confidential values: N literals, M patterns from …"
  ```

  Worth it in agentic-marketplace too (also on GitHub). Not in the vault or the internal repos: those values belong there. Before the first push of this track, audit what it adds: `git ls-files -co --exclude-standard -z | CLAUDE_GUARDS_CONFIDENTIAL=skills-amazon/guards/private.json xargs -0 python3 hooks/guards/precommit_scan.py scan-files` (exit 0 and no output: clean).
- **Vault auto-commit** (opt-in): `~/.claude/guards/config.json` →

  ```json
  {"vault_commit": {"path": "/local/home/pablomv/obsidian-vaults/vault", "remote": "origin", "branch": "mainline", "push": true, "sweep": true}}
  ```

  The WBR cron appends raw `--output-format stream-json` to `~/.claude/logs/wbr-<label>-<date>.jsonl`. `redact` rewrites what Claude sees, but a failed call's output and an MCP result's `structuredContent` stay unredacted in that stream (see the README's redact limits), so those logs need to stay private (0600) and rotated, or be piped through `patterns.redact` (wbr-pipeline-hardening).

  `sweep: true` matters on this machine: sessions start in the home directory (not a repo) and auto mode edits through Bash, so `bashEditDiff` almost never reports vault files; without sweep only Write/Edit changes are committed. Sweep commits any vault file changed since the session's previous Stop, including the WBR cron's writes if they land in the vault while a session is open. Pushes go over ssh to git.amazon.com: an expired Midway cert shows up as `fail: push … ssh auth failed` in `~/.claude/guards/vault-push.log`; run `mwinit` and push by hand. Keep the "commit and push the vault after every change" habit until a week of `vault-push.log` shows the commits you expect.
- **Secrets already on disk**: the hook checks only the lines a commit adds. In a repo that commits from cron, or one with history, audit what is already there with `git ls-files -z | xargs -0 python3 $S scan-files` (one `path:line  kind` per hit, values never printed), and check logs and other untracked files the same way. Rotate any secret it finds that was ever pushed, then remove it from the file (for a log, also stop writing it where the log is produced). Keep the list of open findings in a gitignored note (`skills-amazon/guards/README.md`), not here.

## Tests

```bash
python3 -m unittest discover -s hooks/guards/amazon/tests -t hooks/guards/amazon/tests
```

The guards through their real entry points with placeholder addresses (`@example.com`), `apply.sh`'s fragment and its refusals (a linked worktree; `--with-recipient --apply` with no allowlist), and the committed example. `TestPrivateValues` reads the real `skills-amazon/guards/private.json`: it is valid, has the example's sections, every allowlisted address is caught by the confidential check, and no value from it appears in any file of the guards component (`hooks/guards/**`, `hooks/hooks.json`). It is skipped, saying why, in a checkout without the file.
