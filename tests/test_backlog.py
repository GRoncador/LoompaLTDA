"""The Product Owner is the only writer of the backlog (ADR-0006 §5)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from loompa.agents import KaizenAgent, MasterAgent, ProductOwnerAgent
from loompa.backlog import Backlog, BacklogAuthorityError, BacklogError
from loompa.engine import Stage, StoryState
from loompa.factory import Factory
from test_engine import factory, make_ctx  # noqa: F401

SRC = Path(__file__).resolve().parents[1] / "src" / "loompa"


def test_only_the_product_owner_can_hold_the_backlog(factory: Factory):
    ctx = make_ctx(factory)
    with pytest.raises(BacklogAuthorityError):
        Backlog(ctx, owner=MasterAgent(ctx))
    with pytest.raises(BacklogAuthorityError):
        Backlog(ctx, owner=KaizenAgent(ctx))
    assert Backlog(ctx, owner=ProductOwnerAgent(ctx)) is not None
    ctx.close()


def test_add_item_creates_a_card_and_refuses_duplicates(factory: Factory):
    ctx = make_ctx(factory)
    po = ProductOwnerAgent(ctx)
    first = po.add_item("Exportar relatório em CSV", "todas as colunas", priority=200)
    assert first.created and first.story_id == "S-001"
    row = ctx.store.get_story("S-001")
    assert row["stage"] == Stage.BACKLOG and row["priority"] == 200 and row["origin"] == "founder"
    assert StoryState.from_row(row).description == "todas as colunas"
    # same title, different case/accents/punctuation: same card
    again = po.add_item("exportar relatorio em csv!")
    assert not again.created and again.duplicate_of == "S-001"
    assert len(ctx.store.list_stories(ctx.slug)) == 1
    # a finished card does not block a new one with the same title
    ctx.store.update_story("S-001", stage=Stage.DONE.value)
    assert po.add_item("Exportar relatório em CSV").created
    with pytest.raises(BacklogError):
        po.add_item("   ")
    ctx.close()


def test_kaizen_findings_about_the_same_file_are_one_card(factory: Factory):
    """`contas` S-005: one leftover `debug.txt` became four cards, worded by the Worker, the
    Kaizen loop and two Inspector findings. Same file, open Kaizen card: same finding."""
    ctx = make_ctx(factory)
    po = ProductOwnerAgent(ctx)
    first = po.add_item(
        "[Débito técnico] Remover debug.txt — resíduo fora do escopo do plan.md de S-005",
        origin="kaizen",
    )
    for title in (
        "[Débito técnico] Resíduo debug.txt na raiz do repositório",
        "[Oportunidade] SEC-1: O arquivo debug.txt contém caminhos absolutos do sistema",
    ):
        again = po.add_item(title, origin="kaizen")
        assert not again.created and again.duplicate_of == first.story_id
    # the spec artifacts every story has do not make two findings the same
    assert po.add_item("[Oportunidade] TEST-1: evidências no plan.md", origin="kaizen").created
    # a founder's card is never swallowed by a finding about the same file
    assert po.add_item("Documentar o debug.txt", origin="founder").created
    # a different file is a different finding
    assert po.add_item("[Débito técnico] Remover notes.txt", origin="kaizen").created
    ctx.close()


def test_priority_is_clamped_and_status_is_limited_to_the_backlog(factory: Factory):
    ctx = make_ctx(factory)
    po = ProductOwnerAgent(ctx)
    sid = po.add_item("Login", priority=5000).story_id
    assert ctx.store.get_story(sid)["priority"] == 999
    po.set_priority(sid, 0)
    assert ctx.store.get_story(sid)["priority"] == 1
    with pytest.raises(BacklogError):  # pipeline stages belong to the engine
        po.set_status(sid, Stage.DEV)
    po.set_status(sid, Stage.CANCELLED)
    assert ctx.store.get_story(sid)["stage"] == Stage.CANCELLED
    with pytest.raises(BacklogError):  # already closed
        po.set_status(sid, Stage.BACKLOG)
    with pytest.raises(BacklogError):
        po.set_priority("S-404", 100)
    ctx.close()


def test_admit_moves_a_waiting_card_into_the_pipeline_once(factory: Factory):
    ctx = make_ctx(factory)
    po = ProductOwnerAgent(ctx)
    sid = po.add_item("Login").story_id
    assert po.admit(sid) is True
    row = ctx.store.get_story(sid)
    assert row["stage"] == Stage.SPEC and row["state"]["phase"] == "intake"
    assert po.admit(sid) is False  # not in the backlog any more
    assert ctx.store.checkpoints(sid)[-1]["node"] == "admit"
    ctx.close()


# ---------------------------------------------------------------- authority in code


def _calls(name: str) -> dict[str, list[ast.Call]]:
    """Every call to a method/function called `name`, by source file (relative to src/loompa)."""
    found: dict[str, list[ast.Call]] = {}
    for path in SRC.rglob("*.py"):
        if "static" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                fn = node.func
                if (isinstance(fn, ast.Attribute) and fn.attr == name) or (
                    isinstance(fn, ast.Name) and fn.id == name
                ):
                    found.setdefault(str(path.relative_to(SRC)), []).append(node)
    return found


def test_no_module_but_the_backlog_creates_or_ranks_cards():
    assert set(_calls("upsert_story")) == {"backlog.py"}
    assert set(_calls("next_story_id")) == {"backlog.py"}
    ranking = {
        rel
        for rel, calls in _calls("update_story").items()
        if any(kw.arg == "priority" for c in calls for kw in c.keywords)
    }
    assert ranking == {"backlog.py"}


def test_only_the_product_owner_builds_a_backlog():
    assert set(_calls("Backlog")) == {"agents/product_owner.py"}
