# Orient in an unfamiliar repo (≤5 min)

Do not edit yet. Do these in order, narrating each:

1. **Written context.** `README*`, `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING*`, `docs/`. If none exist, say so: it is the first thing you would add.
2. **Run and test.** Find the test command (`pyproject.toml`, `package.json`, `Makefile`, `.github/workflows/`). Run it once; note the time and the result. Without a green baseline you cannot tell whether you broke something.
3. **Map.** `/repomap`, or `git ls-files | head -100` and `git log --oneline -15`. Find the entry points (HTTP routes, CLI, handlers), the data model, and where the LLM is called and traced.
4. **Ten-line summary** to the user: what it does, how it is tested, where the seam for the challenge is, what risk you see.
5. **Minimal harness, if missing.** Propose the `agent-harness` setup (check command + after-edit test hook). Add it only with the user's OK — on someone else's repo, ask the owner.

Done when the user has the summary and a green (or explicitly red) baseline. Then go to step 1 of [SKILL.md](SKILL.md).
