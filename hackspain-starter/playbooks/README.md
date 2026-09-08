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
| `03-tracks-dossier.md` | 25 | One section per track startup: product loop, what they care about, likely challenge themes, what "great" looks like, Q&A landmines, careers pages. |
| `04-pitch-and-demo.md` | 15 | The three-minute skeleton for VC and startup judges, demo engineering with a fallback ladder, the four-step Q&A protocol, likely questions. |
| `05-networking.md` | 15 | Before, during, after. Target list by group, the ask ladder, openers, co-founder fit, follow-up cadence. |
| `me.md` | 5 | Your intros, story, answer bank, HappyRobot approach. Personal. |

## Print these (one A4 page each)

- `cheatsheets/kickoff-60.md`: the hour-0 facilitator card. Read it on the way in.
- `cheatsheets/team-brief.md`: send to teammates before Friday; five-minute phone read.
- `cheatsheets/pitch-card.md`: by the laptop from H30.
- `cheatsheets/people-card.md`: names, hooks and openers for judges and mentors.

Render to PDF with `npx md-to-pdf <file>` or the VS Code "Markdown PDF"
extension. If a sheet spills past one page, cut until it doesn't.

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

## Starter code

The Python scaffold one level up (`agent.py`, `llm.py`, `search.py`,
`media.py`, `app.py`) wires Claude, Exa and fal.ai with a Streamlit front end.
Run the four smoke tests before Friday; see `../README.md`.
