"""Ops: deterministic first responder and SRE when a story crashes.

SRE / Factory Backend Service ($0,00 IA):
Ops operates as a 100% deterministic resilience engine. It handles transient
failures (rate limit / cooldown, network, 5xx, locked database) with exponential
backoff and self-healing. It consumes zero LLM tokens.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from loompa.agents.base import TIER_LIFT_KEY, LoompaAgent
from loompa.comms import FounderMessage, MessageKind
from loompa.engine.state import StoryState
from loompa.llm import LLMError

INCIDENT_KEY = "ops_incident"


class StoryStalled(RuntimeError):
    """A running story emitted nothing for `schedule.stall_minutes`: the watchdog cancelled it
    and the Ops Loompa treats it like a crash (restart from the last checkpoint, then ask)."""

    def __init__(self, story_id: str, minutes: float, last: str = ""):
        super().__init__(
            f"{story_id} ficou {minutes:.0f} min sem nenhum evento"
            + (f" (último: {last})" if last else "")
        )
        self.minutes = minutes


# A failure that is not an instability is tried again without waiting: time will not change it.
RETRY_AT_ONCE_S = 1.0
# Right after the machine wakes the network is often still down (a dark wake may last seconds):
# a network failure then is the sleep, not the story. tamagotchi-retro (2026-10-01) spent its
# three recoveries on three wakes in a row and went to the founder as a persistent failure.
WAKE_GRACE_S = 300.0
RETRY_AFTER_WAKE_S = 30.0


@dataclass
class Triage:
    transient: bool
    cause: str  # plain pt-BR sentence fragment, e.g. "o serviço de IA atingiu o limite de uso"
    # A setup problem (no API key): trying again cannot fix it, the founder must.
    setup: bool = False
    network: bool = False  # the connection itself failed (no answer at all)


def triage(exc: BaseException) -> Triage:
    text = str(exc).lower()
    if isinstance(exc, StoryStalled):
        return Triage(
            True, "uma etapa ficou parada, sem nenhum sinal de progresso, por muito tempo"
        )
    if isinstance(exc, LLMError):
        if "chave de api" in text or "não configurado" in text:
            return Triage(False, "falta configurar o acesso ao serviço de IA", setup=True)
        if "em loop" in text:
            return Triage(
                False, "a IA entrou em um raciocínio repetitivo, sem chegar a uma resposta"
            )
        if "resposta cortada" in text:
            return Triage(
                False,
                "a resposta da IA passou do tamanho máximo configurado, mesmo depois de eu ampliá-lo",
            )
        if not exc.retryable:
            return Triage(False, "o serviço de IA devolveu uma resposta inesperada")
        if any(k in text for k in ("cooldown", "cota", "limite", "429")):
            return Triage(True, "o serviço de IA atingiu o limite de uso por alguns minutos")
        if "rede" in text:
            return Triage(
                True, "houve instabilidade de rede ao falar com o serviço de IA", network=True
            )
        return Triage(True, "o serviço de IA ficou instável por alguns minutos")
    if isinstance(exc, httpx.HTTPError | ConnectionError | TimeoutError):
        return Triage(True, "houve instabilidade de rede", network=True)
    if "locked" in text or "busy" in text:
        return Triage(True, "o banco de dados local estava ocupado")
    return Triage(False, "aconteceu um problema inesperado nesta etapa")


class OpsAgent(LoompaAgent):
    role = "ops"
    display = "Ops Loompa"

    @property
    def max_recoveries(self) -> int:
        return self.ctx.config.schedule.ops_max_recoveries

    def wait_for(self, recoveries: int) -> float:
        base = self.ctx.config.schedule.ops_retry_base_s
        return min(base * (2 ** max(recoveries - 1, 0)), 600.0)

    def on_failure(self, state: StoryState, node: str, exc: BaseException) -> float | None:
        """Decide what to do with a crash. Returns seconds to wait before re-running the node,
        or None when the story must be escalated to the Founder (see `executive_reason`).

        Every failure but a setup problem gets `ops_max_recoveries` (3) more tries before the
        founder hears of it (ADR-0016 §5): the first on the tier the step used, the next ones on
        the tier above. An instability waits a growing backoff first; anything else (an answer
        cut even at full room, an unexpected answer or error) is tried again at once."""
        t = triage(exc)
        incident = dict(state.extra.get(INCIDENT_KEY) or {})
        woke = getattr(self.ctx, "woke_at", None)
        if t.network and woke is not None and time.monotonic() - woke < WAKE_GRACE_S:
            self.ctx.emit(
                "story.retry",
                story_id=state.story_id,
                node=node,
                wait_s=RETRY_AFTER_WAKE_S,
                recoveries=int(incident.get("recoveries", 0)),
                cause=t.cause,
                after_sleep=True,
            )
            return RETRY_AFTER_WAKE_S  # not counted: the sleep took the network, not the story
        recoveries = int(incident.get("recoveries", 0)) + 1
        incident.update(node=node, cause=t.cause, recoveries=recoveries, transient=t.transient)
        state.extra[INCIDENT_KEY] = incident
        if t.setup or recoveries > self.max_recoveries:
            state.extra.pop(TIER_LIFT_KEY, None)
            self.set_state("idle", state)
            return None
        if recoveries >= 2:
            state.extra[TIER_LIFT_KEY] = True
        wait = self.wait_for(recoveries) if t.transient else RETRY_AT_ONCE_S
        self.set_state(
            "waiting",
            state,
            detail=f"{t.cause}; nova tentativa em {wait:.0f}s ({recoveries}/{self.max_recoveries})",
        )
        self.ctx.emit(
            "story.retry",
            story_id=state.story_id,
            node=node,
            wait_s=wait,
            recoveries=recoveries,
            cause=t.cause,
            lifted=bool(state.extra.get(TIER_LIFT_KEY)) or None,
        )
        return wait

    def executive_reason(self, state: StoryState) -> str:
        """Plain-language explanation for the BLOCKED inbox message."""
        incident = state.extra.get(INCIDENT_KEY) or {}
        cause = str(incident.get("cause") or "aconteceu um problema inesperado nesta etapa")
        tried = int(incident.get("recoveries", 1)) - 1
        if tried > 0:
            stronger = f", {tried - 1} delas com um modelo de IA mais forte," if tried >= 2 else ""
            return (
                f"{cause[0].upper()}{cause[1:]}. O Ops Loompa tentou retomar sozinho {tried} vez(es)"
                f"{stronger} sem sucesso, e preferiu pedir sua orientação em vez de insistir."
            )
        return f"{cause[0].upper()}{cause[1:]}. O Ops Loompa não conseguiu resolver sozinho e pediu sua orientação."

    def on_success(self, state: StoryState) -> None:
        """The node ran fine after a retry: close the incident and leave one calm note."""
        state.extra.pop(TIER_LIFT_KEY, None)
        incident = state.extra.pop(INCIDENT_KEY, None)
        if not incident or not incident.get("transient"):
            return
        cause = str(incident.get("cause") or "houve uma instabilidade temporária")
        n = int(incident.get("recoveries", 1))
        msg = FounderMessage(
            factory=self.ctx.slug,
            story_id=state.story_id,
            kind=MessageKind.INFO,
            sender=self.name,
            title=f"Resolvi um contratempo em “{state.title}”",
            context=f"{cause[0].upper()}{cause[1:]}. Aguardei e retomei o trabalho sozinho "
            f"({n} tentativa{'s' if n > 1 else ''}).",
            impact="Nenhuma ação necessária da sua parte; a entrega segue normalmente.",
            allow_free_text=False,
        )
        self.ctx.inbox(msg)
        self.set_state("idle", state)
        self.ctx.emit("story.recovered", story_id=state.story_id, node=incident.get("node"))
