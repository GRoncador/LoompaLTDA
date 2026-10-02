"""`loompa models sync`: propose a refreshed model list, apply it only when the founder approves
and no work is in flight (ADR-0011).

The ranking is `loompa.llm.catalog`. What lives here is the governance the founder asked for:
the proposal goes to the inbox as a decision, an approval never swaps models in the middle of a
sprint (the prompts are tuned for the model in use, so it waits for the sprint to end) and a
configuration edited since the proposal is never overwritten.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from typing import Any

import httpx

from loompa.comms import FounderAnswer, FounderMessage, MessageKind, Option
from loompa.config.schema import LoompaConfig, Price
from loompa.engine.context import EngineContext
from loompa.engine.state import TERMINAL, Stage
from loompa.llm.catalog import (
    PROVIDER,
    REASONS,
    EndpointPrices,
    Policy,
    Proposal,
    apply_proposal,
    build_proposal,
    fetch_catalog,
    fetch_endpoint_prices,
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
                f"- {m['name']}: nota {m['score']:.0f}, {_usd(m['price'])} por milhão de tokens"
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


PRICE_SEEN = "price_seen:"  # kv: the catalogue's reference price (one provider), for the event
PRICE_MEDIAN = "price_median:"  # kv: `{"median", "providers"}` last told to the founder
MODEL_GONE = "model_gone:"  # kv: `model_gone:<provider>/<model>` = already reported once
PRICE_JUMP = 0.10  # a rise under 10% is noise from rounding, not news


def _blended(price: Price) -> float:
    """USD per 1M tokens at 3 input : 1 output, the same mix the ranking prices a model at."""
    return (3 * price.input + price.output) / 4


class ModelWatch:
    """Two things the founder should hear about between catalogue refreshes (ADR-0011 §5).

    A model that got more expensive: caught when the catalogue is read, by comparing the median
    price of the providers that serve it (with tools) against the median seen last time. Not the
    catalogue's own price: that is one provider among many, and OpenRouter changes which, so every
    earlier "X% mais caro" note was a switch of reference provider, not a price change (Fase 8.1).
    A switch like that is only an event. And a model the provider says it does not have: caught on
    the call itself, because a name that is wrong today was right yesterday.

    Both are told once per change. There is no schedule behind either: the founder refreshes the
    catalogue when they want to, and a bad id announces itself the first time it is used."""

    def __init__(self, ctx: EngineContext):
        self.ctx = ctx

    # ------------------------------------------------------------------ prices
    def check_prices(
        self,
        models: list[Any],
        *,
        fetch: Callable[[list[str]], dict[str, EndpointPrices]] | None = None,
    ) -> list[str]:
        """Compare a freshly read catalogue against the prices seen before. Returns the models a
        note went out for, so a caller can say what happened."""
        in_use = {
            c.model
            for cands in self.ctx.config.models.matrix.values()
            for cs in cands.values()
            for c in cs
            if c.provider == PROVIDER
        }
        # an alias has no endpoints of its own: the model it points to today has them
        wanted = {
            m.id: (m.target_id if m.alias and m.target_id else m.id)
            for m in models
            if m.id in in_use and (m.blended or 0) > 0
        }
        if not wanted:
            return []
        fetch = fetch or (lambda ids: fetch_endpoint_prices(self.ctx.config, ids))
        prices = fetch(sorted(set(wanted.values())))
        told: list[str] = []
        for m in models:
            if m.id not in wanted:
                continue
            self._reference(m.id, float(m.blended))
            now = prices.get(wanted[m.id])
            if now is None or not now.median:
                continue
            before = self._remembered(m.id)
            self.ctx.store.set(
                PRICE_MEDIAN + m.id, json.dumps({"median": now.median, "providers": now.providers})
            )
            if before is None:
                continue  # the first sight is the baseline, not news
            if now.median <= before["median"] * (1 + PRICE_JUMP):
                continue
            self._tell_price(m.id, m.label or m.id, before, now)
            told.append(m.id)
        return told

    def _reference(self, model_id: str, price: float) -> None:
        """The catalogue's reference provider moved: said in the event log, never in the inbox."""
        raw = self.ctx.store.get(PRICE_SEEN + model_id)
        self.ctx.store.set(PRICE_SEEN + model_id, f"{price:.6f}")
        try:
            before = float(raw) if raw else None
        except ValueError:
            before = None
        if before and abs(price - before) > before * PRICE_JUMP:
            self.ctx.emit("models.price.reference_moved", model=model_id, before=before, now=price)

    def _remembered(self, model_id: str) -> dict[str, Any] | None:
        raw = self.ctx.store.get(PRICE_MEDIAN + model_id)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if float(data.get("median") or 0) > 0 else None
        except (ValueError, TypeError, AttributeError):
            return None

    def _tell_price(
        self, model_id: str, label: str, before: dict[str, Any], now: EndpointPrices
    ) -> None:
        was, median = float(before["median"]), float(now.median or 0)
        pct = int(round((median / was - 1) * 100))
        self.ctx.emit(
            "models.price.raised",
            model=model_id,
            before=was,
            now=median,
            providers=now.providers,
            providers_before=before.get("providers"),
        )
        self.ctx.inbox(
            FounderMessage(
                factory=self.ctx.slug,
                kind=MessageKind.FINANCE,
                sender=SENDER,
                title=f"{label} ficou {pct}% mais caro",
                context=(
                    f"O preço típico do modelo {label}, que a fábrica usa, passou de {_usd(was)} "
                    f"para {_usd(median)} por milhão de tokens. É a mediana entre os "
                    f"{now.providers} provedores que o atendem pela OpenRouter"
                    + (
                        f" (antes eram {before['providers']})."
                        if before.get("providers") and before["providers"] != now.providers
                        else "."
                    )
                ),
                impact=(
                    "O controle de custos registra o valor que a OpenRouter cobra de fato em cada "
                    "chamada, então o orçamento do período já acompanha o preço novo. Se preferir "
                    "trocar de modelo, peça uma sugestão inteligente na tela de configurações."
                ),
                allow_free_text=False,
            )
        )

    # ------------------------------------------------------------- missing model
    def model_gone(self, provider: str, model: str) -> None:
        """A provider answered 404 for one of the configured ids: it left the air or is a typo."""
        key = MODEL_GONE + f"{provider}/{model}"
        if self.ctx.store.get(key):
            return
        self.ctx.store.set(key, "1")
        self.ctx.emit("models.gone", provider=provider, model=model)
        self.ctx.inbox(
            FounderMessage(
                factory=self.ctx.slug,
                kind=MessageKind.INFO,
                sender=SENDER,
                title="Um modelo configurado não existe mais",
                context=(
                    f"O provedor {provider} respondeu que não conhece o modelo {model}. "
                    "Ou ele saiu do ar, ou o nome foi digitado com algum erro. A fábrica seguiu "
                    "com o próximo modelo da lista."
                ),
                impact=(
                    "Enquanto o nome não for corrigido, esse modelo é pulado em toda chamada. "
                    "Na tela de configurações, o botão de testar conexão diz se o caminho existe."
                ),
                allow_free_text=False,
            )
        )

    def forget(self, provider: str, model: str) -> None:
        """A model that answers again may fail again later, and that is news once more."""
        self.ctx.store.set(MODEL_GONE + f"{provider}/{model}", "")
