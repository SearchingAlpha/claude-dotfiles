// The herd's $.state contract, and the snapshot it draws from. Every value the
// module reads or writes with atom/read/update is declared under 'herd'; the
// snapshot's shapes are declared beside it so the hooks import them from
// 'claude-code'. No imports and no `export {}`: `claude plugin validate`
// refuses a contract that exports.
declare module 'claude-code' {
  /** How a line or a run reads: green, yellow, red, in progress, or nothing to say. */
  type HerdTone = 'ok' | 'warn' | 'fail' | 'busy' | 'dim'

  /** The pane's tabs. */
  type HerdTab = 'tracks' | 'sessions' | 'runs'

  /** One enrolled repo, as `shepherd status --all --json` lists it. */
  interface HerdRepo {
    name: string
    /** The main checkout: where `shepherd inbox` and `accept` run. */
    primary: string
  }

  /** One shepherd track, joined to its owner's row in `claude agents --json --all`. */
  interface HerdTrack {
    repo: string
    name: string
    /** active, review, paused or merged. */
    status: string
    goal: string | null
    sessionId: string | null
    /** Shepherd's word for the owner session: busy, idle, shell or ended. */
    owner: string | null
    isLive: boolean
    reviewId: string | null
    reviewState: string | null
    /** passing, failing, pending, none or unknown. */
    checks: string | null
    /** When shepherd last asked the review backend, in ms; only `shepherd serve` refreshes it. */
    checkedAt: number | null
    reviewUrl: string | null
    lastActivity: number | null
    warnings: string[]
    /** Pairs with another track whose merge would conflict (the band counts the tracks with any). */
    conflicts: number
    /** Its session waits on the person (state blocked, or status waiting). */
    isBlocked: boolean
    waitingFor: string | null
    /** Active, and nobody works on it: the owner ended, or no activity for stale_hours. */
    isStale: boolean
  }

  /** One row of `claude agents --json --all`. */
  interface HerdSession {
    sessionId: string
    shortId: string | null
    name: string | null
    /** interactive or background. */
    kind: string
    /** Background sessions: working, blocked, done, failed or stopped. */
    state: string | null
    /** While the process lives: busy, waiting or idle. */
    status: string | null
    waitingFor: string | null
    startedAt: number | null
    cwd: string | null
    /** Its process is alive (the row has a pid), so a message reaches it now. */
    isLive: boolean
    isBlocked: boolean
    /** The shepherd track it owns, when it owns one. */
    track: string | null
  }

  /** One headless run log (a stream-json log, or a text log's last matching line). */
  interface HerdRun {
    /** path@mtimeMs: what Ack stores; a rewritten log alerts again. */
    key: string
    source: string
    kind: 'stream' | 'line' | 'late'
    name: string
    path: string
    size: number
    mtimeMs: number
    /** ok, denied, asked, running, quiet, unjudged, unfinished, failed, no result, no line, unreadable, late. */
    verdict: string
    tone: HerdTone
    /** One phrase for the band and the status line. */
    why: string
    /** The numbers the Runs tab shows under it. */
    detail: string
  }

  /** One configured run source and what reading it found. */
  interface HerdSource {
    label: string
    kind: 'stream' | 'line'
    glob: string
    error: string | null
    count: number
  }

  /** What one refresh found; cached in $.store and shared by every session that loads the mod. */
  interface HerdSnapshot {
    /** The shape's version (SCHEMA in herd.ts): a snapshot another build wrote is not drawn. */
    schema: number
    at: number
    /** How long the read took, in ms. */
    tookMs: number
    /** The options it was read with: a session with other options reads for itself. */
    config: string
    tracksOn: boolean
    repos: HerdRepo[]
    tracks: HerdTrack[]
    tracksError: string | null
    /** The shepherd binary would not start: the band leaves tracks out. */
    tracksMissing: boolean
    sessionsOn: boolean
    sessions: HerdSession[]
    sessionsError: string | null
    sessionsMissing: boolean
    runs: HerdRun[]
    sources: HerdSource[]
    sourcesError: string | null
  }

  /** One unaccepted shepherd log entry addressed to the person. */
  interface HerdInboxEntry {
    repo: string
    primary: string
    id: string
    at: number | null
    type: string
    from: string
    text: string
  }

  /** The mod's own failures, shown in the pane's footer: none of them stops the rest. */
  interface HerdFaults {
    /** The last refresh failed (the band shows `herd ?` until one lands). */
    refresh: string | null
    /** The last read could not be stored for the other sessions. */
    share: string | null
    /** `/herd` could not be registered: a skill or command owns the name. */
    command: string | null
  }

  interface PluginState {
    herd: {
      /** The last refresh, this session's or another's; null before the first. */
      snapshot: HerdSnapshot | null
      /** Ack keys of the run failures the person has seen. */
      acked: string[]
      /** The pane's open tab. */
      tab: HerdTab
      /** What Inbox found, null before it is pressed. */
      inbox: HerdInboxEntry[] | null
      /** What the last button press said: the board URL, an accept, a ping. */
      notice: string | null
      faults: HerdFaults
    }
  }
}
