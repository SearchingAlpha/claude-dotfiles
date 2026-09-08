# Team brief — a five-minute read

How I propose we work this weekend. Proposed, not imposed. Shout if you
disagree.

## Roles

- **Pablo — product, business, presenter.** Scope, the one-liner, the business
  case, demo script, pitch, mentors, submission, and the recurring question
  "what do we show if we present now?" Also the data/ML logic if the challenge
  has a modelling core.
- **Devs — the stack, the architecture, the code.** You call the technical
  shots.
- **Demo laptop owner: [name].** Everything runs there by H24.

## Timeline (H0 = challenges announced)

- **H1** team on the board, first commit, Framing Brief pinned in chat.
- **H3** tracer-bullet spec agreed, scaffold running.
- **H12** tracer-bullet demo: thinnest end-to-end path, ugly, fake data fine.
- **H24 FEATURE FREEZE.** Bugs, demo data and polish of the demo path only.
- **H30** demo locked, fallback video recorded, project submitted.
- **H30–36** rehearse three times. Presenter sleeps before pitching.

## Working rules

1. Tracer bullet first. Ugly and end to end beats three pretty pieces that
   don't connect.
2. Commit when green. Small commits. Nobody breaks main after H20.
3. Every four hours: "if we had to present now, what do we show?" Never
   "nothing".
4. One task per person at a time. Say what you're taking in chat.
5. Real > faked > nothing. An integration that fights for 30 minutes gets
   faked, labelled, and mentioned honestly in the pitch.
6. Stuck 30 minutes? Ask a mentor. Not three hours.
7. Sleep: one 3–4 hour block each, staggered, agreed at H12. Nobody codes
   past 20 hours awake.

## When the coding agent loops

1. Stop. A third retry of the same prompt almost never works.
2. Read the real error yourself: last traceback line, file, line number.
3. Reproduce the smallest piece in isolation. Which layer fails: model,
   search, image, glue?
4. Re-prompt with the literal error pasted in.
5. Still looping: revert to the last green commit and try another path.
6. Iterate on a fast model; switch to the strong one for the hard part.

Anti-patterns: letting the agent rewrite half the project in one go · no
commits · debugging the output instead of the error · new features on a broken
base.

## CLI (one machine keeps `hackspain watch` open all weekend)

`hackspain team join <code>` · `track list` · `milestone add
firstBuild|firstDemo|custom --label "…"` · `post "…" --image x.png` · `feed` ·
`perk list` · `submit`. Only usage counts leave the machine, never code or
prompts.

## Submission at H30

Name · two-sentence description · public repo with README and screenshot ·
demo URL or video · tracks · perks used · status **submitted**.

## Demo rules

One golden path, canned inputs, pre-warmed app, fallback video open in a
second tab. Presenter narrates, one dev drives. No debugging on stage.
