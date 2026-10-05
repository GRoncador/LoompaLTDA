"""The merge queue: approved stories go into the base one at a time, in the order approved.

Answers reach the factory from more than one process (the dashboard runs with `--no-engine` next
to `loompa run`, and `loompa inbox answer` is a third), so a lock inside the engine guarded
nothing the dashboard did (tamagotchi SP-008, ADR-0029). Two pieces make the queue:

- `MergeLock`: an advisory `flock` on `<git dir>/loompa-merge.lock`, held by whoever touches the
  base — the engine for a whole integration (sync, checks, merge), an approval for its instant
  merge. The OS releases it if the holder dies. It lives in the git dir: it guards the
  repository, and nothing there is ever committed.
- the queue itself: `MERGE_QUEUE_KEY` in the story's state, with a sequence number taken at
  approval. It lives in the shared store, so every process sees it; only the engine works it,
  head first (`Scheduler._eligible`).

A story that leaves the queue (back to `dev`, blocked for the founder, merged) drops its entry, so
one stuck story never holds the others.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from loompa.engine.state import Stage
from loompa.worktrees import WorktreeManager

try:  # POSIX only; elsewhere approvals merge without the cross-process guard
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

MERGE_QUEUE_KEY = "merge_queue"  # {"seq": int, "why": str}: approved, waiting for its merge


def enqueue(extra: dict[str, Any], why: str) -> int:
    """Put an approved story in the queue (or move it to the end: a retry is a new turn)."""
    seq = time.time_ns()
    extra[MERGE_QUEUE_KEY] = {"seq": seq, "why": why}
    return seq


def dequeue(extra: dict[str, Any]) -> None:
    extra.pop(MERGE_QUEUE_KEY, None)


def queued(extra: Mapping[str, Any]) -> dict[str, Any] | None:
    entry = extra.get(MERGE_QUEUE_KEY)
    return entry if isinstance(entry, dict) and "seq" in entry else None


def merge_queue(rows: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """The stories waiting for their merge, head first. Only a story in `review` is in it: one
    paused for the founder, sent back to the backlog or finished is not, whatever its state still
    says, so it never holds the line."""
    waiting = []
    for sid, row in rows.items():
        if row.get("stage") != Stage.REVIEW:
            continue
        entry = queued((row.get("state") or {}).get("extra") or {})
        if entry is not None:
            waiting.append((int(entry["seq"]), sid))
    return [sid for _, sid in sorted(waiting)]


def waits_its_turn(
    rows: Mapping[str, Mapping[str, Any]], story_id: str, running: set[str] | dict[str, Any]
) -> bool:
    """True for a queued story that may not run yet: it is not the head, or another queued
    story is being worked on."""
    queue = merge_queue(rows)
    if story_id not in queue:
        return False
    return queue[0] != story_id or any(sid in running for sid in queue if sid != story_id)


class MergeLock:
    """Who may touch the base right now (see the module docstring). One instance per holding:
    a second instance in the same process is refused like another process would be."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fh = None

    @classmethod
    def for_repo(cls, worktrees: WorktreeManager) -> MergeLock:
        git_dir = Path(worktrees.git("rev-parse", "--git-common-dir"))
        if not git_dir.is_absolute():
            git_dir = worktrees.repo_root / git_dir
        return cls(git_dir / "loompa-merge.lock")

    def try_acquire(self) -> bool:
        if fcntl is None:
            return True
        if self._fh is not None:
            return False
        fh = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    async def acquire(self, poll_s: float = 0.2) -> None:
        """Wait for the lock without blocking the event loop. The only other holders are
        approvals merging on the spot, which take a moment."""
        while not self.try_acquire():
            await asyncio.sleep(poll_s)

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    async def __aenter__(self) -> MergeLock:
        await self.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.release()
