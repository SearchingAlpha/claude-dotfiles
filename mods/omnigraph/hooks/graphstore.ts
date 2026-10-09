// omnigraph's pure half: the store format and the adapter that maps another
// producer's build onto it, reading a store, diffing two reads, naming the op a
// command or tool call is, and the words the header, band and feed draw.
// No `$` and no I/O here: register.ts reads, this file judges.
import type { OmniInfo, OmniOp, PluginOptions } from 'claude-code'

import { clean, cut, cutStart, hasControl, padCells } from './text'

/** The feed keeps this many ops per root. */
export const FEED_MAX = 50

/** How many touched ids an op keeps. */
const TOUCHED_MAX = 12

/** An op that adds or changes more nodes than this is a bulk load: the graph does not jump to it. */
const BULK_NODES = 5

/** What an op keeps of its name and of its error. */
const OP_MAX = 80
const ERROR_MAX = 200

/** An ops-log record counts for a call it started within this much of (the two clocks may differ a little). */
export const RECORD_SKEW_MS = 1000

// The defaults plugin.json declares, for a run that hands no options (tests).
// The CLI must be the command: at the start or after a separator, past shell
// keywords (`do`, `then`, `{`, `!`), wrappers (env, time, timeout, nohup, exec,
// command, nice, sudo, xargs, with their flags), VAR=value words, `bash -c '`,
// a folder, or a python interpreter and its flags. No part of it runs on past
// a separator (`[^\s;&|()]` and `[ \t]`, never `\S` or `\s`), so a long
// command costs one pass: a `\S*` would rescan the rest after every `;`.
const DEFAULT_CMD = String.raw`(?:^|[\n;&|(])[ \t]*(?:(?:do|then|else|if|elif|while|until|\{|!)[ \t]+)*(?:(?:timeout|time|env|nohup|exec|command|nice|sudo|xargs)(?:[ \t]+-[^\s;&|()]+(?:[ \t]+[^-\s;&|()][^\s;&|()]*)?)*(?:[ \t]+\d[\w.]*)?[ \t]+|[A-Za-z_]\w*=[^\s;&|()]*[ \t]+|(?:ba|z)?sh[ \t]+-[a-z]*c[ \t]+['"])*(?:(?:[^\s;&|()]*/)?python3?(?:\.\d+)?[ \t]+(?:-[XW][ \t]*[^\s;&|()]+[ \t]+|-\w+[ \t]+)*)?(?:[^\s;&|()]*/)?(?:omnigraph|omni\.py)[ \t]+((?:mutate|query)[ \t]+[\w.-]+|init)\b`

/** How much of a command a pattern reads: its head, and its last lines past that. */
const MATCH_MAX = 16 * 1024

/** The userConfig options, read once: what the store is called and how its records are spelled. */
export type Adapter = {
  store: string
  storeFlag: string
  storeLabel: string
  cmdRegex: RegExp | null
  mcpPrefix: string
  manifestFile: string
  nodesFile: string
  edgesFile: string
  opsFile: string
  versionKey: string
  idKey: string
  labelKey: string
  /** '.' reads a node's properties off the record itself. */
  propsKey: string
  fromKey: string
  typeKey: string
  toKey: string
  nameKeys: string[]
  statusKey: string
  sourceKey: string
  conflictsKey: string
  queueStatus: string[]
  acceptStatus: string
  owner: string
  patchDir: string
  /** Why an option could not be used (a cmdRegex that does not compile). */
  problem: string | null
}

const textOpt = (options: PluginOptions, key: string, fallback: string): string => {
  const value = options[key]

  return typeof value === 'string' ? value.trim() : fallback
}

const wordsOf = (text: string): string[] => text.split(/\s+/).filter(word => word !== '')

export function adapterOf(options: PluginOptions): Adapter {
  const opt = (key: string, fallback: string) => textOpt(options, key, fallback)
  const [idKey = 'id', labelKey = 'label', propsKey = 'props'] = wordsOf(opt('nodeKeys', 'id label props'))
  const [fromKey = 'from', typeKey = 'type', toKey = 'to'] = wordsOf(opt('edgeKeys', 'from type to'))
  const source = opt('cmdRegex', DEFAULT_CMD)
  let cmdRegex: RegExp | null = null
  let problem: string | null = null

  try {
    cmdRegex = source === '' ? null : new RegExp(source)
  } catch (error) {
    problem = `cmdRegex does not compile: ${String(error)}`
  }

  return {
    store: opt('store', 'graphs/dev.omni') || 'graphs/dev.omni',
    storeFlag: opt('storeFlag', '--store'),
    storeLabel: opt('storeLabel', ''),
    cmdRegex,
    mcpPrefix: opt('mcpPrefix', ''),
    manifestFile: opt('manifestFile', 'manifest.json'),
    nodesFile: opt('nodesFile', 'nodes.jsonl') || 'nodes.jsonl',
    edgesFile: opt('edgesFile', 'edges.jsonl') || 'edges.jsonl',
    opsFile: opt('opsFile', 'ops.jsonl'),
    versionKey: opt('versionKey', 'version') || 'version',
    idKey,
    labelKey,
    propsKey,
    fromKey,
    typeKey,
    toKey,
    nameKeys: wordsOf(opt('nameKeys', 'name title')),
    statusKey: opt('statusKey', 'status'),
    sourceKey: opt('sourceKey', 'source'),
    conflictsKey: opt('conflictsKey', 'open_conflicts'),
    queueStatus: wordsOf(opt('queueStatus', 'proposed')),
    acceptStatus: opt('acceptStatus', 'accepted') || 'accepted',
    owner: opt('owner', ''),
    patchDir: opt('patchDir', ''),
    problem,
  }
}

// ------------------------------------------------------------------ the graph

/**
 * One node. `id` is the record's own (edges and patches name it); `label`,
 * `name` and `status` are cleaned of control characters for drawing.
 */
export type GNode = {
  id: string
  label: string
  name: string
  status: string | null
  props: Record<string, unknown>
  /** The record's line as read: a changed line is a changed node. */
  line: string
}

export type GEdge = { from: string; type: string; to: string; key: string }

export type Graph = {
  dir: string
  version: string | null
  manifest: Record<string, unknown> | null
  nodes: Map<string, GNode>
  edges: GEdge[]
  out: Map<string, GEdge[]>
  into: Map<string, GEdge[]>
  /** Labels by name, and each label's node ids by node name. */
  labels: string[]
  byLabel: Map<string, string[]>
  /** Lines that were not a record this adapter reads. */
  bad: number
  isPresent: boolean
}

export type StoreTexts = { manifest: string | null; nodes: string | null; edges: string | null }

export const NO_TEXTS: StoreTexts = { manifest: null, nodes: null, edges: null }

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const idOf = (value: unknown): string | null =>
  typeof value === 'string' && value !== '' ? value : typeof value === 'number' ? String(value) : null

/** A control character or a line separator, or white space at either end: what shownOf has to change. */
const NEEDS_CLEANING = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]|^\s|\s$/

/** A string field as a drawing shows it: control characters gone, trimmed; null when empty or not a string. */
const shownOf = (value: unknown): string | null => {
  if (typeof value !== 'string' || value === '') {
    return null
  }
  // Most fields are clean already: one test before building a new string.
  if (!NEEDS_CLEANING.test(value)) {
    return value
  }

  const text = clean(value).trim()

  return text === '' ? null : text
}

/** shownOf for values a store repeats (labels, edge types), each worked out once. */
function shownOnce(cache: Map<string, string | null>, value: unknown): string | null {
  if (typeof value !== 'string') {
    return shownOf(value)
  }

  let shown = cache.get(value)
  if (shown === undefined) {
    shown = shownOf(value)
    cache.set(value, shown)
  }

  return shown
}

/** A file's text without the byte-order mark an editor may have put first. */
const unmarked = (text: string | null): string | null => (text !== null && text.charCodeAt(0) === 0xfeff ? text.slice(1) : text)

/** FNV-1a over the text, 8 hex digits: a version for a store whose manifest names none. */
export function hashOf(text: string): string {
  let hash = 0x811c9dc5

  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }

  return hash.toString(16).padStart(8, '0')
}

/** Case-blind order with code points breaking ties: cheaper than localeCompare on a big label. */
export function compareText(x: string, y: string): number {
  const a = x.toLowerCase()
  const b = y.toLowerCase()

  return a < b ? -1 : a > b ? 1 : x < y ? -1 : x > y ? 1 : 0
}

export function emptyGraph(dir: string): Graph {
  return parseGraph(dir, NO_TEXTS, adapterOf({}))
}

const push = <K, V>(map: Map<K, V[]>, key: K, value: V) => {
  const list = map.get(key)

  if (list) {
    list.push(value)
  } else {
    map.set(key, [value])
  }
}

export function parseGraph(dir: string, raw: StoreTexts, a: Adapter): Graph {
  const texts = { manifest: unmarked(raw.manifest), nodes: unmarked(raw.nodes), edges: unmarked(raw.edges) }
  let manifest: Record<string, unknown> | null = null
  let bad = 0

  if (texts.manifest !== null) {
    try {
      const parsed: unknown = JSON.parse(texts.manifest)
      manifest = isRecord(parsed) ? parsed : null
    } catch {
      manifest = null
    }
    if (manifest === null) {
      bad++
    }
  }

  const nodes = new Map<string, GNode>()
  const repeated = new Map<string, string | null>()

  for (const line of (texts.nodes ?? '').split('\n')) {
    if (line.trim() === '') {
      continue
    }
    try {
      const record: unknown = JSON.parse(line)
      const id = isRecord(record) ? idOf(record[a.idKey]) : null
      if (!isRecord(record) || id === null) {
        bad++
        continue
      }
      const props = a.propsKey === '.' ? record : isRecord(record[a.propsKey]) ? (record[a.propsKey] as Record<string, unknown>) : {}
      let name: string | null = null
      for (let k = 0; k < a.nameKeys.length && name === null; k++) {
        name = shownOf(props[a.nameKeys[k] ?? ''])
      }
      const status = a.statusKey === '' ? null : shownOf(props[a.statusKey])
      nodes.set(id, { id, label: shownOnce(repeated, record[a.labelKey]) ?? '?', name: name ?? shownOf(id) ?? '?', status, props, line })
    } catch {
      bad++
    }
  }

  const edges: GEdge[] = []
  const out = new Map<string, GEdge[]>()
  const into = new Map<string, GEdge[]>()
  const seen = new Set<string>()

  for (const line of (texts.edges ?? '').split('\n')) {
    if (line.trim() === '') {
      continue
    }
    try {
      const record: unknown = JSON.parse(line)
      const from = isRecord(record) ? idOf(record[a.fromKey]) : null
      const to = isRecord(record) ? idOf(record[a.toKey]) : null
      const stated = isRecord(record) ? idOf(record[a.typeKey]) : null
      if (from === null || to === null || stated === null) {
        bad++
        continue
      }
      const type = shownOnce(repeated, stated) ?? '?'
      const key = `${from}\u0000${type}\u0000${to}`
      if (seen.has(key)) {
        continue
      }
      seen.add(key)
      const edge = { from, type, to, key }
      edges.push(edge)
      push(out, from, edge)
      push(into, to, edge)
    } catch {
      bad++
    }
  }

  // Each label's ids by name, case-blind: the sort keys made once, not per comparison.
  const keyed = new Map<string, { id: string; key: string; name: string }[]>()

  for (const node of nodes.values()) {
    push(keyed, node.label, { id: node.id, key: node.name.toLowerCase(), name: node.name })
  }

  const byLabel = new Map<string, string[]>()
  for (const [label, entries] of keyed) {
    entries.sort((x, y) => (x.key < y.key ? -1 : x.key > y.key ? 1 : x.name < y.name ? -1 : x.name > y.name ? 1 : x.id < y.id ? -1 : x.id > y.id ? 1 : 0))
    byLabel.set(
      label,
      entries.map(entry => entry.id),
    )
  }

  const stated = manifest?.[a.versionKey]
  const version =
    typeof stated === 'string' || typeof stated === 'number'
      ? shownOf(String(stated))
      : texts.nodes !== null || texts.edges !== null
        ? hashOf(`${texts.nodes ?? ''}\u0000${texts.edges ?? ''}`)
        : null

  return {
    dir,
    version,
    manifest,
    nodes,
    edges,
    out,
    into,
    // Code-point order, not the locale's: a label's colour is its place here.
    labels: [...byLabel.keys()].sort((x, y) => (x < y ? -1 : x > y ? 1 : 0)),
    byLabel,
    bad,
    isPresent: texts.manifest !== null || texts.nodes !== null,
  }
}

export function nameOf(graph: Graph, id: string): string {
  return graph.nodes.get(id)?.name ?? clean(id)
}

/**
 * `ids` in case-blind name order (code points break ties), after `first` when
 * given: the keys made once, not per comparison, as a hub can have thousands.
 */
export function byName(graph: Graph, ids: Iterable<string>, first: (id: string) => number = () => 0): string[] {
  const keyed: { id: string; rank: number; key: string; name: string }[] = []

  for (const id of ids) {
    const name = nameOf(graph, id)
    keyed.push({ id, rank: first(id), key: name.toLowerCase(), name })
  }
  keyed.sort((x, y) => x.rank - y.rank || (x.key < y.key ? -1 : x.key > y.key ? 1 : x.name < y.name ? -1 : x.name > y.name ? 1 : 0))

  return keyed.map(entry => entry.id)
}

export function degreeOf(graph: Graph, id: string): number {
  return (graph.out.get(id)?.length ?? 0) + (graph.into.get(id)?.length ?? 0)
}

// ------------------------------------------------------------------- the diff

export type GraphDiff = {
  addedNodes: string[]
  removedNodes: string[]
  changedNodes: string[]
  addedEdges: GEdge[]
  removedEdges: GEdge[]
}

export function diffGraphs(before: Graph, after: Graph): GraphDiff {
  const addedNodes: string[] = []
  const changedNodes: string[] = []

  for (const [id, node] of after.nodes) {
    const was = before.nodes.get(id)
    if (was === undefined) {
      addedNodes.push(id)
    } else if (was.line !== node.line) {
      changedNodes.push(id)
    }
  }

  const removedNodes = [...before.nodes.keys()].filter(id => !after.nodes.has(id))
  const beforeKeys = new Set(before.edges.map(edge => edge.key))
  const afterKeys = new Set(after.edges.map(edge => edge.key))

  return {
    addedNodes,
    removedNodes,
    changedNodes,
    addedEdges: after.edges.filter(edge => !beforeKeys.has(edge.key)),
    removedEdges: before.edges.filter(edge => !afterKeys.has(edge.key)),
  }
}

export function touchedOf(diff: GraphDiff): string[] {
  const ids = new Set<string>([...diff.addedNodes, ...diff.changedNodes])

  for (const edge of [...diff.addedEdges, ...diff.removedEdges]) {
    ids.add(edge.from)
    ids.add(edge.to)
  }

  return [...ids].slice(0, TOUCHED_MAX)
}

/**
 * Where the graph goes after an op: the first new (else changed) node in file
 * order, drawn around its busiest neighbour; else the first new edge, drawn
 * around its busier end with the other one highlighted (an epic and the issue
 * just added to it).
 */
export function focusOf(diff: GraphDiff, graph: Graph): OmniOp['focus'] {
  // A bulk load or rebuild (a seed, a compile that rewrote many records) is not one place to go.
  if (diff.addedNodes.length + diff.changedNodes.length > BULK_NODES) {
    return null
  }

  const isThere = (id: string) => graph.nodes.has(id)
  const fresh = diff.addedNodes.find(isThere) ?? diff.changedNodes.find(isThere)

  if (fresh !== undefined) {
    const around = [...(graph.out.get(fresh) ?? []).map(edge => edge.to), ...(graph.into.get(fresh) ?? []).map(edge => edge.from)]
      .filter(id => id !== fresh && isThere(id))
      .sort((x, y) => degreeOf(graph, y) - degreeOf(graph, x))

    return { center: around[0] ?? fresh, selected: fresh }
  }

  const edge = [...diff.addedEdges, ...diff.removedEdges].find(e => isThere(e.from) && isThere(e.to) && e.from !== e.to)

  if (edge === undefined) {
    return null
  }

  return degreeOf(graph, edge.from) >= degreeOf(graph, edge.to)
    ? { center: edge.from, selected: edge.to }
    : { center: edge.to, selected: edge.from }
}

// ------------------------------------------------------------ naming the ops

const squeeze = (text: string, max = 60): string => clean(text).replace(/\s+/g, ' ').trim().slice(0, max)

/** `text` cut to `max` characters, back to a space, tab or newline so no word is cut in two. */
function headOf(text: string, max: number): string {
  for (let i = max; i > 0; i--) {
    const c = text.charCodeAt(i)
    if (c === 32 || c === 9 || c === 10) {
      return text.slice(0, i)
    }
  }

  return text.slice(0, max)
}

/**
 * The op a Bash command is: the first capture group that matched, else the
 * match; null when it is not one. The pattern reads at most MATCH_MAX of a
 * long command's head and as much of its last lines (a heredoc, then the
 * graph command): a pattern that backtracks costs a bounded time, not one
 * that grows with the square of the command.
 */
export function opNameOf(command: string, pattern: RegExp | null): string | null {
  if (pattern === null) {
    return null
  }

  let match: RegExpExecArray | null

  if (command.length <= MATCH_MAX) {
    match = pattern.exec(command)
  } else {
    match = pattern.exec(headOf(command, MATCH_MAX))
    // The tail starts at a newline, so a pattern anchored at `^` does not take a cut line for a command.
    const tail = command.slice(-MATCH_MAX)
    const at = tail.indexOf('\n')
    if (match === null && at >= 0) {
      match = pattern.exec(tail.slice(at))
    }
  }

  if (match === null) {
    return null
  }

  for (const group of match.slice(1)) {
    if (group) {
      return squeeze(group) || 'op'
    }
  }

  return squeeze(match[0]) || 'op'
}

const unquote = (word: string): string =>
  (word.startsWith('"') && word.endsWith('"')) || (word.startsWith("'") && word.endsWith("'")) ? word.slice(1, -1) : word

const escapeRe = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** A path the mod cannot spell out itself: a variable, a command substitution, a glob, another user's home. */
const isUnresolvable = (path: string): boolean => /[$`*?]/.test(path) || /^~[^/]/.test(path)

/** The value of `--store dir` or `--store=dir` in a command, unquoted; null when absent. */
export function storeFlagOf(command: string, flag: string): string | null {
  if (flag === '') {
    return null
  }

  const match = new RegExp(`(?:^|\\s)${escapeRe(flag)}(?:=|\\s+)("[^"]*"|'[^']*'|[^\\s;&|)]+)`).exec(command)

  return match?.[1] === undefined ? null : unquote(match[1])
}

/** The folder of a leading `cd <dir> &&` (or `;`, or inside a `(`), unquoted; null when the command does not start with one. */
export function cdPrefixOf(command: string): string | null {
  // No two white-space runs side by side: a command that opens with a long one stays one pass.
  const match = /^\s*(?:\(\s*)?cd\s+("[^"]*"|'[^']*'|[^\s;&|()]+)\s*(?:&&|;)/.exec(command)

  return match?.[1] === undefined ? null : unquote(match[1])
}

/** `rel` under `base`, `.` and `..` folded; an absolute `rel` (or `~/...` with a home) stands alone. */
export function joinPath(base: string, rel: string, home: string | null = null): string {
  const expanded = home !== null && (rel === '~' || rel.startsWith('~/')) ? home + rel.slice(1) : rel
  const isAbsolute = expanded.startsWith('/') || /^[A-Za-z]:[\\/]/.test(expanded)
  const full = isAbsolute ? expanded : `${base}/${expanded}`
  const kept: string[] = []

  for (const part of full.split(/[\\/]+/)) {
    if (part === '' || part === '.') {
      continue
    }
    if (part === '..') {
      if (kept.length > 0 && !/^[A-Za-z]:$/.test(kept[kept.length - 1] ?? '')) {
        kept.pop()
      }
      continue
    }
    kept.push(part)
  }

  const joined = kept.join('/')

  return full.startsWith('/') ? `/${joined}` : joined
}

/** True when `path` is `dir` or under it. */
export function isUnder(path: string, dir: string): boolean {
  return path === dir || path.startsWith(dir.endsWith('/') ? dir : `${dir}/`)
}

/**
 * Where a command's store is: its store flag under its cwd (and leading cd),
 * else the configured store under the root; null when the flag or the cd
 * names a path only the shell can spell (`"$STORE"`, a glob).
 */
export function storeDirOf(root: string, cwd: string, command: string | null, a: Adapter, home: string | null): string | null {
  const flagged = command === null ? null : storeFlagOf(command, a.storeFlag)

  if (flagged === null) {
    return joinPath(root, a.store, home)
  }

  const cd = command === null ? null : cdPrefixOf(command)

  if (isUnresolvable(flagged) || (cd !== null && isUnresolvable(cd))) {
    return null
  }

  return joinPath(cd === null ? cwd : joinPath(cwd, cd, home), flagged, home)
}

export function baseName(path: string): string {
  const parts = path.split(/[\\/]+/).filter(part => part !== '')

  return parts[parts.length - 1] ?? path
}

/** The store's name: the storeLabel option, the manifest's name, or the folder's name. */
export function storeNameOf(graph: Graph, a: Adapter): string {
  return clean(a.storeLabel) || shownOf(graph.manifest?.name) || clean(baseName(graph.dir))
}

/** A producer's record of one op, and its place in the lines it was picked from. */
export type OpRecord = { op: string | null; ok: boolean | null; ms: number | null; error: string | null; index: number }

/**
 * The producer's own record of a call, from the ops-log lines written since
 * the mod last read the log (and those no op took yet): the newest one that
 * started while the call ran (`from`..`to`, give or take RECORD_SKEW_MS) and,
 * for a command, whose op the command names. Null when none is: the call wrote
 * no record (it failed before logging, or ran something else), and an older
 * record never stands in.
 */
export function opRecordOf(lines: readonly string[], from: number, to: number, command: string | null): OpRecord | null {
  const words = command === null ? null : new Set(command.split(/[^\w.-]+/))

  for (let i = lines.length - 1; i >= 0; i--) {
    let record: unknown
    try {
      record = JSON.parse(lines[i] ?? '')
    } catch {
      continue
    }
    if (!isRecord(record) || typeof record.t !== 'number' || record.t < from - RECORD_SKEW_MS || record.t > to + RECORD_SKEW_MS) {
      continue
    }
    const op = typeof record.op === 'string' ? record.op : null
    if (words !== null && (op === null || !words.has(op))) {
      continue
    }
    const kind = typeof record.kind === 'string' ? record.kind : null
    const named = op === null ? null : kind === 'mutate' || kind === 'query' ? `${kind} ${op}` : op

    return {
      op: named === null ? null : squeeze(named, OP_MAX) || null,
      ok: typeof record.ok === 'boolean' ? record.ok : null,
      ms: typeof record.ms === 'number' && record.ms >= 0 ? Math.round(record.ms) : null,
      error: typeof record.error === 'string' ? squeeze(record.error, ERROR_MAX) || null : null,
      index: i,
    }
  }

  return null
}

/** The first line of a failed tool's error text, kept short. */
export function errorLineOf(text: string | undefined): string | null {
  const line = (text ?? '').split('\n').find(part => part.trim() !== '' && !/^error: exit code/i.test(part.trim()))

  return line === undefined ? null : squeeze(line, 120) || null
}

/** Whether a Bash call went to the background (asked, on a timeout, or Ctrl+B): its result names the task. */
export function isBackgroundResult(result: unknown): boolean {
  return isRecord(result) && typeof result.backgroundTaskId === 'string' && result.backgroundTaskId !== ''
}

// --------------------------------------------------------------- the feed

export function pushRing<T>(list: readonly T[], item: T, max = FEED_MAX): T[] {
  return [...list, item].slice(-max)
}

const numberOr = <T>(value: unknown, fallback: T): number | T => (typeof value === 'number' && Number.isFinite(value) ? value : fallback)

/** One stored op made safe to draw (another session or an older build may have written it); null when it is not one. */
export function opOf(value: unknown): OmniOp | null {
  if (!isRecord(value) || typeof value.t !== 'number' || typeof value.op !== 'string' || typeof value.ok !== 'boolean') {
    return null
  }

  const focus = isRecord(value.focus) ? value.focus : null
  const op: OmniOp = {
    t: value.t,
    op: squeeze(value.op, OP_MAX) || 'op',
    source: value.source === 'mcp' ? 'mcp' : 'bash',
    store: typeof value.store === 'string' ? clean(value.store) : null,
    ok: value.ok,
    ms: Math.max(0, numberOr(value.ms, 0)),
    dNodes: numberOr(value.dNodes, null),
    dEdges: numberOr(value.dEdges, null),
    touched: Array.isArray(value.touched) ? value.touched.filter((id): id is string => typeof id === 'string').slice(0, TOUCHED_MAX) : [],
    focus:
      focus !== null && typeof focus.center === 'string' && typeof focus.selected === 'string' ? { center: focus.center, selected: focus.selected } : null,
    error: typeof value.error === 'string' ? squeeze(value.error, ERROR_MAX) : null,
    version: typeof value.version === 'string' ? clean(value.version) : null,
  }

  if (typeof value.session === 'string') {
    op.session = value.session
  }
  if (value.isBackground === true) {
    op.isBackground = true
  }
  if (typeof value.changed === 'number' && value.changed > 0) {
    op.changed = value.changed
  }

  return op
}

export function feedOf(value: unknown): OmniOp[] {
  return Array.isArray(value) ? value.map(opOf).filter((op): op is OmniOp => op !== null).slice(-FEED_MAX) : []
}

export type Totals = { ops: number; dNodes: number; dEdges: number; errors: number }

/** The ops since `since` that `session` recorded (all of them when it is null): the feed holds every session's on the root. */
export function totalsOf(feed: readonly OmniOp[], since: number, session: string | null = null): Totals {
  const totals = { ops: 0, dNodes: 0, dEdges: 0, errors: 0 }

  for (const op of feed) {
    if (op.t < since || (session !== null && op.session !== session)) {
      continue
    }
    totals.ops++
    totals.dNodes += op.dNodes ?? 0
    totals.dEdges += op.dEdges ?? 0
    totals.errors += op.ok ? 0 : 1
  }

  return totals
}

// ---------------------------------------------------------------- the words

export const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

/** `+3n`, `-2e`, `+0n`; a dot when there was no store to diff. */
export function deltaOf(n: number | null, unit: string): string {
  if (n === null) {
    return `·${unit}`
  }

  return `${n < 0 ? '-' : '+'}${Math.abs(n)}${unit}`
}

export function msOf(ms: number): string {
  if (ms < 1000) {
    return `${Math.round(ms)}ms`
  }
  if (ms < 60_000) {
    return `${(ms / 1000).toFixed(1)}s`
  }

  return `${Math.round(ms / 60_000)}m`
}

const pad2 = (n: number): string => String(n).padStart(2, '0')

/** HH:MM:SS, local time. */
export function clockOf(ms: number): string {
  const d = new Date(ms)

  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

/** An edge type as the views say it: `owned_by` and `OWNED_BY` both read `owned by`. */
export function humanType(type: string): string {
  const spaced = clean(type).replace(/_+/g, ' ').trim()

  return spaced === spaced.toUpperCase() ? spaced.toLowerCase() : spaced
}

/** `text` cut to `width` cells with an ellipsis (a wide character takes two). */
export const fit = cut

/** `text` cut from the start, keeping its end: `…c › Offline editing`. */
export const fitStart = cutStart

export const padEnd = padCells

/** The header's right half: `8 ops · +3n +2e · 0 ×`. */
export function totalsLine(totals: Totals): string {
  return `${plural(totals.ops, 'op')} · ${deltaOf(totals.dNodes, 'n')} ${deltaOf(totals.dEdges, 'e')} · ${totals.errors} ×`
}

/** One feed row: `16:13:53 ▸ mutate create_issue  +1n +0e  652ms ✓`; `~1n` when it changed nodes, `bg …` when it went to the background. */
export function feedRow(op: OmniOp, opWidth: number): { time: string; op: string; delta: string; ms: string; mark: string } {
  return {
    time: clockOf(op.t),
    op: padEnd(fit(op.op, opWidth), opWidth),
    delta: `${deltaOf(op.dNodes, 'n')} ${deltaOf(op.dEdges, 'e')}${op.changed ? ` ~${op.changed}n` : ''}`,
    ms: (op.isBackground ? 'bg' : msOf(op.ms)).padStart(6),
    mark: op.isBackground ? '…' : op.ok ? '✓' : '✗',
  }
}

// ---------------------------------------------------------------- the store

export function queueOf(graph: Graph, a: Adapter): string[] {
  if (a.queueStatus.length === 0) {
    return []
  }

  const wanted = new Set(a.queueStatus)
  const ids: string[] = []

  for (const label of graph.labels) {
    for (const id of graph.byLabel.get(label) ?? []) {
      const status = graph.nodes.get(id)?.status
      if (status !== null && status !== undefined && wanted.has(status)) {
        ids.push(id)
      }
    }
  }

  return ids
}

export function conflictsOf(graph: Graph, a: Adapter): string[] {
  const listed = a.conflictsKey === '' ? undefined : graph.manifest?.[a.conflictsKey]

  return Array.isArray(listed) ? listed.filter((id): id is string => typeof id === 'string') : []
}

export function infoOf(graph: Graph, a: Adapter, branch: string | null, problems: readonly (string | null)[]): OmniInfo {
  const notes = [...problems, a.problem, graph.bad > 0 ? `${plural(graph.bad, 'line')} not read` : null].filter(
    (note): note is string => note !== null && note !== '',
  )

  return {
    dir: graph.dir,
    name: storeNameOf(graph, a),
    branch,
    version: graph.version,
    nodes: graph.nodes.size,
    edges: graph.edges.length,
    labels: graph.labels.length,
    isPresent: graph.isPresent,
    queued: queueOf(graph, a).length,
    conflicts: conflictsOf(graph, a).length,
    problem: notes.length > 0 ? clean(notes.join('; ')) : null,
  }
}

/** `ref: refs/heads/main` reads `main`; a detached HEAD its short hash. */
export function branchOfHead(text: string): string | null {
  const head = text.trim()
  const ref = /^ref:\s*refs\/heads\/(.+)$/.exec(head)

  if (ref?.[1] !== undefined) {
    return clean(ref[1])
  }

  return /^[0-9a-f]{7,64}$/i.test(head) ? head.slice(0, 7) : null
}

/** The folder a worktree's `.git` file points at (`gitdir: <path>`). */
export function gitDirOf(text: string): string | null {
  const match = /^gitdir:\s*(.+)$/m.exec(text)

  return match?.[1] === undefined ? null : match[1].trim()
}

/** A node's first `path:line` source, as the sourceKey property holds it (a string or a list). */
export function sourceRefOf(node: GNode, a: Adapter): { path: string; line: number } | null {
  if (a.sourceKey === '') {
    return null
  }

  const value = node.props[a.sourceKey]
  const first = Array.isArray(value) ? value.find(item => typeof item === 'string') : value

  if (typeof first !== 'string' || hasControl(first)) {
    return null
  }

  const match = /^(.+?):(\d+)(?:-\d+)?$/.exec(first.trim())

  return match?.[1] !== undefined ? { path: match[1], line: Number(match[2]) } : { path: first.trim(), line: 1 }
}

/**
 * A file name for an id: what is safe in a path, the rest dashed, never `.`,
 * `..` or a hidden name; with a hash of the id when it had to change, so `a:b`
 * and `a-b` do not overwrite one another.
 */
export function slugOf(id: string, fallback = 'node'): string {
  const safe = id.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^[-.]+|-+$/g, '').slice(0, 120)

  return safe === id ? safe : `${(safe || fallback).slice(0, 111)}-${hashOf(id)}`
}

/**
 * A YAML double-quoted string: JSON's escapes, and `\u` escapes for what JSON
 * leaves raw but YAML may not hold raw (DEL and C1, U+FFFE and U+FFFF) or
 * reads as a line break inside the quotes (NEL, U+2028, U+2029), so the value
 * reads back as the record holds it.
 */
const yamlQuoted = (text: string): string =>
  JSON.stringify(text).replace(/[\u007f-\u009f\u2028\u2029\ufffe\uffff]/g, ch => `\\u${ch.charCodeAt(0).toString(16).padStart(4, '0')}`)

/** A YAML mapping key: as it is when plain, quoted otherwise. */
const yamlKey = (key: string): string => (/^[A-Za-z_][A-Za-z0-9_-]*$/.test(key) ? key : yamlQuoted(key))

/** A YAML comment line: no line break of any kind (clean takes U+2028 and U+2029 too), nothing a reader refuses. */
const yamlComment = (text: string): string => clean(text).replace(/[\ufffe\uffff]/g, ' ')

/**
 * The overlay a Queue export writes: the record's id, the accepted status and
 * the owner, as YAML. Values are quoted and escaped, and the comment lines
 * hold no line break, so a name or a label cannot add records of its own.
 */
export function patchOf(node: GNode, a: Adapter, storeName: string, version: string | null, at: string): string {
  const lines = [
    yamlComment(`# omnigraph patch for ${storeName}${version === null ? '' : ` ${version}`}, exported ${at}`),
    yamlComment(`# was: ${node.status ?? 'no status'} (${node.label}) ${JSON.stringify(node.name)}`),
    `- id: ${yamlQuoted(node.id)}`,
    `  ${yamlKey(a.statusKey || 'status')}: ${yamlQuoted(a.acceptStatus)}`,
  ]

  if (a.owner !== '') {
    lines.push(`  owner: ${yamlQuoted(a.owner)}`)
  }

  return `${lines.join('\n')}\n`
}
