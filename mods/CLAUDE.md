# Writing mods in this repo

A mod is a plugin whose `hooks/hooks.json` names a hooks module (`"modules": ["./register.ts"]`). Each folder under `mods/` is one mod, its own plugin, validated, tested and installed on its own. Start every mod from `mods/_template/` (see its README) and keep it passing `mods/check.sh <name>`.

Verified against Claude Code 2.1.294 and 2.1.295. The declarations the engine lays in `<mod>/.claude-plugin/types/claude-code/index.d.ts` are the authority for this build: grep them (`'tool.call'`, `Pane: {`, `export type ToolCallResult`) before trusting this page or the online docs. `mods/check.sh` re-lays them on every run, so after an update `tsc` shows what moved.

## Loop

1. Copy the template and rename `mod-template` everywhere; `_template/README.md` has the commands. The plugin name is the folder name (`mods/check.sh` fails a `mods/<name>` whose plugin is named otherwise, and two mods with one name). Validate refuses a name that passes as Anthropic's: one starting with `claude-`, `anthropic-`, `anthropics-` or `cc-plugin-`, one of `claude`, `anthropic`, `anthropics`, `claude-code`, `claude-mods`, or one with `official` beside `claude` or `anthropic`. It only warns on `claude`, `anthropic` or `anthropics` as a whole word anywhere else (`fleet-for-claude`), but `check.sh` runs `--strict`, which fails on warnings, so that name fails too.
2. `harnesses/tools/moddev.sh <name>`: a live session with `--plugin-dir mods/<name>` and the debug log in `mods/.debug/<name>.log`. It reloads on save; a reload runs `register` and `session.start` again. The folder is a [protected path](https://code.claude.com/docs/en/permission-modes#protected-paths): Claude's edits to it prompt you in default and `acceptEdits` mode, go to the classifier in auto mode, are denied in `dontAsk` and allowed in `bypassPermissions`.
3. `mods/check.sh <name>` before every commit: validate `--strict` plus the gate's own rules, `claude plugin test`, the probe, a headless load, `tsc` (each is below). It exits 1 on a finding and 2 when a step could not run (npm, claude, hooks turned off). A `claude` run that outlives `MODS_TIMEOUT` (120 s) is killed; for the tests, the probe and the load that is a finding.
4. `harnesses/tools/moddev.sh <name> --smoke` shows the headless load on its own (`hooks module <name>@inline loaded`).
5. After a Claude Code update: `mods/check.sh --all`, and `mods/check.sh --selftest`, which proves the gate still fails forty-three broken copies of the template at the step it should and passes six good ones (about three minutes).

Two traps behind steps 3 and 4. The types under `.claude-plugin/types/` exist only after a load, and from 2.1.295 only a load that watches the folder lays them: an interactive `--plugin-dir` session, or `-p` with `CLAUDE_CODE_PLUGIN_DIR_WATCH=1` (the variable is in the [mods reference](https://code.claude.com/docs/en/plugins/mods/reference) settings table; that it decides whether `-p` writes types is not). `validate` and `plugin test` never write them. And `claude -p "/<command>" --plugin-dir mods/<name>` sends the text to the model when the mod is inert headless, as it should be: its command is never registered. `check.sh` loads beside the mod a helper of its own that answers `/mods-typegen`, with `--setting-sources project` and `--strict-mcp-config`; the probe reads this build's events, `$` calls and tools from the declarations a load of the helper alone lays, once per build. Every `claude` it runs gets a minimal environment (`HOME`, `PATH`, the locale and XDG folders, `CLAUDE_CONFIG_DIR`), so `CLAUDE_CODE_PLUGIN_DIRS`, bare mode or a provider login in your shell change nothing. Every request goes to a closed local port, and `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING` and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` are set, because the `env` of your user settings is not read there. When the helper itself does not load (hooks modules turned off by a managed policy), the load could not run (exit 2).

## What check.sh holds you to

- **Validate.** `--strict` lets a missing `.catch` pass; the gate does not. It fails a hook, a glob such as `*`, `!x` or `classic.*` included, on `tool.check`, `classic.PreToolUse` or `classic.PermissionRequest`, and nothing clears that. It fails a hook on an event the probe does not fire (below), unless the mod's `README.md` declares it: a line that starts `Changes <event>: <why>` (a list item and bold are fine: `- **Changes prompt.submit:** adds the open ticket`). A line inside an HTML comment or a code fence does not count, a near miss gets a `note:`, and no line clears `tool.call` or those three. It fails anything in the plugin but its hooks module: a `hooks` block in `hooks/hooks.json` (settings-style command, prompt or HTTP hooks, which run outside the module where nothing checks them), `skills/`, `agents/`, `commands/`, `output-styles/`, `workflows/`, `themes/`, `monitors/`, `bin/`, `settings.json`, `.mcp.json`, `.lsp.json`, and the plugin.json keys that add them (`hooks`, `mcpServers`, `lspServers`, `dependencies`, `settings`, ...). Command hooks belong in the repo's `hooks/`. And it fails a `mods/<name>` whose plugin is not named `<name>`.
- **Probe.** A generated test in a copy of the mod, its own tests removed. It runs four sessions: a terminal one; one that starts with nothing attached and that the desktop app then attaches to; one before `session.start`; and one with nothing attached (`claude -p`, cron, the SDK). Each runs with the default options and again with each boolean `userConfig` option flipped. In each it fires every event of the list below that the mod hooks, in the order a session raises them, and fails when the event that reached the engine (once) or the answer that came back differs. With nothing attached it also fails on any `$` call but the reads listed under [Where it runs](#where-it-runs), from any hook: a `tool.call`, `prompt.submit` or `classic.Stop` observer as much as `session.start`. Its ids, folder, session id and words are new each run; the version is the real one.
  - `tool.call` with every built-in tool of this build, an `mcp__probe__tool`, and each tool name and field value a matcher names, exactly as written (`'git push, now'` is fired as that). A RegExp gets the strings its parse gives (the first and the last branch, a class's first member, each repeat at its minimum); one the probe cannot make a string for (a lookahead it cannot meet, say) fails the probe, for `tool.call` and for any other event the README does not declare. The fields of one matcher are fired together. Each call also comes from a subagent (`agentId`), and four ways: the tool runs, the engine refuses it, it fails (`isError`), a `PreToolUse` hook beneath the mod refuses it (the tool must not run then). `classic.PreToolUse` must be raised once inside each call. The tools the probe sees the mod register with `$.tool.register` are left out, and a `review:` line says when the mod answers one in place of the engine; any other `mcp__<plugin>__*` name is fired like the rest.
  - `prompt.submit`, `prompt.section`, `prompt.context`, `prompt.attachment`, `prompt.mention`, `skill.prompt`, `attribution.text`, `tool.describe`, `command.run` (the mod's own command too, in each session where it has not registered it: before `session.start` and with nothing attached), `command.describe`, `config.set`, `config.describe`, `agent.offer`, `agent.spawn`, `session.compact`, `session.send`, `session.receive`, `session.measure`, `session.start`, `session.attach`, `session.detach`, `session.end`, `turn.start`, `turn.step`, `turn.complete`, and every other `classic.*` event (`classic.SessionStart` with each `source`, `startup` first), with the values a matcher names, and with `agentId` / `agent_id` where the event carries it. A declared event is fired too: its changes are not checked, but with nothing attached it must not act either.
  - Free to hook without the probe: the drawing events `ui.render`, `ui.resolve`, `ui.press`, `ui.input`, `ui.select`, `ui.message`, `ui.fault`, `ui.focus`, `ui.scroll`, `ui.close`. Some get a `review:` line: `ui.render` on a site other than `Pane` or `AbovePrompt` (it changes how Claude Code draws that site), `ui.press`, `ui.input` or `ui.select` without a `{ plugin: '<name>' }` matcher (it can take another mod's button press, typing or pick), and every `ui.resolve` hook (it can restyle or remove any plugin's elements).
  - Not fired, so declare or drop: `prompt.fill`, `prompt.suggest`, `prompt.edit`, `prompt.autocomplete`, `prompt.compose`, `session.append`, `plugin.register`, `engine.create`, `telemetry.*`, and every mods-API event (`fs.read`, `ui.open`, ...; a hook there answers other mods' calls).
  - Each probe test gets (`MODS_TIMEOUT` − 15 s) / the number of tests, at least 3 s, so a hook that never returns is named by its test. The probe answers `$.clock.sleep` at once and never fires a `$.clock.after` or `every` callback.
  - A failed probe test is a finding unless its first line is the test kit refusing the probe's own stubs (`HooksError: test: hooks module did not load:`), which is the gate's (exit 2). What a hook answers cannot turn a finding into that.
- **Load.** A real headless load, after the probe so a mod that acts never runs for real: skipped when the probe saw it act. It fails when the module does not load, when the engine logs the mod making a `$` call but the reads (with no login a model call fails before it sends anything, and it still counts), when a settings-style hook of the mod loads (`Loading hooks from plugin: <name>`), or when the load does not end.
- **What it cannot see.** The probe sees only what its inputs trigger: a hook keyed on a value no matcher names (one that refuses `rm -rf` inside a hook without a matcher), a string or number option it leaves at its default, state that builds up over more calls than it makes, a change on a site or event it does not fire, or a declared event it has no input for still needs a reader. The real load then runs the mod for what a headless start raises. And a mod that tells the probe from a session on purpose is out of scope: the gate catches mistakes, it is not a sandbox. Each `review:` line is for the person reading the diff.

## Static analysis (validate refuses the module otherwise)

- Spell every call `$.noun.method(...)` in full. Never assign, destructure or index `$` or a namespace (`const ui = $.ui` fails).
- `$` may be passed only to a function declared at the top level of the same file (validate lists the call `via` it) and to the `read` / `update` helpers from `'claude-code'`. Not to a method, a nested function or an imported function. For a mod split over several files, bind `$` once in `register.ts` into a plain object of closures (each spelled `$.fs.read(path)` etc.) and pass that object around: `hostOf($)` in code-modernization's `hooks/register.ts` is the worked example.
- Event names are string literals: `on('tool.call', ...)`, never a variable or a loop.
- One hook per event without a matcher: a second `on('session.start', ...)` makes the module fail to load. Do everything for an event in one hook.
- Do not redeclare `on` inside `register`.
- Imports: relative files of the mod, `'claude-code'` (types, `atom` / `read` / `update`), and `'claude-code/testing'` in tests only. ES modules, `import` declarations at the top; no `require`, no dynamic `import()`.
- Each `atom` sits in a `const`, with `plugin` and `key` as string literals, and each value is declared under the plugin's name in `types/index.d.ts` (named by `"types"` in `plugin.json`). The contract file has no imports and no `export {}`.
- The module has no DOM, no Node, no timers: `$.clock.after/every` instead of `setTimeout`, `$.fs`, `$.process`, `$.http` instead of Node APIs.

## Observe-only by default

These mods watch and draw. They do not decide what Claude may do; enforcement lives in permission rules and the guard hooks (`hooks/`), which are settings-style `PreToolUse` hooks and run only after the last mod calls `next`.

- Every hook on an engine event returns `next(e)` or `await next(e)` and returns that result unchanged. To act after the event: `const result = await next(e)`, then observe, then `return result`.
- Never answer `tool.call` without `next` (`{ deny }` or `{ result }`): that skips the permission check and every `PreToolUse` hook, the guard pack and shepherd included. The one exception is a tool the mod adds itself (below).
- Never rewrite a tool call's input: in auto mode the call is denied (`a hook changed this call's input after the model wrote it`).
- Never hook `tool.check`: a mod there can approve a call a `PreToolUse` hook or an `ask` rule stopped.
- Never hook `classic.PreToolUse` or `classic.PermissionRequest`. A mod's `classic.PreToolUse` runs above the settings `PreToolUse` hooks (the chain is managed hooks, then modules, then the other settings hooks), so its `{ allow: true }`, `{ deny }` or `updatedInput` decides a call before the guards see it; observe calls with `tool.call`. `classic.PermissionRequest` answers the permission dialog in the person's place.
- Every other `classic.*` hook returns `next(e)`'s result unchanged: each classic result can carry `block`, `preventContinuation` and `stopReason`, and some carry more (`updatedToolOutput` on `PostToolUse`, `suppressOriginalPrompt` on `UserPromptSubmit`, `additionalContext` on several). `agent.spawn` likewise: never `{ deny }`, never a rewritten prompt, model or agent type.
- Never rewrite `prompt.submit` text or answer it with `{ drop }`. Adding `context` is a design decision to state in the mod's README, not a default.
- The same goes for what Claude reads and what the session keeps: never drop or rewrite a system-prompt section, a reminder, the first-message context, a skill's text, a tool's description, an @-mention's file, a `/config` change, a compaction, a message between sessions or a subagent type.
- A mod that changes an event by design says so in its `README.md` with a line that starts `Changes <event>: <why>` (for example `Changes prompt.submit: adds the open ticket as context`; as a list item or in bold is fine), where a reader sees it: not in an HTML comment or a code fence. The gate then stops checking what that event's hooks change (it still fires them with nothing attached) and prints the line for review. No line clears `tool.call`, `tool.check`, `classic.PreToolUse` or `classic.PermissionRequest`.
- A tool the mod adds with `$.tool.register({ name, description, inputSchema })` is `mcp__<plugin>__<name>` to Claude, and the mod answers it in `on('tool.call', { tool: 'mcp__<plugin>__<name>' }, async ($, e) => ({ result }))`, without `next`: the one `tool.call` answer allowed. The probe leaves out the names it saw the mod register and prints a `review:` line for each it answers; every other `mcp__<plugin>__*` name is fired like any tool, so a mod named like an MCP server cannot answer that server's tools. Answering in place of `next` skips the permission prompt and every `PreToolUse` hook, so such a tool only reads. Register it in `goLive`, never before the surfaces check: a tool added headless joins every `claude -p` and cron session.
- A gating hook must have a `.catch`. In 2.1.294-2.1.295 validate counts these as gating even when they only observe: `tool.call`, `prompt.submit`, `prompt.mention`, `agent.spawn`, `agent.offer`, `command.run` when it calls `next`, `config.set`, `ui.close`, `session.compact`, `session.receive`, `session.send`, every `classic.*` event, and every mods-API-call event (`fs.read`, ...). Not gating: `session.start`, `session.end`, `turn.start`, `turn.step`, `turn.complete`, `tool.describe`, `prompt.context`, `ui.press`, `ui.render`, and a `command.run` hook without `next`, which the probe fails for the mod's own command (see [Where it runs](#where-it-runs)). For an observer the handler is the explicit fail-open:

  ```ts
  on('tool.call', async ($, e, next) => {
    const result = await next(e)
    // observe e and result here; keep it cheap
    return result
  }).catch(($, e, next) => next(e))
  ```

  `--strict` lets a missing `.catch` pass; `mods/check.sh` does not.
- A hook that throws or times out before `next` is skipped and the chain goes on: mods fail open. Report your own failures with `$.ui.log(text, { to: 'debug' })`; the engine logs every skipped hook as `<plugin>: <event> hook skipped: <reason>` in the debug log.
- MCP tool names: the engine declares the tools of the servers the last live session had connected (`.claude-plugin/types/claude-code-mcp/`), so in your editor `e.tool === 'mcp__notes__search'` fails with TS2367, and a matcher `{ tool: 'mcp__notes__search' }` with TS2322, when another server was connected and that one was not. `check.sh` type-checks against no MCP server, where every `mcp__*` name is accepted. Compare `(e.tool as string) === 'mcp__notes__search'` and both agree.
- `tool.call` runs on every tool call of every session the mod loads in, subagents included (`e.agentId` set), `claude -p` and cron sessions too: an observer that does more than read (a status line, a toast, a fetch) does it only once the mod is live (`if (isLive)`). Keep the hook's own work under a few milliseconds: no `$.process`, `$.http`, `$.fs` or `$.model` before `next`. Batch slow work behind a debounced `$.clock.after` (code-modernization's `scheduleRefresh`). The debug log times every dispatch, `hooks module <name>@inline <event> settled in <ms>`, worker hop and `next` included (so for `tool.call` the tool's own run too); to time your part, take `await $.clock.now()` before `next` and log the difference with `{ to: 'debug' }` while developing. In a live terminal session the template's render, command and start hooks settled in 2-63 ms. The result carries `isReadOnly: true` when Claude Code's own check found the call only reads (`cat`, `grep`, `sed -n`).
- A regular expression a `tool.call` hook tests before `next` stays linear in the command's length. Every Bash call of every session pays for it, and a hooks worker that misses its 5 s heartbeat is respawned, which runs every mod's `session.start` again and drops their module state: a `\S*` before a literal took 13 s on a 120 KB command and wedged the worker. Stop every run at a separator (`[^\s;&|()]*`, `[ \t]+`), and test a pattern the person supplies on a bounded slice of the command (omnigraph reads the first and the last 16 KB).

## Where it runs

- In `session.start`, return `next(e)` at once when `(await $.session.surfaces()).length === 0` (`claude -p`, cron jobs, the SDK): register nothing, start no timer, draw nothing. A module variable set there gates every other hook that does more than read: `turn.start`, `turn.complete`, `session.end`, and the `tool.call`, `prompt.submit` and `classic.*` observers too. With nothing attached a mod may only read: `$.session` reads (`cwd`, `root`, `id`, `model`, `repo`, `surface`, `surfaces`, `turns`, `usage`, `version`, `messages`), `$.store.*`, `$.state.*`, `$.env.get`, `$.fs.read` / `list` / `exists` / `stat` / `ancestors`, `$.clock.now` / `sleep`, `$.settings.read`, `$.tool.list`, `$.command.list`, `$.agent.list`, `$.config.list`, `$.prompt.read` / `compose`, `$.ui.log` / `invalidate` / `resolve` / `panes` / `selection` / `close`. Any other `$` call acts: a timer, a command or tool registered, a tool call, a turn aborted, a pane, status line, toast or notice, a process, a fetch, an MCP call, a prompt, a model call, a subagent, a message sent, a compaction, a `/config` change, a file or env write. `check.sh` holds you to it twice: its probe stubs every `$` call of this build and, with nothing attached, fires every event the mod hooks and records each call that acts; its headless load fails on the ones the engine logs.
- A session that starts with nothing attached can gain a surface later (the desktop app or a phone joins it): `session.start` does not fire again, `session.attach` does. Go live from both, through one top-level function that takes `$`, as the template's `goLive` does. `session.attach` is not gating; the terminal never raises it.
- After setting such a variable, call `$.ui.invalidate('ui.render')`: the band may have drawn before `session.start` finished, and a module variable redraws nothing.
- A background session (`claude --bg`, the agent view) does not start with nothing attached, whatever the docs say: it raises `session.start` on a terminal surface, interactive, with nobody looking, so the mod goes live there at once, and its timers and refreshes run in every such session.
- `$.session.root()` moves on `/cd`, but Claude Code raises `classic.CwdChanged` only for a shell `cd`. A mod that follows the root reads it after `next` in `command.run` `{ command: 'cd' }` (a gate: `.catch`) and `tool.call` `{ tool: ['EnterWorktree', 'ExitWorktree'] }`, and in `turn.start` as a catch-all, as omnigraph's `followRoot` hooks do.
- Register commands last in `session.start`, inside `try`/`catch`: `$.command.register` throws for a taken name and a hook that throws is skipped whole. Names: letters, digits, `_`, `-`, up to 64, not a built-in command. A personal skill's folder name is its command too, so a `~/.claude/skills/<name>/` (`install.sh` links each `skills-amazon/<name>/` with a `SKILL.md` there) keeps `/<name>` and the mod's register throws.
- Answer the mod's own command only where it registered it: set a flag once `$.command.register` resolves, and pass the event on otherwise.

  ```ts
  on('command.run', { command: '<name>' }, async ($, e, next) => {
    if (!isLive || !isRegistered) {
      return next(e)
    }
    // open the pane, answer
  }).catch(($, e, next) => next(e))
  ```

  Without the check the hook takes `/<name>` from the person's skill or command of that name (the one register was refused for), and acts in `claude -p "/<name>"`, where nothing draws. With `next` in it the hook is gating, so it needs the `.catch`. The probe fires the mod's own command before `session.start` and with nothing attached, and fails a hook that does not pass it on there.
- `immediate: true` on a command lets it run mid-turn (needed for a command that only opens a pane).
- A render hook reads `e.surface` per draw (`terminal`, `desktop`, `vscode`, `mobile`) and takes elements from `$.ui.resolve(e)`. `Raster` and `Image` draw on the terminal only, `Client` on the terminal and the desktop: decide on `e.surface` and draw `Text` elsewhere. Not on `'Raster' in table`, which is always true (a resolved table holds every element name on every surface); cast the table for the types instead, `(el as Elements['terminal']).Raster(...)`.

## Side effects

- `$.mcp.call(server, tool, args)` runs with no permission prompt: the mod's call is the grant. Call read-only tools only. Never a tool that writes, sends, posts, creates or deletes, whatever the server.
- `$.process.run`, `$.http.fetch`, `$.fs.write`, `$.env.set` run as the user with no prompt and outside the sandbox. Use them only for the mod's own reads, or for a write the person asked for with a button or a command. Write paths under the mod's own data, never the repo or settings.
- `$.prompt.submit` starts a turn and `$.model.complete` / `fork` / `classify` spend the user's usage: only from a person's press or command.
- Mods are not sandboxed and see every prompt and tool call: no secrets, hosts or account details in a committed mod. Private specifics belong in a gitignored folder, read at run time.

## Drawing

- A pane: `$.ui.open({ id, title, focus: true, closeOnEscape: true })` from a command or a press (placed at any width); opened unasked (a timer, `session.start`) it waits below 144 terminal columns, 110 once the person opened it before. Check `opened.isPlaced` and say `opened.reason`.
- `focus`, `closeOnEscape`, `holdToasts` accept only `true`; to leave one off, omit it (`false` throws and the hook is skipped).
- Draw the pane in `on('ui.render', { component: 'Pane', requestId: '<id>' }, ...)`. Fit to `e.props.bodyColumns`; height is `e.props.scroll.bodyRows` when `e.props.placement` is `'dock'`.
- The band: `on('ui.render', { component: 'AbovePrompt' }, ...)` returns `next(e)` to show nothing; otherwise put `await next(e)` inside your `Box` so the mods after yours keep drawing. Yield to `e.props.hasSurvey`.
- One line under the prompt: `$.ui.status(text)` (`undefined` clears it). A notice: `$.ui.toast(text)`. A transcript line Claude does not read: `$.ui.log(text)`. `$.ui.status`, `toast`, `log` and `invalidate` return nothing: never `.catch` or `await` them. `$.ui.open`, `close`, `blit`, `$.command.register`, `$.store.*`, `$.fs.*` return promises: fire-and-forget calls get `.catch(() => undefined)`.
- Give every control a `key` (tests press by key). `hotkey` is one digit or lowercase letter; a digit hotkey in the band also fires from an empty prompt, so prefer letters or none in the band.
- A `Client`'s props are capped at 100,000 serialized characters; past that the engine draws its placeholder in place of the whole pane. Cut free text in the props and check their size before mounting (omnigraph's `propsFit` in `hooks/ego.ts`).
- A tree that does not validate is replaced by the engine's own drawing; the debug log says `ui.render (<Component>): a hook returned a tree that does not validate` with the reason. `$.ui.mount` in a test catches the same.

## State

| Keep it in | Lasts until | For |
| :- | :- | :- |
| a module variable | the next reload (every save in dev) | caches you can lose |
| `$.state` (`atom` / `read` / `update`) | the session ends, or `/clear`, `/resume`, `/branch` | what a drawing reads; a write redraws its readers |
| `$.store` | the mod deletes it | preferences, history across sessions |

- A `ui.render` hook reads state and never writes it: write from `onPress`, `onSubmit` or another event's hook.
- `$.store` is one JSON file per mod (4 MiB at most), shared by every session on the machine that loads the mod: one key per item, re-read right before a write.
- A value copied from `$.store` into `$.state` at `session.start` must be copied again in `on('classic.SessionStart', { source: ['clear', 'resume', 'fork'] }, ...)`, which is a gating hook: give it `.catch(($, e, next) => next(e))`.

## Tests

Every mod has at least one `tests/*.test.ts`; `claude plugin test` runs them with no session, network or model.

- Register every stub before the first call on `$`. Seat them in one `worldOf(on)` helper, as `mods/_template/tests/fixtures.ts` does, and change the world's fields in a test, not its stubs.
- `session.start` does not run by itself: fire `$.session.start({ surface: 'terminal', isInteractive: true, cwd: '/work' })`, with stubs for `session.surfaces` (`{ value: ['terminal'] }`, `[]` for a headless run), `command.register` (`{ value: { command: e.name } }`), and `session.start` itself (`{ cwd: e.cwd }`). A client joining later: `$.session.attach({ surface: 'desktop', clientId: 'desktop:default' })` with a `session.attach` stub returning `{ clientId: e.clientId }`.
- A mods-API stub returns `{ value }` (`{ deny }` makes the call reject); an engine-event stub returns that event's result (`tool.call` → `{ result }`, `turn.complete` → `{ text: '' }`). A hook that returns `next(e)` from `ui.render` needs `on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))`.
- The kit answers `$.state` and `$.ui.invalidate` itself; use `mock.clock(on)` for `$.clock`, `mock.store(on, {...})` for a store you do not inspect.
- Typed inputs: `$.command.run` needs `origin: { kind: 'composer' }` and `presentation: { isFullscreen, columns }`; `$.turn.complete` needs `reason`. `userConfig` values per test: `test(name, { options: { band: false } }, body)`.
- Draw with `$.ui.mount({ plugin, surface, component, requestId, viewport, props })` on every surface the mod targets, then `find`, `press`, `input`, `unmount`.

## Limits (2.1.294-2.1.295)

| Limit | Value |
| :- | :- |
| A hook's own time per event (not `next` or API calls, except `$.clock.sleep`) | 10 s; 50 ms for `prompt.edit` |
| A `.catch` handler | 1 s |
| `$.fs.read` / `$.fs.write` | 4 MiB per file |
| Text in one tree | the first 100,000 characters are drawn |
| A `Client`'s props | 100,000 characters serialized; past that the whole pane is the engine's placeholder |
| The hooks worker's heartbeat | 5 s; a worker that misses it is respawned, every mod's `session.start` runs again and module state is lost |
| `Raster` | `columns` up to 512, `rows` up to 256, terminal only |
| `$.store` | 4 MiB of JSON per mod, shared by every session |
| `$.process.run` | 30 s by default, 10 min at most |
| Redraws | 10 a second (30 in the terminal for the visible pane, band, hint line); extra calls coalesce |
| A pane opened unasked | placed from 144 columns, 110 once opened by the person |
| One `claude plugin test` test | 5 s unless `timeoutMs` |
| One `claude` run in `mods/check.sh` (all of a mod's tests, the probe, the load) | 120 s, `MODS_TIMEOUT` |
| One probe test | (`MODS_TIMEOUT` − 15 s) / the number of probe tests (10 for one boolean option), at least 3 s |

## Off switches

- One installed mod: `/plugin` → Installed → disable, `claude plugin disable <mod>@claude-dotfiles`, or `"enabledPlugins": { "<mod>@claude-dotfiles": false }` in settings.
- A dev copy: drop `--plugin-dir` (and `CLAUDE_CODE_PLUGIN_DIRS`), or `"enabledPlugins": { "<mod>@inline": false }`.
- One option: `userConfig` values live in `"pluginConfigs": { "<mod>@claude-dotfiles": { "options": { ... } } }` (`<mod>@inline` for a dev copy) in user settings, or `claude plugin configure <mod>@claude-dotfiles`. Project settings are not read for them; `<mod>@inline` values passed with `--settings` apply even with `--setting-sources project`.
- One session, everything: `claude --safe-mode`. It also turns off CLAUDE.md, skills, hooks and MCP servers, so the guard hooks and shepherd stop too.
- Not `"disableAllHooks": true`: it stops every installed mod and every settings hook as well (guards, shepherd, the stop gate), the custom status line, and `/goal`.
- A mod that crashes the hooks worker is unloaded by the engine; `/reload-plugins` loads it again after the fix.

## Read before building a pane

- code-modernization (`~/.claude/plugins/marketplaces/claude-plugins-official/plugins/code-modernization/`): `hooks/register.ts` (binding `$`, `.catch(() => undefined)` on fire-and-forget calls, debounced refresh), `hooks/fleet/fleet.ts` (per-subagent activity), `hooks/map/raster.ts` and `treemap.ts` (a `Raster` map repainted with `$.ui.blit`), `tests/fixtures/world.ts` (a full stub world).
- The built-in mods' source in the Claude Code repository (`mods/diff`, `agents-md`, `sec-default`, `telemetry`): `diff` for a scrolling pane and buttons bound to keybinding actions.
