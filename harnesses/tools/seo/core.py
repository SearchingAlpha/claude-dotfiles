#!/usr/bin/env python3
"""
core.py — utilidades compartidas de la librería SEO (stdlib-only).

Config (`seo-project.json`), HTTP con reintentos (urllib), almacén JSONL,
contador de cuota por fuente, heurística de intención y utilidades de dominio.
Nada de este fichero conoce un proyecto concreto: todo sale del config.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

USER_AGENT = "claude-dotfiles-seo/1.0 (+https://github.com/SearchingAlpha/claude-dotfiles)"
REQUIRED_KEYS = ("domain", "ops_dir", "geo", "queries")
DEFAULT_BUDGETS = {"brave": 900, "serpapi": 0, "bing_kw": 300}
DEFAULT_PER_RUN = {"brave": 40, "serpapi": 10}
CONFIG_CANDIDATES = ("seo-project.json", "site/config/seo-project.json",
                     "config/seo-project.json", ".claude/seo-project.json")

# Claves del config cuyo valor es una ruta relativa al directorio del JSON.
_PATH_KEYS = (
    ("ops_dir",),
    ("seeds", "negatives_file"), ("seeds", "ads_keywords_csv"),
    ("geo", "metros_db", "path"),
    ("scripts", "gsc_queries"),
)
_PATH_MAPS = (("secrets",), ("sources",))          # dict: todos sus valores son rutas
_PATH_LISTS = (("context_docs",),)                  # lista de rutas

for _stream in (sys.stdout, sys.stderr):            # Windows: cp1252 rompe con "→"
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


# ----------------------------------------------------------------- salida --

def human(msg: str = "") -> None:
    """Mensaje para humanos → stderr (stdout queda limpio para --json)."""
    print(msg, file=sys.stderr, flush=True)


def emit_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2), flush=True)


def today() -> str:
    return str(date.today())


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")


# ----------------------------------------------------------------- config --

def find_project(explicit: str | None) -> Path:
    if explicit:
        p = Path(os.path.expanduser(explicit))
        if not p.exists():
            raise SystemExit(f"config no encontrado: {p}")
        return p
    env = os.environ.get("SEO_PROJECT")
    for cand in CONFIG_CANDIDATES:
        p = Path.cwd() / cand
        if p.exists():
            return p
    if env and Path(os.path.expanduser(env)).exists():
        return Path(os.path.expanduser(env))
    raise SystemExit("Sin seo-project.json (probado: ./, ./site/config/, ./config/, "
                     "./.claude/, $SEO_PROJECT). Usa --project o /seo-setup.")


def _resolve(base: Path, value: str) -> str:
    v = os.path.expanduser(value)
    p = Path(v)
    return str(p if p.is_absolute() else (base / p).resolve())


def _walk_set(d: dict, keys: tuple, fn) -> None:
    cur = d
    for k in keys[:-1]:
        cur = cur.get(k) if isinstance(cur, dict) else None
        if cur is None:
            return
    last = keys[-1]
    if isinstance(cur, dict) and isinstance(cur.get(last), str):
        cur[last] = fn(cur[last])


def load_project(path: str | None) -> dict:
    """Carga el config; resuelve rutas relativas al directorio del propio JSON."""
    p = find_project(path)
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"config inválido {p}: {e}")
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    for parent, child in (("geo", "country"), ("geo", "language"), ("queries", "money")):
        if not (isinstance(cfg.get(parent), dict) and cfg[parent].get(child)):
            missing.append(f"{parent}.{child}")
    if missing:
        raise SystemExit(f"config {p}: faltan claves obligatorias {missing}")
    base = p.parent
    fn = lambda v: _resolve(base, v)
    for keys in _PATH_KEYS:
        _walk_set(cfg, keys, fn)
    for keys in _PATH_MAPS:
        m = cfg.get(keys[0])
        if isinstance(m, dict):
            for k, v in list(m.items()):
                if isinstance(v, str) and not k.startswith("_"):
                    m[k] = fn(v)
    for keys in _PATH_LISTS:
        lst = cfg.get(keys[0])
        if isinstance(lst, list):
            cfg[keys[0]] = [fn(v) if isinstance(v, str) else v for v in lst]
    scripts = cfg.setdefault("scripts", {})
    if isinstance(scripts.get("dir"), str):
        scripts["dir"] = os.path.expanduser(scripts["dir"])
    cfg.setdefault("budgets", {})
    for k, v in DEFAULT_BUDGETS.items():
        cfg["budgets"].setdefault(k, v)
    cfg.setdefault("per_run", {})
    for k, v in DEFAULT_PER_RUN.items():
        cfg["per_run"].setdefault(k, v)
    cfg.setdefault("brand", {}).setdefault("tokens", [])
    cfg["_path"] = str(p)
    cfg["_dir"] = str(base)
    return cfg


def save_project(cfg: dict) -> None:
    """Reescribe el config conservando claves `_x` y orden; sin rutas resueltas."""
    p = Path(cfg["_path"])
    raw = json.loads(p.read_text(encoding="utf-8"))
    for k in ("queries",):                       # solo lo que la librería edita
        raw[k] = {kk: vv for kk, vv in cfg[k].items()}
    p.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def secret(cfg: dict, name: str) -> str:
    path = (cfg.get("secrets") or {}).get(name)
    if not path:
        raise SystemExit(f"config: secrets.{name} no definido")
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"secreto ausente: {p} (secrets.{name})")
    v = p.read_text(encoding="utf-8").strip()
    if not v:
        raise SystemExit(f"secreto vacío: {p}")
    return v


def has_secret(cfg: dict, name: str) -> bool:
    path = (cfg.get("secrets") or {}).get(name)
    return bool(path) and Path(path).exists() and Path(path).read_text().strip() != ""


def ops_path(cfg: dict, filename: str) -> Path:
    d = Path(cfg["ops_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d / filename


# ------------------------------------------------------------------- http --

def get_json(url: str, params: dict | None = None, headers: dict | None = None,
             timeout: int = 30, retries: int = 3, backoff: float = 5.0,
             method: str = "GET", data: dict | None = None) -> tuple[object, dict]:
    """GET/POST JSON con reintentos en timeout/429/5xx; honra Retry-After.

    Devuelve (objeto, cabeceras de respuesta). Lanza RuntimeError con URL y
    código en el último fallo; 4xx distinto de 429 no se reintenta.
    """
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                return (json.loads(raw) if raw.strip() else {}), dict(r.headers)
        except urllib.error.HTTPError as e:
            snippet = e.read().decode("utf-8", "replace")[:300]
            last = RuntimeError(f"HTTP {e.code} {url.split('?')[0]} · {snippet}")
            if e.code == 429 or e.code >= 500:
                ra = e.headers.get("Retry-After")
                wait = float(ra) if ra and ra.isdigit() else backoff * (attempt + 1)
                if e.code == 429:
                    wait = max(wait, 20.0 * (attempt + 1))   # Brave: 20-90 s (auditoría)
                human(f"  {e.code} → espera {wait:.0f}s (intento {attempt + 1}/{retries})")
                time.sleep(wait)
                continue
            raise last
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = RuntimeError(f"red: {e} · {url.split('?')[0]}")
            time.sleep(backoff * (attempt + 1))
    raise last  # type: ignore[misc]


# ---------------------------------------------------------------- dominio --

def domain(url: str) -> str:
    return urllib.parse.urlparse(url or "").netloc.lower().replace("www.", "")


def is_ours(url: str, cfg: dict) -> bool:
    d, ours = domain(url), cfg["domain"].lower().replace("www.", "")
    return d == ours or d.endswith("." + ours)


# ------------------------------------------------------------------ store --

def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path | str, n: int | None = None) -> list[dict]:
    """Todas (o últimas n) las entradas válidas; una línea corrupta se salta."""
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out[-n:] if n else out


def latest_by(path: Path | str, key_fn) -> dict:
    """Última entrada por clave (key_fn(row) → hashable)."""
    out = {}
    for row in read_jsonl(path):
        try:
            out[key_fn(row)] = row
        except (KeyError, TypeError):
            continue
    return out


# ------------------------------------------------------------------ cuota --

def _quota_file(cfg: dict) -> Path:
    return ops_path(cfg, "seo-quota.json")


def _quota_load(cfg: dict) -> dict:
    p = _quota_file(cfg)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def quota_used(cfg: dict, source: str) -> int:
    return int(_quota_load(cfg).get(today()[:7], {}).get(source, 0))


def quota_charge(cfg: dict, source: str, n: int = 1) -> None:
    q = _quota_load(cfg)
    month = q.setdefault(today()[:7], {})
    month[source] = int(month.get(source, 0)) + n
    _quota_file(cfg).write_text(json.dumps(q, indent=2) + "\n", encoding="utf-8")


def quota_guard(cfg: dict, source: str, planned: int, force: bool = False) -> None:
    """SystemExit(3) si used+planned supera budgets[source]; aviso a partir del 80%."""
    limit = int(cfg["budgets"].get(source, 0))
    used = quota_used(cfg, source)
    if limit <= 0 and not force:
        raise SystemExit(f"cuota {source}: presupuesto 0 en config (budgets.{source}); "
                         f"usa --force para saltarlo")
    if used + planned > limit and not force:
        raise SystemExit(f"cuota {source}: {used} usadas + {planned} planeadas > {limit}/mes "
                         f"(ops/seo-quota.json). --force para saltarlo.")
    if limit and (used + planned) >= 0.8 * limit:
        human(f"  aviso cuota {source}: {used + planned}/{limit} este mes")


# -------------------------------------------------------------- intención --

_INFO = re.compile(r"^(how to|what is|what's|whats|when|why|can i|do i need|is it|should i)\b|"
                   r"\b(best months?|guide|explained|tips)\b")
_COMM = re.compile(r"\b(cost|costs|price|prices|pricing|how much|rate|rates|cheap|cheapest|"
                   r"best|vs|versus|minimum|no minimum|per yard|estimate)\b")
_TRANS = re.compile(r"\b(delivery|delivered|deliver|near me|order|supplier|suppliers|company|"
                    r"companies|hire|quote|service|services|buy)\b")


def _metro_tokens(cfg: dict) -> list[str]:
    toks = []
    for m in (cfg.get("geo") or {}).get("metros", []):
        for v in (m.get("name"), m.get("county"), *(m.get("cities") or [])):
            if v:
                toks.append(str(v).lower())
    return toks


def classify_intent(keyword: str, cfg: dict) -> str:
    k = keyword.lower()
    brand = [t.lower() for t in cfg.get("brand", {}).get("tokens", [])]
    if any(t in k for t in brand):
        return "navigational"
    if _INFO.search(k):
        return "informational"
    if _COMM.search(k):
        return "commercial"
    if _TRANS.search(k) or any(t in k for t in _metro_tokens(cfg)):
        return "transactional"
    return "informational"


def load_negatives(cfg: dict) -> set[str]:
    path = (cfg.get("seeds") or {}).get("negatives_file")
    if not path or not Path(path).exists():
        return set()
    out = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        t = line.strip().strip('"').strip("'").lower()
        if t and not t.startswith("#"):
            out.add(t)
    return out


def has_negative(keyword: str, negatives: set[str]) -> bool:
    k = " " + re.sub(r"\s+", " ", keyword.lower()) + " "
    return any((" " + n + " ") in k for n in negatives)


def bing_ms_date(s: str | None) -> str | None:
    """'/Date(1787122800000-0700)/' → 'YYYY-MM-DD' (o None)."""
    m = re.search(r"/Date\((\d+)", s or "")
    if not m:
        return None
    return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
