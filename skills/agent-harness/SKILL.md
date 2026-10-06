---
name: agent-harness
description: Make a repo agent-ready — one check command, an after-edit test hook, permissions, and a CLAUDE.md with commands, rules, seams and gotchas. Use when the user wants to set up or audit a harness for coding agents, add a hook that runs tests, or before live-coding on a repo without one.
---

# Agent harness

A coding agent is only as reliable as the feedback it gets. The **harness** is the part of the repo that gives that feedback without a human: it tells the agent what the rules are, and tells it — fast and automatically — when it broke something. Four pieces, each small; set up only what is missing.

Read the repo first: `CLAUDE.md`/`AGENTS.md`, the test command, the CI config. Never overwrite an existing file without showing the diff to the user.

## Steps

### 1. Baseline

Run the test suite once and time it. Done when you know the command, the duration and whether it is green. If it takes more than ~10 s or calls real LLMs/networks, the after-edit hook (step 3) must run a fast subset: agree which with the user (`HARNESS_TEST_CMD`).

### 2. One check command

A single command that answers "is it done?" and exits non-zero otherwise: the offline tests, plus the repo's mechanical rules (spec updated with code, lint, types — only what the repo already cares about). Optional flags for the expensive proofs: one real run on one input with its trace URL, a small eval subset. Write it in the repo's language (e.g. `scripts/check.py`), print `CHECK PASS n/n` or `CHECK FAIL`. Done when it passes on a clean tree and fails when you break a test on purpose (then revert).

### 3. After-edit hook

Copy [scripts/after_edit.py](scripts/after_edit.py) to `.claude/hooks/after_edit.py` and register it in `.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Edit|Write|MultiEdit",
      "hooks": [{"type": "command", "timeout": 60,
                 "command": "cd \"$CLAUDE_PROJECT_DIR\" && <runner> python .claude/hooks/after_edit.py"}]
    }]
  }
}
```

`<runner>` makes `sys.executable` the project's interpreter (`uv run`, `poetry run`, or nothing for a plain venv). Test it by piping a fake event: `echo '{"tool_input":{"file_path":"'$PWD'/<file>"}}' | <command>` → exit 0 when green; exit 2 after breaking a test elsewhere; exit 0 with a "Red" note when only a newly written test fails. Done when all three cases behave.

### 4. Permissions

In `.claude/settings.json`, allow the commands the agent runs every few minutes (tests, check, read-only git, `git add`/`commit`), and deny what must never happen unattended: reading `.env`, `git push`, `reset --hard`, deploys. Done when a normal loop runs without permission prompts and the denials are listed.

### 5. CLAUDE.md

Keep it short and true. Sections: **Commands** (including the check), **Rules** (numbered; each one the agent can follow or the check can verify), **Seams and gotchas** — the non-obvious places a change must touch (allow-lists, enums, minimum data per class, prompt labels, cache-sensitive ordering). Every line must be checked against the code: delete stale lines and references to skills or tools that are not installed. Done when every command in it runs and every file it names exists.

## Talking points

When someone asks why the repo looks like this: the agent gets the rules from `CLAUDE.md`, the feedback from the hook, the definition of done from the check; the human reviews diffs and decides. That split is the workflow.
