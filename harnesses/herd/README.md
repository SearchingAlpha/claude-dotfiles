# herd

herd answers one question: **which of my Claude Code sessions needs me right now?** It lists every live session on the machine — blocked ones first, then the ones that finished their turn, then the ones still working — with what each was last asked and where it runs. `herd serve` shows the same thing as a local web dashboard, with each session's workflows and subagents underneath. It can also start sessions inside tmux so they survive a dropped SSH connection, and jump you into one.

It is the light sibling of [shepherd](../shepherd/README.md): no tracks, no reviews, no hooks and no daemon. It only reads what Claude Code already writes; the one thing it keeps is the dashboard's access token. Stdlib only, Python 3.9+, Linux.

```mermaid
flowchart LR
  subgraph cc["Written by Claude Code"]
    reg["~/.claude/sessions/&lt;pid&gt;.json<br/>status · waitingFor · name · cwd"]
    tr["projects/*/&lt;session&gt;.jsonl<br/>last-prompt · ai-title"]
    wf["projects/*/&lt;session&gt;/workflows/…<br/>scripts · journals · final records"]
    sub["projects/*/&lt;session&gt;/subagents/…<br/>meta + transcripts"]
  end
  proc["/proc<br/>is the pid still that session?"]
  tmux["tmux list-panes<br/>which pane runs it"]
  reg --> herd(["herd"])
  tr --> herd
  wf --> herd
  sub --> herd
  proc --> herd
  tmux --> herd
  herd --> cli(["terminal list"])
  herd --> web(["dashboard on 127.0.0.1"])
```

## Install

```bash
ln -sfn ~/claude-dotfiles/harnesses/herd/herd.py ~/.local/bin/herd
```

## Use

```bash
herd                          # the list (same as `herd ls`); --json for scripts
herd watch                    # redraw every 2 s (-n SECS); rings the bell when a session blocks
herd serve                    # dashboard on http://127.0.0.1:7798 (-p PORT)
herd new api ~/src/api        # start `claude --name api` in a detached tmux session `api`
herd new api ~/src/api -- --model opus   # everything after -- goes to claude
herd go api                   # attach to it (switch-client when already inside tmux)
```

```text
STATE     FOR  NAME                         DIR                      BRANCH           TMUX         LAST PROMPT
blocked    3m  api                          ~/src/api                fix-auth         api:0.0      dialog open
done      12m  Flaky test fix               ~/src/web                main             -            fix the flaky login test
working    1m  sca-serving-layer (bg)       ~                        -                -            continue
```

- **STATE** — `blocked` (a permission prompt, question or dialog is waiting on you; the last column shows Claude Code's own reason, such as `dialog open` or `input needed`), `done` (finished its turn, your move), `working`. Within a state, whoever has waited longest comes first.
- **FOR** — time since the session's status last changed.
- **NAME** — the session name; auto-derived names like `pablomv-6a` are replaced by the session's AI title. `(bg)` marks background sessions.
- **TMUX** — the pane running it, if any; `herd go` accepts a tmux session name, a session name or title, a pid or a session-id prefix.

## The dashboard

`herd serve` prints a URL like `http://127.0.0.1:7798/?t=<token>`; open it once and the token moves into a cookie. On a remote machine, forward the port first (VS Code: *Ports → Forward*, or `ssh -L 7798:localhost:7798 <host>`). To keep it running, start it in tmux: `tmux new -d -s herd-ui herd serve`.

The page redraws every 2 s and groups sessions as **Needs you**, **Done — your turn** and **Working**. Each session card shows its state and how long it has been in it, the reason it is blocked, its directory, branch, tmux pane and last prompt. Underneath:

- **Workflows** — every run still going plus those that ended in the last 24 h, running ones first. A row has the workflow's name, status, description, agents done out of started (and how many are running, stalled, failed or interrupted), elapsed time and tokens, and a **phase bar**: one segment per phase from the script's `meta.phases`, filled by finished agents, pulsing for running ones, red for failures; the phase in progress is outlined. Click a row to see each phase's agents — unfinished ones with their latest tool call, finished ones as a strip of marks (hover for the label). Running workflows open by default; your open/closed choice survives redraws.
- **Subagents** — agents the session spawned itself, with type, description, status and latest tool call.

The title bar counts blocked sessions (`(2) herd`), *Notify me when one blocks* turns on browser notifications, and *Hide sessions with nothing running* keeps only blocked sessions and those with a live workflow or subagent.

## How it decides

- A session is live when its pid exists **and** the process start time matches the `procStart` in its registry file — a crashed session leaves its file behind, and pids get reused.
- Spare pre-started background workers and daemon processes are left out.
- The last prompt and title come from the bookkeeping records Claude Code re-appends to the end of each transcript, so herd reads only the tail of the file.
- A workflow's name and phases come from its script (`workflows/scripts/<name>-<run>.js`), its agents from the run's journal (`started` / `result` / `failed`), and its outcome from the final record Claude Code writes when the run ends (`completed`, `killed`…). A run with no final record is **running** while its journal or an agent transcript was written in the last 15 minutes, **stalled** after that.
- An agent is **done** when its transcript ends in a final answer or a `SubagentHandback`, **stopped** when the user interrupted it, **stalled** when it has been silent for 15 minutes.
- `herd new` unsets the calling session's identity variables (`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, the messaging socket…) so a session started from inside another doesn't inherit it.
- The dashboard listens on 127.0.0.1 only, answers only requests whose `Host` is local (no DNS rebinding), and needs the token kept in `~/.config/herd/token` (mode 0600) — the page shows your prompts.

## Limits

- The session registry, workflow journals and transcripts are undocumented Claude Code files (written against CLI 2.1.294). If a release changes them, `sessions()`, `workflows()` and `subagents()` in `herd.py` are the places to fix.
- Sessions on other machines don't show up; run herd on each machine (over SSH it works as is).
- `herd go` only reaches sessions running in tmux; others show their directory so you can find the terminal.
- A workflow whose script wasn't kept shows its run id instead of a name, and no phases until its agents start.

## Tests

```bash
python3 -m unittest discover -s harnesses/herd/tests -t harnesses/herd/tests
```

They use a fake Claude config dir (`CLAUDE_CONFIG_DIR`), fake sessions and workflow runs, a dashboard on a random port and a private tmux server (`TMUX_TMPDIR`), so they never touch your real sessions.
