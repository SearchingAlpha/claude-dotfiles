#!/usr/bin/env python3
"""PreToolUse guard for aws-outlook-mcp outbound email and meeting invitations:
nothing leaves for an address that isn't on the allowlist.

Rules (tool schemas read from aws-outlook-mcp 0.4.0 dist/tools/tools/email/):
- email_send: every to/cc/bcc must be allowlisted.
- email_forward: every `to` (and cc/bcc, should a version accept them) must be
  allowlisted.
- email_reply: saveAsDraft → allowed (nothing is sent). replyAll → denied (its
  recipients aren't in tool_input). A plain reply goes to the original sender,
  which the hook can't see: RECIPIENT_GUARD_UNVERIFIED decides (ask, the
  default, prompts you interactively and is refused in `claude -p`; or deny /
  allow). Extra to/cc/bcc must be allowlisted.
- email_draft: create/get/update/delete are allowed (a draft isn't sent);
  update with send:true is denied (the draft's recipients aren't visible).
- calendar_meeting (meeting.js; create and update send invitations, with an
  HTML body, when OUTLOOK_MCP_ENABLE_WRITES is on): every attendee,
  optionalAttendee and resource must be allowlisted. An update that changes
  anything but attendees and local fields (categories, reminder, showAs) also
  notifies the meeting's existing attendees, and an RSVP goes to the
  organizer, neither visible here: RECIPIENT_GUARD_UNVERIFIED decides, as for a
  plain reply. read and delete pass.

Allowlist: `recipient_guard.allow` in the values file kept out of git,
skills-amazon/guards/private.json at the repo root (or $CLAUDE_GUARDS_PRIVATE);
private.example.json beside this file shows the shape. Addresses compare
case-insensitively. A missing file or an empty list is a setup error: the
guard says so (stderr, errors.log, one notice per session) and lets the call
through. Fails open on its own errors."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))                     # hooks/guards: common.py

# The addresses are confidential: they live in an ignored file, the example is committed.
VALUES = HERE.parents[2] / "skills-amazon" / "guards" / "private.json"
EXAMPLE = HERE / "private.example.json"

COMPONENT = "recipient_guard"
PREFIX = "mcp__aws-outlook-mcp__"
ADDR = re.compile(r"<([^<>@\s]+@[^<>\s]+)>")
MEETING_ADDRS = ("attendees", "optionalAttendees", "resources")
# calendar_meeting update fields that change only the user's own copy.
MEETING_LOCAL = frozenset(("operation", "meetingId", "meetingChangeKey", "categories",
                           "reminderMinutes", "showAs", "calendarFolderId"))


class Unconfigured(Exception):
    """The values file is missing or names no addresses: nothing to check against."""


def values_path() -> Path:
    return Path(os.environ.get("CLAUDE_GUARDS_PRIVATE") or VALUES)


def allowlist() -> tuple:
    """(values file, set of allowed addresses). Raises Unconfigured when there is
    no usable list; a file that isn't valid JSON is an internal error."""
    path = values_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Unconfigured(f"{path} not found") from None
    section = data.get(COMPONENT) if isinstance(data, dict) else None
    allow = section.get("allow") if isinstance(section, dict) else None
    allowed = {a.strip().lower() for a in allow if isinstance(a, str) and a.strip()} \
        if isinstance(allow, list) else set()
    if not allowed:
        raise Unconfigured(f"{path} has no {COMPONENT}.allow addresses")
    return path, allowed


def unconfigured(common, err: Unconfigured, inp) -> int:
    """No allowlist: say what to set up and let the call through (exit 0)."""
    common.log_error(COMPONENT, err)
    fix = (f"copy {EXAMPLE} to {values_path()} and list the addresses mail may go to "
           f"under {COMPONENT}.allow")
    print(f"guards/{COMPONENT}: not enforced: {err}; {fix}", file=sys.stderr)
    sid = (inp or {}).get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if common.notice_once(COMPONENT, sid):
        common.emit({"systemMessage": f"guards/{COMPONENT} is not checking recipients: {err}. "
                                      f"To turn it on, {fix}."})
    return 0


def _norm(addr) -> str:
    s = str(addr).strip()
    m = ADDR.search(s)
    return (m.group(1) if m else s).strip().strip("'\"").lower()


def _recipients(ti: dict, keys=("to", "cc", "bcc")) -> list:
    out = []
    for k in keys:
        v = ti.get(k)
        if isinstance(v, str):
            v = [x for x in re.split(r"[;,]", v) if x.strip()]
        if isinstance(v, list):
            out += [_norm(a) for a in v if str(a).strip()]
    return out


def _decide(common, decision: str, reason: str) -> int:
    return common.decide(decision, reason)


def _unverified(common, reason: str, hint: str) -> int:
    """Mail goes to someone the call doesn't name: RECIPIENT_GUARD_UNVERIFIED
    (ask, the default; deny; allow) decides."""
    mode = os.environ.get("RECIPIENT_GUARD_UNVERIFIED", "ask").strip().lower()
    if mode == "allow":
        return 0
    if mode == "deny":
        return _decide(common, "deny", f"{reason} {hint}")
    return _decide(common, "ask", f"{reason} Confirm the recipients before sending.")


def _meeting(ti: dict, common) -> int:
    operation = str(ti.get("operation") or "").lower()
    if operation not in ("create", "update"):
        return 0                                # read; delete (cancellations carry no new text)
    path, allowed = allowlist()
    outside = sorted({a for a in _recipients(ti, MEETING_ADDRS) if a not in allowed})
    hint = (f"Leave the invitation for the user to send, or ask the user to add the address to "
            f"{COMPONENT}.allow in {path}.")
    if outside:
        return _decide(common, "deny",
                       f"recipient_guard: blocked calendar_meeting {operation} inviting "
                       f"{', '.join(outside)}: not in the allowlist. {hint}")
    if operation == "update":
        if ti.get("rsvpResponse"):
            return _unverified(common, "recipient_guard: this RSVP goes to the meeting's organizer, "
                                       "whom the guard can't see or check against the allowlist.", hint)
        if set(k for k, v in ti.items() if v is not None) - MEETING_LOCAL - set(MEETING_ADDRS):
            return _unverified(common, "recipient_guard: this meeting update is sent to the "
                                       "meeting's existing attendees, whom the guard can't see "
                                       "or check against the allowlist.", hint)
    return 0


def run(inp: dict, common) -> int:
    tool = str(inp.get("tool_name") or "")
    if not tool.startswith(PREFIX):
        return 0
    op = tool[len(PREFIX):]
    if op not in ("email_send", "email_reply", "email_forward", "email_draft", "calendar_meeting"):
        return 0
    ti = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    if common.disabled(COMPONENT, common.config()):
        return 0
    if op == "calendar_meeting":
        return _meeting(ti, common)
    if op == "email_draft":
        if str(ti.get("operation")).lower() == "update" and ti.get("send") is True:
            return _decide(common, "deny",
                           "recipient_guard: sending a draft (email_draft update with send:true) "
                           "is blocked: the draft's recipients aren't visible to the guard. Leave "
                           "the draft for the user to send from Outlook, or use email_send to "
                           "allowlisted recipients.")
        return 0
    if op == "email_reply" and ti.get("saveAsDraft") is True:
        return 0
    path, allowed = allowlist()
    # email_forward 0.4.0 has cc/bcc commented out of its schema; checked anyway,
    # so a version that turns them on is covered.
    outside = sorted({a for a in _recipients(ti) if a not in allowed})
    hint = (f"Save it as a draft instead (email_draft create, or email_reply with "
            f"saveAsDraft:true) for the user to review and send, or ask the user to add the "
            f"address to {COMPONENT}.allow in {path}.")
    if outside:
        return _decide(common, "deny",
                       f"recipient_guard: blocked {op} to {', '.join(outside)}: not in the "
                       f"allowlist. {hint}")
    if op == "email_reply":
        if ti.get("replyAll") is True:
            return _decide(common, "deny",
                           f"recipient_guard: blocked reply-all: its recipients come from the "
                           f"original thread and can't be checked against the allowlist. {hint}")
        return _unverified(common, "recipient_guard: this reply goes to the original sender, whom "
                                   "the guard can't see or check against the allowlist.", hint)
    return 0


def main() -> int:
    inp = None
    try:
        import common
    except Exception as e:
        print(f"guards/{COMPONENT}: cannot load hooks/guards/common.py ({type(e).__name__}); "
              f"not enforced", file=sys.stderr)
        return 0
    try:
        inp = common.read_payload()
        return run(inp, common) if inp is not None else 0
    except Unconfigured as e:
        return unconfigured(common, e, inp)
    except Exception as e:
        return common.fail_open(COMPONENT, e, inp)


if __name__ == "__main__":
    sys.exit(main())
