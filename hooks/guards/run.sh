#!/bin/sh
# Launcher for the guard hooks: `sh run.sh <guard> [args]` runs hooks/guards/<guard>.py
# with the first working Python 3, stdin passed through; `sh run.sh /abs/x_guard.py`
# runs a guard by path (the opt-in amazon/ ones, registered in user settings).
# hooks/hooks.json calls it in shell form, so it works wherever Claude Code's hook
# shell is sh or Git Bash.
#
# A guard that can't run must not block. Python exits 2 when it can't open its
# script, and exit 2 is a block on PreToolUse (and keeps Claude going on Stop), so a
# missing or unreadable guard (renamed in a pull, a branch switch or a partial
# checkout of the tree the plugin is read from) exits 0 here instead.
#
# python3 is tried first (the only name on most Linux and macOS machines). Windows
# often has no real python3: python.org installs python.exe and the `py` launcher,
# and WindowsApps holds a python3.exe alias that only prints "Python was not
# found" (exit 9009) when the Store Python isn't installed. With no usable Python
# this exits 0 too. Either way it says so once a day.
case "$1" in
  */*) g=$1; name=${1##*/}; name=${name%.py} ;;
  *) g="$(dirname "$0")/$1.py"; name=$1 ;;
esac
[ $# -gt 0 ] && shift

# once <marker> <message>: print a systemMessage for the user at most once a day.
once() {
  d="${CLAUDE_GUARDS_HOME:-$HOME/.claude/guards}"
  if mkdir -p "$d" 2>/dev/null && [ -z "$(find "$d/$1" -mtime -1 2>/dev/null)" ]; then
    : > "$d/$1"
    printf '{"systemMessage": "%s"}\n' "$2"
  fi
}

if [ ! -f "$g" ] || [ ! -r "$g" ]; then
  echo "guards: $g is missing or unreadable; $name did not run" >&2
  name=$(printf %s "$name" | tr -cd 'A-Za-z0-9_.-')        # it goes into JSON and a file name
  once "missing-$name" "guards: $name.py is missing or unreadable, so $name is not running. Check the checkout it lives in; see hooks/guards/README.md."
  exit 0
fi
for py in python3 python; do
  p=$(command -v "$py" 2>/dev/null) || continue
  case "$p" in
    *WindowsApps*) "$p" -c "" >/dev/null 2>&1 || continue ;;
  esac
  if [ "$py" = python ]; then        # may be Python 2 on old systems
    "$p" -c 'import sys; sys.exit(sys.version_info < (3, 7))' >/dev/null 2>&1 || continue
  fi
  exec "$p" -I "$g" "$@"
done
if command -v py >/dev/null 2>&1 && py -3 -c "" >/dev/null 2>&1; then
  exec py -3 -I "$g" "$@"
fi
echo "guards: no Python 3 found (python3, python, py -3); $name did not run" >&2
once no-python "guards: no Python 3 found (python3, python or py -3), so the secret guards are not running. Install Python 3 or disable the plugin hooks; see hooks/guards/README.md."
exit 0
