"""Git worktree isolation: one directory + branch per story, outside the repo (see
`default_worktrees_dir`).

Several Loompas code simultaneously without touching the repo root. All git calls are
Tier 3 (deterministic, $0). Output is trimmed so nothing noisy leaks into agent context.
"""

from __future__ import annotations

import copy
import hashlib
import re
import shutil
import subprocess
import threading
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from loompa.config.secrets import KEY_SHAPED


class GitError(RuntimeError):
    pass


class GitAuthorityError(GitError):
    """A role other than the Deployer tried an operation that changes shared history."""


# Only the Deployer may change what the base branch holds or talk to the remote (ADR-0006 §5).
DEPLOYER_ROLE = "deployer"
DEPLOYER_ONLY = frozenset(
    {"merge", "rebase", "push", "pull", "checkout", "switch", "reset", "cherry-pick", "tag"}
)
_BOT = ("-c", "user.name=Loompa", "-c", "user.email=loompa@localhost")
# Stories are dispatched concurrently; only one of them may create the first commit.
_BOOTSTRAP_LOCK = threading.Lock()
_BRANCH_DELETE_FLAGS = frozenset({"-d", "-D", "--delete", "-m", "-M", "--move", "-f", "--force"})
_GLOBAL_OPTS_WITH_VALUE = frozenset({"-c", "-C", "--git-dir", "--work-tree", "--namespace"})


def git_subcommand(args: tuple[str, ...]) -> str:
    """The git subcommand in `args`, skipping global options such as `-c user.name=x`."""
    it = iter(args)
    for arg in it:
        if arg in _GLOBAL_OPTS_WITH_VALUE:
            next(it, None)
        elif not arg.startswith("-"):
            return arg
    return ""


def is_deployer_only(args: tuple[str, ...]) -> bool:
    sub = git_subcommand(args)
    if sub in DEPLOYER_ONLY:
        return True
    return sub == "branch" and any(a in _BRANCH_DELETE_FLAGS for a in args)


def settle_additions(text: str) -> tuple[str, int]:
    """Resolve, without a model, the diff3 conflict blocks where the common ancestor had
    nothing: both sides only added lines (two stories appending tests at the end of the same
    file — `contas` S-007's 22k test file), so the answer is both, story first. Blocks with a
    real common part are left in the usual two-way form for the Worker. Returns the text and
    how many blocks are left."""
    out: list[str] = []
    ours: list[str] = []
    base: list[str] = []
    theirs: list[str] = []
    section, left = None, 0
    for line in text.splitlines(keepends=True):
        if line.startswith("<<<<<<<"):
            section, head, ours, base, theirs, saw_base = "ours", line, [], [], [], False
        elif section == "ours" and line.startswith("|||||||"):
            section, saw_base = "base", True
        elif section in ("ours", "base") and line.rstrip("\r\n") == "=======":
            section, sep = "theirs", line
        elif section == "theirs" and line.startswith(">>>>>>>"):
            if saw_base and not "".join(base).strip():
                out += ours + theirs
            else:
                out += [head, *ours, sep, *theirs, line]
                left += 1
            section = None
        elif section == "ours":
            ours.append(line)
        elif section == "base":
            base.append(line)
        elif section == "theirs":
            theirs.append(line)
        else:
            out.append(line)
    if section is not None:  # unbalanced markers: give the text back untouched
        return text, max(left, 1)
    return "".join(out), left


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


def default_worktrees_dir(repo_root: Path) -> Path:
    """Where a repo's story worktrees live: outside the repo, under the hub. Inside it (the old
    `.loompa/worktrees/`), every tool that walks up for its config found the main project's:
    pytest took the root `pyproject.toml` as rootdir and imported the main checkout's package
    instead of the story's own code (`contas` S-003); Node would resolve the root node_modules."""
    from loompa.config.secrets import hub_home

    root = Path(repo_root).resolve()
    tag = hashlib.sha1(str(root).encode()).hexdigest()[:8]
    return hub_home() / "worktrees" / f"{root.name}-{tag}"


class WorktreeManager:
    def __init__(
        self,
        repo_root: Path,
        worktrees_dir: Path | None = None,
        *,
        branch_prefix: str = "loompa/",
        actor: str | None = None,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.dir = (worktrees_dir or default_worktrees_dir(self.repo_root)).resolve()
        # where worktrees lived before they moved out of the repo; moved on first use
        self.legacy_dir = self.repo_root / ".loompa" / "worktrees"
        self.branch_prefix = branch_prefix
        self.actor = actor

    def as_role(self, role: str) -> WorktreeManager:
        """A view of this manager acting as `role`. Only `deployer` may merge, push or open PRs."""
        view = copy.copy(self)
        view.actor = role
        return view

    def _require_deployer(self, what: str) -> None:
        if self.actor != DEPLOYER_ROLE:
            who = self.actor or "engine"
            raise GitAuthorityError(f"{what}: só o Deployer pode fazer isso (chamado por {who})")

    # ------------------------------------------------------------------ plumbing
    def git(
        self, *args: str, cwd: Path | None = None, check: bool = True, timeout: int = 120
    ) -> str:
        if is_deployer_only(args):
            self._require_deployer(f"git {git_subcommand(args)}")
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
        if not (path / ".git").exists() and not self._move_legacy(story_id):
            return None
        branch = self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=path, check=False) or ""
        return Worktree(story_id=story_id, path=path, branch=branch, base=self.default_branch())

    def _move_legacy(self, story_id: str) -> bool:
        """A worktree still under the repo's `.loompa/worktrees/` moves out, keeping its branch
        and any uncommitted work (`git worktree move`)."""
        old = self.legacy_dir / story_id
        if self.legacy_dir == self.dir or not (old / ".git").exists():
            return False
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            self.git("worktree", "move", str(old), str(self.path_for(story_id)))
        except GitError:
            return False
        # a virtualenv's scripts carry their old absolute path; `uv` rebuilds it on next use
        shutil.rmtree(self.path_for(story_id) / ".venv", ignore_errors=True)
        return True

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
        if delete_branch:  # check first: never remove the worktree and then refuse the branch
            self._require_deployer("git branch -D")
        path = self.path_for(story_id)
        wt = self.get(story_id)
        if wt is None:
            return False
        self.git("worktree", "remove", "--force", str(path), check=False)
        self.git("worktree", "prune", check=False)
        if delete_branch and wt.branch:
            self.git("branch", "-D", wt.branch, check=False)
        return True

    def prune_merged_branches(self, base: str | None = None) -> list[str]:
        """Delete the story branches already merged into the base that no worktree uses: what
        stories merged before branches were deleted on merge left behind (`contas` had one per
        delivery). Deployer only; `-d` refuses anything not merged."""
        self._require_deployer("git branch -d")
        base = base or self.default_branch()
        listed = self.git(
            "branch",
            "--merged",
            base,
            "--format=%(refname:short)",
            "--list",
            f"{self.branch_prefix}*",
            check=False,
        )
        busy = {wt.branch for wt in self.list()}
        pruned = []
        for branch in (b.strip() for b in listed.splitlines()):
            if not branch or branch in busy:
                continue
            try:
                self.git("branch", "-d", branch)
            except GitError:  # checked out somewhere else, or not merged after all
                continue
            pruned.append(branch)
        return pruned

    # ----------------------------------------------------------------- operations
    def status(self, wt: Worktree) -> list[str]:
        return [
            line
            for line in self.git(
                "status", "--porcelain", "--", ".", *self.JUNK, cwd=wt.path
            ).splitlines()
            if line.strip()
        ]

    def diff_stat(self, wt: Worktree) -> str:
        return self.git("diff", "--stat", f"{wt.base}...HEAD", cwd=wt.path, check=False)

    def diff(self, wt: Worktree, *, max_chars: int = 20000) -> str:
        text = self.git("diff", f"{wt.base}...HEAD", cwd=wt.path, check=False)
        return text if len(text) <= max_chars else text[:max_chars] + "\n… (diff truncado)"

    def diff_working(self, wt: Worktree, *, max_chars: int = 20000) -> str:
        """Uncommitted changes (modified and new files) against HEAD — what a task produced
        before its commit. Intent-to-add makes untracked files show up with their content."""
        self.git("add", "-A", "-N", "--", ".", *self.JUNK, cwd=wt.path, check=False)
        text = self.git("diff", "HEAD", "--", ".", *self.JUNK, cwd=wt.path, check=False)
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

    # Never part of a first commit, whatever the repo's .gitignore says.
    SECRET_PATHS = tuple(
        f":(exclude,glob)**/{pat}"  # glob magic: `**/` also matches the repo root
        for pat in (".env", "*.env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*")
    )
    # What the factory's agents keep rewriting in the main checkout. Tracked, it left main
    # permanently dirty after the first story, in the way of the Deployer's merges.
    AGENT_WRITTEN = tuple(
        f":(exclude,glob).loompa/{p}"
        for p in ("specs/**", "decisions/**", "learnings.md", "onboarding_report.md", "audit.json")
    )
    _SECRET_IN_TEXT = KEY_SHAPED

    def initial_commit(self, message: str = "chore: esqueleto inicial") -> CommitResult | None:
        """Give a repo with no commits its first one, so story worktrees have a base to branch
        from. Deployer only (it creates the base branch). Honours .gitignore, never stages
        secret-shaped files, and unstages any file whose text carries something shaped like an
        API key. Returns None when HEAD already existed (someone else got there first)."""
        self._require_deployer("commit inicial")
        with _BOOTSTRAP_LOCK:
            if self.head_is_valid():
                return None
            self.git("add", "-A", "--", ".", *self.JUNK, *self.SECRET_PATHS, *self.AGENT_WRITTEN)
            staged = [
                f for f in self.git("diff", "--cached", "--name-only").splitlines() if f.strip()
            ]
            for rel in list(staged):
                path = self.repo_root / rel
                try:
                    if path.stat().st_size > 512_000:
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if self._SECRET_IN_TEXT.search(text):
                    self.git("rm", "-q", "--cached", "--", rel)
                    staged.remove(rel)
            self.git(
                "-c",
                "user.name=Loompa",
                "-c",
                "user.email=loompa@localhost",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                message,
            )
            return CommitResult(sha=self.git("rev-parse", "--short", "HEAD"), files=len(staged))

    def log(self, wt: Worktree, limit: int = 20) -> list[str]:
        return self.git(
            "log", "--oneline", f"-{limit}", f"{wt.base}..HEAD", cwd=wt.path, check=False
        ).splitlines()

    def behind_base(self, wt: Worktree) -> bool:
        """True when the base branch has commits the story branch does not (a merge landed)."""
        try:
            self.git("merge-base", "--is-ancestor", wt.base, "HEAD", cwd=wt.path)
        except GitError:
            return True
        return False

    @contextmanager
    def base_checkout(self, ref: str) -> Iterator[Path]:
        """A throw-away detached checkout of `ref`, to run the checks on the base alone."""
        path = self.dir / f"_base-{uuid.uuid4().hex[:8]}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.git("worktree", "add", "--detach", "-q", str(path), ref)
        try:
            yield path
        finally:
            self.git("worktree", "remove", "--force", str(path), check=False)
            self.git("worktree", "prune", check=False)

    # Generated from the manifest: on a conflict the base's copy wins and the tool re-locks.
    LOCKFILES = frozenset(
        {"uv.lock", "poetry.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock"}
    )

    def merge_base_into(self, wt: Worktree) -> list[str]:
        """Merge the base into the story branch (Deployer only). Returns the files still in
        conflict (lockfiles already settled on the base's copy); [] means the merge is committed.
        A merge, not a rebase: conflicts are resolved once, not once per story commit, and the
        delivery's rebase is then a no-op because the base is already in the branch."""
        self._require_deployer("git merge")
        self.git(
            *_BOT,
            "-c",
            "merge.conflictStyle=diff3",
            "merge",
            "--no-ff",
            "--no-commit",
            wt.base,
            cwd=wt.path,
            check=False,
        )
        conflicts = self.conflicted_files(wt)
        for rel in [c for c in conflicts if c.rsplit("/", 1)[-1] in self.LOCKFILES]:
            self.git("checkout", "--theirs", "--", rel, cwd=wt.path, check=False)
            self.git("add", "--", rel, cwd=wt.path)
            conflicts.remove(rel)
        for rel in list(conflicts):
            path = wt.path / rel
            if not path.is_file():
                continue
            text, left = settle_additions(path.read_text(encoding="utf-8", errors="replace"))
            path.write_text(text, encoding="utf-8")
            if not left:  # both sides only added lines: keeping both is the whole answer
                self.git("add", "--", rel, cwd=wt.path)
                conflicts.remove(rel)
        if not conflicts:
            self.conclude_merge(wt)
        return conflicts

    def merge_in_progress(self, wt: Worktree) -> bool:
        return bool(self.git("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=wt.path, check=False))

    def conflicted_files(self, wt: Worktree) -> list[str]:
        out = self.git("diff", "--name-only", "--diff-filter=U", cwd=wt.path, check=False)
        return [f for f in out.splitlines() if f.strip()]

    def has_conflict_markers(self, wt: Worktree, files: list[str]) -> list[str]:
        marked = []
        for rel in files:
            path = wt.path / rel
            text = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""
            if re.search(r"^(<{7}|>{7})( |$)", text, re.M):
                marked.append(rel)
        return marked

    def conclude_merge(self, wt: Worktree) -> str:
        self._require_deployer("git merge")
        self.git("add", "-A", "--", ".", *self.JUNK, cwd=wt.path)
        self.git(
            *_BOT, "commit", "-q", "--no-edit", "-m", f"merge: {wt.base} na história", cwd=wt.path
        )
        return self.git("rev-parse", "--short", "HEAD", cwd=wt.path)

    def abort_merge(self, wt: Worktree) -> None:
        self._require_deployer("git merge")
        self.git("merge", "--abort", cwd=wt.path, check=False)

    def abort_base_merge(self) -> None:
        """Abort a merge left half-done in the main checkout (a merge into the base that died)."""
        self._require_deployer("git merge")
        self.git("merge", "--abort", check=False)

    def main_status(self) -> str:
        """`git status --short --branch` of the main checkout, for the Deployer's diagnosis."""
        return self.git("status", "--short", "--branch", check=False)

    def rebase_on_base(self, wt: Worktree) -> bool:
        """Try to rebase the story branch on its base; abort cleanly on conflict. A branch that
        already contains the base (merged in by `merge_base_into`) is left alone: replaying its
        commits over the merge would meet the conflicts the merge already resolved."""
        self._require_deployer("git rebase")
        if not self.behind_base(wt):
            return True
        out = subprocess.run(
            ["git", "rebase", wt.base], cwd=wt.path, capture_output=True, text=True
        )
        if out.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=wt.path, capture_output=True)
            return False
        return True

    def merge_into_base(self, wt: Worktree, *, message: str | None = None) -> str:
        """Fast-forward or merge the story branch into base in the main checkout."""
        self._require_deployer("git merge")
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

    def open_pull_request(
        self, wt: Worktree, *, title: str, body: str, timeout: int = 120
    ) -> str | None:
        """Push the story branch and open a PR with `gh`; None when there is no remote or no `gh`."""
        self._require_deployer("abrir pull request")
        if not shutil.which("gh") or not self.git("remote", "get-url", "origin", check=False):
            return None
        try:
            # The story branch is the Deployer's own and is rebased before each delivery, so a
            # second delivery (after review changes) must replace what the PR shows.
            self.git(
                "push",
                "--force-with-lease",
                "-u",
                "origin",
                wt.branch,
                cwd=wt.path,
                timeout=timeout,
            )
            out = subprocess.run(
                ["gh", "pr", "create", "--base", wt.base, "--head", wt.branch]
                + ["--title", title, "--body", body],
                cwd=wt.path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (GitError, subprocess.SubprocessError):
            return None
        return out.stdout.strip().splitlines()[-1] if out.returncode == 0 else None

    def has_conflicts_with_base(self, wt: Worktree) -> bool:
        merge_base = self.git("merge-base", wt.base, "HEAD", cwd=wt.path)
        out = subprocess.run(
            ["git", "merge-tree", merge_base, wt.base, "HEAD"],
            cwd=wt.path,
            capture_output=True,
            text=True,
        ).stdout
        return "<<<<<<<" in out or "changed in both" in out
