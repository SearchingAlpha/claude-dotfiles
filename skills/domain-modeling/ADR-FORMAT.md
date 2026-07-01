# ADR Format

ADRs are stored in `docs/adr/` with sequential naming like `0001-slug.md`. Create this directory only when the first ADR is needed.

## Core Template

Minimal by design — just a title and 1-3 sentences covering context, decision, and rationale. "An ADR can be a single paragraph."

```md
# {sequential number} — {slug}

{Context, decision, and rationale in 1–3 sentences.}
```

## Optional Sections

Include only when genuinely useful:

- **Status** frontmatter
- **Considered Options**
- **Consequences**

## When to Write One

All three criteria must apply:

1. **Hard to reverse** — the cost of changing your mind later is meaningful
2. **Surprising without context** — a future reader will wonder "why did they do it this way?"
3. **The result of a real trade-off** — there were genuine alternatives and you picked one for specific reasons

If reversible — skip it. If obvious — skip it. If there was no real alternative — skip it.

## What Qualifies

- Architectural patterns and technology choices with lock-in
- Integration patterns between system boundaries
- Scope/ownership decisions
- Deliberate deviations from the obvious path
- Constraints invisible in code (compliance, SLA requirements)
- Rejected alternatives where the rejection isn't self-evident

## Key Philosophy

The purpose is recording *that* a decision happened and *why* — not bureaucratic section-filling. Explicit "no" decisions can be as valuable as "yes" decisions.
