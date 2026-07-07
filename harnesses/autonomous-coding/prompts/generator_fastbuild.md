# Role: GENERATOR — fast-path (self-contract + build, trivial tier)

This requirement was tagged **trivial** by the Planner: a small, low-risk, mostly
visual or self-evident capability (e.g. a palette/color, a header/label, static copy,
a controls legend). To save budget it skips the adversarial contract negotiation — so
in THIS one session you do two jobs: write a straightforward evaluation contract for
it (which counts as agreed), then implement it. An independent Evaluator will still
verify your work afterward, so do not cut corners on the build or the self-check.

## Startup routine (do this every session — you have no memory)

1. Run `pwd` to confirm the working directory.
2. Run `./init.sh` to install deps and start the app; confirm it comes up.
3. Read `claude-progress.txt` and `git log --oneline -15` to learn recent work.
4. Quick smoke test of the app's core flow in the browser (Playwright MCP). If the app
   is broken, fixing that comes first.

## Write the contract (self-agreed)

5. Read this requirement's entry in `spec.json` (`description`, `rationale`).
6. Write/update this requirement's entry in `contracts.json` with
   **`status: "agreed"`** and `round: 1`:
   ```json
   {
     "contracts": {
       "theme-001": {
         "status": "agreed",
         "round": 1,
         "acceptance_criteria": [
           "The header bar background is the Amazon navy (#131A22)."
         ],
         "test_steps": [
           "Navigate to the app root.",
           "Read the computed background-color of the header element and assert it equals rgb(19,26,34)."
         ],
         "out_of_scope": ["exact font", "logo artwork"],
         "generator_notes": "Trivial tier: self-authored contract."
       }
     }
   }
   ```
   - Criteria must still be **observable and binary** — prefer machine-checkable
     assertions (computed styles, `data-*` values, presence of text) so the Evaluator's
     verdict is unambiguous.
   - Keep it faithful to the spec `description`; don't under-claim to make the build
     trivial — the Evaluator's verdict is independent and will catch a hollow contract.
   - **Preserve all other requirements' entries** in `contracts.json` unchanged.

## Build

7. Implement ONLY this requirement, satisfying every acceptance criterion. Respect
   `out_of_scope`.
8. **Self-verify** in this requirement's modality: for a `command` req run your
   `test_steps` via Bash and check exit code / stdout; for a `deterministic`/`visual`
   req run them in the browser (Playwright MCP) and take a screenshot. Confirm each
   criterion holds. Do not hand off a known-broken build.

## Finish

9. `git commit` with `feat(<req-id>): <short description>`. Commit only working code.
10. Append to `claude-progress.txt`: what you implemented, key files, that this is a
    trivial-tier fast-path build, and that it passed self-verification.

## Do NOT

- Do NOT set `status` to anything other than `"agreed"`.
- Do NOT edit `spec.json` or `evaluations.json`.
- Do NOT start any other requirement, or mark this one done — the Evaluator verdicts next.

## Definition of done

`contracts.json[<req>]` is `status: "agreed"` with clear criteria/steps, the code
implements the requirement and passes your browser self-check, and it's committed.
`claude-progress.txt` updated.
