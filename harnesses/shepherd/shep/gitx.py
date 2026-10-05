"""Thin git layer: repo resolution without subprocesses, plus the few git calls
shepherd needs (worktrees, ahead/behind, conflict prediction, merged checks)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Set by git when it runs a hook; inherited, they would point our own git
# calls at the wrong repository.
_GIT_LOCATION_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX",
                      "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                      "GIT_COMMON_DIR")


@dataclass
class Result:
    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


def run(args: list[str], cwd: str | Path, timeout: float = 20,
        input: Optional[str] = None) -> Result:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_LOCATION_VARS}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("LC_ALL", "C")
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout, input=input, env=env,
                           stdin=None if input is not None else subprocess.DEVNULL)
        return Result(p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired:
        return Result(124, "", f"git {' '.join(args)}: timed out after {timeout:.0f}s")
    except OSError as e:
        return Result(127, "", str(e))


def out(args: list[str], cwd: str | Path, timeout: float = 20) -> str:
    r = run(args, cwd, timeout)
    return r.out.strip() if r.ok else ""


# --- repo resolution (pure Python: hooks call this on every edit) -----------------

@dataclass
class Repo:
    toplevel: str      # realpath of this checkout's root
    git_dir: str       # this checkout's git dir (.git, or .git/worktrees/<n>)
    common_dir: str    # shared git dir of the repository
    branch: Optional[str]   # current branch, or the branch being rebased; None if detached
    detached: bool

    @property
    def primary(self) -> str:
        """Root of the main checkout (parent of the common git dir)."""
        return str(Path(self.common_dir).parent)

    @property
    def name(self) -> str:
        return Path(self.primary).name


def _read(p: Path) -> str:
    try:
        return p.read_text().strip()
    except OSError:
        return ""


def realpath_any(path: str | Path) -> str:
    """realpath that also works for files that don't exist yet."""
    p = Path(os.path.abspath(str(path)))
    tail: list[str] = []
    while not p.exists() and p != p.parent:
        tail.append(p.name)
        p = p.parent
    real = Path(os.path.realpath(str(p)))
    for part in reversed(tail):
        real = real / part
    return str(real)


def branch_from_git_dir(git_dir: str) -> tuple[Optional[str], bool]:
    head = _read(Path(git_dir) / "HEAD")
    if head.startswith("ref: refs/heads/"):
        return head[len("ref: refs/heads/"):], False
    for sub in ("rebase-merge", "rebase-apply"):
        name = _read(Path(git_dir) / sub / "head-name")
        if name.startswith("refs/heads/"):
            return name[len("refs/heads/"):], True
    return None, True


def repo_of(path: str | Path) -> Optional[Repo]:
    """The checkout containing `path` (file or dir, existing or not), or None."""
    p = Path(realpath_any(path))
    if not p.is_dir():
        p = p.parent
    while not p.exists() and p != p.parent:
        p = p.parent
    for d in (p, *p.parents):
        if d.name == ".git":          # inside a git dir: not a checkout
            return None
        dotgit = d / ".git"
        if dotgit.is_dir():
            git_dir = str(dotgit)
        elif dotgit.is_file():
            ref = _read(dotgit)
            if not ref.startswith("gitdir:"):
                return None
            gd = ref[len("gitdir:"):].strip()
            git_dir = os.path.realpath(gd if os.path.isabs(gd) else str(d / gd))
        else:
            continue
        common = git_dir
        cd = _read(Path(git_dir) / "commondir")
        if cd:
            common = os.path.realpath(cd if os.path.isabs(cd) else os.path.join(git_dir, cd))
        branch, detached = branch_from_git_dir(git_dir)
        return Repo(str(d), git_dir, common, branch, detached and branch is None)
    return None


# --- repository facts ---------------------------------------------------------

def remotes(cwd: str) -> list[str]:
    return out(["remote"], cwd).split()


def remote_url(cwd: str, remote: str) -> str:
    """The URL as configured (before insteadOf rewriting), which names the host."""
    return out(["config", "--get", f"remote.{remote}.url"], cwd)


def detect_remote(cwd: str, main: Optional[str] = None) -> Optional[str]:
    if main:
        r = out(["config", f"branch.{main}.remote"], cwd)
        if r:
            return r
    rs = remotes(cwd)
    if "origin" in rs:
        return "origin"
    return rs[0] if rs else None


def detect_main(cwd: str, remote: Optional[str]) -> str:
    """The repository's main branch, without assuming a name."""
    if remote:
        ref = out(["symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"], cwd)
        if ref.startswith(remote + "/"):
            return ref[len(remote) + 1:]
        r = run(["ls-remote", "--symref", remote, "HEAD"], cwd, timeout=10)
        for line in r.out.splitlines():
            if line.startswith("ref: refs/heads/"):
                return line.split()[1][len("refs/heads/"):]
    for cand in ("main", "master", "trunk"):
        if run(["rev-parse", "--verify", "-q", f"refs/heads/{cand}"], cwd).ok:
            return cand
    return out(["branch", "--show-current"], cwd) or "main"


def rev(cwd: str, ref: str) -> Optional[str]:
    r = run(["rev-parse", "--verify", "-q", ref + "^{commit}"], cwd)
    return r.out.strip() if r.ok else None


def base_ref(cwd: str, remote: Optional[str], main: str) -> str:
    """Remote-tracking main if it exists, else the local main branch."""
    if remote and rev(cwd, f"refs/remotes/{remote}/{main}"):
        return f"{remote}/{main}"
    return main


def fetch(cwd: str, remote: Optional[str], ref: Optional[str] = None,
          timeout: float = 30) -> Result:
    if not remote:
        return Result(0, "", "")
    return run(["fetch", "--quiet", remote, *([ref] if ref else [])], cwd, timeout=timeout)


def worktrees(cwd: str) -> list[dict]:
    """`git worktree list --porcelain`, as dicts {path, head, branch, detached, prunable}."""
    res: list[dict] = []
    cur: dict = {}
    for line in out(["worktree", "list", "--porcelain"], cwd).splitlines() + [""]:
        if not line:
            if cur:
                res.append(cur)
            cur = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            cur = {"path": os.path.realpath(val), "head": None, "branch": None,
                   "detached": False, "prunable": False, "bare": False}
        elif key == "HEAD":
            cur["head"] = val
        elif key == "branch":
            cur["branch"] = val[len("refs/heads/"):] if val.startswith("refs/heads/") else val
        elif key == "detached":
            cur["detached"] = True
        elif key == "prunable":
            cur["prunable"] = True
        elif key == "bare":
            cur["bare"] = True
    return res


def branch_heads(cwd: str) -> dict[str, str]:
    """{branch: sha} for all local branches, in one call."""
    heads = {}
    for line in out(["for-each-ref", "--format=%(refname:short) %(objectname)",
                     "refs/heads"], cwd).splitlines():
        name, _, sha = line.partition(" ")
        if sha:
            heads[name] = sha
    return heads


def ahead_behind(cwd: str, ref: str, base: str) -> tuple[int, int]:
    """(commits in ref not in base, commits in base not in ref)."""
    r = run(["rev-list", "--left-right", "--count", f"{ref}...{base}"], cwd)
    try:
        a, b = r.out.split()
        return int(a), int(b)
    except ValueError:
        return 0, 0


def dirty(cwd: str, timeout: float = 5) -> list[str]:
    """Uncommitted paths in a checkout (staged, unstaged or untracked)."""
    r = run(["status", "--porcelain=v1", "-z", "--untracked-files=normal"], cwd, timeout)
    if not r.ok:
        return []
    paths, parts, i = [], r.out.split("\0"), 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) > 3:
            paths.append(entry[3:])
            if entry[0] in "RC":     # rename/copy: next field is the source path
                i += 1
        i += 1
    return paths


def merge_base(cwd: str, a: str, b: str) -> Optional[str]:
    r = run(["merge-base", a, b], cwd)
    return r.out.strip() if r.ok else None


def changed_files(cwd: str, base: str, head: str) -> list[str]:
    """Files changed on `head` since it forked from `base`."""
    return [f for f in out(["diff", "--name-only", f"{base}...{head}"], cwd).splitlines() if f]


def predict_conflicts(cwd: str, a: str, b: str) -> Optional[list[str]]:
    """Files that would conflict merging a and b; [] if clean, None if unknown."""
    r = run(["merge-tree", "--write-tree", "--name-only", "--no-messages", a, b], cwd, timeout=30)
    if r.rc not in (0, 1):
        return None
    lines = [l for l in r.out.splitlines()[1:] if l]
    return sorted(set(lines)) if r.rc == 1 else []


def merged_into(cwd: str, branch: str, target: str) -> bool:
    """True if `branch`'s changes are already in `target`: fast-forward or merge commit,
    rebase-merge (same patches), or squash merge (merging adds nothing)."""
    if run(["merge-base", "--is-ancestor", branch, target], cwd).ok:
        return True
    cherry = run(["cherry", target, branch], cwd)
    if cherry.ok and cherry.out.strip() and not any(
            l.startswith("+") for l in cherry.out.splitlines()):
        return True
    r = run(["merge-tree", "--write-tree", target, branch], cwd, timeout=30)
    if r.rc == 0:
        tree = r.out.splitlines()[0].strip() if r.out else ""
        return bool(tree) and tree == out(["rev-parse", f"{target}^{{tree}}"], cwd)
    return False


def log(cwd: str, rng: str, limit: int = 50) -> list[dict]:
    fmt = "%H%x1f%h%x1f%an%x1f%at%x1f%s"
    res = []
    for line in out(["log", f"--max-count={limit}", f"--format={fmt}", rng], cwd).splitlines():
        parts = line.split("\x1f")
        if len(parts) == 5:
            res.append({"sha": parts[0], "short": parts[1], "author": parts[2],
                        "at": int(parts[3]), "subject": parts[4]})
    return res
