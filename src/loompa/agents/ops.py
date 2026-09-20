"""Ops: deterministic first responder and SRE when a story crashes.

SRE / Factory Backend Service ($0,00 IA):
Ops operates as a 100% deterministic resilience engine. It handles transient
failures (rate limit / cooldown, network, 5xx, locked database) with exponential
backoff and self-healing. It consumes zero LLM tokens.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from loompa.agents.base import LoompaAgent
from loompa.comms import FounderMessage, MessageKind
from loompa.engine.state import StoryState
from loompa.llm import LLMError

INCIDENT_KEY = "ops_incident"


@dataclass
class Triage:
    transient: bool
    cause: str  # plain pt-BR sentence fragment, e.g. "o serviço de IA atingiu o limite de uso"


def triage(exc: BaseException) -> Triage:
    text = str(exc).lower()
    if isinstance(exc, LLMError):
        if "chave de api" in text or "não configurado" in text:
            return Triage(False, "falta configurar o acesso ao serviço de IA")
        if not exc.retryable:
            return Triage(False, "o serviço de IA devolveu uma resposta inesperada")
        if any(k in text for k in ("cooldown", "cota", "limite", "429")):
            return Triage(True, "o serviço de IA atingiu o limite de uso por alguns minutos")
        if "rede" in text:
            return Triage(True, "houve instabilidade de rede ao falar com o serviço de IA")
        return Triage(True, "o serviço de IA ficou instável por alguns minutos")
    if isinstance(exc, httpx.HTTPError | ConnectionError | TimeoutError):
        return Triage(True, "houve instabilidade de rede")
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
        or None when the story must be escalated to the Founder (see `executive_reason`)."""
        t = triage(exc)
        incident = dict(state.extra.get(INCIDENT_KEY) or {})
        recoveries = int(incident.get("recoveries", 0)) + 1
        incident.update(node=node, cause=t.cause, recoveries=recoveries, transient=t.transient)
        state.extra[INCIDENT_KEY] = incident
        if not t.transient or recoveries > self.max_recoveries:
            self.set_state("idle", state)
            return None
        wait = self.wait_for(recoveries)
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
        )
        return wait

    def executive_reason(self, state: StoryState) -> str:
        """Plain-language explanation for the BLOCKED inbox message."""
        incident = state.extra.get(INCIDENT_KEY) or {}
        cause = str(incident.get("cause") or "aconteceu um problema inesperado nesta etapa")
        tried = int(incident.get("recoveries", 1)) - 1
        if tried > 0:
            return (
                f"{cause[0].upper()}{cause[1:]}. O Ops Loompa tentou retomar sozinho {tried} vez(es), "
                "sem sucesso, e preferiu pedir sua orientação em vez de insistir."
            )
        return f"{cause[0].upper()}{cause[1:]}. O Ops Loompa não conseguiu resolver sozinho e pediu sua orientação."

    def on_success(self, state: StoryState) -> None:
        """The node ran fine after a retry: close the incident and leave one calm note."""
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
