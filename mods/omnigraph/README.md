# omnigraph

omnigraph is a live pane over a typed graph while Claude works on it. When Claude runs a graph command (a Bash command your pattern matches, or a tool of an MCP server you name), the mod reads the graph's store before and after, works out which nodes and edges changed, and shows it straight away: a node-link drawing of the touched node's neighbourhood, a Miller-column browser and a feed of every op with its latency and outcome. It also adds a one-line band above the prompt, `/omni`, and a toast when ops arrive while the pane is closed.

```text
◉ omnigraph · dev.omni · main                                     5 ops · +1n +2e · 1 ×
1: Graph  2: Browse  3: Feed  4: Queue  f: following ops  click the graph to browse with ↑↓←→
Offline editing & sync › contains › Conflict toast hides unsynced edits ● live

     crdt ●···                               ·● Backoff for sync ret…
                   tagged ×2         contains ×6
sync-core ●·············    ·····     ··········◆ Conflict toast hides…
                        ············◉···········● Persist the offline …
                         Offline editing & sync
 Nadia Okonkwo ●····            ·        ··· ··● Presence dots after …
                           ← specifies   targets
     RFC: Offline editing… ●·                     ·● v1.5.0
● Epic  ● Label  ● Actor  ● RFC  ● Issue  ● Release
── browse ──────────────────────────────────────────────────────────────────
Epic 4                       Offline editing & sync         Conflict toast hides unsynce…
  Hard tenant isolation      → contains · Issue 6           ← contains · Epic 1
▶ Offline editing & sync     ▶ Conflict toast hides unsyn…    Offline editing & sync
  Sync observability basel…    Persist the offline edit q…
── feed ────────────────────────────────────────────────────────────────────
13:41:24 ▸ mutate create_issue       +1n +0e    31ms ✓
13:41:24 ▸ mutate add_issue_to_epic  +0n +1e    41ms ✓
```

The engine is generic: it reads a small documented store format, and an adapter of `userConfig` options maps another producer's build onto it. A synthetic issue-tracker demo ships in `demo/` so the whole thing runs anywhere, and `presets/` holds tested adapters for real producers, the SupplyChainAgents ontology build first ([presets/README.md](presets/README.md)).

Contents: [Try it in 60 seconds](#try-it-in-60-seconds) · [The pane](#the-pane) · [How an op is seen](#how-an-op-is-seen) · [The store format](#the-store-format) · [Options](#options) · [What it never does](#what-it-never-does) · [Files](#files) · [Tests](#tests) · [Limits](#limits)

## Try it in 60 seconds

Needs `python3`. The pane docks beside the transcript in fullscreen mode (`"tui": "fullscreen"`) from 110 columns (144 or more is comfortable); elsewhere it opens above the prompt.

```bash
mkdir -p ~/omni-demo && cd ~/omni-demo
export PATH="<claude-dotfiles>/mods/omnigraph/demo:$PATH"   # puts the demo CLI on PATH as `omnigraph`
omnigraph init                                              # seeds graphs/dev.omni and queries/queries.gq
claude --plugin-dir <claude-dotfiles>/mods/omnigraph        # or plain `claude` once the mod is installed
```

In the session:

1. Type `/omni`. The pane docks on the right, drawn around the busiest node (the offline-sync epic); the band above the prompt reads `omnigraph · dev.omni · 31n 41e · 0 ops · …`.
2. Ask Claude: *"Using the omnigraph CLI (`omnigraph schema` lists the ops), create the issue 'Conflict toast hides unsynced edits', add it to the offline-sync epic, assign it to Nadia and make it block the three-way merge issue."*
3. Watch the pane while Claude runs `omnigraph mutate create_issue …`, `omnigraph mutate add_issue_to_epic --store graphs/dev.omni --params '{"epic":"epc-offline-sync","issue":"iss-offline-conflict-toast"}'` and the rest: each op lands in the feed (`+1n +0e 31ms ✓`), the graph jumps to the node it touched (a diamond marks it), and the breadcrumb follows.
4. Click the graph and browse with the arrow keys; Enter opens a node, Backspace goes back, `f` follows ops again, `2` opens the browser. Click `3: Feed` for every op and its error, `4: Queue` for the review queue (or `/omni focus` and use the digits).

Commands you run yourself with `!` (bash mode) do not go through Claude's tools, so they are not ops; ask Claude, or run them in another terminal and use `/omni reload`.

## The pane

| Tab | What it shows |
| :- | :- |
| `1` Graph | The selected node's neighbourhood: the node in the middle, incoming edges' nodes on the left, outgoing on the right, one group per edge type with its label (`contains ×6`, `← specifies`), a `+N more` where a group outgrows the rows, and a legend of node labels by colour. Under it the browser's columns and the last four ops. |
| `2` Browse | Miller columns: a label's nodes → the selected node's relations grouped by direction, type and target label (`→ contains · Issue 6`) → the next node's, and so on; a `type` picker for the first column, `h` back, and the selection's properties with the source lines its `path:line` property names. |
| `3` Feed | The last 50 ops on this root, every session's: time, op, `+nodes +edges` (`~1n` when records changed, `·n ·e` when there was nothing to diff), latency (`bg` for a command that went to the background), `✓`/`✗`/`…`, and the error under a failure. |
| `4` Queue | Nodes in a review status (`proposed`), open conflicts first; `export` writes a patch file per node (see below) and applies nothing. |

The header reads `store · branch` and this session's totals since it started or was cleared: ops, net `+n +e`, and failures (`×`). Ops other sessions on the same root recorded are in the feed but not in these totals. A warning line under the breadcrumb names a store file it could not read, lines it could not parse, or the mod's last internal error (`/omni reload` clears that). The breadcrumb is the browse path, `● live` while the pane follows ops. Following stops when you browse and starts again with `f`.

The graph is a `Client` region: a click on it hands it the keys, then `↑↓←→` move the selection to the nearest node that way, Enter (or a second click) draws the graph around it, Backspace goes back one, `f` follows ops. Esc gives the keys back. In the VS Code extension and the mobile app, which have no `Client`, the graph is drawn as text; VS Code's integrated terminal is the terminal surface and draws the graph. The client's props carry names, labels and edge types cut to 64 cells (the drawing shows 32 at most), as the engine refuses a `Client` whose props pass 100,000 characters and puts its own placeholder in place of the whole pane; a store whose ids alone would pass that has its graph drawn as text.

`/omni [graph|browse|feed|queue|focus|close|clear|reload]` opens the pane (on a tab, or holding the keyboard), closes it, clears this root's feed, or reads the store again. `/omni` runs mid-turn, and opens the pane without taking the keyboard, so you keep typing to Claude while it follows the work. For the tab hotkeys (`1`-`4`, `f`, `h`) the pane needs the keyboard: `/omni focus`, Ctrl+X Tab, or a click on the pane, as the [interface docs](https://code.claude.com/docs/en/plugins/mods/interface) list them (in a scripted terminal, `/omni focus` worked; a click pressed the tab buttons and gave the graph the keys, but did not visibly focus the pane). While the graph holds the keys, `1`-`4` switch tabs too. When a skill or command of yours is already called `omni` (a `~/.claude/skills/omni/` folder, say: a personal skill's folder name is its command too), Claude Code refuses to register the mod's `/omni` and the mod passes `/omni` on, so yours runs; the band still opens the pane, whose warning line says `/omni not registered`. In `claude -p`, where the mod is inert, `/omni` is passed on as well.

The band shows `omnigraph · store · branch · Nn Ee · totals · last op` while a store is present or the feed has ops, with an `open` button. When ops arrive and the pane is not showing, a toast says how many (at most one every 20 s).

## How an op is seen

```mermaid
sequenceDiagram
  participant C as Claude
  participant M as omnigraph (tool.call observer)
  participant T as Bash / MCP tool
  participant S as store files
  C->>M: tool.call Bash "omnigraph mutate add_issue_to_epic …"
  Note over M: pattern test only, nothing else before next
  M->>T: next(e)
  T->>S: writes nodes.jsonl, edges.jsonl, manifest.json, ops.jsonl
  T-->>M: result (ran, refused or failed)
  M->>S: stat the three files; read them only when one changed
  Note over M: diff against this store's last read: +n +e, touched nodes, focus
  M->>S: the ops log's new lines, when it grew
  M->>M: feed ($.state, then $.store), follow, toast
  M-->>C: the same result, unchanged
```

- A Bash command is an op when `cmdRegex` matches it; the first capture group that matched names the op (`mutate add_issue_to_epic`). The default needs the CLI as the command: at the start of a line or after `;`, `&&`, `|` or `(`, past shell keywords (`do`, `then`, `else`, `if`, `while`, `{`, `!`), wrappers with their flags (`env`, `time`, `timeout 30`, `nohup`, `exec`, `command`, `nice -n 5`, `sudo -u me`, `xargs -n1`), `VAR=value` words, `bash -c '`, a folder, or a `python3` interpreter and its flags (`-u`, `-X dev`). So `for i in a b; do omnigraph mutate …; done` is an op, and `echo omnigraph init` or a one-line commit message that names it is not. A command Claude Code's own check found read-only (`cat`, `grep`, `sed -n`) is never one. An MCP tool is an op when its name starts with `mcpPrefix` (the rest names it).
- The pattern reads at most the first 16 KB of a command and, past that, its last 16 KB from a line start (a long heredoc, then the graph command). Every run in the default stops at a separator (`[^\s;&|()]*`, `[ \t]+`), so a 120 KB command costs about a millisecond. Write your own the same way: a `\S*` or `\s*` before a literal makes the engine rescan the rest of the command after every `;`, and the time grows with the square of its length (12 s for 120 KB of `;a`, which stalled the session's hooks; within the 16 KB slices, about half a second).
- The store is the `store` option under the session root, or the command's `--store <dir>` (`storeFlag`) under its working directory and a leading `cd <dir> &&` or `(cd <dir> &&`. A flag only the shell can spell (`"$STORE"`, a backtick, a glob, `~user`) is not guessed: the op is feed-only (`·n ·e`). A `--store` folder with no store in it does not replace the store the pane shows. The mod reads no environment variable for it: the demo CLI's `OMNIGRAPH_STORE` (its tests set it) moves the CLI's default store but not the mod's, so pass `--store` with it.
- After the call the mod stats the store's three files and reads them only when one changed. Each store keeps its last read (the last four stores), so the diff is against that store's own read before: net nodes and edges, changed records, the touched ids, and where to look. That is the first new (else changed) node, drawn around its busiest neighbour; else a new edge, drawn around its busier end with the other highlighted. An op that adds or changes more than five nodes does not move the view.
- With an ops log (`opsFile`), the mod reads the lines it gained since its last read: those after the last line it read then, so a log the producer trims at the top to a fixed length works too. It takes the newest that started while the call ran and whose `op` the command names, for its op name, `ms` and `error`. A call that wrote no record gets none: an older record never names it. The tool's own outcome decides `✓`/`✗`; a record can only add the producer's reason, or turn a zero exit into `✗` when it says `ok: false`. Without a record, latency is the tool call's wall time.
- A Bash call that runs in the background (`run_in_background`, a timeout, Ctrl+B) is recorded as started (`bg …`) without reading the store; the next op's diff carries what it did. One the engine refused, or that failed to start, is a failure (`✗`), not a running op.
- Ops are recorded one at a time, in the order their calls settled, so overlapping calls (parallel tool calls, subagents) each get a feed entry and their own ops-log record. The first one recorded carries the store changes of both.
- The feed is kept per session root in `$.store`, shared by the sessions on that root, and copied back after `/clear`, `/resume` and `/branch`. The feeds of the 40 roots used last are kept (each worktree is a root).
- When the session root moves, the pane takes that root's feed and its configured store at once, so the first op there has a diff. Claude Code raises no `CwdChanged` for `/cd`, so the mod looks after `/cd`, after the `EnterWorktree` and `ExitWorktree` tools, on `CwdChanged`, and at each turn's start for anything else; where the root did not move, that costs one `$.session.root()` read. `/cd` was checked in a live session; the worktree tools only through the test kit.

## The store format

A store is a folder (`omnigraph/1`):

| File | What it holds |
| :- | :- |
| `manifest.json` | A JSON object. Read: the version (`versionKey`, default `version`), `name` (the header's store name), and an open-conflicts list (`conflictsKey`). Counts (`nodes`, `edges`, `labels`, `edge_types`, `status`) are for humans; the mod counts the records itself. Optional: without it the version is a hash of the records. |
| `nodes.jsonl` | One node per line: `{"id": "iss-1", "label": "Issue", "props": {"name": "…", "status": "open", "source": "docs/x.md:12"}}` |
| `edges.jsonl` | One edge per line: `{"from": "epc-1", "type": "contains", "to": "iss-1", "props": {}}`; a repeated edge counts once |
| `ops.jsonl` | Optional, appended per op (trimming its oldest lines is fine): `{"t": <ms epoch when the op started>, "kind": "mutate", "op": "create_issue", "ok": true, "ms": 31, "error": "…"}`. Log failures too (a bad argument, a refusal), so the feed shows the producer's reason. |

Write the manifest last, and each file whole (write a temp file, rename it), so a reader never sees half a store. `demo/omni.py` is a reference writer. A byte-order mark is skipped, and control characters in names, labels, types and statuses are drawn as spaces (the patch keeps the record's own id).

**Another producer's build** works with options only: `nodeKeys` and `edgeKeys` rename the record keys (`uid kind .` reads a node's properties off the record itself), `versionKey` names the manifest's version key, `nameKeys` the display-name properties, `statusKey` and `sourceKey` the review status and the `path:line` source. A line that does not parse is counted in the header's warning, not fatal. A producer you use often gets a preset in `presets/`: its options in one file, a test of its pattern and shape, and `preset.py block` to print the settings.

## Options

`userConfig` values live under `"pluginConfigs": { "omnigraph@claude-dotfiles": { "options": { … } } }` in user settings (`omnigraph@inline` for a `--plugin-dir` copy), or `claude plugin configure omnigraph@claude-dotfiles`. Its `--values-stdin` drops empty values (2.1.295), so a whole preset goes in as the block `presets/preset.py block` prints.

| Option | Default | What it does |
| :- | :- | :- |
| `store` | `graphs/dev.omni` | The store folder, relative to the session root, or absolute |
| `storeFlag` | `--store` | A flag whose value names the store a command writes; empty: always `store` |
| `storeLabel` | empty | The header's store name; empty: the manifest's `name`, else the folder's |
| `cmdRegex` | the demo CLI's `mutate`/`query`/`init`, as the command | A JavaScript regular expression for graph commands; anchor yours to the command word and stop every run at a separator (`[^\s;&\|()]*`, `[ \t]+`, never `\S*`) as the default does. Empty: no Bash ops |
| `mcpPrefix` | empty | MCP tool-name prefix of graph ops, e.g. `mcp__graph__` |
| `manifestFile`, `nodesFile`, `edgesFile`, `opsFile` | `manifest.json`, `nodes.jsonl`, `edges.jsonl`, `ops.jsonl` | The store's file names; `opsFile` empty: none |
| `versionKey` | `version` | The manifest's version key |
| `nodeKeys`, `edgeKeys` | `id label props`, `from type to` | The record keys, in that order |
| `nameKeys` | `name title` | Properties tried for a node's name |
| `statusKey`, `sourceKey`, `conflictsKey` | `status`, `source`, `open_conflicts` | Review status, `path:line` source, manifest list of open conflicts; empty turns one off |
| `queueStatus`, `acceptStatus`, `owner` | `proposed`, `accepted`, empty | What the Queue lists, and what a patch sets |
| `patchDir` | empty | Where exports go (one folder per store); empty: `omnigraph/patches` under the Claude Code configuration folder |
| `band`, `toast` | `true`, `true` | The band above the prompt; the new-ops toast |

## What it never does

- It never changes what Claude does. Every hook passes its event on and returns the engine's answer unchanged; nothing refuses, rewrites or answers a tool call. Commands and tool calls that are not graph ops cost one regular-expression test over at most 32 KB of the command; `/cd`, the worktree tools and each turn's start cost one `$.session.root()` read while the root stays put.
- In a session where nothing draws (`claude -p`, cron, the SDK) it registers nothing and reads nothing until a client attaches.
- It reads the store, the store's ops log, the root's `.git/HEAD` and, in Browse, the source lines a node names, only when the path lands under the session root (spelled and with links resolved). It writes only its own `$.store` (the feed), `errors.log` in its folder under the Claude Code configuration folder (its own failures, the last 200 lines), and, when you press `export` in the Queue tab, a patch file under `patchDir`. It never writes the store or the repository: applying a patch is yours (or a script of the producer's that validates it).
- A patch is the record's id, the accepted status and the owner, as YAML. The comment lines hold no line break of any kind, whatever the store's names say: no `\n` or `\r`, and no NEL, U+2028 or U+2029, which YAML 1.1 readers such as PyYAML also take for line breaks. The quoted values escape those three (and DEL, C1, U+FFFE, U+FFFF), so the id reads back as the store holds it. The file is `<id>.yaml` when the id is safe in a file name, else `<id-slug>-<hash>.yaml`:

  ```yaml
  # omnigraph patch for dev.omni 4be52b68f527, exported 2026-10-09T13:41:24.000Z
  # was: proposed (Issue) "Prorated invoices on plan change"
  - id: "iss-bill-proration"
    status: "accepted"
  ```

## Files

| Path | What it holds |
| :- | :- |
| `.claude-plugin/plugin.json` | Name, version, the `types` contract and the options |
| `hooks/hooks.json` | `"modules": ["./register.ts"]` |
| `hooks/register.ts` | The hooks: going live, the `tool.call` observers, `/omni`, `/clear`, a moved root (`/cd`, the worktree tools, `CwdChanged`, `turn.start`), the pane, the graph client's messages, the band; every `$` call is here |
| `hooks/graphstore.ts` | The store format and adapter, the diff, naming ops, the ops-log record, the feed's words; no `$` |
| `hooks/text.ts` | Cleaning control characters, display width in cells (wide characters take two), cutting to a width |
| `hooks/ego.ts`, `hooks/canvas.ts` | A node's neighbourhood picked from the store, laid out on a character grid, and the arrow-key and click rules |
| `hooks/graph-view.ts` | The graph's `Client` module: draws the layout, posts keys and clicks to `register.ts` |
| `hooks/views.ts` | The pane's and the band's trees |
| `types/index.d.ts` | The `$.state` contract and the op and store shapes |
| `demo/omni.py`, `demo/omnigraph` | The synthetic issue-tracker CLI (`init`, `schema`, `mutate`, `query`, `status`) and its PATH wrapper |
| `demo/test_omni.py` | The CLI's tests, and the check that `tests/demo-store.ts` matches what the CLI writes |
| `presets/` | Adapter presets (`sca.json` and its how-to `sca.md`), `preset.py` (print one as settings, check a real build with it), the real-build test it runs, and their Python tests: [presets/README.md](presets/README.md) |
| `tests/` | `engine.test.ts` (store, adapter, diff, ops log, ring, layout, browser), `omnigraph.test.ts` (the hooks through the test kit), `hardening.test.ts` (failures, overlap, background, store resolution, hostile data, its own failures), `presets.test.ts` (each preset's pattern and options, and the sca preset on a made-up build of its shape), `fixtures.ts` (the stub world), `demo-store.ts` and `presets.ts` (generated) |

## Tests

```bash
mods/check.sh omnigraph                                        # validate, 98 tests, probe, headless load, tsc
python3 -m unittest discover -s mods/omnigraph/demo -p 'test_*.py'
python3 -m unittest discover -s mods/omnigraph/presets -p 'test_*.py'
python3 mods/omnigraph/demo/test_omni.py --regen               # after changing the demo CLI
python3 mods/omnigraph/presets/test_presets.py --regen         # after changing a preset
harnesses/tools/moddev.sh omnigraph                            # live, with the debug log in mods/.debug/
```

The debug log has one line per op with the mod's own cost (`mutate create_issue recorded in 16ms`). In a live session on the demo store that was 8-16 ms per op after the tool answered. An op on a store whose files did not change only stats them. A changed store is read and parsed again before the tool's result goes back to Claude, so the cost grows with the store: in the test kit, a real build of about 1,000 nodes and 2,000 edges took 8-9 ms for an unchanged op and 17-19 ms for a changed one (`presets/preset.py check`); a 20,000-node, 43,000-edge store (4.5 MiB) about 60-75 ms to parse and 150-250 ms for a changed op. A hub with 20,000 neighbours, the busiest node and so the first one shown, took 16 ms for its graph, 52 ms for its browser column and 86 ms for the whole Graph tab. A 120 KB Bash command full of separators took the pattern about a millisecond; live, its `tool.call` settled 6 ms after the built-in diff mod's. Tested with Claude Code 2.1.295.

## Limits

- One op per Bash call: a command that runs two graph commands is one op, named by the first match, with both diffs together. A pattern match inside a quoted string after a separator (`git commit -m "fix; omnigraph init"`) is still an op, with no change, and so is a line of a heredoc body or a multi-line string that starts with the CLI (`cat > notes.md <<'EOF'` with an `omnigraph init` line, a commit message's second paragraph).
- A graph command the pattern does not see (a wrapper it does not know, such as `uv run`; a line continuation before the CLI's arguments; one in the middle of a command longer than 32 KB) is no op, and its change lands on the next op's diff.
- Overlapping calls on one store share a diff: the first recorded carries both changes, the other shows `+0n +0e`. A change another session or a background command made shows in the next op's diff, or after `/omni reload`.
- The first op on a store the mod has not read yet (a new `--store`) has no diff (`·n ·e`); after that it has one. A moved root's configured store is read when the root moves.
- Without an ops log, latency includes any time the permission prompt waited for you.
- Ops are seen in the sessions that load the mod.
- A store file over 4 MiB is not read: the pane keeps the last read, the op has no diff, and the header says so.
- The Queue only exports patches; there is no accept button yet.
