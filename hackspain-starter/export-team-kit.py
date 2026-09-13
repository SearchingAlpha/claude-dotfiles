#!/usr/bin/env python3
"""Export the shareable part of the HackSpain kit into a separate team repo.

    python3 hackspain-starter/export-team-kit.py [target_dir]   # default ~/hackspain26-team-kit

Source of truth stays in claude-dotfiles (private, has personal notes). This
script copies an allowlist of files into the target, rewrites the handful of
sentences that only make sense for Pablo, drops everything personal (me.md,
contacts.md, 05-networking.md, /hs-contact), extracts the public people
appendix, and writes a README for teammates. Re-run any time the kit changes;
then commit and push in the target repo. Teammates' edits arrive as PRs there
and get ported back by hand.
"""
import os, re, shutil, sys, pathlib

SRC = pathlib.Path(__file__).resolve().parent            # hackspain-starter/
DOTFILES = SRC.parent
TARGET = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "~/hackspain26-team-kit").expanduser()

PLAYBOOKS = ["01-game-plan.md", "02-challenge-framing.md", "03-tracks-dossier.md",
             "04-pitch-and-demo.md", "06-team-coherence.md"]
CHEATSHEETS = ["kickoff-60.md", "team-brief.md", "pitch-card.md", "people-card.md", "render.py"]
TEMPLATES = ["CONTRACT.md", "AGENTS.md", "check.sh", "check.py", "github-workflow-check.yml"]
EXAMPLES = ["framing-brief-sample.md"]
COMMANDS = ["hs-frame.md", "hs-demo.md", "hs-pitch.md", "hs-judge.md", "hs-check.md"]
SANDBOX = ["agent.py", "app.py", "config.py", "llm.py", "media.py", "search.py",
           "trace.py", "harness.py", "repomap.py", "hackspain_cli.py",
           "requirements.txt", ".env.example", ".gitignore", "README.md"]

# (regex, replacement, description) applied to every exported text file.
# Whitespace-tolerant so line wrapping in the source doesn't matter.
GLOBAL_RULES = [
    (r"hackspain-starter/playbooks/", "kit/playbooks/", "kit path"),
    (r"`hackspain-starter/`", "`sandbox/`", "sandbox path"),
    (r"~/claude-dotfiles/", "~/hackspain26-team-kit/", "fallback path"),
    (r"(?s)\*\*Your angle\.\*\*.*?\n\n", "", "dossier angle paragraph (private)"),
    (r"(?s)\*\*Careers\.\*\*.*?\n\n", "", "dossier careers paragraphs (private)"),
    (r"FDE-style", "consulting-style", "hs-judge coffee wording"),
    (r"Stated\s+assumptions are exactly the signal FDE and deployment roles hire for\.",
     "Stated assumptions are what senior operators listen for.", "FDE hiring tell"),
    (r"the people and careers lines are your openers and your warm\s+application path\.",
     "the people and careers lines are your openers.", "warm application tell"),
    (r"Yours: shipped an AI", "Pablo's: shipped an AI", "why-us line"),
    (r"Claim it in the first two minutes \(script in `kickoff-60`\)\. Strong developers\s+"
     r"usually welcome someone taking this off their plate\. What you own:",
     "Agreed at kickoff (script in `kickoff-60`). What the CPO owns:", "role claim"),
    (r"This exists because open-ended questions once froze you in an interview\. The\s+"
     r"fix is a script for thinking out loud, not more knowledge\.",
     "This is a script for thinking out loud under pressure. It works for whoever on the "
     "team takes the question.", "Q&A origin"),
    (r"Reads playbook 04, 03 and me\.md\.", "Reads playbooks 04 and 03.", "hs-judge description"),
    (r"The user once froze on an\s+open-ended interview question; the cure is repetition of the "
     r"protocol under\s+mild pressure, with feedback\.",
     "Repetition under mild pressure, with feedback, is what makes it automatic.", "hs-judge origin"),
    (r",?\s*and\s+`kit/playbooks/me\.md` for the user's background\.", ".", "hs-judge me.md read"),
    (r"Notes, sources and the funds: appendix of `05-networking\.md`\.",
     "Notes, sources and the funds: `../people-notes.md`.", "people-card pointer"),
    (r"Personal context and approach live in `me\.md`\.[^\n]*\n[^\n]*\n[^\n]*\n", "", "me.md pointer"),
    (r"\(ver `playbooks/06-team-coherence\.md`\)", "(ver `../kit/playbooks/06-team-coherence.md`)", "sandbox readme"),
    (r"(?s)└── playbooks/.*?cheatsheets/[^\n]*\n",
     "└── (los playbooks del equipo están en ../kit/)\n", "sandbox readme tree"),
]

def apply_rules(text, path):
    for pattern, repl, _desc in GLOBAL_RULES:
        text = re.sub(pattern, repl, text)
    leftovers = [w for w in ("me.md", "contacts.md", "hs-contact", "Palantir", "HappyRobot rejection")
                 if w in text]
    if leftovers:
        print(f"  ! {path.name}: still mentions {leftovers}")
    return text

def copy_text(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8")
    dst.write_text(apply_rules(text, dst), encoding="utf-8")
    if src.suffix in (".sh", ".py"):
        os.chmod(dst, 0o755)

def main():
    if TARGET.exists():
        for child in TARGET.iterdir():
            if child.name == ".git":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    TARGET.mkdir(parents=True, exist_ok=True)
    pb = SRC / "playbooks"
    for f in PLAYBOOKS:
        copy_text(pb / f, TARGET / "kit" / "playbooks" / f)
    for f in CHEATSHEETS:
        copy_text(pb / "cheatsheets" / f, TARGET / "kit" / "cheatsheets" / f)
    for f in TEMPLATES:
        copy_text(pb / "templates" / f, TARGET / "kit" / "templates" / f)
    for f in EXAMPLES:
        copy_text(pb / "examples" / f, TARGET / "kit" / "examples" / f)
    for f in COMMANDS:
        copy_text(DOTFILES / "commands" / f, TARGET / "commands" / f)
    for f in SANDBOX:
        copy_text(SRC / f, TARGET / "sandbox" / f)

    # Public people appendix from 05 -> kit/people-notes.md
    net = (pb / "05-networking.md").read_text(encoding="utf-8")
    m = re.search(r"## Appendix — people notes and sources\n(.*)", net, re.S)
    if m:
        body = m.group(1).replace("The compact version is `cheatsheets/people-card.md`.",
                                  "The compact version is `cheatsheets/people-card.md`.")
        (TARGET / "kit" / "people-notes.md").write_text(
            "# Judges and mentors — public notes and sources\n\n"
            "Public professional information only, researched 8 Sep 2026. Read across the general\n"
            "panel: young, technical, several are recent hackathon winners; they will look at the code.\n"
            + body, encoding="utf-8")

    (TARGET / ".gitignore").write_text(
        "# secrets\n.env\n.env.*\n!.env.example\n\n# python\n__pycache__/\n*.pyc\n.venv/\nvenv/\n\n"
        "# rendered cheat sheets\nkit/cheatsheets/pdf/\n\n# local\noutputs/\n*.png\n*.jpg\n.DS_Store\n",
        encoding="utf-8")

    (TARGET / "README.md").write_text(TEAM_README, encoding="utf-8")
    n = sum(1 for _ in TARGET.rglob("*") if _.is_file() and ".git" not in _.parts)
    print(f"exported {n} files to {TARGET}")

TEAM_README = """# HackSpain 2026 — team kit

Prep material for our team at HackSpain 2026 (Madrid, Fri 18 Sep 17:00 → Sun 20 Sep,
36 hours, five startup challenges, VC and operator judges). Written by Pablo from the
product/business/presenter seat, so "you" in the playbooks means the CPO role. Everything
about the *team* is a proposal: push back, open a PR, or just tell him.

**The product stack is deliberately not decided here.** Every place it comes up says
"team decision at kickoff". The Python code in `sandbox/` is Pablo's playground for the
sponsor APIs, not a default.

## Read in this order (about 60 minutes total)

| Doc | Minutes | Why |
|---|---|---|
| `kit/cheatsheets/team-brief.md` | 5 | How we work the weekend: roles, timeline, rules. Start here. |
| `kit/playbooks/06-team-coherence.md` | 12 | Four people, four local coding agents, one product. Contract, fixtures, check script, integration owner. **This is where your technical opinion matters most.** |
| `kit/playbooks/01-game-plan.md` | 15 | The 36-hour timeline with checkpoints, the `hackspain` CLI, submission checklist. |
| `kit/playbooks/02-challenge-framing.md` | 15 | Hours 1–3: from challenge text to a pinned Framing Brief. `kit/examples/` has a sample. |
| `kit/playbooks/03-tracks-dossier.md` | 10 for one track | The five startups: product loop, what they care about, likely challenge themes, what "great" looks like, Q&A landmines. Read the section for the track you'd lean towards. |
| `kit/playbooks/04-pitch-and-demo.md` | 15 | The 3-minute pitch, demo engineering with a fallback ladder, the 4-step Q&A protocol. |
| `kit/cheatsheets/kickoff-60.md` | 3 | The first hour at the venue, minute by minute. Read on the way in. |
| `kit/cheatsheets/people-card.md`, `kit/people-notes.md` | 5 | Who the judges and mentors are, with a conversation hook each. |

Print the cheat sheets with `python3 kit/cheatsheets/render.py` (needs `pip install
markdown-it-py` and a local Chrome). Each one fits one A4 page.

## Templates for the project repo (hour 1)

`kit/templates/` holds `CONTRACT.md` (the shared brain every coding agent reads first),
`AGENTS.md` (agent rules; symlink `CLAUDE.md` to it so Cursor, Codex and Claude Code read
the same file), `check.sh` (the golden path as a test, run before every merge) and a
GitHub Actions skeleton. They go into the *project* repo at hour 1, via `/hs-check setup`
or by hand. **The most useful PR you can open before Friday:** fill `check.sh` with the
real commands for the stack you would pick, and challenge the module split in playbook 06.

## Slash commands (Claude Code)

`commands/` has five Claude Code slash commands. To use them, copy the files into
`~/.claude/commands/` (user-wide) or into the project repo's `.claude/commands/`
(project-wide). Each reads its playbook first and does one job:

| Command | When | Job |
|---|---|---|
| `/hs-frame <challenge text>` | H1–H3 | Framing Brief from the challenge text. No code. |
| `/hs-check setup` · `/hs-check review` | H1, then H12 / H20 / H28 | Copy the templates into the project repo; later run the check and review the diff for drift against the contract. |
| `/hs-demo <what works + demo moment>` | H24–H30 | Demo script with fallback ladder. |
| `/hs-pitch draft` · `tighten` | H16, H30 | English draft + Spanish delivery script with timings. |
| `/hs-judge panel <track>` · `coffee` | Any time | Mock Q&A that scores the 4-step protocol. Good solo practice. |

Cursor or Codex users: the agent rules reach you through `AGENTS.md` in the project repo;
the playbooks are plain markdown.

## Sandbox

`sandbox/` is a small Python scaffold that already talks to Claude, Exa and fal.ai with a
Streamlit front end and four smoke tests. Useful to check the sponsor APIs work before
Friday and to prototype model logic. Not a stack decision.

## How to contribute

Open a PR or an issue. Most valuable before Friday, in order: (1) `check.sh` and the
module split in 06 for the stack you'd choose; (2) technical surfaces per track in 03
(what you could actually build against); (3) anything in the timeline you think is wrong.
Pablo mirrors accepted changes back into his source copy.

## What is not here

Pablo's personal notes: networking plan, intros, contact log. Ask him if you want the
networking playbook; the people card above is the public part.
"""

if __name__ == "__main__":
    main()
