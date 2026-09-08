# 01 — Game plan: 36 hours, your role, the checkpoints

Read time: 15 min. Not for printing; `cheatsheets/kickoff-60.md` and
`cheatsheets/team-brief.md` are the printable versions of this doc.

## Goals, in priority order

1. **A working demo that tells a business story** both a VC partner and a
   startup operator buy. That is what wins a track or the grand prize.
2. **Meet the people**: judges, mentors, track founders, the other 245
   builders. Not a side quest. The demo and the pitch are what open those
   conversations, and the live board is how people learn your name first.
3. **Learn** how five serious startups think about their problems.

One rule for every decision in the 36 hours: *does this make the demo moment
stronger or the story clearer?* If neither, don't do it.

## Your role: CPO, business, presenter

Claim it in the first two minutes (script in `kickoff-60`). Strong developers
usually welcome someone taking this off their plate. What you own:

- **Problem and scope**: the one-liner, the demo moment, the NOT-doing list.
- **The business case**: who pays, what it saves or earns, why now. Quantified,
  with the formula visible.
- **The narrative**: the pitch starts at hour 0, not hour 30. You write the
  problem paragraph while the devs scaffold.
- **Integration and pace**: you are the one who asks every four hours,
  "if we had to present right now, what do we show?"
- **The outside**: mentors, sponsors, organisers, the CLI board, the
  submission form, the venue feed.
- **Data and ML logic** when the challenge has a modelling core (forecasting,
  anomaly detection, scoring, matching). You own the model logic; the devs
  own the plumbing around it.

What you don't own: the stack, the repo layout, code review. Don't
second-guess developers on their turf. Do challenge them on scope, every time.

The translation from your day job: weekly deep dive → impact → present to
leadership. A VC panel is leadership with a different vocabulary. A startup
judge is a senior engineer who wants to know it's real.

## The 36-hour timeline

H0 = the moment the challenges are announced. The agenda isn't published;
`hackspain watch` announces schedule changes and judging slots, so keep it
open. Adjust the hours to the real agenda on Friday, keep the order.

| Hour | Checkpoint | You |
|---|---|---|
| H0–H1 | **Kickoff** (run `kickoff-60`). Team created in the CLI, tracks registered, project draft saved, first commit, `milestone add firstCommit`. | Facilitate. Claim the role. |
| H1–H3 | **Frame.** Framing Brief agreed (playbook 02, `/hs-frame`). Devs scaffold on the starter kit. Demo laptop chosen. | Write the problem paragraph and the impact formula. Book a mentor for H3–H4. |
| H3–H12 | **Tracer bullet.** Thinnest end-to-end path: input → one step of logic → visible output. Ugly, hardcoded, fake data fine. `milestone add firstBuild` when it runs; `firstDemo` when the demo moment shows end to end. | Build the demo dataset. First venue post. Every 4h: "what do we show now?" |
| H12–H24 | **Build up**, in order of demo impact. Nothing that isn't visible in the demo moment. | `/hs-pitch draft` at ~H16 and show the team; it realigns priorities. Collect real numbers. Talk to mentors during the day. |
| **H24** | **FEATURE FREEZE.** Non-negotiable. From here: bugs on the golden path, demo data, polish of what the judge will see. | Announce it. Hold the line at H26 when someone has "a quick idea". |
| H24–H30 | **Demo engineering** (`/hs-demo`): golden path, canned inputs, recorded fallback video, 0–3 slides. Submission draft complete. | Own this end to end. |
| **H30** | **Demo locked. Submit.** Don't wait for the deadline; you can update the submission. | Check `hackspain project show` says submitted. |
| H30–H36 | **Rehearse** aloud, in Spanish, timed, three or more times. `/hs-judge panel` for Q&A. | Sleep 2–3 hours before pitching. |

Sleep: one 3–4 hour block per person, staggered, agreed at H12. The presenter
sleeps before the pitch. Nobody codes past 20 hours awake; that is when the
agent-loop bugs start and when people break main.

## Decision rules

- **Smallest demoable wedge.** If it isn't visible in the demo moment, it isn't
  a feature. It's a distraction.
- **Cut by demo impact, not sunk cost.** Two hours invested is not a reason.
- **One primary track.** A second only if the same demo serves it with zero
  extra work and you want to meet that judge anyway.
- **Real > faked > nothing.** If the real integration fights back for 30
  minutes, fake it, label it, and say so in the pitch. Judges forgive faked
  data. They don't forgive a broken demo or a hidden fake.
- **Ask a mentor after 30 minutes stuck**, not three hours. That's what they're
  there for, and every conversation is a networking touch.
- **Every four hours**: "if we present now, what do we show?" The answer is
  never "nothing".

## The `hackspain` CLI as a visibility asset

Organisers and other teams see a live board at the venue. Use it.

- **`hackspain watch`** in a spare terminal on one machine, all weekend. It puts
  the team on the live board organisers use to see who is shipping, and pushes
  announcements (lunch, judging slots, schedule changes) as desktop
  notifications. Only usage counts leave the machine, never code or prompts.
  The board and the token/commit dashboards are labelled by the organisers as
  **activity volume, not jury score**. Don't optimise for them; use them for
  visibility and announcements only.
- **Scouting is built in and fair.** `hackspain project list` and
  `hackspain team list` show every team's project name, tracks, repo URL,
  demo URL and tech stack; the feed streams everyone's commits within minutes.
  Run `project list` at H2 (is someone building your wedge in your track?
  differentiate or switch) and at H12 (who is stuck and could use help).
- **Milestones early**: `firstCommit` at H1, `firstBuild` when it runs,
  `firstDemo` when the tracer bullet works. Then custom ones as
  micro-marketing: `hackspain milestone add custom --label "First real
  invoice parsed end to end"`.
- **The venue feed**: `hackspain post "…" --image shot.png`. Four posts over
  the weekend: the one-liner at H3, the tracer-bullet screenshot at H12, the
  demo teaser at H30, thanks plus repo link after pitching. Short, concrete,
  with an image. It's how mentors and organisers know your name before you
  pitch.
- **`hackspain team repo <url>`** so GitHub pushes show on the feed, and
  **`hackspain stack set …`** with the real stack.
- **`hackspain perk list`** on Friday: partner credits (Cursor, Exa, fal.ai and
  others), claimed in-app, one code per person per perk. Decide which you use;
  you declare them in the submission. Sponsor APIs go through a proxy at
  api.hackspain.com; the site source hints at more providers behind it than
  the announced ones (unannounced as of 8 Sep, may change), so check the list
  on Friday before the devs pick a model stack.

## Submission checklist (H30)

- [ ] Project name: short, sayable, no pun that needs explaining.
- [ ] Description: two sentences, problem then what it does. Paste from the
      Framing Brief.
- [ ] Public repo with a README: what it does, how to run, one screenshot or
      GIF, what's mocked.
- [ ] Demo URL: deployed app, or the fallback video link. Video is fine.
- [ ] Tracks registered: primary, optional secondary.
- [ ] Perks declared.
- [ ] Status is **submitted**, not draft (`hackspain project show`).
- [ ] Milestones logged.

## Logistics in four lines

- Eat at the organised meals. That is where the mentors are.
- Demo machine is one laptop, chosen at H1, with everything installed by H24.
- Bring: charger, HDMI and USB-C adapters, phone battery, hotspot, printed
  cheat sheets, water, a hoodie.
- The presenter's phone has the fallback video too.
