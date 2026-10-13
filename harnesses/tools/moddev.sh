#!/usr/bin/env bash
# moddev.sh — work on a mod in a live session: loads mods/<name> with
# --plugin-dir (it hot-reloads on save, Claude's own edits when its turn ends,
# and stands in for an installed plugin of the same name this session) and
# writes the session's debug log to mods/.debug/<name>.log.
#
#   harnesses/tools/moddev.sh <name> [claude args...]   interactive session
#   harnesses/tools/moddev.sh <name> --smoke            headless load, no model
#                                                       turn (mods/check.sh --load)
#
# <name> is the folder under mods/ (e.g. _template). The mods are those of the
# checkout this script sits in, so run it from the worktree you edit in.
# A --plugin-dir folder is a protected path: Claude's edits to the mod prompt
# you in default and acceptEdits mode, go to the classifier in auto mode, are
# denied in dontAsk and allowed in bypassPermissions. In another terminal:
#   tail -f mods/.debug/<name>.log | grep <plugin-name>
#
# Env: MODS_CLAUDE  the claude binary (default: claude)

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mods_dir="$root/mods"
claude_bin="${MODS_CLAUDE:-claude}"

if [[ $# -lt 1 || "$1" == -h || "$1" == --help ]]; then
    sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
fi

name="${1%/}"
shift
# A folder directly under mods/: no path, no .., no .debug.
case "$name" in
    ""|.*|*/*)
        echo "moddev.sh: name a mod folder under mods/ (e.g. _template), not '$name'" >&2
        exit 2
        ;;
esac
dir="$mods_dir/$name"
if [[ ! -f "$dir/.claude-plugin/plugin.json" || ! -f "$dir/hooks/hooks.json" ]]; then
    echo "moddev.sh: $dir is not a mod (needs .claude-plugin/plugin.json and hooks/hooks.json)" >&2
    exit 2
fi

if [[ "${1:-}" == --smoke ]]; then
    exec "$mods_dir/check.sh" --load "$name"
fi

if ! command -v "$claude_bin" >/dev/null 2>&1; then
    echo "moddev.sh: '$claude_bin' not found on PATH" >&2
    exit 2
fi

mkdir -p "$mods_dir/.debug"
log="$mods_dir/.debug/$name.log"
plugin="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8-sig"))["name"])' "$dir/.claude-plugin/plugin.json" 2>/dev/null || echo "$name")"

echo "moddev: loading $plugin from $dir" >&2
echo "moddev: debug log $log  (tail -f $log | grep $plugin)" >&2
echo "moddev: /plugin shows 'N mods active · ... $plugin ...' once it loaded" >&2
exec "$claude_bin" --plugin-dir "$dir" --debug-file "$log" "$@"
