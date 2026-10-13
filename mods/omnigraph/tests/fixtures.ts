import type { On, RenderSurface } from 'claude-code'
import { mock } from 'claude-code/testing'

import { SEED } from './demo-store'

export const PLUGIN = 'omnigraph'
export const ROOT = '/work'
export const STORE = '/work/graphs/dev.omni'
export const START = 1_791_500_000_000

/** session.start as an interactive terminal session raises it, and as claude -p does. */
export const SESSION = { surface: 'terminal', isInteractive: true, cwd: ROOT } as const
export const HEADLESS = { surface: null, isInteractive: false, cwd: ROOT } as const

/** The desktop app joining a session that started with nothing attached. */
export const ATTACH = { surface: 'desktop', clientId: 'desktop:default' } as const

/** /omni as the person types it at the prompt: the types require origin and presentation. */
export const runOmni = (args = '') =>
  ({ command: 'omni', args, origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 180 } }) as const

/** What the engine hands the pane's and the band's ui.render hooks, apart from the surface. */
export const PANE = {
  plugin: PLUGIN,
  component: 'Pane',
  requestId: 'omnigraph',
  viewport: { columns: 180, rows: 50 },
  props: { title: 'omnigraph', isFocused: true, bodyColumns: 80, placement: 'dock', scroll: { offset: 0, bodyRows: 44 }, view: {} },
} as const

export const BAND = {
  plugin: PLUGIN,
  component: 'AbovePrompt',
  viewport: { columns: 180, rows: 50 },
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 120, scroll: { offset: 0, bodyRows: 9 }, view: {} },
} as const

export type StoreTexts = { manifest: string; nodes: string; edges: string }

/** What the mod asked of the engine, and the files and answers the stubs give. */
export type World = {
  commands: { name: string; immediate?: true }[]
  /** Set to make $.command.register refuse, as it does for a taken name. */
  refuseCommand: string | null
  opened: { id: string; focus?: true; closeOnEscape?: true }[]
  closed: string[]
  toasts: string[]
  logs: string[]
  /** Every path the mod stat'ed or read: a headless session must leave this empty for a graph command. */
  touched: string[]
  /** Every path the mod read (not only stat'ed). */
  reads: string[]
  /** The session root `$.session.root()` answers: a worktree move changes it. */
  root: string
  files: Map<string, { text: string; mtimeMs: number }>
  dirs: Set<string>
  /** Symbolic links: a path, and where it lands (what `$.fs.stat` resolves it to). */
  links: Map<string, string>
  written: Map<string, string>
  store: Map<string, unknown>
  /** `$.store.set` rejects, as it does for a store over 4 MiB. */
  isStoreFull: boolean
  sessionId: string
  /** What the engine's own tool.call answers, and what running the tool does to the files first. */
  answer: Record<string, unknown>
  onTool: ((command: string) => void) | null
  ranTools: string[]
  /** Whether the pane is shown (ui.panes), and whether the next $.ui.open is placed. */
  isPaneShown: boolean
  isPlaced: boolean
  writes: number
  clock: ReturnType<typeof mock.clock>
}

/** Lays a store's files at `dir` (its ops log too, when given), each with a fresh mtime. */
export function layStore(world: World, texts: StoreTexts, ops?: string, dir = STORE): void {
  // A fresh mtime each write, as a real write leaves even within one millisecond.
  world.writes++
  const at = world.clock.now() + world.writes

  world.dirs.add(dir)
  world.files.set(`${dir}/manifest.json`, { text: texts.manifest, mtimeMs: at })
  world.files.set(`${dir}/nodes.jsonl`, { text: texts.nodes, mtimeMs: at })
  world.files.set(`${dir}/edges.jsonl`, { text: texts.edges, mtimeMs: at })
  if (ops !== undefined) {
    const was = world.files.get(`${dir}/ops.jsonl`)?.text ?? ''
    world.files.set(`${dir}/ops.jsonl`, { text: was + ops, mtimeMs: at })
  }
}

/** One ops-log line as the demo CLI writes it. */
export const opLine = (t: number, op: string, ok = true, ms = 652, error?: string, kind: 'mutate' | 'query' = 'mutate'): string =>
  `${JSON.stringify({ t, kind, op, ok, ms, dNodes: 0, dEdges: 0, ...(error && { error }) })}\n`

/** Where a path lands with every link in `links` followed. */
function resolved(links: Map<string, string>, path: string): string {
  for (const [from, to] of links) {
    if (path === from || path.startsWith(`${from}/`)) {
      return to + path.slice(from.length)
    }
  }

  return path
}

/**
 * Seats the stubs that answer in Claude Code's place, all before the test's
 * first call on `$`. The world starts with the demo's seed store and a git
 * checkout on `main` at /work.
 */
export function worldOf(on: On, surfaces: readonly RenderSurface[] = ['terminal'], seed: StoreTexts | null = SEED): World {
  const world: World = {
    commands: [],
    refuseCommand: null,
    opened: [],
    closed: [],
    toasts: [],
    logs: [],
    touched: [],
    reads: [],
    root: ROOT,
    files: new Map(),
    dirs: new Set([ROOT, `${ROOT}/.git`]),
    links: new Map(),
    written: new Map(),
    store: new Map(),
    isStoreFull: false,
    sessionId: 'session-1',
    answer: { result: 'ok' },
    onTool: null,
    ranTools: [],
    isPaneShown: false,
    isPlaced: true,
    writes: 0,
    clock: mock.clock(on, { now: START }),
  }

  world.files.set(`${ROOT}/.git/HEAD`, { text: 'ref: refs/heads/main\n', mtimeMs: START })
  if (seed !== null) {
    layStore(world, seed)
  }

  mock.env(on, { HOME: '/home/tester', CLAUDE_CONFIG_DIR: '/home/tester/.claude' })
  on('session.surfaces', () => ({ value: surfaces }))
  on('session.root', () => ({ value: world.root }))
  on('session.cwd', () => ({ value: world.root }))
  on('session.id', () => ({ value: world.sessionId }))
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
  on('ui.panes', () => ({
    value: world.isPaneShown ? [{ id: 'omnigraph', title: 'omnigraph', isShown: true, isFocused: false, isPlaced: true }] : [],
  }))
  on('ui.toast', ($, e) => {
    world.toasts.push(e.text)

    return { value: undefined }
  })
  on('ui.log', ($, e) => {
    world.logs.push(e.text)

    return { value: undefined }
  })
  on('store.get', ($, e) => ({ value: world.store.get(e.key) }))
  on('store.set', ($, e) => {
    if (world.isStoreFull) {
      return { deny: 'the store would be over 4 MiB' }
    }
    world.store.set(e.key, e.value)

    return { value: undefined }
  })
  on('store.keys', () => ({ value: [...world.store.keys()] }))
  on('store.delete', ($, e) => {
    world.store.delete(e.key)

    return { value: undefined }
  })
  on('fs.stat', ($, e) => {
    world.touched.push(e.path)
    const real = resolved(world.links, e.path)
    const file = world.files.get(real)
    const where = e.resolve ? { realPath: real } : {}
    if (file !== undefined) {
      return { value: { kind: 'file' as const, size: file.text.length, mtimeMs: file.mtimeMs, isLink: real !== e.path, ...where } }
    }

    return world.dirs.has(real) ? { value: { kind: 'dir' as const, size: 0, mtimeMs: 0, isLink: real !== e.path, ...where } } : { deny: `ENOENT: ${e.path}` }
  })
  on('fs.read', ($, e) => {
    world.touched.push(e.path)
    world.reads.push(e.path)
    const file = world.files.get(resolved(world.links, e.path))

    return file === undefined ? { deny: `ENOENT: ${e.path}` } : { value: file.text }
  })
  on('fs.write', ($, e) => {
    world.written.set(e.path, e.text)

    return { value: undefined }
  })

  // The engine's own answers beneath the hooks that pass their event on.
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('session.attach', ($, e) => ({ clientId: e.clientId }))
  on('classic.SessionStart', () => ({}))
  on('classic.CwdChanged', () => ({}))
  // A command beneath the mod's hooks (/cd, or a skill's /omni the mod passed on): it ran and printed nothing.
  on('command.run', () => ({ text: '' }))
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  on('tool.call', ($, e) => {
    world.ranTools.push(e.tool)
    if (world.onTool !== null && e.tool === 'Bash' && !('deny' in world.answer)) {
      world.onTool((e as { command: string }).command)
    }

    return world.answer as { result: string }
  })
  on('ui.message', () => ({}))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))

  return world
}
