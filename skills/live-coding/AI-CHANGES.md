# Patterns for changes to LLM features

Generic shapes for the changes most often asked in a live session on an agent or LLM app. Map each to the repo's own files in step 2; the repo's `CLAUDE.md` wins over this file. Every pattern has the same spine: **deterministic core, tested; LLM at the edge, traced and evaluated.**

## Gotchas that apply everywhere

- **Allow-lists and enums.** A new tool, category or label usually has to be added to a schema, an enum or a validation list that the data tests check. Grep for an existing value before adding one.
- **Model ids, thresholds, prices: one place** (a config module). Never inline.
- **Prompts under version control or a prompt store:** push changes to a non-production label and evaluate before promoting.
- **Tool order and system prompt are part of the prompt cache key.** Append new tools; keep definitions stable.
- **Network in tests:** inject the sender (a function parameter) and pass a fake; don't add an HTTP mocking library live.
- **One eval run proves little** on a non-deterministic agent. Say so; propose N runs and compare means.

## Tool for the agent

1. Spec/doc of the tool list.
2. Test the tool function directly: right data for the scoped entity, nothing from others, error on bad input.
3. Implement next to the existing tools; same registry, same input schema style, same tracing decorator. Narrow inputs: let the context fix the tenant/customer, not a model-supplied id.
4. Teach the agent: prompt line on when to use it and how to cite it as evidence.
5. Eval: add the tool to the expected tools of the labelled items that need it; run the eval subset.

Talk about: narrow tool vs bigger one (context size, citable evidence, recall is measurable).

## Classification label or category

1. Enum + the field description/prompt that defines it, including the **boundary** with its nearest neighbour.
2. Labelled examples (respect any minimum per class in the data tests).
3. Classification-only eval run: cheap and fast; report accuracy and confidence calibration if available.

## Guardrail

1. Deterministic first (regex/rules) for explicit cases; LLM yes/no question only for what rules miss.
2. Test both: a hit, a near-miss that must pass (information vs commitment).
3. Decide block vs escalate vs flag; a failing checker fails **closed** (escalate) and leaves a mark in the trace.

Talk about: false positives cost reviewer time, false negatives cost customers — which one this domain can afford.

## Router / escalation rule

1. **Check the labels first.** Does the eval data agree with the new rule? If labelled items contradict it, show the user before coding.
2. Check the reliability of the signal you route on (per-field accuracy, confidence). Routing on a weak field needs a confidence threshold.
3. Prefer **flag** (human sees a marker) over **drop** (no draft) unless the data says otherwise.
4. Pure function + test; wire it at the routing point; measure escalation correctness before and after.

## Eval scorer

1. Deterministic scorer first, reusing the production rule (no duplicated regex).
2. Unit-test the scorer with a hit and a near-miss.
3. Register it with the experiment runner; aggregate as a mean.
4. LLM-as-judge only for what rules can't check; validate the judge against a stronger one or human labels before trusting it.

Talk about: guardrail blocks in production, eval measures on the dataset; when they disagree, one of them has a hole.

## Outbound integration (Slack, email, ticketing API)

1. Pure `should_send(result)` + `format(result)`; injected `send`.
2. Wire after the result is persisted, inside a try/except: the integration failing never breaks the pipeline.
3. Config from env; missing config = no-op recorded in the trace.
4. Idempotency key (don't notify twice), minimal personal data in the message, a link back to the human UI.

## Inbound integration (webhook)

1. Verify the signature (HMAC over timestamp + body) before parsing; test with a fixed payload and a signature computed in the test.
2. Map the external payload to the internal input model; reject unknown tenants.
3. Return fast (202), process in the background; the external id is the idempotency key.

## Scheduled LLM job (daily digest, trends)

1. Deterministic aggregation, tested on a temporary database.
2. One LLM call that only **writes** the summary from the aggregate — never computes numbers.
3. Scheduling is infra (cron, platform trigger): name it, don't build it live.

Talk about: "some problems need an agent, some need 20 lines of code".

## Exposing tools over MCP

1. Wrap the existing tool registry; one MCP tool per registry entry, no duplicated logic.
2. MCP clients pass the entity id the internal agent never needed: that needs per-user authorization and an audit log. Say it.
3. Test by calling the wrapped functions directly; demo with `claude mcp add`.

## Feedback loop (human decisions → dataset)

1. Pure rule: which reviews become eval items (rejected, or edit distance over a threshold).
2. Create the item linked to its source trace; labels come later from a human.

## Model, prompt or threshold A/B

1. Change only the config value or a non-production prompt label.
2. Run the same eval subset on both; compare quality, cost and latency in one table.
3. Promote only with repeated runs; record the decision and the numbers.
