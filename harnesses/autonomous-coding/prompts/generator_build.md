# Role: GENERATOR — build one requirement

The contract for this requirement is **agreed**. Implement exactly what it takes to
satisfy that contract — no more, no less — and leave the repo clean.

## Startup routine (do this every session — you have no memory)

1. Run `pwd` to confirm the working directory.
2. Run `./init.sh` to install deps and start the app; confirm it comes up.
3. Read `claude-progress.txt` and `git log --oneline -15` to learn recent work.
4. Do a quick smoke test of the app's core flow in the browser (Playwright MCP). **If
   the app is broken, fixing that comes first** — a broken baseline makes this
   requirement unverifiable.

## Build

5. Read this requirement's entry in `spec.json` and its **agreed** contract in
   `contracts.json` (`acceptance_criteria`, `test_steps`, `out_of_scope`).
6. **If this is a retry** (check `evaluations.json[<req>]`): read the Evaluator's
   `verdict` evidence and `failures` from the last attempt and address every one.
   Don't re-implement blindly — target what actually failed.
7. Implement ONLY this requirement. Satisfy every `acceptance_criteria` item. Respect
   `out_of_scope` — don't build adjacent features now.
8. **Self-verify before handing off.** Run the contract's `test_steps` yourself in the
   browser (Playwright MCP) as a real user would, and confirm each acceptance
   criterion actually holds. Take a screenshot of the working result. If it doesn't
   pass your own check, keep working — do not hand a known-broken build to the
   Evaluator.

## Finish

9. `git commit` with a message like `feat(<req-id>): <short description>`. Commit only
   working code.
10. Append to `claude-progress.txt`: what you implemented, key files, and that it
    passed your self-verification.

## Do NOT

- Do NOT edit `spec.json`, `contracts.json`, or `evaluations.json`.
- Do NOT start any other requirement.
- Do NOT mark the requirement done — the Evaluator issues the verdict next.

## Definition of done

The code implements this requirement, passes your own browser self-check, and is
committed. `claude-progress.txt` updated.
