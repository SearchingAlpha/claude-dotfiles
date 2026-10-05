#!/bin/sh
# Claude Code hook launcher for shepherd (wired in hooks/hooks.json).
# Prefers the live checkout's CLI (~/.local/bin/shepherd) over this plugin
# snapshot, so hooks and the CLI agents call always run the same code.
# Always exits 0: a broken shepherd must never block a tool call.
bin="$HOME/.local/bin/shepherd"
[ -f "$bin" ] || bin="$(dirname "$0")/shepherd.py"
mkdir -p "$HOME/.claude/shepherd" 2>/dev/null
python3 "$bin" hook "$1" 2>>"$HOME/.claude/shepherd/hook-errors.log"
exit 0
