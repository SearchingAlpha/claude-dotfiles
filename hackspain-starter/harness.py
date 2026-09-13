#!/usr/bin/env python3
"""
harness.py — CLI Universal de Harness para Desarrollo con IA en HackSpain.
Agnóstico de IDE: funciona en Cursor, Antigravity, VS Code, Windsurf o terminal.

Subcomandos:
    repomap   [dir]        Extrae el mapa semántico de símbolos del proyecto (<2k tokens).
    check     [dir]        Verifica sintaxis (Python/TS/JS) antes de commitear (SWE-agent gate).
    eval      [comando...] Ejecuta tests/aserciones de forma adversarial y emite veredicto.
    init      [dir]        Inyecta reglas multi-IDE (AGENTS.md, Cursor rules, Copilot).
    architect [idea...]    Muestra la plantilla y directrices del Modo Arquitecto.
"""

from __future__ import annotations

import argparse
import io
import os
import py_compile
import subprocess
import sys
import time
from pathlib import Path

# Configurar encoding seguro en Windows para evitar charmap UnicodeEncodeError
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

TOOLS_DIR = Path(__file__).resolve().parent


def cmd_repomap(args):
    """Ejecuta el generador de mapa de símbolos."""
    from repomap import generate_repomap
    target = Path(args.dir).resolve()
    if not target.exists():
        print(f"Error: {target} no existe.", file=sys.stderr)
        return 1
    print(generate_repomap(target, max_lines=args.max_lines))
    return 0


def cmd_check(args):
    """Verificación sintáctica y de higiene de código (puerta SWE-agent)."""
    target = Path(args.dir).resolve()
    print(f"[CHECK] Verificando sintaxis en: {target.name}...")
    errors = 0
    checked = 0

    # 1. Comprobar sintaxis Python
    for py_file in target.rglob("*.py"):
        if any(part.startswith(".") or part in {"node_modules", "venv", ".venv", "dist"} for part in py_file.parts):
            continue
        checked += 1
        try:
            py_compile.compile(str(py_file), doraise=True)
        except py_compile.PyCompileError as e:
            print(f"[ERROR] Error de sintaxis en {py_file.relative_to(target)}:\n   {e.msg}", file=sys.stderr)
            errors += 1

    print(f"\nArchivos Python analizados: {checked}. Errores encontrados: {errors}")
    if errors > 0:
        print("[FAIL] Fallo de compilacion: Corrige los errores sintacticos antes de continuar.", file=sys.stderr)
        return 1
    
    print("[OK] Sintaxis verificada correctamente. Sin errores de compilacion.")
    return 0


def cmd_eval(args):
    """Ejecuta una comprobación adversarial y formatea las evidencias objetivas."""
    cmd = args.command
    if not cmd:
        print("Uso: python harness.py eval <comando de test/validacion...>")
        return 1

    cmd_str = " ".join(cmd)
    print(f"[EVALUATOR ADVERSARIAL] Ejecutando: {cmd_str}\n" + "-" * 60)
    
    start = time.time()
    proc = subprocess.run(cmd_str, shell=True, capture_output=True, text=True)
    duration = time.time() - start

    status = "PASS" if proc.returncode == 0 else "FAIL"

    print("\n" + "=" * 60)
    print(f"[{status}] VEREDICTO DE EVALUACION (Exit code: {proc.returncode}, {duration:.2f}s)")
    print("=" * 60)

    if proc.stdout.strip():
        print("\n--- STDOUT ---")
        print(proc.stdout.strip()[:2000])

    if proc.stderr.strip():
        print("\n--- STDERR ---")
        print(proc.stderr.strip()[:2000])

    return proc.returncode


def cmd_architect(args):
    """Muestra el protocolo del Modo Arquitecto."""
    print("""
======================================================================
 MODO ARQUITECTO: PENSAR PRIMERO, EDITAR DESPUÉS
======================================================================
Directriz para agentes de IA:
1. PROHIBIDO editar o crear ficheros en el primer turno.
2. Analizar el problema y generar:
   - Diagnóstico del problema y seams afectados.
   - Ficheros y funciones exactas a modificar.
   - Diff conceptual y trade-offs.
   - Comando de verificación adversarial exacto.
3. Pedir confirmación explícita al desarrollador antes de aplicar cambios.
======================================================================
""")
    return 0


def cmd_init(args):
    """Inyecta las reglas de arnés multi-IDE en el directorio objetivo."""
    target = Path(args.dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    
    agents_path = target / "AGENTS.md"
    claude_path = target / "CLAUDE.md"
    cursor_dir = target / ".cursor" / "rules"
    github_dir = target / ".github"

    cursor_dir.mkdir(parents=True, exist_ok=True)
    github_dir.mkdir(parents=True, exist_ok=True)

    # 1. AGENTS.md
    agents_content = f"""# Agent Instructions & Harness Protocols — HackSpain 2026

This project uses standard AI development harness protocols.
All coding agents (Cursor, Claude Code, Copilot, Antigravity) must follow these rules.

---

## 1. Orientation Protocol (Repo-Map)
Before searching or reading files blindly across the codebase, generate a symbol map:
```bash
python harness.py repomap .
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
Before finishing any modification or committing:
```bash
python harness.py check .
```

---

## 4. Adversarial Evaluation (Zero Trust)
Before marking any task as complete:
```bash
python harness.py eval <test_command>
```
Report the objective PASS/FAIL verdict and evidence.
"""
    agents_path.write_text(agents_content, encoding="utf-8")
    claude_path.write_text(agents_content, encoding="utf-8")

    # 2. Cursor Architect rule
    architect_mdc = """---
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
   - Conceptual diff and trade-offs.
   - Test command to verify the change.
3. Explicitly ask for user approval before writing code.
"""
    (cursor_dir / "architect.mdc").write_text(architect_mdc, encoding="utf-8")

    # 3. Copilot instructions
    (github_dir / "copilot-instructions.md").write_text(agents_content, encoding="utf-8")

    print(f"[OK] Harness multi-IDE inicializado en: {target}")
    print(f"  + {agents_path.name}")
    print(f"  + {claude_path.name}")
    print(f"  + .cursor/rules/architect.mdc")
    print(f"  + .github/copilot-instructions.md")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="harness.py — CLI Universal de Harness para Desarrollo con IA",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcomando a ejecutar")

    # repomap
    p_repomap = subparsers.add_parser("repomap", help="Extrae el mapa semántico de símbolos")
    p_repomap.add_argument("dir", nargs="?", default=".", help="Directorio a indexar")
    p_repomap.add_argument("--max-lines", type=int, default=160, help="Límite máximo de líneas")

    # check
    p_check = subparsers.add_parser("check", help="Verifica sintaxis (SWE-agent pre-commit gate)")
    p_check.add_argument("dir", nargs="?", default=".", help="Directorio a comprobar")

    # eval
    p_eval = subparsers.add_parser("eval", help="Ejecuta una validación adversarial")
    p_eval.add_argument("command", nargs=argparse.REMAINDER, help="Comando de test a ejecutar")

    # architect
    p_arch = subparsers.add_parser("architect", help="Muestra directrices del Modo Arquitecto")

    # init
    p_init = subparsers.add_parser("init", help="Inyecta reglas de arnés multi-IDE")
    p_init.add_argument("dir", nargs="?", default=".", help="Directorio objetivo")

    args = parser.parse_args()

    if args.subcommand == "repomap":
        sys.exit(cmd_repomap(args))
    elif args.subcommand == "check":
        sys.exit(cmd_check(args))
    elif args.subcommand == "eval":
        sys.exit(cmd_eval(args))
    elif args.subcommand == "architect":
        sys.exit(cmd_architect(args))
    elif args.subcommand == "init":
        sys.exit(cmd_init(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
