---
name: explaining-clearly
description: Use when writing a plan, designing or building a module, writing a spec, or explaining how code or a system works.
---

Every module has a **spec**: a markdown file with **Mermaid diagrams** and prose in **ASD-STE100** (Simplified Technical English), applied at 80%.

These rules override terse or "caveman" compression styles in specs, plans and explanations. STE keeps articles and complete sentences.

## 1. One spec per module

- Path: `docs/specs/<module>.md`, plus `docs/specs/overview.md` for the whole system. Use the project's convention if one exists.
- Write or update the spec **before** you write or change the module's code. The spec is the contract; the code implements it.
- A plan links to the spec of each module that it touches.
- When you explain a module in chat, point to its spec and explain from it. Do not draw diagrams in chat.

Template:

```markdown
# <module>

## Purpose
One or two sentences: what this module does and why it exists.

## Diagram
<Mermaid diagram(s) — see table>

## Interface
Inputs, outputs, types. Functions, endpoints or events that other modules use.

## Behavior
Numbered steps. Step N = node N in the diagram.

## Errors and edge cases
What fails, and what the module does then.

## Open decisions
Assumptions you made that the user must confirm. Omit if none.

## Done when
Checkable criteria: tests, eval items, observable results.
```

## 2. Mermaid diagrams

`overview.md` always has a `flowchart` of all modules and the data that moves between them. Each module spec has at least one diagram:

| What you explain | Mermaid type |
|---|---|
| Pipeline, decision, request flow | `flowchart TD` |
| Calls between modules or services, in time order | `sequenceDiagram` |
| Lifecycle, status changes | `stateDiagram-v2` |
| Data model, tables | `erDiagram` |
| Classes, interfaces | `classDiagram` |

Rules:
- Use real names: files, functions, services, thresholds (`classify.py`, `confidence < 0.6`).
- Label every branch with its condition: `A -->|confidence < 0.6| B`.
- Number the nodes from the entry point: `n1["1. classify()"]`. The Behavior steps use the same numbers.
- Max ~15 nodes per diagram. If it has more, split it into two diagrams.
- Put labels that contain `()`, `<`, `:` or `/` in double quotes, so that the Mermaid syntax stays valid.
- When the code changes, update the diagram in the same commit.

## 3. Prose in ASD-STE100 (80%)

- One sentence = one idea. Max **20 words** for an instruction, max **25** for a description.
- Use the active voice. Write instructions in the imperative.
- One paragraph = one topic, max 6 sentences. Use numbered lists for sequences.
- **One term = one meaning.** Pick one word for a concept and use only that word everywhere: specs, code names, chat.
- Use simple, common words. Do not use phrasal verbs or idioms.
- Max 3 nouns in a noun cluster. Do not use parentheses to add a second idea.

The other 20% is flexibility. Break a rule only for technical names, code identifiers, quoted text, or text the user wants verbatim. Write in the user's language; the rules apply to Spanish the same way.

## Done when

- [ ] Each module that the work touches has an up-to-date spec in `docs/specs/`.
- [ ] Each spec has at least one valid Mermaid diagram, and each branch has a condition label.
- [ ] The Behavior step numbers match the diagram node numbers.
- [ ] No sentence is longer than 25 words, except verbatim text.
- [ ] Each concept uses one term only.

## Common mistakes

| Mistake | Fix |
|---|---|
| Code first, spec "later" | Write the spec first. |
| One big diagram for the whole system in each spec | Overview in `overview.md`. Each module spec shows only its own flow. |
| The diagram is not updated after a code change | Update it in the same commit. |
| `A[classify()]`: the Mermaid parse fails | `A["classify()"]` |
| "Escalate", then "hand off", then "send to a human" | Use one term. |
