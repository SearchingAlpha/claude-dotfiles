#!/usr/bin/env bash
# subagent-statusline.sh — rows of the subagent panel (settings key `subagentStatusLine`).
#
# Claude Code sends {"columns": N, "tasks": [...]} on stdin once per refresh tick
# and replaces the row of every task whose {"id", "content"} line we print:
#   name ▁▂▄▆█ 48k/1000k 5% opus-5-5 xhigh · label
# (sparkline of the last tokenCount samples, context use, model, effort, label).
# Rows we don't print keep the default rendering: with no jq or python3, or
# input that isn't {"tasks": [...]}, this prints nothing and exits 0, and a
# task it can't read loses only its own row.
# Field names follow https://code.claude.com/docs/en/statusline#task-fields.
#
# Claude Code renders `content` as-is, escape sequences included, and names,
# labels and descriptions come from the model or agent definitions: control
# characters (C0, DEL and C1) in them become spaces. A row wider than
# `columns` is cut by display width, wide characters (CJK, emoji) counting 2;
# the WIDE ranges match dwidth() in statusline.sh.

# stdin is a socket, which /dev/stdin can't reopen (ENXIO): read fd 0 itself.
input=$(cat 2>/dev/null) || IFS= read -r -d '' input

if command -v jq >/dev/null 2>&1; then
    printf '%s' "$input" | jq -c '
      def spark:
        if length < 2 then ""
        else (min) as $lo | (max) as $hi
          | map(if $hi == $lo then 0 else (((. - $lo) * 7 / ($hi - $lo)) | floor) end)
          | map(["▁","▂","▃","▄","▅","▆","▇","█"][.]) | join("")
        end;
      def k: if . < 1000 then (floor | tostring) else ((. / 1000 | floor | tostring) + "k") end;
      # A number the row can print: jq reads 1e400 as the largest double.
      def n: type == "number" and . > -1e15 and . < 1e15;
      def short: (capture("claude-(?<m>[a-z]+-[0-9]+(-[0-9]+)?)") | .m) // .;
      def clean: if type == "string"
                 then explode | map(if . < 32 or (. >= 127 and . < 160) then 32 else . end) | implode
                 else . end;
      def cw: . as $c
        | if $c < 4352 then 1
          elif any([[4352,4447],[8986,8987],[9001,9002],[9193,9203],[9725,9726],[9728,10175],
                    [11035,11093],[11904,42191],[43360,43391],[44032,55203],[63744,64255],
                    [65040,65049],[65072,65135],[65280,65376],[65504,65510],[94176,111359],
                    [126976,129791],[131072,262141]][]; $c >= .[0] and $c <= .[1]) then 2
          else 1 end;
      def cut($n): explode as $cs
        | if ($cs | map(cw) | add // 0) <= $n then .
          else (reduce $cs[] as $c ({w: 0, o: [], full: false};
                  if .full then . else ($c | cw) as $x
                    | if .w + $x > $n - 1 then .full = true else .w += $x | .o += [$c] end end)
                | .o | implode) + "…"
          end;
      (.columns | if type == "number" and . > -1e9 and . < 1e9 then floor else 0 end) as $cols
      | (.tasks | if type == "array" then .[] else empty end)
      | try (
        select(type == "object" and .id != null)
      | [ (.name // .agentType // "agent" | clean),
          (.tokenSamples | if type == "array" then map(select(n)) | .[-16:] else [] end | spark),
          (if (.tokenCount | n) then
             (.tokenCount | k)
             + (if (.contextWindowSize | n) and .contextWindowSize > 0
                then "/" + (.contextWindowSize | k) + " "
                     + ((.tokenCount * 100 / .contextWindowSize) | floor | tostring) + "%"
                else "" end)
           else "" end),
          (if (.model | type) == "string" then (.model | short | clean) else "" end),
          (if .effort == null then "" else (.effort | tostring | clean) end)
        ] as $parts
      | ($parts | map(select(. != "")) | join(" ")) as $head
      | ((.label // .description // "") | tostring | clean) as $label
      | (if $label == "" then $head else $head + " · " + $label end) as $row
      | {id: (.id | tostring),
         content: (if $cols > 1 then ($row | cut($cols)) else $row end)}
      ) catch empty
    ' 2>/dev/null
elif command -v python3 >/dev/null 2>&1; then
    printf '%s' "$input" | python3 -c '
import json, re, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(d, dict):
    sys.exit(0)
num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and -1e15 < v < 1e15
WIDE = ((0x1100, 0x115F), (0x231A, 0x231B), (0x2329, 0x232A), (0x23E9, 0x23F3),
        (0x25FD, 0x25FE), (0x2600, 0x27BF), (0x2B1B, 0x2B55), (0x2E80, 0xA4CF),
        (0xA960, 0xA97F), (0xAC00, 0xD7A3), (0xF900, 0xFAFF), (0xFE10, 0xFE19),
        (0xFE30, 0xFE6F), (0xFF00, 0xFF60), (0xFFE0, 0xFFE6), (0x16FE0, 0x1B2FF),
        (0x1F000, 0x1FAFF), (0x20000, 0x3FFFD))
cw = lambda ch: 2 if any(lo <= ord(ch) <= hi for lo, hi in WIDE) else 1
clean = lambda s: re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", s) if isinstance(s, str) else s
def cut(row, n):
    if sum(map(cw, row)) <= n:
        return row
    out, w = "", 0
    for ch in row:
        if w + cw(ch) > n - 1:
            break
        out, w = out + ch, w + cw(ch)
    return out + "…"
def spark(xs):
    xs = [x for x in xs if num(x)][-16:] if isinstance(xs, list) else []
    if len(xs) < 2:
        return ""
    lo, hi = min(xs), max(xs)
    return "".join("▁▂▃▄▅▆▇█"[0 if hi == lo else int((x - lo) * 7 // (hi - lo))] for x in xs)
k = lambda n: "%d" % n if n < 1000 else "%dk" % (n // 1000)
c = d.get("columns")
cols = int(c // 1) if num(c) and -1e9 < c < 1e9 else 0
def render(t):
    parts = [clean(t.get("name") or t.get("agentType") or "agent"), spark(t.get("tokenSamples"))]
    tc, cw_ = t.get("tokenCount"), t.get("contextWindowSize")
    if num(tc):
        s = k(tc)
        if num(cw_) and cw_ > 0:
            s += "/%s %d%%" % (k(cw_), tc * 100 // cw_)
        parts.append(s)
    m = t.get("model")
    if isinstance(m, str):
        hit = re.search(r"claude-([a-z]+-[0-9]+(-[0-9]+)?)", m)
        parts.append(clean(hit.group(1) if hit else m))
    if t.get("effort") is not None:
        parts.append(clean(str(t["effort"])))
    row = " ".join(p for p in parts if p)
    label = clean(str(t.get("label") or t.get("description") or ""))
    if label:
        row += " · " + label
    if cols > 1:
        row = cut(row, cols)
    return json.dumps({"id": str(t["id"]), "content": row}, ensure_ascii=False)
tasks = d.get("tasks")
for t in tasks if isinstance(tasks, list) else []:
    if not isinstance(t, dict) or t.get("id") is None:
        continue
    try:
        print(render(t))
    except Exception:
        pass               # this task keeps its default row
' 2>/dev/null
fi
exit 0
