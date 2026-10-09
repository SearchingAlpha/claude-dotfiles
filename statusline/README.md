# statusline

Two Claude Code status line scripts. [`settings/user.template.json`](../settings/README.md) registers both; nothing installs them for you.

| Script | Settings key | Prints |
|---|---|---|
| `statusline.sh` | `statusLine` | `Opus 5.5 xhigh │ ctx 65%/400k │ cache 91% 1h │ ~$3.46 est │ ⎇ my-track (track) #42 │ 14:05:09` |
| `subagent-statusline.sh` | `subagentStatusLine` | One row per subagent: `verifier ▁▂▅█ 48k/1000k 4% opus-5-5 xhigh · Reading docs` |

## statusline.sh

Segments, in order. Each one is left out when its data is missing:

| Segment | Source ([available data](https://code.claude.com/docs/en/statusline#available-data)) |
|---|---|
| Model and effort | `model.display_name`, `effort.level` |
| Context | `context_window.used_percentage`. Yellow at 60%, red at 80%. With an auto-compact window (below), it is computed as `total_input_tokens / window` and labelled `/Nk`, because `used_percentage` always counts against the full window and compaction runs at the auto-compact window. |
| Cache | `prompt_cache.hit_ratio` and `ttl`; dimmed `cold` once the cache has expired |
| Cost | `cost.total_cost_usd`, shown as `~$x.xx est`: a client-side list-price estimate, not a bill |
| Branch | `worktree.branch`, else `git symbolic-ref` in `workspace.current_dir`; `[wt]` in a linked worktree |
| Track | `shepherd status --json`: the track whose worktree is the current repo, with its review id and state. On a protected branch with no track, it shows `protected: claim a track`. |
| PR | `pr.number` and `pr.review_state` (GitHub; `MR !n` for GitLab), unless shepherd already shows the review |
| Clock | `--clock FMT` (strftime, default `%H:%M:%S`) in `--tz ZONE` or `$STATUSLINE_TZ`, else the system zone |

**Width.** The line stops `--margin` columns (default 4) short of `COLUMNS`. Claude Code indents the row by 2 and cuts a line that reaches the edge with `…`; add your `statusLine.padding` to the margin. When the line doesn't fit, segments go in this order: PR, cache, cost, clock, the effort word, track, branch. Model and context always stay. Claude Code shows notifications at the right of the same row, and sometimes the effort as `◉ xhigh · /effort`, which can still cut the line on a narrow terminal ([docs](https://code.claude.com/docs/en/statusline#notifications-share-the-status-line-row)).

Widths are display columns, counted the same in any locale: the script decodes UTF-8 itself, and East Asian wide and fullwidth characters (CJK) and emoji count 2. Its wide ranges are a generous superset (all of U+2600–27BF counts 2, for example), because counting too much only drops a segment early. Combining accents count 1. The one way past the edge is a model name plus context that alone are wider than the terminal, since those two are never dropped.

Options:

- `--compact-window N`: the auto-compact window, normally the same number as `autoCompactWindow`. `$CLAUDE_CODE_AUTO_COMPACT_WINDOW` wins over it when set, as it does in Claude Code. Like Claude Code, it reads the variable's leading digits and clamps it to 100K–1M; the model's own window caps it too. A per-model `/autocompact` value (saved in `modelSettings`) or a `--autocompact` launch flag isn't visible to the script, so update the flag if you use those.
- `--tz ZONE` and `--clock FMT`.
- `--margin N`: columns left free at the right (default 4).

Environment: `STATUSLINE_TZ`, `STATUSLINE_SHEPHERD_TTL` (seconds, default 30), `STATUSLINE_CACHE_DIR` (default `~/.claude/statusline`) and `NO_COLOR`. Point `STATUSLINE_CACHE_DIR` at a scratch directory in trial settings, so trial runs leave nothing in `~/.claude`.

**Speed.** One `jq` call per render, plus `git symbolic-ref` outside a worktree session, takes about 15 ms of wall and CPU time on an idle machine. At `refreshInterval: 1` that is about 1.5% of one core per open session, idle or not. `refreshInterval: 10` with `--clock %H:%M` cuts it tenfold.

**Shepherd off the render path.** `shepherd status` takes about 150 ms, so it never runs while rendering:

- A render whose cache is stale or missing starts one detached refresher (`setsid`, else `nohup`) and returns. The next render reads the per-session cache file `shepherd-<session_id>`, which is reused for 30 s.
- The refresher takes `shepherd-<session_id>.lock` itself, after it is detached, so a render that Claude Code cancels mid-launch leaves no lock behind. The lock appears with its epoch already in it (a hard link to a written temp file). A lock older than 60 s belongs to a dead refresher and is taken over. With the lock in hand, the refresher checks the cache once more, so parallel renders make one shepherd call.
- It runs shepherd with `GIT_OPTIONAL_LOCKS=0`. `shepherd status` runs `git status` in every track worktree, and with optional locks on, those calls take `index.lock`. A `git add` or `git commit` in another session then fails with `index.lock: File exists`.
- A current directory that no longer exists, such as a finished track's worktree, shows no track and starts nothing. One that exists but can't be entered gets an empty cache entry, so it isn't retried every second.
- Each refresh deletes the per-session caches untouched for more than 24 hours.

**Failing open.** Claude Code blanks the status line when the script exits non-zero, so it always exits 0 and prints at least the model name:

- Without `jq` it parses with `python3`.
- Without either, it pulls the model name out of the raw text.
- Without `git` or `shepherd`, it drops those segments.

Odd input gets the same treatment from both parsers:

- Anything that isn't exactly one JSON object, trailing text included, gets the fallback above and a line in `statusline.log` in the cache dir.
- An empty string counts as a missing field.
- Control characters become spaces: C0 (newline and ESC included), DEL and C1 (U+0080–009F, such as U+009B, a one-character CSI). A newline in a field can't shift the fields after it, and no escape sequence reaches the terminal. The same goes for a branch name read from git, which allows C1 characters, and for the model name the no-parser fallback reads.
- A number that isn't a non-negative integer of at most 15 digits once rounded, such as a negative cost or `1e18`, drops its segment instead of breaking the shell arithmetic.

Claude Code passes stdin as a socket, which can't be reopened through `/dev/stdin`, so both scripts read file descriptor 0 directly.

## subagent-statusline.sh

Reads `tasks[]` ([task fields](https://code.claude.com/docs/en/statusline#task-fields)) and writes one `{"id", "content"}` line per task. A row is the name, a sparkline of the last 16 `tokenSamples`, `tokenCount` and its share of `contextWindowSize`, the short model name and effort, and the label. Claude Code keeps its default row for any task the script doesn't print. With no `jq` or `python3`, or input that isn't `{"tasks": [...]}`, it prints nothing. A task it can't read loses only its own row.

- **Width.** A row wider than `columns` is cut with `…`, by display width: CJK and emoji count 2, with the same ranges as `statusline.sh`.
- **Control characters.** Claude Code renders `content` as-is, escape sequences included, and the name, label and description come from the model or from agent definitions, so prompt injection can steer them. C0, DEL and C1 characters in every field become spaces, so they can't clear the screen or write the clipboard (OSC 52).
- **Parsers.** `jq` and `python3` give the same rows, odd input included: a non-array `tasks` or `tokenSamples` is ignored, and so is a number of 10^15 or more.

## Tests

```bash
python3 -m unittest discover -s statusline/tests
```

The tests run the scripts the way Claude Code does, with socket stdin and sample JSON. Each test gets a sandboxed `PATH`, so cases like "no jq", "no shepherd" or "no `ln`" are real. A fake `shepherd` counts its calls and records the `GIT_OPTIONAL_LOCKS` it got, and a `setsid` wrapper counts refresher launches. Widths are checked in the C and POSIX locales too, with CJK, emoji and the edges of every wide range, and the `jq` and `python3` parsers are compared on odd input and control characters. A render must take under 100 ms (median).

## Windows

Claude Code runs status line commands through Git Bash when it is installed. Use forward slashes in the command path.

Bash can't run a script with CRLF line endings, and Git for Windows checks files out with CRLF by default (`core.autocrlf=true`). With CRLF, the status line goes blank. `install.ps1` warns when it finds CRLF in the scripts. To fix a checkout, set `git config core.autocrlf input` in the repo, delete the two scripts, and run `git checkout -- statusline/` to get them back with LF. A root `.gitattributes` line `*.sh text eol=lf` would make LF the default for every clone; the repo doesn't have one yet.
