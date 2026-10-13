// The herd mod's reading half: one refresh is one `herd snapshot --json`, over
// a narrow Reader that register.ts binds from `$` (and a test binds from
// fixtures). herd reads shepherd, the sessions and the run logs and judges
// them; each part of what it prints carries its own error. The presses run
// here too: Ack through `herd ack`, Inbox, Accept and the board through shepherd.
import type { HerdInboxEntry, HerdRepo, HerdSnapshot, ProcessRunInit, ProcessRunResult } from 'claude-code'

import { expandHome, firstLineOf, notSnapshotWhy, snapshotOf } from './herd'
import type { Config } from './herd'

/** What a refresh and the presses need of the engine: the clock and processes. */
export type Reader = {
  now: () => Promise<number>
  run: (argv: readonly string[], init?: ProcessRunInit) => Promise<ProcessRunResult>
}

/** `herd snapshot` waits up to 20 s on shepherd itself; the rest answer in well under a second. */
const SNAPSHOT_TIMEOUT_MS = 30_000
const RUN_TIMEOUT_MS = 15_000

/** Shepherd's inbox and accept act as the person, never as this session's track. */
const AS_THE_PERSON = { CLAUDE_CODE_SESSION_ID: '' }

const messageOf = (error: unknown): string => (error instanceof Error ? error.message : String(error))

/**
 * An error as one short line, the reason first: the engine words a failed call
 * `herd: $.process.run(/a/long/path) failed to start: ENOENT: …`, and in a
 * docked pane the path would push the reason out of sight.
 */
export const errorText = (error: unknown): string =>
  firstLineOf(messageOf(error).replace(/^[\w@.-]+: \$\.[\w.]+(?:\([^)\n]*\))?:?\s*/, ''), 160)

/**
 * Runs a command that prints JSON and parses it; throws with the command's own
 * words on failure. A command that cannot start (ENOENT) says how to get one.
 */
async function runJson(r: Reader, argv: readonly string[], init: ProcessRunInit = {}): Promise<unknown> {
  // The command's name, not its path: `herd snapshot`.
  const what = [(argv[0] ?? '').replace(/^.*\//, ''), ...argv.slice(1, 2)].join(' ')
  let out: ProcessRunResult

  try {
    out = await r.run(argv, { timeoutMs: RUN_TIMEOUT_MS, ...init })
  } catch (error) {
    // Rejects only when it cannot start or outlives its timeout; classify the whole message, cut it after.
    const missing = /\bENOENT\b/.test(messageOf(error)) ? ' (not installed here? install.sh links it, or set its _bin option)' : ''

    throw new Error(`${what}: ${errorText(error)}${missing}`)
  }

  if (out.exitCode !== 0) {
    throw new Error(`${what} exited ${out.exitCode}: ${firstLineOf(out.stderr || out.stdout, 140) || 'no output'}`)
  }

  try {
    return JSON.parse(out.stdout)
  } catch {
    throw new Error(`${what} printed no JSON: ${firstLineOf(out.stdout, 100) || 'nothing'}`)
  }
}

/** One refresh: what `herd snapshot` prints, checked. Throws when herd cannot run or prints another shape. */
export async function readSnapshot(r: Reader, cfg: Config, home: string | null): Promise<HerdSnapshot> {
  const argv = [expandHome(cfg.herd, home), 'snapshot', '--json', '--stale-hours', String(cfg.staleHours)]
  const value = await runJson(r, argv, { timeoutMs: SNAPSHOT_TIMEOUT_MS })
  const snapshot = snapshotOf(value)

  if (snapshot === null) {
    throw new Error(`herd snapshot printed ${notSnapshotWhy(value)}`)
  }

  return snapshot
}

/** `herd ack <key>…`: marks run failures as seen for every session and the board. Resolves what herd printed. */
export async function ackRuns(r: Reader, cfg: Config, home: string | null, keys: readonly string[]): Promise<string> {
  const out = await r.run([expandHome(cfg.herd, home), 'ack', ...keys], { timeoutMs: RUN_TIMEOUT_MS })

  if (out.exitCode !== 0) {
    throw new Error(firstLineOf(out.stderr || out.stdout, 160) || `herd ack exited ${out.exitCode}`)
  }

  return firstLineOf(out.stdout, 160) || `acked ${keys.length}`
}

// ------------------------------------------------------------------ shepherd presses (the person's only)

const ENTRY_ID = /^[0-9a-f]{4,32}$/

/** Shepherd's unaccepted entries addressed to the person, in every enrolled repo. */
export async function readInbox(r: Reader, cfg: Config, home: string | null, repos: readonly HerdRepo[]): Promise<{ entries: HerdInboxEntry[]; errors: string[] }> {
  const entries: HerdInboxEntry[] = []
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
export async function acceptEntry(r: Reader, cfg: Config, home: string | null, entry: HerdInboxEntry): Promise<string> {
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
