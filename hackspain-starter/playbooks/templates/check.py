#!/usr/bin/env python3
"""
check.py — Comprobación unificada de la Golden Path (Cross-platform: Windows, Mac, Linux).
Sustituto agnóstico de SO para check.sh.
Ejecuta:
  1. Comprobación sintáctica (SWE-agent gate).
  2. Tests unitarios del proyecto si existen.
  3. Prueba de la Golden Path sobre fixtures/.
"""

from __future__ import annotations

import io
import os
import py_compile
import subprocess
import sys
from pathlib import Path

# Configurar encoding seguro en Windows para evitar charmap UnicodeEncodeError
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent


def step_syntax() -> bool:
    print("[1/3] Verificando sintaxis de archivos...")
    errors = 0
    checked = 0
    for py_file in ROOT.rglob("*.py"):
        if any(part.startswith(".") or part in {"node_modules", "venv", ".venv", "dist"} for part in py_file.parts):
            continue
        checked += 1
        try:
            py_compile.compile(str(py_file), doraise=True)
        except py_compile.PyCompileError as e:
            print(f"  [FAIL] Error en {py_file.relative_to(ROOT)}:\n    {e.msg}", file=sys.stderr)
            errors += 1
    print(f"  Analizados {checked} archivos. Errores: {errors}")
    return errors == 0


def step_tests() -> bool:
    print("\n[2/3] Tests del proyecto...")
    # Si hay tests configurados, ejecutarlos. Si no, skip informativo.
    test_dir = ROOT / "tests"
    if test_dir.exists():
        res = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT)
        return res.returncode == 0
    print("  (Sin carpeta tests/ específica; omitiendo)")
    return True


def step_golden_path() -> bool:
    print("\n[3/3] Golden Path sobre fixtures/...")
    fixtures_dir = ROOT / "fixtures"
    if not fixtures_dir.exists():
        print("  [WARN] Carpeta fixtures/ no encontrada; asegúrate de definirla en H2.")
        return True
    
    # Aquí el equipo añade el comando que ejercita el momento demo.
    # Ejemplo:
    # res = subprocess.run([sys.executable, "main.py", "--fixture", "fixtures/sample.json"], capture_output=True, text=True)
    # return res.returncode == 0 and "DEMO_SUCCESS" in res.stdout
    print("  [OK] Fixtures presentes.")
    return True


def main():
    print(f"=== CHECK.PY: VALIDACIÓN DE CALIDAD [{ROOT.name}] ===")
    if not step_syntax():
        print("\n[FALLO] Errores de sintaxis detectados. Corrige antes de commitear.", file=sys.stderr)
        sys.exit(1)
    if not step_tests():
        print("\n[FALLO] Los tests unitarios han fallado.", file=sys.stderr)
        sys.exit(1)
    if not step_golden_path():
        print("\n[FALLO] La Golden Path ha fallado sobre las fixtures.", file=sys.stderr)
        sys.exit(1)
    print("\n[PASS] Todos los checks han pasado correctamente.")
    sys.exit(0)


if __name__ == "__main__":
    main()
