"""Secret-shaped literal patterns shared by the guard hooks and the git
pre-commit scanner. Stdlib only, Python 3.9+.

`scan(text)` returns findings as (kind, start, end) offsets. Callers report the
kind and line number, never the matched text, so a secret is not echoed into a
transcript, a terminal or a log while being blocked.

Three families of rules:
- named shapes with a fixed prefix (sk-ant-, AKIA, xoxb-, ghp_, glpat-, eyJ…,
  PEM headers …), precise enough to apply anywhere. A PEM private key's
  finding runs from its BEGIN line to its END line, so redaction hides the
  whole body;
- credentials in context: `scheme://user:password@host`, `mysql -p<pw>` (the
  client as a command word), `sshpass -p <pw>`, `Bearer <token>`;
- assignments (`API_KEY=...`, `"client_secret": "..."`, `--token=...`) whose
  name says key/token/secret/password and whose value (20+ characters) is hex
  or random-looking. A bare hash, UUID, base64 blob or public key is never
  flagged on its own, an ARN's `:secret:<name>` segment is not an assignment,
  and a name that only ends in `key` (`cache_key`, `"key"`) needs a random
  mixed-case value that isn't a file name, or an UPPER_CASE env-style name.

A line containing ALLOW_MARKER is skipped (intentional fixtures), when the
caller asks for it.

Speed matters: the guards run on every tool call. Every regex starts with a
literal or a one-character class, so the engine can skip ahead; word-boundary
checks happen in Python after a match, or as a lookbehind placed after the
literal, never as a leading lookbehind, which would disable that skip. No rule
retries a candidate inside a run it already scanned, so the cost is linear:
ordinary text and base64 (of JSON too) scan at 30-50 ms per MB, input dense in
PEM headers or JWTs at about 200 ms per MB, and input that is nothing but
secret-named assignments (a finding every 50 bytes) at about 500 ms per MB."""

from __future__ import annotations

import bisect
import math
import re
from typing import Callable, Iterator, List, NamedTuple, Optional, Tuple

ALLOW_MARKER = "guards:allow-secret"
PEM_MAX = 16_384             # a 4096-bit RSA key is ~3.3 KB of PEM; no END line past this


class Finding(NamedTuple):
    kind: str
    start: int
    end: int


# --- shape checks -----------------------------------------------------------------

_LOWER_RUN = re.compile(r"[a-z]+")
_UPPER_RUN = re.compile(r"[A-Z]+")
_LETTER_RUN = re.compile(r"[A-Za-z]+")


def entropy(s: str) -> float:
    """Shannon entropy in bits per character."""
    if not s:
        return 0.0
    n = len(s)
    counts: dict = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def _avg_run(rx, v: str) -> float:
    runs = rx.findall(v)
    return sum(map(len, runs)) / len(runs) if runs else 0.0


def looks_random(v: str) -> bool:
    """A credential-like random string: upper, lower and digit, high entropy, and
    short same-case letter runs (camelCase identifiers and CAPS-WORD markers have
    long ones; random base62 averages under two)."""
    if not (any(c.isupper() for c in v) and any(c.islower() for c in v)
            and any(c.isdigit() for c in v)):
        return False
    if _avg_run(_LOWER_RUN, v) > 3.0 or _avg_run(_UPPER_RUN, v) > 3.0:
        return False
    return entropy(v) >= 3.5 and not _sequential(v)


def _random_two_class(v: str) -> bool:
    """Random alphanumerics drawn from two classes only: lower+digit, upper+digit
    (`secrets.choice(ascii_lowercase + digits)`) or mixed-case letters. Words,
    separators and long letter runs rule a value out."""
    if len(v) < 20 or not (v.isascii() and v.isalnum()) or _sequential(v):
        return False
    digits = sum(c.isdigit() for c in v)
    letters = v if not digits else "".join(c for c in v if c.isalpha())
    if not letters:
        return False
    if digits:
        if digits < 3 or not (letters.islower() or letters.isupper()):
            return False
        return _avg_run(_LETTER_RUN, v) <= 8.0 and entropy(v) >= 3.5
    return (_avg_run(_LOWER_RUN, v) <= 2.0 and _avg_run(_UPPER_RUN, v) <= 2.0
            and entropy(v) >= 3.8)


def _sequential(v: str, n: int = 6) -> bool:
    """A run of n ascending characters (123456, ABCDEF): a hand-typed fake."""
    run = 1
    for a, b in zip(v, v[1:]):
        run = run + 1 if ord(b) == ord(a) + 1 else 1
        if run >= n:
            return True
    return False


def _hf_token(v: str) -> bool:
    """hf_ + random mixed-case letters, not hf_ + a CamelCase class name (AutoModel...)."""
    body = v[3:]
    return any(c.isupper() for c in body) and any(c.islower() for c in body) \
        and _avg_run(_LOWER_RUN, body) <= 4.0 and _avg_run(_UPPER_RUN, body) <= 4.0


# --- named shapes -------------------------------------------------------------------

_ALNUM = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_IDENT = _ALNUM | {"_"}
_TOKEN = _IDENT | {"-"}

# (kind, regex, characters that must not come right before the match, extra check)
_NAMED: List[Tuple[str, "re.Pattern", frozenset, Optional[Callable[[str], bool]]]] = [
    ("anthropic-api-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), _TOKEN, None),
    ("openai-api-key", re.compile(r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{40,}"), _TOKEN, None),
    ("aws-access-key-id", re.compile(r"A(?:KIA|SIA)[0-9A-Z]{16}(?![A-Za-z0-9])"), _ALNUM, None),
    ("slack-token", re.compile(r"xox[abposre]-[0-9A-Za-z-]{20,}"), _ALNUM, None),
    ("slack-token", re.compile(r"xapp-[0-9]-[0-9A-Za-z-]{20,}"), _ALNUM, None),
    ("slack-webhook", re.compile(r"hooks\.slack\.com/services/T[0-9A-Z]{6,}/B[0-9A-Z]{6,}/[0-9A-Za-z]{20,}"),
     frozenset(), None),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,255}(?![A-Za-z0-9])"), _IDENT, None),
    ("github-token", re.compile(r"github_pat_[A-Za-z0-9_]{22,255}"), _IDENT, None),
    ("gitlab-token", re.compile(r"glpat-[0-9A-Za-z_-]{20,}"), _TOKEN, None),
    ("google-api-key", re.compile(r"AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])"), _TOKEN, None),
    ("stripe-key", re.compile(r"[rs]k_live_[0-9A-Za-z]{20,}"), _IDENT, None),
    ("huggingface-token", re.compile(r"hf_[A-Za-z0-9]{34,}(?![A-Za-z0-9])"), _IDENT, _hf_token),
    ("npm-token", re.compile(r"npm_[A-Za-z0-9]{36}(?![A-Za-z0-9])"), _IDENT, None),
    ("pypi-token", re.compile(r"pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}"), _TOKEN, None),
    ("sendgrid-key", re.compile(r"SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])"), _TOKEN, None),
    # header.payload.signature, both JSON parts base64url ("eyJ" = '{"'). The
    # lookbehind sits after the literal, so the engine still skips ahead to each
    # "eyJ" and drops one inside a longer base64 run at once: base64 of JSON holds
    # an "eyJ" every few dozen characters, and trying each of them against the rest
    # of the run made the scan quadratic (3 s for 90 KB).
    ("jwt", re.compile(r"eyJ(?<![A-Za-z0-9_-]eyJ)[A-Za-z0-9_-]{8,16384}\.eyJ[A-Za-z0-9_-]{8,16384}"
                       r"\.[A-Za-z0-9_-]{16,}"), _TOKEN, None),
]

# A PEM header alone (docs, ssh-keygen output) is not a key; one followed by a
# base64 body is. The body is looked for in a short window after the header; the
# finding then runs to the matching END line, or, with none in view (truncated
# output), to the end of the block of base64 lines, real or JSON-escaped.
_PEM_HEADER = re.compile(r"-----BEGIN ((?:[A-Z0-9]+ ){0,3}PRIVATE KEY(?: BLOCK)?)-----")
_B64_RUN = re.compile(r"[A-Za-z0-9+/]{40,}")
_PEM_BODY = re.compile(r"[A-Za-z0-9+/=]+(?:(?:\\r|\\n|\r|\n)+[A-Za-z0-9+/=]+)*")

_BEARER = re.compile(r"[Bb]earer[ \t]+([A-Za-z0-9._~+/-]{20,}=*)")

# scheme://user:password@host — the password only.
_URL_CRED = re.compile(r"://[A-Za-z0-9._~%+-]{0,64}:([^\s:/?#@'\"<>\\]{6,128})@")
# Passwords glued to a CLI flag: `sshpass -p PW`, `mysql ... -pPW`. Plain-token
# values only, so `-p$PASS`, `-p<password>` and regex sources don't count.
_SSHPASS = re.compile(r"sshpass[ \t]+-p[ \t]*(['\"]?)([A-Za-z0-9!#%&*+,.:;=?@^_~-]{4,})\1(?=\s|$)")
# The MySQL client's `-p<pw>` only counts when the client is a command word of its
# own: `mysql`, `/usr/bin/mysql`, `sudo mysql`, `docker exec db mysql`, `-- mysql`,
# `sh -c 'mysql`, not '*mysql*', `mysql:8`, `/var/lib/mysql` or the value of an
# option (`--name mysql`). The -p must follow on the same simple command, and a
# port map (`-p3306:3306`) or a find/cc action (`-print`, `-prune`, `-pthread`)
# is another program's flag.
_SQL_CLIENT = re.compile(r"m(?:ysql(?:dump|admin|import|check|show|pump)?"
                         r"|ariadb(?:-dump|-admin|-import|-check|-show)?)(?=[ \t])")
_COMMAND_START = frozenset("\n;&|(){}`'\"")
_OPTION = re.compile(r"--?[A-Za-z]")
_COMMAND_END = re.compile(r"[\n;&|`]")
_DASH_P = re.compile(r"[ \t]-p")
_P_VALUE = re.compile(r"(['\"]?)([A-Za-z0-9!#%*+,.:=?@^_~][A-Za-z0-9!#%*+,.:=?@^_~-]{3,})\1"
                      r"(?=[\s`'\");&|]|$)")                   # `sh -c 'mysql … -pPW'` too
_P_FLAGS = frozenset(("rint", "rint0", "rintf", "rune", "thread", "edantic"))
_PORT_MAP = re.compile(r"[0-9]+(?:[.:][0-9]+)+")
SPAN_MAX = 200               # characters between the client word and its -p

# Values that are documentation, not credentials: AWS's own AKIA...EXAMPLE pair,
# "your-key-here", "xxxxxxxx", "<redacted>" and friends.
_PLACEHOLDER = re.compile(r"(?i)example|placeholder|your[_-]|redacted|dummy|sample|changeme"
                          r"|xxxx|\*\*\*|(.)\1{7,}")

# --- assignments --------------------------------------------------------------------

# Value first: an operator, an optional (JSON-escaped) quote, then a 20+ char
# value; `=` only as trailing base64 padding, so `env: API_KEY=<value>` can't
# hide the inner assignment inside the outer value; `%` so a URL-encoded query
# value (X-Amz-Security-Token=…%2F…) is taken whole, up to the next `&`. The
# name in front of the operator is recovered by scanning back from it.
_ASSIGN = re.compile(r"(?:=>|:=|[:=])[ \t]*\\?[\"']?([A-Za-z0-9+/_.~%-]{20,}={0,2})")
_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
_SECRET_NAME = re.compile(
    r"api_?key|access_?key|secret|private_?key|signing_?key|encryption_?key|master_?key"
    r"|app_?key|auth_?key|license_?key|account_?key|token|passw(?:or)?d|passphrase|credential")
# Name segments that mark secret-adjacent metadata (NextToken, password_file,
# token_count, public_key, cache_key, pagination_token, signingkey ...), never
# the secret itself.
_NOT_SECRET_SEGMENTS = frozenset("""
next page pagination continuation start starting sync cursor count max min limit size len
length type name path file dir url uri endpoint env field header label prefix suffix format
policy rotation ttl expiry expires expiration usage budget hash digest checksum sha sha1
sha256 md5 fingerprint id ids arn ref template placeholder hint example sample dummy fake
mock version public primary sort partition foreign cache idempotency dedup dedupe object row
range group map dict join lookup unique composite natural surrogate routing shard sharding
dist distribution bucket s3 item message event record entry column table index trace span
correlation kms content signingkey""".split())
_SEGMENT = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")
# Not assignments at all: an ARN's resource-type segment (`…:123456789012:secret:Name-AbC123`)
# and the WebSocket handshake nonce header.
_ARN_BEFORE = re.compile(r"arn:aws[a-z-]*:[a-z0-9-]+:[a-z0-9-]*:(?:\d{12}|aws)?:[^\s\"'<>]*$")
_NOT_SECRET_NAMES = frozenset(("sec-websocket-key", "sec-websocket-accept"))
# A public key in DER/base64 (SPKI: EC P-256, P-384, Ed25519, RSA 2048/3072/4096).
_PUBLIC_DER = re.compile(r"MFkwEwYHKoZIzj0CAQ|MHYwEAYHKoZIzj0CAQ|MCowBQYDK2Vw"
                         r"|MII[BC][Io]jANBgkqhkiG9w0BAQEFAAOC")
_FILE_EXT = re.compile(r"\.[A-Za-z0-9]{2,5}$")              # S3 `"Key": "Report_2026-Q3.xlsx"`
_HEX = re.compile(r"[0-9a-fA-F]+")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_DOTTED_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")


def _name_strength(name: str) -> Tuple[int, List[str]]:
    """(2 for a secret word, 1 for a name that only ends in key/keys, else 0;
    the lower-cased segments)."""
    segments = [s.lower() for s in _SEGMENT.findall(name)]
    if not segments or _NOT_SECRET_SEGMENTS.intersection(segments):
        return 0, segments
    if _SECRET_NAME.search("_".join(segments)):
        return 2, segments
    return (1 if segments[-1] in ("key", "keys") else 0), segments


def _assignment_kind(text: str, m: "re.Match") -> str:
    """'' when the assignment is not a secret, else the finding kind."""
    i = m.start()
    while i > 0 and text[i - 1] in " \t":                     # KEY = value
        i -= 1
    if i > 0 and text[i - 1] in "\"'":                         # "key": value
        i -= 1
        if i > 0 and text[i - 1] == "\\":                      # {\"key\": \"value\"}
            i -= 1
    j = i
    while i > 0 and text[i - 1] in _NAME_CHARS:
        i -= 1
    if i == j:
        return ""
    name = text[i:j]
    strength, segments = _name_strength(name)
    if not strength or name.lower() in _NOT_SECRET_NAMES:
        return ""
    if m.group(0)[0] == ":" and m.group(0)[:2] != ":=" and _ARN_BEFORE.search(text, max(0, i - 200), i):
        return ""
    v = m.group(1)
    end = m.end(1)
    if end < len(text) and text[end] == "(":                  # a call: token = make_token2(...)
        return ""
    core = v.rstrip("=")
    if (v[0] == "/" and "/" in v[1:]) or v[0] in ".~" or _PLACEHOLDER.search(v) \
            or _UUID.fullmatch(core) or _DOTTED_IDENT.fullmatch(core) or _PUBLIC_DER.match(core):
        return ""
    # A name that only ends in "key" (cache_key, "key", run_key) holds identifiers
    # far more often than secrets, unless it is an UPPER_CASE env-style name.
    weak = strength == 1 and not _ENV_NAME.fullmatch(name.strip(".-_"))
    if weak and _FILE_EXT.search(core):                         # an object key / file name
        return ""
    if _HEX.fullmatch(core):
        if weak or _sequential(core):
            return ""
        if "signing" in segments and len(core) in (16, 40):   # a GPG key id or fingerprint
            return ""
        lo = 32 if strength == 1 else 24
        return "hex-secret" if lo <= len(core) <= 128 else ""
    if weak and "/" in core:                                     # content_key = "a/b/c..."
        return ""
    if looks_random(core) or (strength == 2 and _random_two_class(core)):
        return "high-entropy-secret"
    return ""


def _url_password(pw: str) -> bool:
    """A URL password worth flagging: not an env reference, not a dictionary word,
    not a docs example (`dotenv://:key_1234@…`)."""
    if pw[0] in "$%{" or _PLACEHOLDER.search(pw) or _sequential(pw, 4):
        return False
    classes = sum((any(c.islower() for c in pw), any(c.isupper() for c in pw),
                   any(c.isdigit() for c in pw), any(not c.isalnum() for c in pw)))
    return looks_random(pw) or _random_two_class(pw) or \
        (len(pw) >= 10 and classes >= 3 and entropy(pw) >= 3.0)


def _line_bounds(text: str, pos: int) -> Tuple[int, int]:
    a = text.rfind("\n", 0, pos) + 1
    b = text.find("\n", pos)
    return a, (len(text) if b < 0 else b)


def _client_word(text: str, start: int) -> bool:
    """The SQL client name at start is a command word, not part of a path, a glob,
    an image name or an option's value."""
    if text.endswith("bin/", 0, start):                        # /usr/bin/mysql
        while start and not text[start - 1].isspace() and text[start - 1] not in _COMMAND_START:
            start -= 1
    if not start or text[start - 1] in _COMMAND_START:
        return True
    if text[start - 1] not in " \t":
        return False
    i = start
    while i and text[i - 1] in " \t":
        i -= 1
    j = i
    while j and not text[j - 1].isspace():
        j -= 1
    return not _OPTION.match(text, j, i)                       # `--name mysql`, `-rn mysql`


def _mysql_passwords(text: str) -> Iterator[Finding]:
    # Client spans only move forward, so each stretch of text is searched for a
    # -p once and the next command end is looked up once per command.
    searched, end = 0, -1
    for m in _SQL_CLIENT.finditer(text):
        if end < m.end():
            e = _COMMAND_END.search(text, m.end())
            end = e.start() if e else len(text)
        stop = min(end, m.end() + SPAN_MAX)
        lo = max(m.end(), searched)
        if lo >= stop:
            continue
        if not _DASH_P.search(text, lo, stop):
            searched = stop
            continue
        if not _client_word(text, m.start()):
            continue
        for d in _DASH_P.finditer(text, lo, stop):
            v = _P_VALUE.match(text, d.end())
            if not v:
                continue
            pw = v.group(2)
            if pw in _P_FLAGS or _PORT_MAP.fullmatch(pw) or _PLACEHOLDER.search(pw) \
                    or (pw.isalpha() and pw.isupper()):                    # -pPASSWORD
                continue
            yield Finding("cli-password", v.start(2), v.end(2))
        searched = stop


def _private_keys(text: str) -> Iterator[Finding]:
    for m in _PEM_HEADER.finditer(text):
        for b in _B64_RUN.finditer(text, m.end(), m.end() + 400):
            run = b.group(0)
            if not (any(c.isupper() for c in run) and any(c.islower() for c in run)
                    and any(c.isdigit() for c in run)):
                continue
            footer = f"-----END {m.group(1)}-----"
            stop = text.find(footer, m.end(), m.end() + PEM_MAX)
            if 0 <= stop < b.start():
                break                              # BEGIN ... END with no key between
            if stop >= 0:
                end = stop + len(footer)
            else:
                body = _PEM_BODY.match(text, b.start(), m.end() + PEM_MAX)
                end = body.end() if body else b.end()
            yield Finding("private-key", m.start(), end)
            break


def _iter_raw(text: str) -> Iterator[Finding]:
    for kind, rx, before, check in _NAMED:
        for m in rx.finditer(text):
            if m.start() and text[m.start() - 1] in before:
                continue
            v = m.group(0)
            if not _PLACEHOLDER.search(v) and not _sequential(v) and (check is None or check(v)):
                yield Finding(kind, m.start(), m.end())
    if "-----BEGIN " in text:
        yield from _private_keys(text)
    for m in _BEARER.finditer(text):
        if m.start() and text[m.start() - 1] in _ALNUM:
            continue
        v = m.group(1)
        if _PLACEHOLDER.search(v) or ".." in v:                 # "Bearer eyJhbGciOi..." in docs
            continue
        parts = v.split(".")
        dotted = len(parts) == 3 and min(map(len, parts)) >= 4 and len(parts[2]) >= 16
        if looks_random(v) or dotted:
            yield Finding("bearer-token", m.start(1), m.end(1))
    if "://" in text:
        for m in _URL_CRED.finditer(text):
            if _url_password(m.group(1)):
                yield Finding("url-password", m.start(1), m.end(1))
    if "-p" in text:
        for m in _SSHPASS.finditer(text):
            v = m.group(2)
            if not _PLACEHOLDER.search(v) and not (v.isalpha() and v.isupper()):  # -p PASSWORD
                yield Finding("cli-password", m.start(2), m.end(2))
        if "mysql" in text or "mariadb" in text:
            yield from _mysql_passwords(text)
    pos = 0
    while True:
        m = _ASSIGN.search(text, pos)
        if m is None:
            break
        kind = _assignment_kind(text, m)
        if kind:
            yield Finding(kind, m.start(1), m.end(1))
            pos = m.end()
        else:
            # A trailing "=" may be the operator of an inner assignment
            # (`env: SERPAPI_API_KEY_PROD=<value>`): resume the search on it.
            pad = len(m.group(1)) - len(m.group(1).rstrip("="))
            pos = m.end() - pad


def scan(text: str, honor_marker: bool = True) -> List[Finding]:
    """Non-overlapping findings sorted by position. With honor_marker, findings on
    a line that contains ALLOW_MARKER are dropped."""
    if not text or not isinstance(text, str):
        return []
    found = sorted(_iter_raw(text), key=lambda f: (f.start, -f.end))
    marked = honor_marker and ALLOW_MARKER in text
    out: List[Finding] = []
    for f in found:
        if out and f.start < out[-1].end:
            continue
        if marked:
            a, b = _line_bounds(text, f.start)
            if ALLOW_MARKER in text[a:b]:
                continue
        out.append(f)
    return out


def line_of(text: str, pos: int) -> int:
    """1-based line number of offset pos."""
    return text.count("\n", 0, pos) + 1


def line_numbers(text: str, found: List[Finding]) -> List[int]:
    """1-based line of each finding, in one pass over the text (many findings)."""
    newlines = [m.start() for m in re.finditer("\n", text)] if found else []
    return [bisect.bisect_left(newlines, f.start) + 1 for f in found]


def redact(text: str, honor_marker: bool = True) -> Tuple[str, List[str]]:
    """(text with each finding replaced by [REDACTED:<kind>], kinds found). A
    finding spanning lines (a PEM key) keeps its line breaks, so line numbers
    after it still match the source."""
    found = scan(text, honor_marker)
    if not found:
        return text, []
    parts, last = [], 0
    for f in found:
        parts.append(text[last:f.start])
        parts.append(f"[REDACTED:{f.kind}]" + "\n" * text.count("\n", f.start, f.end))
        last = f.end
    parts.append(text[last:])
    return "".join(parts), [f.kind for f in found]


def summarize(kinds: List[str]) -> str:
    """'2 x github-token, 1 x private-key' — counts by kind, no values."""
    counts: dict = {}
    for k in kinds:
        counts[k] = counts.get(k, 0) + 1
    return ", ".join(f"{n} x {k}" for k, n in sorted(counts.items()))


def describe(text: str, found: List[Finding], limit: int = 5) -> str:
    """'github-token on line 3, private-key on line 9' — kinds and lines, no values."""
    parts = [f"{f.kind} on line {line_of(text, f.start)}" for f in found[:limit]]
    if len(found) > limit:
        parts.append(f"{len(found) - limit} more")
    return ", ".join(parts)
