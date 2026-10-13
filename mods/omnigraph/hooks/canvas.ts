// The node-link drawing: one node's neighbourhood laid out on a character grid
// (the node in the middle, its neighbours on two arcs, grouped by edge type),
// and the arrow-key and pointer rules that move the selection over it. Plain
// data in, plain data out: graph-view.ts draws it, the tests read it. Widths
// are terminal cells: a CJK character or an emoji takes two.
import { charWidth, cut, widthOf } from './text'

/** Colours for node labels, by the label's place in the store's sorted label list. */
export const PALETTE = [
  '#5fafff',
  '#ffaf5f',
  '#87d787',
  '#d787d7',
  '#ffd75f',
  '#5fd7d7',
  '#ff8787',
  '#afaf5f',
  '#87afff',
  '#d7af87',
  '#af87ff',
  '#87d7af',
] as const

export function colorOf(index: number): string {
  return PALETTE[((index % PALETTE.length) + PALETTE.length) % PALETTE.length] ?? PALETTE[0]
}

/** One node drawn: the centre, a neighbour, or `+N more` for the rest of its group. */
export type EgoNode = {
  id: string
  name: string
  label: string
  color: string
  side: 'center' | 'left' | 'right'
  /** Index into EgoProps.groups; -1 for the centre. */
  group: number
  isSelected: boolean
  isTouched: boolean
  /** Above 0: this is the `+N more` stand-in, `id` the group's key. */
  more: number
}

/** The neighbours one edge type and direction leads to. */
export type EgoGroup = { type: string; dir: 'out' | 'in'; count: number; side: 'left' | 'right'; title: string }

/** What the graph client draws: plain data, the Client's props. */
export type EgoProps = {
  center: string
  selected: string
  nodes: EgoNode[]
  groups: EgoGroup[]
  legend: { label: string; color: string }[]
  /** The size the hooks module laid it out for, until the client knows its own. */
  columns: number
  rows: number
  isFollowing: boolean
  /** Drawn instead of a graph: no store, no node. */
  empty: string | null
}

export type Style = { color: string | null; bold: boolean; dim: boolean; inverse: boolean; italic: boolean }
export type Run = { text: string; style: Style | null }

/** Where a node landed: its glyph and the cells of its label, for hits and arrow keys. */
export type Spot = { id: string; x: number; y: number; x0: number; x1: number; isMore: boolean }

export type Layout = { rows: Run[][]; spots: Spot[] }

/** One cell; the right half of a wide character is a tail with no text of its own. */
type Cell = { ch: string; style: Style | null; isLabel: boolean; isTail: boolean }

const blank = (): Cell => ({ ch: ' ', style: null, isLabel: false, isTail: false })

const PLAIN: Style = { color: null, bold: false, dim: false, inverse: false, italic: false }
const DOT: Style = { ...PLAIN, dim: true }
const EDGE_LABEL: Style = { ...PLAIN, dim: true, italic: true }

const clamp = (n: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, n))

class Grid {
  readonly cells: Cell[][]

  constructor(
    readonly columns: number,
    readonly rows: number,
  ) {
    this.cells = Array.from({ length: rows }, () => Array.from({ length: columns }, blank))
  }

  /** Puts one character at x, two cells when it is wide; the other half of a wide one it lands on goes blank. */
  put(x: number, y: number, ch: string, style: Style | null, isLabel: boolean, isWide = false): void {
    const row = this.cells[y]
    if (row === undefined || x < 0 || x + (isWide ? 1 : 0) >= this.columns) {
      return
    }
    this.free(row, x)
    row[x] = { ch, style, isLabel, isTail: false }
    if (isWide) {
      this.free(row, x + 1)
      row[x + 1] = { ch: '', style, isLabel, isTail: true }
    }
  }

  private free(row: Cell[], x: number): void {
    if (row[x]?.isTail && x > 0) {
      row[x - 1] = blank()
    } else if (row[x + 1]?.isTail) {
      row[x + 1] = blank()
    }
  }

  /** Writes `text` from x; false (and nothing written) when a label is in the way. */
  write(x: number, y: number, text: string, style: Style | null, isLabel: boolean, force = false): boolean {
    if (!force && !this.isFree(x, y, widthOf(text))) {
      return false
    }

    let at = x
    for (const ch of text) {
      const w = charWidth(ch.codePointAt(0) ?? 0)
      const before = this.cells[y]?.[at - 1]
      if (w === 0) {
        // A combining mark joins the character before it.
        if (at > x && before !== undefined) {
          before.ch += ch
        }
        continue
      }
      if (at + w > this.columns) {
        break
      }
      this.put(at, y, ch, style, isLabel, w === 2)
      at += w
    }

    return true
  }

  isFree(x: number, y: number, width: number): boolean {
    const row = this.cells[y]
    if (row === undefined || x < 0 || x + width > this.columns) {
      return false
    }

    return row.slice(x, x + width).every(cell => !cell.isLabel)
  }

  /** A dotted line between two cells, under whatever labels are there. */
  line(x0: number, y0: number, x1: number, y1: number): void {
    const dx = Math.abs(x1 - x0)
    const dy = -Math.abs(y1 - y0)
    const sx = x0 < x1 ? 1 : -1
    const sy = y0 < y1 ? 1 : -1
    let err = dx + dy
    let x = x0
    let y = y0

    for (let guard = 0; guard < 2000; guard++) {
      const cell = this.cells[y]?.[x]
      if (cell !== undefined && !cell.isLabel && cell.ch === ' ') {
        this.put(x, y, '·', DOT, false)
      }
      if (x === x1 && y === y1) {
        return
      }
      const e2 = 2 * err
      if (e2 >= dy) {
        err += dy
        x += sx
      }
      if (e2 <= dx) {
        err += dx
        y += sy
      }
    }
  }

  runs(): Run[][] {
    return this.cells.map(row => {
      const runs: Run[] = []

      for (const cell of row) {
        const last = runs[runs.length - 1]
        if (last !== undefined && sameStyle(last.style, cell.style)) {
          last.text += cell.ch
        } else {
          runs.push({ text: cell.ch, style: cell.style })
        }
      }

      return runs
    })
  }
}

function sameStyle(a: Style | null, b: Style | null): boolean {
  if (a === null || b === null) {
    return a === b
  }

  return a.color === b.color && a.bold === b.bold && a.dim === b.dim && a.inverse === b.inverse && a.italic === b.italic
}

/** The text a node's label shows. */
export function labelText(node: EgoNode, width: number): string {
  return node.more > 0 ? `+${node.more} more` : cut(node.name, width)
}

/** Lays the props out on a grid of `columns` x `rows` cells. */
export function layoutEgo(props: EgoProps, columns: number, rows: number): Layout {
  const width = clamp(Math.floor(columns), 1, 400)
  const height = clamp(Math.floor(rows), 1, 200)
  const grid = new Grid(width, height)
  const spots: Spot[] = []

  if (props.empty !== null || props.nodes.length === 0) {
    grid.write(0, 0, cut(props.empty ?? 'nothing to draw', width), { ...PLAIN, dim: true }, true, true)

    return { rows: grid.runs(), spots }
  }

  const hasLegend = height >= 6 && props.legend.length > 0
  const graphRows = hasLegend ? height - 1 : height
  // The hub's glyph sits in the middle with its name on the row under it, so
  // every edge starts at one point and the arcs get the width.
  const hasNameRow = graphRows >= 4
  const cy = Math.max(0, Math.floor((graphRows - (hasNameRow ? 2 : 1)) / 2))
  const cx = Math.floor(width / 2)
  const nameWidth = clamp(Math.floor(width * 0.3), 6, 26)
  const center = props.nodes.find(node => node.side === 'center') ?? props.nodes[0]
  if (center === undefined) {
    return { rows: grid.runs(), spots }
  }
  const centerName = cut(center.name, clamp(width - 2, 4, nameWidth + 6))
  const centerLength = widthOf(centerName)
  const nameY = hasNameRow ? cy + 1 : cy
  const nameX0 = hasNameRow ? clamp(cx - Math.floor(centerLength / 2), 0, Math.max(0, width - centerLength)) : cx + 2
  const taken: { y: number; x0: number; x1: number }[] = [
    { y: cy, x0: cx - 1, x1: cx + 1 },
    { y: nameY, x0: nameX0 - 1, x1: nameX0 + centerLength },
  ]
  const isOpen = (y: number, x0: number, x1: number) => taken.every(span => span.y !== y || x1 < span.x0 || x0 > span.x1)

  const placed: { node: EgoNode; x: number; y: number; text: string; x0: number }[] = []

  for (const side of ['left', 'right'] as const) {
    const nodes = props.nodes.filter(node => node.side === side)
    const n = nodes.length

    nodes.forEach((node, i) => {
      const text = labelText(node, nameWidth)
      const length = widthOf(text)
      // Where the glyph goes on row y: further out towards the middle rows, an arc.
      const xAt = (y: number) => {
        const dy = (y - cy) / Math.max(1, graphRows / 2)
        const arc = 0.25 + 0.75 * Math.sqrt(Math.max(0, 1 - dy * dy))
        if (side === 'right') {
          const outer = Math.max(cx + 3, width - 3 - length)
          return clamp(Math.round(cx + 3 + (outer - cx - 3) * arc), cx + 2, outer)
        }
        const outer = Math.min(cx - 3, length + 1)
        return clamp(Math.round(cx - 3 - (cx - 3 - outer) * arc), outer, cx - 2)
      }
      const spanAt = (y: number) => {
        const x = xAt(y)
        return side === 'right' ? { x, x0: x + 2, lo: x, hi: x + 1 + length } : { x, x0: x - 1 - length, lo: x - 1 - length, hi: x }
      }
      const ideal = n === 1 ? cy : clamp(Math.round(((i + 0.5) * graphRows) / n - 0.5), 0, graphRows - 1)

      // The nearest row to the ideal one where the glyph and the label fit.
      for (let d = 0; d < graphRows * 2; d++) {
        const y = ideal + (d % 2 === 0 ? d / 2 : -(d + 1) / 2)
        if (y < 0 || y >= graphRows) {
          continue
        }
        const span = spanAt(y)
        if (isOpen(y, span.lo - 1, span.hi + 1)) {
          taken.push({ y, x0: span.lo, x1: span.hi })
          placed.push({ node, x: span.x, y, text, x0: span.x0 })
          return
        }
      }
    })
  }

  // Edges first, from the hub's glyph to each node's; the labels then write over them.
  for (const spot of placed) {
    const isRight = spot.node.side === 'right'
    grid.line(isRight ? cx + 1 : cx - 1, cy, isRight ? spot.x - 1 : spot.x + 1, spot.y)
  }

  grid.put(cx, cy, '◉', { ...PLAIN, bold: true, color: center.color }, true)
  grid.write(nameX0, nameY, centerName, { ...PLAIN, bold: true, inverse: center.isSelected }, true, true)
  spots.push({ id: center.id, x: cx, y: cy, x0: cx - 1, x1: cx + 1, isMore: false })
  spots.push({ id: center.id, x: cx, y: nameY, x0: nameX0, x1: nameX0 + centerLength - 1, isMore: false })

  for (const spot of placed) {
    const { node } = spot
    const glyph = node.more > 0 ? '…' : node.isTouched ? '◆' : '●'
    const nameStyle: Style = { ...PLAIN, dim: node.more > 0, bold: node.isTouched, inverse: node.isSelected }
    const glyphStyle: Style = { ...PLAIN, color: node.color, bold: node.isTouched }
    const length = widthOf(spot.text)

    grid.put(spot.x, spot.y, glyph, glyphStyle, true)
    grid.write(spot.x0, spot.y, spot.text, nameStyle, true, true)
    spots.push({ id: node.id, x: spot.x, y: spot.y, x0: Math.min(spot.x, spot.x0), x1: Math.max(spot.x, spot.x0 + length - 1), isMore: node.more > 0 })
  }

  // One label per group, near the middle of its middle edge.
  props.groups.forEach((group, index) => {
    const members = placed.filter(spot => spot.node.group === index).sort((a, b) => a.y - b.y)
    const middle = members[Math.floor((members.length - 1) / 2)]
    if (middle === undefined) {
      return
    }
    const text = cut(group.title, Math.max(4, Math.floor(width / 3)))
    const length = widthOf(text)
    const midX = Math.round((cx + middle.x) / 2)
    const midY = Math.round((cy + middle.y) / 2)
    const x = clamp(midX - Math.floor(length / 2), 0, Math.max(0, width - length))

    // Off the hub's two rows, with a cell of air on each side.
    for (const y of [midY, midY - 1, midY + 1, midY - 2, midY + 2]) {
      const lo = Math.max(0, x - 1)
      const room = Math.min(width, x + length + 1) - lo
      if (y >= 0 && y < graphRows && y !== cy && y !== nameY && grid.isFree(lo, y, room) && grid.write(x, y, text, EDGE_LABEL, true)) {
        break
      }
    }
  })

  if (hasLegend) {
    let x = 0
    for (const entry of props.legend) {
      const text = `● ${entry.label}`
      const length = widthOf(text)
      if (x + length > width) {
        break
      }
      grid.put(x, height - 1, '●', { ...PLAIN, color: entry.color }, true)
      grid.write(x + 1, height - 1, ` ${entry.label}`, { ...PLAIN, dim: true }, true, true)
      x += length + 2
    }
  }

  return { rows: grid.runs(), spots }
}

export type Step = 'up' | 'down' | 'left' | 'right'

/**
 * The node an arrow key moves the selection to: the nearest one in a cone that
 * way (a row counts as two columns, as a cell is twice as tall as wide), else
 * the nearest one that way at all; null when there is none.
 */
export function stepFrom(layout: Layout, from: string, step: Step): string | null {
  const here = layout.spots.find(spot => spot.id === from) ?? layout.spots[0]

  if (here === undefined) {
    return null
  }

  const [ux, uy] = step === 'up' ? [0, -1] : step === 'down' ? [0, 1] : step === 'left' ? [-1, 0] : [1, 0]
  let best: { id: string; score: number } | null = null

  for (const spot of layout.spots) {
    if (spot.id === here.id || spot.isMore) {
      continue
    }
    const dx = spot.x - here.x
    const dy = (spot.y - here.y) * 2
    const along = dx * ux + dy * uy
    const across = Math.abs(dx * uy) + Math.abs(dy * ux)
    if (along <= 0) {
      continue
    }
    const score = Math.hypot(dx, dy) + (across > along ? 1000 : 0)
    if (best === null || score < best.score) {
      best = { id: spot.id, score }
    }
  }

  return best?.id ?? null
}

/** The node (or `+N more`) under a cell, or null. */
export function hitTest(layout: Layout, x: number, y: number): Spot | null {
  return layout.spots.find(s => s.y === y && x >= s.x0 - 1 && x <= s.x1 + 1) ?? null
}

/** A drawing's text, row by row: what a test or a log reads. */
export function textOf(layout: Layout): string[] {
  return layout.rows.map(row => row.map(run => run.text).join('').replace(/\s+$/, ''))
}
