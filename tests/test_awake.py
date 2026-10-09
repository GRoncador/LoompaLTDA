"""Plan 8.6.8: the engine holds `caffeinate -s` while a story runs, and only then. No network."""

from __future__ import annotations

import shutil
import sys

import pytest

from loompa.engine.awake import Awake


def test_nothing_happens_when_disabled():
    awake = Awake(enabled=False)
    awake.hold(True)
    assert not awake.holding


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("caffeinate") is None, reason="macOS only"
)
def test_the_assertion_follows_the_running_stories():
    awake = Awake()
    awake.hold(True)
    first = awake._proc
    assert awake.holding
    awake.hold(True)  # held once, not once per cycle
    assert awake._proc is first
    awake.hold(False)
    assert not awake.holding and first.poll() is not None
    awake.hold(True)
    awake.release()
    assert not awake.holding


def test_a_mac_on_battery_is_told_apart(monkeypatch):
    """ADR-0036 §10: `caffeinate -s` holds nothing on battery, and SP-011's 10 hours held 7 of a
    Mac asleep on 1% battery. `loompa run` says so when it starts."""
    import subprocess
    import sys

    from loompa.comms import audit_executive_text
    from loompa.engine import awake

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(awake.shutil, "which", lambda name: "/usr/bin/pmset")
    out = {"text": "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t1%; discharging"}

    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(a, 0, stdout=out["text"], stderr="")

    monkeypatch.setattr(awake.subprocess, "run", fake_run)
    assert awake.on_battery() is True
    out["text"] = "Now drawing from 'AC Power'\n"
    assert awake.on_battery() is False
    assert audit_executive_text(awake.BATTERY_WARNING) == []
