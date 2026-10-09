import type { On, RenderSurface } from 'claude-code'

export const PLUGIN = 'fleet'

/** 2026-10-09 12:00 UTC: the same calendar day in every timezone the tests run in. */
export const NOW = Date.UTC(2026, 9, 9, 12, 0, 0)
export const MIN = 60_000
export const HOUR = 3_600_000

export const HOME = '/home/user'
export const LOGS = `${HOME}/logs`
export const SOURCES = `${HOME}/fleet/sources.json`

/** session.start as an interactive terminal session raises it, and as claude -p does. */
export const SESSION = { surface: 'terminal', isInteractive: true, cwd: '/work' } as const
export const HEADLESS = { surface: null, isInteractive: false, cwd: '/work' } as const

/** The desktop app joining a session that started with nothing attached. */
export const ATTACH = { surface: 'desktop', clientId: 'desktop:default' } as const

/** This session's id: the pane offers no Ping for it. */
export const SELF = '11111111-1111-4111-8111-111111111111'

/** The id the session goes on under after a /clear. */
export const AFTER_CLEAR = '22222222-2222-4222-8222-222222222222'

/** A shepherd at a path long enough that a 160-character cut would drop the reason. */
export const LONG_BIN = `/work/${'deep/'.repeat(30)}bin/shepherd`

/** /fleet as the person types it at the prompt: the types require origin and presentation. */
export const RUN = {
  command: 'fleet',
  args: '',
  origin: { kind: 'composer' },
  presentation: { isFullscreen: false, columns: 120 },
} as const

/** What the engine hands the pane's and the band's ui.render hooks, apart from the surface. */
export const PANE = {
  plugin: PLUGIN,
  component: 'Pane',
  requestId: 'fleet',
  viewport: { columns: 160, rows: 40 },
  props: { title: 'Fleet', isFocused: true, bodyColumns: 140, placement: 'inline', scroll: { offset: 0, bodyRows: 30 }, view: {} },
} as const

export const BAND = {
  plugin: PLUGIN,
  component: 'AbovePrompt',
  viewport: { columns: 200, rows: 40 },
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 190, scroll: { offset: 0, bodyRows: 9 }, view: {} },
} as const

// ------------------------------------------------------------------ shepherd and claude agents, as they print

const s = (ms: number): number => Math.floor(ms / 1000)

/** `shepherd status --all --json`, trimmed to the fields the mod reads (the real output has more). */
export const STATUS = {
  generated_at: s(NOW),
  repos: [
    {
      name: 'demo-repo',
      primary: '/work/demo-repo',
      mode: 'observe',
      tracks: [
        {
          name: 'parser-fix',
          goal: 'Fix the parser',
          status: 'active',
          agent: { session_id: 'aaaaaaaa-0000-4000-8000-000000000001', name: 'demo-1', live: true, status: 'busy' },
          last_activity: s(NOW - 5 * MIN),
          review: null,
          warnings: [],
        },
        {
          name: 'docs',
          goal: 'Write the docs',
          status: 'review',
          agent: { session_id: 'aaaaaaaa-0000-4000-8000-000000000002', name: 'demo-2', live: true, status: 'idle' },
          last_activity: s(NOW - 2 * HOUR),
          review: { backend: 'github', id: '6', state: 'open', checks: 'passing', checked_at: s(NOW - 3 * HOUR), url: 'https://example.com/pr/6' },
          warnings: ['3 behind origin/main'],
        },
        {
          name: 'old-idea',
          goal: 'An idea nobody works on',
          status: 'active',
          agent: { session_id: 'aaaaaaaa-0000-4000-8000-000000000003', name: 'demo-3', live: false, status: 'ended' },
          last_activity: s(NOW - 72 * HOUR),
          review: null,
          warnings: ['owner session ended'],
        },
      ],
      overlaps: [],
    },
  ],
  unmanaged: [],
}

/** `claude agents --json --all`: the owner of parser-fix waits on a permission prompt. */
export const AGENTS = [
  {
    kind: 'interactive',
    pid: 101,
    status: 'waiting',
    waitingFor: 'permission prompt',
    name: 'demo-1',
    sessionId: 'aaaaaaaa-0000-4000-8000-000000000001',
    cwd: '/work/demo-repo-parser-fix',
    startedAt: NOW - HOUR,
  },
  { kind: 'interactive', pid: 102, status: 'idle', name: 'demo-2', sessionId: 'aaaaaaaa-0000-4000-8000-000000000002', cwd: '/work', startedAt: NOW - 3 * HOUR },
  { kind: 'interactive', pid: 103, status: 'busy', name: 'me', sessionId: SELF, cwd: '/work', startedAt: NOW - 10 * MIN },
  { id: 'bg000001', kind: 'background', state: 'done', name: 'nightly report', sessionId: 'bbbbbbbb-0000-4000-8000-000000000001', cwd: '/work', startedAt: NOW - 20 * HOUR },
  { id: 'bg000002', kind: 'background', state: 'working', pid: 104, status: 'busy', name: 'long refactor', sessionId: 'bbbbbbbb-0000-4000-8000-000000000002', cwd: '/work', startedAt: NOW - 30 * MIN },
]

/** `shepherd inbox --json` run as the person: one handoff addressed to "user". */
export const INBOX = [{ id: 'a1b2c3', at: s(NOW - 15 * MIN), type: 'handoff', from: 'docs', to: 'user', text: 'Review is open, please look\nmore detail', ref: null }]

// ------------------------------------------------------------------ stream-json logs, as `claude -p --output-format stream-json` writes them

const j = (event: unknown): string => JSON.stringify(event)

const stats = (started: number, completed: number, killedSystem = 0, killedParent = 0, killedUser = 0) => ({
  spawned: started,
  requested: { background: started, foreground: 0, unset: 0 },
  started_in_background: started,
  max_depth: started > 0 ? 1 : 0,
  spawned_by_subagents: 0,
  completed,
  failed: 0,
  killed: { parent: killedParent, user: killedUser, system: killedSystem },
  refused: { depth_limit: 0, concurrency_limit: 0, budget: 0 },
  by_type: started > 0 ? { 'general-purpose': started } : {},
})

const result = (fields: Record<string, unknown>): string =>
  j({
    type: 'result',
    subtype: 'success',
    is_error: false,
    num_turns: 7,
    result: 'Report written to output/report.md.',
    stop_reason: 'end_turn',
    session_id: 'cccccccc-0000-4000-8000-000000000001',
    total_cost_usd: 0.48,
    permission_denials: [],
    terminal_reason: 'completed',
    subagent_stats: stats(0, 0),
    duration_ms: 154_000,
    ...fields,
  })

const INIT = j({ type: 'system', subtype: 'init', session_id: 'cccccccc-0000-4000-8000-000000000001', tools: ['Bash', 'Read'] })
// An assistant line whose text holds the spelling of a result event, escaped as JSON escapes it.
const ASSISTANT = j({ type: 'assistant', message: { content: [{ type: 'text', text: 'printing {"type":"result"} as text' }] } })
const TOOL_RESULT = j({ type: 'user', message: { content: [{ type: 'tool_result', content: 'ok' }] } })
const BG_START = j({
  type: 'system',
  subtype: 'task_started',
  task_id: 'a155',
  description: 'Deep dive',
  subagent_type: 'general-purpose',
  is_backgrounded: true,
  task_type: 'local_agent',
})
const BASH_START = j({ type: 'system', subtype: 'task_started', task_id: 'b1', is_backgrounded: false, task_type: 'local_bash' })
const BASH_DONE = j({ type: 'system', subtype: 'task_notification', task_id: 'b1', status: 'completed' })

const BG_START_2 = BG_START.replace('"a155"', '"a156"').replace('Deep dive', 'Duplicate deep dive')

/** A run that did its work and said so. */
export const LOG_OK = [INIT, ASSISTANT, TOOL_RESULT, BASH_START, BASH_DONE, result({})].join('\n') + '\n'

/**
 * rc=0 and subtype success, but its background subagent never finished: `-p`
 * stopped waiting after ten minutes and killed it, then wrote the result. The
 * order is a real log's: stderr folded in, the kill, then the result.
 */
export const LOG_UNFINISHED =
  [
    INIT,
    ASSISTANT,
    BG_START,
    'Background tasks still running after 600s; terminating. Set CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 to wait indefinitely.',
    j({ type: 'system', subtype: 'task_updated', task_id: 'a155', patch: { status: 'killed', end_time: NOW } }),
    j({ type: 'system', subtype: 'task_notification', task_id: 'a155', status: 'stopped' }),
    result({ num_turns: 20, total_cost_usd: 2.42, result: 'The builder works.\nNow waiting for the deep-dive agent to finish.', subagent_stats: stats(1, 0, 1) }),
  ].join('\n') + '\n'

/**
 * Two background subagents; the model stopped the duplicate itself with
 * TaskStop (killed.parent) and the other finished: the run finished its work.
 */
export const LOG_STOPPED_BY_MODEL =
  [
    INIT,
    BG_START,
    BG_START_2,
    j({ type: 'system', subtype: 'task_updated', task_id: 'a156', patch: { status: 'killed', end_time: NOW } }),
    j({ type: 'system', subtype: 'task_notification', task_id: 'a156', status: 'stopped' }),
    j({ type: 'system', subtype: 'task_notification', task_id: 'a155', status: 'completed' }),
    result({ num_turns: 9, result: 'Report written; stopped the duplicate deep dive.', subagent_stats: stats(2, 1, 0, 1) }),
  ].join('\n') + '\n'

/** The same run, but the stop was not the model's (killed.user): something stopped the subagent from outside. */
export const LOG_STOPPED_BY_OTHER = LOG_STOPPED_BY_MODEL.replace('"parent":1,"user":0', '"parent":0,"user":1')

/** The main turn answered while its background subagent still runs: `-p` is still waiting on it. */
export const LOG_WAITING =
  [INIT, BG_START, result({ result: 'Started the deep dive; waiting for it.', subagent_stats: stats(1, 0) })].join('\n') + '\n'

/** A run the permission system refused a call in. */
export const LOG_DENIED =
  [
    INIT,
    j({ type: 'system', subtype: 'permission_denied', tool_name: 'Bash', tool_use_id: 'toolu_01' }),
    result({ permission_denials: [{ tool_name: 'Bash', tool_use_id: 'toolu_01', tool_input: { command: 'cp a b' } }] }),
  ].join('\n') + '\n'

/** A run that stopped mid-turn: no result event at all. */
export const LOG_KILLED = [INIT, ASSISTANT, TOOL_RESULT].join('\n') + '\n'

/** A run that hit its turn limit. */
export const LOG_FAILED =
  [INIT, result({ subtype: 'error_max_turns', is_error: true, terminal_reason: 'max_turns', errors: ['Reached maximum number of turns (40)'] })].join('\n') + '\n'

/** A run whose last word was a question nobody was there to answer. */
export const LOG_ASKED =
  [INIT, result({ result: 'Two queries returned no rows.\n\n1. Publish anyway\n2. Wait for the data\n\nWhich do you want?' })].join('\n') + '\n'

/** A run that answered its background subagent in a second result: finished, two results, one denial over both. */
export const LOG_TWO_RESULTS =
  [
    INIT,
    BG_START,
    result({ num_turns: 24, total_cost_usd: 4.41, result: 'Waiting for the deep-dive agent.', permission_denials: [{ tool_name: 'Skill', tool_use_id: 'toolu_02', tool_input: {} }], subagent_stats: stats(1, 1) }),
    j({ type: 'system', subtype: 'task_notification', task_id: 'a155', status: 'completed' }),
    result({ num_turns: 8, total_cost_usd: 4.41, origin: { kind: 'task-notification' }, subagent_stats: stats(1, 1) }),
  ].join('\n') + '\n'

/** A build that writes no subagent_stats: the task events say the background subagent never completed. */
export const LOG_NO_STATS = [INIT, BG_START, result({ subagent_stats: undefined, result: 'Started the deep dive.' })].join('\n') + '\n'

/** No subagent_stats and no task events: only the result's words say it stopped waiting. */
export const LOG_SAYS_WAITING = [INIT, result({ subagent_stats: undefined, result: 'Built the page.\nNow waiting for the review agent to finish.' })].join('\n') + '\n'

/** LOG_OK as a JSON writer with spaces (Python's json.dumps, an SDK logger) writes it. */
export const LOG_OK_SPACED = LOG_OK.split('\n')
  .map(l => l.replace(/":/g, '": ').replace(/,"/g, ', "'))
  .join('\n')

/** One foreground subagent completed and one background subagent failed: the run still says success. */
export const LOG_SUBAGENT_FAILED =
  [
    INIT,
    BG_START,
    j({ type: 'system', subtype: 'task_notification', task_id: 'a155', status: 'failed' }),
    result({
      result: 'Report written; the deep dive could not finish.',
      subagent_stats: { ...stats(1, 1), spawned: 2, requested: { background: 1, foreground: 1, unset: 0 }, failed: 1 },
    }),
  ].join('\n') + '\n'

/** A run whose final report is longer than `len` characters: its result line alone outgrows a short tail. */
export const logWithLongResult = (len: number): string => [INIT, result({ result: `Report:\n${'x'.repeat(len)}\nDone.` })].join('\n') + '\n'

/** How a peer's message reaches `session.receive`: the engine's envelope around the sender's text. */
export const PEER_ENVELOPE =
  '<cross-session-message from="uds:/tmp/cc-socks/1110052.sock" from-name="cwd-14" from-mode="prompting">\nStatus check from the person\n</cross-session-message>'

/** A subagent of this session handing its report back to the main conversation. */
export const HAND_BACK =
  'Another Claude session sent a message: <agent-message from="a0fc1234abcd5678">\n[Subagent hand-back] The text below is the final report of a subagent this session delegated to.\n</agent-message>'

/**
 * An append-only log three cron jobs write to: the weekly run (its start, its
 * steps, its watchdog verdict), the noon watchdog, and a Friday check that crashed.
 */
export const SHARED_LOG_OK =
  '=== pull_leads ===\nok\n=== stage_gate ===\nok\n=== watchdog ===\nwatchdog: todo en orden\nwatchdog: todo en orden\n=== structure_check ===\nTraceback (most recent call last):\nKeyError: x\n'
/** The next week's run started and has not reached its watchdog yet. */
export const SHARED_LOG_NEXT_RUN = `${SHARED_LOG_OK}=== pull_leads ===\nok\n=== build_deploy ===\n`

/** A text log of a weekly cron wrapper, judged by its last matching line. */
export const WEEKLY_OK = '[09:30] === Weekly run start ===\n[09:43] --- report: claude -p exit rc=0 after 823s ---\n[09:43] === Weekly run done; a_rc=0 b_rc=0 ===\n'
export const WEEKLY_SKIP = '[09:30] === Weekly run start ===\n[09:30] SKIP: login stale, not running\n'

/** A sources file naming one scheduled stream source and one text log. */
export const SOURCES_FILE = {
  runs: [{ label: 'Nightly', glob: `${LOGS}/nightly-*.jsonl`, expectEveryHours: 30 }],
  lines: [{ label: 'Weekly', glob: `${LOGS}/weekly-*.log`, match: '=== Weekly run done|SKIP:', ok: 'done; (?:\\w+_rc=0 ?)+===' }],
}

// ------------------------------------------------------------------ the stub world

export type File = { text: string; mtimeMs: number; size?: number }

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
  /** What `$.command.register` answers for /fleet: null registers it, a string refuses with it. */
  commandDeny: string | null
  /** What `$.session.id()` answers; /clear moves it on. */
  selfId: string
  /**
   * What `shepherd status` prints. 'missing': a shepherd that is not installed;
   * 'missing-long': the same at a long absolute path, as the engine words it;
   * 'broken': one that exits 1; 'broken-fnf': one that exits 1 saying a file is missing.
   */
  status: unknown
  agents: unknown
  inbox: unknown[]
  /** Files by absolute path; a directory lists the files under it. */
  files: Map<string, File>
  isPlaced: boolean
  isDelivered: boolean
  /** While set, `claude agents` answers only once it resolves: a refresh held mid-read. */
  gate: Promise<void> | null
}

const dirOf = (path: string): string => path.slice(0, path.lastIndexOf('/')) || '/'

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
    status: STATUS,
    agents: AGENTS,
    inbox: INBOX,
    files: new Map(),
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
  on('fs.list', ($, e) => {
    const inside = [...world.files.entries()].filter(([path]) => dirOf(path) === e.path)

    if (inside.length === 0 && ![...world.files.keys()].some(path => path.startsWith(`${e.path}/`))) {
      return { deny: `ENOENT: no such file or directory, scandir '${e.path}'` }
    }

    return {
      value: inside.map(([path, f]) => ({ name: path.slice(path.lastIndexOf('/') + 1), kind: 'file' as const, size: f.size ?? f.text.length, mtimeMs: f.mtimeMs, isLink: false })),
    }
  })
  on('fs.read', ($, e) => {
    const f = world.files.get(e.path)

    return f === undefined ? { deny: `ENOENT: no such file or directory, open '${e.path}'` } : { value: f.text }
  })
  on('process.run', async ($, e) => {
    if (world.gate !== null && e.argv[1] === 'agents') {
      await world.gate
    }
    world.processes.push({ argv: e.argv, ...(e.init?.cwd !== undefined && { cwd: e.init.cwd }), ...(e.init?.env !== undefined && { env: e.init.env }) })

    const [bin, verb] = e.argv

    if (bin === 'tail') {
      const f = world.files.get(e.argv[3] ?? '')

      return f === undefined ? out('', 1, 'tail: no such file') : out(f.text.slice(-Number(e.argv[2])))
    }
    if (verb === 'agents') {
      return out(JSON.stringify(world.agents))
    }
    if (world.status === 'missing') {
      return { deny: `spawn ${bin} ENOENT` }
    }
    if (world.status === 'missing-long') {
      return { deny: `${LONG_BIN} failed to start: ENOENT: no such file or directory, posix_spawn '${LONG_BIN}'` }
    }
    if (world.status === 'broken') {
      return out('', 1, 'shepherd: state written by a newer shepherd')
    }
    if (world.status === 'broken-fnf') {
      return out('', 1, "FileNotFoundError: [Errno 2] No such file or directory: '/home/u/.claude/shepherd/repos.json'")
    }

    switch (verb) {
      case 'status':
        return out(JSON.stringify(world.status))
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
  // What runs /fleet when the mod passes it on: the person's own skill of that name, say.
  on('command.run', ($, e) => ({ text: `core ran /${e.command}` }))
  on('classic.SessionStart', () => ({}))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['drawn by Claude Code'] }))

  return world
}

/** Puts the stream logs and the sources file of a busy night in the world. */
export function nightOf(world: World): void {
  world.files.set(SOURCES, { text: JSON.stringify(SOURCES_FILE), mtimeMs: NOW - 48 * HOUR })
  world.files.set(`${LOGS}/nightly-report-2026-10-09.jsonl`, { text: LOG_OK, mtimeMs: NOW - 6 * HOUR })
  world.files.set(`${LOGS}/nightly-report-2026-10-08.jsonl`, { text: LOG_FAILED, mtimeMs: NOW - 30 * HOUR })
  world.files.set(`${LOGS}/nightly-deep-dive-2026-10-09.jsonl`, { text: LOG_UNFINISHED, mtimeMs: NOW - 5 * HOUR })
  world.files.set(`${LOGS}/weekly-2026-10-05.log`, { text: WEEKLY_OK, mtimeMs: NOW - 4 * 24 * HOUR })
}
