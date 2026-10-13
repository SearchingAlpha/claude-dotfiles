// The hooks: inert where nothing draws, observe-only, ops diffed against the
// store, the ring, /omni, the pane's tabs (the graph client's keys and clicks,
// the Miller browser, the feed, the queue), the band, the toast and /clear.
import { describe, expect, test } from 'claude-code/testing'
import type { OmniOp } from 'claude-code'

import { layoutEgo } from '../hooks/canvas'
import type { EgoProps } from '../hooks/canvas'
import { ADD_PARAMS, AFTER_ADD, AFTER_CREATE, CREATE_PARAMS, SEED } from './demo-store'
import { ATTACH, BAND, HEADLESS, PANE, ROOT, SESSION, STORE, layStore, opLine, runOmni, worldOf } from './fixtures'
import type { StoreTexts, World } from './fixtures'

const CREATE = `omnigraph mutate create_issue --query queries/queries.gq --store graphs/dev.omni --params '${CREATE_PARAMS}'`
const ADD = `omnigraph mutate add_issue_to_epic --query queries/queries.gq --store graphs/dev.omni --params '${ADD_PARAMS}'`
const QUERY = `omnigraph query blocked --store graphs/dev.omni`
const FEED_KEY = `feed:${ROOT}`

/** The next graph command the engine runs leaves the store as `texts` and logs `op` as the CLI does. */
function cliWrites(world: World, texts: StoreTexts, op: string, ok = true, ms = 652, error?: string, kind: 'mutate' | 'query' = 'mutate'): void {
  world.onTool = () => layStore(world, texts, opLine(world.clock.now(), op, ok, ms, error, kind))
}

const storedFeed = (world: World): OmniOp[] => (world.store.get(FEED_KEY) as OmniOp[] | undefined) ?? []

describe('where nothing draws', () => {
  test('claude -p starts it inert: no command, no band, a graph command passes untouched and unread', async ($, on) => {
    const world = worldOf(on, [])

    await $.session.start(HEADLESS)
    cliWrites(world, AFTER_CREATE, 'create_issue')

    expect(await $.tool.call({ tool: 'Bash', command: CREATE })).toEqual({ result: 'ok' })
    expect(world.commands).toEqual([])
    expect(world.touched).toEqual([])
    expect(world.store.size).toBe(0)
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
    expect(await band.find({ type: 'Text', text: /omnigraph/ })).toBeUndefined()
    expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
  })

  test('a client attaching later brings it live once', async ($, on) => {
    const world = worldOf(on, [])

    await $.session.start(HEADLESS)
    expect(await $.session.attach(ATTACH)).toEqual({ clientId: 'desktop:default' })
    await $.session.attach({ ...ATTACH, surface: 'mobile', clientId: 'mobile:default' })

    expect(world.commands).toEqual([{ name: 'omni', immediate: true }])
    const band = await $.ui.mount({ ...BAND, surface: 'desktop' })
    expect(await band.find({ type: 'Text', text: /omnigraph · dev\.omni · main · 31n 41e · 0 ops/ })).toBeDefined()
  })
})

describe('observe-only', () => {
  test('a graph command comes back as the engine answered it: run, refused or failed, run once each', async ($, on) => {
    const world = worldOf(on)
    const answers = [
      { result: 'ok' },
      { deny: 'not now' },
      { isError: true, result: 'Exit code 1', text: "Error: Exit code 1\nomnigraph: mutate link_blocks: no node 'iss-x'" },
    ]

    await $.session.start(SESSION)
    for (const answer of answers) {
      world.answer = answer
      expect(await $.tool.call({ tool: 'Bash', command: CREATE })).toEqual(answer)
    }

    expect(world.ranTools).toEqual(['Bash', 'Bash', 'Bash'])
    expect(storedFeed(world).map(op => [op.ok, op.error])).toEqual([
      [true, null],
      [false, 'refused'],
      [false, "omnigraph: mutate link_blocks: no node 'iss-x'"],
    ])
  })

  test('session.start and /clear come back as the engine answered them', async ($, on) => {
    worldOf(on)

    expect(await $.session.start(SESSION)).toEqual({ cwd: ROOT })
    expect(await $.classic.SessionStart({ source: 'clear' })).toEqual({})
  })

  test('a command that is not a graph op reads nothing', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    world.touched = []
    await $.tool.call({ tool: 'Bash', command: 'git status && ls graphs' })
    await $.tool.call({ tool: 'Bash', command: 'omnigraph schema' })

    expect(world.touched).toEqual([])
    expect(storedFeed(world)).toEqual([])
  })
})

describe('ops and the store diff', () => {
  test('create_issue, then add_issue_to_epic: each op with its diff, latency and focus', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    cliWrites(world, AFTER_CREATE, 'create_issue', true, 652)
    await $.tool.call({ tool: 'Bash', command: CREATE })
    cliWrites(world, AFTER_ADD, 'add_issue_to_epic', true, 668)
    await $.tool.call({ tool: 'Bash', command: ADD })

    expect(storedFeed(world)).toMatchObject([
      { op: 'mutate create_issue', source: 'bash', store: 'dev.omni', ok: true, ms: 652, dNodes: 1, dEdges: 0, touched: ['iss-offline-conflict-toast'] },
      {
        op: 'mutate add_issue_to_epic',
        ok: true,
        ms: 668,
        dNodes: 0,
        dEdges: 1,
        focus: { center: 'epc-offline-sync', selected: 'iss-offline-conflict-toast' },
        version: JSON.parse(AFTER_ADD.manifest).version,
      },
    ])

    // The pane follows: drawn around the epic, the new issue selected.
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: /2 ops · \+1n \+1e · 0 ×/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /Offline editing & sync › contains › Conflict toast hides unsynced edits ● live/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /16|\d\d:\d\d:\d\d ▸ mutate add_issue_to_epic/ })).toBeDefined()
  })

  test('a query changes nothing: +0n +0e; a refused mutation keeps the CLI\'s error', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    cliWrites(world, SEED, 'blocked', true, 40, undefined, 'query')
    await $.tool.call({ tool: 'Bash', command: QUERY })
    world.answer = { isError: true, result: 'Exit code 1', text: 'Error: Exit code 1' }
    cliWrites(world, SEED, 'link_blocks', false, 3, "no node 'iss-x'")
    await $.tool.call({ tool: 'Bash', command: `omnigraph mutate link_blocks --params '{"blocker":"iss-x","blocked":"iss-offline-merge"}'` })

    expect(storedFeed(world).map(op => [op.op, op.dNodes, op.dEdges, op.ok, op.ms, op.error])).toEqual([
      ['query blocked', 0, 0, true, 40, null],
      ['mutate link_blocks', 0, 0, false, 3, "no node 'iss-x'"],
    ])
  })

  test('init on a folder with no store yet counts the whole seed', async ($, on) => {
    const world = worldOf(on, ['terminal'], null)

    await $.session.start(SESSION)
    // init writes no ops log: the op is named by the command, timed by the call.
    world.onTool = () => layStore(world, SEED)
    await $.tool.call({ tool: 'Bash', command: 'omnigraph init' })

    expect(storedFeed(world)).toMatchObject([{ op: 'init', dNodes: 31, dEdges: 41, ok: true, focus: null }])
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: /^Offline editing & sync ● live$/ })).toBeDefined()
  })

  test('the ring keeps the last 50 ops, in $.store and in the pane', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    for (let i = 0; i < 55; i++) {
      await $.tool.call({ tool: 'Bash', command: `omnigraph query q${i}` })
    }

    expect(storedFeed(world)).toHaveLength(50)
    expect(storedFeed(world)[0]?.op).toBe('query q5')
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-feed' })
    expect(await pane.find({ type: 'Text', text: /50 ops kept/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /query q54/ })).toBeDefined()
  })

  test('an MCP tool under the prefix is an op; another server\'s is not', { options: { mcpPrefix: 'mcp__graph__' } }, async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    world.onTool = null
    expect(await $.tool.call({ tool: 'mcp__graph__propose' as 'Bash', command: '' })).toEqual({ result: 'ok' })
    await $.tool.call({ tool: 'mcp__notes__search' as 'Bash', command: '' })

    expect(storedFeed(world).map(op => [op.op, op.source])).toEqual([['propose', 'mcp']])
  })

  test('a pattern that does not compile records nothing and says why', { options: { cmdRegex: '(' } }, async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.tool.call({ tool: 'Bash', command: CREATE })

    expect(storedFeed(world)).toEqual([])
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: /cmdRegex does not compile/ })).toBeDefined()
  })
})

describe('/omni and the pane', () => {
  test('/omni before session.start, in claude -p, or taken by a skill of that name is passed on and opens nothing', async ($, on) => {
    const world = worldOf(on, [])

    expect(await $.command.run(runOmni())).toEqual({ text: '' })
    await $.session.start(HEADLESS)
    expect(await $.command.run(runOmni('clear'))).toEqual({ text: '' })
    expect(world.store.size).toBe(0)

    world.refuseCommand = '"/omni" refused: taken'
    expect(await $.session.attach(ATTACH)).toEqual({ clientId: 'desktop:default' })
    expect(world.commands).toEqual([])
    expect(world.logs.join('\n')).toMatch(/\/omni not registered/)
    expect(await $.command.run(runOmni())).toEqual({ text: '' })

    expect(world.opened).toEqual([])
  })

  test('/omni runs mid-turn and opens the pane without taking the keyboard', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)

    expect(world.commands).toEqual([{ name: 'omni', immediate: true }])
    expect(await $.command.run(runOmni())).toEqual({})
    expect(world.opened).toEqual([{ id: 'omnigraph' }])
  })

  test('/omni says why a pane waits, opens on a named tab, and closes', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    world.isPlaced = false
    expect(await $.command.run(runOmni())).toEqual({ text: 'the pane is waiting: unasked below 144 columns' })
    world.isPlaced = true
    await $.command.run(runOmni('feed'))
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: /no ops yet/ })).toBeDefined()
    expect(await $.command.run(runOmni('close'))).toEqual({})
    expect(world.closed).toEqual(['omnigraph'])
    expect(await $.command.run(runOmni('sideways'))).toMatchObject({ text: expect.stringContaining('/omni [graph|') })
    await $.command.run(runOmni('focus'))
    expect(world.opened.slice(-1)).toEqual([{ id: 'omnigraph', focus: true }])
  })

  test('the Graph tab: header, tabs, breadcrumb, the graph client and the feed strip, on the terminal and the desktop', async ($, on) => {
    worldOf(on)

    await $.session.start(SESSION)
    for (const surface of ['terminal', 'desktop'] as const) {
      const pane = await $.ui.mount({ ...PANE, surface })

      expect(await pane.find({ type: 'Text', text: /omnigraph · dev\.omni · main/ })).toBeDefined()
      expect(await pane.find({ type: 'Text', text: /0 ops · \+0n \+0e · 0 ×/ })).toBeDefined()
      expect(await pane.find({ key: 'tab-browse' })).toBeDefined()
      expect(await pane.find({ type: 'Text', text: /^Offline editing & sync ● live$/ })).toBeDefined()
      expect(await pane.find({ type: 'Client' })).toBeDefined()
      expect(await pane.find({ type: 'Text', text: /Offline editing & sync/, in: 'graph' })).toBeDefined()
      expect(await pane.find({ type: 'Text', text: /contains ×5/, in: 'graph' })).toBeDefined()
      expect(await pane.find({ type: 'Text', text: /no ops yet/ })).toBeDefined()
      await pane.unmount()
    }
  })

  test('arrow keys in the graph move the selection, Enter opens it, Backspace goes back', async ($, on) => {
    worldOf(on)

    await $.session.start(SESSION)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })

    await pane.key({ key: 'right', in: 'graph' })
    const crumb = await pane.find({ type: 'Text', text: /^Offline editing & sync › contains › .+ ○ browsing$/ })
    expect(crumb).toBeDefined()
    const picked = /› contains › (.+) ○ browsing$/.exec(crumb?.text ?? '')?.[1] ?? ''

    await pane.key({ key: 'return', in: 'graph' })
    // Drawn around the issue now: its name is the hub's, under the glyph.
    const client = (await pane.find({ type: 'Client' }))?.props.props as EgoProps
    expect(client.nodes.find(node => node.side === 'center')?.name).toBe(picked)

    await pane.key({ key: 'backspace', in: 'graph' })
    const back = (await pane.find({ type: 'Client' }))?.props.props as EgoProps
    expect(back.center).toBe('epc-offline-sync')
    expect(back.nodes.find(node => node.isSelected)?.name).toBe(picked)

    // The client keeps the keys after a click: its tab digits still switch tabs.
    await pane.key({ key: '2', in: 'graph' })
    expect(await pane.find({ type: 'Text', text: 'Epic 4' })).toBeDefined()
    expect(await pane.find({ type: 'Client' })).toBeUndefined()
  })

  test('a click selects the node under it; a second click opens it; f follows ops again', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    const props = (await pane.find({ type: 'Client' }))?.props.props as EgoProps
    // The region as the terminal lays it out: the size the pane gave it.
    await pane.resize({ columns: props.columns, rows: props.rows, in: 'graph' })
    const layout = layoutEgo(props, props.columns, props.rows)
    const nadia = layout.spots.find(spot => spot.id === 'act-nadia')!

    await pane.pointer({ type: 'down', x: nadia.x0 + 1, y: nadia.y, button: 'left', in: 'graph' })
    expect(await pane.find({ type: 'Text', text: /› owned by › Nadia Okonkwo ○ browsing$/ })).toBeDefined()
    await pane.pointer({ type: 'down', x: nadia.x0 + 1, y: nadia.y, button: 'left', in: 'graph' })
    expect(((await pane.find({ type: 'Client' }))?.props.props as EgoProps).center).toBe('act-nadia')

    cliWrites(world, AFTER_CREATE, 'create_issue')
    await $.tool.call({ tool: 'Bash', command: CREATE })
    cliWrites(world, AFTER_ADD, 'add_issue_to_epic')
    await $.tool.call({ tool: 'Bash', command: ADD })
    expect(((await pane.find({ type: 'Client' }))?.props.props as EgoProps).center).toBe('act-nadia')
    await pane.press({ key: 'follow' })
    expect(await pane.find({ type: 'Text', text: /Offline editing & sync › contains › Conflict toast hides unsynced edits ● live/ })).toBeDefined()
  })

  test('Browse: Miller columns from a label to a node, its relations, a target; back; another label', async ($, on) => {
    worldOf(on)

    await $.session.start(SESSION)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-browse' })

    expect(await pane.find({ type: 'Text', text: 'Epic 4' })).toBeDefined()
    expect(await pane.find({ key: 'm0:epc-offline-sync' })).toMatchObject({ props: { label: '▶ Offline editing & sync' } })
    expect(await pane.find({ type: 'Text', text: '→ contains · Issue 5' })).toBeDefined()

    await pane.press({ key: 'm1:0:iss-offline-queue' })
    expect(await pane.find({ type: 'Text', text: /^Offline editing & sync › contains › Persist the offline edit queue/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: '← contains · Epic 1' })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: '→ owned by · Actor 1' })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /iss-offline-queue · Issue · open/ })).toBeDefined()

    await pane.press({ key: 'm2:1:act-nadia' })
    expect(await pane.find({ type: 'Text', text: /› Persist the offline edit queue › owned by › Nadia Okonkwo/ })).toBeDefined()

    await pane.press({ key: 'back' })
    expect(await pane.find({ type: 'Text', text: /^Offline editing & sync › contains › Persist the offline edit queue ○/ })).toBeDefined()

    await pane.select({ key: 'label', value: 'Actor' })
    expect(await pane.find({ type: 'Text', text: 'Actor 4' })).toBeDefined()
    await pane.press({ key: 'm0:act-priya' })
    expect(await pane.find({ type: 'Text', text: /^Priya Raman ○ browsing$/ })).toBeDefined()
  })

  test('Browse shows the source lines a node names, read only under the root', async ($, on) => {
    const world = worldOf(on, ['terminal'], null)
    const node = (id: string, source: string) => JSON.stringify({ id, label: 'Rule', props: { name: id, status: 'proposed', source: [source], value: 6 } })

    layStore(world, { manifest: '{"version":"v1"}', nodes: `${node('r-in', 'docs/notes.md:4')}\n${node('r-out', '../outside.md:1')}\n`, edges: '' })
    world.files.set(`${ROOT}/docs/notes.md`, { text: Array.from({ length: 12 }, (_, i) => `line ${i + 1}`).join('\n'), mtimeMs: 1 })
    world.files.set('/outside.md', { text: 'secret', mtimeMs: 1 })
    await $.session.start(SESSION)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-browse' })
    await pane.press({ key: 'm0:r-in' })

    expect(await pane.find({ type: 'Text', text: 'docs/notes.md:4' })).toBeDefined()
    expect(await pane.find({ type: 'Code' })).toMatchObject({ props: { source: 'line 2\nline 3\nline 4\nline 5\nline 6\nline 7\nline 8\nline 9', startLine: 2 } })
    expect(await pane.find({ type: 'Text', text: 'value: 6' })).toBeDefined()

    await pane.press({ key: 'm0:r-out' })
    expect(await pane.find({ type: 'Code' })).toBeUndefined()
    expect(world.touched).not.toContain('/outside.md')
  })

  test('the Feed tab lists the ops, a failure with its error', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    cliWrites(world, AFTER_CREATE, 'create_issue')
    await $.tool.call({ tool: 'Bash', command: CREATE })
    cliWrites(world, AFTER_CREATE, 'link_blocks', false, 4, "no node 'iss-x'")
    world.answer = { isError: true, result: 'Exit code 1', text: 'Error: Exit code 1' }
    await $.tool.call({ tool: 'Bash', command: `omnigraph mutate link_blocks --params '{}'` })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-feed' })

    expect(await pane.find({ type: 'Text', text: /▸ mutate create_issue +\+1n \+0e +652ms ✓/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /▸ mutate link_blocks +\+0n \+0e +4ms ✗/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /no node 'iss-x'/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /2 ops · \+1n \+0e · 1 ×/ })).toBeDefined()
  })

  test('the Queue tab exports a patch file under the config folder, and applies nothing', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-queue' })
    expect(await pane.find({ type: 'Text', text: /2 proposed · export writes one patch file each/ })).toBeDefined()

    await pane.press({ key: 'x:iss-bill-proration' })

    const path = '/home/tester/.claude/omnigraph/patches/dev.omni/iss-bill-proration.yaml'
    expect([...world.written.keys()]).toEqual([path])
    expect(world.written.get(path)).toContain('- id: "iss-bill-proration"\n  status: "accepted"\n')
    expect(await pane.find({ type: 'Text', text: `wrote ${path}` })).toBeDefined()
    expect(world.toasts).toEqual(['patch for Prorated invoices on plan change written'])
    expect(world.files.get(`${STORE}/nodes.jsonl`)?.text).toBe(SEED.nodes)
  })

  test('a surface with no Client draws the graph as text', async ($, on) => {
    worldOf(on)

    await $.session.start(SESSION)
    const pane = await $.ui.mount({ ...PANE, surface: 'vscode' })

    expect(await pane.find({ type: 'Client' })).toBeUndefined()
    expect(await pane.find({ type: 'Text', text: '◉ Offline editing & sync' })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /→ contains ×5: / })).toBeDefined()
  })
})

describe('the band and the toast', () => {
  test('the band shows the store and the last op, over what the mods after it draw', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    cliWrites(world, AFTER_CREATE, 'create_issue')
    await $.tool.call({ tool: 'Bash', command: CREATE })
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: /omnigraph · dev\.omni · main · 32n 41e · 1 op · \+1n \+0e · 0 × · last mutate create_issue ✓ 652ms/ })).toBeDefined()
    expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
    await band.press({ key: 'open' })
    expect(world.opened).toEqual([{ id: 'omnigraph' }])
  })

  test('no store and no ops: no band', async ($, on) => {
    worldOf(on, ['terminal'], null)

    await $.session.start(SESSION)
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: /omnigraph/ })).toBeUndefined()
  })

  test('the band option set to false leaves the band to the others', { options: { band: false } }, async ($, on) => {
    worldOf(on)

    await $.session.start(SESSION)
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: /omnigraph/ })).toBeUndefined()
  })

  test('ops while the pane is closed raise one toast per 20 s; none while it shows', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.tool.call({ tool: 'Bash', command: QUERY })
    await $.tool.call({ tool: 'Bash', command: QUERY })
    expect(world.toasts).toEqual(['1 new op, last query blocked ✓ · /omni'])

    await world.clock.advance(21_000)
    await $.tool.call({ tool: 'Bash', command: QUERY })
    expect(world.toasts[1]).toBe('3 new ops, last query blocked ✓ · /omni')

    world.isPaneShown = true
    await world.clock.advance(21_000)
    await $.tool.call({ tool: 'Bash', command: QUERY })
    expect(world.toasts).toHaveLength(2)
  })

  test('the toast option set to false raises none', { options: { toast: false } }, async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.tool.call({ tool: 'Bash', command: QUERY })

    expect(world.toasts).toEqual([])
    expect(storedFeed(world)).toHaveLength(1)
  })
})

describe('after /clear', () => {
  test('the feed comes back from $.store, with what another session added', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.tool.call({ tool: 'Bash', command: QUERY })
    // Another session on this root logged two more.
    world.store.set(FEED_KEY, [...storedFeed(world), ...storedFeed(world), ...storedFeed(world)])

    await $.classic.SessionStart({ source: 'clear' })

    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-feed' })
    expect(await pane.find({ type: 'Text', text: /3 ops kept/ })).toBeDefined()
  })
})
