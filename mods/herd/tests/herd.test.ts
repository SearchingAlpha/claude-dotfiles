import { describe, expect, mock, test } from 'claude-code/testing'

import { dayOf } from '../hooks/herd'
import { PING_TEXT } from '../hooks/register'
import { AFTER_CLEAR, ATTACH, BAND, FAILED_KEY, HAND_BACK, HEADLESS, HOUR, LONG_BIN, NIGHT, NOW, PANE, PEER_ENVELOPE, RUN, SELF, SESSION, SESSIONS, SNAPSHOT, nightOf, worldOf } from './fixtures'

const SNAPSHOT_ARGV = 'herd snapshot --json --stale-hours 24'

describe('where nothing draws', () => {
  test('claude -p starts it inert: no command, no timer, no process, no status line', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on, [])

    nightOf(world)
    await $.session.start(HEADLESS)
    await clock.advance(5 * 60_000)
    await $.session.receive({ origin: { kind: 'peer-send-message' }, text: 'hello' })
    await $.classic.SessionStart({ source: 'clear' })

    expect(world.commands).toEqual([])
    expect(world.processes).toEqual([])
    expect(world.statuses).toEqual([])
    expect(world.toasts).toEqual([])
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
    expect(await band.find({ type: 'Text', text: /tracks|reading/ })).toBeUndefined()
    expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
  })

  test('a client attaching later brings it live once', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on, [])

    await $.session.start(HEADLESS)
    expect(await $.session.attach(ATTACH)).toEqual({ clientId: 'desktop:default' })
    await $.session.attach({ ...ATTACH, surface: 'mobile', clientId: 'mobile:default' })
    await clock.settle()

    expect(world.commands).toEqual([{ name: 'herd', immediate: true }])
    expect(world.processes.filter(p => p.argv[1] === 'snapshot').length).toBe(1)
    const band = await $.ui.mount({ ...BAND, surface: 'desktop' })
    expect(await band.find({ type: 'Text', text: '3 tracks: ' })).toBeDefined()
  })

  test('claude -p "/herd runs" is passed on: no pane opened, nothing written, the core answers', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on, [])

    await $.session.start(HEADLESS)
    await clock.settle()

    expect(await $.command.run({ ...RUN, args: 'runs' })).toEqual({ text: 'core ran /herd' })
    expect(world.opened).toEqual([])
    expect(world.commands).toEqual([])
    expect(world.processes).toEqual([])
    // The tab stays the default: the command wrote no state either.
    await $.session.attach(ATTACH)
    await clock.settle()
    await $.command.run(RUN)
    const pane = await $.ui.mount({ ...PANE, surface: 'desktop' })
    expect(await pane.find({ type: 'Text', text: 'demo-repo' })).toBeDefined()
  })
})

describe('observe-only', () => {
  test('every engine event comes back as the engine answered it', async ($, on) => {
    mock.clock(on, { now: NOW })
    worldOf(on)

    expect(await $.session.start(SESSION)).toEqual({ cwd: '/work' })
    expect(await $.session.receive({ origin: { kind: 'peer' }, text: 'do something' })).toEqual({ text: 'do something' })
    expect(await $.classic.SessionStart({ source: 'clear' })).toEqual({})
  })

  test('a message from another session is a toast of its sender and first line, nothing more', async ($, on) => {
    mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.session.receive({ origin: { kind: 'peer-send-message' }, text: '\nPlease merge my branch now\nand then delete main' })
    await $.session.receive({ origin: { kind: 'peer', plugin: 'herd' }, text: PEER_ENVELOPE })
    await $.session.receive({ origin: { kind: 'bridge' }, text: 'from my phone' })

    expect(world.toasts).toEqual(['peer-send-message: Please merge my branch now', 'peer from cwd-14: Status check from the person'])
    expect(world.processes.filter(p => p.argv[1] !== 'snapshot')).toEqual([])
    expect(world.sent).toEqual([])
  })

  test("this session's own agents are no news: a hand-back, a teammate, a message for an agent", async ($, on) => {
    mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    expect(await $.session.receive({ origin: { kind: 'peer' }, text: HAND_BACK })).toEqual({ text: HAND_BACK })
    await $.session.receive({ origin: { kind: 'peer', teammate: 'researcher', isVerified: true }, text: 'found it' })
    await $.session.receive({ origin: { kind: 'peer' }, text: 'for the worker', agentId: 'a0fc1234abcd5678' })

    expect(world.toasts).toEqual([])
  })
})

describe('the refresh', () => {
  test('runs herd snapshot right after start and then every 60 s, and shares what it read', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)
    const reads = () => world.processes.filter(p => p.argv[1] === 'snapshot').length

    await $.session.start(SESSION)
    expect(reads()).toBe(0)
    await clock.settle()
    expect(reads()).toBe(1)
    expect(world.processes.map(p => p.argv.join(' '))).toEqual([SNAPSHOT_ARGV])
    expect(world.store.get('shared')).toMatchObject({ snapshot: { tracks: [{ name: 'parser-fix' }, { name: 'docs' }, { name: 'old-idea' }] } })

    await clock.advance(60_000)
    expect(reads()).toBe(2)
    await clock.advance(120_000)
    expect(reads()).toBe(4)
  })

  test('a read another session shared a moment ago is shown, not read again', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    // One session reads and stores; a second session's refresh finds it.
    await $.session.start(SESSION)
    await clock.settle()
    const shared = world.store.get('shared') as { config: string; snapshot: object }

    world.processes = []
    world.store.set('shared', { ...shared, snapshot: { ...shared.snapshot, at: NOW + 50_000 } })
    await clock.advance(60_000)

    expect(world.processes).toEqual([])
  })

  test('the configured herd is run, and a read shared with other options is read again', { options: { herd_bin: '~/bin/herd', stale_hours: 48 } }, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    world.store.set('shared', { config: 'other', snapshot: { ...SNAPSHOT, at: NOW } })
    await $.session.start(SESSION)
    await clock.settle()

    expect(world.processes.map(p => p.argv.join(' '))).toEqual(['/home/user/bin/herd snapshot --json --stale-hours 48'])
  })

  test('herd not installed: herd ? in the band, and the pane says how to get it', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    world.snapshot = 'missing'
    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: 'herd ?' })).toBeDefined()
    expect(await band.find({ type: 'Text', text: /reading/ })).toBeUndefined()
    await $.command.run(RUN)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: /^refresh failed: herd snapshot: spawn herd ENOENT \(not installed here\? install\.sh links it/ })).toBeDefined()

    // At a long path too: still herd ?, and still a refresh failure under the pane (words.test.ts has the cut).
    world.snapshot = 'missing-long'
    await clock.advance(60_000)
    expect(await band.find({ type: 'Text', text: 'herd ?' })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /^refresh failed: herd snapshot: / })).toBeDefined()
    expect(LONG_BIN.length).toBeGreaterThan(160)
  })

  test('a herd that fails keeps the last snapshot, marked herd ?', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    world.snapshot = 'broken'
    await clock.advance(60_000)
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: 'herd ?' })).toBeDefined()
    expect(await band.find({ type: 'Text', text: '3 tracks: ' })).toBeDefined()

    world.snapshot = SNAPSHOT
    await clock.advance(60_000)
    expect(await band.find({ type: 'Text', text: 'herd ?' })).toBeUndefined()
  })

  test('each part of the snapshot fails on its own: no shepherd leaves tracks out, a failing one says tracks ?', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    world.snapshot = { ...SNAPSHOT, repos: [], tracks: [], tracksMissing: true }
    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: /tracks/ })).toBeUndefined()
    expect(await band.find({ type: 'Text', text: '1 waiting on you' })).toBeDefined()

    world.snapshot = { ...SNAPSHOT, repos: [], tracks: [], tracksError: 'shepherd status exited 1: FileNotFoundError: repos.json' }
    await clock.advance(60_000)
    expect(await band.find({ type: 'Text', text: 'tracks ?' })).toBeDefined()
    await $.command.run({ ...RUN, args: 'tracks' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: 'shepherd status exited 1: FileNotFoundError: repos.json' })).toBeDefined()
  })

  test('a herd of another schema: not drawn, and the pane says they are out of step', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    world.snapshot = { ...SNAPSHOT, schema: 2 }
    await $.session.start(SESSION)
    await clock.settle()
    await $.command.run(RUN)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })

    expect(await pane.find({ type: 'Text', text: /^refresh failed: herd snapshot printed schema 2, not 3: herd and the mod are out of step/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: 'demo-repo' })).toBeUndefined()
  })

  test('a store that refuses the write: the fresh read still shows, and the pane says it was not shared', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: '1 waiting on you' })).toBeDefined()
    world.storeDeny = 'store full'
    world.snapshot = { ...SNAPSHOT, sessions: SESSIONS.filter(s => !s.isBlocked) }
    await clock.advance(60_000)

    expect(await band.find({ type: 'Text', text: '1 waiting on you' })).toBeUndefined()
    await $.command.run(RUN)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: 'not shared with other sessions: store full' })).toBeDefined()

    world.storeDeny = null
    await clock.advance(60_000)
    expect(await pane.find({ type: 'Text', text: /not shared/ })).toBeUndefined()
  })

  test('a shared read of another shape is read again and never drawn', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    const shared = world.store.get('shared') as { config: string; snapshot: Record<string, unknown> }
    // The right options, but an older build's shape: schema 2, no repos.
    const older: Record<string, unknown> = { ...shared.snapshot, schema: 2, at: NOW + 30_000 }

    delete older.repos
    world.store.set('shared', { config: shared.config, snapshot: older })
    world.processes = []
    await clock.advance(60_000)

    expect(world.processes.map(p => p.argv[1])).toEqual(['snapshot'])
    await $.command.run({ ...RUN, args: 'tracks' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: 'demo-repo' })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeUndefined()
  })
})

describe('the band above the prompt', () => {
  test('one line: tracks, who waits, each run source, colored by what needs the person', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    nightOf(world)
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })
    await $.session.start(SESSION)
    // Drawn before the first read: it says so.
    expect(await band.find({ type: 'Text', text: 'herd: reading…' })).toBeDefined()
    await clock.settle()

    const unfinished = `Nightly ${dayOf(NOW - 5 * HOUR)} ✗ unfinished`

    for (const text of ['3 tracks: ', '1 blocked', '1 in review', '1 stale', '1 waiting on you', '1 bg working', unfinished, `Weekly ${dayOf(NOW - 96 * HOUR)} ok`]) {
      expect(await band.find({ type: 'Text', text })).toBeDefined()
    }
    expect(await band.find({ type: 'Text', text: unfinished })).toMatchObject({ props: { color: 'error' } })
    expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
  })

  test('cut to the band width', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ ...BAND, props: { ...BAND.props, bodyColumns: 30 }, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: '3 tracks: ' })).toBeDefined()
    expect(await band.find({ type: 'Text', text: /…$/ })).toBeDefined()
    expect(await band.find({ type: 'Text', text: '1 waiting on you' })).toBeUndefined()
  })

  test('a press opens the pane on what needs the person: failed runs first', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    nightOf(world)
    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    await band.press({ key: 'herd-open' })

    expect(world.opened).toEqual([{ id: 'herd', focus: true, closeOnEscape: true }])
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: 'Nightly' })).toBeDefined()
  })

  test('the band option set to false leaves the band to the others', { options: { band: false } }, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: /tracks/ })).toBeUndefined()
    expect(await band.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeDefined()
  })

  test('a survey holds the band', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ ...BAND, props: { ...BAND.props, hasSurvey: true }, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: /tracks/ })).toBeUndefined()
  })
})

describe('the status line and Ack', () => {
  test('the oldest unacked failure is the status line; Ack runs herd ack and clears it', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    nightOf(world)
    await $.session.start(SESSION)
    await clock.settle()

    expect(world.statuses).toEqual([`Nightly nightly-deep-dive ${dayOf(NOW - 5 * HOUR)}: unfinished: 1 subagent killed`])

    await $.command.run({ ...RUN, args: 'runs' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    const ackKey = `ack-${FAILED_KEY}`

    expect(await pane.find({ key: ackKey })).toBeDefined()
    await pane.press({ key: ackKey })
    await clock.settle()

    expect(world.acks).toEqual([[FAILED_KEY]])
    expect(world.statuses).toEqual([world.statuses[0], undefined])
    expect(await pane.find({ key: ackKey })).toBeUndefined()
    expect(await pane.find({ type: 'Text', text: /^ {2}nightly-deep-dive \d\d-\d\d \d\d:\d\d · acked · unfinished/ })).toBeDefined()
    // The press reads again at once, and herd's own acked list keeps it acked from then on.
    expect(world.processes.filter(p => p.argv[1] === 'snapshot').length).toBe(2)
    await clock.advance(60_000)
    expect(world.statuses.length).toBe(2)
  })

  test('an Ack herd refuses says so under the pane, and the failure stays', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    nightOf(world)
    await $.session.start(SESSION)
    await clock.settle()
    world.ackDeny = 'herd: state dir not writable'
    await $.command.run({ ...RUN, args: 'runs' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })

    await pane.press({ key: 'ack-all' })

    expect(await pane.find({ type: 'Text', text: 'ack failed: herd: state dir not writable' })).toBeDefined()
    expect(world.statuses.length).toBe(1)
    expect(await pane.find({ key: 'ack-all' })).toBeDefined()
  })

  test('an Ack pressed while a refresh is reading stays acked when that refresh lands', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    nightOf(world)
    await $.session.start(SESSION)
    await clock.settle()
    await $.command.run({ ...RUN, args: 'runs' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })

    // A refresh starts and waits on herd; meanwhile the person acks, and herd
    // answers that refresh with what it read before the ack.
    let release: () => void = () => undefined

    world.gate = new Promise<void>(resolve => {
      release = resolve
    })
    await pane.press({ key: 'refresh' })
    await clock.settle()
    const before = world.snapshot

    await pane.press({ key: `ack-${FAILED_KEY}` })
    expect(world.statuses).toEqual([world.statuses[0], undefined])
    world.snapshot = before

    world.gate = null
    release()
    await clock.settle()

    // The read that landed shows the ack: no status line pinned again, no Ack button back.
    expect(world.statuses).toEqual([world.statuses[0], undefined])
    expect(await pane.find({ key: `ack-${FAILED_KEY}` })).toBeUndefined()
  })
})

describe('the /herd pane', () => {
  test('/herd opens a focused pane that Esc closes; /herd runs opens it on Runs', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    expect(await $.command.run(RUN)).toEqual({})
    expect(world.opened).toEqual([{ id: 'herd', focus: true, closeOnEscape: true }])

    await $.command.run({ ...RUN, args: 'runs' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: /No run sources: herd reads ~\/\.config\/herd\/sources\.json/ })).toBeDefined()
  })

  test('/herd says why when the pane waits', async ($, on) => {
    mock.clock(on, { now: NOW })
    const world = worldOf(on)

    world.isPlaced = false
    await $.session.start(SESSION)

    expect(await $.command.run(RUN)).toEqual({ text: 'The herd pane is waiting: unasked below 144 columns' })
  })

  test('three tabs, on the terminal and the desktop', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    nightOf(world)
    await $.session.start(SESSION)
    await clock.settle()

    for (const surface of ['terminal', 'desktop'] as const) {
      const pane = await $.ui.mount({ ...PANE, surface })

      await pane.press({ key: 'tab-tracks' })
      expect(await pane.find({ type: 'Text', text: 'demo-repo' })).toBeDefined()
      expect(await pane.find({ type: 'Text', text: '  parser-fix · active · waiting on you: permission prompt' })).toBeDefined()
      expect(await pane.find({ type: 'Text', text: /docs · review · #6 open passing \(checked 3h ago\) · idle · 3 behind origin\/main/ })).toBeDefined()

      await pane.press({ key: 'tab-sessions' })
      expect(await pane.find({ type: 'Text', text: /^5 sessions · 1 waiting on you · 2 background \(1 working\)/ })).toBeDefined()
      expect(await pane.find({ type: 'Text', text: /^demo-1 · interactive · waiting on you: permission prompt/ })).toBeDefined()

      await pane.press({ key: 'tab-runs' })
      expect(await pane.find({ type: 'Text', text: new RegExp(`nightly-report ${dayOf(NOW - 6 * HOUR)} \\d\\d:\\d\\d · ok`) })).toBeDefined()
      expect(await pane.find({ type: 'Text', text: /weekly \d\d-\d\d \d\d:\d\d · \[09:43\] === Weekly run done/ })).toBeDefined()
      expect(await pane.find({ type: 'Text', text: '    success · 20 turns · ≈$2.42 · 0 denied · subagents 0/1 done · 1 killed' })).toBeDefined()
      await pane.unmount()
    }
  })

  test('Ping goes only to a live session that is not this one, and says whether it arrived', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    world.snapshot = { ...SNAPSHOT, sessions: SESSIONS.map(s => (s.name === 'nightly report' ? { ...s, isLive: false } : s)) }
    await $.session.start(SESSION)
    await clock.settle()
    await $.command.run({ ...RUN, args: 'sessions' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })

    expect(await pane.find({ key: `ping-${SELF}` })).toBeUndefined()
    expect(await pane.find({ key: 'ping-bbbbbbbb-0000-4000-8000-000000000001' })).toBeUndefined()
    await pane.press({ key: 'ping-aaaaaaaa-0000-4000-8000-000000000002' })
    world.isDelivered = false
    await pane.press({ key: 'ping-aaaaaaaa-0000-4000-8000-000000000001' })

    expect(world.sent).toEqual([
      { to: 'aaaaaaaa-0000-4000-8000-000000000002', text: PING_TEXT },
      { to: 'aaaaaaaa-0000-4000-8000-000000000001', text: PING_TEXT },
    ])
    expect(world.toasts).toEqual(['ping delivered to demo-2', 'ping to demo-1 not delivered: recipient gone'])
  })

  test('Inbox and Accept run shepherd as the person, in the repo', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    world.processes = []
    await $.command.run({ ...RUN, args: 'tracks' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })

    await pane.press({ key: 'inbox' })
    expect(await pane.find({ type: 'Text', text: 'demo-repo a1b2c3 15m ago · handoff from docs: Review is open, please look' })).toBeDefined()
    await pane.press({ key: 'accept-demo-repo-a1b2c3' })

    const asThePerson = { cwd: '/work/demo-repo', env: { CLAUDE_CODE_SESSION_ID: '' } }

    expect(world.processes).toEqual([
      { argv: ['shepherd', 'inbox', '--json'], ...asThePerson },
      { argv: ['shepherd', 'accept', 'a1b2c3'], ...asThePerson },
      { argv: ['shepherd', 'inbox', '--json'], ...asThePerson },
    ])
    expect(await pane.find({ type: 'Text', text: 'Inbox: nothing addressed to you.' })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: 'accepted a1b2c3 (logged ffeedd)' })).toBeDefined()
  })

  test('Open board prints the dashboard URL; Refresh reads again; Close closes', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    await $.command.run(RUN)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })

    await pane.press({ key: 'board' })
    expect(await pane.find({ type: 'Text', text: 'shepherd dashboard: http://127.0.0.1:7799/?t=token' })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: 'remote machine? forward the port first' })).toBeDefined()

    world.processes = []
    await pane.press({ key: 'refresh' })
    await clock.settle()
    expect(world.processes.map(p => p.argv[1])).toEqual(['snapshot'])

    await pane.press({ key: 'close' })
    expect(world.closed).toEqual(['herd'])
  })

  test('/clear moves the session id: the new one is this session, offered no Ping', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    world.selfId = AFTER_CLEAR
    world.snapshot = { ...SNAPSHOT, sessions: SESSIONS.map(s => (s.sessionId === SELF ? { ...s, sessionId: AFTER_CLEAR } : s)) }
    await $.classic.SessionStart({ source: 'clear' })
    await clock.advance(60_000)
    await $.command.run({ ...RUN, args: 'sessions' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })

    expect(await pane.find({ key: `ping-${AFTER_CLEAR}` })).toBeUndefined()
    expect(await pane.find({ type: 'Text', text: '(this session)' })).toBeDefined()
    expect(world.sent).toEqual([])
  })

  test('/herd taken by a skill of the same name: /herd runs the skill, the band still opens the pane, and the pane says why', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    world.commandDeny = '"/herd" refused: it is the user\'s /herd'
    await $.session.start(SESSION)
    await clock.settle()

    // What the person typed reaches their own skill; the mod opens nothing.
    expect(await $.command.run({ ...RUN, args: 'runs' })).toEqual({ text: 'core ran /herd' })
    expect(world.opened).toEqual([])

    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    await band.press({ key: 'herd-open' })
    expect(world.opened).toEqual([{ id: 'herd', focus: true, closeOnEscape: true }])
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: /^\/herd not registered \(the band still opens this pane\): "\/herd" refused/ })).toBeDefined()
  })

  test('/clear keeps the band: the snapshot comes back from the store', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    await $.classic.SessionStart({ source: 'clear' })
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: '3 tracks: ' })).toBeDefined()
  })

  test('a shared read with a malformed row is not drawn: the pane keeps the last good one', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    const shared = world.store.get('shared') as { config: string; snapshot: Record<string, unknown> }

    // This schema's number, but a session row without a sessionId (a hand-edited or corrupted store).
    world.store.set('shared', { config: shared.config, snapshot: { ...shared.snapshot, at: NOW + 1, sessions: [{ kind: 'interactive', isLive: true }] } })
    await $.classic.SessionStart({ source: 'clear' })
    await $.command.run({ ...RUN, args: 'sessions' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })

    expect(await pane.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeUndefined()
    expect(await pane.find({ type: 'Text', text: /^demo-1 · interactive · waiting on you/ })).toBeDefined()

    // And the next refresh reads again rather than adopt it.
    world.processes = []
    await clock.advance(60_000)
    expect(world.processes.map(p => p.argv[1])).toEqual(['snapshot'])
  })

  test('the night fixture is a snapshot this build draws', () => {
    expect(NIGHT.schema).toBe(3)
  })
})
