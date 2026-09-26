#!/usr/bin/env python3
"""
seo.py — CLI de herramientas SEO con fuentes gratuitas (stdlib-only).

Réplica de los resultados de open-seo (MIT) sin DataForSEO: Bing Webmaster
Tools (volumen de keywords, enlaces propios), Brave Search API, SerpAPI y
Common Crawl. Cada proyecto aporta un `seo-project.json` (plantilla y
esquemas en README.md); las salidas van a `<ops_dir>/` como JSONL.

Uso:
    python ~/claude-dotfiles/harnesses/tools/seo/seo.py <cmd> --project <ruta> [--json]
    python seo.py probe --project site/config/seo-project.json
    python seo.py serp --queries money --engine brave --dry-run
    python seo.py rank-track run
    python seo.py report --format md --write-matrix

Flags globales: --project, --dry-run, --force, --json, --only, --limit.
Salida: 0 ok · 1 config/secreto · 2 todo falló · 3 cuota rechazada.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import human, load_project  # noqa: E402

COMMANDS = ("cmd_probe", "cmd_seeds", "cmd_kw", "cmd_serp", "cmd_rank", "cmd_links",
            "cmd_report")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", help="ruta a seo-project.json (default: autodescubrir)")
    common.add_argument("--dry-run", action="store_true",
                        help="listar llamadas y coste sin red ni escritura")
    common.add_argument("--force", action="store_true", help="saltar el guard de cuota")
    common.add_argument("--json", action="store_true",
                        help="resultado JSON a stdout (lo humano va a stderr)")
    common.add_argument("--only", help="solo ítems que contengan esta subcadena (reanudar)")
    common.add_argument("--limit", type=int, help="máximo de ítems a procesar")

    ap = argparse.ArgumentParser(prog="seo.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", metavar="<cmd>")
    for name in COMMANDS:
        try:
            mod = importlib.import_module(name)
        except ImportError as e:        # módulo aún no escrito: el resto sigue usable
            if name in str(e):
                continue
            raise
        mod.register(sub, common)
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help(sys.stderr)
        return 1
    try:
        cfg = load_project(args.project)
    except SystemExit as e:
        human(str(e))
        return 1
    try:
        return int(args.func(args, cfg) or 0)
    except SystemExit as e:
        msg = str(e)
        if msg.startswith("cuota"):
            human(msg)
            return 3
        if e.code in (0, None):
            return 0
        human(msg)
        return 1 if not isinstance(e.code, int) else e.code


if __name__ == "__main__":
    sys.exit(main())
