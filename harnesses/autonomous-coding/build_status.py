#!/usr/bin/env python3
"""build_status.py — one-glance dashboard of every autonomous build.

Scans a builds root (default ~/builds) for project dirs that carry the harness
artifacts (app_spec.txt / spec.json / state.json), and prints a compact table:
progress %, current phase, whether a loop.py is still running, escalations, and
the last commit. Read-only — spawns nothing, never touches state.

Usage:
    python3 build_status.py                 # scan ~/builds
    python3 build_status.py --root DIR      # scan a different root
    python3 build_status.py --project DIR   # a single project dir
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

PHASE_RANK = ["done", "building", "negotiating", "escalated"]


def sh(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except Exception:
        return ""


def running_projects() -> set[str]:
    """Absolute --project-dir paths that currently have a loop.py process."""
    out = sh(["ps", "aux"])
    dirs: set[str] = set()
    for line in out.splitlines():
        if "loop.py" not in line or "--project-dir" not in line:
            continue
        parts = line.split()
        if "--project-dir" in parts:
            i = parts.index("--project-dir")
            if i + 1 < len(parts):
                dirs.add(str(Path(parts[i + 1]).resolve()))
    return dirs


def summarize(project: Path, live: set[str]) -> dict | None:
    spec = project / "spec.json"
    state = project / "state.json"
    if not (project / "app_spec.txt").exists() and not spec.exists():
        return None

    is_live = str(project.resolve()) in live
    info: dict = {"name": project.name, "live": is_live, "total": 0, "done": 0,
                  "phases": {}, "escalated": 0, "commit": "", "planned": spec.exists()}

    # Requirement counts from spec + state.
    reqs = []
    if spec.exists():
        try:
            reqs = json.loads(spec.read_text()).get("requirements", [])
        except Exception:
            reqs = []
    info["total"] = len(reqs)

    if state.exists():
        try:
            st = json.loads(state.read_text()).get("requirements", {})
        except Exception:
            st = {}
        for r in st.values():
            ph = r.get("phase", "")
            info["phases"][ph] = info["phases"].get(ph, 0) + 1
            if ph == "done":
                info["done"] += 1
            if ph == "escalated":
                info["escalated"] += 1

    info["commit"] = sh(["git", "log", "-1", "--format=%h %s"], cwd=project)
    return info


def bar(done: int, total: int, width: int = 16) -> str:
    if total == 0:
        return "░" * width
    filled = round(width * done / total)
    return "█" * filled + "░" * (width - filled)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "builds"))
    ap.add_argument("--project", default=None)
    args = ap.parse_args()

    live = running_projects()

    if args.project:
        projects = [Path(args.project)]
    else:
        root = Path(args.root)
        projects = sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []

    rows = [s for p in projects if (s := summarize(p, live))]
    if not rows:
        print(f"No builds found under {args.root}")
        return

    print(f"\n  autonomous builds under {args.root}\n")
    for r in rows:
        pct = round(100 * r["done"] / r["total"]) if r["total"] else 0
        status = "🟢 running" if r["live"] else "⚪ stopped"
        if r["escalated"]:
            status = f"🔴 {r['escalated']} escalated"
        elif not r["planned"]:
            status = "🟢 planning" if r["live"] else "⚪ no spec yet"

        head = f"  {r['name']:<16} {bar(r['done'], r['total'])} {r['done']}/{r['total']} ({pct}%)  {status}"
        print(head)

        # active phases line (non-done)
        active = {k: v for k, v in r["phases"].items() if k != "done"}
        if active:
            parts = ", ".join(f"{v}× {k}" for k, v in sorted(active.items()))
            print(f"  {'':<16} └─ {parts}")
        if r["commit"]:
            print(f"  {'':<16}    last: {r['commit'][:78]}")
        print()


if __name__ == "__main__":
    main()
