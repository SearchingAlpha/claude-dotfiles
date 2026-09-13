#!/usr/bin/env python3
"""
harness.py — CLI Universal de Harness para Desarrollo con IA.
Agnóstico de IDE: funciona en Cursor, Antigravity, VS Code, Windsurf o terminal.

Subcomandos:
    repomap   [dir]        Extrae el mapa semántico de símbolos del proyecto.
    check     [dir]        Verifica sintaxis (Python/TS/JS) antes de commitear (SWE-agent gate).
    eval      [comando...] Ejecuta tests/aserciones de forma adversarial y emite veredicto.
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
HARNESS_DIR = TOOLS_DIR.parent


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

    if status == "FAIL":
        print("\n[ADVICE] La prueba fallo. Revisa el STDERR arriba y corrige el fallo antes de dar la tarea por completada.")
        return proc.returncode

    print("\n[VERIFIED] Evidencia comprobada objetivamente. Listo para cerrar la tarea.")
    return 0


def cmd_architect(args):
    """Muestra la guía y plantilla obligatoria del Modo Arquitecto."""
    goal = " ".join(args.goal) if args.goal else "[Objetivo de la tarea]"
    template = f"""
================================================================================
PROTOCOLO MODO ARQUITECTO (PENSAR PRIMERO, EDITAR DESPUES)
================================================================================
Objetivo: {goal}

REGLA OBLIGATORIA: NO MODIFICAR NINGUN ARCHIVO DE CODIGO TODAVIA.

Estructura requerida de la propuesta para el usuario:
--------------------------------------------------------------------------------
### Diagnostico e Intencion
[Explicacion clara del problema o feature y el criterio de exito]

### Seams y Modulos Afectados
- `fichero_a_modificar.ext` (funcion/clase): [descripcion del cambio]
- `nuevo_fichero.ext`: [proposito]

### Diff Conceptual / Estrategia
```
[Fragmento clave del cambio propuesto sin boilerplate excesivo]
```

### Plan de Verificacion (Comando exacto de test)
`python harness.py eval <comando_para_probar_el_cambio>`

### Pregunta de Aprobacion
"Apruebas esta arquitectura para proceder a la edicion?"
================================================================================
"""
    print(template)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Harness Universal de Desarrollo con IA")
    subparsers = parser.add_subparsers(dest="subcommand", help="Comando a ejecutar")

    # repomap
    p_map = subparsers.add_parser("repomap", help="Generar mapa de simbolos del proyecto")
    p_map.add_argument("dir", nargs="?", default=".", help="Directorio raiz")
    p_map.add_argument("--max-lines", type=int, default=150, help="Lineas maximas")

    # check
    p_check = subparsers.add_parser("check", help="Comprobar sintaxis y compilacion basica")
    p_check.add_argument("dir", nargs="?", default=".", help="Directorio a comprobar")

    # eval
    p_eval = subparsers.add_parser("eval", help="Ejecutar comprobacion adversarial")
    p_eval.add_argument("command", nargs=argparse.REMAINDER, help="Comando a ejecutar")

    # architect
    p_arch = subparsers.add_parser("architect", help="Mostrar guia del Modo Arquitecto")
    p_arch.add_argument("goal", nargs=argparse.REMAINDER, help="Descripcion del objetivo")

    args = parser.parse_args()
    if not args.subcommand:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "repomap": cmd_repomap,
        "check": cmd_check,
        "eval": cmd_eval,
        "architect": cmd_architect,
    }

    exit_code = dispatch[args.subcommand](args)
    sys.exit(exit_code or 0)


if __name__ == "__main__":
    main()
