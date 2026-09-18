# HackSpain 2026 prep kit — index

Everything a product-business-presenter needs for a 36-hour, VC-judged,
startup-challenge hackathon, plus the networking plan around it. English.
Total read: about 90 minutes. Event facts as of 8 Sep 2026; the live site
(hackspain.com) wins if anything differs.

## Read in this order

| Doc | Minutes | What it gives you |
|---|---|---|
| `01-game-plan.md` | 15 | Goals, your role, the 36h timeline with checkpoints, decision rules, the `hackspain` CLI as a visibility asset, submission checklist. |
| `02-challenge-framing.md` | 15 | Hours 1–3: from challenge text to a pinned Framing Brief. JTBD, scoring matrix, demo moment, tracer bullet, quantified impact. |
| `embat-xray-research.md` | 20 | Embat "X Ray" challenge condensed, dataset = Embat API schema, prior art (cash-flow underwriting, payment behaviour, trajectory, explainability, monitoring), evaluation protocol, product comparables. |
| `03-tracks-dossier.md` | 25 | One section per track startup: product loop, what they care about, likely challenge themes, what "great" looks like, Q&A landmines, careers pages. |
| `04-pitch-and-demo.md` | 15 | The three-minute skeleton for VC and startup judges, demo engineering with a fallback ladder, the four-step Q&A protocol, likely questions. |
| `05-networking.md` | 15 | Before, during, after. Target list by group, the ask ladder, openers, co-founder fit, follow-up cadence. |
| `06-team-coherence.md` | 12 | Four people, four local agents, one product: the contract every agent reads, fixtures as schema, the check script, integration owner, four-hour syncs. |
| `me.md` | 5 | Your intros, story, answer bank, HappyRobot approach. Personal. |

## Print these (one A4 page each)

- `cheatsheets/kickoff-60.md`: the hour-0 facilitator card. Read it on the way in.
- `cheatsheets/team-brief.md`: send to teammates before Friday; five-minute phone read.
- `cheatsheets/pitch-card.md`: by the laptop from H30.
- `cheatsheets/people-card.md`: names, hooks and openers for judges and mentors.

Render to PDF with `python3 cheatsheets/render.py` (markdown-it-py plus a
local Chrome; writes `cheatsheets/pdf/*.pdf` and prints the page count). All
four fit one portrait A4 page as of 8 Sep 2026. If a sheet spills past one
page, cut text until it doesn't. `npx md-to-pdf` also works but pulls a
Chromium download.

## Templates for the project repo

`templates/`: `CONTRACT.md`, `AGENTS.md` (symlink `CLAUDE.md` to it),
`check.sh`, `github-workflow-check.yml`. `/hs-check setup` copies them into
the team's repo at H1 and fills what the Framing Brief already knows. The
stack line stays a placeholder until the developers decide at kickoff.

## Sharing with the team

This repo stays private. `python3 hackspain-starter/export-team-kit.py` builds
a curated team edition in `~/hackspain26-team-kit/` (playbooks 01–04 and 06,
cheat sheets, templates, commands except `/hs-contact`, public people notes,
the sandbox) with personal sentences rewritten and `me.md`, `contacts.md` and
`05-networking.md` left out. Re-run after changes, then commit and push in
that repo. Teammates' PRs land there and get ported back here by hand.

## Worked example

`examples/framing-brief-sample.md`: what `/hs-frame` produced on a fictional
Embat-style challenge during a pre-event dry run. Read it once to calibrate
the density a good brief has.

## Slash commands for live use

Installed by `./install.sh` at the repo root (symlinks `commands/*.md` into
`~/.claude/commands/`). Each reads its playbook first and does one job.

| Command | When | Job |
|---|---|---|
| `/hs-frame <challenge text>` | H1–H3 | Framing Brief from the challenge text. |
| `/hs-demo <what works + demo moment>` | H24–H30 | Demo script with fallback ladder. |
| `/hs-pitch draft` · `/hs-pitch tighten <script>` | H16 and H30 | English draft + Spanish delivery script with timings. |
| `/hs-judge panel <track>` · `/hs-judge coffee` | Pre-event and H30–H36 | Mock Q&A scoring the four-step protocol. |
| `/hs-contact <name, company, what we discussed>` | Every night | Log a person, draft the 48h and two-week follow-ups into `contacts.md`. |
| `/hs-check setup` · `/hs-check review` | H1, then H12 / H20 / H28 | Copy the coherence templates into the project repo; later, run the check and review the diff for drift against the contract. |

## Starter code: a sandbox, not the stack

The Python scaffold one level up (`agent.py`, `llm.py`, `search.py`,
`media.py`, `app.py`) wires Claude, Exa and fal.ai with a Streamlit front end.
It exists so the sponsor APIs are proven before Friday and so model logic can
be prototyped fast. **The product stack is the developers' decision at
kickoff** (playbook 06). Run the four smoke tests before Friday; see
`../README.md`.
