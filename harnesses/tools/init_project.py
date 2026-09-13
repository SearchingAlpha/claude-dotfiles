#!/usr/bin/env python3
"""
init_project.py — Generador de configuración de Harness Multi-IDE para proyectos.
Inyecta las reglas de AGENTS.md, Cursor (.cursor/rules/) y Copilot (.github/)
en cualquier directorio para que los harnesses funcionen en cualquier editor.

Uso:
    python init_project.py [ruta-del-proyecto]
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

# Configurar encoding seguro en Windows para evitar charmap UnicodeEncodeError
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).resolve().parent
HARNESS_PATH = (TOOLS_DIR / "harness.py").as_posix()


AGENTS_MD_TEMPLATE = f"""# Agent Instructions & Harness Protocols

This project uses standard AI development harness protocols.
All coding agents (Antigravity, Cursor, Claude Code, Copilot, Codex) must follow these rules.

---

## 1. Orientation Protocol (Repo-Map)
Before searching or reading files blindly across the codebase, generate a symbol map:
```bash
python "{HARNESS_PATH}" repomap .
```
Use the output to understand existing classes, functions, and interfaces without wasting context tokens.

---

## 2. Architect Protocol (Think First, Edit Later)
When asked for a feature, architectural change, or non-trivial bug fix:
- ⛔ **NEVER edit or create code files on your first response.**
- Analyze the codebase, identify affected *seams*, and produce:
  1. Diagnostic & Intent.
  2. Affected files and functions.
  3. Conceptual diff / strategy.
  4. Specific test command to verify the change.
- Ask the user: *"Do you approve this architecture so I can proceed to apply the edits?"*
- Only apply modifications once the user confirms.

---

## 3. Pre-Commit Syntax Check (SWE-agent Gate)
Before finishing any modification, verify that no syntax or compilation errors were introduced:
```bash
python "{HARNESS_PATH}" check .
```

---

## 4. Adversarial Evaluation (Zero Trust)
Before marking any task as complete or declaring success:
- Do not trust your own code assumptions.
- Run the actual verification command and observe stdout/exit codes:
```bash
python "{HARNESS_PATH}" eval <test_command>
```
Report the objective PASS/FAIL verdict and evidence.
"""


CURSOR_ARCHITECT_MDC = """---
description: Activates Architect Mode — think and plan before touching any files
globs: *
alwaysApply: false
---

# Architect Mode Protocol

When activated (@architect or user requests a design/feature):
1. DO NOT edit or create any files in this turn.
2. Formulate a technical proposal with:
   - Problem diagnosis and target seams.
   - Files and functions to modify.
   - Conceptual diff snippet.
   - Test command to verify.
3. Ask the user for explicit approval before editing code.
"""


CURSOR_EVAL_MDC = f"""---
description: Adversarial verification of changes before marking done
globs: *
alwaysApply: false
---

# Adversarial Evaluation Protocol

1. Assume an independent, skeptical Evaluator role.
2. Run the real verification command:
   `python "{HARNESS_PATH}" eval <test_command>`
3. Check exit code and actual outputs.
4. Report PASS or FAIL with evidence.
"""


CURSOR_REPOMAP_MDC = f"""---
description: Generates a symbol map of classes and functions in the repo
globs: *
alwaysApply: false
---

# Repo-Map Extraction

To inspect the repo architecture without loading full files:
Run:
```bash
python "{HARNESS_PATH}" repomap .
```
"""


COPILOT_INSTRUCTIONS = f"""# Copilot & Coding Agent Instructions

1. **Architect First:** Before generating extensive code changes, outline the affected files, classes, and conceptual diffs. Do not rewrite files prematurely.
2. **Repo Map:** When exploring the architecture, use `python "{HARNESS_PATH}" repomap .` to see existing signatures.
3. **Syntax Hygiene:** Always verify syntax before committing: `python "{HARNESS_PATH}" check .`.
4. **Verification:** Validate code behavior by running tests through `python "{HARNESS_PATH}" eval <test_cmd>`.
"""


def setup_project(target_dir: Path):
    target_dir = target_dir.resolve()
    print(f"[SETUP] Configurando Harness Multi-IDE en: {target_dir}")

    # 1. AGENTS.md (Universal)
    agents_file = target_dir / "AGENTS.md"
    if not agents_file.exists():
        agents_file.write_text(AGENTS_MD_TEMPLATE, encoding="utf-8")
        print("  [OK] Creado: AGENTS.md (Universal para Antigravity, Claude, Copilot, etc.)")
    else:
        # Añadir bloque si no existe
        current = agents_file.read_text(encoding="utf-8")
        if "Harness Protocols" not in current:
            agents_file.write_text(current + "\n\n" + AGENTS_MD_TEMPLATE, encoding="utf-8")
            print("  [UPDATE] Actualizado: AGENTS.md (Protocolos de Harness anadidos)")
        else:
            print("  [INFO] AGENTS.md ya contiene los protocolos.")

    # 2. Cursor Rules (.cursor/rules/)
    cursor_rules_dir = target_dir / ".cursor" / "rules"
    cursor_rules_dir.mkdir(parents=True, exist_ok=True)
    
    (cursor_rules_dir / "architect.mdc").write_text(CURSOR_ARCHITECT_MDC, encoding="utf-8")
    (cursor_rules_dir / "eval.mdc").write_text(CURSOR_EVAL_MDC, encoding="utf-8")
    (cursor_rules_dir / "repomap.mdc").write_text(CURSOR_REPOMAP_MDC, encoding="utf-8")
    print("  [OK] Creado: .cursor/rules/ (architect.mdc, eval.mdc, repomap.mdc)")

    # 3. GitHub Copilot (.github/copilot-instructions.md)
    github_dir = target_dir / ".github"
    github_dir.mkdir(parents=True, exist_ok=True)
    copilot_file = github_dir / "copilot-instructions.md"
    if not copilot_file.exists():
        copilot_file.write_text(COPILOT_INSTRUCTIONS, encoding="utf-8")
        print("  [OK] Creado: .github/copilot-instructions.md (Para VS Code + Copilot)")

    print("\n[DONE] Configuracion completada. Tu proyecto ahora soporta los harnesses en cualquier IDE.")


def main():
    parser = argparse.ArgumentParser(description="Inicializa las reglas de harness multi-IDE en un proyecto.")
    parser.add_argument("dir", nargs="?", default=".", help="Ruta del proyecto a configurar")
    args = parser.parse_args()

    project_dir = Path(args.dir)
    if not project_dir.exists():
        print(f"Error: {project_dir} no existe.", file=sys.stderr)
        sys.exit(1)

    setup_project(project_dir)


if __name__ == "__main__":
    main()
