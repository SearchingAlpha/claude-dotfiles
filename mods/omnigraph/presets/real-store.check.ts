// The real-build check `presets/preset.py check` runs in a copy of the mod, as
// tests/real-store.test.ts beside tests/real-store.ts (the build, embedded
// there read-only, the preset's options and its samples). It is not one of the
// mod's own tests: those never need a real build. It prints what it found
// (`facts:`), and fails where the build and the preset do not fit.
import { expect, test } from 'claude-code/testing'
import type { OmniOp } from 'claude-code'

import { adapterOf, conflictsOf, degreeOf, opNameOf, parseGraph, queueOf, slugOf } from '../hooks/graphstore'
import type { StoreTexts } from '../hooks/graphstore'
import { BAND, PANE, ROOT, SESSION, worldOf } from './fixtures'
import type { World } from './fixtures'
import { BUILD, OPTIONS, SAMPLES } from './real-store'

// The test runner prints console output; the mods' declarations leave console out.
declare const console: { log(...items: unknown[]): void }

const a = adapterOf(OPTIONS)
const STORE = OPTIONS.store.startsWith('/') ? OPTIONS.store : `${ROOT}/${OPTIONS.store}`
const SURFACES = ['terminal', 'desktop', 'vscode', 'mobile'] as const
const TABS = ['tab-graph', 'tab-browse', 'tab-feed', 'tab-queue']
/** A control character or a line separator in an id: the patch file and its YAML must survive it. */
const CONTROL = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/
const BOM = /^\uFEFF/

const manifestOf = (texts: StoreTexts): Record<string, unknown> => JSON.parse((texts.manifest ?? '{}').replace(BOM, '')) as Record<string, unknown>

/** Lays the build under the configured file names, each with a fresh mtime. */
function lay(world: World, texts: { manifest: string; nodes: string; edges: string }): void {
  world.writes++
  const at = world.clock.now() + world.writes

  world.dirs.add(STORE)
  world.files.set(`${STORE}/${a.manifestFile}`, { text: texts.manifest, mtimeMs: at })
  world.files.set(`${STORE}/${a.nodesFile}`, { text: texts.nodes, mtimeMs: at })
  world.files.set(`${STORE}/${a.edgesFile}`, { text: texts.edges, mtimeMs: at })
}

/** The build with one queued record moved to the accepted status, as a rebuild after a curation would leave it. */
function curatedOne(id: string): typeof BUILD {
  const nodes = BUILD.nodes.split('\n').map(line => {
    let record: Record<string, unknown> | null = null
    try {
      record = JSON.parse(line.replace(BOM, '')) as Record<string, unknown>
    } catch {
      // A line that is no record stays as it is (the first test counts it).
    }
    if (record === null || typeof record !== 'object' || record[a.idKey] !== id) {
      return line
    }
    const props = (a.propsKey === '.' ? record : record[a.propsKey]) as Record<string, unknown>
    props[a.statusKey] = a.acceptStatus

    return JSON.stringify(record)
  })

  return { ...BUILD, nodes: nodes.join('\n') }
}

test('the build reads whole with the preset: records, names, version, queue, conflicts, file names', () => {
  expect(a.problem).toBeNull()
  let t = Date.now()
  const g = parseGraph(STORE, BUILD, a)
  const parseMs = Date.now() - t
  const manifest = manifestOf(BUILD)
  const ids = [...g.nodes.keys()]
  const queued = queueOf(g, a)
  const statusCounts = manifest.status
  const named = [...g.nodes.values()].filter(node => node.name !== node.id).length
  t = Date.now()
  let hub = ''
  for (const id of ids) {
    if (hub === '' || degreeOf(g, id) > degreeOf(g, hub)) {
      hub = id
    }
  }
  const facts = {
    parseMs,
    hubMs: Date.now() - t,
    nodes: g.nodes.size,
    edges: g.edges.length,
    labels: g.labels.length,
    unreadable: g.bad,
    named,
    queued: queued.length,
    conflicts: conflictsOf(g, a).length,
    hubDegree: hub === '' ? 0 : degreeOf(g, hub),
    longestId: Math.max(0, ...ids.map(id => id.length)),
    idsWithControls: ids.filter(id => CONTROL.test(id)).length,
    version: g.version,
  }
  console.log('facts:', JSON.stringify(facts))

  expect(g.isPresent).toBe(true)
  expect(g.bad).toBe(0)
  expect(g.nodes.size).toBeGreaterThan(0)
  // The manifest's own counts, version and lists, where it has them.
  if (typeof manifest.nodes === 'number') {
    expect(g.nodes.size).toBe(manifest.nodes)
  }
  if (typeof manifest.edges === 'number') {
    expect(g.edges.length).toBe(manifest.edges)
  }
  if (a.versionKey in manifest) {
    expect(g.version).toBe(String(manifest[a.versionKey]))
  }
  if (typeof statusCounts === 'object' && statusCounts !== null && a.queueStatus.every(s => typeof (statusCounts as Record<string, unknown>)[s] === 'number')) {
    expect(queued.length).toBe(a.queueStatus.reduce((sum, s) => sum + ((statusCounts as Record<string, number>)[s] ?? 0), 0))
  }
  if (a.conflictsKey !== '' && Array.isArray(manifest[a.conflictsKey])) {
    expect(conflictsOf(g, a)).toEqual(manifest[a.conflictsKey])
  }
  // nameKeys that name no node at all are the wrong keys.
  expect(named).toBeGreaterThan(0)
  // One patch file per record: no two ids share a file name.
  expect(new Set(ids.map(id => slugOf(id))).size).toBe(ids.length)
})

test('through the hooks: every tab on every surface, an unchanged and a rewritten build, a non-op, an export', { options: OPTIONS, timeoutMs: 60_000 }, async ($, on) => {
  const world = worldOf(on, ['terminal'], null)
  const [command, opName] = SAMPLES.ops[0] ?? ['', '']
  const g = parseGraph(STORE, BUILD, a)
  const label = a.storeLabel || (typeof manifestOf(BUILD).name === 'string' ? String(manifestOf(BUILD).name) : STORE.split('/').pop())

  lay(world, BUILD)
  let t = Date.now()
  await $.session.start(SESSION)
  const startMs = Date.now() - t

  const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
  const bandLine = (await band.find({ type: 'Text', text: /omnigraph ·/ }))?.text ?? ''
  expect(bandLine).toContain(`omnigraph · ${label} · main · ${g.nodes.size}n ${g.edges.length}e · 0 ops`)
  await band.unmount()

  const missing: string[] = []
  for (const surface of SURFACES) {
    const pane = await $.ui.mount({ ...PANE, surface })
    for (const key of TABS) {
      await pane.press({ key })
      if ((await pane.find({ type: 'Text', text: /omnigraph/ })) === undefined) {
        missing.push(`${surface}/${key}`)
      }
    }
    await pane.unmount()
  }
  expect(missing).toEqual([])

  // The first sample op over an unchanged build: named, +0n +0e, the files only stat'ed.
  expect(opNameOf(command, a.cmdRegex)).toBe(opName)
  world.reads = []
  t = Date.now()
  await $.tool.call({ tool: 'Bash', command })
  const unchangedMs = Date.now() - t
  const readsUnchanged = world.reads.filter(path => path.startsWith(`${STORE}/`)).length

  // A rebuild that moved one queued record to the accepted status: ~1n.
  const queued = queueOf(g, a)
  const target = queued.find(id => /[:/]/.test(id)) ?? queued[0]
  if (target !== undefined) {
    world.onTool = () => lay(world, curatedOne(target))
  }
  t = Date.now()
  await $.tool.call({ tool: 'Bash', command })
  const changedMs = Date.now() - t
  world.onTool = null
  // Commands that only touch the scripts are no ops.
  for (const notOp of SAMPLES.notOps.slice(0, 3)) {
    await $.tool.call({ tool: 'Bash', command: notOp })
  }

  const feed = (world.store.get(`feed:${ROOT}`) as OmniOp[] | undefined) ?? []
  const rows = feed.map(op => [op.op, op.dNodes, op.dEdges, op.changed ?? 0])
  expect(readsUnchanged).toBe(0)
  expect(rows).toEqual([
    [opName, 0, 0, 0],
    [opName, 0, 0, target === undefined ? 0 : 1],
  ])

  // A Queue export: one record, the accepted status, the owner when set; the store is not written.
  let exported: [string, string] | undefined
  if (target !== undefined) {
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    await pane.press({ key: 'tab-queue' })
    // The target is curated now: export the first record still queued.
    const still = queueOf(parseGraph(STORE, curatedOne(target), a), a)
    const id = still.find(each => /[:/]/.test(each)) ?? still[0]
    if (id !== undefined) {
      for (let page = 0; page < 200 && (await pane.find({ key: `x:${id}` })) === undefined; page++) {
        await pane.press({ key: 'next' })
      }
      await pane.press({ key: `x:${id}` })
      exported = [...world.written.entries()].find(([path]) => path.includes('/patches/'))
      expect(exported?.[0]).toMatch(new RegExp(`/${slugOf(id).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\.yaml$`))
      const text = exported?.[1] ?? ''
      expect(text.split('\n').filter(line => line.startsWith('- '))).toEqual([`- id: ${JSON.stringify(id)}`])
      expect(text).toContain(`  ${a.statusKey}: ${JSON.stringify(a.acceptStatus)}\n`)
      expect(text.includes('  owner: ')).toBe(a.owner !== '')
    }
    await pane.unmount()
  }
  expect([...world.written.keys()].filter(path => path.startsWith(`${STORE}/`))).toEqual([])

  console.log('hooks:', JSON.stringify({ startMs, unchangedMs, changedMs, readsUnchanged, feed: rows, exported: exported !== undefined }))
})
