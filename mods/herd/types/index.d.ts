// The herd mod's $.state contract, and the snapshot it draws: what
// `herd snapshot --json` prints (schema 3, harnesses/herd/herd.py). Every value
// the module reads or writes with atom/read/update is declared under 'herd';
// the snapshot's shapes are declared beside it so the hooks import them from
// 'claude-code'. No imports and no `export {}`: `claude plugin validate`
// refuses a contract that exports.
declare module 'claude-code' {
  /** How a line or a run reads: green, yellow, red, in progress, or nothing to say. */
  type HerdTone = 'ok' | 'warn' | 'fail' | 'busy' | 'dim'

  /** The pane's tabs. */
  type HerdTab = 'tracks' | 'sessions' | 'runs'

  /** One enrolled repo, as shepherd lists it. */
  interface HerdRepo {
    name: string
    /** The main checkout: where `shepherd inbox` and `accept` run. */
    primary: string
  }

  /** One shepherd track, joined by herd to its owner session. */
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
    /** Its session waits on the person. */
    isBlocked: boolean
    waitingFor: string | null
    /** Active, and nobody works on it: the owner ended, or no activity for stale_hours. */
    isStale: boolean
  }

  /** One live session, as `herd ls` lists it. */
  interface HerdSession {
    sessionId: string
    shortId: string | null
    name: string | null
    /** interactive or background. */
    kind: string
    /** herd's word: blocked, done or working. */
    state: string | null
    /** The registry's: busy, waiting, idle or shell. */
    status: string | null
    waitingFor: string | null
    startedAt: number | null
    cwd: string | null
    /** Its process is alive, so a message reaches it now. */
    isLive: boolean
    isBlocked: boolean
    /** The shepherd track it owns, when it owns one. */
    track: string | null
  }

  /** The counts behind a stream run's verdict; null for text logs and schedules. */
  interface HerdRunFacts {
    results: number
    turns: number
    cost: number | null
    denied: number
    subagents: { spawned: number; completed: number; failed: number; killed: number; stopped: number; pending: number }
  }

  /** One headless run log (a stream-json log, or a text log's last matching line), judged by herd. */
  interface HerdRun {
    /** path@mtimeMs: what `herd ack` keeps; a rewritten log alerts again. */
    key: string
    source: string
    kind: 'stream' | 'line' | 'late'
    name: string
    path: string
    size: number
    mtimeMs: number
    /** ok, denied, asked, running, quiet, unfinished, failed, no result, no line, unreadable, late. */
    verdict: string
    tone: HerdTone
    /** One phrase for the band and the status line. */
    why: string
    /** The numbers the Runs tab shows under it. */
    detail: string
    facts: HerdRunFacts | null
  }

  /** One run source of herd's sources file and what reading it found. */
  interface HerdSource {
    label: string
    kind: 'stream' | 'line'
    glob: string
    error: string | null
    count: number
  }

  /** What one `herd snapshot --json` printed; shared in $.store by every session that loads the mod. */
  interface HerdSnapshot {
    /** The shape's version (SCHEMA in herd.ts, SNAPSHOT_SCHEMA in herd.py): another one is not drawn. */
    schema: number
    at: number
    /** How long herd took, in ms. */
    tookMs: number
    host: string
    tracksOn: boolean
    repos: HerdRepo[]
    tracks: HerdTrack[]
    tracksError: string | null
    /** herd found no shepherd to run: the band leaves tracks out. */
    tracksMissing: boolean
    sessionsOn: boolean
    sessions: HerdSession[]
    sessionsError: string | null
    sessionsMissing: boolean
    runs: HerdRun[]
    sources: HerdSource[]
    /** The sources file herd read, or null when none is set up. */
    sourcesFile: string | null
    sourcesError: string | null
    /** Keys of the runs above that someone acked with `herd ack`. */
    acked: string[]
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
      /** The last snapshot, this session's or another's; null before the first. */
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
