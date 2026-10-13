// One node's neighbourhood, picked out of the store for the graph client: its
// neighbours grouped by edge type and direction, incoming on the left and
// outgoing on the right (balanced when one side is crowded), each group cut to
// the rows there are with a `+N more` for the rest. No `$`, no I/O.
import { colorOf } from './canvas'
import type { EgoGroup, EgoNode, EgoProps } from './canvas'
import { byName, compareText, humanType, nameOf } from './graphstore'
import type { Graph } from './graphstore'
import { clean, cut } from './text'

type Draft = { type: string; dir: 'out' | 'in'; ids: string[]; side: 'left' | 'right' }

/**
 * What a name, a label or a group title keeps in the props, in cells: the
 * drawing shows 32 at most, and the engine refuses a Client whose props
 * serialize past 100,000 characters (a paragraph for a name would do it).
 */
export const PROP_TEXT_MAX = 64

const short = (text: string): string => cut(text, PROP_TEXT_MAX)

/** Past this many serialized characters the pane draws the graph as text (the engine's cap is 100,000). */
const PROPS_MAX = 90_000

/** Whether the props stay under the engine's cap for a Client, which ids of any length could still break. */
export function propsFit(props: EgoProps): boolean {
  return JSON.stringify(props).length <= PROPS_MAX
}

/** The props the graph client draws for `center`, with `selected` highlighted. */
export function egoOf(
  graph: Graph,
  center: string | null,
  selected: string | null,
  touched: readonly string[],
  columns: number,
  rows: number,
  isFollowing: boolean,
): EgoProps {
  const base = { columns, rows, isFollowing, legend: [], groups: [], nodes: [] }

  if (!graph.isPresent) {
    return { ...base, center: '', selected: '', empty: `no store at ${clean(graph.dir)}` }
  }
  if (center === null || !graph.nodes.has(center)) {
    return { ...base, center: '', selected: '', empty: graph.nodes.size === 0 ? 'the store has no nodes yet' : 'pick a node in Browse' }
  }

  const colorFor = (label: string) => colorOf(Math.max(0, graph.labels.indexOf(label)))
  const touchedSet = new Set(touched)
  const drafts = new Map<string, Draft>()
  const shown = new Set<string>([center])

  for (const edge of graph.out.get(center) ?? []) {
    addTo(drafts, 'out', edge.type, edge.to, center)
  }
  for (const edge of graph.into.get(center) ?? []) {
    addTo(drafts, 'in', edge.type, edge.from, center)
  }

  const groups = [...drafts.values()].sort((a, b) => b.ids.length - a.ids.length || compareText(a.type, b.type))
  const graphRows = rows >= 6 ? rows - 1 : rows
  const capacity = Math.max(1, graphRows)

  // Incoming left, outgoing right; a group crosses over when its side is full
  // and the other is not, or when that evens the two out.
  const load = { left: 0, right: 0 }
  for (const group of groups) {
    const natural = group.dir === 'in' ? 'left' : 'right'
    const other = natural === 'left' ? 'right' : 'left'
    const size = group.ids.length
    const crosses =
      (load[natural] + size > capacity && load[other] + size <= capacity) || load[natural] - load[other] >= size + 2
    group.side = crosses ? other : natural
    load[group.side] += size
  }

  const nodes: EgoNode[] = []
  // The colour goes by the label as the store spells it; the props carry it cut.
  const legend = new Map<string, { label: string; color: string }>()
  const nodeOf = (id: string, side: EgoNode['side'], group: number, isSelected: boolean): EgoNode => {
    const label = graph.nodes.get(id)?.label ?? '?'
    const color = colorFor(label)
    if (!legend.has(label)) {
      legend.set(label, { label: short(label), color })
    }

    return { id, name: short(nameOf(graph, id)), label: short(label), color, side, group, isSelected, isTouched: touchedSet.has(id), more: 0 }
  }

  nodes.push(nodeOf(center, 'center', -1, selected === center || selected === null))

  const outGroups: EgoGroup[] = []

  for (const side of ['left', 'right'] as const) {
    const mine = groups.filter(group => group.side === side)
    const slots = allot(
      mine.map(group => group.ids.length),
      capacity,
    )

    mine.forEach((group, i) => {
      const room = slots[i] ?? 0
      if (room <= 0) {
        return
      }
      const index = outGroups.length
      const type = short(group.type)
      outGroups.push({
        type,
        dir: group.dir,
        count: group.ids.length,
        side,
        title: `${group.dir === 'in' ? '← ' : ''}${short(humanType(group.type))}${group.ids.length > 1 ? ` ×${group.ids.length}` : ''}`,
      })
      // The selection and the op's nodes stay in view when the group is cut.
      const fits = group.ids.length <= room
      const kept = fits ? group.ids : byName(graph, group.ids, id => rank(id, selected, touchedSet)).slice(0, Math.max(0, room - 1))
      const keep = byName(graph, kept)
      for (const id of keep) {
        nodes.push(nodeOf(id, side, index, id === selected))
      }
      if (!fits) {
        nodes.push({
          id: `more:${group.dir}:${type}`,
          name: '',
          label: '',
          color: '#808080',
          side,
          group: index,
          isSelected: false,
          isTouched: false,
          more: group.ids.length - keep.length,
        })
      }
    })
  }

  return {
    ...base,
    center,
    selected: selected !== null && nodes.some(node => node.id === selected) ? selected : center,
    nodes,
    groups: outGroups,
    legend: [...legend.values()],
    empty: null,
  }

  function addTo(map: Map<string, Draft>, dir: 'out' | 'in', type: string, id: string, self: string): void {
    if (id === self || shown.has(id)) {
      return
    }
    shown.add(id)
    const key = `${dir}\u0000${type}`
    const draft = map.get(key)
    if (draft) {
      draft.ids.push(id)
    } else {
      map.set(key, { type, dir, ids: [id], side: dir === 'in' ? 'left' : 'right' })
    }
  }
}

function rank(id: string, selected: string | null, touched: Set<string>): number {
  return id === selected ? 0 : touched.has(id) ? 1 : 2
}

/**
 * Splits `capacity` rows over groups of these sizes: all of each when they fit,
 * else a share by size, one row at least while rows last.
 */
export function allot(sizes: readonly number[], capacity: number): number[] {
  const total = sizes.reduce((sum, size) => sum + size, 0)

  if (total <= capacity) {
    return [...sizes]
  }

  const slots = sizes.map(() => 0)
  let left = capacity

  // One row each, biggest groups first, while rows last.
  sizes
    .map((size, i) => ({ size, i }))
    .sort((a, b) => b.size - a.size)
    .forEach(({ i }) => {
      if (left > 0) {
        slots[i] = 1
        left--
      }
    })

  // Then the rest by size, one row at a time to the group shown least.
  while (left > 0) {
    let pick = -1
    let worst = Infinity
    sizes.forEach((size, i) => {
      const given = slots[i] ?? 0
      if (given === 0 || given >= size) {
        return
      }
      const share = given / size
      if (share < worst) {
        worst = share
        pick = i
      }
    })
    if (pick < 0) {
      break
    }
    slots[pick] = (slots[pick] ?? 0) + 1
    left--
  }

  return slots
}
