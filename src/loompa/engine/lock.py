"""One engine per factory.

Two schedulers on the same factory dispatch the same stories twice: two Workers write the same
worktree, two merges of the base race, two deliveries go out. It happened while validating
`contas` (a `loompa run` left alive next to a new one). The lock is an advisory `flock` on
`.loompa/engine.lock`, held for the whole run and released by the OS if the process dies, so
a crash never leaves the factory locked. The file carries the holder's pid for the message.
"""

from __future__ import annotations

import os
from pathlib import Path

try:  # POSIX only; elsewhere the engine runs without the guard
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


class EngineBusy(RuntimeError):
    def __init__(self, pid: str):
        super().__init__(
            f"outra esteira já está rodando nesta fábrica (processo {pid or '?'}); "
            "pare aquela antes de ligar outra"
        )
        self.pid = pid


class EngineLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> None:
        if fcntl is None or self._fh is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.seek(0)
            pid = fh.read().strip()
            fh.close()
            raise EngineBusy(pid) from None
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.seek(0)
            self._fh.truncate()
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def holder(self) -> str | None:
        """Pid of the process running this factory's engine, or None when it is free. Probes
        with a lock of its own, so it never releases one this instance holds."""
        probe = EngineLock(self.path)
        try:
            probe.acquire()
        except EngineBusy as busy:
            return busy.pid or "?"
        probe.release()
        return None
