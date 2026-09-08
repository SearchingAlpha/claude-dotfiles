#!/usr/bin/env python3
"""Render the cheat sheets to one-page A4 PDFs.

Usage:  python3 render.py            # all four sheets -> ./pdf/
        python3 render.py pitch-card # one sheet

Needs: `pip install markdown-it-py` and a Chrome/Chromium binary (looked up in
$CHROME, then google-chrome, chromium, chromium-browser, and the ~/.local/chrome
path used on the dev desktop). `pdfinfo` (poppler) is optional; if present the
script reports the page count so you can cut until each sheet is one page.
Layout below was tested on 8 Sep 2026: every sheet fits one portrait page.
"""
import os, pathlib, shutil, subprocess, sys

try:
    from markdown_it import MarkdownIt
except ImportError:
    sys.exit("pip install markdown-it-py")

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "pdf"
SHEETS = ["kickoff-60", "team-brief", "pitch-card", "people-card"]

CSS = """
@page { size: A4 portrait; margin: 10mm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 9.5pt; line-height: 1.22; color: #111; }
h1 { font-size: 13pt; margin: 0 0 3px; }
h2 { font-size: 10.5pt; margin: 6px 0 2px; }
p, li { margin: 1px 0; }
ul, ol { margin: 1px 0 1px 16px; padding: 0; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; margin: 2px 0; }
td, th { border: 1px solid #999; padding: 1px 4px; vertical-align: top; text-align: left; }
th { background: #eee; }
blockquote { margin: 3px 0 3px 10px; padding-left: 8px; border-left: 3px solid #999; }
code { font-size: 8.5pt; }
"""


def find_chrome():
    cands = [os.environ.get("CHROME"), "google-chrome", "google-chrome-stable", "chromium",
             "chromium-browser", os.path.expanduser("~/.local/chrome/opt/google/chrome/chrome")]
    for c in cands:
        if not c:
            continue
        p = shutil.which(c) or (c if os.path.exists(c) else None)
        if p:
            return p
    sys.exit("No Chrome/Chromium found. Set $CHROME to the binary path.")


def main(names):
    chrome = find_chrome()
    OUT.mkdir(exist_ok=True)
    md = MarkdownIt("commonmark").enable("table")
    for name in names:
        src = HERE / f"{name}.md"
        html = OUT / f"{name}.html"
        pdf = OUT / f"{name}.pdf"
        body = md.render(src.read_text(encoding="utf-8"))
        html.write_text(f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>",
                        encoding="utf-8")
        subprocess.run([chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf}", html.as_uri()], capture_output=True, timeout=90)
        pages = "?"
        if shutil.which("pdfinfo") and pdf.exists():
            info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
            pages = next((l.split()[-1] for l in info.splitlines() if l.startswith("Pages")), "?")
        flag = "" if pages in ("1", "?") else "  <-- over one page, cut text"
        print(f"{name}: {pdf.relative_to(HERE)}  pages={pages}{flag}")


if __name__ == "__main__":
    main(sys.argv[1:] or SHEETS)
