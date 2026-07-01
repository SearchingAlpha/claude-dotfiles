---
name: diagnosing-bugs
description: Diagnosis loop for hard bugs and performance regressions. Use when the user says "diagnose"/"debug this", or reports something broken/throwing/failing/slow.
---

# Diagnosing Bugs

A structured six-phase discipline for tackling difficult bugs. The central insight: invest heavily in building a reliable feedback loop before doing anything else.

## Phase 1 — Build a feedback loop

"**This is the skill.** Everything else is mechanical." The goal is a single command that reliably goes red on the specific bug. Without it, no amount of code-reading helps.

Approaches to try, roughly in order:
1. Failing unit/integration/e2e test
2. Curl/HTTP script against a dev server
3. CLI invocation diffing stdout against a snapshot
4. Headless browser script (Playwright/Puppeteer)
5. Replaying a captured trace/payload
6. Throwaway minimal harness
7. Property/fuzz loop
8. Bisection harness (`git bisect run`)
9. Differential loop (old vs. new version)
10. Human-in-the-loop bash script as last resort

Once you have a loop, tighten it — faster, sharper signal, more deterministic. "A 2-second deterministic one is tight — a debugging superpower."

**Cannot build a loop?** Stop, say so explicitly, and request artifacts or environment access. "Do **not** proceed to hypothesise without a loop."

## Phase 2 — Reproduce + minimise

Run it, watch it go red, confirm it matches the user's described symptom. Then shrink the repro — cut inputs and steps one at a time until every remaining element is load-bearing.

## Phase 3 — Hypothesise

Generate **3–5 ranked, falsifiable hypotheses** before testing any. Each must follow the form: "If X is the cause, then changing Y will make the bug disappear." Share with the user before proceeding — they may have domain knowledge that re-ranks instantly.

## Phase 4 — Instrument

Map each probe to a specific prediction. Change one variable at a time. Prefer debugger/REPL over logging. Tag every debug log with a unique prefix (e.g., `[DEBUG-a4f2]`) for easy cleanup. For performance regressions, measure first with a profiler/timing harness rather than adding logs.

## Phase 5 — Fix + regression test

Write the regression test *before* the fix — but only at a "correct seam" that genuinely exercises the real bug pattern. If no correct seam exists, that architectural gap is itself a finding worth noting.

Order: failing test → watch it fail → apply fix → watch it pass → re-run original Phase 1 loop.

## Phase 6 — Cleanup + post-mortem

- Original repro no longer reproduces
- Regression test passes (or absent seam is documented)
- All `[DEBUG-...]` tags removed
- Throwaway code deleted
- Correct hypothesis stated in commit/PR message

Finally, ask what would have *prevented* the bug. If architectural changes are needed, hand off to the appropriate improvement workflow — after the fix is in, when you have maximum context.
