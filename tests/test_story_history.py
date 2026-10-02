"""The history tab (plan 11.3): one sentence per step, written in code from the checkpoints."""

from __future__ import annotations

from itertools import count
from pathlib import Path

import pytest

from loompa import store as store_mod
from loompa.comms import FounderMessage, MessageKind, Option, audit_executive_text
from loompa.store import Store
from loompa.story_history import story_history


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """One millisecond per write, as in the engine, where a node's events precede its checkpoint."""
    tick = count()
    monkeypatch.setattr(store_mod, "now_iso", lambda: f"2026-10-01T10:00:00.{next(tick):06d}+00:00")


def state(**kw) -> dict:
    return {"tasks_total": 0, "tasks_done": [], "commits": [], "acceptance": [], **kw}


def test_each_step_says_what_came_of_it(tmp_path: Path):
    """Built from cases seen in contas and tamagotchi-retro: a review sent back with its gaps, a
    fix pass after the Inspector, retries after the Mac slept, the option the founder picked."""
    st = Store(tmp_path / "state.db")
    s = "S-006"
    planned = state(tasks_total=6, acceptance=["a"] * 8)
    built = {**planned, "tasks_done": [1, 2, 3, 4, 5, 6], "commits": ["a"] * 6}
    question = FounderMessage(
        factory="f",
        story_id=s,
        kind=MessageKind.BLOCKED,
        title="t",
        context="c",
        options=[Option(key="opt1", label="Aceitar assim mesmo")],
    )
    st.put_message(question)
    steps = [
        ("admit", "SPEC", state(), []),
        ("node_intake", "SPEC", state(kind="bugfix", route=["intake", "spec", "plan", "dev"]), []),
        ("node_spec", "SPEC", state(acceptance=["a"] * 8), []),
        (
            "node_spec_review",
            "SPEC",
            state(spec_review_rounds=1),
            [("spec.reviewed", {"approved": False, "missing": 2})],
        ),
        ("node_plan", "DEV", planned, []),
        ("node_dev", "AWAITING_FOUNDER", planned, []),
        (
            "founder_answer",
            "DEV",
            planned,
            [("inbox.answered", {"message_id": question.id, "option": "opt1"})],
        ),
        ("node_dev", "TEST", built, []),
        (
            "node_test",
            "DEV",
            {**built, "qa_verdict": "FAIL"},
            [("inspector.verdict", {"verdict": "FAIL", "findings": 3})],
        ),
        (
            "node_dev",
            "DEV",
            {**built, "qa_verdict": "FAIL"},
            [
                (
                    "story.retry",
                    {"cause": "houve instabilidade de rede ao falar com o serviço de IA"},
                )
            ],
        ),
        (
            "node_dev",
            "AWAITING_FOUNDER",
            {**built, "qa_verdict": "FAIL"},
            [("story.blocked", {"reason": "persistent_failure"})],
        ),
        (
            "founder_answer",
            "DEV",
            {**built, "qa_verdict": "FAIL"},
            [("story.resumed", {"answer": "retry"})],
        ),
        ("node_dev", "TEST", {**built, "qa_verdict": "FAIL", "commits": ["a"] * 7}, []),
        (
            "node_test",
            "REVIEW",
            {**built, "qa_verdict": "PASS"},
            [("inspector.verdict", {"verdict": "PASS", "findings": 0})],
        ),
        (
            "founder_answer",
            "DONE",
            built,
            [("story.resumed", {"answer": "approve"}), ("story.merged", {"sha": "abc"})],
        ),
    ]
    for node, stage, data, evs in steps:
        for type_, payload in evs:
            st.emit("f", type_, story_id=s, **payload)
        st.checkpoint(s, node, stage, data)
    said = [(h["role"], h["summary"]) for h in story_history(st, s)]
    assert said[1] == ("master", "Classificada como correção: especificação → plano → código.")
    assert said[2][1] == "Spec escrita com 8 critérios de aceite."
    assert said[3] == (
        "product_owner",
        "Spec devolvida para ajuste (2 critérios faltando), rodada 1.",
    )
    assert said[4][1] == "Plano com 6 tarefas."
    assert said[6] == ("founder", "Você escolheu “Aceitar assim mesmo”.")
    assert said[7][1] == "6 de 6 tarefas concluídas (+6), 6 versões salvas, foi para os testes."
    assert said[8] == ("inspector", "Reprovada com 3 problemas: volta para o código.")
    assert said[9][1].startswith("Parou porque houve instabilidade de rede")
    assert said[10][1] == "Parou depois de várias tentativas sem sucesso e pediu a sua orientação."
    assert (
        said[12][1]
        == "Correções do que foi apontado na revisão, 1 versão salva, foi para os testes."
    )
    assert said[13][1] == "Testes e revisão do código aprovados."
    assert said[14][1] == "Você aprovou a entrega; a história foi integrada à versão principal."
    assert all(not audit_executive_text(text) for _, text in said)


def test_a_change_request_quotes_the_founder_without_the_option_label(tmp_path: Path):
    st = Store(tmp_path / "state.db")
    note = "Pedir ajustes / Quase lá. Remova o arquivo de sobra da raiz " + "e ajuste o teste " * 20
    st.emit("f", "story.resumed", story_id="S-1", answer="changes")
    st.checkpoint("S-1", "founder_answer", "DEV", state(founder_notes=[note]))
    (h,) = story_history(st, "S-1")
    assert h["summary"].startswith("Você pediu ajustes: “Quase lá. Remova")
    assert "Pedir ajustes" not in h["summary"] and h["summary"].endswith("…”.")
