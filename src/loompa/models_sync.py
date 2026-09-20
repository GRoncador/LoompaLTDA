"""`loompa models sync`: propose a refreshed model list, apply it only when the founder approves
and no work is in flight (ADR-0011).

The ranking is `loompa.llm.catalog`. What lives here is the governance the founder asked for:
the proposal goes to the inbox as a decision, an approval never swaps models in the middle of a
sprint (the prompts are tuned for the model in use, so it waits for the sprint to end) and a
configuration edited since the proposal is never overwritten.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from typing import Any

import httpx

from loompa.comms import FounderAnswer, FounderMessage, MessageKind, Option
from loompa.config.schema import LoompaConfig
from loompa.engine.context import EngineContext
from loompa.engine.state import TERMINAL, Stage
from loompa.llm.catalog import (
    PROVIDER,
    REASONS,
    Policy,
    Proposal,
    apply_proposal,
    build_proposal,
    fetch_catalog,
)
from loompa.sprints import SprintBoard, SprintStatus

SENDER = "Ops Loompa"
PREFIX = "models_sync:"  # kv: `models_sync:<message id>` holds the proposal that message asks about
DEFERRED = PREFIX + "deferred"  # kv: id of an approved proposal waiting for the sprint to end
ALIAS_SEEN = "alias_seen:"  # kv: `alias_seen:<alias>` = the model that last answered for it
TIER_LABEL = {
    "tier1": "Raciocínio (Master, Architect)",
    "tier2": "Execução (Worker e demais)",
    "tier3_free": "Gratuito (Tarefas Leves e Contingência)",
}


def _usd(value: float) -> str:
    return f"US$ {value:.2f}".replace(".", ",")


def proposal_to_dict(proposal: Proposal) -> dict[str, Any]:
    d = asdict(proposal)
    d["changed"] = proposal.changed
    return d


def plan(
    config: LoompaConfig,
    policy: Policy | None = None,
    *,
    client: httpx.Client | None = None,
    today: date | None = None,
    force_refresh: bool = False,
) -> Proposal:
    """Read the catalogue and work out the list this factory would use. Touches nothing."""
    pol = policy or Policy(
        tier1_ceiling=config.models.tier1_ceiling,
        tier2_floor=config.models.tier2_floor,
    )
    models = fetch_catalog(config, client=client, force_refresh=force_refresh)
    return build_proposal(config, models, pol, today or date.today())


class ModelSync:
    def __init__(self, ctx: EngineContext):
        self.ctx = ctx

    # ---------------------------------------------------------------- propose
    def propose(self, proposal: Proposal) -> FounderMessage | None:
        """Put `proposal` in the inbox. None when there is nothing to change. An earlier proposal
        still waiting is withdrawn: the newest one is the only one the founder should answer."""
        if not proposal.changed:
            return None
        for old in self.ctx.store.list_messages(self.ctx.slug, status="pending"):
            if self.ctx.store.get(PREFIX + old.id):
                self.ctx.store.archive_message(old.id)
        msg = self.ctx.inbox(self._message(proposal))
        self.ctx.store.set(PREFIX + msg.id, json.dumps(asdict(proposal)))
        return msg

    def _message(self, p: Proposal) -> FounderMessage:
        lines = [
            f"Comparei o catálogo da OpenRouter: {p.eligible} de {p.considered} modelos "
            "passaram (usam ferramentas, têm nota em benchmarks e o raciocínio pode ser limitado)."
        ]
        for tier, picks in p.summary.items():
            lines.append(f"\n{TIER_LABEL.get(tier, tier)}:")
            lines += [
                f"- {m['name']}: nota {m['quality']:.0f}, {_usd(m['price'])} por milhão de tokens"
                for m in picks
            ]
        if p.added:
            lines.append(f"\nEntram: {', '.join(p.added)}.")
        if p.removed:
            lines.append(f"Saem: {', '.join(p.removed)}.")
        if p.repriced and not p.added:
            lines.append(f"\nPreços atualizados: {', '.join(p.repriced)}.")
        if p.gone:
            lines.append(f"Já saíram do catálogo e serão retirados: {', '.join(p.gone)}.")
        if p.expiring:
            lines.append(f"Serão desativados em breve: {', '.join(p.expiring)}.")
        return FounderMessage(
            factory=self.ctx.slug,
            kind=MessageKind.DECISION,
            sender=SENDER,
            title="Nova lista de modelos de IA para aprovar",
            context="\n".join(lines),
            impact=(
                "Trocar de modelo muda o jeito como os agentes respondem, por isso a troca só "
                "vale quando não há trabalho em andamento; se houver, ela espera o fim do sprint. "
                "Os preços novos já entram no controle de custos."
            ),
            options=[
                Option(key="approve", label="Aprovar a nova lista", recommended=True),
                Option(key="reject", label="Manter os modelos atuais"),
            ],
            allow_free_text=False,
        )

    # ---------------------------------------------------------------- approve
    def busy(self) -> bool:
        """Work is in flight: a sprint is running or a story is somewhere between backlog and done
        (a story waiting on the founder counts, it resumes on whatever model is configured)."""
        board = SprintBoard(self.ctx.store, self.ctx.slug)
        if board.sprints(SprintStatus.RUNNING):
            return True
        return any(
            s["stage"] not in TERMINAL and s["stage"] != Stage.BACKLOG
            for s in self.ctx.store.list_stories(self.ctx.slug)
        )

    def on_answer(self, msg: FounderMessage, answer: FounderAnswer) -> None:
        """Called for every answered inbox message; acts only on model proposals."""
        if not self.ctx.store.get(PREFIX + msg.id):
            return
        if answer.option_key != "approve":
            self.ctx.emit("models.sync.rejected", message_id=msg.id)
            return
        if self.busy():
            self.ctx.store.set(DEFERRED, msg.id)
            self._note(
                "Nova lista de modelos aprovada",
                "A troca entra em vigor assim que o sprint em andamento terminar.",
            )
            return
        self._apply(msg.id)

    def apply_deferred(self) -> None:
        """Apply an approved proposal that was waiting for the work in flight to end."""
        message_id = self.ctx.store.get(DEFERRED)
        if message_id and not self.busy():
            self._apply(message_id)

    def _apply(self, message_id: str) -> None:
        self.ctx.store.set(DEFERRED, "")
        raw = self.ctx.store.get(PREFIX + message_id)
        proposal = Proposal(**json.loads(raw)) if raw else None
        if proposal is None or not apply_proposal(self.ctx.config, proposal):
            self._note(
                "Lista de modelos não foi trocada",
                "A configuração dos modelos mudou depois da proposta, então nada foi alterado. "
                "Peça uma nova proposta para comparar de novo.",
            )
            return
        self.ctx.factory.save()
        self.ctx.emit("models.sync.applied", message_id=message_id, added=proposal.added)
        self._note(
            "Modelos de IA atualizados",
            "A nova lista já está valendo para as próximas histórias"
            + (f": entram {', '.join(proposal.added)}." if proposal.added else "."),
        )

    def _note(self, title: str, context: str) -> None:
        self.ctx.inbox(
            FounderMessage(
                factory=self.ctx.slug,
                kind=MessageKind.INFO,
                sender=SENDER,
                title=title,
                context=context,
                allow_free_text=False,
            )
        )


def excluded_report(proposal: Proposal) -> list[tuple[str, int]]:
    """Why models were left out, most common first, in words: the CLI shows it so nothing the
    filters drop is silent."""
    return [
        (REASONS.get(reason, reason), n)
        for reason, n in sorted(proposal.excluded.items(), key=lambda kv: -kv[1])
    ]


class AliasWatch:
    """Tells the founder when a `~vendor/model-latest` alias starts answering with another model.

    An alias follows the vendor's newest version, so the swap ADR-0011 §5 wants approved happens
    without one. The provider hands back the model that answered (`response.model`); the first one
    seen is remembered silently and every later change becomes one inbox note."""

    def __init__(self, ctx: EngineContext):
        self.ctx = ctx

    def observe(self, role: str, agent: str, routed: Any) -> None:
        """A call answered: `response.model` says which model is behind the alias right now."""
        alias, served = routed.candidate.model, routed.response.model
        if routed.candidate.provider == PROVIDER and alias.startswith("~") and served != alias:
            self.note(alias, served)

    def check_targets(self, targets: dict[str, str]) -> None:
        """The catalogue says which model each alias points to (`alias_target`), so a move shows
        before any call is made. Same memory as `observe`, so a move is told once."""
        for alias, target in targets.items():
            self.note(alias, target)

    def note(self, alias: str, now: str) -> None:
        if not now:  # a provider that does not say who answers
            return
        before = self.ctx.store.get(ALIAS_SEEN + alias)
        if before == now:
            return
        self.ctx.store.set(ALIAS_SEEN + alias, now)
        if before:  # the first sight is the baseline, not news
            self._tell(alias, before, now)

    def _tell(self, alias: str, before: str, now: str) -> None:
        tiers = [
            TIER_LABEL.get(tier, tier)
            for tier, cands in self.ctx.config.models.tiers.items()
            if any(c.provider == PROVIDER and c.model == alias for c in cands)
        ]
        self.ctx.emit("models.alias.moved", alias=alias, before=before, now=now)
        self.ctx.inbox(
            FounderMessage(
                factory=self.ctx.slug,
                kind=MessageKind.INFO,
                sender=SENDER,
                title="O modelo por trás de um apelido mudou",
                context=(
                    f"O apelido {alias}"
                    + (f", usado em {' e '.join(tiers)}," if tiers else "")
                    + f" agora responde com {now}; antes era {before}. "
                    "A fábrica já está usando o modelo novo."
                ),
                impact=(
                    "Cada modelo responde de um jeito e cobra um preço, e os textos dos agentes "
                    "foram ajustados para o anterior. Se algo piorar, avise. A próxima comparação "
                    "de modelos atualiza os preços."
                ),
                allow_free_text=False,
            )
        )
