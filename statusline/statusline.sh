#!/usr/bin/env bash
# statusline.sh — Claude Code status line (settings key `statusLine`).
#
# Reads the session JSON Claude Code sends on stdin and prints one line:
#   model effort │ ctx % │ cache hit ttl │ ~$cost est │ ⎇ branch (track) #review │ PR │ clock
#
# Fails open: a missing tool (jq, python3, git, shepherd), a missing field or a
# bad input drops segments; the script always prints something and exits 0,
# because Claude Code blanks the status line on a non-zero exit.
#
#   --compact-window N   show and colour context % against the auto-compact
#                        window instead of the full context window. Like
#                        Claude Code, $CLAUDE_CODE_AUTO_COMPACT_WINDOW wins
#                        over it when set.
#   --tz ZONE            IANA zone for the clock, e.g. Europe/Paris (default:
#                        $STATUSLINE_TZ, else the system zone). Only the clock
#                        changes; nothing exports TZ.
#   --clock FMT          strftime format of the clock (default %H:%M:%S)
#   --margin N           columns left free at the right (default 4): Claude
#                        Code indents the row by 2 and truncates a line that
#                        reaches the edge. Add the statusLine `padding`.
# Env:
#   STATUSLINE_TZ            IANA zone for the clock when --tz is not given
#   STATUSLINE_SHEPHERD_TTL  seconds a cached `shepherd status --json` stays fresh (30)
#   STATUSLINE_CACHE_DIR     cache and log dir (default: ~/.claude/statusline)
#   NO_COLOR                 set to disable colours
#
# Field names follow https://code.claude.com/docs/en/statusline#available-data.
# `shepherd status` takes ~150 ms, so it never runs on the render path: a
# detached refresher rewrites a per-session cache file and this script reads it.

SEP=$'\x1f'
cache_dir="${STATUSLINE_CACHE_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/statusline}"
log_file="$cache_dir/statusline.log"

now="${EPOCHSECONDS:-}"
[ -n "$now" ] || now=$(date +%s)
ttl_s="${STATUSLINE_SHEPHERD_TTL:-30}"
case "$ttl_s" in ''|*[!0-9]*) ttl_s=30 ;; esac

log() {
    mkdir -p "$cache_dir" 2>/dev/null || return 0
    printf '%s %s\n' "$now" "$*" >>"$log_file" 2>/dev/null
}

# Control characters (C0, DEL, and C1 written as UTF-8) as spaces, in REPLY:
# for text that didn't come through a parser, such as a git branch name.
clean() {
    local LC_ALL=C
    local s="${1//[$'\001'-$'\037'$'\177']/ }"
    REPLY="${s//$'\302'[$'\200'-$'\237']/ }"
}

# --- shepherd refresher (detached; never on the render path) ---------------
# The lock holds the epoch it was taken. It appears with that content already
# in place (a hard link to a written temp file), so no reader sees it empty.
# The refresher takes it itself, after setsid: a render killed mid-launch then
# leaves no lock behind. A lock older than 60 s belongs to a dead refresher.
take_lock() {
    local lock="$1" tmp="$1.tmp.$$" held=""
    printf '%s\n' "$now" >"$tmp" 2>/dev/null || return 1
    if ! ln "$tmp" "$lock" 2>/dev/null; then
        [ -e "$lock" ] && IFS= read -r held <"$lock" 2>/dev/null
        case "$held" in ''|*[!0-9]*) held=0 ;; esac
        if [ -e "$lock" ] && [ $(( now - held )) -le 60 ]; then
            rm -f "$tmp"
            return 1
        fi
        rm -f "$lock"
        # No hard links on this filesystem: fall back to an exclusive create.
        ln "$tmp" "$lock" 2>/dev/null || ( set -C; printf '%s\n' "$now" >"$lock" ) 2>/dev/null \
            || { rm -f "$tmp"; return 1; }
    fi
    rm -f "$tmp"
    return 0
}

# Three lines: cwd, epoch, and track/review fields joined by \x1f.
write_cache() {
    printf '%s\n%s\n%s\n' "$1" "$now" "$3" >"$2.tmp.$$" && mv -f "$2.tmp.$$" "$2"
}

refresh_shepherd() {
    local cwd="$1" cache="$2" json="" fields="" top="" branch="" c_cwd="" c_time=0
    # Another refresher may have finished while this one was starting.
    if [ -r "$cache" ]; then
        { IFS= read -r c_cwd; IFS= read -r c_time; } <"$cache"
        case "$c_time" in ''|*[!0-9]*) c_time=0 ;; esac
        [ "$c_cwd" = "$cwd" ] && [ $(( now - c_time )) -lt "$ttl_s" ] && return 0
    fi
    # A cwd that can't be entered (a removed worktree) gets an empty entry, so
    # renders don't start a new refresher every second until the TTL is up.
    if ! cd "$cwd" 2>/dev/null; then
        write_cache "$cwd" "$cache" ""
        return 0
    fi
    # `shepherd status` runs `git status` in every track worktree. Without
    # optional locks it doesn't take index.lock, so it can't make a `git add`
    # or `git commit` running in another session fail.
    export GIT_OPTIONAL_LOCKS=0
    top=$(git rev-parse --show-toplevel 2>/dev/null)
    branch=$(git symbolic-ref --short -q HEAD 2>/dev/null)
    if command -v timeout >/dev/null 2>&1; then
        json=$(timeout 20 shepherd status --json 2>/dev/null)
    else
        json=$(shepherd status --json 2>/dev/null)
    fi
    if [ -n "$json" ] && command -v python3 >/dev/null 2>&1; then
        fields=$(printf '%s' "$json" | python3 -c '
import json, os, re, sys
top, branch = sys.argv[1], sys.argv[2]
try:
    v = json.load(sys.stdin)
except Exception:
    sys.exit(0)            # not enrolled: shepherd prints text, not JSON
if not isinstance(v, dict):
    sys.exit(0)
real = lambda p: os.path.realpath(p) if p else ""
tracks = v.get("tracks") or []
hit = next((t for t in tracks if top and real(t.get("worktree")) == real(top)), None)
if hit is None and branch:
    hit = next((t for t in tracks if t.get("branch") == branch), None)
r = (hit or {}).get("review") or {}
prot = branch if hit is None and branch in (v.get("protected") or []) else ""
# One line of \x1f-separated fields: no control characters inside a field.
clean = lambda x: re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(x or ""))
print("\x1f".join(clean(x) for x in (
    (hit or {}).get("name"), r.get("id"), r.get("state"), prot)))
' "$top" "$branch" 2>>"$log_file")
    fi
    write_cache "$cwd" "$cache" "$fields"
    # Per-session caches are useless once their session has ended: delete
    # those untouched for a day (-mtime +1 would mean two).
    find "$cache_dir" -maxdepth 1 -name 'shepherd-*' -mmin +1440 -delete 2>/dev/null
    return 0
}

if [ "${1:-}" = "--refresh-shepherd" ]; then
    lock_path="$3.lock" owned=""
    trap '[ -n "$owned" ] && rm -f "$lock_path"' EXIT
    trap 'exit 1' HUP INT TERM
    take_lock "$lock_path" || exit 0
    owned=1
    refresh_shepherd "$2" "$3"
    exit 0
fi

flag_window="" tz="${STATUSLINE_TZ:-}" clock_fmt="%H:%M:%S" margin=4
while [ $# -gt 0 ]; do
    case "$1" in
        --compact-window) flag_window="${2:-}"; shift ;;
        --tz) tz="${2:-}"; shift ;;
        --clock) clock_fmt="${2:-$clock_fmt}"; shift ;;
        --margin) margin="${2:-}"; shift ;;
    esac
    [ $# -gt 0 ] && shift
done
case "$margin" in ''|*[!0-9]*) margin=4 ;; esac
# The window Claude Code compacts at: the env var outranks every other way of
# setting it. Like Claude Code, read its leading digits ("500k" is 500) and
# clamp to 100K..1M; the model's own window caps it further down.
compact_window="$flag_window"
if [ -n "${CLAUDE_CODE_AUTO_COMPACT_WINDOW:-}" ]; then
    env_window="${CLAUDE_CODE_AUTO_COMPACT_WINDOW%%[!0-9]*}"
    [ -n "$env_window" ] && compact_window="$env_window"
fi
case "$compact_window" in ''|*[!0-9]*) compact_window="" ;; esac
if [ -n "$compact_window" ]; then
    compact_window=$(( 10#$compact_window ))
    [ "$compact_window" -lt 100000 ] && compact_window=100000
    [ "$compact_window" -gt 1000000 ] && compact_window=1000000
fi

# Claude Code hands the script a socket as stdin, and a socket can't be
# reopened through /dev/stdin (ENXIO), so read fd 0 itself.
input=$(cat 2>/dev/null) || IFS= read -r -d '' input

# --- parse: one jq call, else python3, else a crude match ------------------
# Field order: model effort pct tokens size hit ttl warm cents wt_branch
#              git_worktree cwd session pr pr_state pr_kind
# Both parsers take exactly one JSON object (anything else, trailing text
# included, fails), treat an empty string like a missing one, and turn control
# characters into spaces, so a newline in a field can't shift the ones after it.
JQ_PROG='
if length != 1 or (.[0] | type) != "object" then error("not one JSON object") else .[0] end
| def g(p): try getpath(p) catch null;
  def nz: select(. != null and . != "");
  def r: if type == "number" then (. + 0.5 | floor) else null end;
  def s: if . == null then ""
         elif type == "number" and . == floor then floor | tostring  # 42.0 as 42, like python
         else tostring | explode | map(if . < 32 or (. >= 127 and . < 160) then 32 else . end)
              | implode end;
[ ((g(["model","display_name"]) | nz) // g(["model","id"])),
  g(["effort","level"]),
  (g(["context_window","used_percentage"]) | r),
  (g(["context_window","total_input_tokens"]) | r),
  (g(["context_window","context_window_size"]) | r),
  (g(["prompt_cache","hit_ratio"]) | if type == "number" then (. * 100 | r) else null end),
  g(["prompt_cache","ttl"]),
  g(["prompt_cache","warm"]),
  (g(["cost","total_cost_usd"]) | if type == "number" then (. * 100 | r) else null end),
  g(["worktree","branch"]),
  g(["workspace","git_worktree"]),
  ((g(["workspace","current_dir"]) | nz) // g(["cwd"])),
  g(["session_id"]),
  g(["pr","number"]),
  g(["pr","review_state"]),
  g(["pr","kind"])
] | map(s) | join("\u001f")'

PY_PROG='
import json, math, re, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if not isinstance(d, dict):
    sys.exit(1)
def g(*path):
    v = d
    for k in path:
        if not isinstance(v, dict):
            return None
        v = v.get(k)
    return v
num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
r = lambda v: int(math.floor(v + 0.5)) if num(v) else None
def s(v):  # what jq tostring prints
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    elif isinstance(v, (dict, list)):
        v = json.dumps(v, separators=(",", ":"), ensure_ascii=False)
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(v))
alt = lambda a, b: b if a is None or a is False or a == "" else a  # jq: (a | nz) // b
hit, cost = g("prompt_cache", "hit_ratio"), g("cost", "total_cost_usd")
vals = [alt(g("model", "display_name"), g("model", "id")), g("effort", "level"),
        r(g("context_window", "used_percentage")), r(g("context_window", "total_input_tokens")),
        r(g("context_window", "context_window_size")),
        r(hit * 100) if num(hit) else None,
        g("prompt_cache", "ttl"), g("prompt_cache", "warm"),
        r(cost * 100) if num(cost) else None,
        g("worktree", "branch"), g("workspace", "git_worktree"),
        alt(g("workspace", "current_dir"), g("cwd")), g("session_id"),
        g("pr", "number"), g("pr", "review_state"), g("pr", "kind")]
print("\x1f".join(s(v) for v in vals))
'

fields="" parser=""
if command -v jq >/dev/null 2>&1; then
    parser=jq
    fields=$(printf '%s' "$input" | jq -rs "$JQ_PROG" 2>/dev/null) || fields=""
elif command -v python3 >/dev/null 2>&1; then
    parser=python3
    fields=$(printf '%s' "$input" | python3 -c "$PY_PROG" 2>/dev/null) || fields=""
fi

if [ -z "$fields" ]; then
    # No parser, or input it could not read: show the model name if visible.
    [ -n "$parser" ] && [ -n "$input" ] && log "unparseable input (${#input} bytes, $parser)"
    model=""
    if [[ "$input" =~ \"display_name\"[[:space:]]*:[[:space:]]*\"([^\"]*)\" ]]; then
        clean "${BASH_REMATCH[1]}"
        model="$REPLY"
    fi
    printf '%s\n' "${model:-claude}"
    exit 0
fi

IFS="$SEP" read -r model effort pct tokens size hit ttl warm cents wt_branch git_wt cwd session pr pr_state pr_kind <<<"$fields"

# Numbers go into shell arithmetic: keep non-negative integers of at most 15
# digits (jq writes 1e+20 for big ones, and 64-bit arithmetic overflows past
# 18 digits); anything else drops its segment.
for v in pct tokens size hit cents; do
    n="${!v}"
    case "$n" in ''|*[!0-9]*) n="" ;; esac
    [ "${#n}" -le 15 ] || n=""
    [ -n "$n" ] && n=$(( 10#$n ))
    printf -v "$v" '%s' "$n"
done

if [ -n "${NO_COLOR:-}" ]; then
    YEL="" RED="" DIM="" RST=""
else
    YEL=$'\e[33m' RED=$'\e[31m' DIM=$'\e[2m' RST=$'\e[0m'
fi

# Display width of $1 in columns, in REPLY. ${#s} counts characters in a
# UTF-8 locale and bytes in C or POSIX, and neither is the width of CJK text
# or emoji, so this decodes UTF-8 itself, in any locale: East Asian wide and
# fullwidth characters and emoji count 2, everything else 1. The ranges are a
# generous superset (all of U+2600-27BF, for one), because counting too much
# only drops a segment early, while counting too little cuts off the clock.
dwidth() {
    local s="$1"
    case "$s" in *[![:ascii:]]*) ;; *) REPLY=${#s}; return 0 ;; esac
    local LC_ALL=C
    local rest="${s//[[:ascii:]]/}" b c=0 n=0 i
    REPLY=$(( ${#s} - ${#rest} ))
    for (( i = 0; i < ${#rest}; i++ )); do
        printf -v b '%d' "'${rest:i:1}"
        if (( b >= 0xC0 )); then          # a lead byte starts a character
            (( n > 0 )) && REPLY=$(( REPLY + 1 ))   # the one before was cut short
            if (( b >= 0xF0 )); then c=$(( b & 7 )) n=3
            elif (( b >= 0xE0 )); then c=$(( b & 15 )) n=2
            else c=$(( b & 31 )) n=1; fi
            continue
        fi
        if (( n == 0 )); then             # a stray continuation byte
            REPLY=$(( REPLY + 1 ))
            continue
        fi
        c=$(( c << 6 | (b & 63) )) n=$(( n - 1 ))
        (( n == 0 )) || continue
        if (( (c >= 0x1100 && c <= 0x115F) || (c >= 0x231A && c <= 0x231B)
              || (c >= 0x2329 && c <= 0x232A) || (c >= 0x23E9 && c <= 0x23F3)
              || (c >= 0x25FD && c <= 0x25FE) || (c >= 0x2600 && c <= 0x27BF)
              || (c >= 0x2B1B && c <= 0x2B55) || (c >= 0x2E80 && c <= 0xA4CF)
              || (c >= 0xA960 && c <= 0xA97F) || (c >= 0xAC00 && c <= 0xD7A3)
              || (c >= 0xF900 && c <= 0xFAFF) || (c >= 0xFE10 && c <= 0xFE19)
              || (c >= 0xFE30 && c <= 0xFE6F) || (c >= 0xFF00 && c <= 0xFF60)
              || (c >= 0xFFE0 && c <= 0xFFE6) || (c >= 0x16FE0 && c <= 0x1B2FF)
              || (c >= 0x1F000 && c <= 0x1FAFF) || (c >= 0x20000 && c <= 0x3FFFD) )); then
            REPLY=$(( REPLY + 2 ))
        else
            REPLY=$(( REPLY + 1 ))
        fi
    done
    (( n > 0 )) && REPLY=$(( REPLY + 1 ))
    return 0
}

segs=()   # formatted segments, in display order
plain=()  # the same text without colour codes
wide=()   # its width in columns
prio=()   # higher survives longer in a narrow terminal; 8+ is never dropped
glue=()   # "sep", or "space" to join the previous segment with a space
add() { dwidth "$2"; segs+=("$1"); plain+=("$2"); wide+=("$REPLY"); prio+=("$3")
        glue+=("${4:-sep}"); }

# Model, then effort as a segment of its own (joined with a space): Claude
# Code sometimes shows the effort at the right of the row too, so in a narrow
# terminal the word can go while the model stays.
add "${model:-?}" "${model:-?}" 9
[ -n "$effort" ] && add "$effort" "$effort" 5 space

# Context: % of the full window, or of the auto-compact window when known
# (used_percentage always counts against the full window).
if [ -n "$pct$tokens" ]; then
    label=""
    if [ -n "$compact_window" ] && [ -n "$tokens" ]; then
        win="$compact_window"
        [ -n "$size" ] && [ "$size" -gt 0 ] && [ "$size" -lt "$win" ] && win="$size"
        if [ "$win" -gt 0 ]; then
            pct=$(( tokens * 100 / win ))
            label="/$(( win / 1000 ))k"
        fi
    fi
    if [ -z "$pct" ] && [ -n "$tokens" ] && [ -n "$size" ] && [ "$size" -gt 0 ]; then
        pct=$(( tokens * 100 / size ))
    fi
    pct="${pct:-0}"
    c=""
    if [ "$pct" -ge 80 ]; then c="$RED"; elif [ "$pct" -ge 60 ]; then c="$YEL"; fi
    t="ctx ${pct}%${label}"
    add "${c}${t}${c:+$RST}" "$t" 8
fi

# Prompt cache of the main conversation (present after the first response).
if [ -n "$hit$ttl$warm" ]; then
    t="cache"
    [ -n "$hit" ] && t="$t ${hit}%"
    [ -n "$ttl" ] && t="$t $ttl"
    if [ "$warm" = "false" ]; then
        add "${DIM}${t} cold${RST}" "$t cold" 2
    else
        add "$t" "$t" 2
    fi
fi

# Cost: a client-side list-price estimate, not a bill.
if [ -n "$cents" ]; then
    t=$(printf '~$%d.%02d est' $(( cents / 100 )) $(( cents % 100 )))
    add "${DIM}${t}${RST}" "$t" 3
fi

# Branch: the worktree session's branch, else git (symbolic-ref is O(1)).
branch="$wt_branch"
if [ -z "$branch" ] && [ -n "$cwd" ] && [ -d "$cwd" ] && command -v git >/dev/null 2>&1; then
    branch=$(GIT_OPTIONAL_LOCKS=0 git -C "$cwd" symbolic-ref --short -q HEAD 2>/dev/null) \
        || branch=$(GIT_OPTIONAL_LOCKS=0 git -C "$cwd" rev-parse --short -q HEAD 2>/dev/null)
    # git refuses ASCII controls in ref names, but not C1 ones written as UTF-8.
    clean "$branch"
    branch="$REPLY"
fi

# Shepherd track and review, read from the per-session cache; a stale or
# missing cache starts one detached refresh (the refresher takes the lock).
# A cwd that no longer exists, such as a finished track's worktree, shows no
# track and starts nothing.
s_track="" s_rev="" s_state="" s_prot=""
if [ -n "$cwd" ] && [ -d "$cwd" ] && command -v shepherd >/dev/null 2>&1; then
    sid="${session:-nosession}"
    sid="${sid//[^A-Za-z0-9_-]/}"
    cache="$cache_dir/shepherd-${sid:-nosession}"
    c_cwd="" c_time=0 c_fields=""
    if [ -r "$cache" ]; then
        { IFS= read -r c_cwd; IFS= read -r c_time; IFS= read -r c_fields; } <"$cache"
    fi
    case "$c_time" in ''|*[!0-9]*) c_time=0 ;; esac
    if [ "$c_cwd" != "$cwd" ] || [ $(( now - c_time )) -ge "$ttl_s" ]; then
        lock="$cache.lock" l_time=""
        [ -f "$lock" ] && IFS= read -r l_time <"$lock" 2>/dev/null
        case "$l_time" in ''|*[!0-9]*) l_time=0 ;; esac
        # No launch while a live refresher holds the lock (60 s, as in take_lock).
        if [ $(( now - l_time )) -gt 60 ] && mkdir -p "$cache_dir" 2>/dev/null; then
            launcher=nohup
            command -v setsid >/dev/null 2>&1 && launcher=setsid
            $launcher bash "${BASH_SOURCE[0]}" --refresh-shepherd "$cwd" "$cache" \
                </dev/null >/dev/null 2>>"$log_file" &
        fi
    fi
    if [ "$c_cwd" = "$cwd" ] && [ -n "$c_fields" ]; then
        IFS="$SEP" read -r s_track s_rev s_state s_prot <<<"$c_fields"
    fi
fi

review=""
if [ -n "$s_rev" ]; then
    case "$s_rev" in *[!0-9]*) review="$s_rev" ;; *) review="#$s_rev" ;; esac
    [ -n "$s_state" ] && [ "$s_state" != "open" ] && review="$review $s_state"
fi
if [ -n "$branch" ]; then
    t="⎇ $branch"
    if [ -n "$s_track" ] && [ "$s_track" = "$branch" ]; then
        t="$t (track)${review:+ $review}"
    elif [ -n "$git_wt" ] && [ -z "$s_track" ]; then
        t="$t [wt]"
    fi
    add "$t" "$t" 7
fi
if [ -n "$s_track" ] && [ "$s_track" != "$branch" ]; then
    t="track $s_track${review:+ $review}"
    add "$t" "$t" 6
elif [ -z "$s_track" ] && [ -n "$s_prot" ]; then
    t="protected: claim a track"
    add "${YEL}${t}${RST}" "$t" 6
fi

# Open GitHub PR / GitLab MR for the branch, unless shepherd already shows it.
if [ -n "$pr" ] && [ "$pr" != "$s_rev" ]; then
    t="PR #$pr"
    [ "$pr_kind" = "mr" ] && t="MR !$pr"
    [ -n "$pr_state" ] && t="$t $pr_state"
    add "$t" "$t" 1
fi

# Clock via the strftime builtin (no fork); TZ applies to this command only.
clock=""
if [ -n "$tz" ]; then
    TZ="$tz" printf -v clock "%(${clock_fmt})T" -1 2>/dev/null \
        || clock=$(TZ="$tz" date +"$clock_fmt")
else
    printf -v clock "%(${clock_fmt})T" -1 2>/dev/null || clock=$(date +"$clock_fmt")
fi
[ -n "$clock" ] && add "${DIM}${clock}${RST}" "$clock" 4

# --- fit the terminal width: drop the lowest-priority segments first -------
sep=" │ "
dwidth "$sep"
sep_w="$REPLY"
cols="${COLUMNS:-0}"
case "$cols" in ''|*[!0-9]*) cols=0 ;; esac
if [ "$cols" -gt 0 ]; then
    cols=$(( cols - margin ))
    [ "$cols" -gt 0 ] || cols=1   # keep only what is never dropped
fi
while [ "$cols" -gt 0 ]; do
    width=0 first=1 low=-1 lowp=99
    for i in "${!segs[@]}"; do
        width=$(( width + wide[i] ))
        if [ -z "$first" ]; then
            if [ "${glue[$i]}" = space ]; then width=$(( width + 1 )); else width=$(( width + sep_w )); fi
        fi
        first=""
        if [ "${prio[$i]}" -lt 8 ] && [ "${prio[$i]}" -lt "$lowp" ]; then
            lowp="${prio[$i]}" low="$i"
        fi
    done
    if [ "$width" -le "$cols" ] || [ "$low" -lt 0 ]; then
        break
    fi
    unset 'segs[low]' 'plain[low]' 'wide[low]' 'prio[low]' 'glue[low]'
done

out="" first=1
for i in "${!segs[@]}"; do
    if [ -n "$first" ]; then
        out="${segs[$i]}"
    elif [ "${glue[$i]}" = space ]; then
        out="$out ${segs[$i]}"
    else
        out="$out${DIM}${sep}${RST}${segs[$i]}"
    fi
    first=""
done
printf '%s\n' "$out"
exit 0
