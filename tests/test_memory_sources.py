"""Plan 13.3: sprint reports and the founder's answers become memory. No network."""

from __future__ import annotations

from loompa.comms import Decision, FounderAnswer, FounderMessage, MessageKind, Option
from loompa.engine import Scheduler
from loompa.factory import Factory
from loompa.memory.sources import answer_document
from test_engine import factory, make_ctx  # noqa: F401


def _question() -> FounderMessage:
    return FounderMessage(
        factory="demo-hq",
        kind=MessageKind.DECISION,
        title="Guardar o progresso no navegador ou num arquivo?",
        context="O jogo precisa lembrar o pet entre visitas.",
        options=[Option(key="local", label="No navegador"), Option(key="file", label="Arquivo")],
        decisions=[
            Decision(
                id="S-009", title="Teste de borda", options=[Option(key="drop", label="Descartar")]
            )
        ],
    )


def test_an_answered_question_reads_as_a_decision_with_the_reason():
    m = _question()
    assert answer_document(m) is None  # nothing decided yet
    m.answer = FounderAnswer(option_key="local", text="sem servidor", decisions={"S-009": "drop"})
    doc_id, kind, title, text = answer_document(m)
    assert (doc_id, kind) == (f"inbox/{m.id}", "decision")
    assert (
        "Escolha do Founder: No navegador" in text and "Palavras do Founder: sem servidor" in text
    )
    assert "Card S-009 (Teste de borda): Descartar" in text
    m.kind = MessageKind.INFO
    assert answer_document(m) is None


async def test_answers_and_reports_are_found_in_memory(factory: Factory):  # noqa: F811
    ctx = make_ctx(factory)
    msg = ctx.inbox(_question())
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="local", text="sem servidor"))
    hits = ctx.memory.search("onde guardar o progresso do pet", kinds=("decision",))
    assert hits and hits[0].chunk.doc_id == f"inbox/{msg.id}"  # indexed on the answer
    factory.paths.reports.mkdir(parents=True, exist_ok=True)
    (factory.paths.reports / "SP-001.md").write_text("# SP-001\n\nO juiz levou 20 minutos.")
    ctx.index_memory()
    assert ctx.memory.search("juiz lento no sprint", kinds=("report",))
    await ctx.aclose()
