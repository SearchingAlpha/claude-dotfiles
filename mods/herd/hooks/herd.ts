// The herd mod's pure half: the options, the snapshot `herd snapshot --json`
// prints (checked before it is drawn), and the words the band, the status line
// and the pane say about it. No `$` and no I/O here: read.ts runs herd, this
// file decides what to show. herd itself judges the tracks, sessions and runs.
import type { HerdRun, HerdSession, HerdSnapshot, HerdSource, HerdTab, HerdTone, HerdTrack } from 'claude-code'

// ------------------------------------------------------------------ helpers

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const numberOf = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

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

// ------------------------------------------------------------------ options

export type Config = {
  isBandOn: boolean
  /** The herd and shepherd executables: a name looked up on PATH, or a path. */
  herd: string
  shepherd: string
  periodMs: number
  staleHours: number
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
  const period = numberOf(options.refresh_seconds) ?? DEFAULT_PERIOD_S
  const stale = numberOf(options.stale_hours) ?? DEFAULT_STALE_H

  return {
    isBandOn: options.band !== false,
    herd: optionText(options.herd_bin, 'herd'),
    shepherd: optionText(options.shepherd_bin, 'shepherd'),
    periodMs: Math.max(MIN_PERIOD_S, period) * 1000,
    staleHours: Math.max(1, stale),
  }
}

/**
 * The shape `herd snapshot --json` prints (SNAPSHOT_SCHEMA in herd.py). A
 * snapshot of another schema is not drawn: herd and the mod are out of step.
 */
export const SCHEMA = 3

/** What a snapshot was read with: another session's stored read is adopted only when this matches. */
export function configKeyOf(c: Config): string {
  return JSON.stringify([SCHEMA, c.herd, c.staleHours])
}

/** `~` and `~/...` under HOME; anything else as given. */
export function expandHome(path: string, home: string | null): string {
  if (home === null || (path !== '~' && !path.startsWith('~/'))) {
    return path
  }

  return home.replace(/\/$/, '') + path.slice(1)
}

/** The tab `/herd <word>` names, or null. */
export function tabOf(args: string): HerdTab | null {
  const word = args.trim().toLowerCase()

  return word === 'tracks' || word === 'sessions' || word === 'runs' ? word : null
}

/** Waiting on the person first, then working, idle, finished, and failed or stopped last. */
export function sessionRank(s: HerdSession): number {
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

// ------------------------------------------------------------------ the band and the status line

export type Segment = { text: string; tone: HerdTone }

/** A failure the person acked reads as a warning: still in the band, gone from the status line. */
export function toneOf(run: HerdRun, acked: ReadonlySet<string>): HerdTone {
  return run.tone === 'fail' && acked.has(run.key) ? 'warn' : run.tone
}

const uniqueLabels = (sources: readonly HerdSource[]): string[] => [...new Set(sources.map(s => s.label))]

/** What the band says, as colored pieces: tracks, sessions waiting, then each run source. */
export function bandOf(snapshot: HerdSnapshot | null, acked: ReadonlySet<string>): Segment[] {
  if (snapshot === null) {
    return [{ text: 'herd: reading…', tone: 'dim' }]
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
 * The band with what qualifies it in front: `herd ?` while refreshes fail,
 * `as of 5m ago` once the snapshot is older than a few refresh periods. With
 * nothing read yet and a failing refresh, `herd ?` alone: the pane says why.
 */
export function withHealth(band: readonly Segment[], snapshot: HerdSnapshot | null, refreshFault: string | null, nowMs: number, periodMs: number): Segment[] {
  if (snapshot === null && refreshFault !== null) {
    return [{ text: 'herd ?', tone: 'warn' }]
  }

  const notes: Segment[] = []

  if (refreshFault !== null) {
    notes.push({ text: 'herd ?', tone: 'warn' })
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
export function urgentTab(snapshot: HerdSnapshot | null, acked: ReadonlySet<string>, fallback: HerdTab): HerdTab {
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
export function statusOf(snapshot: HerdSnapshot | null, acked: ReadonlySet<string>): string | undefined {
  const fails = (snapshot?.runs ?? []).filter(r => toneOf(r, acked) === 'fail').sort((a, b) => a.mtimeMs - b.mtimeMs)
  const [f] = fails

  if (f === undefined) {
    return undefined
  }

  const where = f.kind === 'late' ? f.source : `${f.source} ${f.name}${f.mtimeMs > 0 ? ` ${dayOf(f.mtimeMs)}` : ''}`

  return `${where}: ${f.why}${fails.length > 1 ? ` (+${fails.length - 1} more, /herd runs)` : ''}`
}

/** The acks a snapshot carries, and those this session made since it was read: herd keeps them, so a union is never stale. */
export function ackedOf(snapshot: HerdSnapshot | null, ackedHere: readonly string[]): string[] {
  return [...new Set([...(snapshot?.acked ?? []), ...ackedHere])]
}

// ------------------------------------------------------------------ the snapshot's shape

/** A field's kind, as a snapshot is checked: `?` also allows null. */
type Kind = 'string' | 'string?' | 'number' | 'number?' | 'boolean' | 'strings' | 'record?'

const isKind = (value: unknown, kind: Kind): boolean =>
  kind === 'strings'
    ? Array.isArray(value) && value.every(v => typeof v === 'string')
    : kind === 'record?'
      ? value === null || isRecord(value)
      : kind.endsWith('?')
        ? value === null || typeof value === kind.slice(0, -1)
        : typeof value === kind

const hasShape = (value: unknown, shape: Readonly<Record<string, Kind>>): boolean =>
  isRecord(value) && Object.entries(shape).every(([field, kind]) => isKind(value[field], kind))

const listOf = (value: unknown, shape: Readonly<Record<string, Kind>>): boolean => Array.isArray(value) && value.every(v => hasShape(v, shape))

/** Every field the band and the pane read of a snapshot and its rows (types/index.d.ts). */
const SNAPSHOT_SHAPE: Readonly<Record<string, Kind>> = {
  at: 'number',
  tookMs: 'number',
  host: 'string',
  tracksOn: 'boolean',
  tracksError: 'string?',
  tracksMissing: 'boolean',
  sessionsOn: 'boolean',
  sessionsError: 'string?',
  sessionsMissing: 'boolean',
  sourcesFile: 'string?',
  sourcesError: 'string?',
  acked: 'strings',
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
  facts: 'record?',
}
const SOURCE_SHAPE: Readonly<Record<string, Kind>> = { label: 'string', kind: 'string', glob: 'string', error: 'string?', count: 'number' }

/**
 * A snapshot as `herd snapshot --json` prints it (or as another session stored
 * it), or null when it is not one this build draws: another schema, or any
 * field or row of another shape. Null is never drawn.
 */
export function snapshotOf(value: unknown): HerdSnapshot | null {
  if (!isRecord(value) || value.schema !== SCHEMA || !hasShape(value, SNAPSHOT_SHAPE)) {
    return null
  }

  const rows =
    listOf(value.repos, REPO_SHAPE) &&
    listOf(value.tracks, TRACK_SHAPE) &&
    listOf(value.sessions, SESSION_SHAPE) &&
    listOf(value.runs, RUN_SHAPE) &&
    listOf(value.sources, SOURCE_SHAPE)

  return rows ? (value as unknown as HerdSnapshot) : null
}

/** Why a value is not a snapshot this build draws, in one phrase. */
export function notSnapshotWhy(value: unknown): string {
  if (!isRecord(value)) {
    return 'no snapshot'
  }
  if (value.schema !== SCHEMA) {
    return `schema ${String(value.schema)}, not ${SCHEMA}: herd and the mod are out of step (update both from one checkout)`
  }

  return 'a snapshot of another shape'
}

/** A stored read another session shared: what it was read with, and the snapshot. */
export function sharedOf(value: unknown, key: string): HerdSnapshot | null {
  return isRecord(value) && value.config === key ? snapshotOf(value.snapshot) : null
}

// ------------------------------------------------------------------ the pane's lines

/** One track's line in the pane: status, review, owner, the first warning. */
export function trackLineOf(t: HerdTrack, nowMs: number): Segment {
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
  const tone: HerdTone =
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
export function sessionLineOf(s: HerdSession, nowMs: number): Segment {
  const state = s.isBlocked
    ? `waiting on you${s.waitingFor === null ? '' : `: ${s.waitingFor}`}`
    : [s.state, s.status].filter((x): x is string => x !== null).join('/') || 'ended'
  const age = s.startedAt === null ? '' : ` · ${agoOf(s.startedAt, nowMs)}`
  const tone: HerdTone =
    s.isBlocked || s.state === 'failed' ? 'fail' : s.status === 'busy' || s.state === 'working' ? 'busy' : s.isLive ? 'ok' : 'dim'

  return { text: `${s.name ?? s.shortId ?? s.sessionId.slice(0, 8)} · ${s.kind} · ${state}${age}${s.track === null ? '' : ` · ${s.track}`}`, tone }
}

/** One run's line in the pane; `acked` before the reason, where a cut line still shows it. */
export function runLineOf(r: HerdRun, acked: ReadonlySet<string>): Segment {
  const when = r.mtimeMs > 0 ? ` ${stampOf(r.mtimeMs)}` : ''
  const seen = r.tone === 'fail' && acked.has(r.key) ? ' · acked' : ''

  return { text: `${r.kind === 'late' ? 'schedule' : r.name}${when}${seen} · ${r.why}`, tone: toneOf(r, acked) }
}
