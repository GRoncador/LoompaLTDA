"""Git worktree isolation: one directory + branch per story under `.loompa/worktrees/`.

Several Loompas code simultaneously without touching the repo root. All git calls are
Tier 3 (deterministic, $0). Output is trimmed so nothing noisy leaks into agent context.
"""

from __future__ import annotations

import re
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


@dataclass
class Worktree:
    story_id: str
    path: Path
    branch: str
    base: str


@dataclass
class CommitResult:
    sha: str
    files: int


class WorktreeManager:
    def __init__(
        self, repo_root: Path, worktrees_dir: Path | None = None, *, branch_prefix: str = "loompa/"
    ):
        self.repo_root = Path(repo_root).resolve()
        self.dir = (worktrees_dir or self.repo_root / ".loompa" / "worktrees").resolve()
        self.branch_prefix = branch_prefix

    # ------------------------------------------------------------------ plumbing
    def git(
        self, *args: str, cwd: Path | None = None, check: bool = True, timeout: int = 120
    ) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(cwd or self.repo_root),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise GitError("git não encontrado no PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {' '.join(args[:2])} excedeu {timeout}s") from exc
        if check and proc.returncode != 0:
            raise GitError(
                (proc.stderr or proc.stdout).strip()[-800:] or f"git {' '.join(args)} falhou"
            )
        return proc.stdout.strip()

    def default_branch(self) -> str:
        try:
            ref = self.git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
            if ref:
                return ref.split("/", 1)[1]
        except GitError:
            pass
        for candidate in ("main", "master", "develop"):
            if self.git("rev-parse", "--verify", "--quiet", candidate, check=False):
                return candidate
        return self.git("rev-parse", "--abbrev-ref", "HEAD")

    def head_is_valid(self) -> bool:
        return bool(self.git("rev-parse", "--verify", "--quiet", "HEAD", check=False))

    @staticmethod
    def slug(text: str) -> str:
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]

    # ------------------------------------------------------------------ lifecycle
    def branch_for(self, story_id: str, title: str = "") -> str:
        suffix = f"-{self.slug(title)}" if title else ""
        return f"{self.branch_prefix}{story_id.lower()}{suffix}"

    def path_for(self, story_id: str) -> Path:
        return self.dir / story_id

    def create(self, story_id: str, *, title: str = "", base: str | None = None) -> Worktree:
        if not self.head_is_valid():
            raise GitError(
                "o repositório não tem commits ainda; faça o primeiro commit antes de despachar histórias"
            )
        base = base or self.default_branch()
        branch = self.branch_for(story_id, title)
        path = self.path_for(story_id)
        self.dir.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = self.get(story_id)
            if existing:
                return existing
            raise GitError(f"diretório {path} existe mas não é um worktree válido")
        if self.git("rev-parse", "--verify", "--quiet", branch, check=False):
            self.git("worktree", "add", str(path), branch)
        else:
            self.git("worktree", "add", "-b", branch, str(path), base)
        return Worktree(story_id=story_id, path=path, branch=branch, base=base)

    def get(self, story_id: str) -> Worktree | None:
        path = self.path_for(story_id)
        if not (path / ".git").exists():
            return None
        branch = self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=path, check=False) or ""
        return Worktree(story_id=story_id, path=path, branch=branch, base=self.default_branch())

    def list(self) -> list[Worktree]:
        out: list[Worktree] = []
        if not self.dir.exists():
            return out
        for child in sorted(self.dir.iterdir()):
            wt = self.get(child.name)
            if wt:
                out.append(wt)
        return out

    def remove(self, story_id: str, *, delete_branch: bool = False) -> bool:
        path = self.path_for(story_id)
        wt = self.get(story_id)
        if wt is None:
            return False
        self.git("worktree", "remove", "--force", str(path), check=False)
        self.git("worktree", "prune", check=False)
        if delete_branch and wt.branch:
            self.git("branch", "-D", wt.branch, check=False)
        return True

    # ----------------------------------------------------------------- operations
    def status(self, wt: Worktree) -> list[str]:
        return [
            line
            for line in self.git("status", "--porcelain", cwd=wt.path).splitlines()
            if line.strip()
        ]

    def diff_stat(self, wt: Worktree) -> str:
        return self.git("diff", "--stat", f"{wt.base}...HEAD", cwd=wt.path, check=False)

    def diff(self, wt: Worktree, *, max_chars: int = 20000) -> str:
        text = self.git("diff", f"{wt.base}...HEAD", cwd=wt.path, check=False)
        return text if len(text) <= max_chars else text[:max_chars] + "\n… (diff truncado)"

    JUNK = (
        ":!**/__pycache__/**",
        ":!*.pyc",
        ":!**/.pytest_cache/**",
        ":!**/.ruff_cache/**",
        ":!**/.mypy_cache/**",
        ":!**/node_modules/**",
        ":!.DS_Store",
        ":!**/.DS_Store",
        ":!**/*.egg-info/**",
    )

    def commit_all(self, wt: Worktree, message: str) -> CommitResult | None:
        self.git("add", "-A", "--", ".", *self.JUNK, cwd=wt.path)
        staged = [
            f
            for f in self.git("diff", "--cached", "--name-only", cwd=wt.path).splitlines()
            if f.strip()
        ]
        if not staged:
            return None
        self.git(
            "-c",
            "user.name=Loompa",
            "-c",
            "user.email=loompa@localhost",
            "commit",
            "-q",
            "-m",
            message,
            cwd=wt.path,
        )
        return CommitResult(
            sha=self.git("rev-parse", "--short", "HEAD", cwd=wt.path), files=len(staged)
        )

    def log(self, wt: Worktree, limit: int = 20) -> list[str]:
        return self.git(
            "log", "--oneline", f"-{limit}", f"{wt.base}..HEAD", cwd=wt.path, check=False
        ).splitlines()

    def rebase_on_base(self, wt: Worktree) -> bool:
        """Try to rebase the story branch on its base; abort cleanly on conflict."""
        out = subprocess.run(
            ["git", "rebase", wt.base], cwd=wt.path, capture_output=True, text=True
        )
        if out.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=wt.path, capture_output=True)
            return False
        return True

    def merge_into_base(self, wt: Worktree, *, message: str | None = None) -> str:
        """Fast-forward or merge the story branch into base in the main checkout."""
        current = self.git("rev-parse", "--abbrev-ref", "HEAD")
        if current != wt.base:
            self.git("checkout", "-q", wt.base)
        msg = message or f"merge: {wt.branch}"
        self.git(
            "-c",
            "user.name=Loompa",
            "-c",
            "user.email=loompa@localhost",
            "merge",
            "--no-ff",
            "-q",
            "-m",
            msg,
            wt.branch,
        )
        return self.git("rev-parse", "--short", "HEAD")

    def has_conflicts_with_base(self, wt: Worktree) -> bool:
        merge_base = self.git("merge-base", wt.base, "HEAD", cwd=wt.path)
        out = subprocess.run(
            ["git", "merge-tree", merge_base, wt.base, "HEAD"],
            cwd=wt.path,
            capture_output=True,
            text=True,
        ).stdout
        return "<<<<<<<" in out or "changed in both" in out
