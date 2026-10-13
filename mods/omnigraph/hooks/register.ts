// omnigraph: a live pane over a typed graph store while Claude works on it.
//
// It follows mods/CLAUDE.md: inert where nothing draws (claude -p, cron, the
// SDK); observe-only (every hook on an engine event passes it on with next and
// returns what came back, none can refuse anything); a tool.call observer does
// nothing before next and reads the store only after a matched graph command;
// what the drawings read sits in $.state; the pane opens only when the person
// asks; files are written only by a Queue export press and the mod's own error
// log, both under the mod's folder.
import { atom, read, update } from 'claude-code'
import type { Elements, EngineInterface, On, OmniOp, PluginOptions, ToolCallResult } from 'claude-code'

import { egoOf, propsFit } from './ego'
import {
  adapterOf,
  branchOfHead,
  degreeOf,
  diffGraphs,
  emptyGraph,
  errorLineOf,
  feedOf,
  fit,
  focusOf,
  gitDirOf,
  infoOf,
  isBackgroundResult,
  isUnder,
  joinPath,
  NO_TEXTS,
  opNameOf,
  opRecordOf,
  parseGraph,
  patchOf,
  plural,
  pushRing,
  slugOf,
  sourceRefOf,
  storeDirOf,
  storeNameOf,
  totalsOf,
  touchedOf,
} from './graphstore'
import type { Adapter, Graph, OpRecord, StoreTexts } from './graphstore'
import { clean } from './text'
import { bandText, graphTabRows, paneOf, TABS } from './views'
import type { Actions, Tab } from './views'

const PANE = 'omnigraph'
/** At most one "new ops" toast this often while the pane is closed. */
const TOAST_GAP_MS = 20_000
/** A store file, or an ops log, past this is not read (the $.fs.read cap). */
const FILE_MAX = 4 * 1024 * 1024
/** How many stores' last reads are kept, so ops that alternate between stores each get a diff. */
const BASELINES_MAX = 4
/** How many roots keep a feed in $.store; the ones idle longest go first. */
const FEEDS_MAX = 40
/** The error log keeps this many lines. */
const ERROR_LOG_LINES = 200
/** How many ops-log lines no op took are kept per store, for an overlapping op to find its own. */
const PENDING_MAX = 20

// `plugin` and `key` stay string literals and each atom sits in a const:
// `claude plugin validate` reads them off the source and holds them to
// types/index.d.ts.
const feed = atom({ plugin: 'omnigraph', key: 'feed' } as const, [])
const since = atom({ plugin: 'omnigraph', key: 'since' } as const, 0)
const info = atom({ plugin: 'omnigraph', key: 'info' } as const, null)
const graphRev = atom({ plugin: 'omnigraph', key: 'graphRev' } as const, 0)
const tab = atom({ plugin: 'omnigraph', key: 'tab' } as const, 'graph')
const trail = atom({ plugin: 'omnigraph', key: 'trail' } as const, [])
const center = atom({ plugin: 'omnigraph', key: 'center' } as const, null)
const isFollowing = atom({ plugin: 'omnigraph', key: 'isFollowing' } as const, true)
const browseLabel = atom({ plugin: 'omnigraph', key: 'browseLabel' } as const, null)
const queuePage = atom({ plugin: 'omnigraph', key: 'queuePage' } as const, 0)
const notice = atom({ plugin: 'omnigraph', key: 'notice' } as const, null)

// Module state, lost on a reload and read again from the store: the graph is
// too big for $.state, so the drawings read graphRev to redraw when it changes.
let adapter: Adapter = adapterOf({})
/** The store the pane shows. */
let graph: Graph = emptyGraph('')
/** The last reads of the stores the ops touched, by folder, with the stat stamp each was read at. */
const baselines = new Map<string, { graph: Graph; stamp: string }>()
/**
 * How far each store's ops log was read: its stat, and once read its line
 * count and last line (lines null: not read yet), so a log trimmed to a fixed
 * length still shows which lines are new.
 */
const logCursors = new Map<string, { size: number; mtimeMs: number; lines: number | null; last: string | null }>()
/** Ops-log lines read but taken by no op yet (an overlapping op's), per store folder. */
const pendingLines = new Map<string, string[]>()
/** The op being recorded: the next one waits for it, so two overlapping ops neither share a diff nor lose a feed entry. */
let queue: Promise<void> = Promise.resolve()
/** True once the session has a surface; until then every hook only passes its event on. */
let isLive = false
/** True once /omni is the mod's: registered in a live session. */
let isRegistered = false
let sessionId: string | null = null
/** The session root the feed atom was read for. */
let rootSeen = ''
/** The mod's last internal error, shown in the header until /omni reload. */
let internalError: string | null = null
let lastToastAt = -Infinity
let unseen = 0
let isToastOn = true
/** The graph region's size at the last pane draw: a ui.message answer lays the client out the same. */
const lastEgoSize = { columns: 60, rows: 12 }
let sourceCache: { key: string; text: string | null } = { key: '', text: null }

const feedKey = (root: string): string => `feed:${root}`

const messageOf = (error: unknown): string => clean(error instanceof Error ? error.message : String(error)).slice(0, 300)

/** The mod's own folder under the Claude Code configuration folder; null without a home. */
async function ownDirOf($: EngineInterface): Promise<string | null> {
  const home = (await $.env.get('HOME')) ?? null
  const config = (await $.env.get('CLAUDE_CONFIG_DIR')) || (home === null ? null : `${home}/.claude`)

  return config === null ? null : `${config}/omnigraph`
}

/**
 * An internal failure: to the debug log, the header, and (once live)
 * errors.log in the mod's folder. The mod fails open: the call it was
 * watching already has its answer.
 */
async function logError($: EngineInterface, what: string, error: unknown): Promise<void> {
  const line = `${what}: ${messageOf(error)}`

  $.ui.log(line, { to: 'debug' })
  internalError = line
  try {
    await update($, info, was => (was === null ? was : { ...was, problem: [was.problem, line].filter(note => note !== null).join('; ') }))
    const dir = isLive ? await ownDirOf($) : null
    if (dir !== null) {
      const was = (await $.fs.read(`${dir}/errors.log`).catch(() => '')) ?? ''
      const at = new Date(await $.clock.now()).toISOString()
      const lines = `${was}${at} ${line}\n`.split('\n').filter(entry => entry !== '')
      await $.fs.write(`${dir}/errors.log`, `${lines.slice(-ERROR_LOG_LINES).join('\n')}\n`)
    }
  } catch (again) {
    $.ui.log(`omnigraph errors.log not written: ${messageOf(again)}`, { to: 'debug' })
  }
}

// ------------------------------------------------------------- the store

type StoreRead = {
  /** The texts, or null when the stamp was the known one (nothing changed) or a file is over the cap and there is a read to keep. */
  texts: StoreTexts | null
  stamp: string
  problem: string | null
  isPartial: boolean
}

/**
 * Stats the store's three files, then reads them unless the stamp is the
 * `known` one. A file over the cap is not read: with `canKeep` nothing is,
 * so the caller keeps its last read instead of seeing the store emptied.
 */
async function readStore($: EngineInterface, dir: string, known: string | null, canKeep: boolean): Promise<StoreRead> {
  const names = [adapter.manifestFile, adapter.nodesFile, adapter.edgesFile]
  const stats = await Promise.all(names.map(name => (name === '' ? Promise.resolve(null) : $.fs.stat(`${dir}/${name}`).catch(() => null))))
  const stamp = stats.map(stat => (stat === null ? '-' : `${stat.mtimeMs}:${stat.size}`)).join('|')
  const big = names.filter((name, i) => (stats[i]?.size ?? 0) > FILE_MAX)
  const problem = big.length > 0 ? `${big.join(', ')} over 4 MiB, not read` : null

  if (stamp === known || (big.length > 0 && canKeep)) {
    return { texts: null, stamp, problem, isPartial: big.length > 0 }
  }

  const texts = await Promise.all(
    names.map((name, i) => (stats[i] === null || stats[i]?.kind !== 'file' || big.includes(name) ? Promise.resolve(null) : $.fs.read(`${dir}/${name}`).catch(() => null))),
  )

  return {
    texts: { manifest: texts[0] ?? null, nodes: texts[1] ?? null, edges: texts[2] ?? null },
    stamp,
    problem,
    isPartial: big.length > 0,
  }
}

/** A store read: the last read of that folder before it (null: none), this one, and whether it differs. */
type GraphRead = { before: Graph | null; after: Graph; changed: boolean; isPartial: boolean; problem: string | null }

/** Reads the store at `dir` again when its files changed (or `force`), and keeps it as that folder's baseline. */
async function readGraph($: EngineInterface, dir: string, force: boolean): Promise<GraphRead> {
  const cached = baselines.get(dir)
  const found = await readStore($, dir, force || cached === undefined ? null : cached.stamp, cached !== undefined)

  if (!logCursors.has(dir)) {
    await markOpsLog($, dir)
  }
  if (found.texts === null && cached !== undefined) {
    return { before: cached.graph, after: cached.graph, changed: false, isPartial: found.isPartial, problem: found.problem }
  }

  const after = parseGraph(dir, found.texts ?? NO_TEXTS, adapter)

  baselines.delete(dir)
  baselines.set(dir, { graph: after, stamp: found.stamp })
  // The oldest go first, never the one on screen.
  for (const key of baselines.keys()) {
    if (baselines.size <= BASELINES_MAX) {
      break
    }
    if (key !== graph.dir && key !== dir) {
      baselines.delete(key)
    }
  }

  return { before: cached?.graph ?? null, after, changed: true, isPartial: found.isPartial, problem: found.problem }
}

/** The branch the session root is on, from .git/HEAD (a worktree's .git file points at its own). */
async function branchOf($: EngineInterface, root: string): Promise<string | null> {
  let dir = root

  // Up to the filesystem's root: a /cd deep into a checkout still names its branch.
  for (let depth = 0; depth < 64 && dir !== ''; depth++) {
    const dotGit = await $.fs.stat(`${dir}/.git`).catch(() => null)
    if (dotGit !== null) {
      const gitDir = dotGit.kind === 'dir' ? `${dir}/.git` : gitDirOf((await $.fs.read(`${dir}/.git`).catch(() => '')) ?? '')
      if (gitDir === null) {
        return null
      }
      const head = await $.fs.read(`${joinPath(dir, gitDir)}/HEAD`).catch(() => null)

      return head === null ? null : branchOfHead(head)
    }
    dir = dir.replace(/\/[^/]*$/, '')
  }

  return null
}

/** Makes `next` the graph the drawings read, with the header's facts; starts at the busiest node when nothing is selected. */
async function show($: EngineInterface, next: Graph, problem: string | null): Promise<void> {
  const isNew = next !== graph

  graph = next
  const branch = await branchOf($, await $.session.root())
  await update($, info, () => infoOf(graph, adapter, branch, [problem, internalError]))

  if (isNew) {
    await update($, graphRev, rev => rev + 1)
    sourceCache = { key: '', text: null }
  }

  // Nothing selected yet, or the selection is gone: start at the busiest node.
  const path = await read($, trail)
  if (graph.nodes.size > 0 && (path.length === 0 || !path.every(id => graph.nodes.has(id)))) {
    let hub: string | null = null
    for (const id of graph.nodes.keys()) {
      if (hub === null || degreeOf(graph, id) > degreeOf(graph, hub)) {
        hub = id
      }
    }
    if (hub !== null) {
      await update($, trail, () => [hub as string])
      await update($, center, () => hub)
    }
  }
}

/** The configured store under the current root. */
async function configuredDirOf($: EngineInterface): Promise<string> {
  return joinPath(await $.session.root(), adapter.store, (await $.env.get('HOME')) ?? null)
}

/**
 * Reads the store again for /omni reload, /clear and a root that moved. A
 * moved root gets its feed and its configured store; otherwise the pane keeps
 * the store it shows (a --store one) while that is there and under the root,
 * else it too gets the configured one.
 */
async function reload($: EngineInterface): Promise<void> {
  const root = await $.session.root()
  const hasMoved = root !== rootSeen

  if (hasMoved) {
    await loadFeed($)
  }
  internalError = null
  const dir = !hasMoved && graph.isPresent && graph.dir !== '' && isUnder(graph.dir, root) ? graph.dir : await configuredDirOf($)
  const found = await readGraph($, dir, true)
  await show($, found.after, found.problem)
}

/**
 * After a /cd, a worktree tool, a shell cd and at each turn's start: when the
 * session root moved, the pane takes that root's feed and store, read now so
 * the first op there has a diff. Costs one $.session.root() otherwise; never
 * throws, as it runs after the event's own answer.
 */
async function followRoot($: EngineInterface): Promise<void> {
  try {
    if (isLive && (await $.session.root()) !== rootSeen) {
      await reload($)
    }
  } catch (error) {
    await logError($, 'following the session root', error)
  }
}

async function loadFeed($: EngineInterface): Promise<void> {
  const root = await $.session.root()
  const stored = feedOf(await $.store.get(feedKey(root)))

  rootSeen = root
  await update($, feed, () => stored)
}

/** Keeps the feeds of the FEEDS_MAX roots used last (each worktree is a root); the store is 4 MiB for them all. */
async function pruneFeeds($: EngineInterface): Promise<void> {
  const keys = (await $.store.keys()).filter(key => key.startsWith('feed:'))

  if (keys.length <= FEEDS_MAX) {
    return
  }

  const ages = await Promise.all(
    keys.map(async key => {
      const ops = feedOf(await $.store.get(key))
      return { key, t: ops[ops.length - 1]?.t ?? 0 }
    }),
  )
  const current = feedKey(await $.session.root())
  for (const { key } of ages.sort((x, y) => x.t - y.t).slice(0, keys.length - FEEDS_MAX)) {
    if (key !== current) {
      await $.store.delete(key)
    }
  }
}

/** What the session shows once it has somewhere to draw. `$` may be passed to a function at the top level of this file, and nowhere else. */
async function goLive($: EngineInterface): Promise<void> {
  // The prompt and its band may have drawn already, while the mod was inert.
  $.ui.invalidate('ui.render')

  try {
    const now = await $.clock.now()
    sessionId = await $.session.id()
    await update($, since, was => was || now)
    await loadFeed($)
    const found = await readGraph($, await configuredDirOf($), true)
    await show($, found.after, found.problem)
  } catch (error) {
    await logError($, 'reading the store', error)
  }

  try {
    await pruneFeeds($)
  } catch (error) {
    await logError($, 'pruning old feeds', error)
  }

  // Last, and caught: register throws for a taken name (a skill or command of
  // the person's called omni), and a hook that throws is skipped whole.
  try {
    await $.command.register({
      name: 'omni',
      description: 'Open the omnigraph pane: the graph, a browser, the op feed and a review queue',
      argumentHint: '[graph|browse|feed|queue|focus|close|clear|reload]',
      immediate: true,
    })
    isRegistered = true
  } catch (error) {
    await logError($, '/omni not registered', error)
  }
}

// --------------------------------------------------------------- the ops

/** A matched call: when it started and when its result came back, what it was, and that result. */
type Seen = {
  t0: number
  t1: number
  name: string
  command: string | null
  source: OmniOp['source']
  result: ToolCallResult
  /** The command went to the background: it has not run yet. */
  isBackground: boolean
}

/** Notes where the ops log ends when a store is first read, so the records already in it never stand in for an op's. */
async function markOpsLog($: EngineInterface, dir: string): Promise<void> {
  if (adapter.opsFile === '') {
    return
  }

  const stat = await $.fs.stat(`${dir}/${adapter.opsFile}`).catch(() => null)

  logCursors.set(dir, stat === null ? { size: 0, mtimeMs: 0, lines: 0, last: null } : { size: stat.size, mtimeMs: stat.mtimeMs, lines: null, last: null })
}

/**
 * The lines the ops log gained since it was last read: those after the line
 * it ended with then (where an append left it, else wherever a trim moved
 * it), everything when that line is gone, only the last one when the log was
 * never read.
 */
async function freshOpLines($: EngineInterface, dir: string): Promise<string[]> {
  const path = `${dir}/${adapter.opsFile}`
  const stat = await $.fs.stat(path).catch(() => null)
  const was = logCursors.get(dir)

  if (stat === null || stat.kind !== 'file') {
    logCursors.set(dir, { size: 0, mtimeMs: 0, lines: 0, last: null })
    return []
  }
  if (was !== undefined && stat.size === was.size && stat.mtimeMs === was.mtimeMs) {
    return []
  }
  if (stat.size > FILE_MAX) {
    logCursors.set(dir, { size: stat.size, mtimeMs: stat.mtimeMs, lines: null, last: null })
    return []
  }

  const text = await $.fs.read(path).catch(() => null)
  if (text === null) {
    return []
  }
  const lines = text.split('\n').filter(line => line.trim() !== '')
  logCursors.set(dir, { size: stat.size, mtimeMs: stat.mtimeMs, lines: lines.length, last: lines[lines.length - 1] ?? null })

  if (was === undefined || was.lines === null) {
    return lines.slice(-1)
  }
  if (was.last === null) {
    return lines
  }
  // Appended to: the old last line is where it was. Trimmed at the top: it moved up.
  if (lines[was.lines - 1] === was.last) {
    return lines.slice(was.lines)
  }
  const at = lines.lastIndexOf(was.last)

  return at >= 0 ? lines.slice(at + 1) : lines
}

/**
 * The producer's record of this call: a line the ops log gained since it was
 * last read, or one an overlapping op left, that fits the call; else null.
 */
async function newOpRecord($: EngineInterface, dir: string, seen: Seen): Promise<OpRecord | null> {
  if (adapter.opsFile === '') {
    return null
  }

  const lines = [...(pendingLines.get(dir) ?? []), ...(await freshOpLines($, dir))].slice(-PENDING_MAX)
  const found = opRecordOf(lines, seen.t0, seen.t1, seen.command)

  pendingLines.set(dir, found === null ? lines : lines.filter((line, i) => i !== found.index))

  return found
}

/**
 * Records ops one at a time in the order their calls settled: two that
 * overlap (parallel calls, subagents) would otherwise read and write the feed
 * and the store's baseline over each other. A wait that outruns the hook's
 * budget still leaves the call its answer: the engine's catch replays it.
 */
async function recordInOrder($: EngineInterface, seen: Seen): Promise<void> {
  const before = queue
  let done = (): void => undefined
  queue = new Promise<void>(resolve => {
    done = resolve
  })

  try {
    await before
    await recordOp($, seen)
  } catch (error) {
    await logError($, `recording ${seen.name}`, error)
  } finally {
    done()
  }
}

/** After a matched call: diffs its store against that store's last read, logs the op, follows it, toasts it. */
async function recordOp($: EngineInterface, seen: Seen): Promise<void> {
  const root = await $.session.root()
  const isRefused = seen.result.deny !== undefined
  const isFailed = isRefused || seen.result.isError === true
  let found: GraphRead | null = null
  let logged: OpRecord | null = null

  if (root !== rootSeen) {
    await loadFeed($)
  }

  // A background command has not run yet: the next read of its store diffs what it did.
  if (!seen.isBackground) {
    const home = (await $.env.get('HOME')) ?? null
    const dir = storeDirOf(root, await $.session.cwd(), seen.command, adapter, home)

    if (dir !== null) {
      found = await readGraph($, dir, false)
      // A store the op's command named but that is not there stays off the pane.
      if (found.after.isPresent || dir === graph.dir) {
        await show($, found.after, found.problem)
      }
      if (!isRefused && found.after.isPresent) {
        logged = await newOpRecord($, dir, seen)
      }
    }
  }

  const before = found?.before ?? null
  const after = found?.after ?? null
  // A delta needs this store's last read, and a read of all of it now.
  const isCounted = after !== null && after.isPresent && before !== null && found?.isPartial !== true
  const diff = isCounted && found?.changed === true ? diffGraphs(before, after) : null
  // The tool's own failure always wins: a record can only add the producer's reason.
  const ok = !isFailed && (logged?.ok ?? true)
  const op: OmniOp = {
    t: seen.t0,
    op: logged?.op ?? seen.name,
    source: seen.source,
    store: after?.isPresent ? storeNameOf(after, adapter) : null,
    ok,
    ms: logged?.ms ?? Math.max(0, seen.t1 - seen.t0),
    dNodes: isCounted ? after.nodes.size - before.nodes.size : null,
    dEdges: isCounted ? after.edges.length - before.edges.length : null,
    touched: diff === null ? [] : touchedOf(diff),
    focus: diff === null ? null : focusOf(diff, after as Graph),
    error: ok ? null : (logged?.error ?? (isRefused ? 'refused' : errorLineOf(seen.result.isError === true ? seen.result.text : undefined)) ?? 'the ops log says it failed'),
    version: after?.version ?? null,
  }

  if (sessionId !== null) {
    op.session = sessionId
  }
  if (diff !== null && diff.changedNodes.length > 0) {
    op.changed = diff.changedNodes.length
  }
  if (seen.isBackground) {
    op.isBackground = true
  }

  // The store is shared by every session on this root: read it again right
  // before the write. The pane gets the op first, so a write that fails (the
  // store is full) loses only the copy.
  const stored = pushRing(feedOf(await $.store.get(feedKey(root))), op)
  await update($, feed, () => stored)
  try {
    await $.store.set(feedKey(root), stored)
  } catch (error) {
    await logError($, 'keeping the feed in $.store', error)
  }

  if (op.focus !== null && graph === after && (await read($, isFollowing))) {
    await follow($, op.focus)
  }

  const now = await $.clock.now()
  if (!(await $.ui.panes()).some(pane => pane.id === PANE && pane.isShown)) {
    unseen++
    if (isToastOn && now - lastToastAt >= TOAST_GAP_MS) {
      lastToastAt = now
      $.ui.toast(`${plural(unseen, 'new op')}, last ${op.op} ${op.isBackground ? '… bg' : op.ok ? '✓' : '✗'} · /omni`)
    }
  }

  // The mod's own cost of the op, after the tool answered: the debug log has it.
  $.ui.log(`${op.op} recorded in ${Math.round((await $.clock.now()) - seen.t1)}ms`, { to: 'debug' })
}

/** Moves the graph and the trail to an op's nodes, keeping the trail up to its hub when the hub is on it. */
async function follow($: EngineInterface, focus: NonNullable<OmniOp['focus']>): Promise<void> {
  const path = await read($, trail)
  const at = path.indexOf(focus.center)
  const base = at >= 0 ? path.slice(0, at + 1) : [focus.center]

  await update($, trail, () => (focus.selected === focus.center ? base : [...base, focus.selected]))
  await update($, center, () => focus.center)
  await update($, browseLabel, () => null)
}

// ------------------------------------------------------- browsing the graph

/** The trail with `id` selected next to the graph's centre (the centre itself ends it). */
function trailTo(path: readonly string[], hub: string | null, id: string): string[] {
  const at = hub === null ? -1 : path.lastIndexOf(hub)
  const base = at >= 0 ? path.slice(0, at + 1) : hub === null ? [] : [hub]

  return id === hub ? base : [...base, id]
}

async function selectNode($: EngineInterface, id: string): Promise<void> {
  const hub = await read($, center)

  await update($, isFollowing, () => false)
  await update($, trail, path => trailTo(path, hub, id))
}

async function openNode($: EngineInterface, id: string): Promise<void> {
  const hub = await read($, center)

  await update($, isFollowing, () => false)
  await update($, trail, path => (path[path.length - 1] === id ? path : trailTo(path, hub, id)))
  await update($, center, () => id)
}

async function goBack($: EngineInterface): Promise<void> {
  const path = await read($, trail)
  const hub = await read($, center)
  const at = hub === null ? -1 : path.lastIndexOf(hub)

  await update($, isFollowing, () => false)
  if (at > 0) {
    // Up one: the old centre stays selected, drawn around the node before it.
    await update($, trail, () => path.slice(0, at + 1))
    await update($, center, () => path[at - 1])
  } else if (path.length > 1) {
    await update($, trail, () => path.slice(0, -1))
    await update($, center, () => (path.length > 2 ? path[path.length - 3] : path[0]))
  }
}

async function toggleFollow($: EngineInterface): Promise<void> {
  const was = await read($, isFollowing)

  await update($, isFollowing, () => !was)
  if (!was) {
    const last = [...(await read($, feed))].reverse().find(op => op.focus !== null)
    if (last?.focus && graph.nodes.has(last.focus.center)) {
      await follow($, last.focus)
    }
  }
}

async function exportPatch($: EngineInterface, id: string): Promise<void> {
  const node = graph.nodes.get(id)

  if (node === undefined) {
    return
  }

  try {
    const root = await $.session.root()
    const home = (await $.env.get('HOME')) ?? null
    const own = await ownDirOf($)
    const base = adapter.patchDir !== '' ? joinPath(root, adapter.patchDir, home) : own === null ? null : `${own}/patches`
    if (base === null) {
      await update($, notice, () => 'no patch folder: set the patchDir option')
      return
    }
    const name = storeNameOf(graph, adapter)
    const path = `${base}/${slugOf(name, 'store')}/${slugOf(id)}.yaml`
    await $.fs.write(path, patchOf(node, adapter, name, graph.version, new Date(await $.clock.now()).toISOString()))
    await update($, notice, () => `wrote ${path}`)
    $.ui.toast(`patch for ${fit(node.name, 40)} written`)
  } catch (error) {
    await update($, notice, () => `export failed: ${messageOf(error)}`)
  }
}

/** What the pane's buttons do, each bound to `$` here. */
function actionsOf($: EngineInterface): Actions {
  return {
    tab: next => {
      update($, tab, () => next).catch(() => undefined)
    },
    follow: () => {
      toggleFollow($).catch(() => undefined)
    },
    pick: path => {
      update($, isFollowing, () => false)
        .then(() => update($, trail, () => path))
        .then(() => update($, center, () => (path.length >= 2 ? path[path.length - 2] : (path[0] ?? null))))
        .catch(() => undefined)
    },
    label: label => {
      update($, isFollowing, () => false)
        .then(() => update($, browseLabel, () => label))
        .catch(() => undefined)
    },
    back: () => {
      goBack($).catch(() => undefined)
    },
    page: page => {
      update($, queuePage, () => page).catch(() => undefined)
    },
    exportPatch: id => {
      exportPatch($, id).catch(() => undefined)
    },
  }
}

/** Where a path really lands: every link followed; null when it leads nowhere. */
async function realPathOf($: EngineInterface, path: string): Promise<string | null> {
  const stat = await $.fs.stat(path, { resolve: true }).catch(() => null)

  return stat?.realPath ?? null
}

/** The selection's source lines (sourceKey's path:line under the root), cached per node. */
async function sourceOf($: EngineInterface, id: string | undefined): Promise<{ path: string; line: number; text: string } | null> {
  const node = id === undefined ? undefined : graph.nodes.get(id)
  const ref = node === undefined ? null : sourceRefOf(node, adapter)

  if (ref === null) {
    return null
  }

  const key = `${ref.path}:${ref.line}`
  if (sourceCache.key !== key) {
    const root = await $.session.root()
    const path = joinPath(root, ref.path)
    // The path comes from the store's data: read only what lands under the
    // session root, spelled (no `..` out) and resolved (no link out).
    let text: string | null = null
    if (isUnder(path, root) && path !== root) {
      const [real, realRoot] = await Promise.all([realPathOf($, path), realPathOf($, root)])
      if (real !== null && realRoot !== null && isUnder(real, realRoot) && real !== realRoot) {
        text = await $.fs.read(path).catch(() => null)
      }
    }
    const start = Math.max(0, ref.line - 3)
    sourceCache = { key, text: text === null ? null : text.split('\n').slice(start, start + 8).join('\n') }
  }

  return sourceCache.text === null ? null : { ...ref, text: sourceCache.text }
}

// ------------------------------------------------------------------ hooks

export function register(on: On, options: PluginOptions) {
  adapter = adapterOf(options)
  isToastOn = options.toast !== false
  const isBandOn = options.band !== false
  // A reload runs register again: start from nothing, as a fresh module does.
  isLive = false
  isRegistered = false
  graph = emptyGraph('')
  baselines.clear()
  logCursors.clear()
  pendingLines.clear()
  queue = Promise.resolve()
  sessionId = null
  rootSeen = ''
  internalError = null
  lastToastAt = -Infinity
  unseen = 0
  sourceCache = { key: '', text: null }

  on('session.start', async ($, e, next) => {
    if ((await $.session.surfaces()).length === 0) {
      return next(e)
    }

    isLive = true
    await goLive($)

    return next(e)
  })

  // A session that started with nothing to draw on goes live when a client attaches.
  on('session.attach', async ($, e, next) => {
    if (!isLive) {
      isLive = true
      await goLive($)
    }

    return next(e)
  })

  // /clear, /resume and /branch reset $.state: copy the feed back from $.store.
  on('classic.SessionStart', { source: ['clear', 'resume', 'fork'] }, async ($, e, next) => {
    const result = await next(e)

    if (isLive) {
      const now = await $.clock.now()
      sessionId = await $.session.id()
      await update($, since, () => now)
      await loadFeed($)
      await reload($)
    }

    return result
  }).catch(($, e, next) => next(e))

  // The session root moves on /cd, the worktree tools and a host's directory
  // change: the pane follows it to that root's store and feed. Claude Code
  // raises no CwdChanged for /cd, so each of these looks; turn.start catches
  // whatever moved it otherwise before the turn's first tool call.
  on('command.run', { command: 'cd' }, async ($, e, next) => {
    const result = await next(e)
    await followRoot($)

    return result
  }).catch(($, e, next) => next(e))

  on('tool.call', { tool: ['EnterWorktree', 'ExitWorktree'] }, async ($, e, next) => {
    const result = await next(e)
    await followRoot($)

    return result
  }).catch(($, e, next) => next(e))

  on('classic.CwdChanged', async ($, e, next) => {
    const result = await next(e)
    await followRoot($)

    return result
  }).catch(($, e, next) => next(e))

  on('turn.start', async ($, e, next) => {
    await followRoot($)

    return next(e)
  })

  // Observe only: a Bash command the pattern matches is a graph op. Nothing
  // but the pattern test runs before next; the store is read after it.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const name = isLive ? opNameOf(e.command, adapter.cmdRegex) : null

    if (name === null) {
      return next(e)
    }

    const t0 = await $.clock.now()
    const result = await next(e)
    const t1 = await $.clock.now()

    // Claude Code's own check found the command only reads (`cat`, `grep`,
    // `echo` with the CLI's name in it): not a graph op.
    if (result.isReadOnly === true) {
      return result
    }

    // Refused, or failed to start: it never went to the background, it failed.
    const isBackground = result.deny === undefined && result.isError !== true && (e.run_in_background === true || isBackgroundResult(result.result))
    await recordInOrder($, { t0, t1, name, command: e.command, source: 'bash', result, isBackground })

    return result
  }).catch(($, e, next) => next(e))

  // Observe only: an MCP tool under the configured prefix is a graph op too.
  on('tool.call', { tool: /^mcp__/ }, async ($, e, next) => {
    const tool = e.tool as string

    if (!isLive || adapter.mcpPrefix === '' || !tool.startsWith(adapter.mcpPrefix)) {
      return next(e)
    }

    const t0 = await $.clock.now()
    const result = await next(e)
    const t1 = await $.clock.now()

    await recordInOrder($, { t0, t1, name: tool.slice(adapter.mcpPrefix.length) || tool, command: null, source: 'mcp', result, isBackground: false })

    return result
  }).catch(($, e, next) => next(e))

  // Answers /omni only once it registered the name in a live session. A skill
  // or command of the person's called omni (register refused the name), and
  // `claude -p "/omni"` where nothing draws, get the event passed on: an
  // answer without next would run no command at all. A gate, so caught.
  on('command.run', { command: 'omni' }, async ($, e, next) => {
    if (!isLive || !isRegistered) {
      return next(e)
    }

    const arg = e.args.trim().toLowerCase()
    const wanted = TABS.find(entry => entry.tab === arg)

    if (arg === 'close') {
      await $.ui.close({ id: PANE })
      return {}
    }
    if (arg === 'clear') {
      // The pane first: a $.store that cannot take the write loses only the copy.
      await update($, feed, () => [])
      try {
        await $.store.set(feedKey(await $.session.root()), [])
      } catch (error) {
        await logError($, 'clearing the feed in $.store', error)
        return { text: `feed cleared in this pane only: $.store refused the write (${messageOf(error)})` }
      }
      return { text: 'feed cleared for this root' }
    }
    if (arg === 'reload') {
      await reload($)
    } else if (wanted !== undefined) {
      await update($, tab, () => wanted.tab)
    } else if (arg !== '' && arg !== 'focus') {
      return { text: 'usage: /omni [graph|browse|feed|queue|focus|close|clear|reload]' }
    }

    unseen = 0
    // Unfocused unless asked: the prompt keeps the keyboard while the pane
    // follows Claude's work; `/omni focus` hands the pane the keys.
    const pane = { id: PANE, title: 'omnigraph' }
    const opened = await $.ui.open(arg === 'focus' ? { ...pane, focus: true } : pane)

    return opened.isPlaced ? {} : { text: `the pane is waiting: ${opened.reason}` }
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'Pane', requestId: 'omnigraph' }, async ($, e) => {
    const el = $.ui.resolve(e)
    await read($, graphRev)
    const columns = Math.max(24, e.props.bodyColumns)
    const rows = Math.max(12, e.props.scroll.bodyRows)
    const ops = await read($, feed)
    const path = await read($, trail)
    const hub = await read($, center)
    const following = await read($, isFollowing)
    const current = await read($, tab)
    const split = graphTabRows(rows, ops.length)
    const lastOp = ops[ops.length - 1]
    const ego = egoOf(graph, hub ?? path[0] ?? null, path[path.length - 1] ?? null, lastOp?.touched ?? [], columns, split.graph, following)
    lastEgoSize.columns = columns
    lastEgoSize.rows = split.graph
    // Terminal and desktop draw a Client; elsewhere the table completes one to
    // an empty fragment, so the surface decides (the cast only types the table).
    // Props past the engine's cap would cost the whole pane: text then.
    const graphClient =
      current === 'graph' && (e.surface === 'terminal' || e.surface === 'desktop') && propsFit(ego)
        ? (el as Elements['terminal']).Client({ key: 'graph', module: './graph-view.ts', props: ego, width: columns, height: split.graph })
        : null

    unseen = 0

    return paneOf(el, {
      surface: e.surface,
      columns,
      rows,
      info: await read($, info),
      totals: totalsOf(ops, await read($, since), sessionId),
      feed: ops,
      tab: current as Tab,
      trail: path,
      isFollowing: following,
      browseLabel: await read($, browseLabel),
      queuePage: await read($, queuePage),
      notice: await read($, notice),
      graph,
      a: adapter,
      graphClient,
      ego,
      graphRows: split.graph,
      source: current === 'browse' ? await sourceOf($, path[path.length - 1]) : null,
      act: actionsOf($),
    })
  })

  // What the graph client posts: an arrow key, a click, Enter, Backspace. The
  // data comes from code, so it is checked; the answer hands the client its
  // next props at once, before the pane's own redraw.
  on('ui.message', { component: 'Pane', element: 'graph' }, async ($, e, next) => {
    const answer = await next(e)
    const data = e.data as { kind?: unknown; id?: unknown } | null
    const kind = typeof data?.kind === 'string' ? data.kind : ''
    const id = typeof data?.id === 'string' && graph.nodes.has(data.id) ? data.id : null

    if (kind === 'select' && id !== null) {
      await selectNode($, id)
    } else if (kind === 'open' && id !== null) {
      await openNode($, id)
    } else if (kind === 'back') {
      await goBack($)
    } else if (kind === 'follow') {
      await toggleFollow($)
    } else if (kind === 'browse') {
      await update($, tab, () => 'browse')
      return answer
    } else if (kind === 'tab') {
      const wanted = TABS.find(entry => entry.hotkey === (e.data as { tab?: unknown } | null)?.tab)
      if (wanted !== undefined) {
        await update($, tab, () => wanted.tab)
      }
      return answer
    } else {
      return answer
    }

    const path = await read($, trail)
    const ops = await read($, feed)
    const props = egoOf(graph, (await read($, center)) ?? path[0] ?? null, path[path.length - 1] ?? null, ops[ops.length - 1]?.touched ?? [], lastEgoSize.columns, lastEgoSize.rows, await read($, isFollowing))

    // Too big for a Client: the pane's own redraw draws the graph as text.
    return propsFit(props) ? { ...answer, props } : answer
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (!isLive || !isBandOn || e.props.hasSurvey) {
      return next(e)
    }

    const current = await read($, info)
    const ops = await read($, feed)

    if (!current?.isPresent && ops.length === 0) {
      return next(e)
    }

    const { Box, Text, Button } = $.ui.resolve(e)
    const line = bandText(current, totalsOf(ops, await read($, since), sessionId), ops[ops.length - 1])
    // What the mods after this one draw stays, under this line.
    const below = await next(e)

    return Box({
      flexDirection: 'column',
      children: [
        Box({
          flexDirection: 'row',
          columnGap: 1,
          children: [
            Text({ dimColor: true, wrap: 'truncate-end', children: [Text({ color: 'claude', children: ['◉ '] }), fit(line, Math.max(10, e.props.bodyColumns - 9))] }),
            Button({
              key: 'open',
              label: 'open',
              plain: true,
              dimColor: true,
              onPress: () => {
                unseen = 0
                $.ui.open({ id: PANE, title: 'omnigraph' }).catch(() => undefined)
              },
            }),
          ],
        }),
        below,
      ],
    })
  })
}
