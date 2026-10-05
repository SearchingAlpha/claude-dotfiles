#!/usr/bin/env python3
"""check_docs.py — fail fast on documentation that won't render or links nowhere.

For every Markdown file under the given paths:
  * every ```mermaid block is rendered with mermaid-cli (the same engine GitHub
    uses); parse errors are reported as file:line with Mermaid's message;
  * GitHub-hostile Mermaid is flagged (click handlers, `graph` instead of
    `flowchart`, a bare `end` node id, `stateDiagram` without -v2, oversize);
  * relative links and images must point at files that exist (anchors and
    URLs are not fetched).

Usage:
    python3 check_docs.py [PATH ...]            # files or dirs, default: .
    python3 check_docs.py docs/ --png /tmp/out  # also write one PNG per diagram to look at
    python3 check_docs.py README.md --no-render # static checks only (no Node needed)

Needs Node (npx) for rendering; the first run downloads @mermaid-js/mermaid-cli.
Exit code 1 if anything failed. Stdlib only.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MMDC = ["npx", "-y", "@mermaid-js/mermaid-cli@11"]
FENCE = re.compile(r"^(\s*)(```+|~~~+)\s*([\w-]*)")
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|!\[[^\]]*\]\(([^)\s]+)")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
MAX_NODES = 25


def markdown_files(paths: list[str]) -> list[Path]:
    files = []
    for p in map(Path, paths or ["."]):
        if p.is_file() and p.suffix.lower() in (".md", ".markdown"):
            files.append(p)
        elif p.is_dir():
            for root, dirs, names in os.walk(p):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                files += [Path(root) / n for n in names if n.lower().endswith((".md", ".markdown"))]
    return sorted(set(files))


def blocks(text: str):
    """Yield (lang, first_line_no, body) for fenced code blocks."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = FENCE.match(lines[i])
        if not m:
            i += 1
            continue
        fence, lang, start = m.group(2), m.group(3).lower(), i + 1
        body = []
        i += 1
        while i < len(lines) and not lines[i].strip().startswith(fence):
            body.append(lines[i])
            i += 1
        yield lang, start, "\n".join(body)
        i += 1


def lint_mermaid(body: str) -> list[str]:
    issues = []
    first = next((l.strip() for l in body.splitlines() if l.strip() and not l.strip().startswith("%%")), "")
    if re.match(r"^graph\b", first):
        issues.append("use `flowchart` instead of `graph` (newer syntax, same diagram)")
    if re.match(r"^stateDiagram\s*$", first):
        issues.append("use `stateDiagram-v2`")
    if re.search(r"^\s*click\s", body, re.M):
        issues.append("`click` handlers are disabled on GitHub")
    if first.startswith("flowchart") and re.search(r"(^|[\s>-])end\s*[\[(\-]", body):
        issues.append("a node id `end` breaks flowcharts; use `done` or `End`")
    if first.startswith("flowchart"):
        ids = set(re.findall(r"(?:^|\s|;|>|\|)([A-Za-z_][\w]*)\s*(?:\[|\(|\{|>)", body))
        if len(ids) > MAX_NODES:
            issues.append(f"{len(ids)} nodes — split it (aim for ≤ 15)")
    return issues


def render(body: str, png_out: Path | None) -> str | None:
    """None if it renders, else Mermaid's error message."""
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "d.mmd"
        src.write_text(body)
        out = png_out or Path(d) / "d.svg"
        cmd = MMDC + ["-q", "-i", str(src), "-o", str(out), "-b", "white"]
        if png_out:
            cmd += ["-s", "2"]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            return "mermaid-cli timed out"
        if p.returncode == 0 and out.exists():
            return None
        err = (p.stderr or p.stdout).strip().splitlines()
        msg = [l for l in err if l.startswith(("Error", "Parse error", "Lexical error"))
               or l.startswith("...") or l.strip().startswith("-") or "Expecting" in l]
        return "\n      ".join((msg or err or ["unknown error"])[:4])


def check_links(path: Path, text: str) -> list[tuple[int, str]]:
    bad = []
    in_code = False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            in_code = not in_code
            continue
        if in_code:
            continue
        for m in LINK.finditer(line):
            target = m.group(1) or m.group(2)
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith(("#", "//")):
                continue
            file_part = target.split("#", 1)[0]
            if file_part and not (path.parent / file_part).exists():
                bad.append((n, f"broken link: {target}"))
    return bad


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--png", help="write a PNG per diagram into this directory, for review")
    ap.add_argument("--no-render", action="store_true", help="static checks only")
    ap.add_argument("-j", "--jobs", type=int, default=4)
    a = ap.parse_args(argv)

    if not a.no_render and not shutil.which("npx"):
        print("npx not found: install Node to render diagrams, or pass --no-render", file=sys.stderr)
        return 2
    png_dir = Path(a.png) if a.png else None
    if png_dir:
        png_dir.mkdir(parents=True, exist_ok=True)

    failures, diagrams, jobs = 0, 0, []
    for f in markdown_files(a.paths):
        text = f.read_text(errors="replace")
        for n, msg in check_links(f, text):
            print(f"FAIL {f}:{n}: {msg}")
            failures += 1
        for lang, line, body in blocks(text):
            if lang != "mermaid":
                continue
            diagrams += 1
            for issue in lint_mermaid(body):
                print(f"FAIL {f}:{line}: {issue}")
                failures += 1
            if not a.no_render:
                png = png_dir / f"{f.parent.name or 'root'}-{f.stem}-L{line}.png" if png_dir else None
                jobs.append((f, line, body, png))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        results = pool.map(lambda j: (j, render(j[2], j[3])), jobs)
        for (f, line, _, png), err in results:
            if err:
                print(f"FAIL {f}:{line}: mermaid does not render:\n      {err}")
                failures += 1
            else:
                print(f"ok   {f}:{line}" + (f" → {png}" if png else ""))

    print(f"\n{diagrams} diagram(s), {failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
