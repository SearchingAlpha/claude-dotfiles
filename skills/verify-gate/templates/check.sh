#!/usr/bin/env bash
# scripts/check: the one command that answers "is this change done?"
#
#   scripts/check [--fast|--full]
#
#   --fast  (default) seconds, offline, deterministic. The Stop gate runs it at the end of
#           every turn that changed the tree, so keep it well under a minute.
#   --full  everything still deterministic and free: the whole suite, slow tests, structural
#           data checks. Run it before a commit or a PR, and in /goal conditions.
#
# Output contract, read by people, by Claude and by the Stop gate:
#   at most 20 summary lines, then one last line:
#     CHECK PASS n/n      exit 0, every step passed
#     ERROR <reason>      exit 1, something Claude should fix
#     ERROR <reason>      exit 127, the check can't judge (no steps, a tool is missing):
#                         the gate tells the user instead of sending Claude to fix it
#
# Fill the slots with this repo's commands; an empty slot is skipped. Nothing is detected
# for you. Keep model calls, network access and deploys out of both modes. Each slot runs
# under `bash -o pipefail -c`, so `cmd | tail -n 5` fails when cmd does. A slot must not
# rewrite tracked files: run formatters in their check mode (ruff format --check,
# prettier --check), or the gate checks the reformatted tree again on the next stop.
# Gitignore the reports a slot writes (junit.xml, coverage.xml, htmlcov/, test-results/):
# an untracked file that changes on every run makes the gate run the check again.

LINT=""        # e.g. ruff check .               | npx eslint .
TYPES=""       # e.g. mypy src                   | npx tsc --noEmit
TESTS=""       # fast subset, e.g. python3 -m pytest -q -x -m "not slow" | npm test --silent
TESTS_FULL=""  # --full suite, e.g. python3 -m pytest -q; empty = same as TESTS
EXTRA_FULL=""  # --full only: deterministic extras, e.g. python3 tools/check_data.py

set -u
MODE=fast
case "${1:-}" in
  ""|--fast) MODE=fast ;;
  --full) MODE=full ;;
  -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
  *) echo "ERROR unknown option ${1} (use --fast or --full)"; exit 127 ;;
esac
cd "$(dirname "$0")/.." || { echo "ERROR cannot cd to the repo root"; exit 127; }

LOG=$(mktemp "${TMPDIR:-/tmp}/check.XXXXXX") || { echo "ERROR mktemp failed"; exit 127; }
trap 'rm -f "$LOG" "$LOG.fail"' EXIT

total=0 passed=0 first_fail="" first_code=0

step() {  # step NAME COMMAND: run one slot; --fast stops at the first failure
  [ -n "$2" ] || return 0
  if [ -n "$first_fail" ] && [ "$MODE" = fast ]; then return 0; fi
  total=$((total + 1))
  local start=$SECONDS code
  bash -o pipefail -c "$2" </dev/null >"$LOG" 2>&1
  code=$?
  if [ "$code" -eq 0 ]; then
    passed=$((passed + 1))
    printf 'ok    %-6s %ss\n' "$1" "$((SECONDS - start))"
    return 0
  fi
  printf 'FAIL  %-6s %ss  exit %s\n' "$1" "$((SECONDS - start))" "$code"
  if [ -z "$first_fail" ]; then
    first_fail=$1 first_code=$code
    cp "$LOG" "$LOG.fail"
  fi
}

step lint "$LINT"
step types "$TYPES"
if [ "$MODE" = full ]; then
  step tests "${TESTS_FULL:-$TESTS}"
  step extra "$EXTRA_FULL"
else
  step tests "$TESTS"
fi

if [ "$total" -eq 0 ]; then
  echo "ERROR no steps configured: fill LINT, TYPES or TESTS in scripts/check"
  exit 127
fi
if [ -z "$first_fail" ]; then
  echo "CHECK PASS $passed/$total"
  exit 0
fi
# The tail of the first failing step fills the rest of the 20 summary lines.
budget=$((20 - total))
if [ "$budget" -gt 0 ]; then
  grep -v '^[[:space:]]*$' "$LOG.fail" | tail -n "$budget" | cut -c1-300
fi
if [ "$first_code" -eq 126 ] || [ "$first_code" -eq 127 ]; then
  echo "ERROR $first_fail: command not found or not executable (exit $first_code)"
  exit 127
fi
echo "ERROR $first_fail failed (exit $first_code); $passed/$total steps passed"
exit 1
