# fleet

One line above the prompt and a `/fleet` pane that say, from any session, which parallel tracks are blocked, in review or stale, which sessions wait on you, and whether the unattended `claude -p` runs really finished. A run can exit 0 with `subtype: success` while the background subagent it was waiting for got killed; the band turns red for that.

```
3 tracks: 1 blocked · 1 in review · 1 stale │ 1 waiting on you │ 1 bg working │ Nightly 10-09 ✗ unfinished │ Weekly 10-05 ok
```

It is observe-only. Every hook passes its event on unchanged, except `/fleet` itself once the mod has registered it. It has no `tool.call` or `tool.check` hook, makes no `$.model` call and parses no transcript. The one press that reaches a model is **Ping**, which starts a turn in the session you ping (below). In a session where nothing draws (`claude -p`, cron, the SDK) it registers nothing, starts no timer and runs nothing until a client attaches, and `claude -p "/fleet"` is passed on as if the mod were not there. `mods/CLAUDE.md` has the rules it follows.

## What it reads

Every 60 seconds (`refresh_seconds`), in a live session only:

| Source | Command or files | What it gives |
| :- | :- | :- |
| Tracks | `shepherd status --all --json`, with `GIT_OPTIONAL_LOCKS=0` so its `git status` calls never take the index lock your own `git add` needs | Each track's status, review and checks (with how long ago shepherd last checked them), conflicting pairs, warnings, owner session |
| Sessions | `claude agents --json --all` | Who waits on you (`state: blocked` or `status: waiting`, with `waitingFor`), background sessions working, done or failed. A track counts as blocked when its owner session waits |
| Runs | stream-json logs that the `runs` option or a sources file names | Every `result` event of each `claude -p --output-format stream-json` log, and the task events |
| Text logs | logs that a sources file names | The last line that matches a pattern, judged ok or failed by a second pattern |

A live session is any session with a surface: a terminal, the desktop app, and also a background session (`claude --bg`, agent view), which starts on a terminal surface with nobody looking and refreshes like the others for as long as its process lives. Sharing (below) keeps that to about one read a period for the whole machine.

A run is judged from its log alone:

| Verdict | When |
| :- | :- |
| `failed` | `is_error`, a `subtype` other than `success`, or a `terminal_reason` other than `completed` (the first of `errors` says why) |
| `running` | No `result` event and a write in the last `runningMinutes` (15 by default). Or the run answered, its background subagents are still going, and the log is that fresh |
| `unfinished` | A subagent was left running, failed or was killed, or `claude -p` logged `Background tasks still running after …; terminating` (stderr folded into the log with `2>&1`). The counts come from the last result's `subagent_stats`: of its `killed`, `system` (Claude Code's own halt: a budget, or `-p` giving up at its wait ceiling) and `user` (any other stop) count; `parent`, the model stopping a subagent itself with TaskStop, does not, and shows as `1 stopped by the model`. A build that writes no stats is judged by the task events, where every stop counts. With neither, a result whose last line says the run itself still waits on an agent or task (`Now waiting for the deep-dive agent to finish`, `I'm still waiting on 2 tasks`; not `nobody is waiting …`, `I was waiting …` or `the cron is waiting …`) |
| `quiet` | A warning: no `result` event and no write for between one and two `runningMinutes`. A run blocked on one long, silent tool call looks the same as a dead one; raise `runningMinutes` for runs that do that |
| `no result` | No `result` event and no write for twice `runningMinutes`: killed or crashed |
| `unjudged` | A warning: a log too big to read whole whose tail held no `result` event (below) |
| `asked` | A heuristic: the result's last line is a question that picks between things (`Which do you want?`, `Should I publish or wait?`; an `or` counts only beside should I, shall I or do you want), or follows two or more numbered options that read as choices (`1. Publish anyway`, not `1. Report refreshed`), which nobody was there to answer. A closing offer or pleasantry is not: `Want me to also email it?`, `Would you like me to …`, `Any questions?`, `Anything else?`, `Let me know …` |
| `denied` | `permission_denials` (summed over every result) or `permission_denied` events |
| `late` | The newest log of a source is older than its `expectEveryHours` |
| `ok` | None of the above |

The Runs tab also shows the turns (summed over results), `total_cost_usd` (an estimate, and cumulative, so the highest is shown; none when no result carries it), denials, subagents done and any the model stopped. A log is read again only when its size or mtime changes. A log over 3.5 MB is judged by its last 1 MiB (`tail -c`, marked `tail only`); when that tail holds no result, by its last 3.5 MB.

Each live session refreshes on its own clock, and caches what it read in `$.store`, which every session on the machine shares. When another session's read is less than three quarters of a period old (45 s at the default), a session shows that read instead of reading again. A stored read carries a schema number: one an older or newer build of the mod wrote is read again, never drawn. The pane footer says how long the last read took: about 0.3 s here, almost all of it shepherd and `claude agents`. The read runs on the clock, outside any hook.

## The band and the pane

- The band is one button (hotkey `f` once the band has the focus). Pressing it opens the pane on the tab that needs you: runs with an unacked failure first, then tracks that are blocked, conflicting or failing checks, then sessions waiting on you. The `band` option turns the band off. The pane stays on `/fleet`.
- `/fleet [tracks|sessions|runs]` opens the pane. Esc closes it. When a skill or command of yours is already called `fleet` (a `~/.claude/skills/fleet/` folder, say: a personal skill's folder name is its command too), Claude Code refuses to register the mod's `/fleet`, and the mod passes `/fleet` on, so it runs yours. The band still opens the pane, and the pane's footer says why `/fleet` is missing.
- **Tracks** groups tracks by repo. The band's counts are tracks: `2 conflicting` is two tracks with a conflicting pair. **Inbox** runs `shepherd inbox --json` in each repo's main checkout, and **Accept** runs `shepherd accept <id>` there. Both run with `CLAUDE_CODE_SESSION_ID` empty, so shepherd sees the person, not this session's track. **Open board** runs `shepherd serve --daemon` and prints the dashboard URL and shepherd's port-forward hint for a remote machine.
- **Sessions** sorts sessions by what needs you. **Ping** sends a fixed status request with `$.session.send` to a live session other than this one (the check follows the session id across `/clear`, `/resume` and `/branch`), and toasts whether it was delivered. The request lands as a message in that session and starts or joins a turn there, on your usage; it asks for a one-line answer in that session's own transcript, not a message back.
- **Runs** groups runs by source label. **Ack** (or **Ack all**) marks a failure as seen in `$.store`: the status line clears here at once and in every other session at its next refresh, and the band and the pane show the failure as a warning (`⚠`, and `acked` before its reason) until a newer log replaces it. An Ack pressed while a refresh is reading stays: the refresh reads the acks again before it shows. A rewritten log alerts again, because the ack key is the log's path and mtime.
- The status line under the prompt (`$.ui.status`) holds the oldest unacked run failure, with a count of the rest.
- A message from another session (`session.receive` with origin `peer` or `peer-send-message`) shows as a toast of its kind, the sender's name from the engine's `<cross-session-message from-name=…>` envelope, and the body's first line: `peer from api-fix: Status check from the person`. A hand-back from one of this session's own subagents (an `<agent-message>` envelope), a teammate's mailbox message and a message for one of this session's agents are not toasted. The mod never acts on a message, its sender or its text.

Every button is the person's press. Nothing runs on a timer except the reads above.

### When the mod itself fails

It fails open: a failed refresh keeps the last snapshot on screen. Its own failures show in two places, and in the debug log under `claude --debug`:

- The band starts with `fleet ?` while refreshes fail, and with `as of 5m ago` once what it shows is more than three refresh periods old.
- The pane's footer names each one: the refresh that failed, a read that could not be stored for the other sessions (it still shows here), `/fleet` not registered.

## Configure

The options live in `pluginConfigs["fleet@claude-dotfiles"].options` in user settings (`fleet@inline` for a `--plugin-dir` copy), or `claude plugin configure fleet@claude-dotfiles`. All but `runs` also show in `/config`; a `multiple` list does not, so set `runs` in the settings JSON.

| Option | Default | |
| :- | :- | :- |
| `band` | `true` | Draw the band |
| `tracks`, `sessions` | `true` | Read shepherd, `claude agents`. A binary that cannot start (ENOENT) is left out of the band and the pane says why; one that starts and fails shows as `tracks ?` or `sessions ?` |
| `shepherd_bin`, `claude_bin` | `shepherd`, `claude` | A name looked up on PATH, or a path (`~/` is home) |
| `runs` | none | Stream-json log globs: `Label=glob` or a bare glob (label `runs`). Wildcards in the file name only |
| `sources_file` | none | A JSON file with more sources, below |
| `refresh_seconds` | `60` | 15 to 3600 |
| `stale_hours` | `24` | 1 to 720. An active track with no activity for this long, or whose owner ended, is stale |

A number outside its range (`refresh_seconds` 15 to 3600, `stale_hours` 1 to 720) makes Claude Code refuse to load the mod at all: the band and the status line are gone, and only `claude --debug` says why (`fleet: options do not fit plugin.json userConfig: …`).

A sources file holds what the options cannot: text logs, schedules, patterns. `presets/sources.json` is the author's, committed with its how-to in `presets/README.md`; yours can sit anywhere `sources_file` names. The shape:

```json
{
  "runs": [
    { "label": "Nightly", "glob": "~/logs/nightly-*.jsonl", "expectEveryHours": 30, "latest": 12, "maxAgeHours": 192, "runningMinutes": 15 }
  ],
  "lines": [
    { "label": "Nightly", "glob": "~/logs/nightly-wrapper-*.log", "match": "=== run done|SKIP:", "ok": "done; (?:\\w+_rc=0 ?)+===" },
    { "label": "Weekly", "glob": "~/weekly.log", "start": "^=== weekly start ===$", "match": "^check: (fine|PROBLEMS)", "ok": "fine", "runningMinutes": 120 }
  ]
}
```

- A `runs` entry shows the newest log of each run name, which is the file name without its trailing date: a calendar date after `-`, `_` or `.`, with an optional time and run counter after it (`nightly-report-2026-10-09.jsonl` and `nightly-report-2026-10-09-2.jsonl` are both `nightly-report`, as is `nightly-report_20261009T0300.jsonl`). An epoch stamp or a build number is not a date, so `run-1728468000.jsonl` is a run name of its own: stamp such logs with a date. It shows logs no older than `maxAgeHours`, at most `latest` of them.
- A `lines` entry judges its newest log, unless `latest` asks for more. A log with no matching line is `running` while it is fresh and a warning after that.
- A log that holds several runs, one appended after the other, needs `start`: the pattern of the line each run begins with. Only a verdict line after the last one counts, so last week's `fine` never reads as this week's; a run that started and has no verdict yet is `running`, then a warning.
- `expectEveryHours` keys on the file's mtime. On a log that other jobs also append to, the mtime stays fresh when the run you care about did not happen: give that run its own file (a per-run log or manifest) and put `expectEveryHours` on a `lines` entry over it, under the same label.
- Entries with the same label share one piece of the band. A log two entries name is one run.
- A malformed entry, or one whose `match`, `ok` or `start` pattern does not compile, is skipped and named in the pane.

To log a cron run this way: `claude -p "…" --output-format stream-json --verbose > ~/logs/nightly-report-$(date +%F).jsonl 2>&1`.

## Files

| File | What it holds |
| :- | :- |
| `.claude-plugin/plugin.json` | Name, version, the `types` contract and the options |
| `hooks/hooks.json` | `"modules": ["./register.ts"]` |
| `hooks/register.ts` | The hooks: `session.start` / `session.attach` go live (timers, `/fleet`), `classic.SessionStart` reloads state and the session id after `/clear`, `session.receive` toasts, `command.run`, `ui.render` for the pane and the band. `hostOf` binds `$` once |
| `hooks/read.ts` | One refresh over a narrow `Reader`, plus the Inbox, Accept and board presses |
| `hooks/fleet.ts` | Pure parsing and judging: shepherd and agents JSON, stream-json facts and verdicts, sources, peer envelopes, the band's words |
| `hooks/view.ts` | The pane's tree |
| `types/index.d.ts` | The `$.state` contract and the snapshot's shapes |
| `tests/` | `fixtures.ts` (stub world, real-shaped JSON, logs and envelopes), `parsers.test.ts`, `fleet.test.ts` |
| `presets/` | `sources.json` (the author's headless runs) and its `README.md`; `test_presets.py`, which loads the preset through `hooks/fleet.ts` and scans the mod for confidential values; `private.example.json`, the shape of the gitignored deny list it reads |

## Check and try it

```bash
mods/check.sh fleet                         # validate, tests, probe, headless load, tsc
python3 mods/fleet/presets/test_presets.py  # the preset through the mod's parser; no confidential value committed
harnesses/tools/moddev.sh fleet --smoke     # headless load, no model turn
harnesses/tools/moddev.sh fleet             # live: the band shows; type /fleet
```

Not covered:

- The status line setting (`statusLine`), which a mod cannot draw. The band sits beside it.
- The full per-card board: **Open board** goes to shepherd's dashboard for that.
- Shepherd's own log lines. The board has them.

Tested with Claude Code 2.1.295.
