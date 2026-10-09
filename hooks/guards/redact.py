#!/usr/bin/env python3
"""PostToolUse / PostToolUseFailure hook: keep secret-shaped strings in tool
output out of Claude's context.

- PostToolUse: walks tool_response, replaces each finding in every string leaf
  with [REDACTED:<kind>] and returns the result as
  hookSpecificOutput.updatedToolOutput, keeping the response's exact shape (a
  built-in tool's replacement that doesn't match its output schema is ignored by
  Claude Code). Nothing is printed when nothing matched.
- PostToolUseFailure: Claude Code can't rewrite a failed call's output, so this
  only adds an additionalContext warning naming what was in it.

Limits: OpenTelemetry tool spans still capture the original, a failed call's
output reaches Claude unredacted, an MCP result's structuredContent (which the
hook never receives) stays in the stream-json log, and the tool already ran.
This is a second layer behind keeping secrets out of the sources. Always exits 0."""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

COMPONENT = "redact"
BLOB_MIN = 512               # long base64 strings (or data: URIs) under these keys are binary payloads
BLOB_KEYS = ("base64", "data", "image", "source")
BASE64 = re.compile(r"(?:data:[\w.+/-]{1,80};base64,)?[A-Za-z0-9+/=_-]+")


def _walk(obj, key, patterns, kinds):
    if isinstance(obj, str):
        if key and (key.lower().endswith("path") or
                    (key in BLOB_KEYS and len(obj) >= BLOB_MIN and BASE64.fullmatch(obj))):
            return obj
        # The allow marker is for fixtures being written or committed, not for output:
        # one marker on a minified JSON line would let every secret on it through.
        new, found = patterns.redact(obj, honor_marker=False)
        kinds.extend(found)
        return new
    if isinstance(obj, dict):
        return {k: _walk(v, k if isinstance(k, str) else "", patterns, kinds) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, key, patterns, kinds) for v in obj]
    return obj


def run(inp: dict, common) -> int:
    import patterns
    event = inp.get("hook_event_name")
    tool = inp.get("tool_name") or "tool"
    if event == "PostToolUseFailure":
        err = inp.get("error")
        found = patterns.scan(err, honor_marker=False) if isinstance(err, str) else []
        if found and not common.disabled(COMPONENT, common.config()):
            kinds = patterns.summarize([f.kind for f in found])
            common.emit({"hookSpecificOutput": {
                "hookEventName": "PostToolUseFailure",
                "additionalContext": (f"redact: the failed {tool} output above contains "
                                      f"secret-shaped values ({kinds}) that Claude Code can't "
                                      f"redact from a failed call. Don't repeat, store or send "
                                      f"them; refer to them by location only.")}})
        return 0
    if event != "PostToolUse" or "tool_response" not in inp:
        return 0
    kinds: list = []
    new = _walk(inp["tool_response"], "", patterns, kinds)
    if not kinds or common.disabled(COMPONENT, common.config()):
        return 0
    common.emit({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "updatedToolOutput": new,
        "additionalContext": (f"redact: {len(kinds)} secret-shaped value(s) in this {tool} "
                              f"result were replaced with [REDACTED:<kind>] before you saw them "
                              f"({patterns.summarize(kinds)}). The underlying data is unchanged. "
                              f"Refer to them by location; never copy them into files, commands "
                              f"or messages.")}})
    return 0


def main() -> int:
    inp = None
    try:
        import common
    except Exception as e:
        print(f"guards/{COMPONENT}: cannot load helpers ({type(e).__name__}); not enforced",
              file=sys.stderr)
        return 0
    try:
        inp = common.read_payload()
        return run(inp, common) if inp is not None else 0
    except Exception as e:
        return common.fail_open(COMPONENT, e, inp)


if __name__ == "__main__":
    sys.exit(main())
