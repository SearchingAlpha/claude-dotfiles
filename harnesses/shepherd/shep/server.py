"""The dashboard server: read-only JSON API + static UI on 127.0.0.1, and a
background poller that keeps review states fresh."""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import VERSION, board, review, store, tracks

STATIC = Path(__file__).resolve().parent.parent / "static"
STATIC_FILES = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
                "app.css": "text/css; charset=utf-8", "favicon.svg": "image/svg+xml"}
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
POLL_EVERY = 120        # seconds between review checks per track
DISCOVER_EVERY = 600
BACKOFF = 600


def server_file() -> Path:
    return store.home() / "server.json"


class Handler(BaseHTTPRequestHandler):
    server_version = f"shepherd/{VERSION}"

    def log_message(self, fmt, *args):  # quiet; errors go to server.log via stderr
        pass

    def _send(self, code: int, body: bytes, ctype: str, extra: dict = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):  # noqa: N802
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS:
            return self._send(403, b"forbidden host", "text/plain")
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/ping":
            return self._json({"ok": True, "version": VERSION, "pid": os.getpid()})
        token = self.server.token
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        authed = cookie.get("shepherd") is not None and \
            secrets.compare_digest(cookie["shepherd"].value, token)
        if not authed and q.get("t") and secrets.compare_digest(q["t"], token):
            return self._send(302, b"", "text/plain", {
                "Location": "/", "Set-Cookie": f"shepherd={token}; HttpOnly; SameSite=Strict; Path=/"})
        if not authed:
            return self._send(401, b"Open the URL printed by `shepherd serve` (it carries the "
                                   b"access token).", "text/plain")
        try:
            if u.path in ("/", "/index.html"):
                return self._static("index.html")
            if u.path.startswith("/static/"):
                return self._static(u.path[len("/static/"):])
            if u.path == "/api/board":
                return self._json(board.snapshot())
            if u.path == "/api/track":
                common = q.get("repo", "")
                if common not in {r["common"] for r in store.registered_repos()}:
                    return self._json({"error": "unknown repo"}, 404)
                d = board.track_detail(common, q.get("track", ""))
                return self._json(d) if d else self._json({"error": "unknown track"}, 404)
            if u.path == "/api/agents":
                return self._json(board.agents_graph())
            if u.path == "/api/activity":
                since = int(q.get("since") or store.now() - 7200)
                return self._json({"events": board.activity(since)})
        except Exception as e:
            print(f"{time.strftime('%FT%T')} {u.path}: {type(e).__name__}: {e}", file=sys.stderr)
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        return self._send(404, b"not found", "text/plain")

    def _static(self, name: str):
        if name not in STATIC_FILES:
            return self._send(404, b"not found", "text/plain")
        p = STATIC / name
        if not p.exists():
            return self._send(404, b"not found", "text/plain")
        return self._send(200, p.read_bytes(), STATIC_FILES[name])


class Poller(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__(name="review-poller")
        self.next_at: dict = {}
        self.stop = threading.Event()

    def run(self):
        while not self.stop.wait(15):
            try:
                self.tick()
            except Exception as e:
                print(f"{time.strftime('%FT%T')} poller: {type(e).__name__}: {e}", file=sys.stderr)

    def tick(self):
        now = time.time()
        for r in store.registered_repos():
            cfg = store.load_config(r["common"])
            if not cfg:
                continue
            for t in store.tracks(r["common"]):
                if t.get("status") not in ("active", "review", "merged"):
                    continue
                rv = t.get("review") or {}
                if rv.get("state") in review.TERMINAL:
                    continue
                key = (r["common"], t["name"])
                if now < self.next_at.get(key, 0):
                    continue
                if not rv.get("id") and t.get("status") != "active":
                    continue
                st = tracks.refresh_review(r["common"], r["primary"], cfg, t)
                every = POLL_EVERY if rv.get("id") else DISCOVER_EVERY
                if st and st.get("error"):
                    every = BACKOFF
                self.next_at[key] = now + every
                board._cache.clear()


def serve(port: int = 7799, quiet: bool = False) -> None:
    prev = store.read_json(server_file(), {}) or {}
    token = prev.get("token") or secrets.token_urlsafe(18)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.token = token
    port = httpd.server_address[1]
    store.write_json(server_file(), {"pid": os.getpid(), "port": port, "token": token,
                                     "version": VERSION, "root": str(STATIC.parent),
                                     "started_at": store.now()}, mode=0o600)
    poller = Poller()
    poller.start()
    if not quiet:
        print(url_text(port, token))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop.set()
        httpd.server_close()
        cur = store.read_json(server_file(), {}) or {}
        if cur.get("pid") == os.getpid():
            server_file().unlink()


def url_text(port: int, token: str) -> str:
    return (f"shepherd dashboard: http://127.0.0.1:{port}/?t={token}\n"
            f"  remote machine? forward the port first — VS Code: Ports → Forward {port}; "
            f"ssh: ssh -L {port}:localhost:{port} <host>")


def _ping(port: int) -> dict:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def running() -> dict:
    info = store.read_json(server_file(), {}) or {}
    if info.get("port") and _ping(info["port"]).get("pid") == info.get("pid"):
        return info
    return {}


def stop() -> bool:
    info = running()
    if not info:
        return False
    try:
        os.kill(int(info["pid"]), signal.SIGTERM)
    except OSError:
        return False
    for _ in range(50):
        if not _ping(info["port"]):
            break
        time.sleep(0.1)
    return True


def daemon(port: int = 7799) -> str:
    info = running()
    if info and info.get("version") == VERSION and info.get("root") == str(STATIC.parent):
        return url_text(info["port"], info["token"])
    if info:
        stop()
    entry = Path(__file__).resolve().parent.parent / "shepherd.py"
    log = open(store.home() / "server.log", "a")
    subprocess.Popen([sys.executable, str(entry), "serve", "--port", str(port), "--quiet"],
                     stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
                     cwd=str(store.home()))
    for _ in range(60):
        time.sleep(0.1)
        info = running()
        if info:
            return url_text(info["port"], info["token"])
    raise RuntimeError(f"the dashboard did not start; see {store.home() / 'server.log'}")
