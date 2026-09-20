"""Recipes for running Loompa on a schedule: cron lines and launchd agents (Fase 6).

One job, idempotent and safe to run unattended: the nightly cycle, `loompa run`, which works
through the running sprint until nothing is runnable (without `--watch` it ends by itself, so an
overlapping night cannot pile up on launchd).

Refreshing the model catalogue is deliberately *not* scheduled. Swapping models disturbs prompts
tuned for the ones in use, so it happens when the founder asks for it, on the settings screen or
with `loompa models sync`. What does watch by itself needs no clock: a price that goes up is
noticed the next time the catalogue is read, and a model that left the air announces itself on
the first call that fails (`loompa.models_sync.ModelWatch`).

Only builds text. Nothing here installs or loads anything; `loompa schedule` prints it (or writes the
files where the founder asks) and says how to switch it on. Keys are not part of a job: Loompa
reads them from its own secrets files, which is why a job started by cron or launchd needs none.
"""

from __future__ import annotations

import plistlib
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

STANDARD_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
TOOLS = ("git", "gh", "opencode", "coderabbit")  # what a run shells out to


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class Job:
    key: str  # run
    args: tuple[str, ...]
    hour: int
    minute: int
    day: int | None  # day of the month; None = every day
    what: str  # pt-BR, one line


def parse_time(value: str) -> tuple[int, int]:
    hour, sep, minute = value.partition(":")
    if (
        not sep
        or not hour.isdigit()
        or not minute.isdigit()
        or not (0 <= int(hour) < 24 and 0 <= int(minute) < 60)
    ):
        raise ScheduleError(f"horário inválido: {value!r}. Use HH:MM, por exemplo 02:00.")
    return int(hour), int(minute)


def jobs(slug: str, *, run_at: str = "02:00") -> list[Job]:
    run_h, run_m = parse_time(run_at)
    return [
        Job(
            "run",
            ("run", "--factory", slug),
            run_h,
            run_m,
            None,
            f"todo dia às {run_at}: trabalha no sprint em andamento até não sobrar nada a fazer",
        ),
    ]


def loompa_bin() -> str:
    """The command a job should run: the installed `loompa`, as an absolute path."""
    found = shutil.which("loompa")
    if found:  # as installed, not resolved: a symlink survives an upgrade, its target may not
        return found
    return str(Path(sys.argv[0]).resolve()) if sys.argv and sys.argv[0] else "loompa"


def job_path(binary: str) -> str:
    """PATH for a job: cron and launchd start with almost none, and a run needs git and friends."""
    dirs: list[str] = [str(Path(binary).parent)] if "/" in binary else []
    dirs += [str(Path(p).parent) for tool in TOOLS if (p := shutil.which(tool))]
    dirs += STANDARD_PATH
    return ":".join(dict.fromkeys(dirs))


def label(slug: str, job: Job) -> str:
    return f"com.loompa.{slug}.{job.key}"


def cron_block(slug: str, root: Path, log_dir: Path, binary: str, todo: list[Job]) -> str:
    lines = [f"PATH={job_path(binary)}"]
    for job in todo:
        command = " ".join(shlex.quote(a) for a in (binary, *job.args))
        log = shlex.quote(str(log_dir / f"{job.key}.log"))
        when = f"{job.minute} {job.hour} {job.day if job.day else '*'} * *"
        lines.append(f"# {job.what}")
        lines.append(f"{when} cd {shlex.quote(str(root))} && {command} >> {log} 2>&1")
    return "\n".join(lines) + "\n"


def launchd_plist(slug: str, root: Path, log_dir: Path, binary: str, job: Job) -> str:
    when: dict[str, int] = {"Hour": job.hour, "Minute": job.minute}
    if job.day:
        when["Day"] = job.day
    data = {
        "Label": label(slug, job),
        "ProgramArguments": [binary, *job.args],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": when,
        "StandardOutPath": str(log_dir / f"{job.key}.log"),
        "StandardErrorPath": str(log_dir / f"{job.key}.log"),
        "EnvironmentVariables": {"PATH": job_path(binary)},
    }
    return plistlib.dumps(data).decode("utf-8")
