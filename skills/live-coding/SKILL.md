---
name: live-coding
description: Live-coding loop with a coding agent while someone watches (interview, pairing, demo) — frame, plan, red test, minimal change, check, evidence, small commit. Use when the user says "live-coding", "reto"/"challenge", or starts a change with an audience.
argument-hint: [the challenge, as the other person said it]
---

# Live-coding

The user drives a coding agent (you) in front of an audience — usually interviewers. The user narrates; you write. What the audience judges is **judgement**: how the problem is framed, how the change is verified, how it is measured. Speed comes second; a small change that is verified beats a big one that "should work".

Challenge: `$ARGUMENTS`

If the repo is new to you or to the user, run [ORIENT.md](ORIENT.md) first. If the challenge touches an LLM feature (tool, guardrail, eval, routing, prompt, integration), read the matching pattern in [AI-CHANGES.md](AI-CHANGES.md) during step 2.

## Screen rules

- **Short output.** The user reads it aloud. One line before each step: what and why. No recap paragraphs.
- **Small diffs.** One idea per edit, readable on a shared screen.
- **The repo's rules win.** Follow `CLAUDE.md`/`AGENTS.md`: commands, conventions, spec rules, model ids.
- **Never** read `.env` or secrets, push, deploy, or change production config (prompt labels, flags) without the user's explicit OK. New dependency: ask first.
- **Two failed attempts on one step: stop.** Say what you learned and propose a smaller cut. Thrashing on screen is the worst outcome.

## Steps

### 1. Frame (≤3 min, no edits)

Return exactly this and wait for the user's OK:

```
Challenge: <one sentence, in the domain's words>
Questions (with my default):
  1. <question> → <default>        (max 3)
Done when:
  - proof: <the test or eval that shows it>
  - visible: <what the audience will see: trace, UI, CLI output>
Out of scope: <what we do NOT do now, and why>
```

Done when the user accepted or changed every default.

### 2. Plan (≤2 min, no edits)

Read the files you will touch — never guess a signature. List them in order, one line each, plus the risks (what can break, what data or labels the change contradicts). If the data contradicts the request, show it now: pushing back with evidence is a strong signal, not a delay. Done when the user says go.

### 3. Branch and spec

`git status` clean, then `git switch -c <prefix>/<slug>`. If the repo keeps specs, docs or a diagram per module, update them first, in the repo's style. Done when the spec says what the code is about to do.

### 4. Red

Write the test at the agreed seam (see the `tdd` skill). Deterministic, no real LLM or network: stub the model and inject senders. For LLM behaviour the "test" is a labelled eval item plus a scorer. Done when the new test fails for the right reason.

### 5. Green, minimal

The least code that turns the test green. Every new LLM, tool or network call is traced the way the repo already traces. If a harness hook runs the tests after each edit (see `agent-harness`), any failure outside your new test is yours to fix before moving on. Done when the full suite is green.

### 6. Verify with evidence

Run the repo's check command, and — if behaviour of the live system changed — one real run on an input that exercises the change. Show the trace, the log line or the UI. Paste the check's pass line. Without it, do not say "done". If you want an independent verdict, run the `code-review` skill or the `/eval` command on the diff.

### 7. Commit

One commit per challenge, in the repo's message style. Stage by name (`git add <paths>`), never `git add -A`. Done when `git show --stat` lists only your files.

### 8. Close (5 lines)

```
Done: <what changed>
Evidence: <check pass line>, <trace/URL/output>
Trade-off: <the debatable decision and its alternative>
Measure in production: <the score or metric that watches it>
Next: <the next cut with more time>
```

## When it goes wrong

- **Agent wrote something wrong:** say it out loud ("that's not what I want, because…"), revert that edit, re-prompt narrower. Reviewing the agent's diff critically is part of what is being judged.
- **Flaky or slow external service:** stub it, finish the change, say which real run you would do after.
- **Bug planted by the audience:** switch to the `diagnosing-bugs` skill — reproduce with a failing test first, then hypothesise.
- **Time running out:** stop at the last green commit and close with step 8; "Next" carries what is missing.
