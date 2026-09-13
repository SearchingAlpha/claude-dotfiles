#!/usr/bin/env python3
"""
hackspain_cli.py — Helper de integración con la CLI oficial de HackSpain.
Permite registrar hitos, publicar en el feed del venue y monitorizar la telemetría.

Comandos:
    python hackspain_cli.py milestone firstCommit
    python hackspain_cli.py milestone firstBuild
    python hackspain_cli.py milestone firstDemo
    python hackspain_cli.py milestone custom --label "Primer parsing end-to-end con traza"
    python hackspain_cli.py post "Mensaje del equipo" [--image captura.png]
    python hackspain_cli.py scout
    python hackspain_cli.py perks
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys

# Configurar encoding seguro en Windows para evitar charmap UnicodeEncodeError
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


def run_hackspain_command(cmd_args: list[str]) -> int:
    """Ejecuta un comando en la CLI de hackspain si está instalada en PATH."""
    cli = shutil.which("hackspain") or shutil.which("hackspain.cmd")
    if not cli:
        print("[AVISO] La CLI 'hackspain' no esta en el PATH.", file=sys.stderr)
        print(f"[COMANDO SIMULADO] hackspain {' '.join(cmd_args)}")
        print("Instala o loguea la CLI oficial cuando estes en el venue: `npm install -g @hackspain/cli` o binary release.")
        return 0

    full_cmd = [cli] + cmd_args
    proc = subprocess.run(full_cmd)
    return proc.returncode


def cmd_milestone(args):
    kind = args.kind
    if kind == "custom":
        if not args.label:
            print("Error: Los hitos 'custom' requieren --label 'descripcion'", file=sys.stderr)
            return 1
        return run_hackspain_command(["milestone", "add", "custom", "--label", args.label])
    return run_hackspain_command(["milestone", "add", kind])


def cmd_post(args):
    cmd = ["post", args.text]
    if args.image:
        cmd.extend(["--image", args.image])
    return run_hackspain_command(cmd)


def cmd_scout(args):
    print("=== EXPLORACION DE RIVALES Y TRACKS (hackspain project list) ===")
    return run_hackspain_command(["project", "list"])


def cmd_perks(args):
    print("=== PERKS Y CREDITOS DISPONIBLES (hackspain perk list) ===")
    return run_hackspain_command(["perk", "list"])


def cmd_status(args):
    return run_hackspain_command(["project", "show"])


def main():
    parser = argparse.ArgumentParser(description="Helper para interactuar con la CLI oficial de HackSpain")
    subparsers = parser.add_subparsers(dest="subcommand", help="Comando")

    # milestone
    p_milestone = subparsers.add_parser("milestone", help="Registra un hito en el board del venue")
    p_milestone.add_argument("kind", choices=["firstCommit", "firstBuild", "firstDemo", "custom"], help="Tipo de hito")
    p_milestone.add_argument("--label", help="Descripcion (obligatoria para hitos custom, max 120 chars)")

    # post
    p_post = subparsers.add_parser("post", help="Publica una actualizacion en el venue feed")
    p_post.add_argument("text", help="Texto del mensaje (max 500 caracteres)")
    p_post.add_argument("--image", help="Ruta a una captura/imagen opcional")

    # scout
    subparsers.add_parser("scout", help="Lista proyectos y tracks de otros equipos")

    # perks
    subparsers.add_parser("perks", help="Lista los perks y creditos de partners")

    # status
    subparsers.add_parser("status", help="Muestra el estado del proyecto (draft vs submitted)")

    args = parser.parse_args()

    if args.subcommand == "milestone":
        sys.exit(cmd_milestone(args))
    elif args.subcommand == "post":
        sys.exit(cmd_post(args))
    elif args.subcommand == "scout":
        sys.exit(cmd_scout(args))
    elif args.subcommand == "perks":
        sys.exit(cmd_perks(args))
    elif args.subcommand == "status":
        sys.exit(cmd_status(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
