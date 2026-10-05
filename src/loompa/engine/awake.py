"""Keep a Mac awake while a story runs (plan 8.6.8).

Three sleeps added up to 40 minutes stopped in `contas` SP-003: asyncio timers run on the
monotonic clock, which does not advance while the machine sleeps, so nothing in the engine can
notice or prevent it. While at least one story runs, the engine holds `caffeinate -s` (macOS
honours that assertion only on AC power; on battery the Mac still sleeps, as the founder expects)
and lets go when nothing runs. `-w <pid>` ties the assertion to the engine's process, so a crash
never leaves the Mac awake. No effect outside macOS or without `caffeinate`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


class Awake:
    def __init__(self, *, enabled: bool | None = None) -> None:
        self.enabled = sys.platform == "darwin" if enabled is None else enabled
        self._proc: subprocess.Popen[bytes] | None = None

    @property
    def holding(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def hold(self, busy: bool) -> None:
        """Hold the assertion while `busy`, release it when not; cheap to call every cycle."""
        if busy and not self.holding and self.enabled:
            exe = shutil.which("caffeinate")
            if exe is None:
                self.enabled = False
                return
            try:
                self._proc = subprocess.Popen(
                    [exe, "-s", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                self.enabled = False
        elif not busy:
            self.release()

    def release(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
