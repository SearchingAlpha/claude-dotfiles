#!/usr/bin/env python3
"""PreToolUse guard for redshift-mcp execute_query / explain_query (any server
name containing "redshift": redshift-mcp, redshift-mcp-server, the planned
aaisd-redshift-mcp): refuse a read query the server would mangle.

Why (read from redshift-mcp-server 0.8.1.173, core/services/redshift_service.py
and tools/explain.py): in read-only mode (the default; MCP_READ_ONLY_MODE unset
here) the server passes a statement through only when it *starts* with
SELECT/WITH/SHOW/DESCRIBE/EXPLAIN (`READ_ONLY_PATTERN.match`) or is a temp-table
operation. Anything else is wrapped in `BEGIN READ ONLY; …; END;`: no result
set comes back through the Data API, and a trailing `--` comment swallows the
`END;`. explain_query rejects it outright. So a leading `--` or `/* */`
comment, or a leading `(` as in `(SELECT …) UNION (SELECT …)`, is denied with
the fix. Other statements pass: writes fail on the server's own read-only
error, which is clear. Comments *inside* the statement are fine. Fails open."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # hooks/guards: common.py

COMPONENT = "sql_guard"
TOOL = re.compile(r"^mcp__.*redshift.*__(?:execute|explain)_query$")


def run(inp: dict, common) -> int:
    if not TOOL.match(str(inp.get("tool_name") or "")):
        return 0
    sql = (inp.get("tool_input") or {}).get("sql")
    if not isinstance(sql, str):
        return 0
    head = sql.strip()
    if head.startswith(("--", "/*")):
        what, fix = ("starts with a comment",
                     "Start with the keyword and put the comment inside, e.g. `SELECT /* why */ …` "
                     "or a -- comment on a later line, or drop it.")
    elif head.startswith("("):
        what, fix = ("starts with a parenthesis",
                     "Start with SELECT or WITH, e.g. `SELECT * FROM (SELECT …) a UNION SELECT …` "
                     "or `WITH a AS (…), b AS (…) SELECT * FROM a UNION SELECT * FROM b`.")
    else:
        return 0
    if common.disabled(COMPONENT, common.config()):
        return 0
    return common.decide("deny",
                         f"sql_guard: the statement {what}. redshift-mcp only treats SQL as "
                         f"read-only when it begins with SELECT/WITH/SHOW/DESCRIBE/EXPLAIN; "
                         f"anything else makes execute_query wrap it in BEGIN READ ONLY … END "
                         f"(no rows come back) and explain_query reject it. {fix}")


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
    except Exception as e:
        return common.fail_open(COMPONENT, e, inp)


if __name__ == "__main__":
    sys.exit(main())
