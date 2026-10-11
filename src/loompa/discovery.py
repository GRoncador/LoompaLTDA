"""Discovery inside the opening brainstorm of a commercial product (plan 16.2, ADR-0041).

The founder asks for it in the brainstorm (`/descoberta`, or the panel's button). Two steps:

1. the Analyst is consulted on competitors and alternatives, with the brainstorm's citation
   filter: a URL survives only if a web tool returned it (ADR-0020);
2. the product manager writes a one-page Lean Canvas, the competitors with their sources, and
   hypotheses "acreditamos que X; saberemos pela métrica Y", inventing no number.

The result goes to the founder as a decision. Only with "gravar" do the documents land in
`.loompa/company/` (13.2): the Lean Canvas in `business.md` when that file is still the template,
`competitors.md` and `hypotheses.md` when they are. A document the founder already wrote is
never overwritten; the message says which were kept.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from loompa.comms import FounderMessage, MessageKind, Option
from loompa.company import BY_KIND, company_dir, ensure_templates, filled
from loompa.factory_kind import profile
from loompa.lifecycle import DISCOVERY_REF

if TYPE_CHECKING:
    from loompa.agents.conversation import ConversationBoard
    from loompa.conversations import Conversation
    from loompa.engine.context import EngineContext

COMPETITORS_QUESTION = (
    "Quem são os principais concorrentes e alternativas para este produto (inclusive o que as "
    "pessoas usam hoje no lugar dele), o que cada um faz e onde este produto pode ser diferente? "
    "Cite as fontes."
)


class DiscoveryError(ValueError):
    pass


async def run_discovery(
    ctx: EngineContext, board: ConversationBoard, conv: Conversation
) -> FounderMessage:
    from loompa.agents.brainstorm import consult, opinion_text, render_brainstorm
    from loompa.agents.product_manager import ProductManagerAgent

    if not profile(ctx.config).market_discovery:
        raise DiscoveryError("a descoberta de mercado é para produtos comerciais (Configurações)")
    if not conv.draft.direction and not conv.draft.items:
        raise DiscoveryError("conte a ideia primeiro: a descoberta parte da visão do produto")
    opinion = await consult(ctx, board, conv, "analyst", COMPETITORS_QUESTION, asked_by="founder")
    competitors = "" if opinion.failed else opinion_text(opinion)
    drafted = await ProductManagerAgent(ctx).discovery(render_brainstorm(conv.draft), competitors)
    hypotheses = "\n".join(f"- {h}" for h in drafted["hypotheses"])
    docs = {
        "business": f"# Modelo de negócio\n\n{drafted['canvas']}\n",
        "competitors": f"# Concorrentes e alternativas\n\n{drafted['competitors']}\n",
        "hypotheses": f"# Hipóteses\n\n{hypotheses}\n",
    }
    ensure_templates(ctx.factory.paths.loompa)
    kept = [BY_KIND[k].file for k in docs if filled(ctx.factory.paths.loompa, k)]
    msg = FounderMessage(
        factory=ctx.slug,
        kind=MessageKind.DECISION,
        sender="Product Manager Loompa",
        title="Descoberta do produto: Lean Canvas, concorrentes e hipóteses",
        context=(
            drafted["canvas"][:2500]
            + "\n\nConcorrentes:\n"
            + drafted["competitors"][:1500]
            + "\n\nHipóteses:\n"
            + hypotheses[:1200]
            + (f"\n\nVocê já escreveu {', '.join(kept)}: esses ficam como estão." if kept else "")
        ),
        impact="Gravados, esses documentos passam a orientar o Product Owner, o Master e o PM.",
        options=[
            Option(key="save", label="Gravar nos documentos da empresa", recommended=True),
            Option(key="discard", label="Descartar"),
        ],
        technical_ref=DISCOVERY_REF,
    )
    msg = ctx.inbox(msg)
    ctx.store.set(f"{DISCOVERY_REF}:{msg.id}", json.dumps(docs, ensure_ascii=False))
    ctx.emit("lifecycle.discovery", conversation_id=conv.id, message_id=msg.id)
    return msg


def save_discovery(ctx: EngineContext, message_id: str) -> list[str]:
    docs: dict[str, Any] = json.loads(ctx.store.get(f"{DISCOVERY_REF}:{message_id}") or "{}")
    folder = company_dir(ctx.factory.paths.loompa)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for kind, text in docs.items():
        if kind not in BY_KIND or filled(ctx.factory.paths.loompa, kind):
            continue
        (folder / BY_KIND[kind].file).write_text(text, encoding="utf-8")
        written.append(BY_KIND[kind].file)
    ctx.emit("lifecycle.discovery_saved", files=written)
    return written
