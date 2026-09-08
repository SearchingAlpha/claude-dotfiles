# 04 — Pitch and demo

Read time: 15 min. Draft in English for logic, deliver in Spanish. Assume
three minutes plus Q&A unless announced; `hackspain watch` will carry the
judging-slot announcement. `/hs-pitch` drafts and tightens the script,
`/hs-demo` builds the demo script, `/hs-judge` drills the Q&A.

## Two audiences, one story

The grand-prize panel is five investors from Spanish VC funds (two partners, a founding partner, a senior investor and an analyst). The track panels include operators
from the startup itself. Each slot of the pitch has to land with both.

| Slot | The VC partner listens for | The startup judge listens for |
|---|---|---|
| Problem | Real, big, painful? Who pays? | Do they understand our customer's workflow? |
| Who | A specific buyer, not "companies" | The right persona: ops agent, treasurer, clinic admin |
| Why now | Timing, what changed | Why this wasn't possible or done before |
| What we built | Is there a product here? | Is it real? What's under the hood? |
| Demo moment | Do I get it in 20 seconds? | Does it survive the messy case? |
| How it works | Data, defensibility | Architecture choices, tradeoffs, honesty about what's faked |
| Why us | Can this team execute? | Would I hire them? |
| Close | What's next, what would €5k or a pilot do? | Would this plug into our product? |

## The three-minute skeleton

| Time | Slot | What you do |
|---|---|---|
| 0:00–0:30 | **Problem + who** | Open with the person and the pain. One number. Use the company's vocabulary. |
| 0:30–0:45 | **Why now** | One sentence. What changed. |
| 0:45–1:00 | **What we built** | "So we built [name]: a [type] that [does Y] for [who]." Product name, one sentence. |
| 1:00–2:15 | **DEMO** | Show, don't tell. Narrate where to look. Hit the demo moment by 1:40. |
| 2:15–2:35 | **How it works + what's real** | Architecture in one breath. Say what's mocked. Honesty here is credibility. |
| 2:35–2:50 | **Impact + why us** | The number and the gist of the formula. The team's edge in one line. |
| 2:50–3:00 | **Close** | What happens next with a pilot, or what you'd build with more time. One line they remember. |

Over time? Cut "how it works" first, then "why now". Never cut the demo.

## Writing rules

- One idea per sentence. Concrete nouns. Ban: leverage, seamless,
  revolutionary, platform, disrupt, AI-powered.
- One number per slot at most, and say the assumption behind it.
- Name the persona. Use the startup's own words for their domain (playbook 03).
- **Spanish delivery**: write the English draft for logic, then rewrite in
  Spanish the way you'd say it to a colleague. Not a translation. Shorter
  sentences. Memorise the first and last sentence until they're automatic.
- **Slides: zero to three.** Title with the one-liner; the problem with the
  number; an architecture slide for Q&A. The demo is the deck.

## Demo engineering

**Golden path.** One scripted sequence of inputs and clicks, rehearsed five or
more times, with canned inputs that always work: saved files, fixed prompts,
seeded data. Never type free text on stage.

**Pre-warm.** App running and logged in, caches hot, model warm from a request
made two minutes before, browser at 125% zoom, notifications and screen lock
off, fallback video open in a second tab, hotspot ready.

**Fallback ladder.** Decide the triggers beforehand.

1. Live.
2. Live with cached responses. Trigger: any call over five seconds, or one error.
3. Recorded video, 60–75 seconds, recorded at H28 with no voice track so you
   narrate live. Trigger: presenter counts to three in their head and nothing
   happened.
4. Screenshots in slides. Trigger: the laptop dies.

The switch line: "Os enseño la ejecución grabada." Then keep talking. No
debugging on stage, ever. A team that switches calmly to a video looks
prepared; a team that fixes a bug on stage looks like a bug.

**Driver and narrator.** One developer drives, you narrate. Rehearse the
handoffs: "Ahora Ana sube la factura…" The narrator never touches the
keyboard.

**Pre-flight at H35.** Charger, adapters tested on the venue projector if at
all possible, venue wifi plus hotspot, video local on laptop and phone,
script printed, water.

## Q&A: the four-step think-aloud protocol

This exists because open-ended questions once froze you in an interview. The
fix is a script for thinking out loud, not more knowledge. Say each step out
loud, every time, until it's automatic.

1. **Reframe and narrow.** "Si entiendo bien, preguntas si X se sostiene para
   Y. Empiezo por Z." Buys 20 seconds and shows control.
2. **Structure before solving.** "Lo veo en tres partes: A, B, C." Name the
   axes before diving into any.
3. **Prioritise and state assumptions.** "Empezaría por A porque es lo que
   más mueve. Asumo que [X]; si no fuera así, cambiaría a B." Stated
   assumptions are exactly the signal FDE and deployment roles hire for.
4. **Close with a next step.** "Así que el primer paso sería X, y lo
   validaría con Y." Never leave the answer hanging.

Golden rules: thinking silence is fine, blank silence is the only failure;
narrate the process even without an answer; there is no single right answer,
they grade the structure; anchor any question in your domain with an analogy
from supply chain, forecasting or ops; you may ask a clarifying question back.

## Likely questions, with 15-second answers prepared

**From VCs.** How big is this, who pays and how much? Why wouldn't the
startup or an incumbent build it themselves? What's defensible? What would you
do with the €5k or the next three months? Why this team?

**From startup judges.** What's real and what's mocked? How does it handle
the messy case (bad OCR, ambiguous voice, missing data, a robot that
misgrips)? How would it integrate with our product? What's the failure mode
and how would a user notice? Which model and why, and what does it cost at
scale? What surprised you about our domain?

"We didn't get to that; here is how we'd do it" is a good answer. A vague
yes is not.

## Rehearsal plan (H30–H36)

Three full runs aloud, timed, in Spanish. One with a developer as a hostile
judge, using `/hs-judge panel` for the questions. Record one run on a phone
and watch it once. Fix the opening and closing sentences first; the middle
can flex.
