// herd: one line above the prompt and a /herd pane that say, from any session,
// which shepherd tracks are blocked, in review or stale, which sessions wait on
// the person, and whether unattended `claude -p` runs really finished. It draws
// what `herd snapshot --json` prints; herd does the reading and the judging.
// It follows mods/CLAUDE.md: inert where nothing draws (claude -p, cron, the
// SDK), observe-only (every engine event but its own registered /herd is passed
// on unchanged), no tool.call or tool.check hook, no model call, and every
// process it starts is either a read on its clock or a press of the person's.
import { atom, read, update } from 'claude-code'
import type {
  EngineInterface,
  HerdFaults,
  HerdInboxEntry,
  HerdRun,
  HerdSession,
  HerdSnapshot,
  HerdTab,
  On,
  PluginOptions,
  SessionSendResult,
  Timer,
} from 'claude-code'

import { ackedOf, bandOf, configKeyOf, configOf, fitted, peerToastOf, plural, sharedOf, statusOf, tabOf, toneOf, urgentTab, withHealth } from './herd'
import type { Config } from './herd'
import { acceptEntry, ackRuns, boardUrl, errorText, readInbox, readSnapshot } from './read'
import type { Reader } from './read'
import { paneOf, styleOf } from './view'

const PANE = 'herd'

const NO_FAULTS: HerdFaults = { refresh: null, share: null, command: null }

// `plugin` and `key` stay string literals and each atom sits in a const:
// `claude plugin validate` reads them off the source and holds them to
// types/index.d.ts.
const snapshotAtom = atom({ plugin: 'herd', key: 'snapshot' } as const, null)
const ackedAtom = atom({ plugin: 'herd', key: 'acked' } as const, [])
const tabAtom = atom({ plugin: 'herd', key: 'tab' } as const, 'tracks')
const inboxAtom = atom({ plugin: 'herd', key: 'inbox' } as const, null)
const noticeAtom = atom({ plugin: 'herd', key: 'notice' } as const, null)
const faultsAtom = atom({ plugin: 'herd', key: 'faults' } as const, NO_FAULTS)

/**
 * What Ping sends: a question for the other session's model, nothing it must
 * act on. It starts or joins a turn there; the answer stays in that session's
 * own transcript.
 */
export const PING_TEXT =
  'Status check from the person, sent with the herd pane: say in one line, in your own reply here, what you are doing and what you are waiting for. Do not message the sender back.'

/** A refresh another session stored this recently is shown as it is, not read again. */
const FRESH_SHARE = 0.75

/** The band is a plain Button: `h: ` before its pieces, and the engine's hint beside it when focused. */
const BAND_RESERVED_CELLS = 3 + 2

/** The $.store key every session shares its last read under: `{ config, snapshot }`. */
const SHARED = 'shared'

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
  setSnapshot: (snapshot: HerdSnapshot | null) => Promise<unknown>
  setAcked: (acked: string[]) => Promise<unknown>
  setNotice: (notice: string | null) => Promise<unknown>
  setInbox: (inbox: HerdInboxEntry[] | null) => Promise<unknown>
  setFaults: (faults: HerdFaults) => Promise<unknown>
}

/** The module's own state: what a reload may lose. */
type Live = {
  cfg: Config
  key: string
  isLive: boolean
  /** `/herd` is this mod's: registered in this session. Until then /herd is passed on to whatever owns it. */
  isRegistered: boolean
  host: Host | null
  home: string | null
  selfId: string | null
  timers: Timer[]
  isReading: boolean
  isQueued: boolean
  /** The snapshot last shown, and the acks it was shown with. */
  last: HerdSnapshot | null
  shownAt: number | null
  shownAcked: string | null
  /** Keys this session acked with `herd ack`: shown as acked even by a read that began before the press. */
  ackedHere: string[]
  /** The status line last set: set again only when it changes. */
  status: string | undefined
  /** The faults last put in $.state: written again only when they change. */
  faults: HerdFaults
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

/** Puts a snapshot and its acks on screen: the state the band and pane read, and the status line. */
async function show(live: Live, host: Host, snapshot: HerdSnapshot | null): Promise<void> {
  const acked = ackedOf(snapshot, live.ackedHere)
  const ackedKey = JSON.stringify(acked)

  live.last = snapshot
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
async function setFaults(live: Live, host: Host, change: Partial<HerdFaults>): Promise<void> {
  const next = { ...live.faults, ...change }

  if (JSON.stringify(next) !== JSON.stringify(live.faults)) {
    live.faults = next
    await host.setFaults(next)
  }
}

/**
 * One refresh: adopt what another session stored a moment ago, else run herd
 * again, show it, and store it for them. `force` reads whatever is stored. A
 * store that refuses the write never hides a fresh read.
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
    let shareError: string | null = null
    let stored: HerdSnapshot | null = null

    try {
      stored = sharedOf(await host.storeGet(SHARED), live.key)
    } catch (error) {
      shareError = errorText(error)
    }

    if (!force && stored !== null && now - stored.at < live.cfg.periodMs * FRESH_SHARE) {
      await show(live, host, stored)
    } else {
      const snapshot = await readSnapshot(host, live.cfg, live.home)

      host.log(`herd: read in ${snapshot.tookMs} ms (${snapshot.tracks.length} tracks, ${snapshot.sessions.length} sessions, ${snapshot.runs.length} runs)`)
      await show(live, host, snapshot)

      if (shareError === null) {
        shareError = await host.storeSet(SHARED, { config: live.key, snapshot }).then(
          () => null,
          (error: unknown) => errorText(error),
        )
      }
    }
    if (shareError !== null) {
      host.log(`herd: snapshot not shared: ${shareError}`)
    }
    await setFaults(live, host, { refresh: null, share: shareError })
  } catch (error) {
    // Fail open: the band keeps the last snapshot, marked `herd ?`, and the pane says why.
    host.log(`herd: refresh failed: ${String(error)}`)
    await setFaults(live, host, { refresh: errorText(error) }).catch(() => undefined)
  } finally {
    live.isReading = false

    if (live.isQueued) {
      live.isQueued = false
      void tick(live, true)
    }
  }
}

/** Shows the read another session (or this one before a reload) stored, when it was read with these options. */
async function adoptStored(live: Live, host: Host): Promise<void> {
  await show(live, host, sharedOf(await host.storeGet(SHARED), live.key))
}

/** Marks failures as seen with `herd ack`, for every session and the board, then reads again. Says what it acked. */
async function ack(live: Live, host: Host, runs: readonly HerdRun[]): Promise<string> {
  const keys = runs.map(r => r.key)

  await ackRuns(host, live.cfg, live.home, keys)
  live.ackedHere = [...new Set([...live.ackedHere, ...keys])].slice(-500)
  await show(live, host, live.last)
  void tick(live, true)

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
    host.log(`herd: ${said}`)
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
    $.ui.log(`herd: stored snapshot not read: ${String(error)}`, { to: 'debug' })
  }
  $.ui.invalidate('ui.render')

  for (const timer of live.timers) {
    timer.cancel()
  }
  live.timers = [$.clock.after(0, () => void tick(live, false)), $.clock.every(live.cfg.periodMs, () => void tick(live, false))]

  // Last, and caught: register throws for a taken name (a skill or command the
  // person has called herd), and a hook that throws is skipped whole. /herd
  // then stays theirs (command.run passes it on); the band still opens the
  // pane, and the pane's footer says why /herd is missing.
  try {
    await $.command.register({ name: 'herd', description: 'Open the herd pane: shepherd tracks, sessions and headless runs', immediate: true })
    live.isRegistered = true
    await setFaults(live, live.host, { command: null })
  } catch (error) {
    $.ui.log(`herd: /herd not registered: ${String(error)}`, { to: 'debug' })
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
    shownAcked: null,
    ackedHere: [],
    status: undefined,
    faults: NO_FAULTS,
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
  // to itself) and copy the shared snapshot back from $.store. Reads only, so
  // it may run with nothing attached too.
  on('classic.SessionStart', { source: ['clear', 'resume', 'fork'] }, async ($, e, next) => {
    const result = await next(e)

    if (live.isLive) {
      try {
        live.selfId = await $.session.id().catch(() => live.selfId)
        live.last = sharedOf(await $.store.get(SHARED), live.key) ?? live.last
        live.shownAt = live.last?.at ?? null

        const acked = ackedOf(live.last, live.ackedHere)

        live.shownAcked = JSON.stringify(acked)
        // The next refresh pins the status line again if it still has something to say.
        live.status = undefined
        await update($, snapshotAtom, () => live.last)
        await update($, ackedAtom, () => acked)
        await update($, faultsAtom, () => live.faults)
      } catch (error) {
        $.ui.log(`herd: state not restored after ${e.source}: ${String(error)}`, { to: 'debug' })
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

  // Answers /herd only when it is this mod's, registered in a live session. A
  // skill or command of the person's called herd (register refused the name),
  // and `claude -p "/herd"` where nothing draws, get the event passed on: an
  // answer without next would run no command at all. A gate, so caught.
  on('command.run', { command: 'herd' }, async ($, e, next) => {
    if (!live.isLive || !live.isRegistered) {
      return next(e)
    }

    const tab = tabOf(e.args)

    if (tab !== null) {
      await update($, tabAtom, () => tab)
    }

    const opened = await $.ui.open({ id: PANE, title: 'Herd', focus: true, closeOnEscape: true })

    return opened.isPlaced ? {} : { text: `The herd pane is waiting: ${opened.reason}` }
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'Pane', requestId: 'herd' }, async ($, e) => {
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
        accept: (entry: HerdInboxEntry) =>
          press('accept', async h => {
            const said = await acceptEntry(h, live.cfg, live.home, entry)
            const got = await readInbox(h, live.cfg, live.home, snapshot?.repos ?? [])

            await h.setInbox(got.entries)

            return said
          }),
        ping: (session: HerdSession) => {
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
    const fallback: HerdTab = await read($, tabAtom)
    // What the mods after this one draw stays, under this line.
    const below = await next(e)

    return Box({
      flexDirection: 'column',
      children: [
        Button({
          key: 'herd-open',
          label: 'herd',
          hotkey: 'h',
          plain: true,
          onPress: async () => {
            try {
              await update($, tabAtom, () => urgentTab(snapshot, acked, fallback))
              await $.ui.open({ id: PANE, title: 'Herd', focus: true, closeOnEscape: true })
            } catch (error) {
              $.ui.log(`herd: pane not opened: ${String(error)}`, { to: 'debug' })
            }
          },
          children: segments.map(s => Text({ ...styleOf(s.tone), wrap: 'truncate-end', children: [s.text] })),
        }),
        below,
      ],
    })
  })
}
