#!/usr/bin/env python3
"""
loop.py — 3-agent autonomous-coding harness orchestrator.

Drives Claude Code (`claude -p`) as three independent, fresh-context roles that
communicate ONLY through durable on-disk artifacts:

    Planner    -> spec.json         (the WHAT: requirements)
    Generator  <-> Evaluator        (negotiate contracts.json: the HOW-VERIFIED)
    Generator  -> code               (BUILD one requirement)
    Evaluator  -> evaluations.json  (VERDICT via real-user browser tests)

The orchestrator itself holds NO agent memory. It is a deterministic state
machine: each iteration selects one role+phase, runs a single `claude -p`
session, then reads the resulting artifact to compute the next transition.
All progress lives on disk (spec/contracts/evaluations/state.json + git), so
Ctrl+C then re-running the same command resumes cleanly.

Stdlib only. Requires the `claude` CLI on PATH and Node (for the Playwright MCP).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = HARNESS_DIR / "prompts"
MCP_CONFIG = HARNESS_DIR / "mcp.json"

DEFAULT_MODEL = "us.anthropic.claude-opus-4-8"
AUTO_CONTINUE_DELAY_SECONDS = 3

# Role families → which CLI flag supplies their model. The Generator does the
# bulk of the sessions and benefits most from a faster/cheaper model; Planner
# and Evaluator stay on the (default) heavier model for quality of the spec and
# of the adversarial verdicts. resolve_model() maps a role_key to the right one.
GENERATOR_ROLES = {"generator_negotiate", "generator_build", "generator_fastbuild"}
EVALUATOR_ROLES = {"evaluator_negotiate", "evaluator_verdict", "evaluator_probe"}
PLANNER_ROLES = {"planner"}

# Complexity tiers (set per-requirement by the Planner in spec.json) decide how
# much contract ceremony a requirement gets — the process is proportional to
# difficulty instead of flat. An UNTAGGED requirement defaults to COMPLEX (full
# ceremony), so older specs / in-flight runs behave exactly as before.
#   trivial  → fast-path: ONE Generator session writes a self-contract AND builds;
#              the Evaluator still runs the independent verdict. (2 sessions)
#   standard → Generator proposes a contract, orchestrator auto-accepts it (no
#              separate review session), build, verdict. (3 sessions)
#   complex  → full adversarial flow: propose → review (may counter) → build →
#              verdict. (4+ sessions)
COMPLEXITY_TRIVIAL = "trivial"
COMPLEXITY_STANDARD = "standard"
COMPLEXITY_COMPLEX = "complex"
COMPLEXITY_TIERS = {COMPLEXITY_TRIVIAL, COMPLEXITY_STANDARD, COMPLEXITY_COMPLEX}


def req_complexity(req: dict) -> str:
    """Requirement's complexity tier; unknown/absent → COMPLEX (full ceremony)."""
    c = req.get("complexity")
    return c if c in COMPLEXITY_TIERS else COMPLEXITY_COMPLEX


def req_extends(req: dict):
    """A requirement may declare it *extends* an earlier one (`extends: <id>`):
    a generalization or narrower case whose behavior a correct implementation of
    the parent likely ALREADY satisfies (e.g. multi-row clear extends single-row
    clear; the algorithm generalizes with no new code). For these, the harness
    tries a verify-FIRST probe: run the Evaluator against the extended behavior
    up front, and only fall through to the normal negotiate→build→verify cycle if
    the probe fails. Turns a redundant 4-session requirement into 1 session when
    the capability is already present. Absent/blank → None (normal flow)."""
    e = req.get("extends")
    return e if isinstance(e, str) and e.strip() else None


def resolve_model(role_key: str, args, req: dict | None = None) -> str:
    """Pick the model id for a role, each falling back to the shared --model.

    The final verdict is normally the trust anchor and stays on the (heavier)
    evaluator model. But a requirement the Planner tagged
    `verification: "deterministic"` (pass/fail decided by reading `data-*`/DOM
    values, not by judging a screenshot) can have its verdict run on a cheaper
    model without losing trust — routed via --deterministic-verdict-model.
    """
    if role_key in ("evaluator_verdict", "evaluator_probe") and req is not None:
        if req.get("verification") == "deterministic" and args.deterministic_verdict_model:
            return args.deterministic_verdict_model
    if role_key in GENERATOR_ROLES:
        return args.generator_model or args.model
    if role_key in EVALUATOR_ROLES:
        return args.evaluator_model or args.model
    if role_key in PLANNER_ROLES:
        return args.planner_model or args.model
    return args.model

# ---------------------------------------------------------------------------
# Artifact filenames (relative to the project dir)
# ---------------------------------------------------------------------------
APP_SPEC = "app_spec.txt"        # user-authored: the high-level ask
SPEC = "spec.json"               # Planner output: requirements baseline
CONTRACTS = "contracts.json"     # Generator<->Evaluator negotiated criteria
EVALUATIONS = "evaluations.json" # Evaluator verdicts
STATE = "state.json"             # orchestrator bookkeeping (resumable)
PROGRESS = "claude-progress.txt" # human-readable shift log


# ---------------------------------------------------------------------------
# Small JSON helpers — tolerant of missing/partial files
# ---------------------------------------------------------------------------
def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
# Per-requirement phases stored in state.json:
#   "negotiating" -> contract handshake in progress
#   "building"    -> Generator implements the agreed contract
#   "evaluating"  -> Evaluator runs the contract's tests
#   "done"        -> verdict == pass
#   "escalated"   -> a cap was hit; needs human review
PHASE_NEGOTIATING = "negotiating"
PHASE_BUILDING = "building"
PHASE_EVALUATING = "evaluating"
PHASE_DONE = "done"
PHASE_ESCALATED = "escalated"


def spec_requirements(project_dir: Path):
    """Requirements from spec.json, sorted by ascending priority (1 = first)."""
    spec = load_json(project_dir / SPEC, {})
    reqs = spec.get("requirements", []) if isinstance(spec, dict) else []
    return sorted(reqs, key=lambda r: r.get("priority", 1_000_000))


def req_state(state: dict, req_id: str) -> dict:
    """Per-requirement record in state.json, created lazily."""
    return state.setdefault("requirements", {}).setdefault(
        req_id,
        {"phase": PHASE_NEGOTIATING, "round": 0, "retry": 0},
    )


def contract_status(project_dir: Path, req_id: str) -> str | None:
    contracts = load_json(project_dir / CONTRACTS, {})
    entry = (contracts.get("contracts", {}) if isinstance(contracts, dict) else {}).get(req_id)
    return entry.get("status") if isinstance(entry, dict) else None


def accept_contract(project_dir: Path, req_id: str, note: str) -> None:
    """Orchestrator-side auto-accept of a proposed contract (standard tier).

    Flips the requirement's contract `status` to "agreed" without spending an
    Evaluator review session. Only touches this requirement's entry.
    """
    path = project_dir / CONTRACTS
    contracts = load_json(path, {})
    if not isinstance(contracts, dict):
        return
    entry = contracts.get("contracts", {}).get(req_id)
    if isinstance(entry, dict):
        entry["status"] = "agreed"
        entry["evaluator_notes"] = note
        save_json(path, contracts)


def select_role(project_dir: Path, state: dict, only: str | None):
    """
    Return (role_key, req_id, note) for the next session, or (None, None, note)
    when there is nothing left to do. `role_key` maps to a prompt file.

    This is the whole control flow of the harness — deliberately explicit.
    """
    # Phase 0: no spec yet -> Planner bootstraps everything.
    if not (project_dir / SPEC).exists():
        return "planner", None, "no spec.json — bootstrapping with Planner"

    reqs = spec_requirements(project_dir)
    if not reqs:
        return None, None, "spec.json has no requirements"

    if only:
        reqs = [r for r in reqs if r.get("id") == only]
        if not reqs:
            return None, None, f"--only {only!r} matched no requirement"

    for req in reqs:
        rid = req.get("id")
        rs = req_state(state, rid)
        phase = rs["phase"]

        if phase in (PHASE_DONE, PHASE_ESCALATED):
            continue

        if phase == PHASE_NEGOTIATING:
            complexity = req_complexity(req)
            status = contract_status(project_dir, rid)

            # VERIFY-FIRST (extension requirements): if this requirement declares
            # `extends: <parent-id>` and the parent is already done, try one probe
            # session — the Evaluator writes a contract for the extended behavior
            # and verdicts it against the CURRENT code, no build. If the parent's
            # implementation already generalizes (the common case), it passes and
            # we skip negotiate+build entirely. Fires at most once; on fail we fall
            # through to the normal cycle below (with a contract already on disk).
            parent = req_extends(req)
            if (parent and not rs.get("probed")
                    and req_state(state, parent)["phase"] == PHASE_DONE):
                rs["probed"] = True
                return "evaluator_probe", rid, f"{rid}: verify-first probe (extends {parent})"

            # TRIVIAL: skip negotiation entirely — one Generator session writes its
            # own (agreed) contract and builds. The Evaluator still verdicts after.
            if complexity == COMPLEXITY_TRIVIAL:
                if status == "agreed":
                    # fastbuild already recorded the contract (e.g. resumed run) →
                    # proceed to a normal build/verify against it.
                    rs["phase"] = PHASE_BUILDING
                    return "generator_build", rid, f"{rid}: contract agreed — build"
                return "generator_fastbuild", rid, f"{rid}: trivial — self-contract + build"

            # STANDARD / COMPLEX both start by having the Generator propose.
            if status is None or status == "countered":
                if rs["round"] >= state["_caps"]["negotiation_rounds"]:
                    rs["phase"] = PHASE_ESCALATED
                    return "escalate", rid, f"{rid}: negotiation exceeded round cap"
                rs["round"] += 1
                return "generator_negotiate", rid, f"{rid}: propose/revise contract (round {rs['round']})"
            if status == "proposed":
                # STANDARD: orchestrator auto-accepts the proposal (no separate
                # Evaluator review session). COMPLEX: run the adversarial review.
                if complexity == COMPLEXITY_STANDARD:
                    accept_contract(project_dir, rid,
                                    "Auto-accepted by orchestrator (standard tier: "
                                    "no adversarial review; independent verdict still runs).")
                    rs["phase"] = PHASE_BUILDING
                    return "generator_build", rid, f"{rid}: contract auto-accepted (standard) — build"
                return "evaluator_negotiate", rid, f"{rid}: evaluator reviews proposed contract"
            if status == "agreed":
                rs["phase"] = PHASE_BUILDING
                return "generator_build", rid, f"{rid}: contract agreed — build"

        if phase == PHASE_BUILDING:
            return "generator_build", rid, f"{rid}: build (attempt {rs['retry'] + 1})"

        if phase == PHASE_EVALUATING:
            return "evaluator_verdict", rid, f"{rid}: evaluate against contract"

    return None, None, "all requirements done or escalated"


def apply_result(project_dir: Path, state: dict, role_key: str, req_id: str) -> None:
    """
    After a session, read the artifact it produced and advance state.json.
    The orchestrator trusts the on-disk artifacts, not the agent's narration.
    """
    if req_id is None:
        return
    rs = req_state(state, req_id)

    if role_key in ("generator_build", "generator_fastbuild"):
        # Building finished (fastbuild also wrote its own agreed contract) -> hand
        # off to independent evaluation.
        rs["phase"] = PHASE_EVALUATING

    elif role_key == "evaluator_probe":
        # Verify-first probe: the Evaluator verdicted the extended behavior against
        # the existing code. Pass -> done with no build. Fail -> leave the req in
        # `negotiating`; select_role() falls through to the normal cycle (and won't
        # re-probe, since rs["probed"] is set). The probe leaves a countered/absent
        # contract so the Generator revises it into something buildable.
        evals = load_json(project_dir / EVALUATIONS, {})
        entry = (evals.get("evaluations", {}) if isinstance(evals, dict) else {}).get(req_id, {})
        verdict = entry.get("verdict") if isinstance(entry, dict) else None
        if verdict == "pass":
            rs["phase"] = PHASE_DONE
        # else: stay in PHASE_NEGOTIATING, normal flow resumes next iteration.

    elif role_key == "evaluator_verdict":
        evals = load_json(project_dir / EVALUATIONS, {})
        entry = (evals.get("evaluations", {}) if isinstance(evals, dict) else {}).get(req_id, {})
        verdict = entry.get("verdict") if isinstance(entry, dict) else None
        if verdict == "pass":
            rs["phase"] = PHASE_DONE
        else:
            # Fail (or missing verdict) -> retry the build with failure evidence,
            # unless we've exhausted the retry budget.
            rs["retry"] += 1
            if rs["retry"] >= state["_caps"]["build_retries"]:
                rs["phase"] = PHASE_ESCALATED
            else:
                rs["phase"] = PHASE_BUILDING
    # generator_negotiate / evaluator_negotiate: no phase change here — the next
    # select_role() reads contracts.json status to decide the transition.


# ---------------------------------------------------------------------------
# Prompt composition
# ---------------------------------------------------------------------------
def compose_prompt(role_key: str, req_id: str | None) -> str:
    """Role prompt file + an injected task header naming the one requirement."""
    role_file = PROMPTS_DIR / f"{role_key}.md"
    body = role_file.read_text()
    header_lines = [
        "# YOUR TASK THIS SESSION",
        "",
        f"Role: **{role_key}**",
    ]
    if req_id:
        header_lines.append(f"Target requirement id: **{req_id}**")
        header_lines.append(
            f"Work on ONLY this requirement. Read its entry in {SPEC} "
            f"(and {CONTRACTS}/{EVALUATIONS} as your role requires)."
        )
    header_lines += ["", "---", ""]
    return "\n".join(header_lines) + body


# ---------------------------------------------------------------------------
# Session execution
# ---------------------------------------------------------------------------
# `acceptEdits` auto-approves file edits + read-only bash, but in headless (-p)
# mode it does NOT auto-approve arbitrary Bash (npm/git/./init.sh) or MCP tools —
# those must be on an explicit allowlist or the calls fail non-interactively.
# We list the full working set the roles need. Comma-separated single arg so it
# can't accidentally swallow the next flag.
ALLOWED_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,mcp__playwright__*"


def build_cmd(prompt_text: str, project_dir: Path, model: str, max_turns: int) -> list[str]:
    return [
        "claude", "-p", prompt_text,
        "--append-system-prompt-file", str(PROMPTS_DIR / "system.md"),
        "--permission-mode", "acceptEdits",
        "--allowedTools", ALLOWED_TOOLS,
        "--max-turns", str(max_turns),
        "--model", model,
        "--mcp-config", str(MCP_CONFIG),
        # Only Playwright — don't inherit ambient user MCP servers (keeps every
        # session lean and portable across machines).
        "--strict-mcp-config",
        "--add-dir", str(project_dir),
        "--output-format", "stream-json",
        "--verbose",
    ]


def _truncate(text: str, limit: int = 200) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


def print_stream_event(raw: str) -> None:
    """Pretty-print one stream-json line as a compact, watchable log line."""
    line = raw.strip()
    if not line:
        return
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        return  # ignore non-JSON noise

    etype = evt.get("type")
    if etype == "assistant":
        for block in evt.get("message", {}).get("content", []):
            btype = block.get("type")
            if btype == "text" and block.get("text", "").strip():
                print(f"  🗣  {_truncate(block['text'], 300)}")
            elif btype == "tool_use":
                print(f"  🔧 {block.get('name')}  {_truncate(json.dumps(block.get('input', {})))}")
    elif etype == "user":
        for block in evt.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                flag = "❌" if block.get("is_error") else "✓"
                print(f"  {flag} tool_result  {_truncate(content)}")
    elif etype == "result":
        cost = evt.get("total_cost_usd")
        turns = evt.get("num_turns")
        suffix = f"  (turns={turns}, cost=${cost:.4f})" if isinstance(cost, (int, float)) else ""
        print(f"  ── session result: {evt.get('subtype', 'ok')}{suffix}")


def run_session(prompt_text: str, project_dir: Path, model: str, max_turns: int) -> int:
    """Run one `claude -p` session, streaming its output. Returns exit code."""
    cmd = build_cmd(prompt_text, project_dir, model, max_turns)
    proc = subprocess.Popen(
        cmd,
        cwd=str(project_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        for raw in proc.stdout:  # type: ignore[union-attr]
            print_stream_event(raw)
    finally:
        proc.stdout.close()  # type: ignore[union-attr]
    return proc.wait()


# ---------------------------------------------------------------------------
# Orchestration loop
# ---------------------------------------------------------------------------
def pause_for_human(message: str) -> None:
    print(f"\n⏸  {message}")
    try:
        input("   Press Enter to continue, or Ctrl+C to stop and resume later… ")
    except EOFError:
        # Non-interactive stdin: don't hang, just continue.
        print("   (stdin not interactive — continuing)")


def summarize(project_dir: Path, state: dict) -> None:
    reqs = spec_requirements(project_dir)
    done = sum(1 for r in reqs if req_state(state, r["id"])["phase"] == PHASE_DONE)
    esc = [r["id"] for r in reqs if req_state(state, r["id"])["phase"] == PHASE_ESCALATED]
    print(f"\n=== {done}/{len(reqs)} requirements done ===")
    if esc:
        print(f"⚠  escalated (need human review): {', '.join(esc)}")


def show_status(project_dir: Path) -> int:
    """Read-only snapshot of a run: per-requirement phase + git tail. Spawns nothing."""
    project_dir = project_dir.resolve()
    if not (project_dir / SPEC).exists():
        print(f"No {SPEC} yet in {project_dir} — the Planner hasn't run (or wrong dir).")
        return 1
    state = load_json(project_dir / STATE, {})
    reqs = spec_requirements(project_dir)
    glyph = {PHASE_DONE: "✅", PHASE_ESCALATED: "⛔", PHASE_BUILDING: "🔨",
             PHASE_EVALUATING: "🔍", PHASE_NEGOTIATING: "🤝"}
    tier_glyph = {COMPLEXITY_TRIVIAL: "○", COMPLEXITY_STANDARD: "◐", COMPLEXITY_COMPLEX: "●"}
    print(f"project : {project_dir}")
    print(f"{'requirement':<16} {'phase':<12} rnd/try tier description")
    print("-" * 84)
    for r in reqs:
        rs = req_state(state, r["id"])
        ph = rs["phase"]
        tier = req_complexity(r)
        det = "*" if r.get("verification") == "deterministic" else " "
        print(f"{glyph.get(ph,'·')} {r['id']:<14} {ph:<12} "
              f"{rs['round']}/{rs['retry']}   {tier_glyph.get(tier,'?')}{det}   "
              f"{_truncate(r.get('description',''), 40)}")
    print("  tier: ○ trivial (fast-path)  ◐ standard (auto-accept)  ● complex (full)   * = deterministic verdict")
    summarize(project_dir, state)
    # Cheap, read-only git tail for orientation.
    try:
        out = subprocess.run(["git", "-C", str(project_dir), "log", "--oneline", "-5"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            print("\nrecent commits:")
            for line in out.stdout.strip().splitlines():
                print(f"  {line}")
    except (OSError, subprocess.SubprocessError):
        pass
    return 0


def orchestrate(args) -> int:
    project_dir: Path = args.project_dir.resolve()
    project_dir.mkdir(parents=True, exist_ok=True)

    # Copy the harness mcp.json is referenced by absolute path; nothing to copy.
    state_path = project_dir / STATE
    state = load_json(state_path, {})
    state.setdefault("requirements", {})
    # Caps live in state so a resume respects the original run's budgets unless
    # the user overrides them on the CLI.
    state["_caps"] = {
        "negotiation_rounds": args.negotiation_rounds,
        "build_retries": args.build_retries,
    }

    iteration = 0
    while args.max_iterations is None or iteration < args.max_iterations:
        role_key, req_id, note = select_role(project_dir, state, args.only)
        save_json(state_path, state)  # persist any phase transitions select_role made

        if role_key is None:
            print(f"\n✅ Nothing left to do: {note}")
            summarize(project_dir, state)
            return 0

        if role_key == "escalate":
            summarize(project_dir, state)
            pause_for_human(f"ESCALATION — {note}. Inspect artifacts/diff, then resume.")
            # After a human pause we re-loop; state already marks it escalated so
            # it won't spin. The human is expected to fix state.json/contracts by
            # hand (documented in README) before resuming.
            return 2

        iteration += 1
        cur_req = next((r for r in spec_requirements(project_dir) if r.get("id") == req_id), None)
        model = resolve_model(role_key, args, cur_req)
        print(f"\n{'='*72}\n▶ iteration {iteration} | {role_key} | {note}\n"
              f"  model: {model}\n{'='*72}")

        prompt_text = compose_prompt(role_key, req_id)

        # A session can exit non-zero for a *transient* reason — an API/network
        # timeout, a Bedrock throttle, a dropped connection — none of which mean
        # the build is broken. Because all durable state is on disk and each
        # session is idempotent (re-running the same role+requirement just
        # recomputes the same artifact), it is safe to simply retry. We retry
        # with exponential backoff before treating the failure as fatal, so a
        # single hiccup no longer kills a multi-hour run.
        code = run_session(prompt_text, project_dir, model, args.max_turns)
        attempt = 0
        while code != 0 and attempt < args.session_retries:
            attempt += 1
            backoff = args.retry_backoff * (2 ** (attempt - 1))
            print(f"\n⚠ session exited with code {code} "
                  f"(likely transient). Retry {attempt}/{args.session_retries} "
                  f"in {backoff}s… State is on disk; this re-runs the same session.")
            time.sleep(backoff)
            code = run_session(prompt_text, project_dir, model, args.max_turns)

        if code != 0:
            print(f"\n⚠ session still failing after {args.session_retries} "
                  f"retries (exit {code}). State saved; rerun the same command to resume.")
            save_json(state_path, state)
            return code
        if attempt:
            print(f"  ✓ recovered after {attempt} retr{'y' if attempt == 1 else 'ies'}.")

        apply_result(project_dir, state, role_key, req_id)
        save_json(state_path, state)

        if args.review_every and iteration % args.review_every == 0:
            summarize(project_dir, state)
            pause_for_human(f"Scheduled review after {iteration} iteration(s).")

        time.sleep(AUTO_CONTINUE_DELAY_SECONDS)

    print(f"\n⏹ reached --max-iterations={args.max_iterations}. Rerun to continue.")
    summarize(project_dir, state)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="3-agent (Planner/Generator/Evaluator) autonomous-coding harness.",
    )
    p.add_argument("--project-dir", type=Path, required=True,
                   help="Working dir for the app being built (holds all artifacts).")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"Shared/fallback model id for any role without its own "
                        f"override (default: {DEFAULT_MODEL}).")
    p.add_argument("--generator-model", default=None,
                   help="Model id for the Generator roles (negotiate + build). "
                        "Falls back to --model. Use a faster/cheaper model here.")
    p.add_argument("--evaluator-model", default=None,
                   help="Model id for the Evaluator roles (negotiate + verdict). "
                        "Falls back to --model.")
    p.add_argument("--planner-model", default=None,
                   help="Model id for the Planner. Falls back to --model.")
    p.add_argument("--deterministic-verdict-model", default=None,
                   help="Model id for the verdict of requirements the Planner tagged "
                        "verification='deterministic' (pass/fail read from data-*/DOM, "
                        "not judged visually). Falls back to the evaluator model.")
    p.add_argument("--max-iterations", type=int, default=None,
                   help="Stop after N sessions (default: unlimited).")
    p.add_argument("--max-turns", type=int, default=200,
                   help="Per-session agent-turn cap passed to claude (default: 200).")
    p.add_argument("--negotiation-rounds", type=int, default=3,
                   help="Max contract-negotiation rounds before escalating (default: 3).")
    p.add_argument("--build-retries", type=int, default=3,
                   help="Max build attempts per requirement before escalating (default: 3).")
    p.add_argument("--session-retries", type=int, default=4,
                   help="Retries for a session that exits non-zero for a transient "
                        "reason (API/network timeout, throttle) before giving up "
                        "(default: 4). Set 0 to fail fast on the first non-zero exit.")
    p.add_argument("--retry-backoff", type=int, default=30,
                   help="Base seconds for exponential backoff between session "
                        "retries (30 → 60 → 120 → 240…). Default: 30.")
    p.add_argument("--review-every", type=int, default=0,
                   help="Pause for human review every N iterations (0 = never).")
    p.add_argument("--only", default=None,
                   help="Run a single requirement id end-to-end (cheap testing).")
    p.add_argument("--status", action="store_true",
                   help="Print a read-only progress snapshot and exit (spawns nothing).")
    return p.parse_args(argv)


def preflight() -> None:
    if shutil.which("claude") is None:
        sys.exit("error: `claude` CLI not found on PATH. Install: npm i -g @anthropic-ai/claude-code")
    if not MCP_CONFIG.exists():
        sys.exit(f"error: missing {MCP_CONFIG}")
    missing = [f for f in ("system.md", "planner.md", "generator_negotiate.md",
                           "evaluator_negotiate.md", "generator_build.md", "evaluator_verdict.md",
                           "generator_fastbuild.md", "evaluator_probe.md")
               if not (PROMPTS_DIR / f).exists()]
    if missing:
        sys.exit(f"error: missing prompt files in {PROMPTS_DIR}: {', '.join(missing)}")


def main(argv=None) -> int:
    # Line-buffer stdout so the live session log streams in real time even when
    # piped/redirected (Python block-buffers a non-TTY stdout by default).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass  # very old Python; interactive TTY use is unaffected
    args = parse_args(argv)
    if args.status:
        return show_status(args.project_dir)
    preflight()
    print(f"project dir : {args.project_dir.resolve()}")
    print(f"models      : planner={resolve_model('planner', args)}")
    print(f"              generator={resolve_model('generator_build', args)}")
    print(f"              evaluator={resolve_model('evaluator_verdict', args)}")
    if args.deterministic_verdict_model:
        print(f"              verdict(deterministic)={args.deterministic_verdict_model}")
    print(f"caps        : negotiation_rounds={args.negotiation_rounds}, build_retries={args.build_retries}")
    if not (args.project_dir.resolve() / APP_SPEC).exists():
        print(f"\nⓘ No {APP_SPEC} found yet. Create {args.project_dir.resolve()/APP_SPEC} "
              f"describing the app to build, then rerun.")
        return 1
    try:
        return orchestrate(args)
    except KeyboardInterrupt:
        print("\n\n⏸ Interrupted. All state is on disk — rerun the SAME command to resume "
              "from the next pending phase.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
