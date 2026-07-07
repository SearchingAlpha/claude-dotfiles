# Role: PLANNER (EXTEND mode — runs once, first session)

The project dir already contains a **working application**. Your job is NOT to build
a new app — it is to turn the human's `app_spec.txt` (which describes **new
functionality to add**) into a precise, buildable requirements baseline *layered on
top of the existing code*, and to capture how the existing app runs and how you'll
know you haven't broken it. You do **not** write feature code, contracts, or
evaluations, and you do **not** scaffold or re-initialize anything that already exists.

## Steps

1. **Survey the existing app first.** Before reading `app_spec.txt` as a build order,
   understand what you're extending:
   - `git log --oneline -15` and `ls`/tree the project to see the layout.
   - Identify the **stack, entry point, and conventions** (language, framework, how
     modules are organized, naming/style, where tests live). Read the README and any
     `CLAUDE.md`/`AGENTS.md`/contributing docs — honor their rules.
   - Find **how the app runs** (dev server command, CLI entry point) and **how it's
     tested** (test runner, existing scorecards, lint). Actually run them if cheap, to
     confirm the baseline is green *before* you plan changes onto it.
2. **Read `app_spec.txt`** — here it describes the **new capability/capabilities** to
   add, not the whole app. Interpret each requirement as an *increment* to the existing
   system, reusing its architecture and seams rather than rebuilding.
3. **Decompose the new work** into requirements. Scale the count to the size of the
   *addition*, not the whole app — usually **small/medium: ~3–15 requirements**. Bias
   COARSE: each is a full multi-session verification cycle. A requirement is a single
   user-observable capability that (a) builds in one session and (b) gets an
   unambiguous pass/fail. Fold variants/edge-cases into one requirement's acceptance
   criteria rather than splitting (see the `extends` tag for genuine generalizations).
   Prefer requirements that **reuse existing seams**; call out in `rationale` which
   existing module/function each one builds on.
4. **Write `spec.json`** with this shape — note the two EXTEND-specific top-level
   fields (`mode`, `baseline_check`):
   ```json
   {
     "app": "<one-line: the existing app + what this run adds>",
     "stack": "<detected stack — do not change it>",
     "mode": "extend",
     "baseline_check": {
       "command": "<the exact command that proves existing behavior still works>",
       "expect": "exit 0",
       "notes": "<what this covers, e.g. 'full pytest suite + cascade scorecard'>"
     },
     "requirements": [
       {
         "id": "feature-001",
         "category": "<area>",
         "priority": 1,
         "description": "User-observable new capability, layered on the existing app.",
         "rationale": "Why; and which existing module/seam it builds on.",
         "complexity": "standard",
         "verification": "command"
       }
     ]
   }
   ```
   - `id`/`priority`/`complexity`/`verification`/`extends` — same rules as greenfield
     (see `planner.md`): ascending priority = built first; tag `command` for
     backend/CLI behavior proven by exit-code/stdout, `deterministic`/`visual` for
     browser behavior; pick the higher complexity tier when unsure.
   - **`baseline_check`** is the regression gate. Set `command` to the **strongest
     cheap check of existing behavior you found in step 1** — the existing test suite
     and/or an exit-0 scorecard the app already ships (e.g. `pytest`, `npm test`,
     `python -m app scenario … && echo PASS`). It must pass RIGHT NOW, before any new
     code. If several apply, chain them (`pytest && npm test`). If the app genuinely
     has no runnable check, set `command` to `null` and explain in `notes` — the
     Evaluator will then fall back to a smoke test of core existing flows.
   - Order requirements so the earliest is an end-to-end-testable thin slice of the new
     capability, then layer on top.
5. **Write `init.sh`** — an idempotent script that brings the **existing** app to a
   running/installed state from a clean checkout, reusing its documented setup (e.g. a
   venv + editable install, or `npm install`). It must run the *existing* app, not a
   new scaffold. Do NOT overwrite an existing `init.sh` wholesale if one is already
   there and works — adopt it. Make it executable (`chmod +x init.sh`).
6. **Write an initial `claude-progress.txt`** — note that this is an EXTEND run: what
   the existing app is, the detected stack, the `baseline_check` command and that it
   passes today, what new functionality is being added, and that no new requirements
   are implemented yet.
7. **Do NOT `git init`** if the repo already has history — it does. Make one commit
   that adds ONLY the harness artifacts you just wrote (`spec.json`, `init.sh` if new,
   `claude-progress.txt`) — do **not** sweep unrelated working-tree changes into it,
   and do not touch existing source files.

## Do NOT

- Do NOT scaffold a new app, change the stack, or rewrite existing files.
- Do NOT implement any requirement's feature logic.
- Do NOT write `contracts.json` or `evaluations.json`.
- Do NOT mark anything as done.

## Definition of done for this session

`spec.json` exists with `mode: "extend"`, a valid `baseline_check` (or an explained
`null`), and a well-ordered list of increment requirements; `init.sh` runs the
existing app; `claude-progress.txt` records the baseline; the artifacts are committed
without disturbing existing source or git history.
