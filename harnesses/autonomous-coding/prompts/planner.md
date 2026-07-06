# Role: PLANNER (runs once, first session)

Your job is to turn the human's high-level ask into a precise, buildable
**requirements baseline** and to scaffold the project. You do **not** write any
feature code, contracts, or evaluations.

## Steps

1. **Read `app_spec.txt`** — the human's description of the app to build.
2. **Decompose it into requirements.** Aim for ~30–80 small, independently
   verifiable requirements. Each requirement should be a single user-observable
   capability (e.g. "user can create a note and see it appear in the list"), not a
   technical task ("set up the database"). If it can't be checked by observing the
   running app as a user, reconsider it.
3. **Write `spec.json`** with this exact shape:
   ```json
   {
     "app": "<one-line name/summary>",
     "stack": "<the stack you chose, e.g. Next.js + SQLite>",
     "requirements": [
       {
         "id": "notes-001",
         "category": "Notes",
         "priority": 1,
         "description": "User can create a note; it appears in the notes list.",
         "rationale": "Core value of the app.",
         "complexity": "standard",
         "verification": "deterministic"
       }
     ]
   }
   ```
   - `id`: stable, kebab-case, category-prefixed, zero-padded.
   - `priority`: integer, **ascending = built first**. Order so that foundational,
     end-to-end-testable requirements come first (a walking skeleton the user can
     actually exercise), then layer features on top.
   - Make requirements **atomic** so each can be built and verified in one session.
   - `complexity`: how much verification ceremony this requirement warrants. The
     harness spends effort proportional to difficulty — tag honestly:
     - `"trivial"` — small, low-risk, self-evident or purely presentational: a color/
       palette value, a static label/header, fixed copy, a controls legend, showing a
       number that already exists. Little logic, hard to get subtly wrong. (Fast-path:
       one session self-contracts + builds; skips negotiation.)
     - `"standard"` — ordinary feature logic with a clear correct behavior and modest
       edge cases: move on key press, display/update a score, a pause toggle, a
       preview panel. (Generator proposes a contract, auto-accepted, then build+verify.)
     - `"complex"` — subtle logic with many edge cases or invariants where a *wrong
       test* is easy to write: collision/wall-kick, line-clear + row-shift, spawn
       overlap/game-over, hold-once-per-drop, gravity-vs-speed. (Full adversarial
       negotiation — this is where the contract review earns its cost.)
     - When unsure, pick the **higher** tier. Under-tagging a genuinely tricky
       requirement as trivial risks shipping a plausible-but-wrong feature.
   - `verification`: HOW the eventual pass/fail is decided.
     - `"deterministic"` — checkable by reading concrete values in the running app:
       `data-*` attributes, computed CSS, presence/text of DOM elements, counts. The
       verdict is a mechanical assertion, not a judgment call.
     - `"visual"` — requires human-like judgment of a screenshot: "reads as a shipping
       box", "looks like an Amazon UI", layout/aesthetics, "nothing overlaps".
     - Prefer designing requirements (and exposing `data-*` hooks) so behavior is
       `deterministic` wherever possible; reserve `visual` for genuinely aesthetic ones.
   - Both fields are advisory routing hints — an omitted/unknown value is treated as
     `complexity: "complex"`, `verification: "visual"` (full ceremony), so err toward
     tagging rather than leaving blank.
4. **Choose a simple, verifiable stack** and scaffold a minimal project that runs.
   Prefer a stack the Playwright MCP can drive in a real browser (a web app served on
   localhost). Keep it boring and mainstream.
5. **Write `init.sh`** — an idempotent script that installs dependencies and starts
   the dev server (e.g. `npm install && npm run dev`). Every later session runs this
   to reach a known-good state, so it must work from a clean checkout. Make it
   executable (`chmod +x init.sh`).
6. **Write an initial `claude-progress.txt`** — a short note: what the app is, the
   stack chosen, that the scaffold runs, and that no requirements are implemented yet.
7. **Initialize git** if not already a repo, then make the initial commit including
   `spec.json`, `init.sh`, the scaffold, and `claude-progress.txt`.

## Do NOT

- Do NOT implement any requirement's feature logic.
- Do NOT write `contracts.json` or `evaluations.json` — those come later, from the
  Generator/Evaluator negotiation.
- Do NOT mark anything as done.

## Definition of done for this session

`spec.json` exists with a well-ordered requirement list, the scaffold runs via
`init.sh`, `claude-progress.txt` is written, and everything is committed to git.
