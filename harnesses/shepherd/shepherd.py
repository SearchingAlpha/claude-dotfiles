#!/usr/bin/env python3
"""shepherd — tracks, reviews and a live board for parallel Claude Code sessions.

    shepherd status | start | adopt | log | inbox | sync | pr | finish | serve | doctor
    shepherd hook <event>        (Claude Code hooks: JSON on stdin)
    shepherd githook <name> ...  (git hook stubs)

Stdlib only, Python 3.9+. See harnesses/shepherd/README.md."""

from __future__ import annotations

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))


def _log_error(what: str) -> None:
    try:
        from shep import store
        p = store.home() / "hook-errors.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > 1_000_000:
            os.replace(p, p.with_suffix(".log.1"))
        with open(p, "a") as f:
            f.write(f"--- {time.strftime('%Y-%m-%dT%H:%M:%S')} {what}\n{traceback.format_exc()}\n")
    except Exception:
        pass


def main(argv: list[str]) -> int:
    # Hook paths bypass argparse (a usage error exits 2, which Claude Code reads as
    # "block") and swallow every error: shepherd must never break a tool call.
    if argv[:1] == ["hook"]:
        event = argv[1] if len(argv) > 1 else ""
        try:
            raw = sys.stdin.read()
            if event == "pre-bash" and "git" not in raw and "SHEPHERD_ALLOW_MAIN" not in raw:
                return 0
            from shep import hooks
            out = hooks.run_claude_hook(event, raw)
            if out:
                sys.stdout.write(out)
        except Exception:
            _log_error(f"hook {event}")
        return 0
    if argv[:1] == ["githook"]:
        name = argv[1] if len(argv) > 1 else ""
        try:
            from shep import hooks
            stdin = sys.stdin.read() if name == "pre-push" else ""
            return hooks.run_git_hook(name, argv[2:], stdin)
        except Exception:
            _log_error(f"githook {name}")
            return 0
    from shep import cli
    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
