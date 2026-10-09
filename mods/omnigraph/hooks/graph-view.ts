// The graph region of the pane: a Client surface module. It runs on the
// drawing thread with no `$`: it lays the props out (canvas.ts), draws the
// rows, and posts what the person did (an arrow key, a click, Enter) to the
// hooks module, whose ui.message hook moves the selection and answers with the
// next props.
import type { ClientModule, RenderElement } from 'claude-code'

import { hitTest, layoutEgo, stepFrom } from './canvas'
import type { EgoProps, Run, Step } from './canvas'

const STEPS: readonly string[] = ['up', 'down', 'left', 'right']
const TAB_KEYS: readonly string[] = ['1', '2', '3', '4']

const GraphView: ClientModule<EgoProps> = (props, surface) => {
  const { Box, Text } = surface.elements
  // 0 before the first layout: draw for the size the hooks module asked for.
  const columns = surface.columns > 0 ? surface.columns : props.columns
  const rows = surface.rows > 0 ? surface.rows : props.rows
  const layout = layoutEgo(props, columns, rows)

  surface.onKey(event => {
    if (STEPS.includes(event.key)) {
      const next = stepFrom(layout, props.selected, event.key as Step)
      if (next !== null && next !== props.selected) {
        surface.post({ kind: 'select', id: next })
      }
    } else if (event.key === 'return' || event.key === 'enter') {
      surface.post({ kind: 'open', id: props.selected })
    } else if (event.key === 'backspace' || event.key === 'h') {
      surface.post({ kind: 'back' })
    } else if (event.key === 'f') {
      surface.post({ kind: 'follow' })
    } else if (TAB_KEYS.includes(event.key)) {
      // The client holds the keys after a click: the pane's tab digits still switch tabs.
      surface.post({ kind: 'tab', tab: event.key })
    }
  })

  surface.onPointer(event => {
    if (event.type !== 'down') {
      return
    }
    const spot = hitTest(layout, event.x, event.y)
    if (spot === null) {
      return
    }
    if (spot.isMore) {
      surface.post({ kind: 'browse', id: props.center })
    } else {
      surface.post({ kind: spot.id === props.selected ? 'open' : 'select', id: spot.id })
    }
  })

  const runOf = (run: Run): RenderElement | string =>
    run.style === null
      ? run.text
      : Text({
          ...(run.style.color !== null && { color: run.style.color }),
          ...(run.style.bold && { bold: true }),
          ...(run.style.dim && { dimColor: true }),
          ...(run.style.inverse && { inverse: true }),
          ...(run.style.italic && { italic: true }),
          children: [run.text],
        })

  return Box({
    flexDirection: 'column',
    children: layout.rows.map(row => Text({ wrap: 'truncate-end', children: row.map(runOf) })),
  })
}

export default GraphView
