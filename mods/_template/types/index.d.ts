// The mod's $.state contract: every value the module reads or writes with
// atom/read/update is declared here, under the plugin's name. No imports and
// no `export {}`: `claude plugin validate` refuses a contract that exports.
declare module 'claude-code' {
  interface PluginState {
    'mod-template': {
      /** Main-loop turns finished this session (subagent turns are not counted). */
      turns: number
      /** How long the last of them took, in milliseconds; null before the first. */
      lastTurnMs: number | null
      /** The person pressed "hide" on the band; lasts until /clear, /resume or /branch. */
      isBandHidden: boolean
    }
  }
}
