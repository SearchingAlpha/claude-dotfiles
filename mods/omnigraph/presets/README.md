# omnigraph presets

A preset is the mod's options for one producer's build, kept in the repo so they are tested: `preset.py block` prints them as the `pluginConfigs` block for `~/.claude/settings.json`, the mod's tests run their pattern and a build of their shape through the hooks, and `preset.py check` runs a real build through them. What belongs to one person (the patch owner) is not in the preset: it comes from a gitignored values file.

| Preset | For | How-to |
| :- | :- | :- |
| `sca` | The SupplyChainAgents ontology build (`ontology/build`) and its deploy scripts | [sca.md](sca.md) |

## Format

`<name>.json`, with exactly these keys:

| Key | What it holds |
| :- | :- |
| `about` | One line: whose build, where |
| `options` | Every `userConfig` option of the mod with its value (so a merge leaves no old value behind), each of the type `plugin.json` gives it |
| `private` | The options a values file fills; each is empty (`""`) in `options` |
| `samples` | `ops`: `[command, op name]` pairs the pattern must name; `notOps`: commands it must not (reads, edits, tests of the producer's scripts) |

`<name>.private.example.json` shows the values file's shape, every value a `<…>` placeholder:

| Key | What it holds |
| :- | :- |
| `options` | The `private` options' values (a `<…>` one is refused) |
| `checkStore` | Optional: the absolute path of the real build `preset.py check` reads |
| `neverCommit` | Optional: strings no committed file of the mod may hold, such as a work email |

The values file is `skills-amazon/omnigraph-<name>/private.json` under the repo root (`--values FILE` for another). `skills-amazon/` is gitignored, so it stays on the machine; copy it into the main checkout's `skills-amazon/` after a merge.

## Commands

```bash
python3 mods/omnigraph/presets/preset.py list                            # the presets
python3 mods/omnigraph/presets/preset.py block sca                       # the pluginConfigs block on stdout, owner from the values file
python3 mods/omnigraph/presets/preset.py block sca --plugin omnigraph@inline   # for a --plugin-dir copy (moddev.sh)
python3 mods/omnigraph/presets/preset.py check sca [--store DIR] [--keep]      # the real build through the hooks
```

`block` writes nothing; `settings/merge.py` previews and applies the block ([sca.md](sca.md#configure) has the lines). Without a values file it says so on stderr and leaves the private options empty (for `owner`: patches get no owner line). Exit codes: 0 fine, 1 the check failed, 2 bad input (an unknown preset or key, a placeholder left in `options`, a missing build or `claude`).

`check` reads the build's three files (each at most 4 MiB, as the mod reads them) and writes a copy of the mod into a new temporary folder (`--work DIR` for another parent): the plugin, hooks and contract, the stub world from `tests/`, the build embedded as `tests/real-store.ts`, and `real-store.check.ts` as its one test. `claude plugin test` runs it in the minimal environment `mods/check.sh` gives it (no network, no telemetry), and the copy is removed unless `--keep`. The build is only read. What the check holds a build to is in [sca.md](sca.md#check-it-against-the-real-build).

## What stays out of the repo

A committed file of the mod holds no confidential value: no account id, internal host or URL, database, schema or table name, business number, ontology record, colleague's name or alias, work email, or ticket id. Tool, script and project names, paths and the options' structure are fine. `test_presets.py` checks it two ways:

- always, for shapes: a 12-digit account id, an email address, an `*.a2z.com` / `*.aws.dev` / `*.amazon.*` host, a ticket id;
- when the values file is there, for its own values (`options`, `neverCommit`), and, when its `checkStore` names a build, that build's version and every one of its ids (open conflicts too), each where it stands alone. Without the file it skips this with a message saying so.

The tests' build (`tests/presets.ts`, written by `test_presets.py --regen`) is made up: SCA's shape, none of its records.

## Files

| Path | What it holds |
| :- | :- |
| `sca.json`, `sca.md`, `sca.private.example.json` | The SupplyChainAgents preset, its how-to and its values file's shape |
| `preset.py` | `list`, `block`, `check` |
| `real-store.check.ts` | The test `check` runs in its copy of the mod (not one of the mod's own tests: it needs a real build) |
| `test_presets.py` | The presets against `plugin.json`, the values file's rules, `block`, `check` on a made-up build and on a broken one, the boundary checks, and that `tests/presets.ts` matches the presets |

## Tests

```bash
python3 -m unittest discover -s mods/omnigraph/presets -p 'test_*.py'   # 15 tests; check's need claude on PATH
python3 mods/omnigraph/presets/test_presets.py --regen                   # after changing a preset
mods/check.sh omnigraph                                                  # runs tests/presets.test.ts with the rest
```
