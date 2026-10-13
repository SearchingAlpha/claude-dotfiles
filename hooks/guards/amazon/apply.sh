#!/bin/sh
# Preview (default) or apply the Amazon guards' hook registrations in
# ~/.claude/settings.json. Opt-in: hooks/hooks.json never registers them, and
# this is apply.sh, not install.sh, so no installer runs it on its own.
#
#   sh apply.sh                      dry-run diff of ~/.claude/settings.json (nothing written)
#   sh apply.sh --with-recipient     include recipient_guard (see README.md: sequencing)
#   sh apply.sh --print              print the fragment only
#   sh apply.sh --apply [...]        write it (settings/merge.py keeps a timestamped backup)
#   --force-worktree                 allow --apply from a linked git worktree
set -eu
here=$(cd "$(dirname "$0")" && pwd -P)
repo=$(cd "$here/../../.." && pwd -P)
with_recipient=0
force_worktree=0
mode=diff
for a in "$@"; do
  case "$a" in
    --with-recipient) with_recipient=1 ;;
    --force-worktree) force_worktree=1 ;;
    --print) mode=print ;;
    --apply) mode=apply ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "apply.sh: unknown option $a" >&2; exit 2 ;;
  esac
done

# Every handler goes through the plugin's launcher, which exits 0 (does not block)
# when the guard script is gone. Bare `python3 <missing.py>` exits 2, and exit 2
# on PreToolUse blocks the call: every redshift query and Datanet run would stop.
launcher="$repo/hooks/guards/run.sh"
if [ ! -f "$launcher" ]; then
  echo "apply.sh: $launcher not found; run this from a checkout that has hooks/guards/." >&2
  exit 1
fi
gd=$(git -C "$here" rev-parse --absolute-git-dir 2>/dev/null || true)
cd_=$(git -C "$here" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
if [ -n "$gd" ] && [ "$gd" != "$cd_" ]; then
  echo "warning: $here is inside a linked git worktree; hooks registered from here stop running" >&2
  echo "         (without blocking) when it is removed. Run apply.sh from the main checkout," >&2
  echo "         with skills-amazon/guards/private.json copied there." >&2
  if [ "$mode" = apply ] && [ "$force_worktree" = 0 ]; then
    echo "apply.sh: refusing --apply from a linked worktree; add --force-worktree to do it anyway." >&2
    exit 1
  fi
fi

frag=$(mktemp "${TMPDIR:-/tmp}/guards-fragment.XXXXXX.json")
trap 'rm -f "$frag"' EXIT
python3 -I - "$here" "$launcher" "$with_recipient" > "$frag" <<'EOF'
import json, sys
here, launcher, with_recipient = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
scripts = ("create_pipeline", "create_andes_load", "run_job", "reschedule_jobs",
           "deschedule_jobs", "replace_table", "touch_profile", "update_profile_sql",
           "create_klaxon_alarm", "manage_klaxon_alarm", "create_sim_ticket")

def h(name, **extra):
    return dict({"type": "command", "command": "sh",
                 "args": [launcher, f"{here}/{name}.py"], "timeout": 5}, **extra)

# One handler per write script: `if` keeps every other Bash call from spawning it.
datanet = [h("datanet_guard", **{"if": f"Bash(*{s}*)"}) for s in scripts]
# Any server whose name holds "redshift": redshift-mcp, redshift-mcp-server, aaisd-redshift-mcp.
pre = [{"matcher": "^mcp__.*redshift.*__(execute|explain)_query$", "hooks": [h("sql_guard")]},
       {"matcher": "Bash", "hooks": datanet}]
if with_recipient:
    pre.append({"matcher": "^mcp__aws-outlook-mcp__(email_(send|reply|forward|draft)|calendar_meeting)$",
                "hooks": [h("recipient_guard")]})
print(json.dumps({"hooks": {"PreToolUse": pre,
                            "PostToolUse": [{"matcher": "Bash", "hooks": datanet}]}}, indent=2))
EOF

if [ "$mode" = print ]; then cat "$frag"; exit 0; fi
if [ "$with_recipient" = 0 ]; then
  echo "note: recipient_guard left out; add --with-recipient once README.md's sequencing checks pass." >&2
else
  # Registered without an allowlist, recipient_guard would only say it isn't checking.
  if ! got=$(python3 -I - "$here" 2>&1 <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
import recipient_guard as r
try:
    path, allowed = r.allowlist()
except Exception as e:
    print(f"{type(e).__name__}: {e}")
    sys.exit(1)
print(f"recipient_guard: {len(allowed)} allowed addresses in {path}")
EOF
  ); then
    echo "apply.sh: $got. Copy $here/private.example.json to $repo/skills-amazon/guards/private.json" >&2
    echo "          and list the allowed addresses under recipient_guard.allow first." >&2
    if [ "$mode" = apply ]; then exit 1; fi
  else
    echo "$got" >&2
  fi
fi
merge="$repo/settings/merge.py"
if [ ! -f "$merge" ]; then
  cat "$frag"
  echo "settings/merge.py not found: merge the fragment above into ~/.claude/settings.json by hand." >&2
  exit 0
fi
if [ "$mode" = apply ]; then
  python3 "$merge" --apply "$frag"
else
  python3 "$merge" "$frag"
  echo "dry run only; re-run with --apply to write it." >&2
fi
