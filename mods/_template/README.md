# mod-template

The smallest mod worth copying. It is observe-only and draws two things:

- `/mod-template` opens a pane: this session's main-loop turns and how long the last one took, a button that hides or shows the band, and one that closes the pane (Esc closes it too).
- A one-line band above the prompt, `mod-template · 2 turns · last 12s`, with a `hide` button. The `band` option turns it off.

Every hook passes its event on, none can refuse anything, and in a session where nothing draws (`claude -p`, cron, the SDK) it registers nothing and draws nothing until a client attaches. `mods/CLAUDE.md` has the rules it follows.

| File | What it holds |
| :- | :- |
| `.claude-plugin/plugin.json` | Name, version, the `types` contract and the `band` option (`userConfig`) |
| `hooks/hooks.json` | `"modules": ["./register.ts"]`, what makes the plugin a mod |
| `hooks/register.ts` | The hooks. `session.start` (with the surfaces check) and `session.attach` (a client joining later) go live through `goLive`, which registers `/mod-template`; then `turn.complete`, `command.run` (answers `/mod-template` only once the mod registered it in a live session, and passes it on otherwise), and `ui.render` for the pane and the band |
| `types/index.d.ts` | The `$.state` contract: `turns`, `lastTurnMs`, `isBandHidden` |
| `tsconfig.json` | Extends the generated `.claude-plugin/types/tsconfig.json` (laid by a load, gitignored) |
| `tests/` | `fixtures.ts` (the stub world) and `template.test.ts` (17 tests) |

## Copy it

1. `cp -r mods/_template mods/<name> && rm -rf mods/<name>/.claude-plugin/types`, `<name>` being the plugin name (letters, digits, `-`; not one that reads as Anthropic's, such as `claude-...` or `...-for-claude`: `mods/CLAUDE.md` has the rule). `mods/check.sh` lays the types again.
2. Replace `mod-template` with `<name>` everywhere: the manifest `name` and `description`, the `PluginState` key in `types/index.d.ts`, each `atom({ plugin })`, the command, the pane id and title, the band text, and `PLUGIN` and the expected strings in the tests. One pass does it:

   ```bash
   grep -rlZ mod-template mods/<name> | xargs -0 sed -i -- 's/mod-template/<name>/g'
   ```

   (`sed -i ''` for `sed -i` on macOS.) The NUL-separated list and the `--` keep a file name from reading as an option; a worktree-isolated session (a shepherd track) refuses the plain `grep -rl | xargs sed` form. Then rewrite this README for the new mod.
3. Delete what you do not need (the band, the option, the turn counter) and the tests for it; write tests for what you add. Keep the surfaces check in `session.start`, the live-and-registered check in `command.run`, and their tests: `mods/check.sh` fails a mod that acts with nothing attached, or that answers its own command where it has not registered it.
4. `mods/check.sh <name>` until it passes, then `harnesses/tools/moddev.sh <name>` to try it live.
5. When it is ready to install, add its marketplace entry (see `mods/README.md`).

## Try the template itself

```bash
mods/check.sh _template                       # validate, 17 tests, probe, headless load, tsc
harnesses/tools/moddev.sh _template --smoke   # headless load, no model turn
harnesses/tools/moddev.sh _template           # live: the band shows; type /mod-template
```

Tested with Claude Code 2.1.294 and 2.1.295.
