"""The fleet mod's test fixtures (mods/fleet/tests/fixtures.ts at 3aa5437), built the same
way, byte for byte: headless run logs as `claude -p --output-format stream-json` writes
them, text logs of cron wrappers, a sources file and shepherd's status. TestParity checks
them against the TypeScript originals when those are around."""

import json
from datetime import datetime, timezone

# 2026-10-09 12:00 UTC: the same calendar day in every timezone the tests run in.
NOW = int(datetime(2026, 10, 9, 12, tzinfo=timezone.utc).timestamp() * 1000)
MIN = 60_000
HOUR = 3_600_000

HOME = "/home/user"
LOGS = f"{HOME}/logs"
SOURCES = f"{HOME}/fleet/sources.json"

UNDEF = object()                                # a field JSON.stringify leaves out


def j(event) -> str:
    """JSON.stringify: compact, non-ASCII as is, undefined fields left out."""
    def drop(v):
        if isinstance(v, dict):
            return {k: drop(x) for k, x in v.items() if x is not UNDEF}
        return [drop(x) for x in v] if isinstance(v, list) else v
    return json.dumps(drop(event), separators=(",", ":"), ensure_ascii=False)


def s(ms: int) -> int:
    return ms // 1000


def stats(started, completed, killed_system=0, killed_parent=0, killed_user=0) -> dict:
    return {"spawned": started, "requested": {"background": started, "foreground": 0, "unset": 0},
            "started_in_background": started, "max_depth": 1 if started > 0 else 0, "spawned_by_subagents": 0,
            "completed": completed, "failed": 0,
            "killed": {"parent": killed_parent, "user": killed_user, "system": killed_system},
            "refused": {"depth_limit": 0, "concurrency_limit": 0, "budget": 0},
            "by_type": {"general-purpose": started} if started > 0 else {}}


def result(**fields) -> str:
    return j({"type": "result", "subtype": "success", "is_error": False, "num_turns": 7,
              "result": "Report written to output/report.md.", "stop_reason": "end_turn",
              "session_id": "cccccccc-0000-4000-8000-000000000001", "total_cost_usd": 0.48,
              "permission_denials": [], "terminal_reason": "completed", "subagent_stats": stats(0, 0),
              "duration_ms": 154_000, **fields})


def log(*lines) -> str:
    return "\n".join(lines) + "\n"


INIT = j({"type": "system", "subtype": "init", "session_id": "cccccccc-0000-4000-8000-000000000001",
          "tools": ["Bash", "Read"]})
# An assistant line whose text holds the spelling of a result event, escaped as JSON escapes it.
ASSISTANT = j({"type": "assistant", "message": {"content": [{"type": "text", "text": 'printing {"type":"result"} as text'}]}})
TOOL_RESULT = j({"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}})
BG_START = j({"type": "system", "subtype": "task_started", "task_id": "a155", "description": "Deep dive",
              "subagent_type": "general-purpose", "is_backgrounded": True, "task_type": "local_agent"})
BASH_START = j({"type": "system", "subtype": "task_started", "task_id": "b1", "is_backgrounded": False,
                "task_type": "local_bash"})
BASH_DONE = j({"type": "system", "subtype": "task_notification", "task_id": "b1", "status": "completed"})
BG_START_2 = BG_START.replace('"a155"', '"a156"', 1).replace("Deep dive", "Duplicate deep dive", 1)
TERMINATING = ("Background tasks still running after 600s; terminating. "
               "Set CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 to wait indefinitely.")

LOG_OK = log(INIT, ASSISTANT, TOOL_RESULT, BASH_START, BASH_DONE, result())
LOG_UNFINISHED = log(
    INIT, ASSISTANT, BG_START, TERMINATING,
    j({"type": "system", "subtype": "task_updated", "task_id": "a155", "patch": {"status": "killed", "end_time": NOW}}),
    j({"type": "system", "subtype": "task_notification", "task_id": "a155", "status": "stopped"}),
    result(num_turns=20, total_cost_usd=2.42, result="The builder works.\nNow waiting for the deep-dive agent to finish.",
           subagent_stats=stats(1, 0, 1)))
LOG_STOPPED_BY_MODEL = log(
    INIT, BG_START, BG_START_2,
    j({"type": "system", "subtype": "task_updated", "task_id": "a156", "patch": {"status": "killed", "end_time": NOW}}),
    j({"type": "system", "subtype": "task_notification", "task_id": "a156", "status": "stopped"}),
    j({"type": "system", "subtype": "task_notification", "task_id": "a155", "status": "completed"}),
    result(num_turns=9, result="Report written; stopped the duplicate deep dive.", subagent_stats=stats(2, 1, 0, 1)))
LOG_STOPPED_BY_OTHER = LOG_STOPPED_BY_MODEL.replace('"parent":1,"user":0', '"parent":0,"user":1', 1)
LOG_WAITING = log(INIT, BG_START, result(result="Started the deep dive; waiting for it.", subagent_stats=stats(1, 0)))
LOG_DENIED = log(
    INIT, j({"type": "system", "subtype": "permission_denied", "tool_name": "Bash", "tool_use_id": "toolu_01"}),
    result(permission_denials=[{"tool_name": "Bash", "tool_use_id": "toolu_01", "tool_input": {"command": "cp a b"}}]))
LOG_KILLED = log(INIT, ASSISTANT, TOOL_RESULT)
LOG_FAILED = log(INIT, result(subtype="error_max_turns", is_error=True, terminal_reason="max_turns",
                              errors=["Reached maximum number of turns (40)"]))
LOG_ASKED = log(INIT, result(result="Two queries returned no rows.\n\n1. Publish anyway\n2. Wait for the data\n\nWhich do you want?"))
LOG_TWO_RESULTS = log(
    INIT, BG_START,
    result(num_turns=24, total_cost_usd=4.41, result="Waiting for the deep-dive agent.",
           permission_denials=[{"tool_name": "Skill", "tool_use_id": "toolu_02", "tool_input": {}}],
           subagent_stats=stats(1, 1)),
    j({"type": "system", "subtype": "task_notification", "task_id": "a155", "status": "completed"}),
    result(num_turns=8, total_cost_usd=4.41, origin={"kind": "task-notification"}, subagent_stats=stats(1, 1)))
LOG_NO_STATS = log(INIT, BG_START, result(subagent_stats=UNDEF, result="Started the deep dive."))
LOG_SAYS_WAITING = log(INIT, result(subagent_stats=UNDEF, result="Built the page.\nNow waiting for the review agent to finish."))
LOG_OK_SPACED = "\n".join(ln.replace('":', '": ').replace(',"', ', "') for ln in LOG_OK.split("\n"))
LOG_SUBAGENT_FAILED = log(
    INIT, BG_START, j({"type": "system", "subtype": "task_notification", "task_id": "a155", "status": "failed"}),
    result(result="Report written; the deep dive could not finish.",
           subagent_stats={**stats(1, 1), "spawned": 2, "requested": {"background": 1, "foreground": 1, "unset": 0},
                           "failed": 1}))


def log_with_long_result(n: int) -> str:
    return log(INIT, result(result=f"Report:\n{'x' * n}\nDone."))


LOGS_BY_NAME = {name: value for name, value in globals().items() if name.startswith("LOG_")}

# An append-only log three cron jobs write to, and the next week's run before its verdict.
SHARED_LOG_OK = ("=== pull_leads ===\nok\n=== stage_gate ===\nok\n=== watchdog ===\nwatchdog: todo en orden\n"
                 "watchdog: todo en orden\n=== structure_check ===\nTraceback (most recent call last):\nKeyError: x\n")
SHARED_LOG_NEXT_RUN = f"{SHARED_LOG_OK}=== pull_leads ===\nok\n=== build_deploy ===\n"
WEEKLY_OK = ("[09:30] === Weekly run start ===\n[09:43] --- report: claude -p exit rc=0 after 823s ---\n"
             "[09:43] === Weekly run done; a_rc=0 b_rc=0 ===\n")
WEEKLY_SKIP = "[09:30] === Weekly run start ===\n[09:30] SKIP: login stale, not running\n"

SOURCES_FILE = {
    "runs": [{"label": "Nightly", "glob": f"{LOGS}/nightly-*.jsonl", "expectEveryHours": 30}],
    "lines": [{"label": "Weekly", "glob": f"{LOGS}/weekly-*.log", "match": "=== Weekly run done|SKIP:",
               "ok": "done; (?:\\w+_rc=0 ?)+==="}],
}

# `shepherd status --all --json`, trimmed to the fields the snapshot reads.
STATUS = {
    "generated_at": s(NOW),
    "repos": [{
        "name": "demo-repo", "primary": "/work/demo-repo", "mode": "observe",
        "tracks": [
            {"name": "parser-fix", "goal": "Fix the parser", "status": "active",
             "agent": {"session_id": "aaaaaaaa-0000-4000-8000-000000000001", "name": "demo-1", "live": True, "status": "busy"},
             "last_activity": s(NOW - 5 * MIN), "review": None, "warnings": []},
            {"name": "docs", "goal": "Write the docs", "status": "review",
             "agent": {"session_id": "aaaaaaaa-0000-4000-8000-000000000002", "name": "demo-2", "live": True, "status": "idle"},
             "last_activity": s(NOW - 2 * HOUR),
             "review": {"backend": "github", "id": "6", "state": "open", "checks": "passing",
                        "checked_at": s(NOW - 3 * HOUR), "url": "https://example.com/pr/6"},
             "warnings": ["3 behind origin/main"]},
            {"name": "old-idea", "goal": "An idea nobody works on", "status": "active",
             "agent": {"session_id": "aaaaaaaa-0000-4000-8000-000000000003", "name": "demo-3", "live": False, "status": "ended"},
             "last_activity": s(NOW - 72 * HOUR), "review": None, "warnings": ["owner session ended"]},
        ],
        "overlaps": [],
    }],
    "unmanaged": [],
}
