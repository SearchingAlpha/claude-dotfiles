import { describe, expect, test } from 'claude-code/testing'

import { ackedOf, bandOf, configKeyOf, configOf, dayOf, fitted, notSnapshotWhy, peerToastOf, runLineOf, sharedOf, snapshotOf, statusOf, urgentTab, withHealth } from '../hooks/herd'
import { ackRuns, errorText, readSnapshot } from '../hooks/read'
import type { Reader } from '../hooks/read'
import { FAILED_KEY, HAND_BACK, HOME, HOUR, MIN, NIGHT, NOW, PEER_ENVELOPE, SNAPSHOT, TRACKS } from './fixtures'

const text = (segments: { text: string }[]): string => segments.map(s => s.text).join('')

/** What a canned command does: print this, exit with this, or fail to start with this error. */
type Canned = string | { exitCode: number; stderr: string } | Error

/** A Reader over canned commands, for read.ts without the engine. */
function readerOf(commands: Record<string, Canned> = {}): Reader & { ran: string[][] } {
  const ran: string[][] = []

  return {
    ran,
    now: async () => NOW,
    run: async argv => {
      ran.push([...argv])

      const canned = commands[`${(argv[0] ?? '').replace(/^.*\//, '')} ${argv[1] ?? ''}`]

      if (canned === undefined) {
        throw new Error(`spawn ${argv[0] ?? ''} ENOENT`)
      }
      if (canned instanceof Error) {
        throw canned
      }
      if (typeof canned !== 'string') {
        return { exitCode: canned.exitCode, stdout: '', stderr: canned.stderr, isStdoutTruncated: false, isStderrTruncated: false }
      }

      return { exitCode: 0, stdout: canned, stderr: '', isStdoutTruncated: false, isStderrTruncated: false }
    },
  }
}

describe('the band and the status line', () => {
  test('counts tracks by what they need, then sessions, then each run source', () => {
    const day = dayOf(NOW - 5 * HOUR)

    expect(text(bandOf(NIGHT, new Set()))).toBe(
      `3 tracks: 1 blocked · 1 in review · 1 stale │ 1 waiting on you │ 1 bg working │ Nightly ${day} ✗ unfinished │ Weekly ${dayOf(NOW - 96 * HOUR)} ok`,
    )
    expect(bandOf(NIGHT, new Set()).find(s => s.text.includes('unfinished'))?.tone).toBe('fail')
    expect(statusOf(NIGHT, new Set())).toBe(`Nightly nightly-deep-dive ${day}: unfinished: 1 subagent killed`)

    const acked = new Set([FAILED_KEY])

    expect(bandOf(NIGHT, acked).map(s => s.text)).toContain(`Nightly ${day} ⚠ unfinished`)
    expect(statusOf(NIGHT, acked)).toBeUndefined()
    expect(urgentTab(NIGHT, new Set(), 'sessions')).toBe('runs')
    expect(urgentTab(NIGHT, acked, 'sessions')).toBe('tracks')
  })

  test('conflicting counts tracks, as the other counts do, not pairs per track', () => {
    const tracks = TRACKS.map((t, i) => ({ ...t, conflicts: [2, 1, 1][i] ?? 0 }))

    expect(bandOf({ ...SNAPSHOT, tracks }, new Set()).map(s => s.text)).toContain('3 conflicting')
  })

  test('a part herd could not read: missing is left out, failing says ?', () => {
    expect(text(bandOf({ ...SNAPSHOT, tracks: [], tracksMissing: true }, new Set()))).toBe('1 waiting on you │ 1 bg working')
    expect(text(bandOf({ ...SNAPSHOT, tracks: [], tracksError: 'shepherd status exited 1' }, new Set()))).toBe('tracks ? │ 1 waiting on you │ 1 bg working')
    expect(text(bandOf({ ...SNAPSHOT, sessions: [], sessionsError: 'x' }, new Set()))).toMatch(/│ sessions \?$/)
    expect(text(bandOf({ ...SNAPSHOT, sourcesError: 'sources file /x: bad JSON' }, new Set()))).toMatch(/│ runs \?$/)
    expect(text(bandOf(null, new Set()))).toBe('herd: reading…')
  })

  test('the acks shown are the snapshot\'s and this session\'s own', () => {
    expect(ackedOf({ ...NIGHT, acked: ['a'] }, ['b', 'a'])).toEqual(['a', 'b'])
    expect(ackedOf(null, ['b'])).toEqual(['b'])
  })

  test('the band says when refreshes fail, and when what it shows is old', () => {
    const band = bandOf(SNAPSHOT, new Set())

    expect(withHealth(band, SNAPSHOT, null, NOW + 60_000, 60_000)).toEqual(band)
    expect(text(withHealth(band, SNAPSHOT, 'herd exited 1', NOW + 60_000, 60_000))).toMatch(/^herd \? │ 3 tracks/)
    expect(text(withHealth(band, SNAPSHOT, 'x', NOW + 10 * MIN, 60_000))).toMatch(/^herd \? as of 10m ago │ 3 tracks/)
    // Nothing read yet and the refresh fails: herd ? alone, not "reading…".
    expect(text(withHealth(bandOf(null, new Set()), null, 'ENOENT', NOW, 60_000))).toBe('herd ?')
  })

  test("an acked failure's line says so before the reason, where a cut line still shows it", () => {
    const run = NIGHT.runs[0]

    expect(run?.key).toBe(FAILED_KEY)
    if (run !== undefined) {
      expect(runLineOf(run, new Set()).text).toMatch(/^nightly-deep-dive \d\d-\d\d \d\d:\d\d · unfinished: 1 subagent killed$/)
      expect(runLineOf(run, new Set([run.key])).text).toMatch(/^nightly-deep-dive \d\d-\d\d \d\d:\d\d · acked · unfinished: 1 subagent killed$/)
      expect(runLineOf(run, new Set([run.key])).tone).toBe('warn')
    }
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

describe('the snapshot herd prints', () => {
  test('drawn only when every row has the shape the pane reads', () => {
    const snapshot = JSON.parse(JSON.stringify(NIGHT))

    expect(snapshotOf(snapshot)).toEqual(snapshot)
    expect(snapshotOf({ ...snapshot, schema: 2 })).toBeNull()
    expect(snapshotOf({ ...snapshot, acked: undefined })).toBeNull()
    expect(snapshotOf({ ...snapshot, host: null })).toBeNull()
    expect(snapshotOf({ ...snapshot, sessions: [...snapshot.sessions, { kind: 'interactive', isLive: true }] })).toBeNull()
    expect(snapshotOf({ ...snapshot, tracks: [{ ...snapshot.tracks[0], warnings: 'none' }] })).toBeNull()
    expect(snapshotOf({ ...snapshot, runs: [{ ...snapshot.runs[0], why: null }] })).toBeNull()
    expect(snapshotOf({ ...snapshot, runs: [{ ...snapshot.runs[0], facts: 'none' }] })).toBeNull()
    expect(snapshotOf({ ...snapshot, sources: [{ ...snapshot.sources[0], count: '1' }] })).toBeNull()
    expect(snapshotOf({ ...snapshot, repos: [null] })).toBeNull()
    expect(snapshotOf({ ...snapshot, tookMs: undefined })).toBeNull()
  })

  test('why one is not drawn, and a shared read adopted only with the same options', () => {
    expect(notSnapshotWhy({ ...SNAPSHOT, schema: 4 })).toMatch(/^schema 4, not 3: herd and the mod are out of step/)
    expect(notSnapshotWhy([])).toBe('no snapshot')
    expect(notSnapshotWhy({ ...SNAPSHOT, tracks: 'x' })).toBe('a snapshot of another shape')

    const key = configKeyOf(configOf({}))

    expect(sharedOf({ config: key, snapshot: SNAPSHOT }, key)).toEqual(SNAPSHOT)
    expect(sharedOf({ config: 'other', snapshot: SNAPSHOT }, key)).toBeNull()
    expect(sharedOf(SNAPSHOT, key)).toBeNull()
  })

  test('options default and bound', () => {
    expect(configOf({})).toEqual({ isBandOn: true, herd: 'herd', shepherd: 'shepherd', periodMs: 60_000, staleHours: 24 })
    expect(configOf({ refresh_seconds: 1, stale_hours: 0, herd_bin: ' ~/bin/herd ' })).toMatchObject({ periodMs: 15_000, staleHours: 1, herd: '~/bin/herd' })
  })
})

describe('running herd', () => {
  test('one refresh is one herd snapshot, under HOME when the path says ~', async () => {
    const reader = readerOf({ 'herd snapshot': JSON.stringify(NIGHT) })

    expect(await readSnapshot(reader, configOf({ herd_bin: '~/bin/herd', stale_hours: 48 }), HOME)).toEqual(NIGHT)
    expect(reader.ran).toEqual([['/home/user/bin/herd', 'snapshot', '--json', '--stale-hours', '48']])
  })

  test('a herd that cannot start says how to get one; one that fails or prints another shape says so', async () => {
    const cfg = configOf({})

    await expect(readSnapshot(readerOf(), cfg, HOME)).rejects.toThrow(/^herd snapshot: spawn herd ENOENT \(not installed here\? install\.sh links it/)
    await expect(readSnapshot(readerOf({ 'herd snapshot': { exitCode: 1, stderr: 'Traceback: boom' } }), cfg, HOME)).rejects.toThrow(
      'herd snapshot exited 1: Traceback: boom',
    )
    await expect(readSnapshot(readerOf({ 'herd snapshot': 'not json' }), cfg, HOME)).rejects.toThrow('herd snapshot printed no JSON: not json')
    await expect(readSnapshot(readerOf({ 'herd snapshot': JSON.stringify({ ...SNAPSHOT, schema: 2 }) }), cfg, HOME)).rejects.toThrow(
      /^herd snapshot printed schema 2, not 3/,
    )
  })

  test('Ack is herd ack with the keys; a refusal is an error with herd\'s words', async () => {
    const reader = readerOf({ 'herd ack': 'acked 1 run\n' })

    expect(await ackRuns(reader, configOf({}), HOME, [FAILED_KEY])).toBe('acked 1 run')
    expect(reader.ran).toEqual([['herd', 'ack', FAILED_KEY]])
    await expect(ackRuns(readerOf({ 'herd ack': { exitCode: 2, stderr: 'herd: ack takes run keys' } }), configOf({}), HOME, [])).rejects.toThrow(
      'herd: ack takes run keys',
    )
  })

  test('errors read reason first: the engine prefix and its path are dropped', () => {
    expect(errorText(new Error('herd: $.process.run(/a/very/long/path/bin/herd) aborted: still running after 30000 ms'))).toBe('aborted: still running after 30000 ms')
    expect(errorText(new Error('herd: $.store.set: store full'))).toBe('store full')
    expect(errorText('plain words')).toBe('plain words')
  })
})
