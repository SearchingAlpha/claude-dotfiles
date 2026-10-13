"""Shared plumbing for the guard hooks: payload reading, config, state dir,
the PreToolUse decision, error logging and the fail-open path. Stdlib only,
Python 3.9+.

Every guard fails OPEN on its own errors: the error goes to stderr and to
~/.claude/guards/errors.log (values scrubbed), the tool call goes on, and the
user sees one notice per session and component. Only a positive detection
blocks. A config value of the wrong type is logged and read as its default."""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import List, Optional

LOG_MAX = 1_000_000          # rotate errors.log past this many bytes
NOTICE_TTL = 7 * 86400       # forget per-session notice markers after a week


def home() -> Path:
    """State dir: $CLAUDE_GUARDS_HOME, else ~/.claude/guards."""
    env = os.environ.get("CLAUDE_GUARDS_HOME")
    return Path(env) if env else Path.home() / ".claude" / "guards"


def config() -> dict:
    """$CLAUDE_GUARDS_CONFIG, else <home>/config.json. Missing → {}. Unreadable or
    invalid → {} plus a logged error (a broken config must not block anything)."""
    path = Path(os.environ.get("CLAUDE_GUARDS_CONFIG") or home() / "config.json")
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        log_error("config", e)
        return {}
    try:
        cfg = json.loads(text)
    except ValueError as e:
        log_error("config", ValueError(f"{path}: invalid JSON ({e})"))
        return {}
    return cfg if isinstance(cfg, dict) else {}


_TRUE = ("true", "yes", "on", "1")
_FALSE = ("false", "no", "off", "0")


def flag(cfg: dict, key: str, default: bool, where: str = "config") -> bool:
    """A boolean setting. Accepts JSON booleans and "true"/"false"-like strings;
    anything else is logged and read as the default."""
    v = cfg.get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in _TRUE + _FALSE:
        return v.strip().lower() in _TRUE
    log_error("config", ValueError(f"{where}.{key} should be true or false, not {v!r}; "
                                   f"using {str(default).lower()}"))
    return default


def disabled(component: str, cfg: dict) -> bool:
    """Off via CLAUDE_GUARDS_DISABLE=<name>[,<name>|all] or `"<name>": false` in config."""
    off = {s.strip() for s in os.environ.get("CLAUDE_GUARDS_DISABLE", "").split(",")}
    if component in off or "all" in off:
        return True
    return isinstance(cfg.get(component), (bool, str)) and not flag(cfg, component, True)


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def _str_list(cfg: dict, key: str) -> List[str]:
    """A list-of-paths setting; a single string counts as a one-element list."""
    v = cfg.get(key)
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        log_error("config", ValueError(f"{key} should be a list of paths, not {type(v).__name__}"))
        return []
    return [p for p in v if isinstance(p, str) and p.strip()]


def vault_commit_cfg(cfg: dict) -> Optional[dict]:
    """The vault auto-commit settings, or None when the feature is off (default).
    On via config `vault_commit.path` or env CLAUDE_GUARDS_VAULT. `push` and
    `sweep` come back as real booleans."""
    vc = cfg.get("vault_commit")
    vc = dict(vc) if isinstance(vc, dict) else {}
    env = os.environ.get("CLAUDE_GUARDS_VAULT")
    if env:
        vc["path"] = env
    path = vc.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    vc["path"] = _real(path)
    vc["push"] = flag(vc, "push", True, "vault_commit")
    vc["sweep"] = flag(vc, "sweep", False, "vault_commit")
    for key in ("remote", "branch", "message_prefix"):
        if not isinstance(vc.get(key), str) or not vc[key].strip():
            vc.pop(key, None)
    return vc


def vault_dirs(cfg: dict) -> List[str]:
    """Directories where no secret may be written, gitignored or not: config
    `vault_dirs`, plus the vault_commit path."""
    out = [_real(p) for p in _str_list(cfg, "vault_dirs")]
    vc = vault_commit_cfg(cfg)
    if vc:
        out.append(vc["path"])
    return out


def under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def read_payload() -> Optional[dict]:
    """The hook's JSON input, or None for anything that isn't a JSON object."""
    raw = sys.stdin.read()
    if not raw.strip():
        return None
    try:
        inp = json.loads(raw)
    except ValueError:
        return None
    return inp if isinstance(inp, dict) else None


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def decide(decision: str, reason: str, notice: str = "") -> int:
    """A PreToolUse permission decision (deny / ask) as JSON with exit 0: the
    documented single channel, which blocks in every mode (Claude Code 2.1.295
    still prefixes the reason Claude sees with "<event>:<tool> hook error"). If
    stdout is gone, a deny falls back to exit 2 with the reason on stderr, which
    blocks just the same. Returns the exit code."""
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                  "permissionDecisionReason": reason}}
    if notice:
        out["systemMessage"] = notice
    try:
        emit(out)
        return 0
    except Exception:
        print(reason, file=sys.stderr)
        try:                    # let the interpreter's final flush succeed (else exit 120)
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 2 if decision == "deny" else 0


def _scrub(text: str) -> str:
    try:
        import patterns
        return patterns.redact(text, honor_marker=False)[0]
    except Exception:
        return text


def log_error(component: str, err: BaseException) -> None:
    """Append one scrubbed line to <home>/errors.log; never raises."""
    try:
        d = home()
        d.mkdir(parents=True, exist_ok=True)
        path = d / "errors.log"
        try:
            if path.stat().st_size > LOG_MAX:
                os.replace(path, d / "errors.log.1")
        except OSError:
            pass
        tb = traceback.extract_tb(err.__traceback__) if err.__traceback__ else []
        where = f" at {Path(tb[-1].filename).name}:{tb[-1].lineno}" if tb else ""
        msg = _scrub(f"{type(err).__name__}: {err}")[:400].replace("\n", " ")
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {component}{where}: {msg}\n")
    except Exception:
        pass


def notice_once(component: str, session_id: str) -> bool:
    """True the first time a component asks in a session (one notice per session)."""
    try:
        d = home() / "notices"
        d.mkdir(parents=True, exist_ok=True)
        sid = "".join(c for c in (session_id or "nosession") if c.isalnum() or c in "-_")[:80]
        marker = d / f"{component}-{sid}"
        if marker.exists():
            return False
        marker.touch()
        now = time.time()
        for old in d.iterdir():
            try:
                if now - old.stat().st_mtime > NOTICE_TTL:
                    old.unlink()
            except OSError:
                pass
        return True
    except Exception:
        return False


def fail_open(component: str, err: BaseException, inp: Optional[dict] = None,
              notify: bool = True) -> int:
    """Report an internal error and let the tool call go on. Returns exit code 0.
    notify=False for async hooks: their systemMessage reaches Claude, not you, so
    the once-per-session notice is left for a synchronous call to show."""
    log_error(component, err)
    try:
        print(f"guards/{component}: internal error ({type(err).__name__}); not enforced "
              f"for this call. See {home() / 'errors.log'}", file=sys.stderr)
        sid = (inp or {}).get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
        if notify and notice_once(component, sid):
            emit({"systemMessage": f"guards/{component} hit an internal error and is not "
                                   f"checking tool calls; see {home() / 'errors.log'}"})
    except Exception:
        pass
    return 0
