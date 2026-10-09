---
name: verify-gate
description: Give a repo one check command (`scripts/check --fast|--full`, ending in `CHECK PASS n/n` or `ERROR <reason>`) and a Stop hook that sends Claude back to work when a turn ends on a tree that fails it. Use when the user wants Claude to verify its own work before finishing, asks for a stop gate, a check command or "don't stop until the tests pass", prepares a repo for long unattended runs or /goal, or when a `stop_gate:` message appears.
---

# Verify gate

"Done" should be a command, not a feeling. This skill gives a repo one script that decides it, `scripts/check`, and a Stop hook that runs it whenever a turn ends on a tree that changed. A red tree goes back to Claude with the failing lines, as "Stop hook feedback", instead of back to the user. Turns that change nothing, like questions and reviews, cost a few git calls (about 60 ms) and don't run the check. Neither does a turn where only something else wrote (a dev server's log, the user's editor): the transcript shows Claude used read-only tools, or that every change came before Claude's first tool call that can write. The second case matters on Linux, macOS and WSL, where Claude searches with `grep` and `find` through Bash. What a background job Claude started wrote counts as Claude's once the job has finished, and so do edits from a turn that was cut off and then resumed or forked. A question turn that also changed the tree while it ran blocks once, and the message says which files changed and which can't be Claude's.

What ships here:

- [templates/check.sh](templates/check.sh): the `scripts/check` template, with slots for lint, types and tests.
- [scripts/stop_gate.py](scripts/stop_gate.py): the hook, copied to `<repo>/.claude/hooks/stop_gate.py`.
- [scripts/install_gate.py](scripts/install_gate.py): copies the hook and merges it into `<repo>/.claude/settings.json`. It is a dry run unless you pass `--apply`.
- [GATE.md](GATE.md): every decision the gate makes, the loop guard, state, log, cost and limits.

Read the repo before changing it: `CLAUDE.md`/`AGENTS.md`, the README and the CI config. Show the user every diff to a file that already exists before you write it.

## Steps

1. **Baseline.** Find the repo's lint, type and test commands, then run each once and time it. Done when you have, for every command, its duration, whether it is green, and whether it is deterministic: no network, no credentials, no model calls, no data missing from a fresh checkout. A red baseline is the user's call. Either fix it first, or gate only what is green today and name the known failures in a comment in `scripts/check`. Never gate a red baseline silently, because every turn would block.

2. **One check command.** If the repo has none, run `mkdir -p scripts && cp "${CLAUDE_SKILL_DIR}/templates/check.sh" scripts/check && chmod +x scripts/check`, or let step 3's installer do it with `--check-template`. Then fill its slots. Nothing is detected for you.
   - `--fast` must be offline and deterministic, and well under a minute, because the gate runs it after every turn that changed the tree.
   - `--full` is everything that is still deterministic and free: the whole suite, slow tests and structural data checks.

   If the repo already has a check (`make check`, `npm run check`, agent-harness's `scripts/check.py`), make `scripts/check` a thin wrapper that accepts `--fast|--full` and ends with the contract line. You can also point the gate at it with `--check`.

   Done when all of these hold:
   - `./scripts/check --fast` prints `CHECK PASS n/n` and exits 0 on the clean tree.
   - It exits 1 with an `ERROR …` last line after you break a test on purpose. Revert the break afterwards.
   - `./scripts/check --full` passes.

3. **Install the gate.** It needs Claude Code 2.1.163 or later; older versions ignore its block reply. Run `python3 "${CLAUDE_SKILL_DIR}/scripts/install_gate.py" <repo>`, where `<repo>` is the folder Claude Code is started in: a git work tree, or a subproject folder inside one (the gate then runs that folder's `scripts/check` and still hashes the whole work tree). The installer refuses a folder outside git. It prints the plan and the `settings.json` diff and writes nothing. Show the output to the user, and re-run with `--apply` once they agree. Options:

   | Flag | Default | Effect |
   | :- | :- | :- |
   | `--mode fast\|full` | `fast` | flag passed to `scripts/check` |
   | `--timeout S` | `240` | the check is killed after S seconds, and the turn is allowed with a notice |
   | `--max-failures N` | `3` | the gate blocks on failures 1 to N−1 in a prompt and lets failure N through with a notice; at least 2 |
   | `--watch PATH` | whole tree | only changes under these paths, relative to `<repo>`, count (repeatable); the installer flags a path that matches nothing |
   | `--check CMD` | `scripts/check` | shell command to run instead, from `<repo>` |
   | `--no-baseline` | off | skip the SessionStart handler (see [GATE.md](GATE.md#baselines)) |
   | `--check-template` | off | also create `scripts/check` from the template if it is missing |
   | `--remove` | | take the gate's handlers and hook file out again |

   Done when `.claude/settings.json` has the `Stop` handler and running the installer again reports `nothing to change`.

4. **Prove it.** Pipe Stop payloads through the command the installer registered, flags and all, the way Claude Code runs it, using a throwaway session id:

   ```bash
   cmd=$(python3 -c 'import json; print(next(h["command"] for g in json.load(open(".claude/settings.json"))["hooks"]["Stop"] for h in g["hooks"] if "/.claude/hooks/stop_gate.py" in h.get("command", "")))')
   p='{"session_id":"gate-smoke","prompt_id":"p1","hook_event_name":"Stop","cwd":"'"$PWD"'","stop_hook_active":false,"background_tasks":[]}'
   echo "$p" | CLAUDE_PROJECT_DIR="$PWD" sh -c "$cmd"; echo "exit $?"
   ```

   The gate always exits 0. It blocks by printing `{"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": …}}`, which Claude Code shows as feedback rather than as a hook error. Run it four times. Each run should give this result:
   1. On the green tree: `exit 0` and no output.
   2. After you break a test: the `additionalContext` reply, holding the check's last lines.
   3. Again, with nothing changed: a `systemMessage` saying nothing changed, and no block. This is the loop guard.
   4. After you revert the break: no output.

   Finish with `rm -f "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/verify-gate/state/gate-smoke.json"`. Done when all four runs behave as listed.

5. **Hand over.** Add `"Bash(./scripts/check *)"` and `"Bash(scripts/check *)"` to `permissions.allow` in `.claude/settings.json` so that Claude can run the check without a prompt. Any untracked file that isn't gitignored counts as a change, whoever writes it, so gitignore logs, local databases, editor swap files and the check's own output (`__pycache__/`, `.coverage`, `junit.xml`, `coverage.xml`, `htmlcov/`, `test-results/`), plus `.claude/worktrees/`. A report the check rewrites on every run costs one extra run per session: the next stop runs the check again, even on a question turn, sees the same files rewritten, says so once and from then on counts them as the check's output. A report with a new name on every run makes every stop run the check. Commit `scripts/check`, `.claude/hooks/stop_gate.py` and `.claude/settings.json` through the repo's normal review. Done when the user knows these four things:
   - Open sessions pick up the hooks through the settings file watcher.
   - Interactive sessions run project hooks once this folder or a parent folder is trusted. `claude -p` and SDK runs always run them, trusted or not, so every scripted run in this checkout is gated. Those runs don't apply the project's `permissions.allow` rules in a folder that was never trusted, so give scripted runs `--allowedTools 'Bash(./scripts/check *)'` to let Claude run the check.
   - `STOP_GATE=0` turns the gate off for one process. Export it in cron jobs and scripts that run `claude -p` and judge the result themselves. `--settings '{"disableAllHooks": true}'` turns off every hook for one run instead.
   - The log is `~/.claude/verify-gate/stop_gate.log`, next to the per-session state in `state/`.

## The verify habit

In a gated repo, the check is the definition of done. These rules hold for the whole session:

- After each change, run `./scripts/check --fast`. Before a commit or a PR, run `./scripts/check --full`. Quote the final line (`CHECK PASS n/n` or `ERROR …`) in your report. That line is the evidence. "Should work" is not.
- When the gate blocks, read the tail, fix the cause and run the check yourself before finishing. Never edit, skip, loosen or delete tests to get green. Never touch `scripts/check` to get green, and never set `STOP_GATE=0` yourself. If the block says background agents are still running and the failure is in files they are changing, wait for them instead of editing.
- If the failure predates your change (the user's uncommitted work, a branch you checked out, a pull), or needs the user (a service, credentials, data), say so plainly and stop. The gate gives up after 3 failures in a prompt, or as soon as the tree stops changing, and tells the user why.
- Only with the user's agreement, a project skill at `.claude/skills/verify/SKILL.md` makes Claude Code tell Claude to run it before each commit. This needs v2.1.286+, the skill must be model-invocable, and `includeGitInstructions` must be on. It replaces the bundled `/verify`, which builds and drives the app. So its body should run `./scripts/check --full` and then follow `.claude/skills/run-<name>/SKILL.md` if the repo has one. The user records that recipe once with `/run-skill-generator`, which only the user can invoke.

## Bounded /goal templates

`/goal <condition>` keeps Claude working until a small model judges the condition met. That model reads only the transcript, so every condition must name the command whose output proves it. Include a constraint and a turn bound. A skill cannot set a goal: give these to the user to type, in auto mode for unattended runs.

```text
/goal ./scripts/check --full exits 0 and no test file is modified, or stop after 20 turns
/goal a new test reproduces <bug> and fails before the fix, ./scripts/check --full exits 0 after it, or stop after 15 turns
/goal ./scripts/check --full prints the same CHECK PASS n/n as before the refactor and nothing under tests/ changed, or stop after 20 turns
/goal ./scripts/check --fast exits 0 without deleting, skipping or loosening any test, or stop after 10 turns
/goal <tool> <input> exits 0 and git diff --stat lists only files under <dir>, or stop after 10 turns
```

`/goal` and the gate are both Stop hooks and run in the same pass. If either one blocks, Claude continues. The gate decides "is the tree green?" and the goal decides "is the task done?". [GATE.md](GATE.md#loop-guard) explains why the gate's loop guard does not trust `stop_hook_active` when a goal is active.

## Calling this from agent-harness

agent-harness (PR #6) sets up the check command (its step 2) and an after-edit PostToolUse hook (its step 3), but it has no Stop step. Once both skills are merged, it can reuse this skill without copying any code:

- **Step 2:** emit `scripts/check` with this contract. The gate also accepts agent-harness's `CHECK FAIL` line as the error line, but prefer `ERROR <reason>`. Use the same fast command for the after-edit hook's `HARNESS_TEST_CMD`.
- **Stop step:** `python3 "${CLAUDE_PLUGIN_ROOT}/skills/verify-gate/scripts/install_gate.py" "$REPO"`, then `--apply` once the user agrees. Steps 4–5 above then prove the gate and hand it over.
- **Stable interface:** the flags above. Exit status is 0 on success and 1 on any error, usage errors and a folder outside git included. After an error, nothing the run wrote is left behind. The installer is idempotent. It recognises its own handlers by the command prefix it writes, `f="$CLAUDE_PROJECT_DIR/.claude/hooks/stop_gate.py"`, and leaves any other handler alone. Values go into the command as single tokens (`--check=CMD`), so one that starts with `-` stays a value. It refuses a `settings.json` that resolves outside the repo, writes through a symlink that stays inside it, and keeps the file's indent and line endings. The gate honours `HARNESS_CHECK_CMD` as an override for `scripts/check`, in line with agent-harness's `HARNESS_*` variables.

## Turning it off

- **One process:** `STOP_GATE=0 claude …`, or `claude -p --settings '{"disableAllHooks": true}' …` for one headless run. The check itself always runs with `STOP_GATE=0`, so a check that starts Claude can't gate itself.
- **One repo:** `python3 "${CLAUDE_SKILL_DIR}/scripts/install_gate.py" <repo> --remove`, then `--apply`.
- **Every hook:** `"disableAllHooks": true` in settings. This also disables `/goal`.
