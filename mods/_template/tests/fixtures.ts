import type { On, RenderSurface } from 'claude-code'

export const PLUGIN = 'mod-template'

/** session.start as an interactive terminal session raises it, and as claude -p does. */
export const SESSION = { surface: 'terminal', isInteractive: true, cwd: '/work' } as const
export const HEADLESS = { surface: null, isInteractive: false, cwd: '/work' } as const

/** The desktop app joining a session that started with nothing attached. */
export const ATTACH = { surface: 'desktop', clientId: 'desktop:default' } as const

/** A main-loop turn of 12 s, and a subagent's turn the mod must not count. `reason` is required by the types. */
export const TURN = { turnId: 't1', answer: 'done', durationMs: 12_000, isAborted: false, reason: 'answer' } as const
export const SUBAGENT_TURN = { ...TURN, turnId: 't2', agentId: 'agent-1' } as const

/** /mod-template as the person types it at the prompt: the types require origin and presentation. */
export const RUN = {
  command: 'mod-template',
  args: '',
  origin: { kind: 'composer' },
  presentation: { isFullscreen: false, columns: 120 },
} as const

/** What the engine hands the pane's and the band's ui.render hooks, apart from the surface. */
export const PANE = {
  plugin: PLUGIN,
  component: 'Pane',
  requestId: 'mod-template',
  viewport: { columns: 120, rows: 40 },
  props: { title: 'mod-template', isFocused: true, bodyColumns: 60, placement: 'inline', scroll: { offset: 0, bodyRows: 10 }, view: {} },
} as const

export const BAND = {
  plugin: PLUGIN,
  component: 'AbovePrompt',
  viewport: { columns: 120, rows: 40 },
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 100, scroll: { offset: 0, bodyRows: 9 }, view: {} },
} as const

/** What the mod asked of the engine, recorded by the stubs below. */
export type World = {
  commands: { name: string; immediate?: true }[]
  opened: { id: string; focus?: true; closeOnEscape?: true }[]
  closed: string[]
  logs: string[]
  /** Whether the next $.ui.open is placed; false stands for a pane waiting below 144 columns. */
  isPlaced: boolean
  /** Set to make $.command.register refuse, as it does for a taken name. */
  refuseCommand: string | null
}

/**
 * Seats the stubs that answer in Claude Code's place. Every one is registered before the test's first call on
 * `$` (the kit refuses later ones), so a test changes `world` fields afterwards, not the stubs.
 */
export function worldOf(on: On, surfaces: readonly RenderSurface[] = ['terminal']): World {
  const world: World = { commands: [], opened: [], closed: [], logs: [], isPlaced: true, refuseCommand: null }

  on('session.surfaces', () => ({ value: surfaces }))
  on('command.register', ($, e) => {
    if (world.refuseCommand !== null) {
      return { deny: world.refuseCommand }
    }

    world.commands.push({ name: e.name, ...(e.immediate === true && { immediate: true as const }) })

    return { value: { command: e.name } }
  })
  on('ui.open', ($, e) => {
    world.opened.push({
      id: e.id,
      ...(e.focus === true && { focus: true as const }),
      ...(e.closeOnEscape === true && { closeOnEscape: true as const }),
    })

    return { value: world.isPlaced ? { isPlaced: true as const } : { isPlaced: false as const, reason: 'unasked below 144 columns' } }
  })
  on('ui.close', ($, e) => {
    world.closed.push(e.id)

    return { value: undefined }
  })
  on('ui.log', ($, e) => {
    world.logs.push(e.text)

    return { value: undefined }
  })

  // The engine's own answers beneath the hooks that pass their event on.
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('session.attach', ($, e) => ({ clientId: e.clientId }))
  on('turn.complete', () => ({ text: '' }))
  // A command the mod passes on: whoever owns the name (a skill, say) runs it.
  on('command.run', ($, e) => ({ text: `Claude Code ran /${e.command}` }))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))

  return world
}
