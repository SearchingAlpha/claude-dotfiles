# herd

One line above the prompt and a `/herd` pane that say, from any session, which parallel tracks are blocked, in review or stale, which sessions wait on you, and whether the unattended `claude -p` runs really finished. A run can exit 0 with `subtype: success` while the background subagent it was waiting for got killed; the band turns red for that.

```
3 tracks: 1 blocked · 1 in review · 1 stale │ 1 waiting on you │ 1 bg working │ Nightly 10-09 ✗ unfinished │ Weekly 10-05 ok
```

The mod is a view. [herd](../../harnesses/herd/README.md) does the reading and the judging: `herd snapshot --json` joins shepherd's tracks, the live sessions and the run logs its sources file names, and the mod draws what it prints. The run verdicts (`unfinished`, `asked`, `late` and the rest) are described in herd's README, and the sources file in [`harnesses/herd/presets/`](../../harnesses/herd/presets/README.md).

It is observe-only. Every hook passes its event on unchanged, except `/herd` itself once the mod has registered it. It has no `tool.call` or `tool.check` hook, makes no `$.model` call and parses no transcript. The one press that reaches a model is **Ping**, which starts a turn in the session you ping (below). In a session where nothing draws (`claude -p`, cron, the SDK) it registers nothing, starts no timer and runs nothing until a client attaches, and `claude -p "/herd"` is passed on as if the mod were not there. `mods/CLAUDE.md` has the rules it follows.

## What it runs

Every 60 seconds (`refresh_seconds`), in a live session only: `herd snapshot --json --stale-hours <stale_hours>`. That takes about 0.4 s here, most of it `shepherd status`. Each part of the snapshot carries its own error:

- When herd finds no shepherd, the band leaves tracks out. When shepherd fails, the band says `tracks ?`.
- A sources file that doesn't parse shows as `runs ?`, and an unreadable log as its own `unreadable` row.
- When herd itself can't run, the band says `herd ?` and the pane says why. With herd not installed, it says how to get it: `install.sh` links it into `~/.local/bin`, or set `herd_bin`.
- A snapshot of another schema is not drawn. herd and the mod come from one checkout, so that means one of them is stale.

A live session is any session with a surface: a terminal, the desktop app, and also a background session (`claude --bg`, agent view), which starts on a terminal surface with nobody looking.

Each live session refreshes on its own clock and shares its read in `$.store`, which every session on the machine reads. When another session's read is less than three quarters of a period old (45 s at the default), a session shows that read instead of running herd again, so the machine runs herd about once a period. A shared read of another schema or other options is read again, never drawn. The read runs on the clock, outside any hook.

## The band and the pane

- **The band** is one button (hotkey `h` once the band has the focus). Pressing it opens the pane on the tab that needs you: runs with an unacked failure first, then tracks that are blocked, conflicting or failing checks, then sessions waiting on you. The `band` option turns the band off; the pane stays on `/herd`.
- **`/herd [tracks|sessions|runs]`** opens the pane, and Esc closes it.
  - A skill or command of yours may already be called `herd`: a `~/.claude/skills/herd/` folder, say, since a personal skill's folder name is its command too. Claude Code then refuses to register the mod's `/herd`, and the mod passes `/herd` on, so it runs yours.
  - The band still opens the pane, and the pane's footer says why `/herd` is missing.
- **Tracks** groups tracks by repo. The band's counts are tracks: `2 conflicting` is two tracks with a conflicting pair.
  - **Inbox** runs `shepherd inbox --json` in each repo's main checkout, and **Accept** runs `shepherd accept <id>` there. Both run with `CLAUDE_CODE_SESSION_ID` empty, so shepherd sees the person, not this session's track.
  - **Open board** runs `shepherd serve --daemon` and prints the dashboard URL, with shepherd's port-forward hint for a remote machine.
- **Sessions** sorts sessions by what needs you.
  - **Ping** sends a fixed status request with `$.session.send` to a live session other than this one, and toasts whether it was delivered. The check follows the session id across `/clear`, `/resume` and `/branch`.
  - The request lands as a message in that session and starts or joins a turn there, on your usage. It asks for a one-line answer in that session's own transcript, not a message back.
- **Runs** groups runs by source label, each with herd's numbers under it.
  - **Ack** (or **Ack all**) runs `herd ack <key>…`, which marks a failure as seen in herd's own state (`~/.local/state/herd/acked.json`). The status line clears here at once, and the pane reads again.
  - Every other session and shepherd's board see the ack at their next read. The band and the pane show an acked failure as a warning (`⚠`, and `acked` before its reason) until a newer log replaces it.
  - An Ack pressed while a refresh is reading stays acked when that refresh lands. A rewritten log alerts again, because the ack key is the log's path and mtime.
- **The status line** under the prompt (`$.ui.status`) holds the oldest unacked run failure, with a count of the rest.
- **A message from another session** (`session.receive` with origin `peer` or `peer-send-message`) shows as a toast: its kind, the sender's name from the engine's `<cross-session-message from-name=…>` envelope, and the body's first line, such as `peer from api-fix: Status check from the person`.
  - Not toasted: a hand-back from one of this session's own subagents (an `<agent-message>` envelope), a teammate's mailbox message, and a message for one of this session's agents.
  - The mod never acts on a message, its sender or its text.

Every button is the person's press. Nothing runs on a timer except the read above.

### When the mod itself fails

It fails open: a failed refresh keeps the last snapshot on screen. Its own failures show in two places, and in the debug log under `claude --debug`:

- The band starts with `herd ?` while refreshes fail, and with `as of 5m ago` once what it shows is more than three refresh periods old.
- The pane's footer names each failure:
  - the refresh that failed;
  - a read that could not be shared with the other sessions (it still shows here);
  - `/herd` not registered.

## Install and configure

```bash
claude plugin install herd@claude-dotfiles
```

The old `fleet@claude-dotfiles` is gone from the marketplace: `claude plugin uninstall fleet@claude-dotfiles` first. For runs to show, herd needs a sources file (`~/.config/herd/sources.json`; see its presets).

The options live in `pluginConfigs["herd@claude-dotfiles"].options` in user settings (`herd@inline` for a `--plugin-dir` copy), or `claude plugin configure herd@claude-dotfiles`.

| Option | Default | |
| :- | :- | :- |
| `band` | `true` | Draw the band |
| `herd_bin` | `herd` | The herd the mod runs: a name looked up on PATH, or a path (`~/` is home) |
| `shepherd_bin` | `shepherd` | The shepherd Inbox, Accept and Open board run |
| `refresh_seconds` | `60` | 15 to 3600 |
| `stale_hours` | `24` | 1 to 720. An active track with no activity for this long, or whose owner ended, is stale |

A number outside its range makes Claude Code refuse to load the mod at all: the band and the status line are gone, and only `claude --debug` says why (`herd: options do not fit plugin.json userConfig: …`).

## Files

| File | What it holds |
| :- | :- |
| `.claude-plugin/plugin.json` | Name, version, the `types` contract and the options |
| `hooks/hooks.json` | `"modules": ["./register.ts"]` |
| `hooks/register.ts` | The hooks, with `hostOf` binding `$` once: `session.start` / `session.attach` go live (timers, `/herd`); `classic.SessionStart` reloads state and the session id after `/clear`; `session.receive` toasts; `command.run`; `ui.render` for the pane and the band |
| `hooks/read.ts` | `herd snapshot`, `herd ack`, and the Inbox, Accept and board presses |
| `hooks/herd.ts` | Pure: the options, the snapshot's shape check, peer envelopes, the band's and the pane's words |
| `hooks/view.ts` | The pane's tree |
| `types/index.d.ts` | The `$.state` contract and the snapshot's shapes |
| `tests/` | `fixtures.ts` (a stub world, and a snapshot as herd prints it); `herd.test.ts` (the hooks); `words.test.ts` (the pure half and read.ts); `test_contract.py` (herd's real output against `snapshotOf` and the fixture's fields) |

## Check and try it

```bash
mods/check.sh herd                          # validate, tests, probe, headless load, tsc
python3 mods/herd/tests/test_contract.py    # what herd prints is what the mod draws (Node 22.6+)
harnesses/tools/moddev.sh herd --smoke      # headless load, no model turn
harnesses/tools/moddev.sh herd              # live: the band shows; type /herd
```

Not covered:

- The status line setting (`statusLine`), which a mod cannot draw. The band sits beside it.
- The full per-card board: **Open board** goes to shepherd's dashboard for that.
- Shepherd's own log lines. The board has them.

Tested with Claude Code 2.1.296.
