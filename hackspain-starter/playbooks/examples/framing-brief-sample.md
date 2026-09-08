# Sample Framing Brief (dry run of `/hs-frame`, 8 Sep 2026)

Produced by running `/hs-frame` on a **fictional** Embat-style challenge to test
the command before the event. The challenge text, the dataset and every number
below are invented. Keep it as a reference for the shape and density a good
brief has; do not reuse the idea blindly, the real challenge will differ.

## The fictional challenge text used

> Embat challenge — Reconciliation at scale. Mid-size companies receive
> thousands of bank transactions a week and have to match them to invoices,
> PSP payouts (Stripe, Adyen) and internal transfers before closing the month.
> Today a treasury analyst does this in spreadsheets: 1-to-1 matches are easy,
> but partial payments, one-payment-many-invoices, FX differences and bank fees
> eat the analyst's week. We provide a synthetic dataset: 12,000 bank lines,
> 9,500 invoices, 800 PSP payouts, 3 entities, EUR/GBP/USD. Build something
> that helps a treasury team reconcile faster and with an audit trail. We care
> about correctness, explainability and what the human still has to touch.
> Bonus: cash forecast impact of unreconciled items.

## What the command produced (Step 6 only)

```
FRAMING BRIEF — Matchbook
Track: Embat (+ Maisa, only if zero extra work; decide at H24)
One-liner: Embat wants a treasury analyst to close the month with every bank line matched and explained without spending the week in spreadsheets on partials, one-to-many, FX and fees.
JTBD: When the month closes, a treasury analyst at a three-entity, three-currency company wants every bank line matched to its invoice, payout or transfer with the reason attached, so the controller can close on time.
      Today they match in spreadsheets, which costs roughly a week per entity per month [assumption] and delays the close.
Demo moment: The judge drops the three CSVs, and within 20 s sees 12,000 lines become "matched / suggested / needs you" with counts, opens one one-to-many suggestion, reads why, accepts and undoes it with both in the audit log, which today takes ten minutes per line and a week per close.
Tracer bullet: three CSVs (one entity, EUR) → deterministic cascade (reference; amount+date; fee/FX tolerance; one-to-many subset-sum) emitting suggestion objects with evidence rows and a tier; model writes the explanation only on the ambiguous tier → funnel counts + suggestion queue with Accept/Reject/Undo + append-only audit log + unreconciled exposure by currency
      Faked for now: template explanations, uncalibrated tiers, one entity/currency, JSON audit log, no write-back.   Real by H24: all 3 entities and currencies; real one-to-many; clickable evidence; accept/undo mutate state and log; measured auto-match %, precision on 200 hand-labelled suggestions, count needing a human.
Impact (rough): 3 entities × 40 h/close × 12 × €35/h × 60% ≈ €30k and ~860 analyst-hours/yr per customer (volume cross-check ≈ €75k). Assumptions: dataset = one month; "eats the week" = 40 h/entity/close; €35/h; we remove 60% of residual-handling time; ×150 Embat clients ≈ €4.5–11M/yr.
Why now: rules-first + model-on-ambiguity is a settled pattern; buildable in 30h.   Why Embat: a suggestion object in Guided mode, scored by acceptance rate.   Why us: an agent shipped to production, exception-driven ops at scale, matching/scoring and formulas are our day job.
NOT doing: 1. Bank/ERP connectors 2. Cash-forecast model (exposure sum only) 3. Trained matcher 4. Users/permissions/entity isolation 5. GL journal generation
Roles: Pablo — product, impact model, matching-rule spec, demo script, pitch; [Dev 1] — cascade, suggestion objects, audit log; [Dev 2] — queue UI, accept/undo, demo data
Demo laptop: [missing: name]
```

Three lines for the CPO to say:

1. "We're not building a matcher; Embat has one. We're building the queue of
   what's left, with the reason attached, and we measure how much the human
   still touches."
2. "Rules match, the model only explains, and every accept can be undone. When
   a judge asks 'what if the model is wrong', the answer is 'it never booked
   anything'."
3. "Tracer bullet by H12: one entity, EUR, three files in, a queue out.
   Everything else is on the NOT list until that works."

## What to notice

- The scoring matrix killed the two ideas that were technically the most
  interesting (a calibrated match scorer, a rule-miner) because neither had a
  20-second demo moment. That is the matrix doing its job.
- "Obvious automation" scored low on track fit because Embat already ships it.
  The winning idea sits on top of what the company has, not beside it.
- Every number carries its assumption. A judge can argue with it; that is the
  point.
- The runner-up is kept as the H12 fallback, not discarded.
