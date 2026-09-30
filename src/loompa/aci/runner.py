"""Bounded subprocess execution for Tier 3 tooling (tests, linters, formatters)."""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import sys
import tempfile
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


# Their mere presence turns colour ON in Rich (Typer's help), ruff and others, whatever the
# value: `FORCE_COLOR=0` here put ANSI codes in every help text a factory's tests read (`contas`
# S-030 failed on them). Output is captured, so NO_COLOR alone says what is meant.
FORCES_COLOR = ("FORCE_COLOR", "CLICOLOR_FORCE", "PY_COLORS")


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
    # Fresh bytecode cache per run: pytest's assertion-rewrite cache is keyed by (size, mtime in
    # seconds), so a same-length fix written within the same second would run stale bytecode.
    pyc_dir = tempfile.mkdtemp(prefix="loompa-pyc-")
    merged_env = {
        **{k: v for k, v in os.environ.items() if k not in FORCES_COLOR},
        "CI": "1",
        "NO_COLOR": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPYCACHEPREFIX": pyc_dir,
        **(env or {}),
    }
    if (
        Path(merged_env.get("VIRTUAL_ENV") or "/nonexistent").resolve()
        == Path(sys.prefix).resolve()
    ):
        # Loompa's own virtualenv is not the factory's: `uv run` warned about it on every
        # command and the warning ended up in what agents read
        merged_env.pop("VIRTUAL_ENV", None)
    try:
        proc = await asyncio.create_subprocess_exec(
            *shlex.split(command),
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
        )
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        shutil.rmtree(pyc_dir, ignore_errors=True)
        return CommandResult(
            command=command,
            returncode=127,
            stdout="",
            stderr=f"não foi possível executar `{command}`: {exc}",
        )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        timed_out = False
    except TimeoutError:
        proc.kill()
        out, err = await proc.communicate()
        timed_out = True
    duration = int((loop.time() - start) * 1000)
    shutil.rmtree(pyc_dir, ignore_errors=True)
    return CommandResult(
        command=command,
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=out.decode("utf-8", "replace")[-max_output:],
        stderr=err.decode("utf-8", "replace")[-max_output:],
        timed_out=timed_out,
        duration_ms=duration,
    )
