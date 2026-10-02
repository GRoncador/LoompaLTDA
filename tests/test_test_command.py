"""A factory with no test command learns it from the product (tamagotchi-retro, Sprint 1)."""

from __future__ import annotations

import json
from pathlib import Path

from loompa.agents.deployer import DeployerAgent
from loompa.factory import Factory
from loompa.onboarding.greenfield import detect_test_command
from test_engine import factory, make_ctx  # noqa: F401


def test_the_test_command_comes_from_the_tests_the_repository_has(tmp_path: Path):
    assert detect_test_command(tmp_path) == ""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_simulator.py").write_text("def test_x():\n    pass\n")
    assert detect_test_command(tmp_path) == "python3 -m pytest -q"
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    pkg = {"scripts": {"test": "node --test tests/test_lcd_screen.js"}}
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    assert detect_test_command(tmp_path) == "uv run pytest -q && npm test --silent"
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": 'echo "Error: no test specified"'}})
    )
    assert detect_test_command(tmp_path) == "uv run pytest -q"


async def test_a_merge_teaches_an_empty_quality_gate_its_test_command(factory: Factory):  # noqa: F811
    factory.config.quality.test_command = ""
    factory.save()
    ctx = make_ctx(factory, dry_run=True)
    deployer = DeployerAgent(ctx)
    (ctx.root / "tests").mkdir(exist_ok=True)
    (ctx.root / "tests" / "test_a.py").write_text("def test_a():\n    pass\n")
    deployer._learn_test_command()
    assert "pytest" in ctx.config.quality.test_command
    assert "pytest" in Factory.open(ctx.root).config.quality.test_command  # saved
    ctx.config.quality.test_command = "make test"
    deployer._learn_test_command()
    assert ctx.config.quality.test_command == "make test"  # a command already set is kept
    await ctx.aclose()
