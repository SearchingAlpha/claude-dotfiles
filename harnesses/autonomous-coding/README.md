# autonomous-coding harness

Harness que **conduce** a Claude Code (no se instala *dentro* de él) para construir
aplicaciones completas de forma autónoma a lo largo de muchas sesiones. A diferencia
de `skills/` y `commands/`, esto se ejecuta *in place*: clonas el repo y lanzas
`loop.py`. No lo toca `install.sh`.

A harness that **drives** Claude Code (`claude -p`) to build a full application
autonomously across many sessions. Run-in-place — not installed into `~/.claude/`.

---

## The idea

A long-running agent is a **relay of amnesiac shifts**: every session starts with a
fresh context window and no memory of the last one. So all durable state lives on
disk, and a thin Python orchestrator (`loop.py`) decides which agent runs next.

This harness extends Anthropic's 2-agent
[autonomous-coding pattern](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
into **three contract-driven roles**, each an independent `claude -p` invocation:

- **Planner** — turns your `app_spec.txt` into `spec.json`, a prioritized list of
  small, user-observable requirements (the *what*). Runs once.
- **Generator** — builds the code, one requirement at a time.
- **Evaluator** — verifies each requirement in a **real browser** and issues a
  pass/fail verdict.

The twist is the **evaluation contract**. Before any code is written for a
requirement, Generator and Evaluator *negotiate* — using the Planner's spec as the
authority — exactly how it will be verified: concrete acceptance criteria + real-user
test steps. The Generator argues for achievable criteria; the Evaluator argues for
spec fidelity. That adversarial tension is what makes the eventual "pass" trustworthy,
instead of the agent grading its own homework.

## Durable artifacts (this *is* the harness)

Everything lives in your `--project-dir`:

| File | Owner (writes) | Purpose |
|------|----------------|---------|
| `app_spec.txt` | **you** | The high-level ask. You write this before running. |
| `spec.json` | Planner | Requirements baseline. Immutable after planning. |
| `contracts.json` | Generator ⇄ Evaluator | Negotiated acceptance criteria + test steps + `status`. |
| `evaluations.json` | Evaluator | Per-requirement verdict + evidence. |
| `state.json` | orchestrator | Phase + counters per requirement. Makes runs resumable. |
| `claude-progress.txt` | every session | Human-readable shift log. |
| `init.sh` | Planner | Install + run the app; every session runs it first. |
| git history | every session | Save points; revert; recover clean state. |

Each role may only write the artifacts it owns (enforced by prompt, not code — see
`prompts/system.md`).

## State machine

`loop.py` is a deterministic driver; the agents are stateless workers. Each iteration
picks ONE role for ONE requirement, runs a single `claude -p` session, then reads the
artifact it produced to decide the next step:

```
no spec.json ─────────────────────────► PLANNER  (writes spec.json, init.sh, first commit)

per requirement (ascending priority, until pass):
  NEGOTIATING
    no contract / status=countered ────► GENERATOR proposes/revises contract   (round++)
    status=proposed ───────────────────► EVALUATOR accepts (agreed) | counters
    status=agreed ─────────────────────► BUILDING
    rounds > cap ──────────────────────► ESCALATE (human pause)
  BUILDING  ───────────────────────────► GENERATOR builds ONLY this req, self-checks, commits
  EVALUATING ──────────────────────────► EVALUATOR runs contract tests in a real browser
    verdict=pass ──────────────────────► done, advance to next requirement
    verdict=fail ──────────────────────► BUILDING (retry++, failure evidence fed back)
    retries > cap ─────────────────────► ESCALATE (human pause)
```

Because state is on disk, **Ctrl+C then rerun the same command resumes** at the next
pending phase.

## Prerequisites

- `claude` CLI on PATH — `npm i -g @anthropic-ai/claude-code`
- Node.js (for the Playwright MCP browser-verification server; auto-fetched via `npx`)
- A Playwright-managed Chromium — `npx playwright install chromium` (one-time).
  `mcp.json` pins `--browser chromium` so verification uses Playwright's own
  browser and does **not** depend on a system Chrome at `/opt/google/chrome`.
- Python 3.9+ (stdlib only — no pip install)
- Authenticated Claude Code (API key or subscription login)

## Usage

```bash
cd harnesses/autonomous-coding

# 1. Create the project dir and describe what to build.
mkdir -p ~/builds/notes
$EDITOR ~/builds/notes/app_spec.txt       # e.g. "A minimal single-user notes web app…"

# 2. Run the harness.
python3 loop.py --project-dir ~/builds/notes

# Cheap smoke tests (don't build a whole app):
python3 loop.py --project-dir ~/builds/notes --max-iterations 1   # just the Planner
python3 loop.py --project-dir ~/builds/notes --only notes-001     # one requirement end-to-end
```

### Real, unconstrained run (build a whole app)

Drop `--max-iterations`/`--only` entirely and launch it **detached** so it survives
closing the terminal (a full build runs for hours across many sessions):

```bash
setsid python3 loop.py --project-dir ~/builds/notes \
  > ~/builds/notes/run.log 2>&1 < /dev/null &
echo "PID $! — see ~/builds/notes/run.log"
```

Monitor and intervene out-of-band:

```bash
python3 loop.py --project-dir ~/builds/notes --status   # read-only progress table (spawns nothing)
tail -f ~/builds/notes/run.log                          # live session stream
git -C ~/builds/notes log --oneline                     # one commit per passed requirement
```

Pause with `kill <PID>` (or Ctrl+C in foreground); **rerun the exact same launch
command to resume** — all state is on disk. When `--status` shows every requirement
`done`, run the app with `cd ~/builds/notes && ./init.sh`.

The **`/autobuild`** slash command wraps all of this: it interviews you for the app
description, writes `app_spec.txt`, preflights, launches detached, and prints these
monitoring commands.

### Flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--project-dir` | (required) | Where the app + all artifacts live. |
| `--model` | `us.anthropic.claude-opus-4-8` | Shared/fallback model for any role without its own override. |
| `--generator-model` | falls back to `--model` | Model for the Generator roles (negotiate + build + fast-path). Point this at a faster/cheaper model — the Generator runs the most sessions. |
| `--evaluator-model` | falls back to `--model` | Model for the Evaluator roles (negotiate + verdict). |
| `--planner-model` | falls back to `--model` | Model for the one-shot Planner. |
| `--deterministic-verdict-model` | falls back to evaluator model | Verdict model for requirements the Planner tagged `verification: "deterministic"` (pass/fail read from `data-*`/DOM, not judged visually). |
| `--max-iterations` | unlimited | Stop after N sessions. |
| `--max-turns` | 200 | Per-session agent-turn cap (passed to `claude`). |
| `--negotiation-rounds` | 3 | Contract rounds before escalating. |
| `--build-retries` | 3 | Build attempts per requirement before escalating. |
| `--session-retries` | 4 | Retries for a session that exits non-zero for a *transient* reason (API/network timeout, throttle) before giving up. State is on disk and sessions are idempotent, so re-running is safe. `0` = fail fast. |
| `--retry-backoff` | 30 | Base seconds for exponential backoff between session retries (30 → 60 → 120 → 240…). |
| `--idle-timeout` | 600 | Kill a session that produces **no output** for this many seconds (a true hang — frozen browser eval, wedged model call, ~0% CPU) so the retry can restart it. Tracks time-since-last-output. `0` = off. |
| `--max-session-seconds` | 1500 | Hard cap on **total** wall-clock per session regardless of output — catches a session stuck in a *loop while still printing* (e.g. repeatedly rewriting an elaborate test-scenario script), which the idle timer never trips on. `0` = off. |
| `--review-every` | 0 (off) | Pause for human review every N iterations. |
| `--only <req-id>` | — | Run a single requirement end-to-end (cheap testing). |
| `--status` | — | Print a read-only progress table + git tail and exit (spawns nothing). |

## Complexity tiers — ceremony proportional to difficulty

The full negotiate→review→build→verdict cycle (4+ sessions) is overkill for a
requirement like "the header is Amazon navy". The Planner tags each requirement with
a `complexity` and a `verification` hint in `spec.json`, and the orchestrator spends
effort accordingly:

| `complexity` | Flow | Sessions | For |
|--------------|------|----------|-----|
| `trivial` | one Generator session self-writes an agreed contract **and** builds → Evaluator verdict | ~2 | colors, static copy, labels, a legend, showing an existing value |
| `standard` | Generator proposes contract → orchestrator **auto-accepts** (no review session) → build → verdict | ~3 | ordinary feature logic with a clear correct behavior |
| `complex` | full adversarial negotiation: propose → review (may counter) → build → verdict | 4+ | subtle logic where a *wrong test* is easy to write (collision, wall-kick, line-clear, game-over) |

`verification` is an orthogonal axis: `deterministic` (pass/fail read from `data-*`/DOM/
computed CSS) vs `visual` (needs judgment of a screenshot). A `deterministic` verdict can
be routed to a cheaper model via `--deterministic-verdict-model` without losing trust,
while `visual` verdicts stay on the heavier evaluator model.

**Backward-compatible:** an untagged (or unknown-valued) requirement defaults to
`complexity: complex` + `verification: visual` — i.e. the original full-ceremony
behavior — so old specs and in-flight runs are unchanged. The `--status` table shows the
tier per requirement (`○` trivial, `◐` standard, `●` complex; `*` = deterministic verdict).

### Verify-first for extension requirements

Sometimes a requirement is really a *generalization* of an earlier one that a correct
implementation already covers — e.g. "clear 2–4 rows at once" over "clear one row"; the
clear algorithm generalizes with no new code. Building it anyway wastes a full
negotiate→build→verify cycle to produce (literally, in one observed run) a comment.

The Planner tags such a requirement `extends: "<parent-id>"`. Once the parent is `done`,
the harness runs a **verify-first probe**: the Evaluator writes a contract for the
extended behavior and verdicts it against the *current* code — no build. If it already
holds, the requirement is done in **one session** instead of four. If the probe fails, it
falls through to the normal cycle (with the probe's contract as the starting point) and
does not re-probe. Best paired with coarser decomposition — prefer folding a
generalization into the parent's acceptance criteria over a separate `extends` requirement.

Example — Opus plans/judges, Sonnet builds, cheap deterministic verdicts:

```bash
setsid python3 loop.py --project-dir ~/builds/notes \
  --generator-model us.anthropic.claude-sonnet-5 \
  --deterministic-verdict-model us.anthropic.claude-sonnet-5 \
  > ~/builds/notes/run.log 2>&1 < /dev/null &
```

## Human intervention

The external loop is what makes intervention easy:

- **Watch + Ctrl+C + resume** — all state is on disk; killing and rerunning is free.
- **`--review-every N`** — the orchestrator pauses and waits for Enter every N
  sessions, so you can inspect the diff / contracts / verdicts between shifts.
- **Escalation pauses** — if a contract can't be agreed within `--negotiation-rounds`,
  or a build fails `--build-retries` times, that requirement is marked `escalated` and
  the run stops for you. Fix `contracts.json`/the code by hand, reset that
  requirement's `phase` in `state.json` (e.g. back to `"negotiating"` or `"building"`),
  and rerun.
- **Tighten autonomy** — the harness runs with `--permission-mode acceptEdits` and an
  explicit tool allowlist (`Bash,Edit,Write,Read,Glob,Grep,mcp__playwright__*`) in
  `loop.py`. Narrow the allowlist to require prompts on riskier tools.

## Cost note

This architecture is **deliberately token-heavy**: a single requirement can span 4+
sessions (negotiation rounds + build + verdict + retries), each a fresh full-context
Claude Code run. That is the price of the robustness the contract negotiation buys.
Use `--only` and `--max-iterations` while tuning prompts; run the full loop when you
mean it.

## When to graduate to the Python Agent SDK

`claude -p` (this harness) is the right tool until you need **runtime, per-tool-call**
control. Move `loop.py` to the Python Agent SDK when you want:

- a `canUseTool` callback to approve/deny/rewrite *individual* tool calls (e.g. gate
  `deploy`/`rm`/external calls while auto-approving edits), or
- a supervisor that inspects each session's structured output and *chooses the next
  prompt dynamically* rather than following a fixed state machine.

The migration is cheap: the six `prompts/*.md` files and the on-disk artifacts are the
actual harness and port over unchanged — you only swap the ~250-line orchestrator.

## Files

```
autonomous-coding/
├── loop.py                    # orchestrator (state machine, stdlib only)
├── mcp.json                   # Playwright MCP (browser verification)
├── prompts/
│   ├── system.md              # shared rules (appended to every session)
│   ├── planner.md
│   ├── generator_negotiate.md
│   ├── evaluator_negotiate.md
│   ├── generator_build.md
│   └── evaluator_verdict.md
├── schemas/                   # JSON Schemas + worked examples for the artifacts
│   ├── spec.schema.json
│   ├── contracts.schema.json
│   └── evaluations.schema.json
└── README.md
```
