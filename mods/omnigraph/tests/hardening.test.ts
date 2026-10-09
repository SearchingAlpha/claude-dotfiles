// The hooks under awkward conditions: a failure the ops log does not record,
// overlapping and background calls, a store the mod cannot place, hostile
// store data, a full $.store, a store past the size cap, a moving root.
import { describe, expect, test } from 'claude-code/testing'
import type { TestBody } from 'claude-code/testing'
import type { OmniOp } from 'claude-code'

import { ADD_PARAMS, AFTER_ADD, AFTER_CREATE, CREATE_PARAMS, SEED } from './demo-store'
import { BAND, PANE, ROOT, SESSION, STORE, layStore, opLine, runOmni, worldOf } from './fixtures'
import type { StoreTexts, World } from './fixtures'

const CREATE = `omnigraph mutate create_issue --query queries/queries.gq --store graphs/dev.omni --params '${CREATE_PARAMS}'`
const ADD = `omnigraph mutate add_issue_to_epic --query queries/queries.gq --store graphs/dev.omni --params '${ADD_PARAMS}'`
const QUERY = 'omnigraph query blocked --store graphs/dev.omni'
const FEED_KEY = `feed:${ROOT}`

function cliWrites(world: World, texts: StoreTexts, op: string, ok = true, ms = 652, error?: string): void {
  world.onTool = () => layStore(world, texts, opLine(world.clock.now(), op, ok, ms, error))
}

const storedFeed = (world: World): OmniOp[] => (world.store.get(FEED_KEY) as OmniOp[] | undefined) ?? []
const rows = (world: World) => storedFeed(world).map(op => [op.op, op.ok, op.dNodes, op.dEdges])

describe('which call an ops-log record belongs to', () => {
  test('a failed call that writes no record right after a logged one is that call, with ✗ and its error', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    cliWrites(world, AFTER_CREATE, 'create_issue')
    await $.tool.call({ tool: 'Bash', command: CREATE })
    await world.clock.advance(1200)
    // The CLI exits 2 on bad --params before it logs anything.
    world.onTool = null
    world.answer = { isError: true, result: 'Exit code 2', text: 'Error: Exit code 2\n--params is not JSON: Expecting value' }
    await $.tool.call({ tool: 'Bash', command: `omnigraph mutate link_blocks --params '{bad'` })

    expect(storedFeed(world).map(op => [op.op, op.ok, op.ms, op.error])).toEqual([
      ['mutate create_issue', true, 652, null],
      ['mutate link_blocks', false, 0, '--params is not JSON: Expecting value'],
    ])
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: /2 ops · \+1n \+0e · 1 ×/ })).toBeDefined()
  })

  test('a record the log already held never names a later call, even a successful one', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    cliWrites(world, AFTER_CREATE, 'create_issue')
    await $.tool.call({ tool: 'Bash', command: CREATE })
    world.onTool = null
    await $.tool.call({ tool: 'Bash', command: 'omnigraph mutate create_issue --params {}' })

    expect(storedFeed(world).map(op => [op.op, op.ms])).toEqual([
      ['mutate create_issue', 652],
      ['mutate create_issue', 0],
    ])
  })

  test('a producer that keeps its log at a fixed number of lines: each op still finds its own record', async ($, on) => {
    const world = worldOf(on)
    const log = `${STORE}/ops.jsonl`
    // Drops the oldest line as it appends one, as a log trimmed to its last five does.
    const append = (line: string) => {
      const lines = (world.files.get(log)?.text ?? '').split('\n').filter(Boolean)
      world.files.set(log, { text: `${[...lines.slice(1), line.trim()].join('\n')}\n`, mtimeMs: world.clock.now() + ++world.writes })
    }

    world.files.set(log, { text: Array.from({ length: 5 }, (_, i) => opLine(world.clock.now() - 100_000 + i, 'old_op')).join(''), mtimeMs: 1 })
    await $.session.start(SESSION)
    world.onTool = () => {
      layStore(world, AFTER_CREATE)
      append(opLine(world.clock.now(), 'create_issue', true, 652))
    }
    await $.tool.call({ tool: 'Bash', command: CREATE })
    await world.clock.advance(2000)
    world.onTool = () => append(opLine(world.clock.now(), 'create_issue', false, 7, 'exists already'))
    await $.tool.call({ tool: 'Bash', command: CREATE })

    expect(storedFeed(world).map(op => [op.op, op.ok, op.ms, op.error])).toEqual([
      ['mutate create_issue', true, 652, null],
      ['mutate create_issue', false, 7, 'exists already'],
    ])
  })

  test('a successful exit with a failed record is a failure: the producer\'s reason wins', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    cliWrites(world, SEED, 'close_issue', false, 9, 'iss-x is closed already')
    await $.tool.call({ tool: 'Bash', command: `omnigraph mutate close_issue --params '{"issue":"iss-x"}' || true` })

    expect(storedFeed(world).map(op => [op.op, op.ok, op.error])).toEqual([['mutate close_issue', false, 'iss-x is closed already']])
  })
})

describe('overlapping and background calls', () => {
  test('two graph calls that settle together: both kept, each with its own record', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    world.onTool = command => {
      if (command.includes('create_issue')) {
        layStore(world, AFTER_CREATE, opLine(world.clock.now(), 'create_issue', true, 600))
      } else {
        layStore(world, AFTER_ADD, opLine(world.clock.now(), 'add_issue_to_epic', true, 700))
      }
    }
    await Promise.all([$.tool.call({ tool: 'Bash', command: CREATE }), $.tool.call({ tool: 'Bash', command: ADD })])

    // The first op recorded sees both changes; the second still gets its own name and latency.
    expect(storedFeed(world).map(op => [op.op, op.ms])).toEqual([
      ['mutate create_issue', 600],
      ['mutate add_issue_to_epic', 700],
    ])
    const deltas = storedFeed(world).map(op => [op.dNodes, op.dEdges])
    expect(deltas).toEqual([
      [1, 1],
      [0, 0],
    ])
  })

  test('three parallel calls: three feed entries in $.store and in the pane', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await Promise.all(['a', 'b', 'c'].map(name => $.tool.call({ tool: 'Bash', command: `omnigraph query parallel-${name}` })))

    expect(storedFeed(world).map(op => op.op).sort()).toEqual(['query parallel-a', 'query parallel-b', 'query parallel-c'])
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-feed' })
    expect(await pane.find({ type: 'Text', text: /3 ops kept/ })).toBeDefined()
  })

  test('run_in_background: recorded as started with no diff; the next op carries its change', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    world.onTool = null
    world.answer = { result: { stdout: '', stderr: '', interrupted: false, backgroundTaskId: 'b1' } }
    world.touched = []
    await $.tool.call({ tool: 'Bash', command: CREATE, run_in_background: true })
    expect(world.touched).toEqual([])

    layStore(world, AFTER_CREATE, opLine(world.clock.now(), 'create_issue', true, 600))
    world.answer = { result: 'ok' }
    await world.clock.advance(5000)
    await $.tool.call({ tool: 'Bash', command: QUERY })

    expect(storedFeed(world).map(op => [op.op, op.isBackground ?? false, op.dNodes, op.ms])).toEqual([
      ['mutate create_issue', true, null, 0],
      ['query blocked', false, 1, 0],
    ])
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-feed' })
    expect(await pane.find({ type: 'Text', text: /▸ mutate create_issue +·n ·e +bg …/ })).toBeDefined()
  })

  test('a background call that was refused or failed to start is a failure, not a running op', async ($, on) => {
    const world = worldOf(on)
    const close = `omnigraph mutate close_issue --store graphs/dev.omni --params '{"issue":"iss-offline-queue"}'`

    await $.session.start(SESSION)
    world.answer = { deny: 'the user said no' }
    await $.tool.call({ tool: 'Bash', command: close, run_in_background: true })
    world.answer = { isError: true, result: 'Exit code 1', text: 'Error: could not start the background task' }
    await $.tool.call({ tool: 'Bash', command: close, run_in_background: true })

    expect(storedFeed(world).map(op => [op.ok, op.isBackground ?? false, op.error])).toEqual([
      [false, false, 'refused'],
      [false, false, 'Error: could not start the background task'],
    ])
    expect(world.toasts).toEqual(['1 new op, last mutate close_issue ✗ · /omni'])
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-feed' })
    expect(await pane.find({ type: 'Text', text: /bg …/ })).toBeUndefined()
  })

  test('a call that went to the background on its own (a timeout, Ctrl+B) is a background op too', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    world.answer = { result: { stdout: '', stderr: '', interrupted: false, backgroundTaskId: 'b2', backgroundedByUser: true } }
    await $.tool.call({ tool: 'Bash', command: QUERY })

    expect(storedFeed(world)[0]).toMatchObject({ op: 'query blocked', isBackground: true, dNodes: null })
  })

  test('a command Claude Code found read-only is not an op', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    world.answer = { result: 'ok', isReadOnly: true }
    world.touched = []
    await $.tool.call({ tool: 'Bash', command: 'omnigraph query blocked' })

    expect(storedFeed(world)).toEqual([])
    expect(world.touched).toEqual([])
  })
})

describe('where the store is', () => {
  test('a --store only the shell can spell is a feed-only op, and the pane keeps its store', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    world.touched = []
    await $.tool.call({ tool: 'Bash', command: 'omnigraph query blocked --store "$OMNI_STORE"' })

    expect(world.touched).toEqual([])
    expect(storedFeed(world)).toMatchObject([{ op: 'query blocked', store: null, dNodes: null, dEdges: null }])
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
    expect(await band.find({ type: 'Text', text: /omnigraph · dev\.omni · main · 31n 41e · 1 op · \+0n \+0e/ })).toBeDefined()
  })

  test('a --store folder with no store in it stays off the pane; /omni reload and /clear read the configured one', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.tool.call({ tool: 'Bash', command: 'omnigraph query blocked --store elsewhere/none.omni' })
    let pane = await $.ui.mount({ ...PANE, surface: 'vscode' })
    expect(await pane.find({ type: 'Text', text: /no store at/ })).toBeUndefined()
    expect(await pane.find({ type: 'Text', text: '◉ Offline editing & sync' })).toBeDefined()
    await pane.unmount()

    await $.command.run(runOmni('reload'))
    await $.classic.SessionStart({ source: 'clear' })
    pane = await $.ui.mount({ ...PANE, surface: 'vscode' })
    expect(await pane.find({ type: 'Text', text: /omnigraph · dev\.omni · main/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: '◉ Offline editing & sync' })).toBeDefined()
  })

  test('ops that alternate between two stores each get a diff against their own store', async ($, on) => {
    const world = worldOf(on)
    const other = `${ROOT}/graphs/other.omni`

    layStore(world, SEED, undefined, other)
    await $.session.start(SESSION)
    world.onTool = command => layStore(world, command.includes('other') ? AFTER_CREATE : AFTER_CREATE, undefined, command.includes('other') ? other : STORE)
    await $.tool.call({ tool: 'Bash', command: 'omnigraph query a --store graphs/other.omni' })
    world.onTool = () => layStore(world, AFTER_CREATE)
    await $.tool.call({ tool: 'Bash', command: 'omnigraph mutate create_issue --store graphs/dev.omni' })
    world.onTool = () => layStore(world, AFTER_ADD, undefined, other)
    await $.tool.call({ tool: 'Bash', command: 'omnigraph mutate add_issue_to_epic --store graphs/other.omni' })

    // The first op on other.omni had no read of it before; after that, each diffs against its own store.
    expect(rows(world)).toEqual([
      ['query a', true, null, null],
      ['mutate create_issue', true, 1, 0],
      ['mutate add_issue_to_epic', true, 0, 1],
    ])
  })

  test('an op on an unchanged store stats it and reads nothing', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    world.reads = []
    await $.tool.call({ tool: 'Bash', command: QUERY })

    expect(world.reads.filter(path => path.startsWith(STORE))).toEqual([])
  })

  // Each way the session root moves: the pane takes that root's store and feed
  // at once, and the first op there diffs against that store.
  const moves: [string, ($: Parameters<TestBody>[0], tree: string) => Promise<unknown>][] = [
    ['/cd', $ => $.command.run({ command: 'cd', args: 'sub', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 180 } })],
    ['EnterWorktree', $ => $.tool.call({ tool: 'EnterWorktree', name: 'feature' } as never)],
    ['a shell cd (CwdChanged)', ($, tree) => $.classic.CwdChanged({ old_cwd: ROOT, new_cwd: tree } as never)],
    ['anything else, at the next turn', $ => $.turn.start({ text: 'next', turnId: 't2' })],
  ]

  for (const [how, move] of moves) {
    test(`a root that moves (${how}): its own store and feed, and a diff for its first op`, async ($, on) => {
      const world = worldOf(on)
      const tree = '/trees/feature'
      const store = `${tree}/graphs/dev.omni`

      await $.session.start(SESSION)
      await $.tool.call({ tool: 'Bash', command: QUERY })
      world.dirs.add(tree)
      layStore(world, AFTER_CREATE, undefined, store)
      world.root = tree
      world.answer = { result: 'ok' }
      await move($, tree)

      const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
      expect(await band.find({ type: 'Text', text: /omnigraph · dev\.omni · 32n 41e · 0 ops/ })).toBeDefined()
      world.onTool = () => layStore(world, AFTER_ADD, undefined, store)
      await $.tool.call({ tool: 'Bash', command: ADD })
      expect((world.store.get(`feed:${tree}`) as OmniOp[]).map(op => [op.op, op.dNodes, op.dEdges])).toEqual([['mutate add_issue_to_epic', 0, 1]])
      expect(storedFeed(world).map(op => op.op)).toEqual(['query blocked'])
    })
  }

  test('a turn that starts where the root did not move reads nothing', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    world.touched = []
    expect(await $.turn.start({ text: 'hello', turnId: 't1' })).toEqual({ turnId: 't1' })
    expect(await $.command.run({ command: 'cd', args: '.', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 180 } })).toEqual({ text: '' })
    expect(world.touched).toEqual([])
  })
})

describe('hostile store data', () => {
  test('control characters in names, labels, types and the store name: every tab draws, on every surface', async ($, on) => {
    const world = worldOf(on, ['terminal'], null)
    const nodes = [
      { id: 'hub', label: 'Ru\u001ble', props: { name: 'esc \u001b[1m bold', status: 'proposed' } },
      { id: 'r\u001b1', label: 'Ru\u001ble', props: { name: 'page one\fpage two\nline', status: 'proposed', note: 'x\u0007y', source: 'docs/a.md:2' } },
      { id: 'r2', label: 'Rule', props: { name: 'wide 漢字 🚀', status: 'proposed' } },
    ]
    layStore(world, {
      manifest: JSON.stringify({ version: 'v\u001b1', name: 'evil\u001b[2Jstore', open_conflicts: ['r\u001b1'] }),
      nodes: nodes.map(node => JSON.stringify(node)).join('\n'),
      edges: [JSON.stringify({ from: 'hub', type: 'li\u001bnks', to: 'r\u001b1' }), JSON.stringify({ from: 'hub', type: 'links', to: 'r2' })].join('\n'),
    })
    world.files.set(`${ROOT}/docs/a.md`, { text: 'one\n\u001b[31mtwo\u001b[0m\r\nthree', mtimeMs: 1 })
    await $.session.start(SESSION)
    world.answer = { isError: true, result: 'x', text: 'Error: Exit code 1\nbad \u001b[2J thing' }
    await $.tool.call({ tool: 'Bash', command: 'omnigraph mutate x\u001b[2J --params {}' })

    for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
      const pane = await $.ui.mount({ ...PANE, surface })
      for (const key of ['tab-browse', 'tab-feed', 'tab-queue', 'tab-graph']) {
        await pane.press({ key })
        expect([surface, key, await pane.find({ type: 'Text', text: /omnigraph/ }) !== undefined]).toEqual([surface, key, true])
      }
      await pane.unmount()
    }
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-browse' })
    await pane.press({ key: 'm0:r\u001b1' })
    expect(await pane.find({ type: 'Code' })).toMatchObject({ props: { source: 'one\n [31mtwo [0m\nthree' } })
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
    expect(await band.find({ type: 'Text', text: /evil \[2Jstore/ })).toBeDefined()
  })

  test('a paragraph for a name: the graph client still draws; ids too long for its props draw as text', async ($, on) => {
    const world = worldOf(on, ['terminal'], null)
    const nodes = [{ id: 'hub', label: 'L', props: { name: 'N'.repeat(120_000) } }, { id: 'a', label: 'L', props: { name: 'leaf-a' } }]

    layStore(world, { manifest: '{}', nodes: nodes.map(n => JSON.stringify(n)).join('\n'), edges: JSON.stringify({ from: 'hub', type: 'links', to: 'a' }) })
    await $.session.start(SESSION)
    let pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: /leaf-a/, in: 'graph' })).toBeDefined()
    // The header draws too: the engine did not swap the pane for its own placeholder.
    expect(await pane.find({ type: 'Text', text: /omnigraph · dev\.omni · main/ })).toBeDefined()
    await pane.unmount()

    const hub = 'h'.repeat(60_000)
    layStore(world, { manifest: '{}', nodes: [JSON.stringify({ id: hub, label: 'L' }), JSON.stringify({ id: 'a'.repeat(60_000), label: 'L' })].join('\n'), edges: JSON.stringify({ from: hub, type: 'links', to: 'a'.repeat(60_000) }) })
    await $.command.run(runOmni('reload'))
    pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: /^◉ h+…$/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /omnigraph/ })).toBeDefined()
  })

  test('a source line that links out of the root is not read', async ($, on) => {
    const world = worldOf(on, ['terminal'], null)

    layStore(world, { manifest: '{"version":"v1"}', nodes: `${JSON.stringify({ id: 'r1', label: 'Rule', props: { name: 'r1', source: 'docs/link.md:1' } })}\n`, edges: '' })
    world.files.set('/outside.md', { text: 'secret', mtimeMs: 1 })
    world.links.set(`${ROOT}/docs/link.md`, '/outside.md')
    await $.session.start(SESSION)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-browse' })
    await pane.press({ key: 'm0:r1' })

    expect(await pane.find({ type: 'Code' })).toBeUndefined()
    expect(world.reads).not.toContain(`${ROOT}/docs/link.md`)
  })

  test('a byte-order mark on the store files loses nothing', async ($, on) => {
    const world = worldOf(on, ['terminal'], null)

    layStore(world, { manifest: `﻿${SEED.manifest}`, nodes: `﻿${SEED.nodes}`, edges: `﻿${SEED.edges}` })
    await $.session.start(SESSION)
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: /omnigraph · dev\.omni · main · 31n 41e · 0 ops/ })).toBeDefined()
  })

  test('a store file that grows past 4 MiB keeps the last read: no delta, a note in the header', async ($, on) => {
    const world = worldOf(on)
    const pad = `${JSON.stringify({ id: 'pad', label: 'Pad', props: { name: 'x'.repeat(4 * 1024 * 1024) } })}\n`

    await $.session.start(SESSION)
    world.onTool = () => layStore(world, { ...AFTER_CREATE, nodes: AFTER_CREATE.nodes + pad })
    await $.tool.call({ tool: 'Bash', command: CREATE })

    expect(storedFeed(world)).toMatchObject([{ dNodes: null, dEdges: null, store: 'dev.omni' }])
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: /nodes\.jsonl over 4 MiB, not read/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /contains ×5/, in: 'graph' })).toBeDefined()
  })
})

describe('failures of its own', () => {
  test('a $.store that cannot take the feed: the op still reaches the pane; errors.log and the header say why', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    world.isStoreFull = true
    await $.tool.call({ tool: 'Bash', command: QUERY })

    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-feed' })
    expect(await pane.find({ type: 'Text', text: /▸ query blocked/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /^keeping the feed in \$\.store: .*\$\.store\.set/ })).toBeDefined()
    expect(world.toasts).toEqual(['1 new op, last query blocked ✓ · /omni'])
    expect(world.written.get('/home/tester/.claude/omnigraph/errors.log')).toMatch(/^\d{4}-\d\d-\d\dT[\d:.]+Z keeping the feed in \$\.store: .*over 4 MiB\n$/)

    // /omni reload clears the note once the cause is gone.
    world.isStoreFull = false
    await $.command.run(runOmni('reload'))
    expect(await pane.find({ type: 'Text', text: /keeping the feed/ })).toBeUndefined()
  })

  test('/omni clear with a full $.store clears the pane and says what it could not do', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.tool.call({ tool: 'Bash', command: QUERY })
    world.isStoreFull = true
    const answer = await $.command.run(runOmni('clear'))

    expect(answer.text).toMatch(/^feed cleared in this pane only: \$\.store refused the write \(.*over 4 MiB\)$/)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-feed' })
    expect(await pane.find({ type: 'Text', text: /no ops yet/ })).toBeDefined()
    expect(world.written.get('/home/tester/.claude/omnigraph/errors.log')).toMatch(/clearing the feed in \$\.store: /)
  })

  test('the header counts this session\'s ops only, not another session\'s on the same root', async ($, on) => {
    const world = worldOf(on)
    const other: OmniOp = { t: world.clock.now() + 5, op: 'mutate other', source: 'bash', store: 'dev.omni', ok: false, ms: 5, dNodes: 7, dEdges: 9, touched: [], focus: null, error: 'x', version: null, session: 'session-2' }

    await $.session.start(SESSION)
    world.store.set(FEED_KEY, [other, { ...other, t: other.t + 1 }])
    await world.clock.advance(1000)
    await $.tool.call({ tool: 'Bash', command: QUERY })

    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: /1 op · \+0n \+0e · 0 ×/ })).toBeDefined()
    await pane.press({ key: 'tab-feed' })
    expect(await pane.find({ type: 'Text', text: /3 ops kept/ })).toBeDefined()
  })

  test('feeds of the roots idle longest are pruned past 40', async ($, on) => {
    const world = worldOf(on)
    const op = (t: number): OmniOp => ({ t, op: 'query x', source: 'bash', store: null, ok: true, ms: 1, dNodes: null, dEdges: null, touched: [], focus: null, error: null, version: null })

    for (let i = 0; i < 45; i++) {
      world.store.set(`feed:/old/${i}`, [op(1000 + i)])
    }
    world.store.set('prefs', { keep: true })
    await $.session.start(SESSION)

    const feeds = [...world.store.keys()].filter(key => key.startsWith('feed:'))
    expect(feeds).toHaveLength(40)
    expect(feeds).not.toContain('feed:/old/0')
    expect(feeds).toContain('feed:/old/44')
    expect(world.store.get('prefs')).toEqual({ keep: true })
  })
})
