// The fleet's reading half: one refresh over a narrow Reader that register.ts
// binds from `$` (and a test or a smoke script binds from fixtures). Every
// source is read on its own and fails on its own: a missing shepherd, a
// sources file that does not parse or a log that cannot be read is named in
// the snapshot, and the rest still shows.
import type {
  FleetInboxEntry,
  FleetRepo,
  FleetRun,
  FleetSession,
  FleetSnapshot,
  FleetSource,
  FleetTrack,
  FsEntry,
  ProcessRunInit,
  ProcessRunResult,
} from 'claude-code'

import {
  SCHEMA,
  configKeyOf,
  expandHome,
  factsOfStream,
  firstLineOf,
  globParts,
  joined,
  lastMatchOf,
  lateRun,
  pickLogs,
  runNameOf,
  sessionsOf,
  sourcesOf,
  tracksOf,
  verdictOfLine,
  verdictOfStream,
} from './fleet'
import type { Config, LineFind, LineSource, RunSource, StreamFacts, Verdict } from './fleet'

/** What a refresh needs of the engine: the clock, processes, and reads. */
export type Reader = {
  now: () => Promise<number>
  run: (argv: readonly string[], init?: ProcessRunInit) => Promise<ProcessRunResult>
  list: (path: string) => Promise<readonly FsEntry[]>
  read: (path: string) => Promise<string>
}

/** `$.process.run` per call: shepherd and claude agents answer in well under a second. */
const RUN_TIMEOUT_MS = 15_000
/** Below `$.fs.read`'s 4 MiB limit, with room; a bigger log is read from its tail. */
const WHOLE_READ_BYTES = 3_500_000
const TAIL_BYTES = 1_048_576
/** The second, longer tail when the first held no result event; under `$.process.run`'s 4 MiB of stdout. */
const LONG_TAIL_BYTES = 3_500_000
/** Parsed logs kept between refreshes, by path, size and mtime. */
const CACHE_MAX = 400

/** Shepherd's inbox and accept act as the person, never as this session's track. */
const AS_THE_PERSON = { CLAUDE_CODE_SESSION_ID: '' }

/**
 * The timer's `shepherd status` runs `git status` in every track worktree: no
 * optional index lock, so a refresh never makes the person's `git add` fail.
 */
const NO_OPTIONAL_LOCKS = { GIT_OPTIONAL_LOCKS: '0' }

const messageOf = (error: unknown): string => (error instanceof Error ? error.message : String(error))

/**
 * An error as one short line, the reason first: the engine words a failed call
 * `fleet: $.process.run(/a/long/path) failed to start: ENOENT: …`, and in a
 * docked pane the path would push the reason out of sight.
 */
export const errorText = (error: unknown): string =>
  firstLineOf(messageOf(error).replace(/^[\w@.-]+: \$\.[\w.]+(?:\([^)\n]*\))?:?\s*/, ''), 160)

/** A command's failure, tagged with whether it could not start at all (not installed here). */
type RunFailure = Error & { isMissing: boolean }

const failureOf = (message: string, isMissing: boolean): RunFailure => Object.assign(new Error(message), { isMissing })

/**
 * Runs a command that prints JSON and parses it; throws with the command's own
 * words on failure. Only a command that could not start, ENOENT, is missing: a
 * non-zero exit is the tool's own failure, whatever its stderr says.
 */
async function runJson(r: Reader, argv: readonly string[], init: ProcessRunInit = {}): Promise<unknown> {
  // The command's name, not its path: `shepherd status`.
  const what = [(argv[0] ?? '').replace(/^.*\//, ''), ...argv.slice(1, 2)].join(' ')
  let out: ProcessRunResult

  try {
    out = await r.run(argv, { timeoutMs: RUN_TIMEOUT_MS, ...init })
  } catch (error) {
    // Rejects only when it cannot start or outlives its timeout; classify the whole message, cut it after.
    throw failureOf(`${what}: ${errorText(error)}`, /\bENOENT\b/.test(messageOf(error)))
  }

  if (out.exitCode !== 0) {
    throw failureOf(`${what} exited ${out.exitCode}: ${firstLineOf(out.stderr || out.stdout, 140) || 'no output'}`, false)
  }

  try {
    return JSON.parse(out.stdout)
  } catch {
    throw failureOf(`${what} printed no JSON: ${firstLineOf(out.stdout, 100) || 'nothing'}`, false)
  }
}

type Part<T> = { value: T; error: string | null; isMissing: boolean }

async function partOf<T>(isOn: boolean, empty: T, read: () => Promise<T>): Promise<Part<T>> {
  if (!isOn) {
    return { value: empty, error: null, isMissing: false }
  }

  try {
    return { value: await read(), error: null, isMissing: false }
  } catch (error) {
    const isMissing = error instanceof Error && (error as Partial<RunFailure>).isMissing === true

    return { value: empty, error: errorText(error), isMissing }
  }
}

/** What one version of a log said: a stream log's facts, or a text log's verdict line. */
type Parsed = { kind: 'stream'; facts: StreamFacts } | { kind: 'line'; pattern: string; found: LineFind }

/** The cache of parsed logs a register keeps across refreshes, by path, size and mtime. */
export type FactsCache = Map<string, Parsed>

const remember = (cache: FactsCache, key: string, parsed: Parsed): void => {
  cache.set(key, parsed)

  while (cache.size > CACHE_MAX) {
    const [oldest] = cache.keys()

    if (oldest === undefined) {
      break
    }
    cache.delete(oldest)
  }
}

/** A log's last `bytes`, read with `tail -c`. */
async function tailOf(r: Reader, path: string, bytes: number): Promise<string> {
  const out = await r.run(['tail', '-c', String(bytes), path], { timeoutMs: RUN_TIMEOUT_MS })

  if (out.exitCode !== 0) {
    throw new Error(`tail exited ${out.exitCode}: ${firstLineOf(out.stderr, 100)}`)
  }

  return out.stdout
}

/** A log's text: whole when one read can take it, else its last MiB (`isPartial`). */
async function textOf(r: Reader, path: string, size: number): Promise<{ text: string; isPartial: boolean }> {
  return size <= WHOLE_READ_BYTES ? { text: await r.read(path), isPartial: false } : { text: await tailOf(r, path, TAIL_BYTES), isPartial: true }
}

/** Reads one stream log, parsed once per version. */
async function factsOf(r: Reader, path: string, size: number, mtimeMs: number, cache: FactsCache): Promise<StreamFacts> {
  const key = `${path}:${size}:${mtimeMs}`
  const hit = cache.get(key)

  if (hit?.kind === 'stream') {
    return hit.facts
  }

  const { text, isPartial } = await textOf(r, path, size)
  let facts = factsOfStream(text, isPartial)

  // A final result longer than the tail starts before it: read a longer one before judging.
  if (isPartial && facts.last === null) {
    facts = factsOfStream(await tailOf(r, path, LONG_TAIL_BYTES), true)
  }

  remember(cache, key, { kind: 'stream', facts })

  return facts
}

/** A text log's verdict line, found once per version of the log and patterns. */
async function lineOf(r: Reader, path: string, size: number, mtimeMs: number, s: LineSource, cache: FactsCache): Promise<LineFind> {
  const key = `${path}:${size}:${mtimeMs}`
  const pattern = `${s.match.source}\n${s.start?.source ?? ''}`
  const hit = cache.get(key)

  if (hit?.kind === 'line' && hit.pattern === pattern) {
    return hit.found
  }

  // The verdict line is near the end: a log over the read limit is judged by its tail.
  const found = lastMatchOf((await textOf(r, path, size)).text, s.match, s.start)

  remember(cache, key, { kind: 'line', pattern, found })

  return found
}

/** The files one glob names, listed once. */
async function filesOf(r: Reader, glob: string, home: string | null): Promise<{ dir: string; files: FsEntry[] }> {
  const { dir, pattern } = globParts(glob, home)
  const entries = await r.list(dir)

  return { dir, files: entries.filter(e => e.kind === 'file' && pattern.test(e.name)) }
}

const runOf = (source: string, kind: 'stream' | 'line', path: string, f: FsEntry, v: Verdict): FleetRun => ({
  key: `${path}@${f.mtimeMs}`,
  source,
  kind,
  name: runNameOf(f.name),
  path,
  size: f.size,
  mtimeMs: f.mtimeMs,
  ...v,
})

async function readStreamSource(r: Reader, s: RunSource, home: string | null, nowMs: number, cache: FactsCache): Promise<{ runs: FleetRun[]; source: FleetSource }> {
  const source: FleetSource = { label: s.label, kind: 'stream', glob: s.glob, error: null, count: 0 }
  const runs: FleetRun[] = []

  try {
    const { dir, files } = await filesOf(r, s.glob, home)
    const picked = pickLogs(files, /./, nowMs, s.maxAgeHours, s.latest)

    for (const f of picked) {
      const path = `${dir}/${f.name}`

      try {
        const facts = await factsOf(r, path, f.size, f.mtimeMs, cache)

        runs.push(runOf(s.label, 'stream', path, f, verdictOfStream(facts, f.mtimeMs, nowMs, s.runningMinutes)))
      } catch (error) {
        runs.push(runOf(s.label, 'stream', path, f, { verdict: 'unreadable', tone: 'warn', why: `unreadable: ${errorText(error)}`, detail: '' }))
      }
    }

    source.count = files.length

    const late = lateRun(s.label, s.glob, 'stream', files.length === 0 ? null : Math.max(...files.map(f => f.mtimeMs)), nowMs, s.expectEveryHours)

    if (late !== null) {
      runs.push(late)
    }
  } catch (error) {
    source.error = errorText(error)
  }

  return { runs, source }
}

async function readLineSource(r: Reader, s: LineSource, home: string | null, nowMs: number, cache: FactsCache): Promise<{ runs: FleetRun[]; source: FleetSource }> {
  const source: FleetSource = { label: s.label, kind: 'line', glob: s.glob, error: null, count: 0 }
  const runs: FleetRun[] = []

  try {
    const { dir, files } = await filesOf(r, s.glob, home)

    for (const f of pickLogs(files, /./, nowMs, s.maxAgeHours, s.latest)) {
      const path = `${dir}/${f.name}`

      try {
        const found = await lineOf(r, path, f.size, f.mtimeMs, s, cache)

        runs.push(runOf(s.label, 'line', path, f, verdictOfLine(found, s.match, s.start, s.ok, f.mtimeMs, nowMs, s.runningMinutes)))
      } catch (error) {
        runs.push(runOf(s.label, 'line', path, f, { verdict: 'unreadable', tone: 'warn', why: `unreadable: ${errorText(error)}`, detail: '' }))
      }
    }

    source.count = files.length

    const late = lateRun(s.label, s.glob, 'line', files.length === 0 ? null : Math.max(...files.map(f => f.mtimeMs)), nowMs, s.expectEveryHours)

    if (late !== null) {
      runs.push(late)
    }
  } catch (error) {
    source.error = errorText(error)
  }

  return { runs, source }
}

/** Every run source the options and the sources file name, read and judged. */
export async function readRuns(
  r: Reader,
  cfg: Config,
  home: string | null,
  nowMs: number,
  cache: FactsCache,
): Promise<{ runs: FleetRun[]; sources: FleetSource[]; error: string | null }> {
  let file: unknown = null
  let fileError: string | null = null

  if (cfg.sourcesFile !== null) {
    try {
      file = JSON.parse(await r.read(expandHome(cfg.sourcesFile, home)))
    } catch (error) {
      fileError = `sources file ${cfg.sourcesFile}: ${errorText(error)}`
    }
  }

  const parsed = sourcesOf(cfg.runs, file)
  const runs: FleetRun[] = []
  const sources: FleetSource[] = []
  const seen = new Set<string>()
  // A log two sources name (the runs option and the sources file, say) is one run: listed and counted once.
  const add = (got: { runs: FleetRun[]; source: FleetSource }): void => {
    for (const run of got.runs) {
      if (!seen.has(run.key)) {
        seen.add(run.key)
        runs.push(run)
      }
    }
    sources.push(got.source)
  }

  for (const s of parsed.runs) {
    add(await readStreamSource(r, s, home, nowMs, cache))
  }
  for (const s of parsed.lines) {
    add(await readLineSource(r, s, home, nowMs, cache))
  }

  const errors = [fileError, ...parsed.errors].filter((e): e is string => e !== null)

  return { runs, sources, error: errors.length === 0 ? null : errors.join('; ') }
}

/**
 * One refresh: shepherd's tracks and `claude agents` side by side, joined, then
 * the run logs. Never throws for a source; each part carries its own error.
 */
export async function readSnapshot(r: Reader, cfg: Config, home: string | null, cache: FactsCache): Promise<FleetSnapshot> {
  const startedAt = await r.now()
  const shepherd = expandHome(cfg.shepherd, home)
  const claude = expandHome(cfg.claude, home)

  const [tracksPart, sessionsPart] = await Promise.all([
    partOf<{ repos: FleetRepo[]; tracks: FleetTrack[] }>(cfg.tracksOn, { repos: [], tracks: [] }, async () =>
      tracksOf(await runJson(r, [shepherd, 'status', '--all', '--json'], { env: NO_OPTIONAL_LOCKS }), startedAt, cfg.staleMs),
    ),
    partOf<FleetSession[]>(cfg.sessionsOn, [], async () => sessionsOf(await runJson(r, [claude, 'agents', '--json', '--all']))),
  ])
  const both = joined(tracksPart.value.tracks, sessionsPart.value)
  const runs = await readRuns(r, cfg, home, startedAt, cache)
  const doneAt = await r.now()

  return {
    schema: SCHEMA,
    at: doneAt,
    tookMs: Math.max(0, doneAt - startedAt),
    config: configKeyOf(cfg),
    tracksOn: cfg.tracksOn,
    repos: tracksPart.value.repos,
    tracks: both.tracks,
    tracksError: tracksPart.error,
    tracksMissing: tracksPart.isMissing,
    sessionsOn: cfg.sessionsOn,
    sessions: both.sessions,
    sessionsError: sessionsPart.error,
    sessionsMissing: sessionsPart.isMissing,
    runs: runs.runs,
    sources: runs.sources,
    sourcesError: runs.error,
  }
}

// ------------------------------------------------------------------ buttons (the person's presses only)

const ENTRY_ID = /^[0-9a-f]{4,32}$/

/** Shepherd's unaccepted entries addressed to the person, in every enrolled repo. */
export async function readInbox(r: Reader, cfg: Config, home: string | null, repos: readonly FleetRepo[]): Promise<{ entries: FleetInboxEntry[]; errors: string[] }> {
  const entries: FleetInboxEntry[] = []
  const errors: string[] = []

  for (const repo of repos) {
    if (repo.primary === '') {
      continue
    }

    try {
      const items = await runJson(r, [expandHome(cfg.shepherd, home), 'inbox', '--json'], { cwd: repo.primary, env: AS_THE_PERSON })

      for (const item of Array.isArray(items) ? items : []) {
        if (typeof item !== 'object' || item === null) {
          continue
        }

        const e = item as Record<string, unknown>
        const id = typeof e.id === 'string' ? e.id : ''

        if (!ENTRY_ID.test(id)) {
          continue
        }
        entries.push({
          repo: repo.name,
          primary: repo.primary,
          id,
          at: typeof e.at === 'number' ? e.at * 1000 : null,
          type: typeof e.type === 'string' ? e.type : '?',
          from: typeof e.from === 'string' ? e.from : '?',
          text: typeof e.text === 'string' ? e.text : '',
        })
      }
    } catch (error) {
      errors.push(`${repo.name}: ${errorText(error)}`)
    }
  }

  return { entries, errors }
}

/** `shepherd accept <id>` in the entry's repo, as the person. Resolves what shepherd printed. */
export async function acceptEntry(r: Reader, cfg: Config, home: string | null, entry: FleetInboxEntry): Promise<string> {
  if (!ENTRY_ID.test(entry.id)) {
    throw new Error(`not a shepherd entry id: ${entry.id}`)
  }

  const out = await r.run([expandHome(cfg.shepherd, home), 'accept', entry.id], {
    cwd: entry.primary,
    env: AS_THE_PERSON,
    timeoutMs: RUN_TIMEOUT_MS,
  })

  if (out.exitCode !== 0) {
    throw new Error(firstLineOf(out.stderr || out.stdout, 160) || `shepherd accept exited ${out.exitCode}`)
  }

  return firstLineOf(out.stdout, 160) || `accepted ${entry.id}`
}

/**
 * `shepherd serve --daemon`: starts the dashboard when it is not running, and
 * resolves what it printed: the URL line, and the port-forward hint a remote
 * machine with no browser needs.
 */
export async function boardUrl(r: Reader, cfg: Config, home: string | null): Promise<string> {
  const out = await r.run([expandHome(cfg.shepherd, home), 'serve', '--daemon'], { timeoutMs: 20_000 })

  if (out.exitCode !== 0) {
    throw new Error(firstLineOf(out.stderr || out.stdout, 160) || `shepherd serve exited ${out.exitCode}`)
  }

  const lines = out.stdout
    .split('\n')
    .map(l => l.trim())
    .filter(l => l !== '')

  return lines.slice(0, 3).join('\n') || 'shepherd serve printed nothing'
}
