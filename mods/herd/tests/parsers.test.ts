import { describe, expect, test } from 'claude-code/testing'

import {
  bandOf,
  configOf,
  dayOf,
  factsOfStream,
  fitted,
  globParts,
  isAsking,
  peerToastOf,
  withHealth,
  joined,
  pickLogs,
  runLineOf,
  runNameOf,
  sessionsOf,
  snapshotOf,
  sourcesOf,
  statusOf,
  tracksOf,
  verdictOfLines,
  verdictOfStream,
} from '../hooks/herd'
import { errorText, readRuns, readSnapshot } from '../hooks/read'
import type { Reader } from '../hooks/read'
import {
  AGENTS,
  HAND_BACK,
  HOME,
  HOUR,
  LOG_ASKED,
  LOG_DENIED,
  LOG_FAILED,
  LOG_KILLED,
  LOG_NO_STATS,
  LOG_OK,
  LOG_OK_SPACED,
  LOG_SAYS_WAITING,
  LOG_STOPPED_BY_MODEL,
  LOG_STOPPED_BY_OTHER,
  LOG_SUBAGENT_FAILED,
  LOG_TWO_RESULTS,
  LOG_UNFINISHED,
  LOG_WAITING,
  LOGS,
  MIN,
  NOW,
  PEER_ENVELOPE,
  SOURCES,
  SOURCES_FILE,
  SHARED_LOG_NEXT_RUN,
  SHARED_LOG_OK,
  STATUS,
  WEEKLY_OK,
  WEEKLY_SKIP,
  logWithLongResult,
} from './fixtures'
import type { File } from './fixtures'

const judge = (log: string, ageMs = 2 * HOUR) => verdictOfStream(factsOfStream(log), NOW - ageMs, NOW, 15)

describe('stream-json verdicts', () => {
  test('a run that finished reads ok, with its turns, cost and denials', () => {
    expect(judge(LOG_OK)).toEqual({ verdict: 'ok', tone: 'ok', why: 'ok', detail: 'success · 7 turns · ≈$0.48 · 0 denied' })
  })

  test('rc=0 and success, but a background subagent was killed: unfinished', () => {
    const facts = factsOfStream(LOG_UNFINISHED)

    expect(facts.stats).toEqual({ spawned: 1, completed: 0, failed: 0, killed: 1, stopped: 0 })
    expect(facts.tasks).toEqual({ spawned: 1, completed: 0, failed: 0, killed: 1, stopped: 0 })
    expect(facts.isTerminated).toBe(true)
    expect(judge(LOG_UNFINISHED)).toEqual({
      verdict: 'unfinished',
      tone: 'fail',
      why: 'unfinished: 1 subagent killed',
      detail: 'success · 20 turns · ≈$2.42 · 0 denied · subagents 0/1 done · 1 killed',
    })
  })

  test('a subagent the model stopped itself (TaskStop, killed.parent) is not unfinished work; any other stop is', () => {
    const facts = factsOfStream(LOG_STOPPED_BY_MODEL)

    expect(facts.stats).toEqual({ spawned: 2, completed: 1, failed: 0, killed: 0, stopped: 1 })
    // The task events see a stop too, but cannot say whose: with stats, they do not decide.
    expect(facts.tasks).toMatchObject({ spawned: 2, completed: 1, killed: 1 })
    expect(judge(LOG_STOPPED_BY_MODEL)).toEqual({
      verdict: 'ok',
      tone: 'ok',
      why: 'ok',
      detail: 'success · 9 turns · ≈$0.48 · 0 denied · subagents 1/2 done · 1 stopped by the model',
    })
    expect(judge(LOG_STOPPED_BY_OTHER)).toMatchObject({ verdict: 'unfinished', tone: 'fail', why: 'unfinished: 1 subagent killed' })
    // A build that writes kills as one number: every kill counts.
    expect(factsOfStream(LOG_STOPPED_BY_MODEL.replace('"killed":{"parent":1,"user":0,"system":0}', '"killed":1')).stats).toMatchObject({ killed: 1, stopped: 0 })
  })

  test('a cost no result carries is unknown, not ≈$0.00', () => {
    const noCost = factsOfStream(LOG_OK.replace('"total_cost_usd":0.48,', ''))

    expect(noCost.cost).toBeNull()
    expect(judge(LOG_OK.replace('"total_cost_usd":0.48,', '')).detail).toBe('success · 7 turns · 0 denied')
  })

  test('a result still waiting on its background work, written a moment ago, is running', () => {
    expect(judge(LOG_WAITING, 2 * MIN)).toMatchObject({ verdict: 'running', tone: 'busy', why: 'waiting on 1 background subagent' })
    expect(judge(LOG_WAITING, 2 * HOUR)).toMatchObject({ verdict: 'unfinished', tone: 'fail', why: 'unfinished: 1 of 1 subagent not finished' })
  })

  test('permission denials are counted from the result and read as a warning', () => {
    expect(factsOfStream(LOG_DENIED).denied).toBe(1)
    expect(judge(LOG_DENIED)).toMatchObject({ verdict: 'denied', tone: 'warn', why: '1 permission denial' })
  })

  test('no result event: running while fresh, quiet for a while (a long tool call?), then killed or crashed', () => {
    expect(judge(LOG_KILLED, 3 * MIN)).toMatchObject({ verdict: 'running', tone: 'busy' })
    expect(judge(LOG_KILLED, 20 * MIN)).toMatchObject({ verdict: 'quiet', tone: 'warn', why: 'no result, no write for 20m: a long tool call, or dead' })
    expect(judge(LOG_KILLED, 3 * HOUR)).toEqual({
      verdict: 'no result',
      tone: 'fail',
      why: 'no result event: killed or crashed',
      detail: 'last write 3h ago',
    })
  })

  test('an error subtype is a failure that names its first error', () => {
    expect(judge(LOG_FAILED)).toMatchObject({ verdict: 'failed', tone: 'fail', why: 'failed: Reached maximum number of turns (40)' })
    expect(judge(LOG_FAILED).detail).toContain('max_turns')
  })

  test('a run that ended on a question it needed answered is a warning; an offer of more work is not', () => {
    expect(judge(LOG_ASKED)).toMatchObject({ verdict: 'asked', tone: 'warn', why: 'ended on a question: Which do you want?' })
    expect(isAsking('Two queries returned no rows.\n\n1. Publish anyway\n2. Wait\n\n**Shall I go ahead?**')).toBe(true)
    expect(isAsking('The data is late. Should I publish with the old data or wait?')).toBe(true)
    expect(isAsking('Report written to output/report.md. Want me to also email it?')).toBe(false)
    expect(isAsking('Report written to output/report.md.\n\nShall I send it?')).toBe(false)
    expect(isAsking('Done.')).toBe(false)
    expect(isAsking('Email drafted. Should I send it now or keep it as a draft?')).toBe(true)
    // An `or` alone is not a choice put to the person: a report may end musing.
    expect(isAsking('Root cause still open: is it the late feed or the routing change?')).toBe(false)
  })

  test('closing offers and pleasantries, and a summary list before them, are not questions the run needed answered', () => {
    for (const text of [
      'Report published. Any questions or concerns?',
      'Done. Would you like me to dig deeper into any of these, or is this sufficient?',
      'All pages republished. Let me know if anything looks off or needs changes?',
      'Summary above. Is there anything else you need, or shall I close out?',
      'Done.\n\n1. Sales report refreshed\n2. Stock report refreshed\n3. Returns report refreshed\n\nAnything else?',
      'Finished the report.\n\nNext steps you might consider:\n1. Review the outliers\n2. Rerun on Tuesday\n\nWant me to schedule that?',
      'Done.\n\n1. Sales report refreshed\n2. Stock report refreshed\n3. ✅ Returns\n\nShall I send it?',
    ]) {
      expect({ text, asked: isAsking(text) }).toEqual({ text, asked: false })
    }
  })

  test('two results: turns and denials add up, cost is the cumulative one, the last decides', () => {
    const facts = factsOfStream(LOG_TWO_RESULTS)

    expect(facts.results).toBe(2)
    expect(facts.turns).toBe(32)
    expect(facts.cost).toBe(4.41)
    expect(judge(LOG_TWO_RESULTS)).toMatchObject({ verdict: 'denied', detail: 'success · 32 turns · ≈$4.41 · 1 denied · subagents 1/1 done · 2 results' })
  })

  test('with no subagent_stats the task events count, and then the words', () => {
    expect(factsOfStream(LOG_NO_STATS).tasks).toEqual({ spawned: 1, completed: 0, failed: 0, killed: 0, stopped: 0 })
    expect(judge(LOG_NO_STATS)).toMatchObject({ verdict: 'unfinished', why: 'unfinished: 1 of 1 subagent not finished' })
    expect(judge(LOG_SAYS_WAITING)).toMatchObject({ verdict: 'unfinished', why: 'unfinished: says "Now waiting for the review agent to finish."' })
  })

  test('the words fallback fires only on waiting for background work, never on "no longer waiting"', () => {
    const says = (text: string) => judge(LOG_SAYS_WAITING.replace('Built the page.\\nNow waiting for the review agent to finish.', text))

    expect(says('No longer waiting for the upstream data.')).toMatchObject({ verdict: 'ok' })
    expect(says('Waiting for the upstream data to land before next week.')).toMatchObject({ verdict: 'ok' })
    expect(says("I'm not waiting on the review agent any more.")).toMatchObject({ verdict: 'ok' })
    expect(says('Started both; still waiting on 2 background tasks.')).toMatchObject({ verdict: 'unfinished' })
    expect(says("I'm still waiting for the review agent.")).toMatchObject({ verdict: 'unfinished' })
    // Nothing left waiting, someone else waiting, or waiting in the past: finished.
    for (const text of [
      'All 3 agents finished; nothing left waiting for the subagents.',
      'The pipeline is no longer blocked: nobody is waiting on the agents now.',
      'Done. I was waiting for the background agent, and it has now returned its report.',
      'Note: the cron is waiting for 2 jobs upstream before Tuesday (not ours).',
    ]) {
      expect({ text, verdict: says(text).verdict }).toEqual({ text, verdict: 'ok' })
    }
  })

  test('a failed subagent is unfinished work, even beside a completed one', () => {
    const facts = factsOfStream(LOG_SUBAGENT_FAILED)

    expect(facts.stats).toEqual({ spawned: 2, completed: 1, failed: 1, killed: 0, stopped: 0 })
    expect(facts.tasks).toEqual({ spawned: 1, completed: 0, failed: 1, killed: 0, stopped: 0 })
    expect(judge(LOG_SUBAGENT_FAILED)).toMatchObject({
      verdict: 'unfinished',
      tone: 'fail',
      why: 'unfinished: 1 subagent failed',
      detail: 'success · 7 turns · ≈$0.48 · 0 denied · subagents 1/2 done · 1 failed',
    })
  })

  test('JSON written with spaces parses as compact JSON does', () => {
    expect(LOG_OK_SPACED).toContain('"type": "result"')
    expect(factsOfStream(LOG_OK_SPACED)).toMatchObject({ results: 1, turns: 7 })
    expect(judge(LOG_OK_SPACED)).toMatchObject({ verdict: 'ok', tone: 'ok' })
  })

  test('a quoted result event inside a message is not an event, and a cut first line is skipped', () => {
    expect(factsOfStream(LOG_KILLED).results).toBe(0)
    expect(factsOfStream(`{"type":"resu${LOG_OK}`, true)).toMatchObject({ results: 1, isPartial: true })
  })
})

describe('text log verdicts', () => {
  const match = /=== Weekly run done|SKIP:/
  const ok = /done; (?:\w+_rc=0 ?)+===/

  test('the last matching line decides', () => {
    expect(verdictOfLines(WEEKLY_OK, match, ok, NOW - HOUR, NOW, 15)).toMatchObject({ verdict: 'ok', tone: 'ok' })
    expect(verdictOfLines(WEEKLY_SKIP, match, ok, NOW - HOUR, NOW, 15)).toMatchObject({
      verdict: 'failed',
      tone: 'fail',
      why: '[09:30] SKIP: login stale, not running',
    })
  })

  test('no matching line: running while fresh, a warning after', () => {
    const started = '[09:30] === Weekly run start ===\n'

    expect(verdictOfLines(started, match, ok, NOW - 2 * MIN, NOW, 15)).toMatchObject({ verdict: 'running' })
    expect(verdictOfLines(started, match, ok, NOW - HOUR, NOW, 15)).toMatchObject({ verdict: 'no line', tone: 'warn' })
  })

  test('a log several jobs append to: with start, only the verdict after the last run start counts', () => {
    const watchdog = /^watchdog: (todo en orden|PROBLEMAS)/
    const fine = /todo en orden/
    const start = /^=== pull_leads ===$/

    // Without start, the newest verdict line in the file decides, whoever wrote after it.
    expect(verdictOfLines(SHARED_LOG_NEXT_RUN, watchdog, fine, NOW - HOUR, NOW, 15)).toMatchObject({ verdict: 'ok' })
    // With it, last week's verdict is not this run's: running while fresh, a warning after.
    expect(verdictOfLines(SHARED_LOG_NEXT_RUN, watchdog, fine, NOW - 5 * MIN, NOW, 15, start)).toMatchObject({ verdict: 'running' })
    expect(verdictOfLines(SHARED_LOG_NEXT_RUN, watchdog, fine, NOW - HOUR, NOW, 15, start)).toEqual({
      verdict: 'no line',
      tone: 'warn',
      why: 'no line matching /^watchdog: (todo en orden|PROBLEMAS)/ after the last run start',
      detail: '',
    })
    expect(verdictOfLines(SHARED_LOG_OK, watchdog, fine, NOW - HOUR, NOW, 15, start)).toMatchObject({ verdict: 'ok', why: 'watchdog: todo en orden' })
    expect(verdictOfLines('watchdog: todo en orden\n', watchdog, fine, NOW - HOUR, NOW, 15, start)).toMatchObject({
      verdict: 'no line',
      why: 'no run start matching /^=== pull_leads ===$/',
    })
  })
})

describe('sources and globs', () => {
  test('the runs option takes Label=glob or a bare glob; the file adds runs and lines', () => {
    const sources = sourcesOf(['Nightly=~/logs/n-*.jsonl', '/var/log/x-*.jsonl', '  '], SOURCES_FILE)

    expect(sources.runs.map(r => [r.label, r.glob, r.expectEveryHours])).toEqual([
      ['Nightly', '~/logs/n-*.jsonl', null],
      ['runs', '/var/log/x-*.jsonl', null],
      ['Nightly', `${LOGS}/nightly-*.jsonl`, 30],
    ])
    expect(sources.lines.map(l => [l.label, l.match.source])).toEqual([['Weekly', '=== Weekly run done|SKIP:']])
    expect(sources.errors).toEqual([])
  })

  test('a malformed entry is named and skipped, the rest still load', () => {
    const sources = sourcesOf([], { runs: [{ glob: '/x/*.jsonl' }, { label: 'A', glob: '/a/*.jsonl' }], lines: [{ label: 'B', glob: '/b/*.log', match: '(' }] })

    expect(sources.runs.map(r => r.label)).toEqual(['A'])
    expect(sources.lines).toEqual([])
    expect(sources.errors.join('\n')).toMatch(/runs\[0\] needs a label and a glob/)
    expect(sources.errors.join('\n')).toMatch(/lines\[0\]\.match: bad pattern/)
    expect(sourcesOf([], [1, 2]).errors).toEqual(['sources file: not a JSON object'])
  })

  test('a bad ok or start pattern skips its entry: it never turns every line ok', () => {
    const sources = sourcesOf([], {
      lines: [
        { label: 'A', glob: '/a/*.log', match: 'done', ok: '(' },
        { label: 'B', glob: '/b/*.log', match: 'done', start: '[' },
        { label: 'C', glob: '/c/*.log', match: 'done', start: '^=== start' },
      ],
    })

    expect(sources.lines.map(l => [l.label, l.start?.source ?? null])).toEqual([['C', '^=== start']])
    expect(sources.errors.join('\n')).toMatch(/lines\[0\]\.ok: bad pattern[\s\S]*lines\[1\]\.start: bad pattern/)
  })

  test('a glob splits into a directory and a file-name pattern, ~ under HOME', () => {
    const { dir, pattern } = globParts('~/logs/nightly-*.jsonl', HOME)

    expect(dir).toBe(`${HOME}/logs`)
    expect(pattern.test('nightly-a-2026-10-05.jsonl')).toBe(true)
    expect(pattern.test('nightly-a-2026-10-05.log')).toBe(false)
    expect(() => globParts('logs/*.jsonl', HOME)).toThrow(/absolute/)
    expect(() => globParts('/logs/*/x.jsonl', HOME)).toThrow(/wildcards only in the file name/)
  })

  test('the newest log of each run name, a trailing date dropped', () => {
    expect(runNameOf('nightly-retail-2026-10-05.jsonl')).toBe('nightly-retail')
    expect(runNameOf('nightly_20261005T0300.jsonl')).toBe('nightly')
    expect(runNameOf('plain.jsonl')).toBe('plain')
    // A second run of the day is the same run; an epoch stamp or a build number is not a date.
    expect(runNameOf('run-2026-10-09-2.jsonl')).toBe('run')
    expect(runNameOf('nightly_20261009_0930.jsonl')).toBe('nightly')
    expect(runNameOf('run-1728468000.jsonl')).toBe('run-1728468000')
    expect(runNameOf('build-12345678.jsonl')).toBe('build-12345678')
    expect(runNameOf('v20261009.jsonl')).toBe('v20261009')
    expect(runNameOf('2026-10-09.jsonl')).toBe('2026-10-09')

    const files = [
      { name: 'a-2026-10-09.jsonl', size: 1, mtimeMs: NOW - HOUR },
      { name: 'a-2026-10-08.jsonl', size: 1, mtimeMs: NOW - 25 * HOUR },
      { name: 'b-2026-10-01.jsonl', size: 1, mtimeMs: NOW - 9 * 24 * HOUR },
      { name: 'c-2026-10-09.jsonl', size: 1, mtimeMs: NOW - 2 * HOUR },
    ]

    expect(pickLogs(files, /\.jsonl$/, NOW, 192, 12).map(f => f.name)).toEqual(['a-2026-10-09.jsonl', 'c-2026-10-09.jsonl'])
    expect(pickLogs(files, /\.jsonl$/, NOW, 192, 1).map(f => f.name)).toEqual(['a-2026-10-09.jsonl'])
  })

  test('options default and bound', () => {
    expect(configOf({})).toMatchObject({ isBandOn: true, tracksOn: true, sessionsOn: true, shepherd: 'shepherd', claude: 'claude', runs: [], sourcesFile: null, periodMs: 60_000 })
    expect(configOf({ refresh_seconds: 1, runs: 'A=/x/*.jsonl', sources_file: ' ' })).toMatchObject({ periodMs: 15_000, runs: ['A=/x/*.jsonl'], sourcesFile: null })
  })
})

describe('tracks and sessions', () => {
  test("shepherd's tracks, joined to their owners' sessions", () => {
    const { repos, tracks } = tracksOf(STATUS, NOW, 24 * HOUR)
    const both = joined(tracks, sessionsOf(AGENTS))

    expect(repos).toEqual([{ name: 'demo-repo', primary: '/work/demo-repo' }])
    expect(both.tracks.map(t => [t.name, t.status, t.isBlocked, t.isStale])).toEqual([
      ['parser-fix', 'active', true, false],
      ['docs', 'review', false, false],
      ['old-idea', 'active', false, true],
    ])
    expect(both.tracks[0]?.waitingFor).toBe('permission prompt')
    expect(both.tracks[1]).toMatchObject({ reviewId: '6', reviewState: 'open', checks: 'passing', checkedAt: NOW - 3 * HOUR })
    expect(both.sessions.find(x => x.name === 'demo-2')?.track).toBe('demo-repo/docs')
    expect(both.sessions.find(x => x.name === 'nightly report')).toMatchObject({ kind: 'background', state: 'done', isLive: false })
  })

  test('JSON that is not theirs throws, so the part shows an error', () => {
    expect(() => tracksOf({ nope: 1 }, NOW, HOUR)).toThrow(/not shepherd status JSON/)
    expect(() => sessionsOf({})).toThrow(/not claude agents JSON/)
  })
})

/** What a canned command does: print this, exit with this, or fail to start with this error. */
type Canned = string | { exitCode: number; stderr: string } | Error

/** A Reader over files and canned commands, for read.ts without the engine. */
function readerOf(files: Map<string, File>, commands: Record<string, Canned> = {}): Reader & { ran: string[][]; reads: string[] } {
  const ran: string[][] = []
  const reads: string[] = []

  return {
    ran,
    reads,
    now: async () => NOW,
    run: async argv => {
      ran.push([...argv])

      const [bin, a, b, c] = argv

      if (bin === 'tail') {
        const text = files.get(c ?? '')?.text ?? ''

        return { exitCode: 0, stdout: text.slice(-Number(b)), stderr: '', isStdoutTruncated: false, isStderrTruncated: false }
      }

      const canned = commands[`${bin} ${a}`]

      if (canned === undefined) {
        throw new Error(`spawn ${bin} ENOENT`)
      }
      if (canned instanceof Error) {
        throw canned
      }
      if (typeof canned !== 'string') {
        return { exitCode: canned.exitCode, stdout: '', stderr: canned.stderr, isStdoutTruncated: false, isStderrTruncated: false }
      }

      return { exitCode: 0, stdout: canned, stderr: '', isStdoutTruncated: false, isStderrTruncated: false }
    },
    list: async dir => {
      const inside = [...files.entries()].filter(([p]) => p.slice(0, p.lastIndexOf('/')) === dir)

      if (inside.length === 0) {
        throw new Error(`ENOENT: no such file or directory, scandir '${dir}'`)
      }

      return inside.map(([p, f]) => ({ name: p.slice(p.lastIndexOf('/') + 1), kind: 'file' as const, size: f.size ?? f.text.length, mtimeMs: f.mtimeMs, isLink: false }))
    },
    read: async path => {
      const f = files.get(path)

      reads.push(path)

      if (f === undefined) {
        throw new Error(`ENOENT: no such file or directory, open '${path}'`)
      }

      return f.text
    },
  }
}

describe('reading the logs', () => {
  test('each source judged; a schedule that went quiet is a late failure', async () => {
    const files = new Map<string, File>([
      [SOURCES, { text: JSON.stringify(SOURCES_FILE), mtimeMs: NOW }],
      [`${LOGS}/nightly-report-2026-10-07.jsonl`, { text: LOG_OK, mtimeMs: NOW - 50 * HOUR }],
      [`${LOGS}/weekly-2026-10-05.log`, { text: WEEKLY_SKIP, mtimeMs: NOW - 96 * HOUR }],
    ])
    const got = await readRuns(readerOf(files), configOf({ sources_file: '~/herd/sources.json' }), HOME, NOW, new Map())

    expect(got.error).toBeNull()
    expect(got.runs.map(r => [r.source, r.kind, r.name, r.verdict])).toEqual([
      ['Nightly', 'stream', 'nightly-report', 'ok'],
      ['Nightly', 'late', 'Nightly', 'late'],
      ['Weekly', 'line', 'weekly', 'failed'],
    ])
    expect(got.runs[1]?.why).toBe('no run for 2d (expected every 30h)')
  })

  test('a log too big for one read is judged by its tail, and a parsed log is not read again', async () => {
    const path = `${LOGS}/big-2026-10-09.jsonl`
    const files = new Map<string, File>([[path, { text: `${'x'.repeat(100)}\n${LOG_UNFINISHED}`, mtimeMs: NOW - HOUR, size: 5_000_000 }]])
    const reader = readerOf(files)
    const cache = new Map()
    const cfg = configOf({ runs: [`Big=${LOGS}/big-*.jsonl`] })
    const first = await readRuns(reader, cfg, HOME, NOW, cache)

    await readRuns(reader, cfg, HOME, NOW, cache)

    expect(first.runs[0]).toMatchObject({ verdict: 'unfinished', detail: expect.stringContaining('tail only') })
    expect(reader.ran).toEqual([['tail', '-c', '1048576', path]])
  })

  test('a text log is read once per version: a write reads it again', async () => {
    const path = `${LOGS}/weekly-2026-10-05.log`
    const files = new Map<string, File>([[path, { text: WEEKLY_OK, mtimeMs: NOW - HOUR }]])
    const reader = readerOf(files)
    const cache = new Map()
    const cfg = configOf({ sources_file: SOURCES })

    files.set(SOURCES, { text: JSON.stringify(SOURCES_FILE), mtimeMs: NOW })
    await readRuns(reader, cfg, HOME, NOW, cache)
    await readRuns(reader, cfg, HOME, NOW, cache)
    expect(reader.reads.filter(p => p === path).length).toBe(1)

    files.set(path, { text: WEEKLY_SKIP, mtimeMs: NOW })
    const after = await readRuns(reader, cfg, HOME, NOW, cache)

    expect(reader.reads.filter(p => p === path).length).toBe(2)
    expect(after.runs.find(r => r.kind === 'line')?.verdict).toBe('failed')
  })

  test('a missing directory or sources file is named, and nothing else stops', async () => {
    const got = await readRuns(readerOf(new Map()), configOf({ runs: ['/nowhere/*.jsonl'], sources_file: '/nope.json' }), HOME, NOW, new Map())

    expect(got.sources).toEqual([{ label: 'runs', kind: 'stream', glob: '/nowhere/*.jsonl', error: "ENOENT: no such file or directory, scandir '/nowhere'", count: 0 }])
    expect(got.error).toMatch(/^sources file \/nope\.json: ENOENT/)
  })

  test('a log too long for a 1 MiB tail is read again with a longer one; too long for that, it is not judged a crash', async () => {
    const path = `${LOGS}/big-2026-10-09.jsonl`
    const cfg = configOf({ runs: [`Big=${LOGS}/big-*.jsonl`] })
    const judgeBig = async (resultLength: number) => {
      const files = new Map<string, File>([[path, { text: logWithLongResult(resultLength), mtimeMs: NOW - HOUR, size: 5_000_000 }]])
      const reader = readerOf(files)
      const got = await readRuns(reader, cfg, HOME, NOW, new Map())

      return { run: got.runs[0], tails: reader.ran.map(argv => argv[2]) }
    }

    expect(await judgeBig(2_000_000)).toMatchObject({ run: { verdict: 'ok', detail: expect.stringContaining('tail only') }, tails: ['1048576', '3500000'] })
    expect(await judgeBig(3_600_000)).toMatchObject({ run: { verdict: 'unjudged', tone: 'warn', why: 'no result event in the tail read: too short to judge' } })
  })

  test('a log two sources name is one run; two late sources with one label are two', async () => {
    const files = new Map<string, File>([
      [SOURCES, { text: JSON.stringify({ runs: [{ label: 'Nightly', glob: `${LOGS}/nightly-*.jsonl` }, { label: 'Nightly', glob: `${HOME}/other/n-*.jsonl`, expectEveryHours: 30 }, { label: 'Nightly', glob: `${HOME}/third/n-*.jsonl`, expectEveryHours: 30 }] }), mtimeMs: NOW }],
      [`${LOGS}/nightly-a-2026-10-09.jsonl`, { text: LOG_FAILED, mtimeMs: NOW - HOUR }],
      [`${HOME}/other/x.txt`, { text: '', mtimeMs: NOW }],
      [`${HOME}/third/x.txt`, { text: '', mtimeMs: NOW }],
    ])
    const got = await readRuns(readerOf(files), configOf({ runs: [`Nightly=${LOGS}/nightly-*.jsonl`], sources_file: SOURCES }), HOME, NOW, new Map())
    const keys = got.runs.map(r => r.key)

    expect(got.runs.map(r => r.verdict)).toEqual(['failed', 'late', 'late'])
    expect(new Set(keys).size).toBe(keys.length)
  })

  test('only a binary that cannot start is missing; one that fails, whatever it says, is shown as failing', async () => {
    const longPath = `/work/${'deep/'.repeat(30)}bin/shepherd`
    const engineWords = new Error(`herd: $.process.run(${longPath}) failed to start: ENOENT: no such file or directory, posix_spawn '${longPath}'`)
    const notInstalled = await readSnapshot(
      readerOf(new Map(), { [`${longPath} status`]: engineWords, 'claude agents': JSON.stringify(AGENTS) }),
      configOf({ shepherd_bin: longPath }),
      HOME,
      new Map(),
    )

    expect(notInstalled).toMatchObject({ tracksMissing: true, tracksError: expect.stringMatching(/^shepherd status: failed to start: ENOENT/) })

    const fnf = { exitCode: 1, stderr: "FileNotFoundError: [Errno 2] No such file or directory: '/home/u/.claude/shepherd/repos.json'" }
    const failing = await readSnapshot(readerOf(new Map(), { 'shepherd status': fnf, 'claude agents': JSON.stringify(AGENTS) }), configOf({}), HOME, new Map())

    expect(failing).toMatchObject({ tracksMissing: false, tracksError: expect.stringMatching(/^shepherd status exited 1: FileNotFoundError/) })
    expect(bandOf(failing, new Set()).map(s => s.text).join('')).toBe('tracks ? │ 1 waiting on you │ 1 bg working')
  })

  test('errors read reason first: the engine prefix and its path are dropped', () => {
    expect(errorText(new Error('herd: $.process.run(/a/very/long/path/bin/shepherd) aborted: still running after 15000 ms'))).toBe('aborted: still running after 15000 ms')
    expect(errorText(new Error('herd: $.store.set: store full'))).toBe('store full')
    expect(errorText('plain words')).toBe('plain words')
  })

  test('a snapshot with shepherd missing: tracks marked missing, sessions still read', async () => {
    const reader = readerOf(new Map(), { 'claude agents': JSON.stringify(AGENTS) })
    const snapshot = await readSnapshot(reader, configOf({}), HOME, new Map())

    expect(snapshot).toMatchObject({ tracksMissing: true, sessionsError: null, sessionsMissing: false })
    expect(snapshot.sessions.length).toBe(5)
    expect(reader.ran).toEqual([
      ['shepherd', 'status', '--all', '--json'],
      ['claude', 'agents', '--json', '--all'],
    ])
    expect(bandOf(snapshot, new Set()).map(s => s.text).join('')).toBe('1 waiting on you │ 1 bg working')
  })
})

describe('the band and the status line', () => {
  test('counts tracks by what they need, then sessions, then each run source', async () => {
    const files = new Map<string, File>([
      [SOURCES, { text: JSON.stringify(SOURCES_FILE), mtimeMs: NOW }],
      [`${LOGS}/nightly-deep-dive-2026-10-09.jsonl`, { text: LOG_UNFINISHED, mtimeMs: NOW - 5 * HOUR }],
      [`${LOGS}/weekly-2026-10-05.log`, { text: WEEKLY_OK, mtimeMs: NOW - 96 * HOUR }],
    ])
    const reader = readerOf(files, { 'shepherd status': JSON.stringify(STATUS), 'claude agents': JSON.stringify(AGENTS) })
    const snapshot = await readSnapshot(reader, configOf({ sources_file: SOURCES }), HOME, new Map())
    const band = bandOf(snapshot, new Set())

    const day = dayOf(NOW - 5 * HOUR)

    expect(band.map(s => s.text).join('')).toBe(
      `3 tracks: 1 blocked · 1 in review · 1 stale │ 1 waiting on you │ 1 bg working │ Nightly ${day} ✗ unfinished │ Weekly ${dayOf(NOW - 96 * HOUR)} ok`,
    )
    expect(band.find(s => s.text.includes('unfinished'))?.tone).toBe('fail')
    expect(statusOf(snapshot, new Set())).toBe(`Nightly nightly-deep-dive ${day}: unfinished: 1 subagent killed`)

    const acked = new Set(snapshot.runs.map(r => r.key))

    expect(bandOf(snapshot, acked).map(s => s.text)).toContain(`Nightly ${day} ⚠ unfinished`)
    expect(statusOf(snapshot, acked)).toBeUndefined()
  })

  test('conflicting counts tracks, as the other counts do, not pairs per track', async () => {
    const overlaps = [
      { a: 'parser-fix', b: 'docs', conflicts: ['src/x.ts'] },
      { a: 'parser-fix', b: 'old-idea', conflicts: ['src/y.ts'] },
      { a: 'docs', b: 'old-idea', conflicts: [] },
    ]
    const repo = STATUS.repos[0]
    const status = { ...STATUS, repos: [{ ...repo, overlaps }] }
    const reader = readerOf(new Map(), { 'shepherd status': JSON.stringify(status), 'claude agents': '[]' })
    const snapshot = await readSnapshot(reader, configOf({}), HOME, new Map())

    expect(snapshot.tracks.map(t => t.conflicts)).toEqual([2, 1, 1])
    expect(bandOf(snapshot, new Set()).map(s => s.text)).toContain('3 conflicting')
  })

  test('a stored snapshot is drawn only when every row has the shape the pane reads', async () => {
    const files = new Map<string, File>([
      [SOURCES, { text: JSON.stringify(SOURCES_FILE), mtimeMs: NOW }],
      [`${LOGS}/nightly-deep-dive-2026-10-09.jsonl`, { text: LOG_UNFINISHED, mtimeMs: NOW - 5 * HOUR }],
    ])
    const reader = readerOf(files, { 'shepherd status': JSON.stringify(STATUS), 'claude agents': JSON.stringify(AGENTS) })
    const snapshot = JSON.parse(JSON.stringify(await readSnapshot(reader, configOf({ sources_file: SOURCES }), HOME, new Map())))

    expect(snapshotOf(snapshot)).toEqual(snapshot)
    expect(snapshotOf({ ...snapshot, sessions: [...snapshot.sessions, { kind: 'interactive', isLive: true }] })).toBeNull()
    expect(snapshotOf({ ...snapshot, tracks: [{ ...snapshot.tracks[0], warnings: 'none' }] })).toBeNull()
    expect(snapshotOf({ ...snapshot, runs: [{ ...snapshot.runs[0], why: null }] })).toBeNull()
    expect(snapshotOf({ ...snapshot, sources: [{ ...snapshot.sources[0], count: '1' }] })).toBeNull()
    expect(snapshotOf({ ...snapshot, repos: [null] })).toBeNull()
    expect(snapshotOf({ ...snapshot, tookMs: undefined })).toBeNull()
  })

  test("an acked failure's line says so before the reason, where a cut line still shows it", async () => {
    const files = new Map<string, File>([
      [SOURCES, { text: JSON.stringify(SOURCES_FILE), mtimeMs: NOW }],
      [`${LOGS}/nightly-deep-dive-2026-10-09.jsonl`, { text: LOG_UNFINISHED, mtimeMs: NOW - 5 * HOUR }],
    ])
    const reader = readerOf(files, { 'shepherd status': '{"repos":[]}', 'claude agents': '[]' })
    const [run] = (await readSnapshot(reader, configOf({ sources_file: SOURCES }), HOME, new Map())).runs

    expect(run?.key).toBe(`${LOGS}/nightly-deep-dive-2026-10-09.jsonl@${NOW - 5 * HOUR}`)
    if (run !== undefined) {
      expect(runLineOf(run, new Set()).text).toMatch(/^nightly-deep-dive \d\d-\d\d \d\d:\d\d · unfinished: 1 subagent killed$/)
      expect(runLineOf(run, new Set([run.key])).text).toMatch(/^nightly-deep-dive \d\d-\d\d \d\d:\d\d · acked · unfinished: 1 subagent killed$/)
      expect(runLineOf(run, new Set([run.key])).tone).toBe('warn')
    }
  })

  test('the band says when refreshes fail, and when what it shows is old', async () => {
    const reader = readerOf(new Map(), { 'shepherd status': JSON.stringify(STATUS), 'claude agents': '[]' })
    const snapshot = await readSnapshot(reader, configOf({}), HOME, new Map())
    const band = bandOf(snapshot, new Set())
    const text = (segments: { text: string }[]) => segments.map(s => s.text).join('')

    expect(withHealth(band, snapshot, null, NOW + 60_000, 60_000)).toEqual(band)
    expect(text(withHealth(band, snapshot, 'store unreadable', NOW + 60_000, 60_000))).toMatch(/^herd \? │ 3 tracks/)
    expect(text(withHealth(band, snapshot, 'x', NOW + 10 * MIN, 60_000))).toMatch(/^herd \? as of 10m ago │ 3 tracks/)
  })

  test('a peer toast names the sender and the body, not the envelope; a hand-back from an agent of this session is no toast', () => {
    expect(peerToastOf('peer', PEER_ENVELOPE)).toBe('peer from cwd-14: Status check from the person')
    expect(peerToastOf('peer', `Another Claude session sent a message while you were working:\n${PEER_ENVELOPE}`)).toBe('peer from cwd-14: Status check from the person')
    expect(peerToastOf('peer', PEER_ENVELOPE.replace('<cross', '<\\cross'))).toBe('peer from cwd-14: Status check from the person')
    expect(peerToastOf('peer', '<cross-session-message from="uds:/tmp/s.sock">\nhi\n</cross-session-message>')).toBe('peer: hi')
    expect(peerToastOf('peer-send-message', 'Please merge my branch now')).toBe('peer-send-message: Please merge my branch now')
    expect(peerToastOf('peer', HAND_BACK)).toBeNull()
  })

  test('fitted keeps whole pieces and cuts the next with an ellipsis', () => {
    const segments = [
      { text: '3 tracks', tone: 'dim' as const },
      { text: ' │ ', tone: 'dim' as const },
      { text: 'Nightly 10-09 ✗ unfinished', tone: 'fail' as const },
    ]

    expect(fitted(segments, 20).map(s => s.text)).toEqual(['3 tracks', ' │ ', 'Nightly …'])
    expect(fitted(segments, 200).length).toBe(3)
  })
})
