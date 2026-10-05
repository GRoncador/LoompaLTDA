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
