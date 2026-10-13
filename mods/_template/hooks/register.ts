// mod-template: the smallest mod worth copying. It follows mods/CLAUDE.md:
// inert where nothing draws (claude -p, cron, the SDK), observe-only (every
// hook on an engine event passes it on with next, none can refuse anything),
// the values a drawing reads kept in $.state, and a pane opened only because
// the person asked for it.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, On, PluginOptions } from 'claude-code'

const PANE = 'mod-template'

// `plugin` and `key` stay string literals and each atom sits in a const:
// `claude plugin validate` reads them off the source and holds them to
// types/index.d.ts.
const turns = atom({ plugin: 'mod-template', key: 'turns' } as const, 0)
const lastTurnMs = atom({ plugin: 'mod-template', key: 'lastTurnMs' } as const, null)
const isBandHidden = atom({ plugin: 'mod-template', key: 'isBandHidden' } as const, false)

/** One line of what the mod has seen: the band and the pane both draw it. */
export function summaryOf(count: number, ms: number | null): string {
  if (count === 0) {
    return 'no turns yet'
  }

  const last = ms === null ? '' : ` · last ${Math.round(ms / 1000)}s`

  return `${count} turn${count === 1 ? '' : 's'}${last}`
}

/**
 * What the mod does once the session has somewhere to draw; resolves to
 * whether /mod-template is now the mod's. `$` may be passed to a function at
 * the top level of this file, and nowhere else.
 */
async function goLive($: EngineInterface): Promise<boolean> {
  // The prompt and its band may have drawn already, while the mod was inert;
  // a module variable redraws nothing by itself, so ask for it.
  $.ui.invalidate('ui.render')

  // Last, and caught: register throws for a taken name (a skill or command of
  // the person's called mod-template), and a hook that throws is skipped whole.
  try {
    await $.command.register({ name: 'mod-template', description: 'Open the mod-template pane', immediate: true })
    return true
  } catch (error) {
    $.ui.log(`/mod-template not registered: ${String(error)}`, { to: 'debug' })
    return false
  }
}

export function register(on: On, options: PluginOptions) {
  const isBandOn = options.band !== false
  // True once the session has a surface; until then every hook only passes its
  // event on. A module variable is enough: a reload runs session.start again.
  let isLive = false
  // True once /mod-template is the mod's: registered in a live session.
  let isRegistered = false

  // A terminal session, a background one (`claude --bg`, agent view: it
  // starts on a terminal surface with nobody looking) and a desktop one go
  // live here.
  on('session.start', async ($, e, next) => {
    if ((await $.session.surfaces()).length === 0) {
      return next(e)
    }

    isLive = true
    isRegistered = await goLive($)

    return next(e)
  })

  // A session that started with nothing to draw on goes live when a client
  // attaches: a phone, the desktop app. The terminal never raises it;
  // session.start covers that one.
  on('session.attach', async ($, e, next) => {
    if (!isLive) {
      isLive = true
      isRegistered = await goLive($)
    }

    return next(e)
  })

  // Observe only: count main-loop turns (a subagent's carries agentId) and pass
  // the event on. Writing $.state redraws the band and the pane that read it.
  on('turn.complete', async ($, e, next) => {
    if (isLive && e.agentId === undefined) {
      await update($, turns, count => count + 1)
      await update($, lastTurnMs, () => e.durationMs)
    }

    return next(e)
  })

  // Answers its own command only once it registered /mod-template in a live
  // session. A skill or command of the person's with that name (register
  // refused it), and `claude -p "/mod-template"` where nothing draws, get the
  // event passed on: an answer without next would run no command at all.
  // Calling next makes it a gate, so it is caught.
  on('command.run', { command: 'mod-template' }, async ($, e, next) => {
    if (!isLive || !isRegistered) {
      return next(e)
    }

    const opened = await $.ui.open({ id: PANE, title: 'mod-template', focus: true, closeOnEscape: true })

    return opened.isPlaced ? {} : { text: `The pane is waiting: ${opened.reason}` }
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'Pane', requestId: 'mod-template' }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const isHidden = await read($, isBandHidden)
    const summary = summaryOf(await read($, turns), await read($, lastTurnMs))

    return Box({
      flexDirection: 'column',
      children: [
        Text({ children: [`This session: ${summary}`] }),
        Text({ dimColor: true, children: ['Copy mods/_template to start a mod; mods/CLAUDE.md has the rules.'] }),
        Box({
          flexDirection: 'row',
          columnGap: 2,
          children: [
            Button({
              key: 'band',
              label: isHidden ? 'Show band' : 'Hide band',
              hotkey: 'b',
              plain: true,
              onPress: () => update($, isBandHidden, was => !was),
            }),
            Button({ key: 'close', label: 'Close', hotkey: 'c', plain: true, onPress: () => $.ui.close({ id: PANE }) }),
          ],
        }),
      ],
    })
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (!isLive || !isBandOn || e.props.hasSurvey || (await read($, isBandHidden))) {
      return next(e)
    }

    const { Box, Text, Button } = $.ui.resolve(e)
    const summary = summaryOf(await read($, turns), await read($, lastTurnMs))
    // What the mods after this one draw stays, under this line.
    const below = await next(e)

    return Box({
      flexDirection: 'column',
      children: [
        Box({
          flexDirection: 'row',
          columnGap: 1,
          children: [
            Text({ dimColor: true, wrap: 'truncate-end', children: [`mod-template · ${summary}`] }),
            Button({ key: 'hide', label: 'hide', plain: true, dimColor: true, onPress: () => update($, isBandHidden, () => true) }),
          ],
        }),
        below,
      ],
    })
  })
}
