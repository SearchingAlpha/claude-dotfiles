# Agent instructions — <project name>

Read `CONTRACT.md` before doing anything. It has the one-liner, the demo
moment, the schema, the interfaces, the ownership map and the NOT-doing list.
If your task conflicts with it, stop and say so instead of improvising.

(Claude Code reads `CLAUDE.md`; keep it a symlink/copy of this file:
`ln -s AGENTS.md CLAUDE.md` o copia directa en Windows.)

## Harness Protocols (Multi-IDE: Cursor / Claude Code / Copilot / Antigravity)

1. **Orientation Protocol (Repo-Map)**:
   Before reading files blindly or guessing symbols across the repo:
   ```bash
   python harness.py repomap .
   ```
   Understand existing classes, functions, and interfaces without wasting context tokens.

2. **Architect Protocol (Think First, Edit Later)**:
   When asked for a feature, architectural change, or non-trivial bug fix:
   - ⛔ **DO NOT edit or create code files on your first response.**
   - Produce diagnosis, affected files/functions, conceptual diff, and test command.
   - Ask for confirmation before editing.

3. **Pre-Commit Syntax Check (SWE-agent Gate)**:
   Before finishing any modification or declaring done:
   ```bash
   python harness.py check .
   ```
   Zero syntax errors allowed. Never commit broken syntax.

4. **Zero-Trust Adversarial Evaluation**:
   Before declaring a feature complete, run the verification command:
   ```bash
   python harness.py eval python check.py
   ```

## Rules

1. **One change per task. Small diffs.** Don't refactor code you don't own.
2. **The schema in `fixtures/` is canonical.** Don't add or rename fields.
   If you need one, say so; the owner updates the fixture first.
3. **Run `python check.py` (o `./check.sh`) before declaring done** and paste its output.
4. **Don't touch** `CONTRACT.md`, `fixtures/`, `check.py`, `check.sh`, `AGENTS.md`, or
   modules owned by someone else (ownership map in the contract) unless the
   task explicitly says so.
5. **Commit when green**, one-line imperative message. Never commit a failing check.
6. **Prefer boring.** Use what is already in the repo before adding a dependency.
7. **Mocks are marked.** When the real API or model isn't ready, read the
   fixture and mark the spot `# MOCK` (or `// MOCK`) so it can be grepped.
8. **Stuck after two attempts?** Stop and report the exact error. Don't retry
   the same change a third time.

## How to run and check

- Run: `<command from CONTRACT.md>`
- Check: `python check.py` (o `./check.sh`)
- Repo Map: `python harness.py repomap .`

## Where things live

- `fixtures/` canonical data objects and demo data
- `<module>/` <owner> — <what it does>
- `<module>/` <owner> — <what it does>
