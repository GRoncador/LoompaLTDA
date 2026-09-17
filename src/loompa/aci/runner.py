"""Bounded subprocess execution for Tier 3 tooling (tests, linters, formatters)."""

from __future__ import annotations

import asyncio
import os
import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()


async def run_command(
    command: str,
    cwd: Path,
    *,
    timeout: int = 600,
    max_output: int = 200_000,
    env: dict[str, str] | None = None,
) -> CommandResult:
    loop = asyncio.get_running_loop()
    start = loop.time()
    merged_env = {
        **os.environ,
        "CI": "1",
        "NO_COLOR": "1",
        "PYTHONUNBUFFERED": "1",
        "FORCE_COLOR": "0",
        **(env or {}),
    }
    proc = await asyncio.create_subprocess_exec(
        *shlex.split(command),
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=merged_env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        timed_out = False
    except TimeoutError:
        proc.kill()
        out, err = await proc.communicate()
        timed_out = True
    duration = int((loop.time() - start) * 1000)
    return CommandResult(
        command=command,
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=out.decode("utf-8", "replace")[-max_output:],
        stderr=err.decode("utf-8", "replace")[-max_output:],
        timed_out=timed_out,
        duration_ms=duration,
    )
