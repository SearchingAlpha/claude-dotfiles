import type { HerdRun, HerdSession, HerdSnapshot, HerdTrack, On, RenderSurface } from 'claude-code'

export const PLUGIN = 'herd'

/** 2026-10-09 12:00 UTC: the same calendar day in every timezone the tests run in. */
export const NOW = Date.UTC(2026, 9, 9, 12, 0, 0)
export const MIN = 60_000
export const HOUR = 3_600_000

export const HOME = '/home/user'
export const LOGS = `${HOME}/logs`
export const SOURCES = `${HOME}/.config/herd/sources.json`

/** session.start as an interactive terminal session raises it, and as claude -p does. */
export const SESSION = { surface: 'terminal', isInteractive: true, cwd: '/work' } as const
export const HEADLESS = { surface: null, isInteractive: false, cwd: '/work' } as const

/** The desktop app joining a session that started with nothing attached. */
export const ATTACH = { surface: 'desktop', clientId: 'desktop:default' } as const

/** This session's id: the pane offers no Ping for it. */
export const SELF = '11111111-1111-4111-8111-111111111111'

/** The id the session goes on under after a /clear. */
export const AFTER_CLEAR = '22222222-2222-4222-8222-222222222222'

/** A herd at a path long enough that a 160-character cut would drop the reason. */
export const LONG_BIN = `/work/${'deep/'.repeat(30)}bin/herd`

/** /herd as the person types it at the prompt: the types require origin and presentation. */
export const RUN = {
  command: 'herd',
  args: '',
  origin: { kind: 'composer' },
  presentation: { isFullscreen: false, columns: 120 },
} as const

/** What the engine hands the pane's and the band's ui.render hooks, apart from the surface. */
export const PANE = {
  plugin: PLUGIN,
  component: 'Pane',
  requestId: 'herd',
  viewport: { columns: 160, rows: 40 },
  props: { title: 'Herd', isFocused: true, bodyColumns: 140, placement: 'inline', scroll: { offset: 0, bodyRows: 30 }, view: {} },
} as const

export const BAND = {
  plugin: PLUGIN,
  component: 'AbovePrompt',
  viewport: { columns: 200, rows: 40 },
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 190, scroll: { offset: 0, bodyRows: 9 }, view: {} },
} as const

// ------------------------------------------------------------------ herd snapshot, as it prints

const track = (t: Partial<HerdTrack> & Pick<HerdTrack, 'name' | 'status'>): HerdTrack => ({
  repo: 'demo-repo',
  goal: null,
  sessionId: null,
  owner: null,
  isLive: false,
  reviewId: null,
  reviewState: null,
  checks: null,
  checkedAt: null,
  reviewUrl: null,
  lastActivity: null,
  warnings: [],
  conflicts: 0,
  isBlocked: false,
  waitingFor: null,
  isStale: false,
  ...t,
})

/** shepherd's three tracks as herd joins them: parser-fix's owner waits on a permission prompt. */
export const TRACKS: HerdTrack[] = [
  track({
    name: 'parser-fix',
    status: 'active',
    goal: 'Fix the parser',
    sessionId: 'aaaaaaaa-0000-4000-8000-000000000001',
    owner: 'busy',
    isLive: true,
    lastActivity: NOW - 5 * MIN,
    isBlocked: true,
    waitingFor: 'permission prompt',
  }),
  track({
    name: 'docs',
    status: 'review',
    goal: 'Write the docs',
    sessionId: 'aaaaaaaa-0000-4000-8000-000000000002',
    owner: 'idle',
    isLive: true,
    reviewId: '6',
    reviewState: 'open',
    checks: 'passing',
    checkedAt: NOW - 3 * HOUR,
    reviewUrl: 'https://example.com/pr/6',
    lastActivity: NOW - 2 * HOUR,
    warnings: ['3 behind origin/main'],
  }),
  track({
    name: 'old-idea',
    status: 'active',
    goal: 'An idea nobody works on',
    sessionId: 'aaaaaaaa-0000-4000-8000-000000000003',
    owner: 'ended',
    lastActivity: NOW - 72 * HOUR,
    warnings: ['owner session ended'],
    isStale: true,
  }),
]

const session = (s: Partial<HerdSession> & Pick<HerdSession, 'sessionId' | 'name' | 'state' | 'status'>): HerdSession => ({
  shortId: null,
  kind: 'interactive',
  waitingFor: null,
  startedAt: null,
  cwd: '/work',
  isLive: true,
  isBlocked: false,
  track: null,
  ...s,
})

/** herd's live sessions: one waits on you, one background session works. */
export const SESSIONS: HerdSession[] = [
  session({
    sessionId: 'aaaaaaaa-0000-4000-8000-000000000001',
    name: 'demo-1',
    state: 'blocked',
    status: 'waiting',
    waitingFor: 'permission prompt',
    startedAt: NOW - HOUR,
    cwd: '/work/demo-repo-parser-fix',
    isBlocked: true,
    track: 'demo-repo/parser-fix',
  }),
  session({ sessionId: 'aaaaaaaa-0000-4000-8000-000000000002', name: 'demo-2', state: 'done', status: 'idle', startedAt: NOW - 3 * HOUR, track: 'demo-repo/docs' }),
  session({ sessionId: SELF, name: 'me', state: 'working', status: 'busy', startedAt: NOW - 10 * MIN }),
  session({ sessionId: 'bbbbbbbb-0000-4000-8000-000000000001', name: 'nightly report', kind: 'background', state: 'done', status: 'idle', startedAt: NOW - 20 * HOUR }),
  session({ sessionId: 'bbbbbbbb-0000-4000-8000-000000000002', name: 'long refactor', kind: 'background', state: 'working', status: 'busy', startedAt: NOW - 30 * MIN }),
]

const facts = (results: number, turns: number, cost: number | null, killed = 0, spawned = killed) => ({
  results,
  turns,
  cost,
  denied: 0,
  subagents: { spawned, completed: spawned - killed, failed: 0, killed, stopped: 0, pending: 0 },
})

const stream = (name: string, ageMs: number, v: Pick<HerdRun, 'verdict' | 'tone' | 'why' | 'detail' | 'facts'>): HerdRun => {
  const path = `${LOGS}/${name}-2026-10-09.jsonl`

  return { key: `${path}@${NOW - ageMs}`, source: 'Nightly', kind: 'stream', name, path, size: 900, mtimeMs: NOW - ageMs, ...v }
}

/** A busy night's runs as herd judges them: one finished, one whose subagent was killed, a weekly text log. */
export const RUNS: HerdRun[] = [
  stream('nightly-deep-dive', 5 * HOUR, {
    verdict: 'unfinished',
    tone: 'fail',
    why: 'unfinished: 1 subagent killed',
    detail: 'success · 20 turns · ≈$2.42 · 0 denied · subagents 0/1 done · 1 killed',
    facts: facts(1, 20, 2.42, 1),
  }),
  stream('nightly-report', 6 * HOUR, { verdict: 'ok', tone: 'ok', why: 'ok', detail: 'success · 7 turns · ≈$0.48 · 0 denied', facts: facts(1, 7, 0.48) }),
  {
    key: `${LOGS}/weekly-2026-10-05.log@${NOW - 96 * HOUR}`,
    source: 'Weekly',
    kind: 'line',
    name: 'weekly',
    path: `${LOGS}/weekly-2026-10-05.log`,
    size: 160,
    mtimeMs: NOW - 96 * HOUR,
    verdict: 'ok',
    tone: 'ok',
    why: '[09:43] === Weekly run done; a_rc=0 b_rc=0 ===',
    detail: '',
    facts: null,
  },
]

/** `herd snapshot --json` with no sources file set up. */
export const SNAPSHOT: HerdSnapshot = {
  schema: 3,
  at: NOW,
  tookMs: 312,
  host: 'devbox',
  tracksOn: true,
  repos: [{ name: 'demo-repo', primary: '/work/demo-repo' }],
  tracks: TRACKS,
  tracksError: null,
  tracksMissing: false,
  sessionsOn: true,
  sessions: SESSIONS,
  sessionsError: null,
  sessionsMissing: false,
  runs: [],
  sources: [],
  sourcesFile: null,
  sourcesError: null,
  acked: [],
}

/** The same with a sources file and a busy night in it. */
export const NIGHT: HerdSnapshot = {
  ...SNAPSHOT,
  runs: RUNS,
  sources: [
    { label: 'Nightly', kind: 'stream', glob: `${LOGS}/nightly-*.jsonl`, error: null, count: 3 },
    { label: 'Weekly', kind: 'line', glob: `${LOGS}/weekly-*.log`, error: null, count: 1 },
  ],
  sourcesFile: SOURCES,
}

/** The key of the night's failure: what Ack sends to `herd ack`. */
export const FAILED_KEY = `${LOGS}/nightly-deep-dive-2026-10-09.jsonl@${NOW - 5 * HOUR}`

/** `shepherd inbox --json` run as the person: one handoff addressed to "user". */
export const INBOX = [{ id: 'a1b2c3', at: Math.floor((NOW - 15 * MIN) / 1000), type: 'handoff', from: 'docs', to: 'user', text: 'Review is open, please look\nmore detail', ref: null }]

/** How a peer's message reaches `session.receive`: the engine's envelope around the sender's text. */
export const PEER_ENVELOPE =
  '<cross-session-message from="uds:/tmp/cc-socks/1110052.sock" from-name="cwd-14" from-mode="prompting">\nStatus check from the person\n</cross-session-message>'

/** A subagent of this session handing its report back to the main conversation. */
export const HAND_BACK =
  'Another Claude session sent a message: <agent-message from="a0fc1234abcd5678">\n[Subagent hand-back] The text below is the final report of a subagent this session delegated to.\n</agent-message>'

// ------------------------------------------------------------------ the stub world

/** What the mod asked of the engine, recorded by the stubs below; change these fields in a test, not the stubs. */
export type World = {
  commands: { name: string; immediate?: true }[]
  opened: { id: string; focus?: true; closeOnEscape?: true }[]
  closed: string[]
  logs: string[]
  statuses: (string | undefined)[]
  toasts: string[]
  processes: { argv: readonly string[]; cwd?: string; env?: Record<string, string> }[]
  sent: { to: string; text: string }[]
  store: Map<string, unknown>
  /** What `$.store.set` answers: null stores, a string refuses with it (a full store, say). */
  storeDeny: string | null
  /** What `$.command.register` answers for /herd: null registers it, a string refuses with it. */
  commandDeny: string | null
  /** What `$.session.id()` answers; /clear moves it on. */
  selfId: string
  /**
   * What `herd snapshot` prints, stamped with the clock's time. 'missing': a
   * herd that is not installed; 'missing-long': the same at a long absolute
   * path, as the engine words it; 'broken': one that exits 1.
   */
  snapshot: unknown
  /** How many times `herd snapshot` answered. */
  reads: number
  /** The keys of each `herd ack`; a successful one also lands in the snapshot's `acked`, as herd keeps it. */
  acks: string[][]
  /** What `herd ack` answers: null acks, a string fails with it on stderr. */
  ackDeny: string | null
  inbox: unknown[]
  isPlaced: boolean
  isDelivered: boolean
  /** While set, `herd snapshot` answers only once it resolves: a refresh held mid-read. */
  gate: Promise<void> | null
}

/**
 * Seats the stubs that answer in Claude Code's place. Every one is registered
 * before the test's first call on `$` (the kit refuses later ones), so a test
 * changes `world` fields afterwards, not the stubs.
 */
export function worldOf(on: On, surfaces: readonly RenderSurface[] = ['terminal']): World {
  const world: World = {
    commands: [],
    opened: [],
    closed: [],
    logs: [],
    statuses: [],
    toasts: [],
    processes: [],
    sent: [],
    store: new Map(),
    storeDeny: null,
    commandDeny: null,
    selfId: SELF,
    snapshot: SNAPSHOT,
    reads: 0,
    acks: [],
    ackDeny: null,
    inbox: INBOX,
    isPlaced: true,
    isDelivered: true,
    gate: null,
  }
  const out = (stdout: string, exitCode = 0, stderr = '') => ({
    value: { exitCode, stdout, stderr, isStdoutTruncated: false, isStderrTruncated: false },
  })

  on('session.surfaces', () => ({ value: surfaces }))
  on('session.id', () => ({ value: world.selfId }))
  on('env.get', ($, e) => ({ value: e.name === 'HOME' ? HOME : undefined }))
  on('command.register', ($, e) => {
    if (world.commandDeny !== null) {
      return { deny: world.commandDeny }
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
  on('ui.status', ($, e) => {
    world.statuses.push(e.text)

    return { value: undefined }
  })
  on('ui.toast', ($, e) => {
    world.toasts.push(e.text)

    return { value: undefined }
  })
  on('store.get', ($, e) => ({ value: world.store.get(e.key) }))
  on('store.set', ($, e) => {
    if (world.storeDeny !== null) {
      return { deny: world.storeDeny }
    }
    world.store.set(e.key, JSON.parse(JSON.stringify(e.value)))

    return { value: undefined }
  })
  on('process.run', async ($, e) => {
    if (world.gate !== null && e.argv[1] === 'snapshot') {
      await world.gate
    }
    world.processes.push({ argv: e.argv, ...(e.init?.cwd !== undefined && { cwd: e.init.cwd }), ...(e.init?.env !== undefined && { env: e.init.env }) })

    const [bin, verb] = e.argv

    if (verb === 'snapshot' || verb === 'ack') {
      if (world.snapshot === 'missing') {
        return { deny: `spawn ${bin} ENOENT` }
      }
      if (world.snapshot === 'missing-long') {
        return { deny: `${LONG_BIN} failed to start: ENOENT: no such file or directory, posix_spawn '${LONG_BIN}'` }
      }
      if (world.snapshot === 'broken') {
        return out('', 1, 'Traceback (most recent call last): herd crashed')
      }
    }

    switch (verb) {
      case 'snapshot': {
        const s = world.snapshot as Record<string, unknown>

        world.reads += 1

        // A later read is a later `at`, as herd stamps it (a stub cannot read the mock clock).
        return out(JSON.stringify(typeof s === 'object' && s !== null && 'at' in s ? { ...s, at: NOW + world.reads } : s))
      }
      case 'ack': {
        if (world.ackDeny !== null) {
          return out('', 1, world.ackDeny)
        }

        const keys = e.argv.slice(2)
        const s = world.snapshot as HerdSnapshot

        world.acks.push(keys)
        world.snapshot = { ...s, acked: [...new Set([...s.acked, ...keys.filter(k => s.runs.some(r => r.key === k))])] }

        return out(`acked ${keys.length} run${keys.length === 1 ? '' : 's'}\n`)
      }
      case 'inbox':
        return out(JSON.stringify(world.inbox))
      case 'accept':
        world.inbox = world.inbox.filter(x => (x as { id: string }).id !== e.argv[2])

        return out(`accepted ${e.argv[2] ?? ''} (logged ffeedd)\n`)
      case 'serve':
        return out('shepherd dashboard: http://127.0.0.1:7799/?t=token\n  remote machine? forward the port first\n')
      default:
        return out('', 2, `unknown ${verb ?? ''}`)
    }
  })
  on('session.send', ($, e) => {
    world.sent.push({ to: e.to, text: e.text })

    return world.isDelivered ? { isDelivered: true as const } : { isDelivered: false as const, reason: 'recipient gone' }
  })

  // The engine's own answers beneath the hooks that pass their event on.
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('session.attach', ($, e) => ({ clientId: e.clientId }))
  on('session.receive', ($, e) => ({ text: e.text }))
  // What runs /herd when the mod passes it on: the person's own skill of that name, say.
  on('command.run', ($, e) => ({ text: `core ran /${e.command}` }))
  on('classic.SessionStart', () => ({}))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))

  return world
}

/** A sources file set up, and a busy night in it. */
export function nightOf(world: World): void {
  world.snapshot = NIGHT
}
