#!/usr/bin/env bash
# check.sh — the golden path as a test. Language-neutral: fill in the commands
# for the stack the team picked. Exit non-zero on any failure. Run before
# every merge; agents run it before declaring done.
set -euo pipefail
cd "$(dirname "$0")"

echo "▶ 1/3 build + lint"
# e.g.  npm run build && npm run lint
# e.g.  uv run ruff check . && uv run python -m compileall -q .
# e.g.  go build ./... && go vet ./...

echo "▶ 2/3 unit tests"
# e.g.  npm test -- --watch=false
# e.g.  uv run pytest -q

echo "▶ 3/3 golden path on fixtures/"
# Run the demo path end to end on the canonical fixture and assert the
# demo-moment output. Example shape:
#   out=$(<command that runs the demo path on fixtures/input.json>)
#   grep -q '<expected marker in the output>' <<<"$out" \
#     || { echo "✗ golden path FAILED"; exit 1; }

echo "✓ check passed"
