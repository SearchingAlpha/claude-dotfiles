# herd run sources

A sources file tells `herd runs`, `herd snapshot` and the herd mod which unattended runs to judge. `sources.json` is the author's: the headless runs of one cloud desktop. Every path is under `~/`, so it works on another desktop with the same layout.

## Use it

herd reads the file `--sources` names, else `$HERD_SOURCES` (set it empty for none), else `~/.config/herd/sources.json` when that exists. It never falls back to this preset by itself, because on a machine without these crons every schedule would read `late`. To use it here:

```bash
mkdir -p ~/.config/herd && ln -sfn ~/claude-dotfiles/harnesses/herd/presets/sources.json ~/.config/herd/sources.json
herd runs
```

Linked from the main checkout, an edit to the preset shows at the next read.

## The format

```json
{
  "runs": [
    {"label": "WBR", "glob": "~/.claude/logs/wbr-*.jsonl", "expectEveryHours": 180, "latest": 8, "maxAgeHours": 192, "runningMinutes": 20}
  ],
  "lines": [
    {"label": "WBR", "glob": "~/.claude/logs/wbr-weekly-*.log", "match": "=== Weekly run done|SKIP:", "ok": "done; (?:\\w+_rc=0 ?)+==="}
  ]
}
```

- **`runs`** are `claude -p --output-format stream-json` logs. Each one is judged from its `result` events, its task events and the stderr folded into it with `2>&1` (herd's README lists the verdicts).
- **`lines`** are text logs, judged by their last line matching `match`. That line is ok when it also matches `ok`, or when no `ok` is set, and a failure otherwise. In a log several jobs append to, `start` marks where a run begins, and only a verdict line after the last start counts.
- **`glob`**: `*` and `?` in the file name only; an absolute path or `~/`.
- **`latest`**: how many logs to show, the newest of each run name (the file name without its extension and a trailing date). Default 12 for `runs`, 1 for `lines`.
- **`maxAgeHours`**: logs older than this are left out. Default 192.
- **`runningMinutes`**: a log with no verdict yet, written within this many minutes, is a run still going. Default 15.
- **`expectEveryHours`**: when the newest log is older than this, the source raises a `late` failure. Unset means no schedule.
- Patterns are Python regular expressions, searched in each trimmed line. `\w`, `\d` and `\b` are ASCII-only, as they were in the mod's JavaScript.

A malformed entry is skipped and named in `sourcesError`; the rest still load.

## sources.json

- **WBR**: the Monday 09:30 cron (`~/.claude/scripts/wbr-weekly.sh`) writes two kinds of log.
  - One stream-json log per branch, `~/.claude/logs/wbr-<branch>-<date>.jsonl` (wbr-its, wbr-nwd, wbr-inbound, wbr-email, retail-adherence, d2d-y2y). No stream log for 180 h raises `late`.
  - A text log, `wbr-weekly-<date>.log`, whose last line is `=== Weekly run done; wbr_rc=… retail_rc=… d2d_rc=… ===`, or `SKIP: …` when the Midway cookie is stale. There is one file per run, so it has no `start`.
- **ShortLoad**: two sources under one label.
  - `~/shortload-weekly.log` is append-only, and three cron jobs write to it:
    - `weekly.sh` (Mon 07:00), whose steps start with `=== pull_leads ===` and end with its own `=== watchdog ===` verdict;
    - `watchdog.py` (Mon 12:00), which adds one more `watchdog: …` line with no header;
    - `structure_check.py` (Fri 09:00).

    `start: ^=== pull_leads ===$` makes only a verdict after the newest weekly run count, so last week's `todo en orden` never reads as this week's. A run that started and has no verdict yet is `running` for 120 min, then a warning.
  - `~/projects/agentic-marketplace/site/ops/weekly-run.txt` is the manifest `weekly.sh` rewrites each run: `ts <iso>`, then one `<step> <rc>` per step, which `watchdog.py` reads. Its mtime is the run's time, so it carries `expectEveryHours: 180`, and no weekly run for 180 h raises `late`. The shared log can't do that, because the Friday job keeps its mtime fresh.
  - The watchdog's cost ceiling and email alerts stay in `watchdog.py`. herd only shows the verdict.

How the logs read on 2026-10-13 (`herd runs`):

- `wbr-retail-adherence` 10-12 and 10-05: **unfinished**. Both runs exited 0 with `subtype: success`, but in each `subagent_stats` says Claude Code itself killed the deep-dive subagent (`killed.system: 1`). On 10-05 stderr also has `Background tasks still running after 600s; terminating`.
- `wbr-wbr-inbound` 10-05: **asked**. It ended on numbered options and "Which do you want?", because two of its queries found no rows for the week. The report was not published.
- `shortload-weekly` 10-12: **failed**, on `watchdog: PROBLEMAS DETECTADOS`.
- The other branches are ok, and so is the weekly text log (all rc=0). The exit codes alone missed every one of these.

## What stays out of the repo

Everything here is committed except confidential values: account ids, internal hosts and URLs, table and schema names, business numbers, colleagues' names and the work email, ticket and review ids. Words are not values: MCP server and tool names, project and script names, and personal paths are committed.

herd reads no confidential value, so a preset never needs one. The gitignored `skills-amazon/herd/private.json` holds the values the leak check looks for, as `{"deny": [...]}` shaped like `private.example.json`. `HERD_PRIVATE` names another path.

## Check it

```bash
python3 harnesses/herd/presets/test_presets.py -v
```

- Each entry of `sources.json` loads through herd's own `sources_of` and `glob_parts`, and sample lines of each text log read as the bullets above say.
- No file of herd or of the herd mod that git would commit holds a value with a confidential shape: an account id, an email address, an internal host, a SharePoint site or a ticket id. That covers tracked files, and untracked ones that aren't ignored. Addresses at the domains reserved for examples are allowed.
- It also checks that no value of the private deny list is committed. That part is skipped when `private.json` is absent, and the output says which path it looked at. A hit names the file and the entry's number, never the value.

## Open items

- `wbr-pipeline-hardening` will make the cron write a per-branch verdict file. Once it does, add a `lines` source over it, or retire the stream parsing for WBR.
- `TASKS_FILE` in `wbr-weekly-lib.sh` points at a missing path. herd doesn't depend on it, but the heads-up line it was meant to write is still silent.
- `operator.sh` (Mon 08:30) is a `claude -p` run that appends to `~/shortload-operator.log`. It isn't in the preset yet.
