# 02 — Framing the challenge (hours 1–3)

Read time: 15 min. Purpose: get from "here is the challenge text" to a
one-line pitch and a tracer-bullet spec in under three hours, for any of the
five startups. `/hs-frame` walks the team through this with the challenge text
pasted in.

## Step 1 — Read it three times, then say it in one line

Silently, each person. Then each writes:

> **[Company] wants [who] to be able to [outcome] without [pain].**

Compare. Disagreement here costs five minutes now and saves an hour at H10.
While reading, underline four things: the **verb** (automate, detect,
forecast, reconcile, route, verify), the **user** (an ops agent, a treasurer,
a clinic admin, a robot operator), the **data** (given dataset, API, public
data, or synthetic), and the **constraint** they stress (auditability,
latency, accuracy, integration, cost).

## Step 2 — The jobs-to-be-done one-liner

> When **[situation]**, **[who]** wants to **[motivation]**, so they can
> **[outcome]**. Today they **[workaround]**, which costs **[time / money /
> risk]**.

Example, Embat-flavoured: *When month-end closes, a treasury analyst at a
500-person manufacturer wants tomorrow's cash position across 12 banks, so
they can decide what to pay. Today they export CSVs into a spreadsheet, which
takes six hours and is wrong by Wednesday.*

This sentence is your problem slide and your first 30 seconds on stage.

## Step 3 — Diverge: five to seven ideas in 15 minutes

One line each, no discussion, no "how". Force variety with these prompts:

- **The obvious automation** of the workflow in the challenge.
- **The control tower**: a view of the live state plus an agent that acts on it.
- **The exception handler**: only touch the 5% the current process fails on.
- **The copilot**: the agent drafts, the human approves. Lowest risk, easy demo.
- **The data product**: a model, score or forecast others plug into. Your
  home turf.
- **The wedge from your domain**: what would a supply-chain forecaster build
  here?
- **The contrarian**: what would this startup not expect a team to build?

## Step 4 — Score with the matrix

Score each idea 1–5 on four axes. Kill anything scoring 1 or 2 on
*Demoable*. Dot-vote the rest. Decide by H2; you propose, the team confirms.

| Axis | The question |
|---|---|
| **Impact** | Does it move a number this company or its customer visibly cares about? |
| **Demoable in 30h** | Can we show the aha moment end to end with what we have (data, APIs, skills)? |
| **Track fit** | Would the judge from this startup say "that is our world"? |
| **Team fit** | Does it use what we're strong at, including data/ML and narrative? |

## Step 5 — Define the demo moment

The single 20-second moment where the judge goes "oh". Write it as one
sentence:

> **The judge [does X], and within [N seconds] sees [Y], which today takes
> [Z].**

Everything you build either leads to that moment or doesn't get built. If the
team can't write this sentence, the idea isn't ready.

## Step 6 — Smallest demoable wedge and the tracer bullet

The tracer bullet is the thinnest end-to-end path: one input, one step of
logic, one visible output. Ugly, hardcoded, one happy path. Spec it:

- **Input**: one concrete example (a file, a message, a query).
- **Processing**: one step (an LLM call, a model, a rule).
- **Output**: what appears on screen.
- **Faked for now**: everything mocked, listed honestly.
- **Real by H24**: what must be real for the demo to be credible.

Then the **NOT-doing list**: five things you're explicitly cutting. Write them
down. At H18 someone will propose number three.

## Step 7 — Quantified impact (this is your edge)

Back of the envelope, transparent, assumptions stated:

> **volume × time or cost per unit × share you affect = impact per year**

Example: *400 invoices a day × 4 minutes of manual matching × €35/hour × 60%
automated ≈ €245k a year per customer.* Sources: the challenge text, the
company's public numbers (playbook 03), your own judgment labelled as an
assumption. A number the judge can argue with beats no number every time.
Say "roughly" and show the formula.

## Step 8 — Why now, why this company, why us

- **Why now**: what changed. Model capability, a regulation, a cost curve, the
  company's own stage.
- **Why this company**: where it plugs into their product loop (playbook 03).
  Use their vocabulary.
- **Why us**: the team's real edge in one sentence each. Yours: shipped an AI
  agent to production, runs supply-chain operations at scale, forecasting and
  data.

## Duplicate check at H2

Run `hackspain project list`. Every team's project name, tracks and stack are
visible to everyone by design. If another team is building your wedge in your
track, either differentiate (a different persona, a different demo moment) or
switch to your second-scored idea now. Two teams with the same demo split the
judge's attention; the one with the sharper business story wins.

## Track-entry rule

One primary track. Add a second only if the same demo genuinely serves it
(an agent that reconciles payments could plausibly enter Embat and Maisa)
and you want to meet that judge anyway. Never build extra for a second
track. `hackspain track register <slug>`; you can `move` later.

## Output: the Framing Brief

Paste this in the team chat and pin it.

```
FRAMING BRIEF — <project name>
Track: <primary> (+ <secondary, if any>)
One-liner: <Company> wants <who> to <outcome> without <pain>.
JTBD: When <situation>, <who> wants to <motivation>, so <outcome>.
      Today <workaround>, which costs <cost>.
Demo moment: The judge <does X>, and within <N s> sees <Y>, which today takes <Z>.
Tracer bullet: <input> → <processing> → <output>
      Faked for now: ...   Real by H24: ...
Impact (rough): <formula> ≈ <number>/yr. Assumptions: ...
Why now: ...   Why <company>: ...   Why us: ...
NOT doing: 1. ... 2. ... 3. ... 4. ... 5. ...
Roles: <name> — ..., <name> — ...
Demo laptop: <name>
```

## Stress test (optional, 10 minutes)

Run the `grilling` skill on the brief, or ask a mentor one question: "What
would make this a no from you?" Fix the framing, not the feature list.
