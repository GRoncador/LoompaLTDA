"""ADR-0038: the Product Owner grooms the backlog at every Sprint Meeting that plans a sprint.

tamagotchi, 10/10: the founder had to ask by name to retire S-098, a finding that asked for the
opposite of a newer card. Every waiting card is judged (keep, retire, merge, split); the factory's
own cards are checked by the Master and land in the draft, the founder's wait for a yes, and
nothing leaves the backlog until the meeting is saved."""

from __future__ import annotations

import json
from typing import Any

from loompa.agents import Conversations, ProductOwnerAgent
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.agents.product_owner import groom_proposals
from loompa.conversations import ConversationBoard, ConversationKind, Draft, apply_ops
from loompa.factory import Factory
from loompa.llm import Message
from test_engine import factory, make_ctx  # noqa: F401


def _cards(ctx):
    return ConversationBoard(ctx.store, ctx.slug).cards()


def test_the_verdicts_are_checked_in_code(factory: Factory):  # noqa: F811
    ctx = make_ctx(factory)
    po = ProductOwnerAgent(ctx)
    a, b, c, d = (po.add_item(t).story_id for t in ("Alfa", "Beta", "Gama", "Delta"))
    cards = _cards(ctx)
    data = {
        "cards": [
            {"ref": a, "verdict": "keep", "reason": "x"},
            {"ref": b, "verdict": "merge", "into": c, "reason": "mesma coisa"},
            {"ref": c, "verdict": "merge", "into": d, "reason": "também"},  # C is leaving: B's goes
            {"ref": d, "verdict": "split", "parts": [{"title": "só uma"}], "reason": "x"},
            {"ref": "S-999", "verdict": "retire", "reason": "não foi dado"},
            {"ref": a, "verdict": "retire", "reason": "repetido"},
        ]
    }
    props = groom_proposals(data, [a, b, c, d], cards)
    # the first verdict on a card stands (A is kept), and B's merge goes with C leaving
    assert [(g.ref, g.action, g.into) for g in props] == [(c, "merge", d)]


def test_a_proposal_lands_in_the_draft_and_leaves_it_again(factory: Factory):  # noqa: F811
    from loompa.conversations import GroomProposal

    ctx = make_ctx(factory)
    po = ProductOwnerAgent(ctx)
    a, b, c = (po.add_item(t).story_id for t in ("Alfa", "Beta", "Gama"))
    draft = Draft(
        grooming=[
            GroomProposal(ref=a, action="merge", into=b, reason="pede o mesmo", founder=True),
            GroomProposal(
                ref=c,
                action="split",
                parts=[{"title": "Parte um"}, {"title": "Parte dois"}],
                founder=True,
            ),
        ]
    )
    cards = _cards(ctx)
    report = apply_ops(draft, [{"op": "groom", "ref": a, "accept": True}], cards)
    assert a in draft.retire and draft.find(b).amend.startswith(f"Juntado de {a}")
    apply_ops(draft, [{"op": "groom", "ref": c, "accept": "sim"}], cards)
    assert [i.title for i in draft.items if not i.story_id] == ["Parte um", "Parte dois"]
    apply_ops(
        draft,
        [{"op": "groom", "ref": c, "accept": False}, {"op": "groom", "ref": a, "accept": False}],
        cards,
    )
    assert draft.retire == {} and draft.items == []
    assert report.changes == [f"{a} junta-se a {b}"]
    # a card another one depends on is not touched
    po.set_dependencies(b, [a])
    draft.grooming[0].accepted = None
    report = apply_ops(draft, [{"op": "groom", "ref": a, "accept": True}], _cards(ctx))
    assert a not in draft.retire and "depende" in report.ignored[0]


def _meeting_script(verdicts: list[dict[str, Any]], master: dict[str, bool], seen: dict):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        if role == "product_owner" and "Groom the backlog" in messages[0].content:
            seen.setdefault("groom", []).append(messages[1].content)
            return json.dumps({"reply": "Revisei o backlog.", "cards": verdicts})
        if role == "master" and "second pair of eyes" in messages[0].content:
            seen.setdefault("confirm", []).append(messages[-1].content)
            return json.dumps(
                {
                    "decisions": [
                        {"ref": r, "confirm": ok, "reason": "ainda pode valer" if not ok else "ok"}
                        for r, ok in master.items()
                    ]
                }
            )
        if role == "product_owner" and "rank the backlog" in messages[0].content:
            seen.setdefault("rerank", []).append(messages[-1].content)
        return dry_run_script(model, messages, tools)

    return script


async def test_the_meeting_grooms_the_backlog_before_the_proposal(factory: Factory):  # noqa: F811
    seen: dict[str, list[str]] = {}
    ctx = make_ctx(factory)
    po = ProductOwnerAgent(ctx)
    k1 = po.add_item("[Oportunidade] criar os documentos de decisão", origin="kaizen").story_id
    k2 = po.add_item("[Bug] o teste do cache lê um arquivo velho", origin="kaizen").story_id
    f1 = po.add_item("Trocar o teste do cache por um de comportamento").story_id
    f2 = po.add_item("Exportar o placar").story_id
    f3 = po.add_item("Álbum e carcaças da edição colecionador").story_id
    verdicts = [
        {"ref": k1, "verdict": "retire", "reason": "Pede o oposto de um card mais novo."},
        {"ref": k2, "verdict": "merge", "into": f1, "reason": "O card do fundador já cobre."},
        {"ref": f2, "verdict": "retire", "reason": "O placar já é exportado."},
        {
            "ref": f3,
            "verdict": "split",
            "parts": [
                {"title": "Adicionar o álbum", "description": "álbum"},
                {"title": "Adicionar as carcaças", "description": "carcaças"},
            ],
            "reason": "São duas entregas.",
        },
    ]
    ctx = make_ctx(factory, _meeting_script(verdicts, {k1: True, k2: False}, seen))
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    await chats.say(conv.id, "Quero o placar exportado neste sprint")
    await chats.propose(conv.id)
    conv = chats.board.require(conv.id)
    assert len(seen["groom"]) == 1 and k1 in seen["confirm"][0] and f2 not in seen["confirm"][0]
    groom_turn = next(t for t in conv.turns if t.name == "Product Owner Loompa")
    assert any("confirmado pelo Master" in c and k1 in c for c in groom_turn.changes)
    assert any(c.startswith(f"Para você confirmar: {k2}") for c in groom_turn.changes)
    assert conv.draft.retire == {k1: "Pede o oposto de um card mais novo."}  # only the confirmed
    waiting = {g.ref: g for g in conv.draft.grooming if g.accepted is None}
    assert set(waiting) == {k2, f2, f3} and waiting[k2].held
    # the founder accepts the split and the merge, keeps the founder's card
    chats.edit(
        conv.id,
        [
            {"op": "groom", "ref": f3, "accept": True},
            {"op": "groom", "ref": k2, "accept": True},
            {"op": "groom", "ref": f2, "accept": False},
        ],
    )
    await chats.propose(conv.id)  # a second proposal does not groom again
    assert len(seen["groom"]) == 1
    await chats.commit(conv.id)
    rows = {r["id"]: r for r in ctx.store.list_stories(ctx.slug)}
    outcome = {sid: rows[sid]["state"]["extra"].get("outcome", {}).get("kind") for sid in rows}
    assert rows[k1]["stage"] == "CANCELLED" and outcome[k1] == "retired"
    assert outcome[k2] == "merged" and rows[k2]["state"]["extra"]["outcome"]["into"] == f1
    assert outcome[f3] == "split" and rows[f2]["stage"] == "BACKLOG"
    titles = {r["title"] for r in rows.values() if r["stage"] == "BACKLOG"}
    assert {"Adicionar o álbum", "Adicionar as carcaças"} <= titles
    amended = rows[f1]["state"]["extra"].get("amended") or []
    assert amended and k2 in amended[0]["text"]
    assert "Where the product is going" in seen["rerank"][-1]
    await ctx.aclose()


async def test_a_meeting_about_the_running_sprint_does_not_groom(factory: Factory):  # noqa: F811
    from loompa.agents import MasterAgent
    from loompa.conversations import MeetingMode

    seen: dict[str, list[str]] = {}
    ctx = make_ctx(factory, _meeting_script([], {}, seen))
    po = ProductOwnerAgent(ctx)
    a = po.add_item("Alfa").story_id
    po.add_item("[Bug] x", origin="kaizen")
    MasterAgent(ctx).start_sprint([a])
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    chats.choose(conv.id, MeetingMode.CURRENT)
    chats.edit(conv.id, [{"op": "update", "ref": "S-002", "in_sprint": True}])
    await chats.propose(conv.id)
    assert "groom" not in seen
    await ctx.aclose()


def test_keeping_a_card_the_master_confirmed_undoes_the_whole_change(factory: Factory):  # noqa: F811
    """The draft's "manter" (unretire) on a split card takes its new parts out too."""
    from loompa.conversations import GroomProposal

    ctx = make_ctx(factory)
    a = ProductOwnerAgent(ctx).add_item("[Bug] duas coisas", origin="kaizen").story_id
    draft = Draft(
        grooming=[GroomProposal(ref=a, action="split", parts=[{"title": "Um"}, {"title": "Dois"}])]
    )
    cards = _cards(ctx)
    apply_ops(draft, [{"op": "groom", "ref": a, "accept": True}], cards)
    assert len(draft.items) == 2 and a in draft.retire
    apply_ops(draft, [{"op": "unretire", "ref": a}], cards)
    assert draft.items == [] and draft.retire == {} and draft.grooming[0].accepted is False
