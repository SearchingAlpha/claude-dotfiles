---
name: writing-docs
description: Write or improve documentation — READMEs, user guides, architecture notes, reference pages — grounded in the code and illustrated with Mermaid diagrams that render on GitHub. Use when the user asks to document, explain or diagram a project, tool or flow, or to restructure, fact-check or add diagrams to existing docs.
---

Documentation fails in three ways: it answers a question nobody reading it has (wrong reader), it says things the code no longer does (drift), and it explains in paragraphs what one picture would show (or draws pictures nobody can read). Each step below closes one of them. Every doc you write is **grounded**: each command, flag, default, path and number in it has a source you checked — a line of code or the output of a command you ran.

## Steps

1. **Pin the reader.** For each doc, write down one reader (user/operator, contributor, integrator), the job they're doing when they open it, and its kind in **Diátaxis** terms — tutorial (learn by doing), how-to (get a task done), reference (look a fact up), explanation (understand why). A README is the entry point: what it is and why in three sentences, one picture, the quick start, then links. Anything a second reader needs goes to its own file in `docs/`. Done when every planned doc has exactly one reader, one job and one kind, and no two docs share all three.
2. **Inventory the ground truth.** Read the existing docs and the code they describe. List every claim the new docs will make — commands, flags, defaults, file paths, limits, timings, state names — each with its source (`file:line` or a command and its output). Run every command you intend to show. Mark each claim already in the docs as current, stale or missing. Done when every claim has a source and every stale one is on the list to fix.
3. **Outline.** Headings first, each phrased as the reader's question or task ("Enroll a repo", "What happens when two agents want the same file"). Order by the reader's path, not the code's structure. Tables for lookups, numbered lists for procedures, prose only for reasoning. Done when the outline reads as a path through the reader's job with no heading that serves a different reader.
4. **Draw.** Decide where a diagram replaces prose, using [MERMAID.md](MERMAID.md) to pick the type and follow the rendering rules. One idea per diagram, introduced by a sentence that states the question it answers, using the same names as the prose and the code. Done when each section that describes components, an ordered interaction, states or a decision has either a diagram or a reason it doesn't need one.
5. **Write.** Fill the outline from the inventory only — nothing that isn't grounded, no roadmap promises, limits stated plainly. Present tense; "you" for the reader. Real commands with real output, copy-pasteable. Say each fact once and link to it elsewhere (one source of truth): a README doesn't restate the reference, it points at it. Follow the repo's language conventions for each file. Done when every inventory claim used is in the text and nothing in the text is outside the inventory.
6. **Check.** Run the checker over every file you touched:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/check_docs.py" <files-or-dirs> --png <scratch-dir>
   ```

   It renders each Mermaid block with mermaid-cli (GitHub's engine), flags GitHub-hostile syntax and broken relative links. Then open every PNG it wrote and fix diagrams whose edges cross badly, labels collide or that need scrolling. Done when the checker exits 0 and you have looked at every new or changed diagram.
7. **Link it in.** The README links every deeper doc; a new doc is reachable from the README in at most two clicks; indexes or tables that list docs or skills (repo README, sidebars) include the new ones. Done when no new doc is orphaned.

## When improving existing docs

Run step 2 first and fix every stale claim before restructuring — a well-organised wrong doc is worse than a messy right one. Keep anchors that other pages link to, or update the links (the checker finds broken relative links).
