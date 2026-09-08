# 06 — Four people, four agents, one product

Read time: 12 min. The failure this prevents: four people each driving a coding
agent locally produce four pieces that each look fine and don't fit. Two
schemas for the same object, three ways to call the model, a UI built against
an API that changed at hour nine. Agents don't drift because they're bad; they
drift because each one only sees its owner's context. The fix is not agents
talking to agents. It is giving all four the **same context** and one
automated definition of **"works"**.

## The stack is a team decision

The kit does not fix a language or a framework. The developers choose at
kickoff (minute 40–50) based on what they ship fastest in. `CONTRACT.md` has
the placeholder. Constraints that matter more than the language:

- One repo. One command runs everything locally. One command checks the
  golden path.
- The demo laptop runs it all by H24.
- Pablo's Python sandbox (`hackspain-starter/`) is where the sponsor APIs are
  already proven and where model logic can be prototyped fast. It is not the
  product stack unless the developers pick it.

## Five systems on top of four agents

### 1. The contract, and every agent reads it first

`CONTRACT.md` at the repo root (template in `templates/`): one-liner, demo
moment, the data objects with their fields, the interfaces between
components, the ownership map, the NOT-doing list, the run and check
commands. The CPO owns it. A change is a message in the team chat plus a
commit; nobody edits it silently.

The agents get it through `AGENTS.md` (read by Cursor, Codex and most
harnesses) with `CLAUDE.md` as a symlink to the same file (read by Claude
Code). One file, one set of rules, four agents with the same context.
Template in `templates/`.

### 2. Interfaces before implementation

At H2, before anyone builds their piece, write the **fixtures**: one example
of each data object as a JSON file in `fixtures/`, and the interface list
(who calls what, with what request and response) in the contract. The
schema *is* the contract, and the fixture is its canonical form. Each person
works against the fixtures with mocks marked `# MOCK`. The tracer bullet at
H12 is the first time mocks get swapped for the real thing.

The fixtures are also the demo data: one canonical set, versioned, owned by
the CPO. Nobody demos on data that isn't in `fixtures/`.

### 3. The check: the golden path as a test

`check.sh` (template in `templates/`): one command that builds, runs the
tests, and executes the golden path end to end on the fixtures, exiting
non-zero if the demo moment breaks. At H3 it just proves the app starts and
returns the fixture. It grows with the demo path.

Rules: run it before every merge to main; every agent is told to run it
before declaring done and to paste the output. Optional but cheap: the
GitHub Actions skeleton runs it on every push, and the pushes show up in the
venue feed.

### 4. Ownership and the integration owner

One module per person. Shared files (`CONTRACT.md`, `fixtures/`, `check.sh`,
`AGENTS.md`) have one owner each; changes are announced in chat first.

One developer is the **integration owner**: merges, keeps main green, runs the
check, resolves conflicts. Branch per person, merge at least every two hours;
a branch older than three hours is a risk to raise at the sync. After H20
nobody merges to main except the integration owner.

### 5. Rituals

Every four hours, a five-minute standing sync: what's on main, what's broken,
what's next, and "if we present now, what do we show?" Then the **drift
review**: the integration owner, or `/hs-check`, reads the diff since the
last sync against the contract. Same schema? Demo moment still the target?
Anything from the NOT list creeping in? Any `# MOCK` still there after H20?

## Agent hygiene for everyone's local agent

- **The contract is in the agent's context**, via `AGENTS.md`: what we're
  building, how to run and check it, where the fixtures are, the conventions,
  what not to touch, "run `./check.sh` before saying done", "small diffs, one
  change per prompt".
- **Small tasks, one module.** Agents rewriting shared code is the top source
  of drift. No agent-driven refactors of shared code after H12.
- **Commit when green, tiny commits.** Revert is the recovery tool, not debug.
- **Prompt from the contract.** "Implement X per CONTRACT.md section Y, using
  `fixtures/z.json`, don't change the schema." Same vocabulary in every prompt
  produces code that fits together.
- **One bug, one person.** Two agents "fixing" the same bug in parallel is
  how main breaks at H26.
- **Stuck 30 minutes?** The loop protocol in `team-brief.md`, then a human.

## The CPO's role in this

You don't write the product code. You own the contract, the fixtures and demo
data, and the drift review. You are the person who notices two names for the
same thing and a feature from the NOT list. At the four-hour sync you ask the
questions. You run `/hs-check` at H12, H20 and H28, or ask the integration
owner to. `/hs-check setup` copies the templates into the project repo at H1.

## Symptoms and fixes

| Symptom | Fix |
|---|---|
| Two schemas for the same object | The fixture is canonical. Fix the other one. |
| UI built against an API that changed | Interfaces frozen at H2; a change is announced and the fixture is updated first. |
| Main broken at H26 | Integration owner only. Revert first, fix second. |
| An agent rewrote a shared module | Revert. Add it to "don't touch" in `AGENTS.md`. Smaller prompts. |
| Nobody knows what's done | The sync, the check output, `hackspain milestone add custom`. |
| Four people, four coding styles | Conventions section of the contract, enforced by the linter in `check.sh`, not by review. |
