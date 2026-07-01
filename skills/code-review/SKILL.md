---
name: code-review
description: Review the changes since a fixed point (commit, branch, tag, or merge-base) along two axes — Standards (does the code follow this repo's documented coding standards?) and Spec (does the code match what the originating issue/PRD asked for?). Runs both reviews in parallel sub-agents and reports them side by side. Use when the user wants to review a branch, a PR, work-in-progress changes, or asks to "review since X".
---

# Code Review — Two-Axis Review

Reviews changes between HEAD and a fixed point via `git diff <fixed-point>...HEAD` across two independent axes.

## The two axes

- **Standards** — conformance to repo coding conventions plus a fixed Fowler smell baseline
- **Spec** — faithfulness to the originating issue/PRD

They run as parallel sub-agents to avoid context contamination, then results are aggregated side-by-side.

## Process

### 1. Pin the fixed point

Validate that the ref resolves and that the diff is non-empty before spawning agents.

### 2. Find the spec

Scan commit messages for issue references (`#123`, `Closes #45`), then check user-supplied paths, then scan `docs/` or `specs/` folders. If none found, ask.

### 3. Find standards sources

Look for `CONTRIBUTING.md`, `CODING_STANDARDS.md`, or equivalent in the repo root and `docs/`.

### 4. Spawn both sub-agents in parallel

Each agent gets a focused brief (under 400 words each) and an isolated context window.

**Standards agent brief:** Check conformance against repo-documented standards. Supplement with the Fowler smell baseline below. Report hard violations from documented standards; report smells as soft findings only.

**Spec agent brief:** Check whether the diff faithfully implements the originating issue/PRD. Flag missing requirements, scope creep, and wrong implementations.

### 5. Aggregate

Present results under separate `## Standards` and `## Spec` headings. Do not merge or re-rank across axes.

End with a one-line summary: finding counts per axis and worst issue within each — no cross-axis winner declared.

## Fowler smell baseline (always applied)

Checked regardless of repo documentation. Always a judgement call, never a hard violation:

- Mysterious Name
- Duplicated Code
- Feature Envy
- Data Clumps
- Primitive Obsession
- Repeated Switches
- Shotgun Surgery
- Divergent Change
- Speculative Generality
- Message Chains
- Middle Man
- Refused Bequest

Two binding rules: *"The repo overrides"* and *"Always a judgement call."*

## Why separate axes?

Code that follows every standard but implements the wrong thing → Standards pass, Spec fail. Keeping them separate prevents one axis from masking the other.
