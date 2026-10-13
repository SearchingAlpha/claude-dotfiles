// The pane's and the band's trees: header, tabs, breadcrumb, the Miller-column
// browser, the op feed, the review queue and a text graph for the surfaces
// with no Client. Built from the element table `$.ui.resolve(e)` handed out and
// a model of plain data and callbacks; no `$` here (register.ts binds it).
import type { Elements, OmniInfo, OmniOp, RenderElement, RenderSurface } from 'claude-code'

import type { EgoProps } from './canvas'
import {
  byName,
  conflictsOf,
  deltaOf,
  feedRow,
  fit,
  fitStart,
  humanType,
  msOf,
  nameOf,
  padEnd,
  plural,
  queueOf,
  totalsLine,
} from './graphstore'
import type { Adapter, Graph, Totals } from './graphstore'
import { clean, cleanLines, widthOf } from './text'

export type Tab = 'graph' | 'browse' | 'feed' | 'queue'

export const TABS: readonly { tab: Tab; label: string; hotkey: string }[] = [
  { tab: 'graph', label: 'Graph', hotkey: '1' },
  { tab: 'browse', label: 'Browse', hotkey: '2' },
  { tab: 'feed', label: 'Feed', hotkey: '3' },
  { tab: 'queue', label: 'Queue', hotkey: '4' },
]

type Table = Elements[RenderSurface]

/** What a press asks for; register.ts binds each to `$`. */
export type Actions = {
  tab: (tab: Tab) => void
  follow: () => void
  /** A new browse path, its last node the selection. */
  pick: (trail: string[]) => void
  label: (label: string) => void
  back: () => void
  page: (page: number) => void
  exportPatch: (id: string) => void
}

export type PaneModel = {
  surface: RenderSurface
  columns: number
  rows: number
  info: OmniInfo | null
  totals: Totals
  feed: readonly OmniOp[]
  tab: Tab
  trail: readonly string[]
  isFollowing: boolean
  browseLabel: string | null
  queuePage: number
  notice: string | null
  graph: Graph
  a: Adapter
  /** The graph client, built where its module path is read (register.ts); null where the surface has none. */
  graphClient: RenderElement | null
  ego: EgoProps
  /** The rows the graph region takes. */
  graphRows: number
  /** The selection's source lines, when its node names a readable path:line. */
  source: { path: string; line: number; text: string } | null
  act: Actions
}

const clamp = (n: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, n))

/** How the Graph tab splits its rows: the graph, the Miller columns, the feed strip. */
export function graphTabRows(rows: number, feedLength: number): { graph: number; miller: number; feed: number } {
  const feed = clamp(feedLength, 1, 4)
  const left = Math.max(6, rows - 3 - feed - 1)
  const miller = left >= 22 ? clamp(Math.floor(left * 0.38), 7, 12) : 0
  const graph = clamp(left - miller - (miller > 0 ? 1 : 0), 6, 30)

  return { graph, miller, feed }
}

// ------------------------------------------------------------------ the pane

export function paneOf(el: Table, m: PaneModel): RenderElement {
  const { Box } = el
  const body: RenderElement[] = [headerOf(el, m), tabsOf(el, m), crumbOf(el, m)]

  if (m.info?.problem) {
    body.push(el.Text({ color: 'warning', wrap: 'truncate-end', children: [fit(m.info.problem, m.columns)] }))
  }

  if (m.tab === 'graph') {
    const split = graphTabRows(m.rows, m.feed.length)
    body.push(graphOf(el, m))
    if (split.miller > 0) {
      body.push(ruleOf(el, m.columns, 'browse'))
      body.push(millerOf(el, m, split.miller))
    }
    body.push(ruleOf(el, m.columns, 'feed'))
    body.push(...feedRowsOf(el, m, split.feed, false))
  } else if (m.tab === 'browse') {
    const detail = detailOf(el, m)
    body.push(browseBarOf(el, m))
    body.push(millerOf(el, m, clamp(m.rows - 4 - detail.rows, 6, 40)))
    body.push(...detail.elements)
  } else if (m.tab === 'feed') {
    body.push(...feedRowsOf(el, m, Math.max(3, m.rows - 3), true))
  } else {
    body.push(...queueTabOf(el, m))
  }

  return Box({ flexDirection: 'column', children: body })
}

function headerOf(el: Table, m: PaneModel): RenderElement {
  const { Box, Text } = el
  const where = [m.info?.name ?? 'no store', m.info?.branch].filter((part): part is string => !!part).join(' · ')
  const right = totalsLine(m.totals)
  const leftWidth = Math.max(10, m.columns - widthOf(right) - 2)

  return Box({
    flexDirection: 'row',
    justifyContent: 'space-between',
    children: [
      Text({
        wrap: 'truncate-end',
        children: [Text({ color: 'claude', children: ['◉ '] }), Text({ bold: true, children: ['omnigraph'] }), fit(` · ${where}`, leftWidth - 11)],
      }),
      Text({
        children: [
          `${plural(m.totals.ops, 'op')} · ${deltaOf(m.totals.dNodes, 'n')} ${deltaOf(m.totals.dEdges, 'e')} · `,
          Text({ ...(m.totals.errors > 0 ? { color: 'error' } : { dimColor: true }), children: [`${m.totals.errors} ×`] }),
        ],
      }),
    ],
  })
}

function tabsOf(el: Table, m: PaneModel): RenderElement {
  const { Box, Button, Text } = el
  const hint =
    m.tab === 'graph'
      ? 'click the graph to browse with ↑↓←→'
      : m.tab === 'browse'
        ? 'Enter opens · h goes back'
        : m.tab === 'feed'
          ? `${plural(m.feed.length, 'op')} kept`
          : 'export writes a patch; nothing is applied'

  return Box({
    flexDirection: 'row',
    columnGap: 2,
    children: [
      ...TABS.map(({ tab, label, hotkey }) =>
        Button({ key: `tab-${tab}`, label, hotkey, plain: true, dimColor: m.tab !== tab, onPress: () => m.act.tab(tab) }),
      ),
      Button({
        key: 'follow',
        label: m.isFollowing ? 'following ops' : 'follow ops',
        hotkey: 'f',
        plain: true,
        dimColor: !m.isFollowing,
        onPress: () => m.act.follow(),
      }),
      Text({ dimColor: true, wrap: 'truncate-end', children: [hint] }),
    ],
  })
}

/** The browse path as one line: `Offline editing & sync › contains › Conflict toast hides unsynced edits`. */
export function crumbText(graph: Graph, trail: readonly string[]): string {
  const parts: string[] = []

  trail.forEach((id, i) => {
    const before = trail[i - 1]
    if (before !== undefined) {
      parts.push(relationWord(graph, before, id))
    }
    parts.push(nameOf(graph, id))
  })

  return parts.join(' › ')
}

function relationWord(graph: Graph, from: string, to: string): string {
  const out = (graph.out.get(from) ?? []).find(edge => edge.to === to)

  if (out) {
    return humanType(out.type)
  }

  const back = (graph.into.get(from) ?? []).find(edge => edge.from === to)

  return back ? `← ${humanType(back.type)}` : '·'
}

function crumbOf(el: Table, m: PaneModel): RenderElement {
  const { Text } = el
  const mark = m.isFollowing ? ' ● live' : ' ○ browsing'
  const text = m.trail.length > 0 ? crumbText(m.graph, m.trail) : m.info?.isPresent ? 'nothing selected yet' : 'waiting for a store'

  return Text({
    wrap: 'truncate-end',
    children: [
      fitStart(text, Math.max(8, m.columns - widthOf(mark))),
      Text({ ...(m.isFollowing ? { color: 'success' } : { dimColor: true }), children: [mark] }),
    ],
  })
}

function ruleOf(el: Table, columns: number, title: string): RenderElement {
  return el.Text({ dimColor: true, wrap: 'truncate-end', children: [`── ${title} ${'─'.repeat(Math.max(0, columns - title.length - 4))}`] })
}

// ----------------------------------------------------------------- the graph

function graphOf(el: Table, m: PaneModel): RenderElement {
  if (m.graphClient !== null) {
    return m.graphClient
  }

  return el.Box({ flexDirection: 'column', children: graphTextOf(m.ego, m.columns, m.graphRows).map(line => el.Text({ wrap: 'truncate-end', children: [line] })) })
}

/** The graph as lines of text, for a surface with no Client: the centre, then a line per group. */
export function graphTextOf(ego: EgoProps, columns: number, rows: number): string[] {
  if (ego.empty !== null) {
    return [fit(ego.empty, columns)]
  }

  const center = ego.nodes.find(node => node.side === 'center')
  const lines = [fit(`◉ ${center?.name ?? ego.center}`, columns)]

  ego.groups.forEach((group, index) => {
    const members = ego.nodes.filter(node => node.group === index)
    const names = members.map(node => (node.more > 0 ? `+${node.more} more` : `${node.isSelected ? '▶ ' : ''}${node.name}`))
    lines.push(fit(`  ${group.dir === 'in' ? '←' : '→'} ${group.title.replace(/^← /, '')}: ${names.join(', ')}`, columns))
  })

  return lines.slice(0, Math.max(1, rows))
}

// ------------------------------------------------------------ the browser

type Line =
  | { kind: 'head'; text: string }
  | { kind: 'item'; key: string; id: string; name: string; isOnTrail: boolean; onPress: () => void }

type Column = { title: string; lines: Line[] }

/** The browser's columns: one label's nodes, then each trail node's relations. */
export function columnsOf(m: Pick<PaneModel, 'graph' | 'trail' | 'browseLabel' | 'act'>): Column[] {
  const { graph, trail } = m
  const first = trail[0]
  const label = m.browseLabel ?? (first !== undefined ? graph.nodes.get(first)?.label : undefined) ?? graph.labels[0]

  if (label === undefined) {
    return []
  }

  const ids = graph.byLabel.get(label) ?? []
  const columns: Column[] = [
    {
      title: `${label} ${ids.length}`,
      lines: ids.map(id => ({
        kind: 'item',
        key: `m0:${id}`,
        id,
        name: nameOf(graph, id),
        isOnTrail: id === first,
        onPress: () => m.act.pick([id]),
      })),
    },
  ]

  // A first column of another label than the trail's starts a new trail.
  const isOwnTrail = first !== undefined && graph.nodes.get(first)?.label === label

  if (!isOwnTrail) {
    return columns
  }

  trail.forEach((id, k) => {
    const next = trail[k + 1]
    const groups = new Map<string, { head: string; ids: Set<string> }>()

    for (const edge of graph.out.get(id) ?? []) {
      const key = `→\u0000${edge.type}\u0000${graph.nodes.get(edge.to)?.label ?? '?'}`
      addLine(groups, key, `→ ${humanType(edge.type)} · ${graph.nodes.get(edge.to)?.label ?? '?'}`, edge.to)
    }
    for (const edge of graph.into.get(id) ?? []) {
      const key = `←\u0000${edge.type}\u0000${graph.nodes.get(edge.from)?.label ?? '?'}`
      addLine(groups, key, `← ${humanType(edge.type)} · ${graph.nodes.get(edge.from)?.label ?? '?'}`, edge.from)
    }

    const lines: Line[] = []
    let g = 0
    for (const group of groups.values()) {
      lines.push({ kind: 'head', text: `${group.head} ${group.ids.size}` })
      for (const target of byName(graph, group.ids)) {
        lines.push({
          kind: 'item',
          key: `m${k + 1}:${g}:${target}`,
          id: target,
          name: nameOf(graph, target),
          isOnTrail: target === next,
          onPress: () => m.act.pick([...trail.slice(0, k + 1), target]),
        })
      }
      g++
    }
    if (lines.length === 0) {
      lines.push({ kind: 'head', text: 'no relations' })
    }
    columns.push({ title: nameOf(graph, id), lines })
  })

  return columns
}

/** Adds `id` to its group once: a Set, as a hub's group can hold thousands. */
function addLine(groups: Map<string, { head: string; ids: Set<string> }>, key: string, head: string, id: string): void {
  const group = groups.get(key)

  if (group) {
    group.ids.add(id)
  } else {
    groups.set(key, { head, ids: new Set([id]) })
  }
}

/** The lines of a column that fit in `rows`, kept around the trail's line, with `↑ n more` / `↓ n more`. */
export function windowOf(lines: readonly Line[], rows: number): { lines: Line[]; above: number; below: number } {
  if (lines.length <= rows) {
    return { lines: [...lines], above: 0, below: 0 }
  }

  const room = Math.max(1, rows - 2)
  const on = lines.findIndex(line => line.kind === 'item' && line.isOnTrail)
  const start = clamp((on < 0 ? 0 : on) - Math.floor(room / 2), 0, lines.length - room)

  return { lines: lines.slice(start, start + room), above: start, below: lines.length - start - room }
}

function millerOf(el: Table, m: PaneModel, rows: number): RenderElement {
  const { Box, Button, Text } = el
  const all = columnsOf(m)

  if (all.length === 0) {
    return Text({ dimColor: true, children: [m.info?.isPresent ? 'the store has no nodes' : 'no store to browse'] })
  }

  const count = clamp(Math.floor((m.columns + 1) / 24), 1, 4)
  const shown = all.slice(-count)
  const width = Math.max(10, Math.floor((m.columns - (shown.length - 1)) / shown.length))
  const itemRows = Math.max(1, rows - 1)

  return Box({
    flexDirection: 'row',
    columnGap: 1,
    children: shown.map((column, c) => {
      const view = windowOf(column.lines, itemRows)
      const children: RenderElement[] = [Text({ bold: true, wrap: 'truncate-end', children: [fit(column.title, width)] })]

      if (view.above > 0) {
        children.push(Text({ dimColor: true, children: [`↑ ${view.above} more`] }))
      }
      for (const line of view.lines) {
        if (line.kind === 'head') {
          children.push(Text({ dimColor: true, wrap: 'truncate-end', children: [fit(line.text, width)] }))
        } else {
          children.push(
            Button({
              key: line.key,
              label: fit(`${line.isOnTrail ? '▶ ' : '  '}${line.name}`, width),
              plain: true,
              dimColor: !line.isOnTrail && c < shown.length - 1,
              onPress: line.onPress,
            }),
          )
        }
      }
      if (view.below > 0) {
        children.push(Text({ dimColor: true, children: [`↓ ${view.below} more`] }))
      }

      return Box({ key: `col-${c}`, flexDirection: 'column', width, children })
    }),
  })
}

function browseBarOf(el: Table, m: PaneModel): RenderElement {
  const { Box, Button, Text } = el
  const first = m.trail[0]
  const current = m.browseLabel ?? (first !== undefined ? m.graph.nodes.get(first)?.label : undefined) ?? m.graph.labels[0] ?? ''
  const children: RenderElement[] = []

  // Mobile has no Select (its table completes one to an empty fragment): the
  // surface decides, and the cast only types the table for it.
  if (m.surface !== 'mobile' && m.graph.labels.length > 0) {
    children.push(
      (el as Elements['terminal']).Select({
        key: 'label',
        label: 'type',
        value: current,
        options: m.graph.labels.map(label => ({ value: label, label: `${label} ${m.graph.byLabel.get(label)?.length ?? 0}` })),
        onSelect: value => m.act.label(value),
      }),
    )
  } else {
    children.push(Text({ dimColor: true, children: [`type ${current}`] }))
  }
  children.push(Button({ key: 'back', label: 'back', hotkey: 'h', plain: true, dimColor: true, onPress: () => m.act.back() }))

  return Box({ flexDirection: 'row', columnGap: 2, children })
}

function detailOf(el: Table, m: PaneModel): { elements: RenderElement[]; rows: number } {
  const { Text } = el
  const id = m.trail[m.trail.length - 1]
  const node = id === undefined ? undefined : m.graph.nodes.get(id)

  if (id === undefined || node === undefined) {
    return { elements: [], rows: 0 }
  }

  const skip = new Set([...m.a.nameKeys, m.a.statusKey, m.a.sourceKey])
  const props = Object.entries(node.props)
    .filter(([key]) => !skip.has(key))
    .slice(0, 6)
    .map(([key, value]) => fit(clean(`${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`), m.columns))
  const elements: RenderElement[] = [
    ruleOf(el, m.columns, 'detail'),
    Text({ bold: true, wrap: 'truncate-end', children: [fit(node.name, m.columns)] }),
    Text({ dimColor: true, wrap: 'truncate-end', children: [fit(clean(`${node.id} · ${node.label}${node.status ? ` · ${node.status}` : ''}`), m.columns)] }),
    ...props.map(line => Text({ wrap: 'truncate-end', children: [line] })),
  ]
  let rows = 3 + props.length

  if (m.source !== null) {
    elements.push(Text({ dimColor: true, wrap: 'truncate-end', children: [fit(clean(`${m.source.path}:${m.source.line}`), m.columns)] }))
    elements.push(el.Code({ source: cleanLines(m.source.text), path: m.source.path, startLine: Math.max(1, m.source.line - 2), wrap: 'truncate-end' }))
    rows += 1 + m.source.text.split('\n').length
  }

  return { elements, rows }
}

// ------------------------------------------------------------------ the feed

function feedRowsOf(el: Table, m: PaneModel, rows: number, isFull: boolean): RenderElement[] {
  const { Text } = el

  if (m.feed.length === 0) {
    return [Text({ dimColor: true, wrap: 'truncate-end', children: [fit('no ops yet: the graph commands Claude runs show up here', m.columns)] })]
  }

  const lines: RenderElement[] = []
  const recent = m.feed.slice(-Math.max(1, rows))
  // As wide as the longest op shown, so the deltas line up close to it, and
  // the deltas as wide as the widest (`~1n` adds to some).
  const opWidth = clamp(Math.max(...recent.map(op => widthOf(op.op))), 12, Math.max(12, m.columns - 30))
  const shown = recent.map(op => feedRow(op, opWidth))
  const deltaWidth = Math.max(...shown.map(row => widthOf(row.delta)))

  for (const [i, op] of recent.entries()) {
    const row = shown[i] ?? feedRow(op, opWidth)
    lines.push(
      Text({
        wrap: 'truncate-end',
        children: [
          Text({ dimColor: true, children: [row.time] }),
          Text({ color: 'claude', children: [' ▸ '] }),
          row.op,
          `  ${padEnd(row.delta, deltaWidth)}  `,
          Text({ dimColor: true, children: [row.ms] }),
          ' ',
          Text({ ...(op.isBackground ? { dimColor: true } : { color: op.ok ? 'success' : 'error' }), children: [row.mark] }),
        ],
      }),
    )
    if (isFull && !op.ok && op.error) {
      lines.push(Text({ color: 'error', dimColor: true, wrap: 'truncate-end', children: [fit(`         ${op.error}`, m.columns)] }))
    }
  }

  return isFull ? lines.slice(-Math.max(1, rows)) : lines
}

// ----------------------------------------------------------------- the queue

/** How many queued nodes one Queue page lists. */
export function queuePageSize(rows: number, conflicts: number): number {
  return Math.max(3, rows - 6 - (conflicts > 0 ? Math.min(conflicts, 5) + 1 : 0))
}

function queueTabOf(el: Table, m: PaneModel): RenderElement[] {
  const { Box, Button, Text } = el
  const queued = queueOf(m.graph, m.a)
  const conflicts = conflictsOf(m.graph, m.a)
  const size = queuePageSize(m.rows, conflicts.length)
  const pages = Math.max(1, Math.ceil(queued.length / size))
  const page = clamp(m.queuePage, 0, pages - 1)
  const lines: RenderElement[] = []

  if (m.notice !== null) {
    lines.push(Text({ color: 'success', wrap: 'truncate-end', children: [fit(clean(m.notice), m.columns)] }))
  }
  if (conflicts.length > 0) {
    lines.push(Text({ color: 'warning', wrap: 'truncate-end', children: [fit(`${plural(conflicts.length, 'open conflict')}: settle these before accepting what they name`, m.columns)] }))
    for (const id of conflicts.slice(0, 5)) {
      lines.push(Text({ wrap: 'truncate-end', children: [fit(clean(`  ! ${nameOf(m.graph, id)}  ${id}`), m.columns)] }))
    }
  }
  if (queued.length === 0) {
    lines.push(Text({ dimColor: true, children: [`no node is ${m.a.queueStatus.join(' or ') || 'queued'}`] }))

    return lines
  }

  lines.push(Text({ dimColor: true, wrap: 'truncate-end', children: [fit(`${queued.length} ${m.a.queueStatus.join('/')} · export writes one patch file each (status: ${m.a.acceptStatus})`, m.columns)] }))
  for (const id of queued.slice(page * size, page * size + size)) {
    const node = m.graph.nodes.get(id)
    lines.push(
      Box({
        flexDirection: 'row',
        columnGap: 1,
        children: [
          Button({ key: `x:${id}`, label: 'export', onPress: () => m.act.exportPatch(id) }),
          Text({ wrap: 'truncate-end', children: [Text({ dimColor: true, children: [`${node?.label ?? '?'} · `] }), fit(node?.name ?? clean(id), Math.max(8, m.columns - 24))] }),
        ],
      }),
    )
  }
  lines.push(
    Box({
      flexDirection: 'row',
      columnGap: 2,
      children: [
        Button({ key: 'prev', label: 'prev', hotkey: 'p', plain: true, dimColor: page === 0, onPress: () => m.act.page(Math.max(0, page - 1)) }),
        Button({ key: 'next', label: 'next', hotkey: 'n', plain: true, dimColor: page >= pages - 1, onPress: () => m.act.page(Math.min(pages - 1, page + 1)) }),
        Text({ dimColor: true, children: [`page ${page + 1}/${pages}`] }),
      ],
    }),
  )

  return lines
}

// ------------------------------------------------------------------ the band

/** The band's one line: `◉ omnigraph · dev.omni · main · 8 ops · +3n +2e · 0 × · last mutate create_issue ✓ 652ms`. */
export function bandText(info: OmniInfo | null, totals: Totals, last: OmniOp | undefined): string {
  const parts = [
    'omnigraph',
    info?.name,
    info?.branch,
    info?.isPresent ? `${info.nodes}n ${info.edges}e` : undefined,
    totalsLine(totals),
    last ? `last ${last.op} ${last.isBackground ? '… bg' : `${last.ok ? '✓' : '✗'} ${msOf(last.ms)}`}` : undefined,
  ]

  return parts.filter((part): part is string => !!part).join(' · ')
}
