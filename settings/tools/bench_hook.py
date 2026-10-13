"""Latency of allow_repo_push.py: allowed pushes in a temp repo (explicit,
bare, the add + heredoc commit + push chain, and the `2>&1 | tail; git log`
suffixes), and a command for another repo (the common case: no answer).

    python3 settings/tools/bench_hook.py
"""
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "allow_repo_push.py"
tmp = Path(tempfile.mkdtemp(prefix="bench-allow-"))
ident = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
v = tmp / "v"
try:
    subprocess.run(["git", "init", "-q", "--bare", str(tmp / "r.git")], check=True)
    subprocess.run(["git", "init", "-q", "-b", "mainline", str(v)], check=True)
    subprocess.run(["git", *ident, "commit", "-q", "--allow-empty", "-m", "i"], cwd=v, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(tmp / "r.git")], cwd=v, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "mainline"], cwd=v, check=True)
    msg = "\"$(cat <<'EOF'\nvault: add n\n\nCo-Authored-By: A <a@example.invalid>\nEOF\n)\""
    for name, cmd in (("explicit", f"git -C {v} push origin mainline"),
                      ("bare", f"cd {v} && git push"),
                      ("chain", f"git -C {v} add n.md && git -C {v} commit -m {msg} && git -C {v} push"),
                      ("suffixes", f"git -C {v} push -q origin HEAD:mainline 2>&1 | tail -2; "
                                   f"git -C {v} log --oneline -1; git -C {v} status -sb | head -1"),
                      ("other repo", "git push origin main")):
        payload = json.dumps({"tool_name": "Bash", "cwd": str(tmp), "tool_input": {"command": cmd}})
        ts = []
        for _ in range(15):
            t = time.perf_counter()
            r = subprocess.run([sys.executable, "-I", str(HOOK), "--repo", str(v),
                                "--url", str(tmp / "r.git"), "--branch", "mainline"],
                               input=payload, text=True,
                               capture_output=True)
            ts.append((time.perf_counter() - t) * 1000)
        print(f"{name:10} median {statistics.median(ts):.1f} ms, max {max(ts):.1f} ms, "
              f"answer: {'allow' if r.stdout.strip() else 'none'}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
