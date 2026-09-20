"""Cron and launchd recipes for the nightly cycle and the monthly model refresh (Fase 6)."""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from loompa.cli.main import app
from loompa.config.secrets import hub_home
from loompa.scheduling import (
    ScheduleError,
    cron_block,
    jobs,
    label,
    launchd_plist,
)

runner = CliRunner()
BIN = "/Users/me/.local/bin/loompa"
ROOT = Path("/Users/me/My Project")
LOGS = Path("/Users/me/.loompa/logs")


def test_the_models_refresh_is_monthly_by_default_and_the_cycle_nightly():
    run, sync = jobs("demo")
    assert (run.key, run.hour, run.minute, run.day) == ("run", 2, 0, None)
    assert (sync.key, sync.hour, sync.minute, sync.day) == ("models-sync", 9, 0, 1)
    assert run.args == ("run", "--factory", "demo")
    assert sync.args == ("models", "sync", "--factory", "demo")


def test_cron_lines_quote_paths_log_and_carry_a_path_for_git():
    block = cron_block(
        "demo", ROOT, LOGS, BIN, jobs("demo", run_at="03:30", sync_day=15, sync_at="10:05")
    )
    lines = [ln for ln in block.splitlines() if not ln.startswith("#")]
    assert lines[0].startswith("PATH=") and "/.local/bin" in lines[0] and "/usr/bin" in lines[0]
    assert lines[1] == (
        f"30 3 * * * cd '{ROOT}' && {BIN} run --factory demo >> {LOGS}/run.log 2>&1"
    )
    assert lines[2] == (
        f"5 10 15 * * cd '{ROOT}' && {BIN} models sync --factory demo >> {LOGS}/models-sync.log 2>&1"
    )
    assert "sk-" not in block  # a job carries no key: Loompa reads its own secrets files


def test_launchd_plist_is_valid_and_the_monthly_job_names_its_day():
    run, sync = jobs("demo")
    nightly = plistlib.loads(launchd_plist("demo", ROOT, LOGS, BIN, run).encode())
    monthly = plistlib.loads(launchd_plist("demo", ROOT, LOGS, BIN, sync).encode())
    assert nightly["Label"] == label("demo", run) == "com.loompa.demo.run"
    assert nightly["ProgramArguments"] == [BIN, "run", "--factory", "demo"]
    assert nightly["StartCalendarInterval"] == {"Hour": 2, "Minute": 0}  # no Day: every day
    assert monthly["StartCalendarInterval"] == {"Hour": 9, "Minute": 0, "Day": 1}
    assert monthly["WorkingDirectory"] == str(ROOT)
    assert monthly["StandardOutPath"] == str(LOGS / "models-sync.log")
    assert "/usr/bin" in monthly["EnvironmentVariables"]["PATH"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"run_at": "25:00"},
        {"run_at": "2am"},
        {"sync_at": "09:60"},
        {"sync_day": 0},
        {"sync_day": 29},
    ],
)
def test_impossible_times_and_days_are_refused(kwargs):
    with pytest.raises(ScheduleError):
        jobs("demo", **kwargs)


@pytest.fixture
def demo(hub, brownfield_repo: Path) -> Path:
    assert (
        runner.invoke(app, ["init", str(brownfield_repo), "--yes", "--name", "Demo"]).exit_code == 0
    )
    return brownfield_repo


def test_cli_prints_the_cron_recipe(demo: Path):
    result = runner.invoke(
        app, ["schedule", "--factory", "demo", "--for", "cron", "--sync-day", "5"]
    )
    assert result.exit_code == 0, result.stdout
    assert "crontab -e" in result.stdout
    assert "0 9 5 * *" in " ".join(result.stdout.split())
    assert "models sync --factory demo" in result.stdout and "run --factory demo" in result.stdout


def test_cli_writes_launchd_files_and_says_how_to_turn_them_on(demo: Path, tmp_path: Path, hub):
    target = tmp_path / "LaunchAgents"
    result = runner.invoke(
        app, ["schedule", "--factory", "demo", "--for", "launchd", "--write", str(target)]
    )
    assert result.exit_code == 0, result.stdout
    files = sorted(p.name for p in target.iterdir())
    assert files == ["com.loompa.demo.models-sync.plist", "com.loompa.demo.run.plist"]
    for path in target.iterdir():
        plistlib.loads(path.read_bytes())  # parses
    assert (hub_home() / "logs").is_dir()  # launchd does not create the log folder
    assert "launchctl bootstrap" in result.stdout


def test_cli_rejects_bad_input_with_exit_code_1(demo: Path):
    for args in (["--for", "systemd"], ["--run-at", "99:00"], ["--sync-day", "31"]):
        assert runner.invoke(app, ["schedule", "--factory", "demo", *args]).exit_code == 1
