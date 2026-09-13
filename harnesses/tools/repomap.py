#!/usr/bin/env python3
"""
repomap.py — Generador ligero de mapa semántico de símbolos del repositorio.
Inspirado en el Repo Map de Aider, pero sin dependencias pesadas (Python stdlib).

Extrae clases, funciones, métodos y exports de ficheros Python, TypeScript, JavaScript
y Astro para proporcionar un mapa de símbolos de bajo consumo de tokens (~1-2k tokens)
que permite a los agentes conocer la estructura global del proyecto sin leer todos los ficheros.

Uso:
    python repomap.py [directorio] [--max-lines 150]
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from pathlib import Path

IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", ".next", ".astro", ".claude", "coverage"
}

IGNORED_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".min.js", ".min.css", ".map", ".lock",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".sqlite", ".db"
}

SUPPORTED_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".astro", ".sql"}


def parse_python_symbols(content: str) -> list[str]:
    """Extrae clases, métodos y funciones de un fichero Python usando AST."""
    symbols = []
    try:
        tree = ast.parse(content)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                args = [a.arg for a in node.args.args if a.arg != "self"]
                sig = f"def {node.name}({', '.join(args[:4])}{'...' if len(args) > 4 else ''})"
                symbols.append(f"  {sig}")
            elif isinstance(node, ast.ClassDef):
                symbols.append(f"  class {node.name}:")
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not item.name.startswith("_") or item.name == "__init__":
                            m_args = [a.arg for a in item.args.args if a.arg != "self"]
                            sig = f"    def {item.name}({', '.join(m_args[:3])}{'...' if len(m_args) > 3 else ''})"
                            symbols.append(sig)
    except Exception:
        # Fallback a regex si hay error de sintaxis en el archivo en desarrollo
        for line in content.splitlines():
            m = re.match(r"^(?:class\s+([A-Za-z0-9_]+)|def\s+([A-Za-z0-9_]+)\s*\((.*?)\))", line.strip())
            if m:
                symbols.append(f"  {line.strip()[:60]}")
    return symbols


def parse_js_ts_symbols(content: str) -> list[str]:
    """Extrae funciones, clases, interfaces y exports de TS/JS/Astro con regex."""
    symbols = []
    patterns = [
        r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z0-9_]+)\s*\((.*?)\)",
        r"^(?:export\s+)?class\s+([A-Za-z0-9_]+)",
        r"^(?:export\s+)?interface\s+([A-Za-z0-9_]+)",
        r"^(?:export\s+)?type\s+([A-Za-z0-9_]+)\s*=",
        r"^(?:export\s+)?const\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s*)?\((.*?)\)\s*=>",
    ]
    for line in content.splitlines():
        line_clean = line.strip()
        for pat in patterns:
            if re.match(pat, line_clean):
                # Limitar longitud para ahorrar tokens
                symbols.append(f"  {line_clean[:70]}")
                break
    return symbols


def generate_repomap(root: Path, max_lines: int = 160) -> str:
    lines = [f"# Repo Map: {root.name}"]
    total_files = 0
    
    file_map: list[tuple[str, list[str]]] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
        for fname in sorted(filenames):
            ext = os.path.splitext(fname)[1].lower()
            if ext in IGNORED_EXTENSIONS or ext not in SUPPORTED_EXTENSIONS:
                continue
            
            fpath = Path(dirpath) / fname
            rel_path = fpath.relative_to(root).as_posix()
            
            try:
                content = fpath.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            if ext == ".py":
                syms = parse_python_symbols(content)
            else:
                syms = parse_js_ts_symbols(content)
            
            if syms:
                total_files += 1
                file_map.append((rel_path, syms))

    # Formatear respetando el límite de líneas
    current_lines = len(lines)
    for rel_path, syms in file_map:
        if current_lines + len(syms) + 1 > max_lines:
            lines.append(f"\n... [{len(file_map) - total_files} archivos adicionales omitidos]")
            break
        lines.append(f"\n{rel_path}:")
        lines.extend(syms)
        current_lines += len(syms) + 1

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Genera un mapa conciso de símbolos del repositorio.")
    parser.add_argument("dir", nargs="?", default=".", help="Directorio raíz a escanear (por defecto '.')")
    parser.add_argument("--max-lines", type=int, default=150, help="Máximo número de líneas del mapa")
    args = parser.parse_args()

    target_dir = Path(args.dir).resolve()
    if not target_dir.exists():
        print(f"Error: {target_dir} no existe.", file=sys.stderr)
        sys.exit(1)

    print(generate_repomap(target_dir, max_lines=args.max_lines))


if __name__ == "__main__":
    main()
