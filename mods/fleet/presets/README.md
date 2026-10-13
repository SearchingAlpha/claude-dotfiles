# fleet presets

Sources files for the fleet mod, kept in the repo. `sources.json` is the author's: the unattended runs of one cloud desktop. The format is in the [mod's README](../README.md#configure); `sources_file` names the file to read.

## sources.json

- **WBR**: the Monday 09:30 cron (`~/.claude/scripts/wbr-weekly.sh`) writes one stream-json log per branch, `~/.claude/logs/wbr-<branch>-<date>.jsonl` (wbr-its, wbr-nwd, wbr-inbound, wbr-email, retail-adherence, d2d-y2y), and a text log `wbr-weekly-<date>.log` whose last line is `=== Weekly run done; wbr_rc=… retail_rc=… d2d_rc=… ===`, or `SKIP: …` when the Midway cookie is stale. One file per run, so no `start`. No stream log for 180 h raises `late`.
- **ShortLoad**, two sources under one label:
  - `~/shortload-weekly.log` is append-only and three cron jobs write to it: `weekly.sh` (Mon 07:00; its steps start with `=== pull_leads ===` and end with its own `=== watchdog ===` verdict), `watchdog.py` (Mon 12:00, one more `watchdog: …` line, no header) and `structure_check.py` (Fri 09:00). `start: ^=== pull_leads ===$` makes only a verdict after the newest weekly run count, so last week's `todo en orden` never reads as this week's. A run that started and has no verdict yet is `running` for 120 min, then a warning.
  - `~/projects/agentic-marketplace/site/ops/weekly-run.txt` is the manifest `weekly.sh` rewrites each run (`ts <iso>`, then one `<step> <rc>` per step; `watchdog.py` reads it). Its mtime is the run's time, so it carries `expectEveryHours: 180`: no weekly run for 180 h raises `late`. The shared log cannot, because the Friday job keeps its mtime fresh.
  - The watchdog's cost ceiling and email alerts stay in `watchdog.py`; the mod only shows the verdict.

Every path is under `~/`, so the preset works on another desktop with the same layout.

What the logs read as on 2026-10-09, in a live session with this preset and in the reading half run on its own:

```
6 tracks: 1 merged · 3 in review · 1 stale │ WBR 10-05 ✗ unfinished │ ShortLoad 10-05 ok
```

- `wbr-retail-adherence` 10-05 **unfinished**: rc=0 and `subtype: success`, but `subagent_stats` says 1 spawned, 0 completed, `killed.system: 1`, and stderr has `Background tasks still running after 600s; terminating`. Its result ends "Now waiting for the deep-dive agent to finish."
- `wbr-wbr-inbound` 10-05 **asked**: it ended on numbered options and "Which do you want?", because two of its queries found no rows for the week. The report was not published.
- The other four branches are ok, and so is the weekly text log (all rc=0): the exit codes alone missed both.

## Use it

The person applies this, never an agent. Once the `fleet` marketplace entry is merged and installed (`claude plugin install fleet@claude-dotfiles`), add to `~/.claude/settings.json`:

```json
"pluginConfigs": {
  "fleet@claude-dotfiles": {
    "options": {
      "sources_file": "~/claude-dotfiles/mods/fleet/presets/sources.json",
      "shepherd_bin": "~/.local/bin/shepherd",
      "claude_bin": "~/.local/bin/claude"
    }
  }
}
```

The marketplace is the main checkout, added as a local directory, so the mod loads in place and reads the preset from the same folder: an edit to it shows at a later refresh, with no reinstall. `shepherd_bin` and `claude_bin` are paths so that a session started without `~/.local/bin` on PATH still finds them; the defaults (`shepherd`, `claude` on PATH) work in a normal login shell. To try it from a worktree before the merge, put the same options under `"fleet@inline"` with the worktree's path and run `harnesses/tools/moddev.sh fleet`.

## What stays out of the repo

Everything here is committed except confidential values: account ids, internal hosts and URLs, table and schema names, business numbers, colleagues' names and the work email, ticket and review ids. Words are not values: MCP server and tool names, project and script names, and personal paths are committed.

The mod reads no confidential value, so a preset never needs one. `skills-amazon/fleet/private.json` (gitignored) holds the ones the leak check looks for: `{"deny": [...]}`, shaped like `private.example.json`. After the merge, copy it into the main checkout's `skills-amazon/fleet/`, or point `FLEET_PRIVATE` at it.

## Check it

```bash
python3 mods/fleet/presets/test_presets.py -v
```

- Each entry of `sources.json` loads through the mod's own `sourcesOf` and `globParts` (`hooks/fleet.ts` under Node's type stripping, 22.6 or later), and sample lines of each text log read as the bullets above say (`verdictOfLines`). Skipped without Node.
- No file of the mod that git would commit (tracked, or untracked and not ignored) holds a value shaped like an account id, an email address, an internal host, a SharePoint site or a ticket id, nor any value of the private deny list. The deny-list part is skipped, with the path it looked at, when `private.json` is absent, and its hits name the file and the entry's number, never the value.

`mods/check.sh fleet` does not run this file: it runs the mod's TypeScript tests, which cannot read a JSON file.

## Open items

- `wbr-pipeline-hardening` will make the cron write a per-branch verdict file. Once it does, add a `lines` source over it, or retire the stream parsing for WBR.
- `TASKS_FILE` in `wbr-weekly-lib.sh` points at a missing path. The band does not depend on it, but the heads-up line it was meant to write is still silent.
- `operator.sh` (Mon 08:30, a `claude -p` run that appends to `~/shortload-operator.log`) is not in the preset yet.
