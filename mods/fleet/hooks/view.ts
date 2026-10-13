// The fleet pane's tree: three tabs (Tracks, Sessions, Runs) over one snapshot,
// and the buttons the person presses. Plain functions over an element table and
// callbacks; register.ts hands both in, so nothing here touches `$`.
import type {
  BoxProps,
  ButtonProps,
  ElementConstructor,
  FleetFaults,
  FleetInboxEntry,
  FleetRun,
  FleetSession,
  FleetSnapshot,
  FleetTab,
  FleetTone,
  RenderElement,
  TextProps,
} from 'claude-code'

import { agoOf, firstLineOf, plural, runLineOf, sessionLineOf, sessionRank, toneOf, trackLineOf } from './fleet'
import type { Segment } from './fleet'

export type Els = {
  Box: ElementConstructor<BoxProps>
  Text: ElementConstructor<TextProps>
  Button: ElementConstructor<ButtonProps>
}

export type PaneModel = {
  snapshot: FleetSnapshot | null
  acked: ReadonlySet<string>
  tab: FleetTab
  inbox: FleetInboxEntry[] | null
  notice: string | null
  /** The mod's own failures: refresh, share, /fleet's registration. */
  faults: FleetFaults
  nowMs: number
  /** This session: no Ping for it. */
  selfId: string | null
}

/** What the buttons do; every one is the person's press. */
export type PaneActions = {
  tab: (tab: FleetTab) => void
  refresh: () => void
  board: () => void
  inbox: () => void
  accept: (entry: FleetInboxEntry) => void
  ping: (session: FleetSession) => void
  ack: (run: FleetRun) => void
  ackAll: () => void
  close: () => void
}

const TABS: readonly { tab: FleetTab; label: string; hotkey: string }[] = [
  { tab: 'tracks', label: 'Tracks', hotkey: '1' },
  { tab: 'sessions', label: 'Sessions', hotkey: '2' },
  { tab: 'runs', label: 'Runs', hotkey: '3' },
]

/** At most this many sessions in the tab: the ones that need the person sort first. */
const MAX_SESSIONS = 40

/** A tone as Text props: the theme's own colors, so it follows the person's theme. */
export function styleOf(tone: FleetTone): TextProps {
  switch (tone) {
    case 'ok':
      return { color: 'success' }
    case 'warn':
      return { color: 'warning' }
    case 'fail':
      return { color: 'error' }
    case 'busy':
      return { color: 'suggestion' }
    default:
      return { dimColor: true }
  }
}

const line = (els: Els, s: Segment, indent = 0): RenderElement =>
  els.Text({ ...styleOf(s.tone), wrap: 'truncate-end', children: [`${' '.repeat(indent)}${s.text}`] })

const dim = (els: Els, text: string, indent = 0): RenderElement =>
  els.Text({ dimColor: true, wrap: 'truncate-end', children: [`${' '.repeat(indent)}${text}`] })

const row = (els: Els, children: RenderElement[]): RenderElement => els.Box({ flexDirection: 'row', columnGap: 2, children })

const button = (els: Els, key: string, label: string, onPress: () => void, hotkey?: string): RenderElement =>
  els.Button({ key, label, plain: true, ...(hotkey !== undefined && { hotkey }), onPress: () => onPress() })

function tracksTab(els: Els, m: PaneModel, a: PaneActions): RenderElement[] {
  const s = m.snapshot
  const out: RenderElement[] = []

  if (s === null) {
    return [dim(els, 'reading…')]
  }
  if (!s.tracksOn) {
    return [dim(els, 'Tracks are off (the tracks option).')]
  }
  if (s.tracksError !== null) {
    out.push(line(els, { text: s.tracksMissing ? `shepherd is not installed here: ${s.tracksError}` : s.tracksError, tone: s.tracksMissing ? 'dim' : 'warn' }))
  }
  if (s.tracks.length === 0 && s.tracksError === null) {
    out.push(dim(els, 'No shepherd tracks in any enrolled repo.'))
  }

  for (const repo of s.repos) {
    const tracks = s.tracks.filter(t => t.repo === repo.name)

    if (tracks.length === 0) {
      continue
    }
    out.push(els.Text({ bold: true, wrap: 'truncate-end', children: [repo.name] }))
    for (const t of tracks) {
      out.push(line(els, trackLineOf(t, m.nowMs), 2))
    }
  }

  out.push(els.Text({ children: [' '] }))
  out.push(row(els, [button(els, 'inbox', m.inbox === null ? 'Inbox' : 'Inbox again', a.inbox, 'i'), button(els, 'board', 'Open board', a.board, 'b')]))

  if (m.inbox !== null) {
    if (m.inbox.length === 0) {
      out.push(dim(els, 'Inbox: nothing addressed to you.'))
    }
    for (const entry of m.inbox) {
      const when = entry.at === null ? '' : ` ${agoOf(entry.at, m.nowMs)} ago`

      out.push(
        row(els, [
          els.Text({ wrap: 'truncate-end', children: [`${entry.repo} ${entry.id}${when} · ${entry.type} from ${entry.from}: ${firstLineOf(entry.text, 120)}`] }),
          button(els, `accept-${entry.repo}-${entry.id}`, 'Accept', () => a.accept(entry)),
        ]),
      )
    }
  }

  return out
}

function sessionsTab(els: Els, m: PaneModel, a: PaneActions): RenderElement[] {
  const s = m.snapshot

  if (s === null) {
    return [dim(els, 'reading…')]
  }
  if (!s.sessionsOn) {
    return [dim(els, 'Sessions are off (the sessions option).')]
  }

  const out: RenderElement[] = []

  if (s.sessionsError !== null) {
    out.push(line(els, { text: s.sessionsError, tone: s.sessionsMissing ? 'dim' : 'warn' }))
  }

  const sessions = [...s.sessions].sort((x, y) => sessionRank(x) - sessionRank(y) || (y.startedAt ?? 0) - (x.startedAt ?? 0))
  const waiting = sessions.filter(x => x.isBlocked).length
  const background = sessions.filter(x => x.kind === 'background')

  out.push(
    dim(
      els,
      `${plural(sessions.length, 'session')} · ${waiting} waiting on you · ${background.length} background (${background.filter(x => x.state === 'working').length} working)`,
    ),
  )

  for (const session of sessions.slice(0, MAX_SESSIONS)) {
    const text = line(els, sessionLineOf(session, m.nowMs))
    const isSelf = session.sessionId === m.selfId

    out.push(
      session.isLive && !isSelf
        ? row(els, [text, button(els, `ping-${session.sessionId}`, 'Ping', () => a.ping(session))])
        : isSelf
          ? row(els, [text, dim(els, '(this session)')])
          : text,
    )
  }
  if (sessions.length > MAX_SESSIONS) {
    out.push(dim(els, `… ${sessions.length - MAX_SESSIONS} more: claude agents`))
  }

  return out
}

function runsTab(els: Els, m: PaneModel, a: PaneActions): RenderElement[] {
  const s = m.snapshot

  if (s === null) {
    return [dim(els, 'reading…')]
  }

  const out: RenderElement[] = []

  if (s.sourcesError !== null) {
    out.push(line(els, { text: s.sourcesError, tone: 'warn' }))
  }
  if (s.sources.length === 0) {
    out.push(dim(els, 'No run sources: set the runs option or a sources file (see the README).'))
  }

  for (const label of [...new Set(s.sources.map(x => x.label))]) {
    const sources = s.sources.filter(x => x.label === label)

    out.push(els.Text({ bold: true, wrap: 'truncate-end', children: [label] }))

    for (const source of sources) {
      if (source.error !== null) {
        out.push(line(els, { text: `${source.glob}: ${source.error}`, tone: 'warn' }, 2))
      } else if (source.count === 0) {
        out.push(dim(els, `${source.glob}: no logs`, 2))
      }
    }

    for (const run of s.runs.filter(r => r.source === label)) {
      const isUnacked = toneOf(run, m.acked) === 'fail'
      const text = line(els, runLineOf(run, m.acked), 2)

      out.push(isUnacked ? row(els, [text, button(els, `ack-${run.key}`, 'Ack', () => a.ack(run))]) : text)
      if (run.detail !== '') {
        out.push(dim(els, run.detail, 4))
      }
    }
  }

  if (s.runs.some(r => toneOf(r, m.acked) === 'fail')) {
    out.push(els.Text({ children: [' '] }))
    out.push(button(els, 'ack-all', 'Ack all', a.ackAll, 'a'))
  }

  return out
}

/** The whole pane: the tab row, the open tab, then when it was read and the common buttons. */
export function paneOf(els: Els, m: PaneModel, a: PaneActions): RenderElement {
  const body = m.tab === 'tracks' ? tracksTab(els, m, a) : m.tab === 'sessions' ? sessionsTab(els, m, a) : runsTab(els, m, a)
  const read =
    m.snapshot === null
      ? 'not read yet'
      : `read ${agoOf(m.snapshot.at, m.nowMs)} ago in ${(m.snapshot.tookMs / 1000).toFixed(1)}s`

  return els.Box({
    flexDirection: 'column',
    children: [
      els.Box({
        flexDirection: 'row',
        columnGap: 3,
        children: TABS.map(t =>
          els.Button({
            key: `tab-${t.tab}`,
            label: t.label,
            hotkey: t.hotkey,
            plain: true,
            dimColor: m.tab !== t.tab,
            onPress: () => a.tab(t.tab),
          }),
        ),
      }),
      els.Text({ children: [' '] }),
      ...body,
      els.Text({ children: [' '] }),
      row(els, [dim(els, read), button(els, 'refresh', 'Refresh', a.refresh, 'r'), button(els, 'close', 'Close', a.close, 'c')]),
      ...faultLines(m.faults).map(text => line(els, { text, tone: 'warn' })),
      ...(m.notice === null ? [] : m.notice.split('\n').map(text => dim(els, text))),
    ],
  })
}

/** The mod's own failures, one line each: nothing else shows them outside `--debug`. */
function faultLines(f: FleetFaults): string[] {
  return [
    f.refresh === null ? null : `refresh failed: ${f.refresh}`,
    f.share === null ? null : `not shared with other sessions: ${f.share}`,
    f.command === null ? null : `/fleet not registered (the band still opens this pane): ${f.command}`,
  ].filter((x): x is string => x !== null)
}
