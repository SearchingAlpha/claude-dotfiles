# Embat "X Ray" — prior art and research digest

Compiled 2026-09-18 (H0 + ~2h). Purpose: everything already solved in this
space so the team copies instead of inventing. Challenge text source: the
Embat brief published in the HackSpain app (summarised in section 1).

## 1. The challenge, condensed

**Ask.** "¿Puede el dinero decir cómo está una empresa?" Build a monthly
**financial-health score** per company from 24 months of treasury data, and a
**sellable product** on top of it. The score is the engine; the product is the
deliverable.

**Data.** 1,286 synthetic companies in 250 corporate groups (1–24 companies
per group, median 2), Sep 2024 → Sep 2026, nine CSVs plus `data_dictionary.md`:

| File | Content |
|---|---|
| `groups.csv` | one corporate group per row |
| `companies.csv` | group, country, currency, ERP, onboarding date; `company_id` is the join key |
| `banking_products.csv` | current, card, TPV, savings, investment, expense platform; bank and currency |
| `debt_products.csv` | loans, leasing, credit lines, mortgages, renting, factoring, confirming, guarantees; granted and outstanding |
| `debt_schedule_config.csv` | instalment type, frequency, periods, interest rate, next payment date |
| `transactions.csv` | 24 months of bank movements: date, amount, category, reconciliation status, counterparty, bank concept |
| `invoices.csv` | ERP invoices issued and received: issue, due, paid date, outstanding, status, counterparty |
| `balances.csv` | balance per account and product at 2026-09-01 |

**The schema is Embat's own API.** `debt_schedule_config` fields map 1:1 to
`DebtScheduleConfigSchema` in Embat's OpenAPI (amortisingFrequency,
outstandingBalance, grantedBalance, totalPeriods, annualInterestRateOrSpread,
interestType, nextPaymentDate); product types, transaction fields
(operationDate / valueDate, reconciled, categories, concept) and balances
match `GetProductsResponseSchema`, `GetTransactionsResponseSchema`,
`GetBalancesResponseSchema`. Pitch consequence: anything built on these
files runs unchanged on `api.embat.io`. Zero-integration product.

**Six questions the system must answer, per company, per month.**
1. Who is healthy (not only who is in trouble).
2. Who is improving (45 → 65 can be next year's best bet).
3. Who is starting to turn (82 → 68 still looks healthy).
4. Bump or fall: one bad cash month vs structural deterioration.
5. Why the score changed: which signal moved and when.
6. How many months in advance it was visible.

**Mandatory deliverables.** Predictions on a hidden test of 60–80 companies
(leaderboard + scoring script available from Friday); bidirectional signal;
trajectory not snapshot; per-company explanation; a product on top of the
score; a named buyer ("la empresa que os entrega los datos es el comprador
más obvio" = Embat); a navigable demo (a notebook on a laptop does not
count). **Bonus:** measured anticipation in months; a monitor that raises
its hand unprompted.

**Judging.** Three equal blocks. *Accuracy*: generalisation, trajectory,
both directions. *Timing*: anticipation measured in months, stability (bump
vs fall), monitor. *Value*: product, buyer, explanation, craftsmanship.
Quote from the brief: "Un modelo sencillo con un producto claro encima nos
interesa más que uno sofisticado que se queda en el número."

**Suggested product directions.** Credit marketplace; credit insurance with
score-driven premium; working-capital line with auto-recalculated limit;
recommendation agent sold to the company on its own data; sector-level
investment signal; supplier scoring; a seal companies show to negotiate.

**Support.** Two Embat engineers rotating all weekend, a data specialist
reachable at night, a 30-minute Saturday-morning session on how money moves
inside a company and where each file comes from. Go to that session.

## 2. What Embat already does (and the gap we fill)

Sources: embat.io/artificial-intelligence-finance, tech blog
"LLMs and determinism in finance", "internal AI agent platform",
financial-risk-management/counterparty, blog "Claude for Treasury".

- **TellMe architecture.** Deterministic rules first, LLM only on ambiguity,
  every output is a *suggestion object* with reasoning and audit trail;
  nothing hits the ledger without acceptance; reversible. Modes: Silent /
  Guided / Ask (Ask reachable via Claude MCP). **Acceptance rate is the
  north-star quality metric.** Shadow mode before autonomy.
- **Already shipped.** Reconciliation, categorisation, enrichment, cash
  accounting, forecasting, and **"accounts receivable forecast date
  prediction"** as an independent pipeline ("if a client consistently pays
  12 days late, TellMe factors that in"). Counterparty page: DSO/DPO from paid
  invoices, aging buckets, concentration, "deviation alerts", IBAN checks.
- **Not shipped.** No numeric health score, no rating bands, no outlook or
  trend, no watchlist, no supplier-vs-customer distinction, no financing or
  insurance action on a flagged counterparty. That is the whole X Ray brief.
- **Internal platform "Agent Mr. Batt"**: 10+ specialist agents on Claude
  Managed Agents, router defers to humans when unsure. Judges will like an
  agent that presents score changes as suggestion objects.

## 3. Prior art by theme

### 3a. Scoring from transaction data (cash-flow underwriting)

- **FinRegLab 2025, "Sharpening the Focus"**: 38k SME loans, Plaid / Ocrolus
  statement data. Seven variables cover the industry baseline: monthly
  credits, withdrawals, average balance, SD of credits and balances, count of
  low or negative ending balances, NSF events, daily-pay (MCA) debits.
  Deposits and balances "the most powerful"; +1 SD balance ≈ −2 pp default for
  young firms. Cash-flow data adds most for thin-file, young companies.
- **Experian Cashflow Score**: tag each transaction income / bill / debt /
  discretionary, derive a monthly net-cash-flow series, target 60 DPD in 12
  months, up to 25% lift over bureau score. Monthly net cash flow is *the*
  derived series.
- **Prism Data CashScore v4**: trajectory, volatility, behaviour and red flags
  are explicit attribute families. Trended attributes are a product axis.
- **Plaid, Ocrolus, Nova Credit Cash Atlas, Codat Credit Model, Mastercard /
  Finicity, Tink, Yapily**: declining balances and income changes as early
  warning; counterparty concentration and hidden-debt detection via
  unreconciled transfers (Ocrolus); cash-based P&L, debt repayment
  projections, customer / supplier reliability (Codat). Codat's 24-month
  scope matches the X Ray dataset exactly. Codat pivoted to selling *insight*
  to banks: the insight layer is what monetises, not the connections.
- **Spain / EU**: Inbonis (ESMA-registered SME rating, statement-based,
  AAA–D, four blocks: business, financial, governance, environment, 12-month
  validity; borrow the block structure for sub-scores). Novicap scores the
  *payer* of the invoice, not the issuer: debtor quality from received
  invoices. Nobody in Spain publishes a bank-transaction SME health score.
- **Academic**: Kou et al. 2021 (170k Chinese SMEs, counterparty-network
  features such as number and volume of counterparties predict bankruptcy
  without statements); AI-BAAM, ICLR 2026 (10 bank-statement features,
  monotonic WoE logistic, AUROC 0.806, +24.6% over application-only);
  Kvamme et al. 2018 (1D-CNN on daily balance series, AUC 0.92: raw balance
  path carries most of the signal); Tiger Analytics SME EWS (peer-normalised
  z-scores, velocity variables, XGBoost KS > 65%, 2× rule-based EWS);
  Italian bank-firm data (credit-line usage and overruns beat accounting
  ratios). Bank of England 2020: the incumbent's edge is seeing "cash flows,
  balances and existing loan performance". That edge is what Embat holds.
- **Classic baselines to name-drop**: Altman Z'' for private firms
  (6.56·WC/TA + 3.26·RE/TA + 6.72·EBIT/TA + 1.05·BVE/TL), Ohlson O,
  Zmijewski. Bank-data proxies: runway = balance / average net outflow;
  DSCR proxy = net operating inflows / scheduled principal + interest (from
  `debt_schedule_config`); equity proxy = balance − outstanding debt.

### 3b. Payment-behaviour features (invoices.csv)

- **Zeng, Melville et al., KDD 2008**, "Using predictive analysis to improve
  invoice-to-cash collection". Canonical 17-feature table: invoice amount,
  terms, dispute flag, plus customer history aggregates (late count and
  ratio, late amount sum and ratio, average days late of paid and of
  outstanding invoices, outstanding counts). Invoice-only features weak;
  customer history lifts accuracy 70% → 87%. Copy this table.
- **Appel et al. 2019/2020** (arXiv 1912.10828, 2008.07363): LightGBM 5-bucket
  late classifier, F1 ~83%; recency-weighted history with ~4-month lookback
  optimal; output a ranked worklist by P(late) × amount.
- **Moore et al. 2024** "Survival Boost" and **Saini et al. 2024** discrete-time
  hazard: open invoices are censored data, do not drop them; a hazard curve
  gives expected date, P(late by X) and aggregate cash forecast from one
  model. Overkill for 36h, cite it.
- **Malkus et al. 2025** (Cluee): fast vs slow moving average of delay adds
  3–5 pp on tiny histories. Cheapest "trajectory" signal there is.
- Industry conventions: **D&B PAYDEX** (dollar- and recency-weighted days
  beyond terms; 80 = on terms, 70 = 15 DBT, 50 = 30 DBT), **Experian DBT**
  (3-month tradelines, industry norm ~7 days), **Creditsafe** rules: a jump
  from DBT 5 to 20 in a quarter is worse than a stable 20; "yo-yo" volatility
  and "DBT creep" (Net-30 drifting to 42–45) are red flags. Ex-JPM judges
  recognise DBT instantly. Show the formula.

### 3c. Trajectory, outlook, bump-vs-fall

- **Rating-agency vocabulary.** Moody's *outlook* = likely direction over
  12–18 months (Positive / Negative / Stable); *watchlist* = change likely
  within ~90 days. Hamilton (Moody's 2004): conditioning on outlook greatly
  improves transition prediction. PIT vs TTC PD (IFRS 9 vs Basel) is exactly
  "bump vs fall". Deliverable framing that judges already know:
  **Level (0–100) + Outlook (trend sign) + Watch (alert flag)**, with a PIT
  score and a TTC-smoothed score side by side.
- **Trend extraction, causal (filter, not smoother).**
  - Kalman local linear trend: `statsmodels.tsa.UnobservedComponents(y, 'lltrend')`;
    filtered slope and its variance give a per-month outlook z-score.
  - Holt's linear: `l_t = a*y_t + (1-a)*(l_{t-1}+b_{t-1}); b_t = b*(l_t-l_{t-1}) + (1-b)*b_{t-1}`. Five lines of numpy, fallback if statsmodels is slow over 1,286 × 24.
  - Theil–Sen rolling slope over 6 months, divided by rolling MAD. Robust to a single dip. Momentum features: delta 3m, delta 6m, slope_6m − slope_3m.
- **Bump vs structural.**
  - Hampel filter tags a month as a shock: `|y_t − median| > 3 × 1.4826 × MAD`. Feature = shock flag plus months since shock. Tag, never overwrite.
  - Recovery ratio `r = (y_{t+1} − m_t) / (y_t − m_t)`: r ≈ 0 transient, r ≈ 1 structural. Same logic as bank early-warning "N consecutive months" triggers.
  - One-sided CUSUM on standardised residuals: `S_t = max(0, S_{t-1} + z_t − k)`, alert when `S_t > h` (k ≈ 0.5, h ≈ 4). Ignores single spikes, and its first crossing **is** the anticipation timestamp.
  - STL (period 12) trend vs remainder for "noise share". Avoid Hodrick–Prescott: two-sided, bad end-points.
  - Offline: `ruptures` PELT to label structural breaks in training data.
- **Regulatory anchor.** EBA GL/2020/06 §8.5 (paras 269–277): quantitative
  early-warning indicators with trigger levels, escalation and a **watch
  list**. Frame the monitor exactly as indicator → trigger → watch list →
  escalation.

### 3d. Explaining the score and its change

- **WoE scorecard** (OptBinning `Scorecard`, or `scorecardpy`): points per bin
  are additive, so month-over-month delta = sum of per-feature point changes.
  A native "score drift waterfall" with zero extra ML, and a 0–100 or 300–850
  scale judges recognise.
- **Monotone GBM**: LightGBM `monotone_constraints` (+1 cash buffer, −1 overdue
  days). AUC cost ≈ 0–1%, big credibility gain (arXiv 2512.17945). `xBooster`
  converts a boosted model into a points scorecard.
- **Delta attribution**: SHAP is additive, so `phi(x_t) − phi(x_{t−1})`
  attributes the change; DeltaSHAP (arXiv 2507.02342) formalises it. Reason
  codes FCRA-style: top four factors, specific, only from scored features.
  Prefer rule-based reasons in the demo: SHAP is unstable across refits.
- Use the LLM only to write the narrative from deterministic reason codes.
  That is Embat's own doctrine ("let the model interpret intent, let
  deterministic code do the work").

### 3e. Monitor that raises its hand

- Hysteresis: trigger at score < 40 for two consecutive months, clear at > 45
  for two months (Datadog recovery-threshold pattern). Kills flapping.
- Tiers: Watch (slope z < −1), Warning (CUSUM alarm), Critical (level below
  threshold and structural). Mirror for upgrades ("upgrade watch").
- Alert budget per portfolio per month; rank by severity × exposure.
- Package each alert as an Embat suggestion object (proposal, reasoning,
  accept / reject, undo) and display acceptance rate live.

### 3f. Feature list, ranked by literature consensus

All computable from the nine CSVs. Build in this order.

1. Balance level normalised by monthly outflows (runway), average and
   month-end.
2. Count of low or negative balance days; returned direct debits
   (devoluciones) and NSF-type events.
3. Monthly net cash flow (inflows − outflows) and its 3- and 6-month slope.
4. Inflow (revenue) level and trend vs prior 12 months, seasonally adjusted
   (STL, period 12).
5. Balance and inflow volatility (SD, coefficient of variation).
6. Debt-service ratio: scheduled principal + interest / net operating inflows.
7. Credit-line utilisation (`balanceCreditGranted` vs drawn) and overrun
   frequency.
8. High-cost financing detection: new debt products, daily or weekly lender
   debits, rising `annualInterestRateOrSpread`.
9. Customer concentration (HHI, top-1 share) and counterparty count from
   `invoices.csv` and `transactions.csv`.
10. Deposit regularity: days since last inflow, inflow frequency.
11. Invoice-based DSO / DPO, late share received vs paid, DBT trend
    (section 3b).
12. Peer-normalised z-scores of all the above by country, currency and size
    cluster, plus interest-cost trend.

Group-level roll-up: aggregate subsidiaries to the corporate group and add
"share of group cash" and intra-group transfer dependence as features; the
groups file exists for a reason.

## 4. Evaluation protocol (copy into the repo)

1. Monthly panel per company. Target `y_{t+h}` = change in the ground-truth
   health metric for h = 3 and 6; classes improve / stable / deteriorate via a
   dead band.
2. Split A: `GroupKFold(5)` by corporate group (subsidiaries share shocks:
   never let a group straddle train and test). Split B: time cut, months ≤ 18
   train, 19–24 test. `mlxtend.GroupTimeSeriesSplit` does both.
3. Baselines: raw level, delta 3m, Holt slope sign, "flag if last month bad".
4. Ranking: Spearman(score_t, y_{t+h}). Direction: AUC(up), AUC(down),
   reported separately (judges check symmetry).
5. Three-class balanced accuracy.
6. Anticipation: months from first alert to labelled event, median per
   company, plus share with ≥ 3 months lead.
7. Stability: mean |delta score| on stable months; alert flap rate.
8. Precision@k on the watchlist, k = alerts a treasurer would review.
9. Calibration: Brier and reliability plot if probabilities are output.
10. Explanation check: top-four reasons cover ≥ 80% of |delta score|.
Ship the model that wins on Split A: that is the hidden-company test.
ERP-RiskBench (arXiv 2603.06671) shows random CV inflates results; do not
report it.

## 5. Product comparables and buyer

| Direction | Comparables | Data / pricing | Embat fit |
|---|---|---|---|
| Counterparty trajectory watchlist | Creditsafe monitoring (~$99–2,500/mo, $15–75k/yr API), D&B Finance Analytics, Coface URBA360 (0–10 = 12m PD), CreditRiskMonitor FRISK (nightly bankruptcy PD, behaviour-derived) | bureau data + alerts | Slots into Embat's Risk module as a TellMe add-on. Embat has bank-truth data bureaus lack. |
| Receivable cover quote | Allianz Trade Single Invoice Cover API + Grade Check (1–10, webhooks), CESCE Pay Per Cover, Crédito y Caución (0.1–0.6% of insured sales), Hokodo (0.25–1.5%/invoice, wound down 2025) | per-debtor risk pricing | Distribution deal, not underwriting. Score → quote button. |
| Pre-qualified working-capital line | iwoca (Open Banking, 40 data points, ~1.6%/mo), Novicap (Spanish factoring, from 0.15%/mo, 48h), Wayflyer / Silvr / Ritmo (dynamic limits, 2–10% flat) | categorised bank feeds | Referral fee on a "line you already qualify for" widget. |
| Self-facing health check + seal | Tide Cashflow Insights (free, ML forecast, shows pre-qualified credit), Xero Business Snapshot, Inbonis rating seal (EUR 500–4,500), D&B rating certificate | statement or bank data | Seal sells to Embat clients' SME suppliers, not to Embat's mid-size clients. |
| Sector nowcasting for investors | CaixaBank Real-Time Economics, BBVA Research, Fable Data, Consumer Edge | card panels, subscriptions | Weak: 400 clients is a small panel, data is confidential. Skip. |

**Ranked recommendation for 36h.**
1. **Counterparty Trajectory Watchlist inside Embat** (buyer: Embat, priced
   like Creditsafe tiers). Metric on screen: months of lead before the first
   60+ day overdue invoice, on backtest. Matches the team's registered name
   "X-Ray Watchlist".
2. **Receivable Cover Quote** (buyer: Embat + insurer partner, take rate
   0.1–0.6%). Metric: premium spread between top and bottom score deciles.
3. **Verified cash-health seal** (buyer: procurement at Embat clients).
   Metric: score stability vs an annual Inbonis-style snapshot.

## 6. Reusable code and data

- Statement parsers: `mt-940`, `bankstatementparser` (CAMT.053 / MT940 / BAI2),
  `pycamt`, `csb43` and `norma43` (Spanish Norma 43; Embat's only public
  GitHub repo is a Norma43 formatter).
- Embat API mirror with OpenAPI, Postman and a candidate MCP tool list:
  github.com/api-evangelist/embat. Sandbox creds are request-only
  (tech@embat.io): ask the Embat engineers on Friday.
- Open-banking sandboxes for a "live feed" demo moment: GoCardless
  `SANDBOXFINANCE_SFIN0000`, Plaid custom sandbox users, Tink demo bank.
- Public receivables datasets if the synthetic set lacks something: IBM Late
  Payment Histories (2.5k invoices, Kaggle), HighRadius payment-date dataset
  (~50k invoices, Kaggle).
- Full-stack analogues: `Amitosh07/msme-receivables-intelligence` (XGBoost
  AUC 0.85, MAE 2.8 days, FastAPI + React), `Kanakbaghel/invoice-chaser`
  (four urgency tiers, per-client risk score, alerts), `NehaKhann/sme-cashflow-explainer`
  (deterministic metrics + rule score + LLM narrative),
  `aboerger/Fabric-Hackathon-AP-Payment-Prediction` ("rewind the clock"
  as-of view, worth stealing for the anticipation demo).
- Libraries: LightGBM (quantile and monotone), OptBinning, MAPIE (conformal
  intervals), ruptures, river (online CUSUM / Page-Hinkley), statsmodels.

## 7. Landmines the research surfaced

- Payment-date leakage: at cut date T never use invoices settled after T.
- Group leakage: subsidiaries in the same group share shocks; split by group.
- SHAP instability across refits; ship rule-based reason codes.
- Alert fatigue: fix the budget first, tune thresholds to it.
- Do not claim LLM prior art for payment timing: none credible exists
  (2024–2026); LLM work in this domain is invoice parsing. Use it for
  narrative only.
- "400 clients" appears only on the HackSpain site; Embat's own materials say
  150 (2024) and TellMe trained on 250+ finance teams. Do not quote 400 to
  the founders.

## 8. Who already runs a similar algorithm in production

What each company publishes about *how*. "n/p" = not published. Vendor
marketing numbers are labelled as such.

| Company, score | Inputs | Algorithm as published | How they handle trajectory | Claimed lift |
|---|---|---|---|---|
| **VantageScore 4.0** trended attributes | 24 months of balance, limit, minimum due, scheduled vs actual payment | Segmented scorecards built with and without trended attributes | Explicit attribute families: slope of balance, start-to-end % change, % change over 12 months, count of limit changes in 3 months, highest monthly utilisation, time since over-limit; windows 3/6/12/24 months; looks past a "momentary spike" | Gini +4.7 to +13.8% existing accounts, up to ~20% (Philadelphia Fed paper) |
| **FICO 10T** | 24+ months balance and limit history | Scorecard; classifies revolver vs transactor from balance path | Balance trending up, down or flat; rising = risk | up to 17% fewer mortgage defaults vs FICO 8 (FICO PR) |
| **Experian Trended 3D** | same 5 fields × 24 months | Attribute library on "rate and stability of change" | Slope of balance growth, payment velocity, erratic behaviour, rapid paydown or accumulation | up to 7% lift (vendor) |
| **TransUnion CreditVision / TruVision** | up to 24 months incl. actual payments | Ratio algorithms: total payment ratio, payment-to-balance, excess payment | Trailing-window ratios, "time-series attributes" | n/p |
| **Plaid LendScore** | bank transactions, 1.44M tradelines, label 90+ DPD in 12 months | XGBoost with monotonic constraints, 145 features, SHAP → 5 reason codes, score 1–99 | 30/60/90-day windows: average monthly inflows, balance SD, balance trend, income regularity; 81% of power from cash-flow features | KS +9.1% overall, +16.6% charge cards (Plaid blog) |
| **Prism Data CashScore v4** | deposit-account transactions, consortium | Attribute families income, balances, expenses, bills, behaviours, ratios; reason codes like "balance trend: increasing", "change in discretionary spending" | recent vs prior comparisons | "30% predictive lift" (vendor) |
| **Nova Credit Cash Atlas**, **Tink Risk Insights** | bank transactions | 1,000+ windowed attributes (1 day to 24 months); overdraft days, loan repayment ratio, collections | multi-window | "+15% approvals", "+40% Gini" (vendor) |
| **Square Loans** (patents US9727912B1, US11176607B1, US10019698B1) | card processing history | ML classifier on payment history, GPV over period, **month-over-month GPV growth**; clustering + supervised models on sales velocity and seasonal cycles; offer ≈ 8% of annual GPV | detects slow seasons from **peer merchants** and defers withholding: separates seasonal dip from decline | n/p |
| **Stripe Capital**, **Shopify Capital**, **QuickBooks Capital** | processing or accounting data, ≥ 90 days | daily automated prequalification, undisclosed ML | "processing consistency, growth trajectory, customer breadth, dispute rate" (Stripe); "sales trends and seasonality" (Intuit) | n/p |
| **OnDeck Score** (patent US20130339217A1) | read-only bank feed, bureau, firmographics, "2,000+ data points" | rules engine plus risk score | trend formula not disclosed | n/p |
| **Intuit** patent US11270375B1 | income, utilisation, balances, negative-balance flags | ML → probability → letter grade A+ to F; three sub-scores: day-to-day (−100..100), emergency (0–10), prosperity (0–100) | none disclosed | n/p |
| **Moody's RiskCalc** (private firms) | 10 ratios from 17 statement items | univariate non-linear transforms → probit → EDF at 1 and 5 years; industry and credit-cycle adjustment | trend enters via cycle factor, not firm history | accuracy-ratio tables in paper |
| **Moody's KMV EDF** | equity value and volatility, liabilities | distance to default = (expected asset value − default point) / asset volatility; default point = short-term debt + ½ long-term debt | the DD path is the early-warning signal | — |
| **CreditRiskMonitor FRISK / PAYCE** | market data, Altman-style ratios, crowdsourced usage; PAYCE = trade-payment data only | blended model | daily re-score; payment-behaviour deterioration | FRISK: 95–96% of bankruptcies in 12 months in classes 1–5, ~3× Z''; PAYCE: "80%+ of bankruptcies 12 months ahead" (vendor) |
| **Dun & Bradstreet Failure Score / Delinquency Predictor** | trade experiences incl. **month-to-month payment trends**, filings, financials | predictive model → 1,001–1,875 score, class 1–5, 12-month horizon | payment trend is an explicit input | n/p |
| **Iberinform Insight View** (Spain) | RAI/ASNEF defaults, statements, investigated data, "AI on financial magnitudes" | "Score de Riesgo" = probability of meeting obligations within 12 months; 25 alert types | change alerts | n/p |
| **CESCE, Coface URBA360, Inbonis, Informa D&B, Axesor** (Spain) | payment behaviour, statements | nothing algorithmic public; converge on 12-month PD + payment behaviour + change alerts | — | — |
| Kabbage/Amex, iwoca, Funding Circle, Tide, Brex, Ramp, Codat, Ocrolus, Credit Kudos, Bud, Qonto, Agicap, bank EWS (Intesa, ING, BBVA, Santander) | bank + accounting feeds | marketing text only | — | — |

**What to imitate.**
1. VantageScore-style trended attributes per metric: slope over 3/6/12/24
   months, start-to-end % change, months above or below threshold, highest
   month, time since event. Applied to cash balance, inflows, DSO, debt service.
2. Square's trick for dip vs decline: compare to same month last year and to
   peer seasonality (Banco de España sector ratios as the peer set), and
   require 2–3 consecutive months of negative slope before calling it
   structural.
3. Plaid's stack: monotone GBDT plus SHAP top-5 reason codes per month.
4. KMV's metric on cash: distance to default = (cash + expected net inflows
   over horizon − debt service due) / volatility of monthly net cash flow.
   Forward-looking and interpretable; months-to-barrier = (A − D) / |drift|.
5. D&B / PAYCE: payment-lag trends degrade before balances do. Lead with them.
6. Report lift like a bureau: score with vs without trended features, Gini or
   KS gain on the held-out set.

## 9. Algorithms that fit this dataset

| Algorithm | Prior art | Recipe | Fit | Gives anticipation / explanation |
|---|---|---|---|---|
| **Amex-style panel aggregation + GBDT** | Kaggle Amex Default Prediction 2022: 13 monthly statements × 190 features, same panel shape. 1st place: denoise → last/mean/min/max/std/first, last − first, lag diffs → LightGBM DART + GRU blend. GBDT on aggregates dominated; sequence nets only as blend members | `groupby(company).agg(last, mean, min, max, std, first)` on rolling 3/6/12 windows, slope via `polyfit`; `LGBMClassifier(boosting_type='dart', num_leaves=63, lr=0.02)`; GroupKFold by group | **Yes**, core | SHAP on aggregates; anticipation comes from the target horizon |
| **Landmarking + discrete-time hazard** | van Houwelingen 2007; Saini 2024 | at each month t, features = history to t, target = event within h ∈ {3,6,12}; `LogisticHazard` (pycox) or one LightGBM per horizon | **Yes**, cleanest anticipation | first horizon where P(event) > threshold = months of lead |
| **Random Survival Forest** | scikit-survival | `RandomSurvivalForest(n_estimators=500, min_samples_leaf=15)` on ~25k landmark rows; `predict_survival_function` | Yes | expected time to event |
| **Cox time-varying** | lifelines `CoxTimeVaryingFitter` | long format start/stop per month | Maybe (prediction awkward) | coefficients per ratio |
| **3-state HMM on the score** | hmmlearn | `GaussianHMM(n_components=3, covariance_type='diag')`, `lengths=[24]*n`; several seeds | **Yes**, trajectory layer | state posterior per month = improving / stable / deteriorating; expected duration 1/(1 − p_ii) = months to transition |
| **Rating migration matrix** | Jarrow–Lando–Turnbull; Moody's / S&P transition studies; `transitionMatrix` lib | bucket score into bands, estimate 1-month transition matrix from the panel | **Yes**, trivial | P(downgrade within h) = row of T^h |
| **Markov-switching regression** | statsmodels `MarkovRegression`; Duprey & Klaus ECB WP 2057 on financial stress regimes | pooled fit on score series, `k_regimes=2`, `switching_variance=True` | Maybe (24 points per company is thin) | `expected_durations`, smoothed regime probabilities |
| **Bayesian online changepoint** | Adams & MacKay 2007 | `bayesian_changepoint_detection`, `constant_hazard(12)`, StudentT | Yes for dip vs structural | run length resets then recovers = one-off; persistent new regime = structural |
| **Kalman local linear trend** | statsmodels `UnobservedComponents` | `'local linear trend'` per series, filtered slope | Yes | slope sign and CI |
| **Cash-flow distance to default** | Bharath & Shumway 2008 naive DD | `DD = (ln(A/D) + (mu − s²/2)·T) / (s·√T)`, A = cash + expected receivables, D = 12-month debt service from `debt_schedule_config`, mu and s from monthly net cash flow | **Yes**, one line | months to barrier = (A − D) / |drift| |
| **TabPFN v2** | Hollmann et al., Nature 2025; TabPFN-TS | `TabPFNClassifier().fit(X, y)` on the aggregated (company, month) rows; zero tuning | Yes as ensemble member (≤ 10k rows) | — |
| **Monotone LightGBM / CatBoost** | Plaid LendScore; arXiv 2512.17945 | `monotone_constraints` (+ runway, − overdue share), `monotone_constraints_method='advanced'` | **Yes** | defensible reason codes |
| **WoE scorecard** | OptBinning, scorecardpy, toad, skorecard, xBooster | `Scorecard(binning_process, LogisticRegression)`; xBooster converts a GBDT into points | **Yes** for the explainable view | additive points, delta waterfall, counterfactuals ("what raises the score") |
| **Bayesian hierarchical** | bambi | `distress ~ ratios + (1|group)`, bernoulli | Maybe (sampling time) | partial pooling across 250 groups helps hidden companies that share a group |
| **MiniRocket / tsfresh** | sktime, tsfresh | `MiniRocket(num_kernels=512)` + `RidgeClassifierCV`; `extract_features(MinimalFCParameters)` | tsfresh yes, MiniRocket maybe (black box) | tsfresh slope, autocorrelation, peaks feed GBDT |
| InceptionTime, GRU/transformer alone | Amex 2nd place | — | No: too few sequences unless trained on ~25k sliding windows | — |

**Label construction when the truth is hidden.** Proxy event at month t:
any of cash runway < 1 month within h, overdue receivables or payables
share above x, DSCR < 1 for two consecutive months, missed amortisation.
Composite index per the OECD / JRC handbook: z-score each ratio, weight by
PCA loadings or equal weights, sensitivity-test the weights. Altman-style:
fit a logistic on the proxy to obtain weights. Pseudo-label with temporal
consistency across adjacent months. Sanity-test the trajectory model on the
Kaggle "Financial Distress" panel (422 companies × periods, distress when
value < −0.50): the only public company-level panel with a distress target.

**Recommended 36h stack.**
1. Monthly ratio table + Amex-style rolling aggregates (3/6/12 months:
   last, mean, min, max, std, slope) + cash-flow distance to default +
   VantageScore-style trended attributes + Banco de España sector-quartile
   peer z-scores.
2. Proxy target = forward distress within h ∈ {3, 6, 12} (landmark framing).
3. Monotone LightGBM + TabPFN v2 blend; GroupKFold by corporate group; SHAP
   reason codes; WoE scorecard as the presentation layer.
4. Trajectory layer: Kalman slope + Bayesian changepoint run length (dip vs
   structural) + 3-state HMM on the score + migration matrix for
   P(downgrade in h months).
5. Anticipation = lead time between first alarm and proxy event: median
   months and AUC per horizon. Report Gini with vs without trended features.

**Rule-based v0 before any ML** (from `pelzade127/cash-flow-creditworthiness-scorer`):
income stability 30%, debt-to-income 25%, runway months 20%, overdraft
history 15%, expense management 10%; tiers A–D with reasons. Ship this in
hour 3, then beat it.

**Regulatory vocabulary for sub-scores** (EBA GL 2020/06 Annex 3, para 274;
Banco de España DO 2020): debt-service ratio increase, drop in turnover or
recurring cash flow (loss of a major client), margin narrowing, worsening
financing conditions, behavioural-score decrease, 30 days past due; ratios
DSCR, cash debt coverage, interest coverage, debt/EBITDA, current ratio,
turnover evolution; BdE flags negative equity and equity < 50% of net debt.
Name the sub-scores after these.

## Sources

Production scores: philadelphiafed.org (VantageScore trended credit data paper, 2017) ·
myfico.com/credit-education/credit-scores/fico-score-versions ·
experian.com/blogs/insights/trended-attributes-help-lenders-better-serve-consumers-across-credit-life-cycle/ ·
transunion.ca/solution/truvision/algorithms-attributes ·
plaid.com/blog/how-we-built-lendscore/ · prismdata.com/cashscore/ ·
novacredit.com/cash-atlas · tink.com/products/risk-insights/ ·
patents.google.com/patent/US9727912B1 · patents.google.com/patent/US11176607B1 ·
patents.google.com/patent/US10019698B1 · patents.google.com/patent/US20130339217A1 ·
patents.google.com/patent/US11270375B1 · docs.stripe.com/capital/how-capital-for-platforms-works ·
quickbooks.intuit.com/capital/ · efalken.com/papers/riskcalc.pdf ·
en.wikipedia.org/wiki/KMV_model · pages.creditriskmonitor.com (FRISK white paper 2026) ·
creditriskmonitor.com/resources/white-papers · dnb.com/en-us/smb/resources/credit-scores/failure-score.html ·
iberinform.es/productos/insightview · cesce.es/es/w/servicios/monitorizacion-de-riesgos ·
arxiv.org/abs/2510.22287

Algorithms: github.com/jxzly/Kaggle-American-Express-Default-Prediction-1st-solution ·
kaggle.com/competitions/amex-default-prediction/discussion/348111 ·
dl.acm.org/doi/fullHtml/10.1145/3582099.3582130 ·
github.com/minerva-ml/open-solution-home-credit · hmmlearn.readthedocs.io ·
statsmodels.org (markov_regression, statespace_local_linear_trend) ·
ecb.europa.eu/pub/pdf/scpwps/ecb.wp2057.en.pdf ·
github.com/hildensia/bayesian_changepoint_detection ·
lifelines.readthedocs.io (CoxTimeVaryingFitter) · scikit-survival.readthedocs.io ·
github.com/havakv/pycox · nature.com/articles/s41586-024-08328-6 (TabPFN v2) ·
arxiv.org/abs/2501.02945 (TabPFN-TS) · bambinos.github.io/bambi/ ·
sktime.net (MiniRocket) · tylergshumway.org/Bharath-ForecastingDefaultMerton-2008.pdf ·
oecd.org (Handbook on constructing composite indicators, 2008) ·
kaggle.com/datasets/shebrahimi/financial-distress

Scorecard and open-source: github.com/ShichenXie/scorecardpy · github.com/guillermo-navas-palencia/optbinning ·
github.com/amphibian-dev/toad · github.com/ing-bank/skorecard · github.com/xRiskLab/xBooster ·
github.com/itlubber/scorecardpipeline · github.com/pelzade127/cash-flow-creditworthiness-scorer ·
github.com/sirilahari10/prism-cashflow-underwriting · github.com/janMagnusHeimann/bankStatementAnalyzer ·
github.com/topics/bankruptcy-prediction

Regulatory and Spain: eba.europa.eu (GL 2020/06 final report, Annex 3) ·
bde.es (Documento Ocasional 2020, Blanco et al.) · app.bde.es/rss_www/Ratios ·
earlywarningeurope.eu · boe.es/buscar/act.php?id=BOE-A-2022-14580

Embat: embat.io/artificial-intelligence-finance ·
embat.io/tech-blog/llms-and-determinism-in-finance ·
embat.io/tech-blog/internal-ai-agent-platform ·
embat.io/financial-risk-management/counterparty ·
embat.io/blog/claude-for-treasury · api.embat.io/docs ·
github.com/api-evangelist/embat · github.com/embatio

Cash-flow underwriting: finreglab.org/research/sharpening-the-focus-using-cash-flow-data-to-underwrite-financially-constrained-businesses/ ·
experian.com/business/products/cashflow-attributes · prismdata.com (CashScore v4 post) ·
plaid.com/resources/lending/cash-flow-lending/ · ocrolus.com/blog/ai-powered-analytics-small-business-lending/ ·
novacredit.com/cash-atlas · docs.codat.io/updates/260107-credit-model ·
tink.com/blog/open-banking/lenders-guide-risk-assessments/ · inbonis.com/es/calificacion-crediticia/ ·
sciencedirect.com/science/article/pii/S0167923620301846 (Kou 2021) · arxiv.org/abs/2510.16066 (AI-BAAM) ·
sciencedirect.com/science/article/abs/pii/S0957417418301179 (Kvamme 2018) ·
medium.com/@tigeranalytics/ml-driven-early-warning-solutions-for-sme-and-corporate-credit-monitoring-67a3825312a7 ·
ncbi.nlm.nih.gov/pmc/articles/PMC8853378/ · bankofengland.co.uk/-/media/boe/files/fintech/open-data-for-sme-finance.pdf ·
arxiv.org/abs/2508.05425

Payment behaviour: dl.acm.org/doi/10.1145/1401890.1402014 ·
arxiv.org/abs/1912.10828 · arxiv.org/abs/2008.07363 ·
sciencedirect.com/science/article/pii/S2666827024000549 ·
link.springer.com/article/10.1007/s44257-024-00013-2 ·
arxiv.org/abs/2511.03631 · arxiv.org/abs/2603.06671 ·
dnb.com/en-us/smb/resources/credit-scores/what-is-paydex-score.html ·
creditsafe.com/us/en/resources/blog/credit/why-dbt-is-a-misunderstood-financial-metric.html ·
creditsafe.com/us/en/resources/blog/payments/5-red-flags-that-customer-late-payments-will-increase.html

Trajectory and monitoring: moodys.com/web/en/us/insights/credit-risk/outlooks.html ·
moodys.com/sites/products/DefaultResearch/2004700000425139.pdf ·
statsmodels.org/dev/examples/notebooks/generated/statespace_local_linear_trend.html ·
github.com/deepcharles/ruptures · riverml.xyz/dev/api/drift/PageHinkley/ ·
lips.cs.princeton.edu/pdfs/adams2007changepoint.pdf ·
eba.europa.eu/activities/single-rulebook/regulatory-activities/credit-risk/guidelines-loan-origination-and-monitoring ·
pmc.ncbi.nlm.nih.gov/articles/PMC13350937/ ·
docs.datadoghq.com/monitors/guide/recovery-thresholds/ ·
fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_4_PerformanceMetrics/TopKBased.html

Explainability: gnpalencia.org/optbinning/scorecard.html ·
github.com/ShichenXie/scorecardpy · github.com/xRiskLab/xBooster ·
arxiv.org/pdf/2512.17945 · arxiv.org/abs/2507.02342 ·
arxiv.org/pdf/2204.12365 · arxiv.org/pdf/2508.01851 ·
mapie.readthedocs.io/en/stable/content/conformal-prediction/regression/ ·
rasbt.github.io/mlxtend/user_guide/evaluate/GroupTimeSeriesSplit/

Products: highradius.com/software/order-to-cash/collections-management/ai-based-worklist-prioritization/ ·
chaserhq.com/features/late-payment-predictor · quadient.com/en-us/blog/yaypays-payer-analysis ·
tesorio.com/blog/ai-improves-customer-payment-prediction ·
iwoca.co.uk/news/iwoca-launches-open-banking · codat.io/lending-api/ ·
novicap.com/sobre-nosotros/ · silvr.co/blog/cash-flow-underwriting-unlocking-insights-from-bank-transactions ·
allianz-trade.com/en_BE/trade-credit-insurance/solutions/single-invoice-cover.html ·
developers.allianz-trade.com/information-product-grade-check ·
cesce.es/es/w/seguros-de-credito/master-oro-integral ·
creditoygestion.es/actualidad/precio-seguro-de-credito-empresas/ ·
vendr.com/marketplace/creditsafe · dnb.com/products/dnb-finance-analytics/credit-intelligence.html ·
coface.us/business-information/urba360-the-business-risk-assessment-tool-that-provides-data-in-one-click ·
creditriskmonitor.com/resources/frisk-score · einforma.com/informacion-empresas/inbonis-rating ·
finextra.com/pressarticle/88204/tide-launches-cashflow-insights-for-smes ·
treasurers.org/hub/treasurer-magazine/treasury-essentials-counterparty-risk

Data and tools: kaggle.com/datasets/hhenry/finance-factoring-ibm-late-payment-histories ·
kaggle.com/datasets/pradumn203/payment-date-prediction-for-invoices-dataset ·
github.com/Amitosh07/msme-receivables-intelligence · github.com/Kanakbaghel/invoice-chaser ·
github.com/NehaKhann/sme-cashflow-explainer · github.com/aboerger/Fabric-Hackathon-AP-Payment-Prediction ·
github.com/WoLpH/mt940 · pypi.org/project/bankstatementparser/ · pypi.org/project/csb43/ ·
developer.gocardless.com/bank-account-data/sandbox/ · github.com/plaid/sandbox-custom-users
