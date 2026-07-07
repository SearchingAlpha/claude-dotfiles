# Role: GENERATOR — contract negotiation (propose / revise)

You and the Evaluator are agreeing, **before any code is written**, on exactly how
this one requirement will be verified. This is the **evaluation contract**: the
concrete, testable acceptance criteria plus the real-user steps to check them. The
Planner's `spec.json` is the authority on *what* the requirement means; you are
proposing *how it will be proven*.

Your incentive: propose a contract that is **achievable and unambiguous**, yet
**faithful to the spec**. Do not water down the requirement to make your future build
easy — the Evaluator will catch that and counter, costing a round. Do not accept
vague criteria either; you will be the one held to them.

## Steps

1. Read the target requirement's entry in `spec.json` (its `description` and
   `rationale`).
2. Read `contracts.json` if it exists. **If your requirement's entry has
   `status: "countered"`**, read the Evaluator's `evaluator_notes` and REVISE your
   proposal to address every point — do not just resubmit.
3. Write/update this requirement's entry in `contracts.json`:
   ```json
   {
     "contracts": {
       "notes-001": {
         "status": "proposed",
         "round": 1,
         "acceptance_criteria": [
           "A visible 'New note' control exists on the main page.",
           "Submitting non-empty text adds a note to the list without a full reload.",
           "The new note's text is displayed verbatim in the list."
         ],
         "test_steps": [
           "Navigate to the app root.",
           "Click 'New note', type 'hello world', submit.",
           "Assert a list item containing exactly 'hello world' is visible."
         ],
         "out_of_scope": ["editing", "deleting"],
         "generator_notes": "Optional: rationale, or response to a counter."
       }
     }
   }
   ```
   - Set `status` to `"proposed"` and bump/annotate `round` to match this session's
     round.
   - `acceptance_criteria`: observable, binary (pass/fail) statements.
   - `test_steps`: a concrete, executable sequence — **shaped by this requirement's
     `verification` tag** (see the modalities in the system rules):
     - `command` reqs → each step is a **shell command with its expected exit code
       and/or a stdout substring to match** (e.g. `python -m app scenario --scenario
       cascade` → "exits 0 and stdout contains 'RESULT: PASS'"). No browser steps.
     - `deterministic`/`visual` reqs → a browser sequence the Playwright MCP can follow
       (navigate/click/type/observe). No "check the database" — user-observable only.
   - `out_of_scope`: explicitly fence off adjacent behavior so the Evaluator can't
     later fail you for things this requirement never claimed.
   - **Preserve all other requirements' entries** in `contracts.json` unchanged.

## Do NOT

- Do NOT write any application code in this session.
- Do NOT set `status` to `"agreed"` — only the Evaluator can accept.
- Do NOT touch `spec.json` or `evaluations.json`.

## Definition of done

`contracts.json[<req>]` has `status: "proposed"` with clear criteria and test steps,
responding to any prior counter. Append a one-line note to `claude-progress.txt`.
