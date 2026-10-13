import { describe, expect, mock, test } from 'claude-code/testing'

import { dayOf } from '../hooks/herd'
import { PING_TEXT } from '../hooks/register'
import { AFTER_CLEAR, AGENTS, ATTACH, BAND, HAND_BACK, HEADLESS, HOUR, LOGS, NOW, PANE, PEER_ENVELOPE, RUN, SELF, SESSION, SOURCES, nightOf, worldOf } from './fixtures'

const WITH_SOURCES = { options: { sources_file: SOURCES } }

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
    expect(world.processes.filter(p => p.argv[1] === 'status').length).toBe(1)
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
    expect(world.processes.filter(p => p.argv[1] !== 'status' && p.argv[1] !== 'agents')).toEqual([])
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
  test('reads right after start and then every 60 s, and stores what it read', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)
    const reads = () => world.processes.filter(p => p.argv[1] === 'status').length

    await $.session.start(SESSION)
    expect(reads()).toBe(0)
    await clock.settle()
    expect(reads()).toBe(1)
    expect(world.processes.map(p => p.argv.join(' '))).toEqual(['shepherd status --all --json', 'claude agents --json --all'])
    // git status under shepherd takes no optional index lock: a refresh never races the person's git add.
    expect(world.processes[0]?.env).toEqual({ GIT_OPTIONAL_LOCKS: '0' })
    expect(world.store.get('snapshot')).toMatchObject({ tracks: [{ name: 'parser-fix' }, { name: 'docs' }, { name: 'old-idea' }] })

    await clock.advance(60_000)
    expect(reads()).toBe(2)
    await clock.advance(120_000)
    expect(reads()).toBe(4)
  })

  test('a snapshot another session stored a moment ago is shown, not read again', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    // One session reads and stores; a second session's start finds it.
    await $.session.start(SESSION)
    await clock.settle()
    const stored = world.store.get('snapshot')

    world.processes = []
    world.store.set('snapshot', { ...(stored as object), at: NOW + 50_000 })
    await clock.advance(60_000)

    expect(world.processes).toEqual([])
  })

  test('the configured binaries are run, and a snapshot read with other options is read again', { options: { shepherd_bin: '~/bin/shepherd', claude_bin: '/opt/claude' } }, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    world.store.set('snapshot', { at: NOW, tookMs: 1, config: 'other', tracksOn: true, repos: [], tracks: [], tracksError: null, tracksMissing: false, sessionsOn: true, sessions: [], sessionsError: null, sessionsMissing: false, runs: [], sources: [], sourcesError: null })
    await $.session.start(SESSION)
    await clock.settle()

    expect(world.processes.map(p => p.argv[0])).toEqual(['/home/user/bin/shepherd', '/opt/claude'])
  })

  test('tracks and sessions off: neither command runs', { options: { tracks: false, sessions: false } }, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()

    expect(world.processes).toEqual([])
  })

  test('shepherd not installed: the band leaves tracks out; a shepherd that fails says tracks ?', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    world.status = 'missing'
    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: /tracks/ })).toBeUndefined()
    expect(await band.find({ type: 'Text', text: '1 waiting on you' })).toBeDefined()

    // At a long path the reason sits past any cut: still not installed.
    world.status = 'missing-long'
    await clock.advance(60_000)
    expect(await band.find({ type: 'Text', text: /tracks/ })).toBeUndefined()

    world.status = 'broken'
    await clock.advance(60_000)
    expect(await band.find({ type: 'Text', text: 'tracks ?' })).toBeDefined()

    // A shepherd that runs and fails saying a file is missing is failing, not missing.
    world.status = 'missing'
    await clock.advance(60_000)
    world.status = 'broken-fnf'
    await clock.advance(60_000)
    expect(await band.find({ type: 'Text', text: 'tracks ?' })).toBeDefined()
  })

  test('a store that refuses the write: the fresh read still shows, and the pane says it was not shared', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: '1 waiting on you' })).toBeDefined()
    world.storeDeny = 'store full'
    world.agents = []
    await clock.advance(60_000)

    expect(await band.find({ type: 'Text', text: '1 waiting on you' })).toBeUndefined()
    await $.command.run(RUN)
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: 'not shared with other sessions: store full' })).toBeDefined()

    world.storeDeny = null
    await clock.advance(60_000)
    expect(await pane.find({ type: 'Text', text: /not shared/ })).toBeUndefined()
  })

  test('a snapshot another build stored, of another shape, is read again and never drawn', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    // The right options, but an older build's shape: schema 1, no repos.
    const older: Record<string, unknown> = { ...(world.store.get('snapshot') as Record<string, unknown>), schema: 1, at: NOW + 30_000 }

    delete older.repos
    world.store.set('snapshot', older)
    world.processes = []
    await clock.advance(60_000)

    expect(world.processes.map(p => p.argv[1])).toEqual(['status', 'agents'])
    await $.command.run({ ...RUN, args: 'tracks' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    expect(await pane.find({ type: 'Text', text: 'demo-repo' })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeUndefined()
  })
})

describe('the band above the prompt', () => {
  test('one line: tracks, who waits, each run source, colored by what needs the person', WITH_SOURCES, async ($, on) => {
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

  test('a press opens the pane on what needs the person: failed runs first', WITH_SOURCES, async ($, on) => {
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
  test('the oldest unacked failure is the status line; Ack clears it for every session', WITH_SOURCES, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    nightOf(world)
    await $.session.start(SESSION)
    await clock.settle()

    expect(world.statuses).toEqual([`Nightly nightly-deep-dive ${dayOf(NOW - 5 * HOUR)}: unfinished: 1 subagent killed`])

    await $.command.run({ ...RUN, args: 'runs' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })
    const ackKey = `ack-${LOGS}/nightly-deep-dive-2026-10-09.jsonl@${NOW - 5 * HOUR}`

    expect(await pane.find({ key: ackKey })).toBeDefined()
    await pane.press({ key: ackKey })

    expect(world.statuses).toEqual([world.statuses[0], undefined])
    expect(world.store.get('acked')).toEqual([`${LOGS}/nightly-deep-dive-2026-10-09.jsonl@${NOW - 5 * HOUR}`])
    expect(await pane.find({ key: ackKey })).toBeUndefined()
    expect(await pane.find({ type: 'Text', text: /^ {2}nightly-deep-dive \d\d-\d\d \d\d:\d\d · acked · unfinished/ })).toBeDefined()

    // The next refresh keeps it acked: no second status line.
    await clock.advance(60_000)
    expect(world.statuses.length).toBe(2)
  })

  test('an Ack the store refuses says so under the pane, and the failure stays', WITH_SOURCES, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    nightOf(world)
    await $.session.start(SESSION)
    await clock.settle()
    world.storeDeny = 'store full'
    await $.command.run({ ...RUN, args: 'runs' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })

    await pane.press({ key: 'ack-all' })

    expect(await pane.find({ type: 'Text', text: 'ack failed: store full' })).toBeDefined()
    expect(world.statuses.length).toBe(1)
    expect(await pane.find({ key: 'ack-all' })).toBeDefined()
  })

  test('an Ack pressed while a refresh is reading stays acked when that refresh lands', WITH_SOURCES, async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)
    const key = `${LOGS}/nightly-deep-dive-2026-10-09.jsonl@${NOW - 5 * HOUR}`

    nightOf(world)
    await $.session.start(SESSION)
    await clock.settle()
    await $.command.run({ ...RUN, args: 'runs' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })

    // Refresh starts and waits on claude agents; the person acks meanwhile.
    let release: () => void = () => undefined

    world.gate = new Promise<void>(resolve => {
      release = resolve
    })
    await pane.press({ key: 'refresh' })
    await clock.settle()
    await pane.press({ key: `ack-${key}` })
    expect(world.statuses).toEqual([world.statuses[0], undefined])

    world.gate = null
    release()
    await clock.settle()

    // The read that landed shows the ack: no status line pinned again, no Ack button back.
    expect(world.processes.filter(p => p.argv[1] === 'agents').length).toBe(2)
    expect(world.statuses).toEqual([world.statuses[0], undefined])
    expect(await pane.find({ key: `ack-${key}` })).toBeUndefined()
    expect(world.store.get('acked')).toEqual([key])
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
    expect(await pane.find({ type: 'Text', text: /No run sources/ })).toBeDefined()
  })

  test('/herd says why when the pane waits', async ($, on) => {
    mock.clock(on, { now: NOW })
    const world = worldOf(on)

    world.isPlaced = false
    await $.session.start(SESSION)

    expect(await $.command.run(RUN)).toEqual({ text: 'The herd pane is waiting: unasked below 144 columns' })
  })

  test('three tabs, on the terminal and the desktop', WITH_SOURCES, async ($, on) => {
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
      await pane.unmount()
    }
  })

  test('Ping goes only to a live session that is not this one, and says whether it arrived', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

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
    expect(world.processes.map(p => p.argv[1])).toEqual(['status', 'agents'])

    await pane.press({ key: 'close' })
    expect(world.closed).toEqual(['herd'])
  })

  test('/clear moves the session id: the new one is this session, offered no Ping', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    world.selfId = AFTER_CLEAR
    world.agents = AGENTS.map(a => (a.sessionId === SELF ? { ...a, sessionId: AFTER_CLEAR } : a))
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

  test('/clear keeps the band: the snapshot and acks come back from the store', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    await $.classic.SessionStart({ source: 'clear' })
    const band = await $.ui.mount({ ...BAND, surface: 'terminal' })

    expect(await band.find({ type: 'Text', text: '3 tracks: ' })).toBeDefined()
  })

  test('a stored snapshot with a malformed row is not drawn: the pane keeps the last good one', async ($, on) => {
    const clock = mock.clock(on, { now: NOW })
    const world = worldOf(on)

    await $.session.start(SESSION)
    await clock.settle()
    const stored = world.store.get('snapshot') as Record<string, unknown>

    // This schema's number, but a session row without a sessionId (a hand-edited or corrupted store).
    world.store.set('snapshot', { ...stored, at: NOW + 1, sessions: [{ kind: 'interactive', isLive: true }] })
    await $.classic.SessionStart({ source: 'clear' })
    await $.command.run({ ...RUN, args: 'sessions' })
    const pane = await $.ui.mount({ ...PANE, surface: 'terminal' })

    expect(await pane.find({ type: 'Text', text: 'drawn by Claude Code' })).toBeUndefined()
    expect(await pane.find({ type: 'Text', text: /^demo-1 · interactive · waiting on you/ })).toBeDefined()

    // And the next refresh reads again rather than adopt it.
    world.processes = []
    await clock.advance(60_000)
    expect(world.processes.map(p => p.argv[1])).toEqual(['status', 'agents'])
  })
})
