# 03 — Tracks dossier: the five startups

Read time: 25 min. One section per track startup, in the order the site
lists them. Facts as of 8 Sep 2026 with sources at the end of each section;
anything not verifiable is tagged `[unverified]`. Challenge statements are not
published; the "likely challenge themes" are hypotheses grounded in each
company's product, nothing more. `/hs-frame` and `/hs-pitch` read this file.

## The pattern across all five

Read the sections and one thing repeats. Every one of these companies sells
**automation of a high-stakes operational workflow to an enterprise buyer**,
and every one has learned the same lesson in public:

1. **LLM for interpretation, deterministic code for action.** The model reads
   the messy input; rules, workflows and validation decide what actually
   happens. HappyRobot calls it "the deterministic layer beneath every agent";
   Embat says "let the model interpret intent, and let deterministic code do
   the work"; Maisa's whole pitch is auditable agents.
2. **Auditability and reversibility are the product**, not a feature. Run
   logs, transcripts, tool calls, undo. A judge from any of them will ask
   "what happens when the model is wrong?"
3. **One measured business metric**, declared in advance: autonomous
   resolution rate, acceptance rate, hours returned, cycle time. Not "it
   works".
4. **Integration with the system of record** (TMS, ERP, EHR, PLC) is where
   the value is and where the hard part is. A demo that writes back
   somewhere beats a demo that only reads.
5. **Exception-driven UX.** The human sees only what needs judgment, with the
   reasoning attached.

So whatever the challenge says, a project that shows an end-to-end action, a
deterministic guardrail, an audit trail, one measured number and a clean
exception queue will look like it was built by people who understand the
customer. That is the bar for a startup judge. For the VC judges, add who
pays and why now.

## How to use this file during the event

- H0–H1: read only the section for the track you are leaning towards. Five
  minutes.
- H1–H3: the "likely challenge themes" and "what great looks like" bullets
  feed the scoring matrix in playbook 02.
- H16 and H30: the vocabulary and "what they care about" quotes feed the
  pitch; the landmines feed `/hs-judge`.
- Networking: the people and careers lines are your openers and your warm
  application path.

## Claves confirmadas desde el repositorio oficial (HackSpain/hackspain26)

- **Tracks confirmados y retirados:** El backend Convex (`tracks.ts`) retira explícitamente cualquier track genérico (`const RETIRED_SLUGS = ["ml", "non-tech"]`). Los **únicos 5 tracks** son Maisa, HappyRobot, Prosper AI, Embat y THEKER.
- **Nuevos jueces añadidos al panel general:**
  - Marta Santiago (Autonomous Alliance, Inc)
  - Xabier Irizar (Canopy Labs)
  - Alejandro Cañada Hinojosa (Sperid Labs)
  - Se suman a Quili Peña (HappyRobot), Alejandro Buján (Base10), Adrià Blancafort / Juan Vecino / Pablo Menéndez (Harbor), Félix Gil (Krea), Nicolás de Ory (Caspian) y María Muñoz (Invoke.bio).
- **Panel VCs Gran Premio (5.000 €):** Jaime Novoa (Kfund), Iván Landabaso (JME), Miguel González (Acurio), Iván Fernández (Enzo), Ignacio Alfeirán (Kibo).
- **Sponsors de Compute / Partners oficiales (`llms.txt`):** Google, Cognition (Devin), Cursor, fal.ai, UPM, Exponential. Google y Gemini tienen presencia destacada; el starter kit soporta Gemini y proxies vía `OPENAI_API_KEY` / `GEMINI_API_KEY`.
- **Telemetría oficial (`hackspain watch`):** Monitorea logs locales de `claude-code`, `codex`, `opencode` y `cline`. Los hitos oficiales validados en base de datos son: `firstCommit`, `firstBuild`, `firstDemo` y `custom` (--label <= 120 caracteres).

---
## Maisa — "AI agents with traceability for the enterprise"

**What they sell.** "Digital Workers": AI agents that run entire high-volume,
multi-system back-office processes (loan origination, KYC, claims intake,
invoice reconciliation, trade finance) with code-backed, auditable traces, for
regulated enterprises in banking, insurance, energy, infrastructure and
manufacturing. Founded 2024, HQ Valencia and San Francisco, Barcelona office
since Jul 2026, about 100 people.

**Product loop.**
- A business user describes the process, policies and acceptance criteria in
  natural language in **Maisa Studio**, attaches reference documents and
  connects systems ("300+ integrations").
- The Digital Worker runs on the **KPU (Knowledge Processing Unit)**, which
  they describe as "a deterministic system designed to limit hallucinations".
  For each case it writes specific code for that action, checks results
  against rules, and iterates. Discrepancies are flagged and routed by policy.
- Every step is logged as the **Chain of Work**: task, tools and sources,
  returned data, validations run, pass or fail. Their claim: "Same input,
  same configuration, same output."
- Output: a decision plus evidence for ops teams and auditors. Traces become
  customer-owned IP that can train a small model to cut cost.

**Scale.** Grupo Santander (Chief AI Officer testimonial, joint talk at
Revolution Banking 2026), Indigo, Elecnor. Production across banking,
insurance, energy, manufacturing, mobility. Named in 15+ Gartner reports.
Marketing figures on the site ("150x lower cost than AI agents", "10x
first-year ROI", 60-day pilot to production) are unsourced.

**Funding.** $25M **seed** (TechCrunch, 27 Aug 2025), **led by Creandum**,
with Forgepoint Capital International (its JV with Santander) participating.
The HackSpain site's "led by Creandum and Forgepoint" overstates Forgepoint's
role. Earlier: $5M pre-seed (Dec 2024) led by NFX and Village Global. Total
about $30M. The "95% of enterprise AI projects fail" line comes from
TechCrunch's headline citing an MIT NANDA report, not from Maisa's own site.

**People.** David Villalón, co-founder and CEO (previously at Clibrain with
Romero; Valencian). Manuel Romero, co-founder and Chief Scientific Officer,
"principal architect" of Chain of Work; prolific open-source ML contributor
as Hugging Face `mrm8488` (hundreds of models; Narrativa, Clibrain).

**What they say they care about.**
- Traceability: Chain of Work is "a structured, traceable log that records
  every step of AI execution".
- Determinism over probability: "The problem is never the hallucination
  itself. It's treating a guess as a fact."
- Model independence: "the work lives in your traces, not the model".
- ROI discipline, learned at Santander: every use case must show "revenue
  increase, cost reduction, or risk mitigation".
- "We started Maisa because trust was missing from AI."

**Your angle.** You shipped an agent to production and know that the hard part
is evaluation, guardrails and the audit trail, not the model. Romero's
open-source background means a technical, honest conversation lands. Their
open roles are consulting and deployment (AI Automation Consultant,
Solutions Consultant, Technical Account Manager), which is FDE work by
another name.

**Likely challenge themes (hypotheses).**
1. A Digital Worker for a document-heavy regulated process (KYC, claims
   intake, invoice reconciliation) that emits a Chain-of-Work-style audit log
   and replays deterministically.
2. A verification layer that cross-references every output to source
   documents, flags inconsistencies and escalates: hallucination resistance
   as a product feature.
3. Cost and ROI observability: route steps to the cheapest adequate model,
   report per-case cost and accuracy, or distil traces into a small model.

**What "great" looks like to them.**
- Deterministic replay and per-step evidence links a compliance officer can
  click through; explicit exception paths (retry, escalate, notify).
- Honest evals against a human baseline (their Santander UK soft launch cited
  "over 90%" match), and a business-user onboarding flow in plain language.
- Framing in revenue, cost and risk terms with per-case unit cost at scale.
  Their own example: 1% error on 50,000 trade-finance documents is 500 bad
  documents.

**Q&A landmines.** Run it twice: is the output identical, and if not, why?
Show me the trace for a wrong answer; where did it fail? How is this not n8n
or RPA plus an LLM? (They wrote a blog post on exactly that.) What does one
case cost at 50,000 a month?

**Careers.** maisa.ai/careers lists nothing; LinkedIn lists nine roles
(Valencia, Madrid, Barcelona): AI Automation Consultant, Solutions Consultant,
Technical Account Manager, Backend Engineers (Go/Python on Kubernetes).

**Sources.** maisa.ai (product, landings/chain-of-work,
landings/ai-hallucinations, landings/digital-workers,
blog/how-maisa-digital-workers-actually-work, blog/maisa-x-santander,
careers) · techcrunch.com 2025/08/27 "Maisa AI gets $25M…" ·
linkedin.com/company/maisa-agentic-process-automation ·
huggingface.co/mrm8488.
## HappyRobot — "the AI operating system of the real economy"

**What they sell.** "AI workers" that execute complete operational tasks over
voice, email, chat and SMS inside enterprise systems, for large logistics,
industrial, utilities, telecom, insurance and finance operators. Freight was
the wedge. Founded 2022, YC S23, HQ San Francisco, large teams in Madrid and
Barcelona.

**Product loop.**
- A call, email or message hits the gateway; the voice pipeline is VAD → ASR
  fine-tuned on supply-chain jargon → LLM → TTS, with an end-of-turn model
  they describe as the hard problem.
- A **deterministic workflow engine** sequences agent nodes, conditionals,
  loops and tool calls, built in a low-code editor or via API and TypeScript
  SDK. Their phrase: "the deterministic layer beneath every agent".
- The agent negotiates or collects data and **writes back to the TMS, ERP or
  CRM** in real time (Circle Logistics case: Transport Pro, DAT, Truckstop).
- A **Post-Call Auditor** (LLM + classical ML + rules) scores every
  interaction on latency, tool-selection accuracy, autonomous resolution and
  business outcome; A/B tests pick winners on one pre-declared metric.
- Humans handle the escalated exceptions. Everything feeds a shared
  "Context" layer.

**Scale.** Company claims: 150+ enterprises, 10M+ interactions a month, 70%+
autonomous resolution. Named customers: DHL, Kuehne+Nagel (78% of connected
calls handled end to end), Naturgy, Repsol, Uber, WWEX, MODE Global, Circle
Logistics (18% of freight booked with zero human touch).

**Funding.** Series C $150M at $1.2B post-money, announced 4 Aug 2026, led by
Prysm Capital, co-led by Eurazeo, with a16z, Base10, YC, Kfund and strategics
(Koch, Orange, Bankinter). Roughly $200M raised in total. Kfund's Jaime Novoa
is a grand-prize judge here; Base10's Alejandro Buján is a general judge.

**People.** Pablo Palafox, CEO (ex computer-vision PhD candidate at TU Munich,
Meta Reality Labs). Luis Paarup, CTO (TUM mechanical engineering; robotics
background). Javier Palafox, COO (ex-CFO of a $300M CPG brand; ran Deoleo's
North American logistics). Carlos Becker, founding forward-deployed engineer.
**Judge: Quili Peña**, listed by HackSpain as HappyRobot; title `[unverified]`.

**What they say they care about.**
- Determinism where creativity is a liability: "as reliable as a script and as
  capable as a human".
- Measured outcomes over vibes: "You designate one primary metric in advance."
  ROI multiples on every case study.
- Context comes from deployment: "AI rarely fails because of models alone. It
  fails because of context." Their FDE post is titled "Past, present and
  future of the forward deployed engineer at HappyRobot". Read it.
- Voice latency and end-of-turn detection; SOC 2, ISO 27001, HIPAA, EU AI Act.
- Values: extreme ownership, craftsmanship, "we are majos".

**Your angle.** Freight operations is your day job. You know what a carrier
check-call, an appointment reschedule and an exception queue look like from
the inside, and you've shipped an agent to production. They are hiring
Forward Deployed Engineers and Deployment Strategists in Madrid right now.
Talk deployment reality, not models.

**Likely challenge themes (hypotheses).**
1. A voice or email agent that completes one operational loop (check-call,
   appointment, collections) with structured write-back to a mock TMS and a
   human escalation path.
2. An evaluation or auditing layer: score transcripts on tool selection,
   resolution and compliance, or an A/B harness tied to a business metric.
3. A hybrid workflow: the agent talks, deterministic rules enforce policy
   (offer limits, mandatory disclosures), with full run traceability.

**What "great" looks like to them.**
- Real end-to-end action that changes state somewhere and leaves an auditable
  trail (run log, transcript, tool calls). Not a chatbot.
- Explicit reliability engineering: deterministic guardrails, retries,
  escalation, a latency number, and one metric you actually measured.
- Their vocabulary: hours automated, autonomous-resolution rate, ROI multiple,
  weeks to go live, the 99% path versus the edge cases.

**Q&A landmines.** Where is the deterministic boundary and how does a human
take over when the model is wrong? What was your primary metric and sample
size? How does it integrate with existing systems, and what's the latency on
a live call?

**Careers.** happyrobot.ai/careers (73 open roles as of Sep 2026). FDE Madrid:
jobs.ashbyhq.com/happyrobot.ai/4fcffc26-ea08-41e2-b85d-9f076d97419c.
Deployment Strategist Spain: jobs.ashbyhq.com/happyrobot.ai/fe931942-eee2-4c25-8d1f-7215a87d622d.

**Sources.** happyrobot.ai (home, careers, technical-overview,
inside-happyrobots-workflow-engine, how-ab-tests-compound-agent-intelligence,
the-agentic-and-deterministic-hybrid-enterprises-need,
past-present-and-future-of-the-forward-deployed-engineer-at-happyrobot,
happyrobot-seriesc-fundraising-announcement, customer-story/kuehne-nagel,
circle-logistics case study) · ycombinator.com/companies/happyrobot ·
a16z.com/announcement/investing-in-happyrobot · HackSpain judges.ts.
## Prosper AI — "AI for healthcare operations"

**What they sell.** AI voice agents that handle both patient and insurer
phone calls to run the administrative patient journey for US outpatient
groups, health systems and EHR/RCM partners: scheduling, benefits and
insurance verification, prior authorisation, claim follow-up, patient
billing. Founded 2023, YC S23, HQ New York, engineering in Barcelona and
Madrid.

**Product loop.**
- Trigger: an inbound patient call, or an outbound campaign fired by an EHR
  event, a waitlist backfill or a reminder.
- A "Gen 3" voice agent authenticates the caller, interprets intent across
  25+ specialties, and reads and writes the EHR (80+ integrations:
  athenahealth, ModMed, Veradigm, eClinicalWorks, ImagineSoftware). Benefits
  are verified by API ("80% resolved instantly") or the agent "dials the
  insurer, waits on hold, and gets the answer".
- Outcome written back (booking, payment posted, auth status) or a warm
  handoff to staff with context. "Every call is automatically reviewed by AI
  for accuracy, compliance, and quality."
- Post-call transcript, extracted data and QA results exposed via REST API
  and webhooks; consumed by front desk, RCM teams and partner platforms.

**Scale.** "150,000+ providers" (this is the "150k physicians" figure), 40+
organisations added in six months, 50+ outpatient groups, 5x revenue in six
months. Named: Jackson Memorial Hospital, athenahealth, ImagineSoftware,
Firstsource, Northeast OB/GYN (50% of scheduling calls automated), The 44
Group (600+ physicians).

**Funding.** $30M Series A, 22 Jun 2026, **led by a16z** (Jay Rughani), with
Base10 and existing Emergence Capital, YC, Company Ventures, plus Spanish
funds Masía and Clara. Seed $5M led by Emergence (Sep 2025). Total $35M.
About €10.5M earmarked for a Barcelona engineering hub, 30 people growing to
100+ in 2027.

**People.** Xavier de Gracia, co-founder and co-CEO: Harvard MBA, ESADE,
ex-Bain, ran a $120M business unit with 800+ contact-centre agents, two
physician parents. Josep Marc Mingot, co-founder and co-CEO: ex MIT computer
vision research, product leader at CoverWallet (acquired by Aon), scaled the
PM org from 3 to 40+.

**What they say they care about.**
- Reliability defined as "compliance integrity, conversational accuracy, and
  system uptime".
- HIPAA and a BAA as "the floor, not the ceiling"; SOC 2 Type II.
- Write-back, not read-only: "The call got handled; the outcome didn't get
  recorded."
- Hallucination gating: "right-gates" cut EHR-write hallucinations "from 10%
  to 1% in production"; turn latency "below 200 milliseconds".
- End-to-end resolution over scheduling only: "60%+ of total inbound calls
  end-to-end"; "One platform replacing three vendors". Mission framed against
  $450B of US healthcare admin cost.

**Your angle.** A contact-centre operation is a queue with service levels,
forecasted demand and exception handling. That is operations research you do
for a living. De Gracia ran 800 agents; talk to him in those terms. They are
hiring a Forward Deployed Engineer in Barcelona whose job is EHR and payor
integrations.

**Likely challenge themes (hypotheses).**
1. A voice agent for an under-served call type (prior-auth status, claim
   status via an insurer IVR, refill status) with tool calling and mock FHIR
   write-back.
2. Reliability tooling: a hallucination detector for EHR writes, per-call QA
   scoring against criteria, a drift dashboard from call logs.
3. Payor-side automation: navigate IVR trees and extract structured benefits,
   or an outbound no-show backfill campaign on their campaigns API.

**What "great" looks like to them.**
- A booking confirmed only after the EHR write succeeds; graceful handling of
  an API timeout mid-call; warm handoff with context; explicit escalation
  triggers.
- Sub-200 ms turn latency, no PHI in logs, an architecture story that a
  compliance officer would sign.
- Metrics the way they judge: end-to-end resolution against the full call
  mix, transfer-back rate, staff hours or dollars collected.

**Q&A landmines.** What happens if the EHR API times out mid-call? Prove the
agent didn't invent a booking. Where does the audio and the PHI go?
Scheduling is only 40–50% of inbound calls; what about the rest?

**Careers.** getprosper.ai/careers (Ashby). Forward Deployed Engineer,
Barcelona: jobs.ashbyhq.com/prosper-ai/7784d5bc-5f72-4177-885e-10e2bb1e56e9.
Senior AI Engineer, Barcelona: jobs.ashbyhq.com/prosper-ai/29fbeb7b-5673-498f-8333-02a7e0ce61d1.
AI Product Manager, Barcelona/NYC: jobs.ashbyhq.com/prosper-ai/d1f79995-3883-4e3f-8a85-fde5db2dc09e.

**Sources.** getprosper.ai (company, press, blog/manifesto,
blog/reliable-voice-ai-agent-healthcare, Series A announcement) ·
docs.getprosperapp.com · ycombinator.com/companies/prosper ·
hitconsultant.net 2026/06/23 · siliconangle.com 2026/06/22 · emprendedores.es
Prosper AI round · linkedin.com/company/prospertechnologies.
## Embat — "the operating system of European treasury"

**What they sell.** A cloud, AI-assisted treasury management system for
finance teams at mid-size and large European companies: real-time cash
visibility, forecasting, reconciliation and cash accounting, payments.
Founded 2021 in Madrid; offices in London, Berlin and Munich; 130–150 people.

**Product loop.**
- Inputs: bank transactions via API, host-to-host, EBICS and SWIFT from
  "15,000+ financial institutions", PSP data (Stripe, Adyen, PayPal), and
  bidirectional ERP sync (SAP, NetSuite, Dynamics, Sage) every few minutes.
  The Necto acquisition (Feb 2025) added direct corporate bank APIs.
- **Deterministic rules run first** (prior categorisations, business rules);
  the LLM ("TellMe") is invoked only on ambiguity, and "its answer is treated
  as a proposal, not a fact".
- Outputs are **suggestion objects**: categorisation, invoice and PSP matches,
  GL postings, forecast adjustments, liquidity moves. Three autonomy modes:
  Silent (executed, logged, reversible), Guided (approve, modify or reject in
  one click), Ask (natural language, including Claude via MCP).
- Consumers: treasurers and CFOs reviewing exceptions only, controllers
  closing the month, payment approvers executing batches.

**Scale.** Named customers: Treatwell, Fever, Wallapop, Cabify, Playtomic,
HOFF, Molins, Northern Data, Cooltra (50,000+ transactions automated). 150
corporate clients in Feb 2024; TellMe trained on data from 250+ finance teams
(Nov 2025). The "400 clients in Europe" figure appears only on the HackSpain
site `[unverified]`.

**Funding.** €30M Series B, 12 May 2026, led by Cathay Innovation with
Creandum, Samaipata, 4Founders and Venture Friends returning. Earlier: €14.7M
Series A led by Creandum (Feb 2024). Angels include Maximilian Tayenthal (N26)
and Martin Blessing (ex-Commerzbank CEO).

**People.** Antonio "Toni" Berga and Carlos Serrano García-Lisón, co-founders
and co-CEOs, both ex-JP Morgan investment banking. Tomás Gil, co-founder and
CTO, ex-CTO of Fintonic Latam.

**What they say they care about.**
- "Let the model interpret intent, and let deterministic code do the work."
  "Boring infrastructure is easier to reason about when the interesting part
  is already unpredictable."
- Reversibility and audit: "Nothing is applied until a person accepts it, and
  an accepted action can still be undone." Permissions enforced at the data
  level.
- Governance, not rubber-stamping: "The AI functions as a policy executor,
  not a decision maker."
- **"Acceptance rate is our north-star quality metric."** Shadow-mode
  rollouts before autonomy.
- Real-time connectivity as the moat: "Your full cash position. Real time.
  One screen."

**Your angle.** Cash forecasting is forecasting. Variance explanation,
multi-entity aggregation, exception-driven review: this is your supply-chain
modelling vocabulary applied to money. Their ex-banker founders will respect
a formula with stated assumptions.

**Likely challenge themes (hypotheses).**
1. A reconciliation or categorisation agent: match messy bank lines to
   invoices or PSP payouts (one-to-many, partial), with explainable,
   reversible suggestions and an acceptance-rate metric.
2. A cash-forecast variance explainer: ingest multi-entity, multi-currency
   positions and answer "why did last week's forecast miss?" with cited rows.
3. A governed liquidity or payment agent: propose intercompany sweeps or
   batches under hard policy limits, human approval and a tamper-evident log.

**What "great" looks like to them.**
- Correctness first: deterministic validation around any LLM step, idempotent
  writes, every suggestion traceable to source rows and undoable.
- Finance-native framing: close days saved, percent auto-reconciled, hours a
  week returned to the treasurer. Know what CAMT, MT940 and ISO 20022 are.
- Exception-driven UX: show the reviewer only what needs judgment, with the
  reasoning, permissions and audit log visible in the demo.

**Q&A landmines.** What stops the model booking a wrong entry or moving
money, where exactly is the approval, can it be reversed? How do you measure
quality: acceptance rate, match precision, false positives at 50,000
transactions? How would this connect to SAP or NetSuite and real bank feeds,
and how do you isolate data across entities?

**Careers.** embat.io/work-with-us. Roles open in Sep 2026 include Senior
Software Engineer (Payments and Banking), Treasury Implementation Specialist,
SAP S/4HANA Product Owner, Senior Platform Engineer (all on
embat.factorialhr.com).

**Sources.** embat.io (home, about-us, work-with-us,
artificial-intelligence-finance, tech/llms-and-determinism-in-finance,
tech/internal-ai-agent-platform, success-stories) · techcrunch.com
2024/02/27/embat · financialit.net Series B note (May 2026) · pymnts.com Necto
acquisition · treasuryxl.com "Building a smarter treasury" ·
theglobaltreasurer.com Nov 2025 · HackSpain track-sponsors.ts.
## THEKER Robotics — "general-purpose robotics made in Spain"

**What they sell.** "AI-native generalist robots": reconfigurable industrial
robots whose hands, arms and body can be swapped or resized per task, sold as
Robotics-as-a-Service to logistics, retail and manufacturing operators for
variable, unstructured handling (parcel induction, packing garments, bottles
and cans). Founded 2022, Barcelona, 100% on-site, dozens of people heading
for 120 by end of 2026.

**Product loop.**
- Site scoping, layout and integration design per facility; certification,
  safety and IP documentation for "repeatable replication across sites".
- The robot is configured with task-appropriate end-effectors; deep-learning
  vision and control handle items "regardless of shape, size, orientation or
  weight" without reprogramming. Research lineage: **InCoRo** (arXiv
  2402.05188, both founders co-authors): an LLM controller plus scene
  understanding plus a feedback loop that "continuously analyzes the state of
  the environment and provides adapted execution commands".
- Deployed robots "operate in real client environments and generate
  continuous data streams" flowing edge to cloud into monitoring; robots
  "keep learning while in operation".
- Output for the buyer: throughput and performance metrics on a subscription.

**Scale.** Inditex (early customer and investor), MANGO logistics centre (Jul
2026), Paack warehouses (parcel induction). Pursuing waste management and
food and beverage. "15,000 job applications." Barcelona showroom.

**Funding.** **$85M (about €74M) Series A**, 11 Jun 2026, led by CRV, with
Samsung, Aglaé Ventures (LVMH), Cathay Innovation, 20VC, Henkel Ventures,
Korelya, Bright Pixel; returning Inditex, Kfund, Kibo, Itnig, Mission. Called
Europe's largest robotics Series A; twice the original target. The HackSpain
site's "$100M+" is cumulative: $21M seed led by Kibo Ventures (Jul 2025,
Spain's largest seed at the time) plus a ~€3M first round (Feb 2025). Kibo's
Ignacio Alfeirán is a grand-prize judge; Kfund's Jaime Novoa too.

**People.** Carla Gómez Cano, co-founder, robotics and AI engineer, company
spokesperson in all funding coverage (CEO title `[unverified]`); InCoRo
co-author. Jiaqiang Ye Zhu, co-founder, engineer, first author of InCoRo with
Meta AI and ServiceNow researchers.

**What they say they care about.**
- Production over demos: "We're not building demos. Our robots are live in
  production." "We founded Theker not to develop pilots, but to deploy robots
  that work from day one."
- Generalism: "If you always have to put the same cookie in the same box,
  that works perfectly, but most processes aren't like that."
- Ambition: "solving 100% of physical work"; "Automating the physical world
  should be as easy as launching an app."
- Culture: Champions, Resilience, Hardcore, Boldness, Agency; "the top 0.01%
  of talent".

**Your angle.** Warehouses are your world: induction, packing, changeovers,
throughput, exceptions. You can talk picks per hour and uptime with an ops
buyer's vocabulary and forecast demand for robot capacity. Their Technical
Deployment Engineer and Systems Engineer roles are deployment work with
hardware attached.

**Realistic software surface for a team.** theker.ai is a teaser: no public
SDK, API or dataset. Plausible surfaces: (a) simulation of heterogeneous
parcel or garment handling (MuJoCo, Isaac) with a sim-to-real story; (b)
natural-language task specification to robot commands with vision feedback,
InCoRo-style; (c) fleet telemetry and root-cause analysis from robot data
streams; (d) perception on irregular or deformable objects (grasp points,
segmentation). Frame any sim work as a path to production; they are allergic
to demos.

**Likely challenge themes (hypotheses).**
1. Natural-language task spec to an executable pick-and-place program with a
   closed vision feedback loop in simulation, for parcel induction or garment
   packing.
2. Perception: grasp-point or pose estimation on heterogeneous parcels or
   deformable garments from RGB-D, robust to orientation.
3. Fleet operations: ingest telemetry, detect and root-cause failures,
   auto-generate deployment documentation or dashboards.

**What "great" looks like to them.**
- Sim-to-real credibility: domain randomisation, sensor noise, an honest gap
  analysis; the same policy shown across two end-effector configurations.
- Measured success rate and cycle time against a human or fixed-automation
  baseline, with a failure taxonomy.
- RaaS framing: picks per hour, uptime, changeover time, and awareness of PLC
  and safety integration on a live line.

**Q&A landmines.** How does it behave on a SKU it has never seen? Is that
success rate from sim or real hardware, and how many trials? Why put an LLM
in the loop when a pick cycle is about a second? How does this plug into a
customer's PLCs and safety systems?

**Careers.** theker.ai/careers → theker.factorialhr.com (19 roles, all
Barcelona on-site): Deep Learning Engineer, Software Robotics Engineer, AI
Tooling Engineer (LLM/RAG internal tools, evals), Technical Deployment
Engineer, Systems Engineer (edge-to-cloud data).

**Sources.** theker.ai · theker.factorialhr.com · techcrunch.com 2026/06/11
"Theker just raised $85M…" · forbes.es (€74M round; €18M seed) ·
novobrief.com "Barcelona's Theker raised Spain's largest ever seed round" ·
arxiv.org/abs/2402.05188 · linkedin.com/company/theker.
