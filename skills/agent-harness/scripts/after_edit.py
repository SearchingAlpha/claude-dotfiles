"""Claude Code PostToolUse hook: run the repo's fast tests after every edit.

Copy to <repo>/.claude/hooks/after_edit.py and register it in <repo>/.claude/settings.json
(see the agent-harness skill). Standard library only.

- Test command: $HARNESS_TEST_CMD, else pytest if the repo has pyproject.toml/pytest.ini, else `npm test`.
- Watched files: $HARNESS_WATCH (comma-separated suffixes), default code, data and docs.
- A failure goes back to Claude (exit 2) so it fixes it before moving on.
- TDD red step (pytest only): if every failure is in the test file just edited, it is reported, not blocked.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
WATCH = tuple(os.environ.get("HARNESS_WATCH", ".py,.ts,.tsx,.js,.json,.jsonl,.md,.toml").split(","))


def test_command() -> list[str]:
    if os.environ.get("HARNESS_TEST_CMD"):
        return shlex.split(os.environ["HARNESS_TEST_CMD"])
    if (ROOT / "pyproject.toml").exists() or (ROOT / "pytest.ini").exists():
        return [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:warnings", "--no-header"]
    if (ROOT / "package.json").exists():
        return ["npm", "test", "--silent"]
    return []


def main() -> int:
    event = json.load(sys.stdin)
    path = (event.get("tool_input") or {}).get("file_path", "")
    if not path.endswith(WATCH) or ".claude" in Path(path).parts:
        return 0
    cmd = test_command()
    if not cmd:
        return 0

    run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if run.returncode == 0:
        return 0

    output = run.stdout + run.stderr
    summary = "\n".join(output.splitlines()[-25:])
    failed = set(re.findall(r"^FAILED (\S+?)::", output, re.MULTILINE))
    edited = Path(path).resolve()
    if failed and all((ROOT / f).resolve() == edited for f in failed):
        note = f"Red: only the tests you just wrote in {path} fail. Expected in TDD; now make them pass.\n{summary}"
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": note}}))
        return 0
    print(f"Tests fail after editing {path}. Fix this before you continue:\n{summary}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
