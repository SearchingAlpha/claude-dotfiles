# The SupplyChainAgents preset

`sca.json` points the pane at the SupplyChainAgents (SCA) ontology build: `scripts/ontology/compile.py` writes `ontology/build/` (`manifest.json`, `nodes.jsonl`, `edges.jsonl`) in the SCA repo and in each of its worktrees, and that is already the store format, so the preset is options only. The ontology itself is never in this repo: the tests use a made-up build of the same shape, and `preset.py check sca` reads the real one where it lies.

Contents: [What the options map](#what-the-options-map) · [Two roots](#two-roots) · [Configure](#configure) · [Check it against the real build](#check-it-against-the-real-build) · [A demo on the real graph](#a-demo-on-the-real-graph) · [Curating from the Queue](#curating-from-the-queue) · [Not in v1](#not-in-v1)

## What the options map

| Option | Value | Why |
| :- | :- | :- |
| `store` | `ontology/build` | Where `compile.py` writes the build, under each checkout's root |
| `versionKey` | `ov` | The manifest's version hash |
| `opsFile` | empty | The build writes no ops log: latency is the tool call's wall time, `✗` a non-zero exit |
| `storeFlag` | empty | The scripts take no store flag |
| `storeLabel` | `ontology` | The header's and the band's store name |
| `cmdRegex` | running `scripts/ontology/(extract,validate,compile,probe,onto,questions).py` or `deploy/ontology(_load).py <stage> <action>` | Ops are named `compile.py`, `ontology.py beta query`, `ontology_load.py beta`. The script must be the command (at a line start, or after `;`, `&&`, `\|`, `(`), run directly or by `python3` and its flags (`-u`, `-X dev`), past shell keywords (`for … do`, `then`, `{`, `!`), wrappers (`timeout 600`, `env`, `nice`, `sudo -u x`, `xargs`), `VAR=value` and `bash -c '`. It is the mod's default with the scripts as the tail, so every run stops at a separator |
| `nameKeys` | `name` | Every record has `props.name` |
| `sourceKey` | `source` | `props.source` is a list of `path:line` under the repo root; Browse shows those lines |
| `conflictsKey` | `open_conflicts` | The manifest lists open conflict ids; the Queue shows them first |
| `queueStatus` / `acceptStatus` | `proposed` / `curated` | The curation overlay is three lines: id, `status: curated`, `owner` (`scripts/ontology/onto.py`) |
| `owner` | from the values file | The person a patch names; empty in `sca.json` |

`samples` in `sca.json` holds the 20 commands the pattern must name (with the op name each gets) and the 22 that only touch the scripts and must not be ops: `cat`, `sed -n 1,80p`, `grep -n`, `head -50`, `wc -l`, `vim`, `black`, `ruff check`, `mypy`, `cp`, `chmod +x`, `touch`, `git diff`, `git add`, `git log`, `git checkout --`, `git mv`, `python3 -m py_compile`, `python3 -m pytest …`, `pytest …`, `cat ontology/build/manifest.json`. `tests/presets.test.ts` runs them, and eleven 120 KB commands built to make a pattern backtrack (separators, paths, `VAR=value` words, wrapper and interpreter flags, keywords, quotes, a minified heredoc) take the pattern about 9 ms together. A command Claude Code itself finds read-only is never an op either.

## Two roots

- **SupplyChainAgents and its worktrees.** `ontology/build` is there, so `extract`/`validate`/`compile`/`probe` ops get a diff (`+n +e`, `~Nn` for rewritten records) and the graph follows them. `compile.py` over an unchanged extraction is `+0n +0e` and only stats the build. After `/cd` the pane reads the new root's build and feed at once, so its first `compile.py` has a diff (checked live on the demo store); after `EnterWorktree` or `ExitWorktree` too, so far only through the test kit.
- **SupplyChainAgentsSlack/src/SupplyChainAgents-Slack.** `deploy/ontology.py` (`up|status|start|stop|query|notebook`) and `deploy/ontology_load.py` live there, with no `ontology/build`. Their ops are feed-only (`·n ·e`), and the band shows once the first one is in the feed.

A long `compile.py` that Claude sends to the background shows as `compile.py ·n ·e bg …`; the next op (or `/omni reload`) shows what it changed.

## Configure

You apply this, never an agent. From the claude-dotfiles checkout:

1. After this branch merges, the marketplace needs an `omnigraph` entry (`"source": "./mods/omnigraph"`); then `claude plugin install omnigraph@claude-dotfiles`.
2. The values file: `cp mods/omnigraph/presets/sca.private.example.json skills-amazon/omnigraph-sca/private.json` (`skills-amazon/` is gitignored), then set `options.owner` to your alias. `checkStore` (the build `preset.py check` reads) and `neverCommit` (strings the boundary test keeps out of this mod) are optional, and a `<…>` value left in them counts as unset. One left in `options.owner` is refused, so a copied example never puts a placeholder in your settings.
3. Print the settings block and merge it into `~/.claude/settings.json` (user settings only: project settings are not read for plugin options):

   ```bash
   python3 mods/omnigraph/presets/preset.py block sca > skills-amazon/omnigraph-sca/settings.json
   python3 settings/merge.py skills-amazon/omnigraph-sca/settings.json            # the diff, nothing written
   python3 settings/merge.py --apply skills-amazon/omnigraph-sca/settings.json    # writes it, after a backup
   ```

   `merge.py` keeps a value your settings already have. To take a newer preset over an older block (the `cmdRegex` changed on 2026-10-09, when its runs were made to stop at separators: the earlier one could stall every hook for seconds on a long command), add `--override-key '/pluginConfigs/omnigraph@claude-dotfiles/options'`.

   `claude plugin configure omnigraph@claude-dotfiles --values-stdin` is no substitute: it drops empty values (checked with 2.1.295), so `opsFile` and `storeFlag` would keep their defaults.
4. To try it from the worktree first: `preset.py block sca --plugin omnigraph@inline`, merged the same way, then `<claude-dotfiles>/harnesses/tools/moddev.sh omnigraph` in the SCA checkout.

## Check it against the real build

```bash
python3 mods/omnigraph/presets/preset.py check sca      # the build checkStore names, or --store <dir>
```

It reads the build (only reads it), embeds it in a copy of the mod under a new temporary folder with `real-store.check.ts` as its test, runs `claude plugin test` there with no network, and removes the copy. It fails when a line does not parse, when the record counts, the version, the proposed count or the open conflicts differ from the manifest's, when no record has a name, when two ids would share a patch file name, when a tab does not draw on a surface, or when the first sample op does not give `+0n +0e` over the unchanged build and `~1n` over a rebuild that curated one record. It prints what it found (`facts:` and `hooks:` lines) to your terminal. `python3 -m unittest discover -s mods/omnigraph/presets` runs it too when the values file names a `checkStore`.

On 2026-10-09, against the build of 2026-10-05: every line parsed, the counts, the version, the Queue and the open conflicts matched the manifest, every tab drew on the terminal, desktop, VS Code and mobile surfaces, an unchanged `compile.py` took 8 ms and read no build file, a rebuild that rewrote one record 19 ms (`~1n`), an export wrote one record, no two ids share a patch file name and none holds a control character.

## A demo on the real graph

In the SCA repo, in a fullscreen terminal of 144+ columns:

1. `/omni`: the pane draws around the busiest node, the band reads `omnigraph · ontology · <branch> · <N>n <E>e · 0 ops`.
2. Ask Claude to re-run the pipeline: `python3 scripts/ontology/extract.py`, then `validate.py`, then `compile.py`. Each lands in the feed with its latency and diff. When a compile adds or rewrites five records or fewer, the graph moves to the first of them in file order (new ones before rewritten ones), drawn around its busiest neighbour. A bigger change (a re-extraction that rewrites many records) keeps the view.
3. Press `2` and pick `Metric` (or `Rule`, `Node`, `Skill`) in the type picker to browse; the detail shows the record's source lines.

## Curating from the Queue

The Queue lists the open conflicts first, then the proposed records by label. `export` writes one patch per record to `~/.claude/omnigraph/patches/ontology/<id-slug>-<hash>.yaml` (SCA ids hold `:` and `/`, so each file name gets a short hash of the id):

```yaml
# omnigraph patch for ontology <ov>, exported <time>
# was: proposed (Rule) "<name>"
- id: "rule:<skill>/<rule>"
  status: "curated"
  owner: "<owner>"
```

The mod never writes the repo. To apply: paste the record into the curated layer (any `ontology/**/*.yaml` outside `extracted/` and `build/`, not `schema.yaml`), settle any conflict that names it first (decisions go in `ontology/conflicts.yaml`, in `curation/session-1.md`'s shape), then run `validate.py` (it refuses a curated record that sits in an open conflict) and `compile.py`. The compile shows in the pane as an op that changed that node (`~1n`).

## Not in v1

- The Neptune notebooks' `%%oc` views (NTI one hop out, WBR skill to dataset, network hierarchy, CONFLICTS_WITH) as saved Graph-tab views, and a hotkey for `deploy/ontology.py beta notebook url`.
- An Accept button: it needs `scripts/ontology/curate.py` (the ontology-mcp-and-curation candidate), which writes the overlay and runs `validate.py`; the press would be the approval.
- Edit/Write curation ops: a `tool.call {tool: ['Edit', 'Write']}` observer matched by a `curationRegex` on the curated YAML. Today an edit to the curated YAML shows in the pane only once the next `compile.py` rebuilds.
- The `turn.complete` summary of the turn's graph ops: returning text there changes the event's answer, which the mods gate flags unless the README declares it.
- MCP ops: once the ontology MCP server exists, set `mcpPrefix` to its `mcp__<server>__` prefix; its propose/related tools then land in the feed with a diff of the build.
