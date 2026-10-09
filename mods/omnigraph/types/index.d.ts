// The omnigraph $.state contract, and the shapes it holds. Every value the
// module reads or writes with atom/read/update is declared under 'omnigraph';
// the shapes sit beside it so the hooks files import them from 'claude-code'.
// No imports and no `export {}`: `claude plugin validate` refuses a contract
// that exports.
declare module 'claude-code' {
  /** One graph op the mod saw: a matched Bash command or MCP tool call, and what it did to the store. */
  interface OmniOp {
    /** When the call started, ms since the epoch. */
    t: number
    /** The op as the feed names it: `mutate create_issue`, `compile`, an MCP tool's short name. */
    op: string
    source: 'bash' | 'mcp'
    /** The store's name (the header's), or null when the op had no store to diff. */
    store: string | null
    ok: boolean
    /** The producer's own latency when its ops log said, else the call's wall time. */
    ms: number
    /** Net node and edge change; null when there was no store to diff. */
    dNodes: number | null
    dEdges: number | null
    /** Nodes the op added, changed or connected, at most twelve. */
    touched: string[]
    /** Where the graph goes when it follows ops: the hub, and the node to highlight. */
    focus: { center: string; selected: string } | null
    /** Why it failed: the producer's error, `refused`, or the tool's error line. */
    error: string | null
    /** The store's version after the op. */
    version: string | null
    /** The session that saw it: the header counts only its own session's ops. */
    session?: string
    /** Nodes whose record changed, when any did (`~1n` in the feed). */
    changed?: number
    /** The command went to the background: it was not diffed, and the next op's diff carries its change. */
    isBackground?: true
  }

  /** The store as the header and the band read it. */
  interface OmniInfo {
    /** The store folder, absolute. */
    dir: string
    name: string
    branch: string | null
    version: string | null
    nodes: number
    edges: number
    labels: number
    /** A manifest or a nodes file is there. */
    isPresent: boolean
    /** Nodes in a queue status (the Queue tab), and open conflicts. */
    queued: number
    conflicts: number
    /** Lines that did not parse, why the store could not be read, or the mod's last internal error. */
    problem: string | null
  }

  interface PluginState {
    omnigraph: {
      /** The last 50 ops on this root, this session's and earlier ones (kept in $.store). */
      feed: OmniOp[]
      /** When this session went live (or was cleared): the header counts its ops from here. */
      since: number
      info: OmniInfo | null
      /** Bumped each time the store is read again, so the drawings that read the graph redraw. */
      graphRev: number
      tab: 'graph' | 'browse' | 'feed' | 'queue'
      /** The browse path, oldest first; its last node is the selection. */
      trail: string[]
      /** The node the graph is drawn around. */
      center: string | null
      /** The graph and the trail jump to each op's nodes. */
      isFollowing: boolean
      /** The label the browser's first column lists; null: the first node's on the trail. */
      browseLabel: string | null
      queuePage: number
      /** What the last Queue export wrote, or why it could not. */
      notice: string | null
    }
  }
}
