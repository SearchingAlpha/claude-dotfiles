# CONTRACT — <project name>

> Owner: <CPO>. A change is a message in team chat plus a commit. Every agent
> reads this file first (see `AGENTS.md`). If a task conflicts with it, stop
> and say so.

## What we're building

- **One-liner:** <Company> wants <who> to <outcome> without <pain>.
- **Demo moment:** The judge <does X>, and within <N s> sees <Y>, which today
  takes <Z>.
- **Track(s):** <primary> (+ <secondary, only if zero extra work>)

## Stack — team decision at kickoff

- **Language / framework:** [decided by the developers at kickoff]
- **Run everything locally:** `<command>`
- **Check the golden path:** `./check.sh`
- **Demo laptop:** <name>
- **Integration owner (merges, keeps main green):** <name>

## Data objects — the schema is the contract

`fixtures/<object>.json` is the canonical example of each object. Add or
rename a field only by updating the fixture first and announcing it.

| Object | Fields (name: type) | Fixture | Owner |
|---|---|---|---|
| <Object> | <field: type>, <field: type> | `fixtures/<object>.json` | <name> |

## Interfaces

| From → To | Call | Request | Response | Owner |
|---|---|---|---|---|
| <UI → API> | `<verb path or function>` | `<fixture>` | `<fixture>` | <name> |

## Ownership map

| Module / area | Owner | Others may |
|---|---|---|
| <module> | <name> | read; ask before changing |
| `CONTRACT.md`, `fixtures/`, demo data | <CPO> | propose in chat |
| `check.sh`, `AGENTS.md`, CI | <integration owner> | propose in chat |

## NOT doing

1. <cut>
2. <cut>
3. <cut>
4. <cut>
5. <cut>

## Conventions

- Naming: <e.g. snake_case for data fields everywhere, including the UI>
- Errors: <e.g. every external call has a timeout and a fallback to the fixture>
- Mocks: mark with `# MOCK` (or `// MOCK`) so we can grep them at H20.
- Commits: one line, imperative, small. Never commit a failing `./check.sh`.

## Timeline

H1 team on the board · H2 fixtures and interfaces frozen · H12 tracer bullet
demo · H24 feature freeze · H30 demo locked and submitted · H30–36 rehearse.
