// fleet: one line above the prompt and a /fleet pane that say, from any
// session, which shepherd tracks are blocked, in review or stale, which
// sessions wait on the person, and whether unattended `claude -p` runs really
// finished. It follows mods/CLAUDE.md: inert where nothing draws (claude -p,
// cron, the SDK), observe-only (every engine event but its own registered
// /fleet is passed on unchanged),
// no tool.call or tool.check hook, no model call, and every process it starts
// is either a read on its clock or a press of the person's.
import { atom, read, update } from 'claude-code'
import type {
  EngineInterface,
  FleetFaults,
  FleetInboxEntry,
  FleetRun,
  FleetSession,
  FleetSnapshot,
  FleetTab,
  On,
  PluginOptions,
  SessionSendResult,
  Timer,
} from 'claude-code'

import { ackedOf, bandOf, configKeyOf, configOf, fitted, peerToastOf, plural, snapshotOf, statusOf, tabOf, toneOf, urgentTab, withHealth } from './fleet'
import type { Config } from './fleet'
import { acceptEntry, boardUrl, errorText, readInbox, readSnapshot } from './read'
import type { FactsCache, Reader } from './read'
import { paneOf, styleOf } from './view'

const PANE = 'fleet'

const NO_FAULTS: FleetFaults = { refresh: null, share: null, command: null }

// `plugin` and `key` stay string literals and each atom sits in a const:
// `claude plugin validate` reads them off the source and holds them to
// types/index.d.ts.
const snapshotAtom = atom({ plugin: 'fleet', key: 'snapshot' } as const, null)
const ackedAtom = atom({ plugin: 'fleet', key: 'acked' } as const, [])
const tabAtom = atom({ plugin: 'fleet', key: 'tab' } as const, 'tracks')
const inboxAtom = atom({ plugin: 'fleet', key: 'inbox' } as const, null)
const noticeAtom = atom({ plugin: 'fleet', key: 'notice' } as const, null)
const faultsAtom = atom({ plugin: 'fleet', key: 'faults' } as const, NO_FAULTS)

/**
 * What Ping sends: a question for the other session's model, nothing it must
 * act on. It starts or joins a turn there; the answer stays in that session's
 * own transcript.
 */
export const PING_TEXT =
  'Status check from the person, sent with the fleet pane: say in one line, in your own reply here, what you are doing and what you are waiting for. Do not message the sender back.'

/** A refresh another session stored this recently is shown as it is, not read again. */
const FRESH_SHARE = 0.75

/** The band is a plain Button: `f: ` before its pieces, and the engine's hint beside it when focused. */
const BAND_RESERVED_CELLS = 3 + 2

/**
 * The engine as `session.start` (or `session.attach`) bound it: the timers and
 * the presses reach the engine through this, so read.ts and the refresh below
 * are plain functions over it.
 */
type Host = Reader & {
  storeGet: (key: string) => Promise<unknown>
  storeSet: (key: string, value: unknown) => Promise<void>
  status: (text: string | undefined) => void
  toast: (text: string) => void
  log: (text: string) => void
  send: (sessionId: string, text: string) => Promise<SessionSendResult>
  setSnapshot: (snapshot: FleetSnapshot | null) => Promise<unknown>
  setAcked: (acked: string[]) => Promise<unknown>
  setNotice: (notice: string | null) => Promise<unknown>
  setInbox: (inbox: FleetInboxEntry[] | null) => Promise<unknown>
  setFaults: (faults: FleetFaults) => Promise<unknown>
}

/** The module's own state: what a reload may lose. */
type Live = {
  cfg: Config
  key: string
  isLive: boolean
  /** `/fleet` is this mod's: registered in this session. Until then /fleet is passed on to whatever owns it. */
  isRegistered: boolean
  host: Host | null
  home: string | null
  selfId: string | null
  timers: Timer[]
  isReading: boolean
  isQueued: boolean
  /** The snapshot last shown, and the acks it was shown with. */
  last: FleetSnapshot | null
  shownAt: number | null
  acked: string[]
  shownAcked: string | null
  /** The status line last set: set again only when it changes. */
  status: string | undefined
  /** The faults last put in $.state: written again only when they change. */
  faults: FleetFaults
  cache: FactsCache
}

/**
 * Binds a Host from `$`. Declared at the top level of this file, each member
 * spelled `$.noun.method(...)`, so the engine reads what the module calls off
 * its source.
 */
function hostOf($: EngineInterface): Host {
  return {
    now: () => $.clock.now(),
    run: (argv, init) => $.process.run(argv, init),
    list: path => $.fs.list(path),
    read: path => $.fs.read(path),
    storeGet: key => $.store.get(key),
    storeSet: (key, value) => $.store.set(key, value),
    status: text => $.ui.status(text),
    toast: text => $.ui.toast(text),
    log: text => $.ui.log(text, { to: 'debug' }),
    send: (sessionId, text) => $.session.send({ to: { sessionId }, text }),
    setSnapshot: snapshot => update($, snapshotAtom, () => snapshot),
    setAcked: acked => update($, ackedAtom, () => acked),
    setNotice: notice => update($, noticeAtom, () => notice),
    setInbox: inbox => update($, inboxAtom, () => inbox),
    setFaults: faults => update($, faultsAtom, () => faults),
  }
}

/** Puts a snapshot and the acks on screen: the state the band and pane read, and the status line. */
async function show(live: Live, host: Host, snapshot: FleetSnapshot | null, acked: string[]): Promise<void> {
  const ackedKey = JSON.stringify(acked)

  live.last = snapshot
  live.acked = acked
  if (snapshot !== null && live.shownAt !== snapshot.at) {
    await host.setSnapshot(snapshot)
    live.shownAt = snapshot.at
  }
  if (live.shownAcked !== ackedKey) {
    await host.setAcked(acked)
    live.shownAcked = ackedKey
  }

  const status = statusOf(snapshot, new Set(acked))

  if (status !== live.status) {
    host.status(status)
    live.status = status
  }
}

/** Records the mod's own failures where the pane and the band show them; a write only when they change. */
async function setFaults(live: Live, host: Host, change: Partial<FleetFaults>): Promise<void> {
  const next = { ...live.faults, ...change }

  if (JSON.stringify(next) !== JSON.stringify(live.faults)) {
    live.faults = next
    await host.setFaults(next)
  }
}

/** The acks and the snapshot every session shares. An unreadable store reads as nothing shared, and says why. */
async function sharedOf(live: Live, host: Host): Promise<{ acked: string[]; stored: FleetSnapshot | null; error: string | null }> {
  try {
    return { acked: ackedOf(await host.storeGet('acked')), stored: snapshotOf(await host.storeGet('snapshot')), error: null }
  } catch (error) {
    return { acked: live.acked, stored: null, error: errorText(error) }
  }
}

/**
 * The acks to show with a read that just landed: the store's now (an Ack
 * pressed while the read ran is there), and this session's own, whose write may
 * still be landing. Acks are only ever added, so the union is never stale.
 */
async function acksNow(live: Live, host: Host): Promise<string[]> {
  const stored = await host.storeGet('acked').then(ackedOf, () => [] as string[])

  return ackedOf([...stored, ...live.acked.filter(k => !stored.includes(k))])
}

/**
 * One refresh: adopt what another session stored a moment ago, else read
 * everything again, show it, and store it for them. `force` reads whatever is
 * stored. A store that refuses the write never hides a fresh read.
 */
async function tick(live: Live, force: boolean): Promise<void> {
  const host = live.host

  if (host === null) {
    return
  }
  if (live.isReading) {
    live.isQueued = live.isQueued || force

    return
  }

  live.isReading = true

  try {
    const now = await host.now()
    const shared = await sharedOf(live, host)
    const stored = shared.stored
    const isFresh = stored !== null && stored.config === live.key && now - stored.at < live.cfg.periodMs * FRESH_SHARE
    let shareError = shared.error

    if (!force && isFresh) {
      await show(live, host, stored, shared.acked)
    } else {
      const snapshot = await readSnapshot(host, live.cfg, live.home, live.cache)

      host.log(`fleet: read in ${snapshot.tookMs} ms (${snapshot.tracks.length} tracks, ${snapshot.sessions.length} sessions, ${snapshot.runs.length} runs)`)
      // Not shared.acked: that was read before the read began, and would undo an Ack pressed since.
      await show(live, host, snapshot, await acksNow(live, host))

      if (shareError === null) {
        shareError = await host.storeSet('snapshot', snapshot).then(
          () => null,
          (error: unknown) => errorText(error),
        )
      }
    }
    if (shareError !== null) {
      host.log(`fleet: snapshot not shared: ${shareError}`)
    }
    await setFaults(live, host, { refresh: null, share: shareError })
  } catch (error) {
    // Fail open: the band keeps the last snapshot, marked `fleet ?`, and the pane says why.
    host.log(`fleet: refresh failed: ${String(error)}`)
    await setFaults(live, host, { refresh: errorText(error) }).catch(() => undefined)
  } finally {
    live.isReading = false

    if (live.isQueued) {
      live.isQueued = false
      void tick(live, true)
    }
  }
}

/** Shows the snapshot another session (or this one before a reload) stored, when it was read with these options. */
async function adoptStored(live: Live, host: Host): Promise<void> {
  const stored = snapshotOf(await host.storeGet('snapshot'))

  await show(live, host, stored !== null && stored.config === live.key ? stored : null, ackedOf(await host.storeGet('acked')))
}

/** Adds failures to the acked keys every session shares; re-read right before the write. Says what it acked. */
async function ack(live: Live, host: Host, runs: readonly FleetRun[]): Promise<string> {
  const acked = ackedOf(await host.storeGet('acked'))
  const next = ackedOf([...acked, ...runs.map(r => r.key).filter(k => !acked.includes(k))])

  await host.storeSet('acked', next)
  await show(live, host, live.last, next)

  const [only] = runs

  return runs.length === 1 && only !== undefined ? `acked ${only.source} ${only.name}` : `acked ${plural(runs.length, 'failure')}`
}

/** Runs one press's work and puts what it said under the pane, a failure included; never rejects. */
async function noted(host: Host, what: string, work: () => Promise<string>): Promise<void> {
  let said: string

  try {
    said = await work()
  } catch (error) {
    said = `${what} failed: ${errorText(error)}`
    host.log(`fleet: ${said}`)
  }
  await host.setNotice(said).catch(() => undefined)
}

/**
 * What the mod does once the session has somewhere to draw. `$` may be passed
 * to a function at the top level of this file, and nowhere else.
 */
async function goLive($: EngineInterface, live: Live): Promise<void> {
  live.isLive = true
  live.host = hostOf($)
  live.home = (await $.env.get('HOME').catch(() => undefined)) ?? null
  live.selfId = await $.session.id().catch(() => null)

  // The last snapshot shows at once; the band may have drawn already, while
  // the mod was inert, and a module variable redraws nothing by itself.
  try {
    await adoptStored(live, live.host)
  } catch (error) {
    $.ui.log(`fleet: stored snapshot not read: ${String(error)}`, { to: 'debug' })
  }
  $.ui.invalidate('ui.render')

  for (const timer of live.timers) {
    timer.cancel()
  }
  live.timers = [$.clock.after(0, () => void tick(live, false)), $.clock.every(live.cfg.periodMs, () => void tick(live, false))]

  // Last, and caught: register throws for a taken name (a skill or command the
  // person has called fleet), and a hook that throws is skipped whole. /fleet
  // then stays theirs (command.run passes it on); the band still opens the
  // pane, and the pane's footer says why /fleet is missing.
  try {
    await $.command.register({ name: 'fleet', description: 'Open the fleet pane: shepherd tracks, sessions and headless runs', immediate: true })
    live.isRegistered = true
    await setFaults(live, live.host, { command: null })
  } catch (error) {
    $.ui.log(`fleet: /fleet not registered: ${String(error)}`, { to: 'debug' })
    await setFaults(live, live.host, { command: errorText(error) }).catch(() => undefined)
  }
}

export function register(on: On, options: PluginOptions) {
  const cfg = configOf(options)
  // isLive is false until the session has a surface; until then every hook
  // only passes its event on. Module state is enough: a reload runs
  // session.start again.
  const live: Live = {
    cfg,
    key: configKeyOf(cfg),
    isLive: false,
    isRegistered: false,
    host: null,
    home: null,
    selfId: null,
    timers: [],
    isReading: false,
    isQueued: false,
    last: null,
    shownAt: null,
    acked: [],
    shownAcked: null,
    status: undefined,
    faults: NO_FAULTS,
    cache: new Map(),
  }

  // A terminal session, a background one (`claude --bg`, agent view: it starts
  // on a terminal surface with nobody looking) and a desktop one go live here.
  on('session.start', async ($, e, next) => {
    if ((await $.session.surfaces()).length === 0) {
      return next(e)
    }

    await goLive($, live)

    return next(e)
  })

  // A session that started with nothing to draw on goes live when a client
  // attaches. The terminal never raises it.
  on('session.attach', async ($, e, next) => {
    if (!live.isLive) {
      await goLive($, live)
    }

    return next(e)
  })

  // /clear, /resume and /branch reset $.state and go on under a new session id,
  // with no session.start: read the id again (so Ping never offers this session
  // to itself) and copy the shared snapshot and acks back from $.store. Reads
  // only, so it may run with nothing attached too.
  on('classic.SessionStart', { source: ['clear', 'resume', 'fork'] }, async ($, e, next) => {
    const result = await next(e)

    if (live.isLive) {
      try {
        live.selfId = await $.session.id().catch(() => live.selfId)

        const stored = snapshotOf(await $.store.get('snapshot'))
        const acked = ackedOf(await $.store.get('acked'))

        live.last = stored !== null && stored.config === live.key ? stored : live.last
        live.shownAt = live.last?.at ?? null
        live.acked = acked
        live.shownAcked = JSON.stringify(acked)
        // The next refresh pins the status line again if it still has something to say.
        live.status = undefined
        await update($, snapshotAtom, () => live.last)
        await update($, ackedAtom, () => acked)
        await update($, faultsAtom, () => live.faults)
      } catch (error) {
        $.ui.log(`fleet: state not restored after ${e.source}: ${String(error)}`, { to: 'debug' })
      }
      $.ui.invalidate('ui.render')
    }

    return result
  }).catch(($, e, next) => next(e))

  // A message from another session: a toast with its sender and first line,
  // never more. A teammate's mailbox delivery, a message for one of this
  // session's agents and a subagent's hand-back are this session's own work,
  // not news. The text is the sender's; nothing here acts on it.
  on('session.receive', async ($, e, next) => {
    const result = await next(e)
    const origin = e.origin

    if (live.isLive && e.agentId === undefined && (origin.kind === 'peer' || origin.kind === 'peer-send-message') && !('teammate' in origin)) {
      const toast = peerToastOf(origin.kind, e.text)

      if (toast !== null) {
        $.ui.toast(toast)
      }
    }

    return result
  }).catch(($, e, next) => next(e))

  // Answers /fleet only when it is this mod's, registered in a live session. A
  // skill or command of the person's called fleet (register refused the name),
  // and `claude -p "/fleet"` where nothing draws, get the event passed on: an
  // answer without next would run no command at all. A gate, so caught.
  on('command.run', { command: 'fleet' }, async ($, e, next) => {
    if (!live.isLive || !live.isRegistered) {
      return next(e)
    }

    const tab = tabOf(e.args)

    if (tab !== null) {
      await update($, tabAtom, () => tab)
    }

    const opened = await $.ui.open({ id: PANE, title: 'Fleet', focus: true, closeOnEscape: true })

    return opened.isPlaced ? {} : { text: `The fleet pane is waiting: ${opened.reason}` }
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'Pane', requestId: 'fleet' }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const snapshot = await read($, snapshotAtom)
    const acked = new Set(await read($, ackedAtom))
    const host = live.host
    // Every press runs through noted: what it said, or why it failed, goes under the pane.
    const press = (what: string, work: (h: Host) => Promise<string>): void => {
      if (host !== null) {
        void noted(host, what, () => work(host))
      }
    }

    return paneOf(
      { Box, Text, Button },
      {
        snapshot,
        acked,
        tab: await read($, tabAtom),
        inbox: await read($, inboxAtom),
        notice: await read($, noticeAtom),
        faults: await read($, faultsAtom),
        nowMs: await $.clock.now(),
        selfId: live.selfId,
      },
      {
        tab: tab => void update($, tabAtom, () => tab).catch(() => undefined),
        refresh: () => void tick(live, true),
        close: () => void $.ui.close({ id: PANE }).catch(() => undefined),
        ack: run => press('ack', h => ack(live, h, [run])),
        ackAll: () => press('ack', h => ack(live, h, (snapshot?.runs ?? []).filter(r => toneOf(r, acked) === 'fail'))),
        board: () => press('board', h => boardUrl(h, live.cfg, live.home)),
        inbox: () =>
          press('inbox', async h => {
            const got = await readInbox(h, live.cfg, live.home, snapshot?.repos ?? [])

            await h.setInbox(got.entries)

            return got.errors.length === 0 ? `inbox: ${got.entries.length} for you` : `inbox: ${got.errors.join('; ')}`
          }),
        accept: (entry: FleetInboxEntry) =>
          press('accept', async h => {
            const said = await acceptEntry(h, live.cfg, live.home, entry)
            const got = await readInbox(h, live.cfg, live.home, snapshot?.repos ?? [])

            await h.setInbox(got.entries)

            return said
          }),
        ping: (session: FleetSession) => {
          // Checked again at the press: the id moves on /clear, /resume and /branch.
          if (!session.isLive || session.sessionId === live.selfId) {
            return
          }
          press('ping', async h => {
            const sent = await h.send(session.sessionId, PING_TEXT)
            const who = session.name ?? session.sessionId.slice(0, 8)

            h.toast(sent.isDelivered ? `ping delivered to ${who}` : `ping to ${who} not delivered: ${sent.reason}`)

            return sent.isDelivered ? `pinged ${who}: its answer stays in its own transcript` : `ping to ${who}: ${sent.reason}`
          })
        },
      },
    )
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (!live.isLive || !live.cfg.isBandOn || e.props.hasSurvey) {
      return next(e)
    }

    const snapshot = await read($, snapshotAtom)
    const acked = new Set(await read($, ackedAtom))
    const faults = await read($, faultsAtom)
    const band = withHealth(bandOf(snapshot, acked), snapshot, faults.refresh, await $.clock.now(), live.cfg.periodMs)
    const segments = fitted(band, Math.max(20, e.props.bodyColumns - BAND_RESERVED_CELLS))

    if (segments.length === 0) {
      return next(e)
    }

    const { Box, Text, Button } = $.ui.resolve(e)
    const fallback: FleetTab = await read($, tabAtom)
    // What the mods after this one draw stays, under this line.
    const below = await next(e)

    return Box({
      flexDirection: 'column',
      children: [
        Button({
          key: 'fleet-open',
          label: 'fleet',
          hotkey: 'f',
          plain: true,
          onPress: async () => {
            try {
              await update($, tabAtom, () => urgentTab(snapshot, acked, fallback))
              await $.ui.open({ id: PANE, title: 'Fleet', focus: true, closeOnEscape: true })
            } catch (error) {
              $.ui.log(`fleet: pane not opened: ${String(error)}`, { to: 'debug' })
            }
          },
          children: segments.map(s => Text({ ...styleOf(s.tone), wrap: 'truncate-end', children: [s.text] })),
        }),
        below,
      ],
    })
  })
}
