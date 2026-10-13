// The fleet's pure half: what `shepherd status --all --json`, `claude agents
// --json --all` and headless `claude -p --output-format stream-json` logs say,
// reduced to the snapshot the band and the pane draw, and the words they draw.
// No `$` and no I/O here: read.ts reads, this file judges.
import type {
  FleetRepo,
  FleetRun,
  FleetSession,
  FleetSnapshot,
  FleetSource,
  FleetTab,
  FleetTone,
  FleetTrack,
} from 'claude-code'

const HOUR_MS = 3_600_000

// ------------------------------------------------------------------ helpers

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const textOf = (value: unknown): string | null => (typeof value === 'string' && value !== '' ? value : null)

const numberOf = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

const countOf = (value: unknown): number => Math.max(0, Math.floor(numberOf(value) ?? 0))

/** Shepherd stamps seconds since the epoch; the snapshot keeps milliseconds. */
const msOfSeconds = (value: unknown): number | null => {
  const seconds = numberOf(value)

  return seconds === null ? null : seconds * 1000
}

export const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

/** How long ago, in one short word: 40s, 12m, 5h, 3d. */
export function agoOf(thenMs: number, nowMs: number): string {
  const s = Math.max(0, Math.round((nowMs - thenMs) / 1000))

  if (s < 60) {
    return `${s}s`
  }
  if (s < 3600) {
    return `${Math.floor(s / 60)}m`
  }
  if (s < 86_400) {
    return `${Math.floor(s / 3600)}h`
  }

  return `${Math.floor(s / 86_400)}d`
}

const pad2 = (n: number): string => String(n).padStart(2, '0')

/** A log's day as MM-DD, local time. */
export function dayOf(ms: number): string {
  const d = new Date(ms)

  return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

/** A log's day and minute as MM-DD HH:MM, local time. */
export function stampOf(ms: number): string {
  const d = new Date(ms)

  return `${dayOf(ms)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/** The first non-blank line of a message, cut to `max` characters. */
export function firstLineOf(text: string, max = 100): string {
  const line = (text.split('\n').find(l => l.trim() !== '') ?? '').trim()

  return line.length > max ? `${line.slice(0, max - 1)}…` : line
}

/** The last non-blank line of a message, cut to `max` characters. */
export function lastLineOf(text: string, max = 100): string {
  const line = (text.split('\n').reverse().find(l => l.trim() !== '') ?? '').trim()

  return line.length > max ? `${line.slice(0, max - 1)}…` : line
}

// ------------------------------------------------------------------ options

export type Config = {
  isBandOn: boolean
  tracksOn: boolean
  sessionsOn: boolean
  /** The shepherd and claude executables: a name looked up on PATH, or a path. */
  shepherd: string
  claude: string
  /** `Label=glob` or a bare glob of stream-json logs, from the `runs` option. */
  runs: string[]
  sourcesFile: string | null
  periodMs: number
  staleMs: number
}

const MIN_PERIOD_S = 15
const DEFAULT_PERIOD_S = 60
const DEFAULT_STALE_H = 24

const optionText = (value: unknown, fallback: string): string => (typeof value === 'string' && value.trim() !== '' ? value.trim() : fallback)

/**
 * The plugin's options as the mod reads them, every one defaulted and bounded.
 * Claude Code refuses to load the mod at all when a number is outside its
 * plugin.json min/max, so the bounds here only hold for callers that skip that
 * check (the tests, a smoke script).
 */
export function configOf(options: Readonly<Record<string, unknown>>): Config {
  const runs = options.runs
  const period = numberOf(options.refresh_seconds) ?? DEFAULT_PERIOD_S
  const stale = numberOf(options.stale_hours) ?? DEFAULT_STALE_H
  const sourcesFile = optionText(options.sources_file, '')

  return {
    isBandOn: options.band !== false,
    tracksOn: options.tracks !== false,
    sessionsOn: options.sessions !== false,
    shepherd: optionText(options.shepherd_bin, 'shepherd'),
    claude: optionText(options.claude_bin, 'claude'),
    runs: (Array.isArray(runs) ? runs : typeof runs === 'string' ? [runs] : []).filter(
      (r): r is string => typeof r === 'string' && r.trim() !== '',
    ),
    sourcesFile: sourcesFile === '' ? null : sourcesFile,
    periodMs: Math.max(MIN_PERIOD_S, period) * 1000,
    staleMs: Math.max(1, stale) * HOUR_MS,
  }
}

/**
 * The shape of the snapshot this build writes to $.store. Sessions still running
 * an older build share the same store file: a snapshot of another schema is
 * treated as absent, never drawn. Raise it with any change to FleetSnapshot.
 */
export const SCHEMA = 2

/** What a snapshot was read with: another session's snapshot is adopted only when this matches. */
export function configKeyOf(c: Config): string {
  return JSON.stringify([SCHEMA, c.tracksOn, c.sessionsOn, c.shepherd, c.claude, c.runs, c.sourcesFile, c.staleMs])
}

/** `~` and `~/...` under HOME; anything else as given. */
export function expandHome(path: string, home: string | null): string {
  if (home === null || (path !== '~' && !path.startsWith('~/'))) {
    return path
  }

  return home.replace(/\/$/, '') + path.slice(1)
}

/** The tab `/fleet <word>` names, or null. */
export function tabOf(args: string): FleetTab | null {
  const word = args.trim().toLowerCase()

  return word === 'tracks' || word === 'sessions' || word === 'runs' ? word : null
}

// ------------------------------------------------------------------ tracks

/** Shepherd's tracks, flat, with the repo each sits in. Throws on JSON that is not shepherd's. */
export function tracksOf(status: unknown, nowMs: number, staleMs: number): { repos: FleetRepo[]; tracks: FleetTrack[] } {
  if (!isRecord(status) || !Array.isArray(status.repos)) {
    throw new Error('not shepherd status JSON (no repos)')
  }

  const repos: FleetRepo[] = []
  const tracks: FleetTrack[] = []

  for (const repo of status.repos) {
    if (!isRecord(repo)) {
      continue
    }

    const repoName = textOf(repo.name) ?? '?'
    const primary = textOf(repo.primary) ?? ''
    const overlaps = Array.isArray(repo.overlaps) ? repo.overlaps.filter(isRecord) : []

    repos.push({ name: repoName, primary })

    for (const t of Array.isArray(repo.tracks) ? repo.tracks : []) {
      if (!isRecord(t)) {
        continue
      }

      const name = textOf(t.name) ?? '?'
      const status = textOf(t.status) ?? 'unknown'
      const agent = isRecord(t.agent) ? t.agent : {}
      const review = isRecord(t.review) ? t.review : null
      const isLive = agent.live === true
      const lastActivity = msOfSeconds(t.last_activity)
      // A merge-tree verdict is a list of files: empty is clean, null unknown.
      const conflicts = overlaps.filter(
        o => (o.a === name || o.b === name) && Array.isArray(o.conflicts) && o.conflicts.length > 0,
      ).length
      const isIdle = lastActivity !== null && nowMs - lastActivity > staleMs
      const reviewId = review === null ? null : (textOf(review.id) ?? (numberOf(review.id) === null ? null : String(review.id)))

      tracks.push({
        repo: repoName,
        name,
        status,
        goal: textOf(t.goal),
        sessionId: textOf(agent.session_id),
        owner: textOf(agent.status),
        isLive,
        reviewId,
        reviewState: review === null ? null : textOf(review.state),
        checks: review === null ? null : textOf(review.checks),
        checkedAt: review === null ? null : msOfSeconds(review.checked_at),
        reviewUrl: review === null ? null : textOf(review.url),
        lastActivity,
        warnings: Array.isArray(t.warnings) ? t.warnings.filter((w): w is string => typeof w === 'string') : [],
        conflicts,
        isBlocked: false,
        waitingFor: null,
        isStale: status === 'active' && (!isLive || isIdle),
      })
    }
  }

  return { repos, tracks }
}

// ------------------------------------------------------------------ sessions

/** `claude agents --json --all`, one row per session. Throws on JSON that is not a list. */
export function sessionsOf(agents: unknown): FleetSession[] {
  if (!Array.isArray(agents)) {
    throw new Error('not claude agents JSON (no list)')
  }

  const sessions: FleetSession[] = []

  for (const a of agents) {
    if (!isRecord(a)) {
      continue
    }

    const sessionId = textOf(a.sessionId)

    if (sessionId === null) {
      continue
    }

    const state = textOf(a.state)
    const status = textOf(a.status)

    sessions.push({
      sessionId,
      shortId: textOf(a.id),
      name: textOf(a.name),
      kind: textOf(a.kind) ?? 'unknown',
      state,
      status,
      waitingFor: textOf(a.waitingFor),
      startedAt: numberOf(a.startedAt),
      cwd: textOf(a.cwd),
      isLive: numberOf(a.pid) !== null,
      isBlocked: state === 'blocked' || status === 'waiting',
      track: null,
    })
  }

  return sessions
}

/** Marks each track whose owner waits on the person, and each session with the track it owns. */
export function joined(tracks: FleetTrack[], sessions: FleetSession[]): { tracks: FleetTrack[]; sessions: FleetSession[] } {
  const byId = new Map(sessions.map(s => [s.sessionId, s]))
  const owned = new Map<string, string>()

  const nextTracks = tracks.map(t => {
    const s = t.sessionId === null ? undefined : byId.get(t.sessionId)

    if (t.sessionId !== null && s !== undefined && t.status !== 'paused' && t.status !== 'merged') {
      owned.set(t.sessionId, `${t.repo}/${t.name}`)
    }

    return s === undefined ? t : { ...t, isBlocked: s.isBlocked, waitingFor: s.waitingFor }
  })

  return { tracks: nextTracks, sessions: sessions.map(s => ({ ...s, track: owned.get(s.sessionId) ?? null })) }
}

/** Waiting on the person first, then working, idle, finished, and failed or stopped last. */
export function sessionRank(s: FleetSession): number {
  if (s.isBlocked) {
    return 0
  }
  if (s.status === 'busy' || s.state === 'working') {
    return 1
  }
  if (s.isLive) {
    return 2
  }
  if (s.state === 'done') {
    return 3
  }

  return 4
}

// ------------------------------------------------------------------ run sources

export type RunSource = {
  label: string
  /** A directory and a file pattern: `*` and `?` in the last part only; `~/` is HOME. */
  glob: string
  /** Logs older than this are not shown. */
  maxAgeHours: number
  /** A log with no result event this young reads as still running. */
  runningMinutes: number
  /** At most this many logs: the newest of each name, a trailing date dropped. */
  latest: number
  /** The newest log older than this is a `late` failure; null: no expectation. */
  expectEveryHours: number | null
}

export type LineSource = {
  label: string
  glob: string
  /** The last line matching this is the verdict line. */
  match: RegExp
  /**
   * For a log that holds several runs: where the last one starts. The verdict
   * line must come after the last line matching this; null: one run per log.
   */
  start: RegExp | null
  /** The verdict line is ok when it matches this, a failure otherwise; null: any line is ok. */
  ok: RegExp | null
  maxAgeHours: number
  /** A log with no verdict line this young reads as still running. */
  runningMinutes: number
  latest: number
  expectEveryHours: number | null
}

export type Sources = { runs: RunSource[]; lines: LineSource[]; errors: string[] }

const DEFAULT_MAX_AGE_HOURS = 192
const DEFAULT_RUNNING_MINUTES = 15
const DEFAULT_LATEST = 12

const positiveOf = (value: unknown, fallback: number): number => {
  const n = numberOf(value)

  return n !== null && n > 0 ? n : fallback
}

const expectOf = (value: unknown): number | null => {
  const n = numberOf(value)

  return n !== null && n > 0 ? n : null
}

const regexOf = (value: unknown, where: string, errors: string[]): RegExp | null => {
  const source = textOf(value)

  if (source === null) {
    return null
  }

  try {
    return new RegExp(source)
  } catch (error) {
    errors.push(`${where}: bad pattern ${JSON.stringify(source)}: ${String(error)}`)

    return null
  }
}

/**
 * The run sources: the `runs` option's globs (`Label=glob` or a bare glob) and a
 * sources file's `runs` and `lines` entries. A malformed entry is skipped and
 * named in `errors`; the rest still load.
 */
export function sourcesOf(globs: readonly string[], file: unknown): Sources {
  const runs: RunSource[] = []
  const lines: LineSource[] = []
  const errors: string[] = []
  const runOf = (label: string, glob: string, entry: Record<string, unknown>): RunSource => ({
    label,
    glob,
    maxAgeHours: positiveOf(entry.maxAgeHours, DEFAULT_MAX_AGE_HOURS),
    runningMinutes: positiveOf(entry.runningMinutes, DEFAULT_RUNNING_MINUTES),
    latest: Math.floor(positiveOf(entry.latest, DEFAULT_LATEST)),
    expectEveryHours: expectOf(entry.expectEveryHours),
  })

  for (const raw of globs) {
    const spec = raw.trim()

    if (spec === '') {
      continue
    }

    const named = /^([^=/~*?]+)=(.+)$/.exec(spec)
    const label = named?.[1]?.trim()
    const glob = named?.[2]?.trim()

    runs.push(runOf(label ?? 'runs', glob ?? spec, {}))
  }

  if (file === undefined || file === null) {
    return { runs, lines, errors }
  }
  if (!isRecord(file)) {
    return { runs, lines, errors: [...errors, 'sources file: not a JSON object'] }
  }

  for (const [i, entry] of (Array.isArray(file.runs) ? file.runs : []).entries()) {
    const label = isRecord(entry) ? textOf(entry.label) : null
    const glob = isRecord(entry) ? textOf(entry.glob) : null

    if (!isRecord(entry) || label === null || glob === null) {
      errors.push(`sources file: runs[${i}] needs a label and a glob`)
      continue
    }

    runs.push(runOf(label, glob, entry))
  }

  for (const [i, entry] of (Array.isArray(file.lines) ? file.lines : []).entries()) {
    const label = isRecord(entry) ? textOf(entry.label) : null
    const glob = isRecord(entry) ? textOf(entry.glob) : null
    const match = isRecord(entry) ? regexOf(entry.match, `lines[${i}].match`, errors) : null

    if (!isRecord(entry) || label === null || glob === null || match === null) {
      errors.push(`sources file: lines[${i}] needs a label, a glob and a match pattern`)
      continue
    }

    const start = regexOf(entry.start, `lines[${i}].start`, errors)
    const ok = regexOf(entry.ok, `lines[${i}].ok`, errors)

    // A bad ok pattern must not turn every line ok, nor a bad start judge an older run.
    if ((textOf(entry.start) !== null && start === null) || (textOf(entry.ok) !== null && ok === null)) {
      continue
    }

    lines.push({
      label,
      glob,
      match,
      start,
      ok,
      maxAgeHours: positiveOf(entry.maxAgeHours, DEFAULT_MAX_AGE_HOURS),
      runningMinutes: positiveOf(entry.runningMinutes, DEFAULT_RUNNING_MINUTES),
      latest: Math.floor(positiveOf(entry.latest, 1)),
      expectEveryHours: expectOf(entry.expectEveryHours),
    })
  }

  return { runs, lines, errors }
}

/** A glob split into the directory to list and a pattern for the names in it. Throws when it cannot be. */
export function globParts(glob: string, home: string | null): { dir: string; pattern: RegExp } {
  const trimmed = glob.trim()

  if ((trimmed === '~' || trimmed.startsWith('~/')) && home === null) {
    throw new Error(`${glob}: ~ needs HOME`)
  }

  const path = expandHome(trimmed, home)

  if (!path.startsWith('/')) {
    throw new Error(`${glob}: needs an absolute path or ~/`)
  }

  const cut = path.lastIndexOf('/')
  const dir = path.slice(0, cut) || '/'
  const name = path.slice(cut + 1)

  if (/[*?[\]]/.test(dir)) {
    throw new Error(`${glob}: wildcards only in the file name`)
  }
  if (name === '') {
    throw new Error(`${glob}: names no file`)
  }

  const body = name.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '[^/]*').replace(/\?/g, '[^/]')

  return { dir, pattern: new RegExp(`^${body}$`) }
}

/**
 * A trailing date after a separator (or alone): 2026-10-09 or 20261009, a time
 * after it, then a run counter for a second run that day (`-2`). A real
 * calendar date only, so an epoch stamp or a build number is not one.
 */
const DATED =
  /(?:^|[-_.])(?:19|20)\d{2}-?(?:0[1-9]|1[0-2])-?(?:0[1-9]|[12]\d|3[01])(?:[T_-]?\d{2}[:-]?\d{2}(?:[:-]?\d{2})?)?(?:[-_.]\d{1,3})?$/

/** A log's run name: the file name without its extension and a trailing date (and a run counter after it). */
export function runNameOf(fileName: string): string {
  const base = fileName.replace(/\.[^.]*$/, '')

  return base.replace(DATED, '') || base
}

export type LogFile = { name: string; size: number; mtimeMs: number }

/** The logs to show: the newest of each run name, no older than maxAgeHours, newest first, at most `latest`. */
export function pickLogs<T extends LogFile>(files: readonly T[], pattern: RegExp, nowMs: number, maxAgeHours: number, latest: number): T[] {
  const seen = new Set<string>()
  const picked: T[] = []

  for (const f of [...files].filter(f => pattern.test(f.name)).sort((a, b) => b.mtimeMs - a.mtimeMs)) {
    const name = runNameOf(f.name)

    if (seen.has(name) || nowMs - f.mtimeMs > maxAgeHours * HOUR_MS) {
      continue
    }

    seen.add(name)
    picked.push(f)

    if (picked.length >= latest) {
      break
    }
  }

  return picked
}

// ------------------------------------------------------------------ verdicts

export type Verdict = { verdict: string; tone: FleetTone; why: string; detail: string }

/**
 * How many subagents a run spawned, and how each ended. `killed` is a stop the
 * run did not choose (Claude Code's own halt, or any stop but the model's);
 * `stopped` is the model stopping one itself with TaskStop, which is not
 * unfinished work.
 */
export type SubagentCounts = { spawned: number; completed: number; failed: number; killed: number; stopped: number }

/** What a stream-json log says, gathered once per version of the file and judged on every refresh. */
export type StreamFacts = {
  /** How many `result` events; a run that answered background work has more than one. */
  results: number
  /** The last result event: how the run ended. */
  last: { subtype: string; isError: boolean; terminal: string | null; errors: string[]; text: string } | null
  /** Turns over every result (each result counts its own). */
  turns: number
  /** total_cost_usd is cumulative and an estimate: the highest seen. */
  cost: number | null
  /** permission_denials over every result, or the permission_denied events when more. */
  denied: number
  /** The last result's subagent_stats, foreground and background, when the build writes them. */
  stats: SubagentCounts | null
  /** Background subagents only, counted one by one from the task events. */
  tasks: SubagentCounts
  /** `claude -p` gave up waiting on background work and terminated it (stderr folded into the log). */
  isTerminated: boolean
  /** Only the log's tail was read: counts may miss what came before. */
  isPartial: boolean
}

/** The line `claude -p` prints on stderr when it gives up waiting on background work. */
const TERMINATING = /Background tasks still running after \d+s?; terminating/

/**
 * The events that decide a run, spelled compact (as `claude -p` writes them) or
 * with spaces (as another JSON writer may). A quote inside a JSON string is
 * escaped, so these spellings appear bare only as an event's own keys.
 */
const WANTED = /"(?:type|subtype)"\s*:\s*"(?:result|permission_denied|task_)/

/**
 * A result's last line saying, in the present, that the run itself still waits
 * on background work: "Now waiting for the deep-dive agent to finish", "I'm
 * still waiting on 2 tasks". Not a third party ("the cron is waiting for 2
 * jobs"), nor the past ("I was waiting for the agent").
 */
const WAITING_ON_WORK =
  /(?:\b(?:I(?:'m| am)|we(?:'re| are))\s+(?:(?:still|now|currently|just)\s+)?|\b(?:now|still|currently)\s+|(?:^|[.;:!]\s*))waiting (?:for|on) (?:the |a |an |its |my |our |\d+ )?(?:[\w-]+ ){0,3}?(?:sub)?(?:agents?|tasks?|jobs?|workers?)\b/i

/** ... but not one saying nothing waits any more, or that the work came back. */
const NOT_WAITING =
  /(?:\b(?:no longer|not|never|stopped|done|finished|quit|nothing|nobody|no one|none)|n't)\s+(?:\w+\s+){0,2}waiting\b|\b(?:has|have|had)\s+(?:now\s+|since\s+|already\s+)?(?:returned|finished|completed|reported)\b/i

/** One of a numbered or lettered list: "1. Publish anyway", "2) Wait", "(b) Skip". */
const OPTION = /^(?:\d{1,2}[.)]|\(?[a-e]\))\s+(\S.*)$/

/** A list item that reports what was done ("2. Stock report refreshed", "3. ✅ Returns"), not a choice to make. */
const DONE_ITEM = /[✓✔✅]|\b(?:\w+ed|done|ok|complete|written|sent|built|run|up to date)[.!*`_\s]*$/i

/** A question that picks between things on its own: "Which do you want?" */
const CHOICE = /\b(?:which|whether|choose|pick|prefer)\b/i

/** A question that asks the person to decide: with an `or`, a choice ("Should I publish or wait?"). */
const DECIDE = /\b(?:should|shall) (?:I|we)\b|\bdo you want\b|\bwould you (?:rather|prefer)\b|\bhow (?:should|do you want)\b/i

/**
 * A closing offer or pleasantry once the job is done, not a question the run
 * needed answered: "Want me to also email it?", "Would you like me to dig
 * deeper, or is this sufficient?", "Any questions?", "Anything else?".
 */
const OFFER =
  /\b(?:also|as well|too)\b|\b(?:would you like|want) me to\b|\bany (?:other |more |further )?(?:questions|concerns)\b|\banything else\b|\blet me know\b|\bif you(?:'d| would)? like\b/i

/**
 * Whether a result ends on a question the run needed answered. A heuristic: the
 * last line asks, and it picks between things (which, whether; or an `or` in a
 * should-I / do-you-want question) or follows two or more numbered options
 * that read as choices, not as a list of what was done. A closing offer or
 * pleasantry is not a question nobody answered.
 */
export function isAsking(text: string): boolean {
  const lines = text
    .split('\n')
    .map(l => l.trim())
    .filter(l => l !== '')
  const question = (lines[lines.length - 1] ?? '').replace(/[*_`\s]+$/, '')

  if (!question.endsWith('?') || OFFER.test(question)) {
    return false
  }
  if (CHOICE.test(question) || (/\bor\b/i.test(question) && DECIDE.test(question))) {
    return true
  }

  const options = lines.slice(-13, -1).filter(l => {
    const item = OPTION.exec(l)?.[1]

    return item !== undefined && !DONE_ITEM.test(item)
  })

  return options.length >= 2
}

/**
 * subagent_stats.killed by who stopped the subagent: `parent` is another agent's
 * TaskStop (the model's choice), `system` Claude Code itself (a budget halt, `-p`
 * giving up at its wait ceiling), `user` any other stop. Every key but `parent`
 * counts as killed, a key a later build adds too; a bare number is all kills.
 */
const killsOf = (value: unknown): { killed: number; stopped: number } =>
  isRecord(value)
    ? {
        killed: Object.entries(value).reduce<number>((n, [who, v]) => (who === 'parent' ? n : n + countOf(v)), 0),
        stopped: countOf(value.parent),
      }
    : { killed: countOf(value), stopped: 0 }

/**
 * Reads the events that decide a headless run out of its stream-json log: every
 * `result` event (subtype, is_error, num_turns, terminal_reason, total_cost_usd,
 * permission_denials, subagent_stats), `permission_denied` events and the task
 * events; other lines are skipped unparsed.
 */
export function factsOfStream(text: string, isPartial = false): StreamFacts {
  let last: Record<string, unknown> | null = null
  let results = 0
  let turns = 0
  let cost: number | null = null
  let denialsInResults = 0
  let denialEvents = 0
  let isTerminated = false
  const background = new Set<string>()
  /** Each task's end, the last word on it winning: a kill then its `stopped` notice is one kill. */
  const ended = new Map<string, 'completed' | 'failed' | 'killed'>()

  for (const line of text.split('\n')) {
    const s = line.trim()

    if (s === '') {
      continue
    }
    if (!s.startsWith('{')) {
      // stderr folded into the log (2>&1)
      isTerminated = isTerminated || TERMINATING.test(s)
      continue
    }
    if (!WANTED.test(s)) {
      continue
    }

    let event: unknown

    try {
      event = JSON.parse(s)
    } catch {
      continue
    }
    if (!isRecord(event)) {
      continue
    }

    const taskId = textOf(event.task_id)

    if (event.type === 'result') {
      last = event
      results += 1
      turns += countOf(event.num_turns)

      const runCost = numberOf(event.total_cost_usd)

      // Unknown stays unknown: no result with a cost shows none, not ≈$0.00.
      if (runCost !== null) {
        cost = Math.max(cost ?? 0, runCost)
      }
      denialsInResults += Array.isArray(event.permission_denials) ? event.permission_denials.length : 0
      continue
    }
    if (event.type !== 'system') {
      continue
    }
    if (event.subtype === 'permission_denied') {
      denialEvents += 1
      continue
    }
    if (taskId === null) {
      continue
    }

    // How a task ended, from its notification or its last update.
    const status = event.subtype === 'task_notification' ? event.status : event.subtype === 'task_updated' && isRecord(event.patch) ? event.patch.status : null

    if (event.subtype === 'task_started' && event.is_backgrounded === true && event.task_type === 'local_agent') {
      background.add(taskId)
    } else if (status === 'completed' || status === 'failed') {
      ended.set(taskId, status)
    } else if (status === 'killed' || status === 'stopped') {
      ended.set(taskId, 'killed')
    }
  }

  const stats = last !== null && isRecord(last.subagent_stats) ? last.subagent_stats : null
  const inBackground = (end: 'completed' | 'failed' | 'killed'): number => [...background].filter(id => ended.get(id) === end).length
  const kills = killsOf(stats?.killed)

  return {
    results,
    last:
      last === null
        ? null
        : {
            subtype: textOf(last.subtype) ?? '?',
            isError: last.is_error === true,
            terminal: textOf(last.terminal_reason),
            errors: Array.isArray(last.errors) ? last.errors.filter((x): x is string => typeof x === 'string') : [],
            text: typeof last.result === 'string' ? last.result : '',
          },
    turns,
    cost,
    denied: Math.max(denialsInResults, denialEvents),
    stats:
      stats === null
        ? null
        : {
            // `spawned` counts every subagent; an older build wrote only the background count.
            spawned: numberOf(stats.spawned) === null ? countOf(stats.started_in_background) : countOf(stats.spawned),
            completed: countOf(stats.completed),
            failed: countOf(stats.failed),
            killed: kills.killed,
            stopped: kills.stopped,
          },
    // Background subagents only (a shell the model stopped is no subagent). The
    // events do not say who stopped one, so every stop counts as killed.
    tasks: { spawned: background.size, completed: inBackground('completed'), failed: inBackground('failed'), killed: inBackground('killed'), stopped: 0 },
    isTerminated,
    isPartial,
  }
}

/**
 * The subagents a run left behind: the last result's subagent_stats, which
 * count foreground and background ones and say who stopped each; the task
 * events only for a build that writes no stats.
 */
function subagentsOf(f: StreamFacts): SubagentCounts & { pending: number } {
  const c = f.stats ?? f.tasks

  return { ...c, pending: Math.max(0, c.spawned - c.completed - c.failed - c.killed - c.stopped) }
}

/**
 * Judges one headless run from its facts. UNFINISHED when a subagent was left
 * running, failed or was killed (by anything but the model's own TaskStop), or
 * the run said it terminated background work, even though the run itself
 * reported success; `asked` when its last word is a question nobody will answer.
 *
 * @param mtimeMs when the log was last written: no result and a recent write is a run still going
 */
export function verdictOfStream(f: StreamFacts, mtimeMs: number, nowMs: number, runningMinutes: number): Verdict {
  const cut = f.isPartial ? ' · tail only' : ''
  const runningMs = runningMinutes * 60_000
  const ageMs = nowMs - mtimeMs
  const isRecent = ageMs < runningMs

  if (f.last === null) {
    const since = `last write ${agoOf(mtimeMs, nowMs)} ago${f.denied > 0 ? ` · ${f.denied} denied` : ''}${cut}`

    if (f.isTerminated) {
      return { verdict: 'no result', tone: 'fail', why: 'no result: background work terminated', detail: since }
    }
    if (isRecent) {
      return { verdict: 'running', tone: 'busy', why: `running, last write ${agoOf(mtimeMs, nowMs)} ago`, detail: `no result event yet${cut}` }
    }
    if (f.isPartial) {
      return { verdict: 'unjudged', tone: 'warn', why: 'no result event in the tail read: too short to judge', detail: since }
    }
    // A run blocked on one long, silent tool call writes nothing either: a warning before a failure.
    if (ageMs < 2 * runningMs) {
      return { verdict: 'quiet', tone: 'warn', why: `no result, no write for ${agoOf(mtimeMs, nowMs)}: a long tool call, or dead`, detail: since }
    }

    return { verdict: 'no result', tone: 'fail', why: 'no result event: killed or crashed', detail: since }
  }

  const { subtype, isError, terminal, errors, text } = f.last
  const sub = subagentsOf(f)
  const isSettled = terminal === null || terminal === 'completed'
  const detail =
    [
      subtype,
      plural(f.turns, 'turn'),
      f.cost === null ? null : `≈$${f.cost.toFixed(2)}`,
      `${f.denied} denied`,
      sub.spawned > 0 ? `subagents ${sub.completed}/${sub.spawned} done` : null,
      sub.failed > 0 ? `${sub.failed} failed` : null,
      sub.killed > 0 ? `${sub.killed} killed` : null,
      sub.stopped > 0 ? `${sub.stopped} stopped by the model` : null,
      isSettled ? null : terminal,
      f.results > 1 ? `${f.results} results` : null,
    ]
      .filter((x): x is string => x !== null)
      .join(' · ') + cut

  if (isError || subtype !== 'success' || !isSettled) {
    const reason = errors[0] ?? (subtype !== 'success' ? subtype : !isSettled && terminal !== null ? terminal : 'is_error')

    return { verdict: 'failed', tone: 'fail', why: `failed: ${firstLineOf(reason, 80)}`, detail }
  }

  // The run answered and is still waiting on its background work: not over yet.
  if (sub.pending > 0 && sub.killed === 0 && sub.failed === 0 && !f.isTerminated && isRecent) {
    return { verdict: 'running', tone: 'busy', why: `waiting on ${plural(sub.pending, 'background subagent')}`, detail }
  }

  if (sub.pending > 0 || sub.failed > 0 || sub.killed > 0 || f.isTerminated) {
    const parts = [
      sub.pending > 0 ? `${sub.pending} of ${plural(sub.spawned, 'subagent')} not finished` : null,
      sub.failed > 0 ? `${plural(sub.failed, 'subagent')} failed` : null,
      sub.killed > 0 ? `${plural(sub.killed, 'subagent')} killed` : null,
      f.isTerminated && sub.killed === 0 ? 'background work terminated' : null,
    ].filter((x): x is string => x !== null)

    return { verdict: 'unfinished', tone: 'fail', why: `unfinished: ${parts.join(', ')}`, detail }
  }

  const lastLine = lastLineOf(text, 400)

  // No subagent_stats and no task events to go by: the result's own words are the fallback.
  if (f.stats === null && f.tasks.spawned === 0 && WAITING_ON_WORK.test(lastLine) && !NOT_WAITING.test(lastLine)) {
    return { verdict: 'unfinished', tone: 'fail', why: `unfinished: says ${JSON.stringify(lastLineOf(text, 60))}`, detail }
  }

  if (isAsking(text)) {
    return { verdict: 'asked', tone: 'warn', why: `ended on a question: ${lastLineOf(text, 70)}`, detail }
  }

  if (f.denied > 0) {
    return { verdict: 'denied', tone: 'warn', why: plural(f.denied, 'permission denial'), detail }
  }

  return { verdict: 'ok', tone: 'ok', why: 'ok', detail }
}

/** A text log's verdict line, and whether the run it belongs to was found. */
export type LineFind = {
  /** The last line matching `match` after the last run start; null when none. */
  line: string | null
  /** A line matching `start` exists (always true without a `start`). */
  hasStart: boolean
}

/**
 * A text log's last line matching `match`, trimmed. With `start`, only a line
 * after the last line matching `start` counts: an older run's verdict in a log
 * that holds several runs is not this run's.
 */
export function lastMatchOf(text: string, match: RegExp, start: RegExp | null = null): LineFind {
  const lines = text.split('\n').map(l => l.trim())
  let from = start === null ? 0 : lines.length - 1

  // The last run start, when the log holds several runs.
  while (start !== null && from >= 0 && !(lines[from] !== '' && start.test(lines[from] ?? ''))) {
    from -= 1
  }
  if (from < 0) {
    return { line: null, hasStart: false }
  }

  for (let i = lines.length - 1; i >= from; i -= 1) {
    const line = lines[i] ?? ''

    // The start line itself may carry the verdict only when no start is set.
    if (line !== '' && (start === null || i > from) && match.test(line)) {
      return { line, hasStart: true }
    }
  }

  return { line: null, hasStart: true }
}

/** Judges a text log by its verdict line: ok when it matches `ok` (or no `ok` is set); none yet is running while the log is fresh. */
export function verdictOfLine(
  found: LineFind,
  match: RegExp,
  start: RegExp | null,
  ok: RegExp | null,
  mtimeMs: number,
  nowMs: number,
  runningMinutes: number,
): Verdict {
  if (found.line !== null) {
    const isOk = ok === null || ok.test(found.line)

    return { verdict: isOk ? 'ok' : 'failed', tone: isOk ? 'ok' : 'fail', why: firstLineOf(found.line, 120), detail: '' }
  }

  if (nowMs - mtimeMs < runningMinutes * 60_000) {
    return { verdict: 'running', tone: 'busy', why: `running, last write ${agoOf(mtimeMs, nowMs)} ago`, detail: '' }
  }

  const why =
    start === null
      ? `no line matching /${match.source}/`
      : found.hasStart
        ? `no line matching /${match.source}/ after the last run start`
        : `no run start matching /${start.source}/`

  return { verdict: 'no line', tone: 'warn', why, detail: '' }
}

/** Both at once, for a log read whole. */
export function verdictOfLines(
  text: string,
  match: RegExp,
  ok: RegExp | null,
  mtimeMs: number,
  nowMs: number,
  runningMinutes: number,
  start: RegExp | null = null,
): Verdict {
  return verdictOfLine(lastMatchOf(text, match, start), match, start, ok, mtimeMs, nowMs, runningMinutes)
}

/** The failure a source raises when its newest log is older than it should be; keyed by label and glob, so two sources never share one. */
export function lateRun(
  source: string,
  glob: string,
  kind: 'stream' | 'line',
  newestMs: number | null,
  nowMs: number,
  expectEveryHours: number | null,
): FleetRun | null {
  if (expectEveryHours === null || (newestMs !== null && nowMs - newestMs <= expectEveryHours * HOUR_MS)) {
    return null
  }

  return {
    key: `${source}@late@${glob}@${newestMs ?? 0}`,
    source,
    kind: 'late',
    name: source,
    path: '',
    size: 0,
    mtimeMs: newestMs ?? 0,
    verdict: 'late',
    tone: 'fail',
    why: newestMs === null ? `no ${kind === 'line' ? 'log' : 'run'} found` : `no run for ${agoOf(newestMs, nowMs)} (expected every ${expectEveryHours}h)`,
    detail: '',
  }
}

// ------------------------------------------------------------------ the band and the status line

export type Segment = { text: string; tone: FleetTone }

/** A failure the person acked reads as a warning: still in the band, gone from the status line. */
export function toneOf(run: FleetRun, acked: ReadonlySet<string>): FleetTone {
  return run.tone === 'fail' && acked.has(run.key) ? 'warn' : run.tone
}

const uniqueLabels = (sources: readonly FleetSource[]): string[] => [...new Set(sources.map(s => s.label))]

/** What the band says, as colored pieces: tracks, sessions waiting, then each run source. */
export function bandOf(snapshot: FleetSnapshot | null, acked: ReadonlySet<string>): Segment[] {
  if (snapshot === null) {
    return [{ text: 'fleet: reading…', tone: 'dim' }]
  }

  const out: Segment[] = []
  const sep = (): void => {
    if (out.length > 0) {
      out.push({ text: ' │ ', tone: 'dim' })
    }
  }

  if (snapshot.tracksOn && !snapshot.tracksMissing) {
    sep()

    if (snapshot.tracksError !== null) {
      out.push({ text: 'tracks ?', tone: 'warn' })
    } else {
      const t = snapshot.tracks
      const pieces: Segment[] = [
        { text: `${t.filter(x => x.isBlocked).length} blocked`, tone: 'fail' as const },
        // Tracks, as the other counts: one conflicting pair is two conflicting tracks.
        { text: `${t.filter(x => x.conflicts > 0).length} conflicting`, tone: 'fail' as const },
        { text: `${t.filter(x => x.checks === 'failing').length} failing`, tone: 'fail' as const },
        { text: `${t.filter(x => x.status === 'merged').length} merged`, tone: 'warn' as const },
        { text: `${t.filter(x => x.status === 'review').length} in review`, tone: 'busy' as const },
        { text: `${t.filter(x => x.isStale).length} stale`, tone: 'warn' as const },
      ].filter(p => !p.text.startsWith('0 '))

      out.push({ text: plural(t.length, 'track') + (pieces.length > 0 ? ': ' : ''), tone: 'dim' })
      pieces.forEach((p, i) => {
        if (i > 0) {
          out.push({ text: ' · ', tone: 'dim' })
        }
        out.push(p)
      })
    }
  }

  if (snapshot.sessionsOn && !snapshot.sessionsMissing) {
    if (snapshot.sessionsError !== null) {
      sep()
      out.push({ text: 'sessions ?', tone: 'warn' })
    } else {
      const waiting = snapshot.sessions.filter(s => s.isBlocked).length
      const working = snapshot.sessions.filter(s => s.kind === 'background' && s.state === 'working').length

      if (waiting > 0) {
        sep()
        out.push({ text: `${waiting} waiting on you`, tone: 'fail' })
      }
      if (working > 0) {
        sep()
        out.push({ text: `${working} bg working`, tone: 'busy' })
      }
    }
  }

  for (const source of uniqueLabels(snapshot.sources)) {
    const runs = snapshot.runs.filter(r => r.source === source)
    const errors = snapshot.sources.filter(s => s.label === source && s.error !== null)

    sep()

    if (runs.length === 0) {
      out.push({ text: `${source} ${errors.length > 0 ? '?' : 'none'}`, tone: errors.length > 0 ? 'warn' : 'dim' })
      continue
    }

    const newest = Math.max(...runs.map(r => r.mtimeMs))
    const fails = runs.filter(r => toneOf(r, acked) === 'fail')
    const warns = runs.filter(r => toneOf(r, acked) === 'warn')
    const busy = runs.filter(r => r.tone === 'busy')
    const head = `${source} ${newest > 0 ? dayOf(newest) + ' ' : ''}`
    const [firstFail] = fails
    const [firstWarn] = warns

    if (firstFail !== undefined) {
      out.push({ text: `${head}✗ ${fails.length === 1 ? firstFail.verdict : plural(fails.length, 'failure')}`, tone: 'fail' })
    } else if (busy.length > 0) {
      out.push({ text: `${head}${busy.length} running`, tone: 'busy' })
    } else if (firstWarn !== undefined) {
      out.push({ text: `${head}⚠ ${warns.length === 1 ? firstWarn.verdict : plural(warns.length, 'warning')}`, tone: 'warn' })
    } else {
      out.push({ text: `${head}ok`, tone: 'ok' })
    }
  }

  if (snapshot.sourcesError !== null && snapshot.sources.length === 0) {
    sep()
    out.push({ text: 'runs ?', tone: 'warn' })
  }

  return out
}

/** Snapshots this many refresh periods old are marked in the band: the refreshes have stopped landing. */
const STALE_PERIODS = 3

/**
 * The band with what qualifies it in front: `fleet ?` while refreshes fail,
 * `as of 5m ago` once the snapshot is older than a few refresh periods.
 */
export function withHealth(band: readonly Segment[], snapshot: FleetSnapshot | null, refreshFault: string | null, nowMs: number, periodMs: number): Segment[] {
  const notes: Segment[] = []

  if (refreshFault !== null) {
    notes.push({ text: 'fleet ?', tone: 'warn' })
  }
  if (snapshot !== null && nowMs - snapshot.at > STALE_PERIODS * periodMs) {
    notes.push({ text: `as of ${agoOf(snapshot.at, nowMs)} ago`, tone: 'dim' })
  }

  return notes.length === 0 ? [...band] : [...notes.flatMap((n, i) => (i === 0 ? [n] : [{ text: ' ', tone: 'dim' as const }, n])), { text: ' │ ', tone: 'dim' }, ...band]
}

/** The framing line the engine may put above a message from another session. */
const PEER_PREFIX = /^(?:Another Claude session|A peer session) sent a message[^\n]*:[ \t]*\n?/

/** The envelope a delivery's body sits in; a nested one is escaped with a backslash. */
const ENVELOPE = /^<\\?(cross-session-message|agent-message|teammate-message)\b([^>]*)>/

/**
 * What a toast says about a message delivered to this session: its kind, the
 * sender's name when the envelope has one, and the body's first line. Null for
 * a hand-back from one of this session's own agents (an `agent-message`
 * envelope), which is not another session's message.
 */
export function peerToastOf(kind: string, text: string): string | null {
  const body = text.trimStart().replace(PEER_PREFIX, '').trimStart()
  const envelope = ENVELOPE.exec(body)

  if (envelope?.[1] === 'agent-message') {
    return null
  }

  const attributes = envelope?.[2] ?? ''
  // The display name the sender gave, else its address unless that is a socket path.
  const name = /\bfrom-name="([^"]+)"/.exec(attributes)?.[1] ?? /\bfrom="([^"]+)"/.exec(attributes)?.[1]
  const from = name === undefined || name.startsWith('uds:') ? '' : ` from ${firstLineOf(name, 40)}`
  const inner = envelope === null ? body : body.slice(envelope[0].length).replace(/<\\?\/[\w-]+>\s*$/, '')

  return `${kind}${from}: ${firstLineOf(inner, 100) || '(empty)'}`
}

/** Cuts the band to `columns`: whole pieces while they fit, then the next one shortened with an ellipsis. */
export function fitted(segments: readonly Segment[], columns: number): Segment[] {
  const out: Segment[] = []
  let left = Math.max(0, Math.floor(columns))

  for (const s of segments) {
    if (s.text.length <= left) {
      out.push(s)
      left -= s.text.length
      continue
    }
    if (left > 1) {
      out.push({ text: `${s.text.slice(0, left - 1)}…`, tone: s.tone })
    }
    break
  }

  return out
}

/** The tab the band opens: what needs the person most. */
export function urgentTab(snapshot: FleetSnapshot | null, acked: ReadonlySet<string>, fallback: FleetTab): FleetTab {
  if (snapshot === null) {
    return fallback
  }
  if (snapshot.runs.some(r => toneOf(r, acked) === 'fail')) {
    return 'runs'
  }
  if (snapshot.tracks.some(t => t.isBlocked || t.conflicts > 0 || t.checks === 'failing')) {
    return 'tracks'
  }
  if (snapshot.sessions.some(s => s.isBlocked)) {
    return 'sessions'
  }

  return fallback
}

/** The status line: the oldest unacknowledged run failure, and how many more there are; undefined when none. */
export function statusOf(snapshot: FleetSnapshot | null, acked: ReadonlySet<string>): string | undefined {
  const fails = (snapshot?.runs ?? []).filter(r => toneOf(r, acked) === 'fail').sort((a, b) => a.mtimeMs - b.mtimeMs)
  const [f] = fails

  if (f === undefined) {
    return undefined
  }

  const where = f.kind === 'late' ? f.source : `${f.source} ${f.name}${f.mtimeMs > 0 ? ` ${dayOf(f.mtimeMs)}` : ''}`

  return `${where}: ${f.why}${fails.length > 1 ? ` (+${fails.length - 1} more, /fleet runs)` : ''}`
}

/** The acked keys kept in $.store, dropping what is not a string; the newest 500. */
export function ackedOf(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((k): k is string => typeof k === 'string').slice(-500) : []
}

/** A field's kind, as a stored snapshot is checked: `?` also allows null. */
type Kind = 'string' | 'string?' | 'number' | 'number?' | 'boolean' | 'strings'

const isKind = (value: unknown, kind: Kind): boolean =>
  kind === 'strings'
    ? Array.isArray(value) && value.every(v => typeof v === 'string')
    : kind.endsWith('?')
      ? value === null || typeof value === kind.slice(0, -1)
      : typeof value === kind

const hasShape = (value: unknown, shape: Readonly<Record<string, Kind>>): boolean =>
  isRecord(value) && Object.entries(shape).every(([field, kind]) => isKind(value[field], kind))

const listOf = (value: unknown, shape: Readonly<Record<string, Kind>>): boolean => Array.isArray(value) && value.every(v => hasShape(v, shape))

/** Every field of a snapshot and of its rows (types/index.d.ts): a stored snapshot is drawn only when all of them fit. */
const SNAPSHOT_SHAPE: Readonly<Record<string, Kind>> = {
  at: 'number',
  tookMs: 'number',
  config: 'string',
  tracksOn: 'boolean',
  tracksError: 'string?',
  tracksMissing: 'boolean',
  sessionsOn: 'boolean',
  sessionsError: 'string?',
  sessionsMissing: 'boolean',
  sourcesError: 'string?',
}
const REPO_SHAPE: Readonly<Record<string, Kind>> = { name: 'string', primary: 'string' }
const TRACK_SHAPE: Readonly<Record<string, Kind>> = {
  repo: 'string',
  name: 'string',
  status: 'string',
  goal: 'string?',
  sessionId: 'string?',
  owner: 'string?',
  isLive: 'boolean',
  reviewId: 'string?',
  reviewState: 'string?',
  checks: 'string?',
  checkedAt: 'number?',
  reviewUrl: 'string?',
  lastActivity: 'number?',
  warnings: 'strings',
  conflicts: 'number',
  isBlocked: 'boolean',
  waitingFor: 'string?',
  isStale: 'boolean',
}
const SESSION_SHAPE: Readonly<Record<string, Kind>> = {
  sessionId: 'string',
  shortId: 'string?',
  name: 'string?',
  kind: 'string',
  state: 'string?',
  status: 'string?',
  waitingFor: 'string?',
  startedAt: 'number?',
  cwd: 'string?',
  isLive: 'boolean',
  isBlocked: 'boolean',
  track: 'string?',
}
const RUN_SHAPE: Readonly<Record<string, Kind>> = {
  key: 'string',
  source: 'string',
  kind: 'string',
  name: 'string',
  path: 'string',
  size: 'number',
  mtimeMs: 'number',
  verdict: 'string',
  tone: 'string',
  why: 'string',
  detail: 'string',
}
const SOURCE_SHAPE: Readonly<Record<string, Kind>> = { label: 'string', kind: 'string', glob: 'string', error: 'string?', count: 'number' }

/**
 * A snapshot read back from $.store, or null when it is not one this build
 * wrote: another schema, or any field or row the band and the pane read of
 * another shape (a hand-edited or corrupted store file, a build that changed a
 * row and kept SCHEMA). Null is read again, never drawn.
 */
export function snapshotOf(value: unknown): FleetSnapshot | null {
  if (!isRecord(value) || value.schema !== SCHEMA || !hasShape(value, SNAPSHOT_SHAPE)) {
    return null
  }

  const rows =
    listOf(value.repos, REPO_SHAPE) &&
    listOf(value.tracks, TRACK_SHAPE) &&
    listOf(value.sessions, SESSION_SHAPE) &&
    listOf(value.runs, RUN_SHAPE) &&
    listOf(value.sources, SOURCE_SHAPE)

  return rows ? (value as unknown as FleetSnapshot) : null
}

// ------------------------------------------------------------------ the pane's lines

/** One track's line in the pane: status, review, owner, the first warning. */
export function trackLineOf(t: FleetTrack, nowMs: number): Segment {
  const review =
    t.reviewId === null
      ? ''
      : ` · #${t.reviewId} ${t.reviewState ?? '?'}${t.checks === null ? '' : ` ${t.checks}`}${t.checkedAt === null ? '' : ` (checked ${agoOf(t.checkedAt, nowMs)} ago)`}`
  const owner = t.isBlocked
    ? ` · waiting on you${t.waitingFor === null ? '' : `: ${t.waitingFor}`}`
    : t.owner === null
      ? ' · unclaimed'
      : ` · ${t.owner}`
  const conflicts = t.conflicts > 0 ? ` · ${plural(t.conflicts, 'conflict')}` : ''
  const warning = t.warnings.find(w => !w.startsWith('owner session ended') && w !== 'unclaimed')
  const tone: FleetTone =
    t.isBlocked || t.conflicts > 0 || t.checks === 'failing'
      ? 'fail'
      : t.isStale || t.status === 'merged'
        ? 'warn'
        : t.status === 'review'
          ? 'busy'
          : t.status === 'paused'
            ? 'dim'
            : 'ok'

  return { text: `${t.name} · ${t.status}${review}${owner}${conflicts}${warning === undefined ? '' : ` · ${warning}`}`, tone }
}

/** One session's line in the pane. */
export function sessionLineOf(s: FleetSession, nowMs: number): Segment {
  const state = s.isBlocked
    ? `waiting on you${s.waitingFor === null ? '' : `: ${s.waitingFor}`}`
    : [s.state, s.status].filter((x): x is string => x !== null).join('/') || 'ended'
  const age = s.startedAt === null ? '' : ` · ${agoOf(s.startedAt, nowMs)}`
  const tone: FleetTone =
    s.isBlocked || s.state === 'failed' ? 'fail' : s.status === 'busy' || s.state === 'working' ? 'busy' : s.isLive ? 'ok' : 'dim'

  return { text: `${s.name ?? s.shortId ?? s.sessionId.slice(0, 8)} · ${s.kind} · ${state}${age}${s.track === null ? '' : ` · ${s.track}`}`, tone }
}

/** One run's line in the pane; `acked` before the reason, where a cut line still shows it. */
export function runLineOf(r: FleetRun, acked: ReadonlySet<string>): Segment {
  const when = r.mtimeMs > 0 ? ` ${stampOf(r.mtimeMs)}` : ''
  const seen = r.tone === 'fail' && acked.has(r.key) ? ' · acked' : ''

  return { text: `${r.kind === 'late' ? 'schedule' : r.name}${when}${seen} · ${r.why}`, tone: toneOf(r, acked) }
}
