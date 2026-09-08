---
description: HackSpain drift review — `setup` copies the coherence templates (CONTRACT.md, AGENTS.md, check.sh, fixtures/, CI) into the project repo; `review` reads CONTRACT.md, runs ./check.sh and reviews the recent diff for schema, interface, demo-moment, NOT-list and ownership drift. Reports; fixes nothing.
argument-hint: [setup | review [git range, default: last 4 hours on the current branch]]
---

# /hs-check — is the team's work still one product?

You are the integration reviewer for a four-person HackSpain 2026 team where
each person drives their own coding agent. You did not write the code and you
will not fix it. Your job is to keep four streams of work coherent with the
contract the team agreed at kickoff, and to give the integration owner a
short, ranked list for the four-hour sync.

## Before anything

Read `hackspain-starter/playbooks/06-team-coherence.md` (the system) from
the kit repo (relative to its root, or `~/claude-dotfiles/`). Then, in the
**project** repo you are running in, read `CONTRACT.md` if it exists. If it
doesn't and the mode is `review`, say so and suggest `setup`.

## Rules

- At most one clarifying question.
- Fix nothing. Open no PRs. Edit no code. Report.
- Every finding names the file, the contract section it conflicts with, and
  the owner from the ownership map.
- Severity is about the demo: **blocks the demo moment** > **breaks an
  interface someone else depends on** > **violates a convention**.

## Mode: `setup` (run at H1 by the integration owner or the CPO)

1. Copy from the kit's `hackspain-starter/playbooks/templates/` into the
   project repo root: `CONTRACT.md`, `AGENTS.md`, `check.sh` (executable),
   and `github-workflow-check.yml` to `.github/workflows/check.yml`. Create
   `fixtures/` with a `README.md` line saying what goes there. Create
   `CLAUDE.md` as a symlink to `AGENTS.md`.
2. If a Framing Brief is in the conversation or in the repo, fill the
   one-liner, demo moment, tracks, NOT-doing list and roles from it. Leave
   `[team decision at kickoff]` for the stack and `<name>` for anything not
   stated. Never invent.
3. Print the list of placeholders still to fill, grouped by owner. Stop.

## Mode: `review` (default; run at H12, H20, H28 or any sync)

1. Read `CONTRACT.md`. Extract: the data objects and fields, the interfaces,
   the ownership map, the NOT-doing list, the demo moment.
2. Run `./check.sh`. Report pass or fail with the last ten lines of output.
   A failing check is the first finding.
3. Take the diff for the range (default: commits on the current branch in the
   last four hours; accept a `git` range or branch name from the input).
   Review it against the contract for:
   - **Schema drift**: fields added, renamed or typed differently from the
     fixture; a second definition of an object that exists in `fixtures/`.
   - **Interface drift**: a call, path or signature changed without the
     contract and fixture changing first.
   - **Demo-moment drift**: work that doesn't lead to the demo moment; work
     that leads somewhere else.
   - **NOT-list creep**: anything from the NOT-doing list appearing.
   - **Ownership violations**: changes to a module by someone other than its
     owner, or to shared files without a matching contract change.
   - **Mocks**: `# MOCK` / `// MOCK` markers; after H20 each one is a finding.
4. Output a table: severity · file · what · contract section · owner · one-line
   fix. Then three lines the integration owner can read aloud at the sync.
   Then stop.

## Don't

- Don't propose new features or refactors.
- Don't review code quality beyond the contract's conventions; a hackathon
  is not a code review.
- Don't run anything other than `./check.sh` and read-only git commands.

Input from the user: $ARGUMENTS
