// The adapter presets (presets/*.json, read through tests/presets.ts, which
// presets/test_presets.py writes): each preset's options compile, its pattern
// names its sample ops and none of its non-ops, in one pass over a long
// command; and the hooks read a synthetic build in the SupplyChainAgents shape
// with the sca preset. A real build is checked by `presets/preset.py check`.
import { describe, expect, test } from 'claude-code/testing'
import type { OmniOp } from 'claude-code'

import { adapterOf, opNameOf, slugOf } from '../hooks/graphstore'
import { BAND, PANE, ROOT, SESSION, layStore, worldOf } from './fixtures'
import type { StoreTexts, World } from './fixtures'
import { PRESETS, SCA_BUILD, SCA_CONFLICT } from './presets'

const K = 120_000
const rep = (unit: string): string => unit.repeat(Math.ceil(K / unit.length)).slice(0, K)
/** 120 KB commands that stress each run of a pattern: separators, paths, words, wrappers, flags, keywords, quotes. */
const LONG: Record<string, string> = {
  semicolons: rep(';a'),
  parens: rep('(x'),
  'pipes and slashes': rep('|/'),
  paths: rep(';a/b/c/'),
  'a=b words': `${rep('a=b ')}x`,
  'sudo flags': rep(';sudo -a b '),
  'python flags': `python3 ${rep('-X a ')}x`,
  keywords: rep('; do then { ! '),
  'bash -c quotes': rep("; bash -c '"),
  'blank lines': rep('\n '),
  heredoc: `cat > app.min.css <<'EOF'\n${rep('.c0{color:red;margin:0}')}\nEOF`,
}

for (const [name, preset] of Object.entries(PRESETS)) {
  describe(`the ${name} preset`, () => {
    const a = adapterOf(preset.options)

    test('its options compile, and the ones a values file fills are empty', () => {
      expect(a.problem).toBeNull()
      expect(a.cmdRegex).not.toBeNull()
      for (const key of preset.private) {
        expect([key, preset.options[key]]).toEqual([key, ''])
      }
    })

    test('its pattern names each sample op, and none of the commands that only touch the scripts', () => {
      for (const [command, want] of preset.samples.ops) {
        expect([command, opNameOf(command, a.cmdRegex)]).toEqual([command, want])
      }
      for (const command of preset.samples.notOps) {
        expect([command, opNameOf(command, a.cmdRegex)]).toEqual([command, null])
      }
    })

    test('a 120 KB command costs its pattern one pass', () => {
      const slow: string[] = []

      for (const [shape, command] of Object.entries(LONG)) {
        const t = Date.now()
        opNameOf(command, a.cmdRegex)
        if (Date.now() - t >= 50) {
          slow.push(shape)
        }
      }
      expect(slow).toEqual([])
    })
  })
}

describe('the sca preset through the hooks, on a synthetic build in its shape', () => {
  const OWNED = { ...PRESETS.sca.options, owner: 'curator' }
  const BUILD_DIR = `${ROOT}/ontology/build`
  const COMPILE = 'python3 scripts/ontology/compile.py'
  const RULE = 'rule:weekly-report/fill-rate-drop'
  const feedOf = (world: World): OmniOp[] => (world.store.get(`feed:${ROOT}`) as OmniOp[] | undefined) ?? []

  /** The build after a curation of `id` and a rebuild: that record's status is curated. */
  const curated = (id: string): StoreTexts => ({
    ...SCA_BUILD,
    nodes: SCA_BUILD.nodes
      .split('\n')
      .map(line => (line.startsWith(`{"id": ${JSON.stringify(id)},`) ? line.replace('"status": "proposed"', '"status": "curated"') : line))
      .join('\n'),
  })

  test('the band and every tab on every surface; an unchanged compile only stats the build; a rewritten record is ~1n and the graph goes there', { options: OWNED }, async ($, on) => {
    const world = worldOf(on, ['terminal'], null)

    layStore(world, SCA_BUILD, undefined, BUILD_DIR)
    await $.session.start(SESSION)
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
    expect(await band.find({ type: 'Text', text: /omnigraph · ontology · main · 10n 12e · 0 ops/ })).toBeDefined()
    for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
      const pane = await $.ui.mount({ ...PANE, surface })
      for (const key of ['tab-graph', 'tab-browse', 'tab-feed', 'tab-queue']) {
        await pane.press({ key })
        expect([surface, key, (await pane.find({ type: 'Text', text: /omnigraph · ontology · main/ })) !== undefined]).toEqual([surface, key, true])
      }
      await pane.unmount()
    }

    world.reads = []
    await $.tool.call({ tool: 'Bash', command: COMPILE })
    expect(world.reads.filter(path => path.startsWith(BUILD_DIR))).toEqual([])
    world.onTool = () => layStore(world, curated(RULE), undefined, BUILD_DIR)
    await $.tool.call({ tool: 'Bash', command: `cd ${ROOT} && ${COMPILE}` })
    world.onTool = null
    await $.tool.call({ tool: 'Bash', command: 'sed -n 1,80p scripts/ontology/compile.py' })

    expect(feedOf(world).map(op => [op.op, op.dNodes, op.dEdges, op.changed ?? 0, op.focus])).toEqual([
      ['compile.py', 0, 0, 0, null],
      ['compile.py', 0, 0, 1, { center: 'dataset:shipments-daily', selected: RULE }],
    ])
  })

  test('the Queue: the open conflict first, then the proposed records; export writes a hashed file name, curated and the owner', { options: OWNED }, async ($, on) => {
    const world = worldOf(on, ['terminal'], null)

    layStore(world, SCA_BUILD, undefined, BUILD_DIR)
    await $.session.start(SESSION)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-queue' })
    expect(await pane.find({ type: 'Text', text: /1 open conflict: settle these before accepting/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: new RegExp(`! ${SCA_CONFLICT}`) })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /6 proposed · export writes one patch file each \(status: curated\)/ })).toBeDefined()

    await pane.press({ key: `x:${RULE}` })

    expect(slugOf(RULE)).toMatch(/^rule-weekly-report-fill-rate-drop-[0-9a-f]{8}$/)
    const path = `/home/tester/.claude/omnigraph/patches/ontology/${slugOf(RULE)}.yaml`
    expect([...world.written.keys()]).toEqual([path])
    const text = world.written.get(path) ?? ''
    expect(text).toMatch(/^# omnigraph patch for ontology 5ca1ab1e0001, exported /)
    expect(text).toContain(`# was: proposed (Rule) "Flag a fill-rate drop over two points"\n- id: "${RULE}"\n  status: "curated"\n  owner: "curator"\n`)
    expect(world.files.get(`${BUILD_DIR}/nodes.jsonl`)?.text).toBe(SCA_BUILD.nodes)
  })

  test('with no values file the owner is empty, and a patch has no owner line', { options: PRESETS.sca.options }, async ($, on) => {
    const world = worldOf(on, ['terminal'], null)

    layStore(world, SCA_BUILD, undefined, BUILD_DIR)
    await $.session.start(SESSION)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-queue' })
    await pane.press({ key: `x:${RULE}` })

    const [text] = [...world.written.values()]
    expect(text).toContain(`- id: "${RULE}"\n  status: "curated"\n`)
    expect(text).not.toContain('owner')
  })

  test('a compile sent to the background: bg with no diff; the next op carries what it changed', { options: OWNED }, async ($, on) => {
    const world = worldOf(on, ['terminal'], null)

    layStore(world, SCA_BUILD, undefined, BUILD_DIR)
    await $.session.start(SESSION)
    world.answer = { result: { stdout: '', stderr: '', interrupted: false, backgroundTaskId: 'b1' } }
    await $.tool.call({ tool: 'Bash', command: COMPILE, run_in_background: true })
    layStore(world, curated(RULE), undefined, BUILD_DIR)
    world.answer = { result: 'ok' }
    await world.clock.advance(5000)
    await $.tool.call({ tool: 'Bash', command: 'python3 scripts/ontology/validate.py' })

    expect(feedOf(world).map(op => [op.op, op.isBackground ?? false, op.dNodes, op.changed ?? 0])).toEqual([
      ['compile.py', true, null, 0],
      ['validate.py', false, 0, 1],
    ])
  })

  test('a root with no build, as in the deploy scripts\' repo: their ops are feed-only and bring the band', { options: OWNED }, async ($, on) => {
    const world = worldOf(on, ['terminal'], null)

    await $.session.start(SESSION)
    const before = await $.ui.mount({ ...BAND, surface: 'terminal' })
    expect(await before.find({ type: 'Text', text: /omnigraph/ })).toBeUndefined()
    await before.unmount()

    await $.tool.call({ tool: 'Bash', command: 'python3 deploy/ontology.py beta query --gremlin "g.V().count()"' })
    await $.tool.call({ tool: 'Bash', command: 'python3 deploy/ontology_load.py beta' })

    expect(feedOf(world).map(op => [op.op, op.dNodes, op.dEdges])).toEqual([
      ['ontology.py beta query', null, null],
      ['ontology_load.py beta', null, null],
    ])
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
    expect(await band.find({ type: 'Text', text: /omnigraph · .*last ontology_load\.py beta ✓/ })).toBeDefined()
  })
})
