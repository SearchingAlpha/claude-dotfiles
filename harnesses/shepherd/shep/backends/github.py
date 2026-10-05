"""GitHub pull requests through the `gh` CLI."""

from __future__ import annotations

import json
import re
import shutil
from typing import Optional

from ..review import Ctx, normalize

_SLUG = re.compile(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$")


def slug(url: str) -> Optional[str]:
    m = _SLUG.search(url or "")
    return f"{m.group(1)}/{m.group(2)}" if m else None


class GitHub:
    name = "github"

    def matches(self, remote_url: str) -> bool:
        return slug(remote_url) is not None

    def config_defaults(self, remote_url: str) -> dict:
        return {}

    def _push(self, ctx: Ctx, track: dict) -> Optional[str]:
        rc, out, err = ctx.run(["git", "push", "-u", ctx.remote, ctx.branch], timeout=180)
        if rc != 0 and "rejected" in err and (track.get("review") or {}).get("pushed_sha"):
            lease = f"--force-with-lease={ctx.branch}:{track['review']['pushed_sha']}"
            ctx.log("branch was rewritten (sync); pushing with " + lease)
            rc, out, err = ctx.run(["git", "push", lease, "-u", ctx.remote, ctx.branch],
                                   timeout=180)
        return None if rc == 0 else (err or out).strip()

    def submit(self, track: dict, ctx: Ctx) -> dict:
        repo = slug(ctx.remote_url)
        err = self._push(ctx, track)
        if err:
            return normalize({"error": f"git push failed: {err}"}, self.name)
        rc, out, _ = ctx.run(["gh", "pr", "list", "--repo", repo, "--head", ctx.branch,
                              "--state", "open", "--json", "number,url", "--limit", "1"])
        existing = json.loads(out or "[]") if rc == 0 else []
        if existing:
            r = {"id": str(existing[0]["number"]), "url": existing[0]["url"]}
        else:
            cmd = ["gh", "pr", "create", "--repo", repo, "--base", ctx.main, "--head", ctx.branch,
                   "--title", ctx.title, "--body-file", "-"]
            if ctx.draft:
                cmd.append("--draft")
            for rv in ctx.reviewers:
                cmd += ["--reviewer", rv]
            rc, out, err = ctx.run(cmd + list(ctx.extra_args), input=ctx.body, timeout=120)
            m = re.search(r"https://\S+/pull/(\d+)", out)
            if rc != 0 or not m:
                return normalize({"error": f"gh pr create failed: {(err or out).strip()}"},
                                 self.name)
            r = {"id": m.group(1), "url": m.group(0)}
        st = self.status(normalize(dict(r, state="open"), self.name), ctx)
        st["pushed_sha"] = ctx.head_sha
        return st

    def update(self, track: dict, ctx: Ctx) -> dict:
        review = dict(track.get("review") or {})
        err = self._push(ctx, track)
        if err:
            review.update(error=f"git push failed: {err}")
            return normalize(review, self.name)
        review["pushed_sha"] = ctx.head_sha
        st = self.status(normalize(review, self.name), ctx)
        st["pushed_sha"] = ctx.head_sha
        return st

    def status(self, review: dict, ctx: Ctx) -> dict:
        repo = slug(ctx.remote_url)
        rc, out, err = ctx.run(["gh", "pr", "view", str(review.get("id")), "--repo", repo,
                                "--json", "state,isDraft,reviewDecision,statusCheckRollup,url,"
                                "title,updatedAt,comments,mergedAt,reviewRequests"], timeout=30)
        if rc != 0:
            msg = (err or out).strip()
            hint = "run `gh auth login`" if "auth" in msg.lower() or "login" in msg.lower() else None
            first = msg.splitlines()[0] if msg else f"gh exited {rc}"
            return normalize(dict(review, error=first[:120], hint=hint, stale=True), self.name)
        try:
            pr = json.loads(out)
        except ValueError:
            return normalize(dict(review, error="unreadable gh output", stale=True), self.name)
        state = pr.get("state")
        if state == "MERGED":
            s = "merged"
        elif state == "CLOSED":
            s = "closed"
        elif pr.get("isDraft"):
            s = "draft"
        elif pr.get("reviewDecision") == "APPROVED":
            s = "approved"
        elif pr.get("reviewDecision") == "CHANGES_REQUESTED":
            s = "changes_requested"
        else:
            s = "open"
        return normalize({**review, "url": pr.get("url") or review.get("url"),
                          "title": pr.get("title"), "state": s,
                          "raw_state": f"{state}/{pr.get('reviewDecision') or '-'}",
                          "checks": _checks(pr.get("statusCheckRollup") or []),
                          "comments": len(pr.get("comments") or []),
                          "reviewers": [r.get("login") or r.get("name") for r in
                                        pr.get("reviewRequests") or [] if isinstance(r, dict)],
                          "updated_at": pr.get("updatedAt"), "error": None, "hint": None,
                          "stale": False}, self.name)

    def discover(self, track: dict, ctx: Ctx) -> Optional[dict]:
        rc, out, _ = ctx.run(["gh", "pr", "list", "--repo", slug(ctx.remote_url), "--head",
                              ctx.branch, "--state", "all", "--json", "number,url",
                              "--limit", "1"], timeout=30)
        try:
            found = json.loads(out or "[]") if rc == 0 else []
        except ValueError:
            return None
        if not found:
            return None
        return self.status(normalize({"id": str(found[0]["number"]), "url": found[0]["url"]},
                                     self.name), ctx)

    def doctor(self) -> list:
        if not shutil.which("gh"):
            return [(False, "gh CLI", "install GitHub CLI: https://cli.github.com")]
        import subprocess
        p = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
        ok = p.returncode == 0
        return [(True, "gh CLI", ""), (ok, "gh auth", "" if ok else "run `gh auth login`")]


def _checks(rollup: list) -> str:
    if not rollup:
        return "none"
    states = []
    for c in rollup:
        st = (c.get("conclusion") or c.get("state") or c.get("status") or "").upper()
        states.append(st)
    if any(s in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED",
                 "STARTUP_FAILURE") for s in states):
        return "failing"
    if any(s in ("PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "WAITING", "REQUESTED", "")
           for s in states):
        return "pending"
    return "passing"


BACKEND = GitHub()
