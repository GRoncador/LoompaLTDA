"""The product's life cycle: phases with exit criteria and the product epics (plan 16.3, 16.5).

Lean, not a classic business plan: the value is in making the founder's decisions explicit and
in checking them against facts, never in a model's invented market size.

* **Phases.** A commercial product goes Discovery → MVP → Validation → Growth → Maturity; a
  personal one has a single phase, "in use" (16.1, `factory_kind`). Each phase has a goal and an
  exit criterion the founder writes ("o MVP termina quando 10 pessoas usarem por 2 semanas"),
  how to measure it (the Metrics Loompa proposes), and the facts the founder brings from outside
  the factory (users, conversion, revenue). Moving to the next phase is the founder's decision in
  the inbox, never automatic: the product manager only proposes it.
* **Epics.** A product epic has a name, a goal and a success metric, and belongs to a phase. It
  is born only through the product manager, with the founder's OK (each proposed epic is one
  question of a batch decision), and becomes cards only through the Product Owner's door
  (ADR-0017). A card filed with an epic's name is linked to it (`stories.epic_id`). The old
  `epic` text of a card stays as its group label (the Master's split of a big request).
* **Progress.** A phase's progress is the share of the cards of its epics that are done.

Messages to and answers from the founder are recognised by `technical_ref` (`lifecycle:…`),
a pointer never shown to the founder.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loompa.comms import FounderAnswer, FounderMessage, MessageKind, Option, Question
from loompa.factory_kind import profile
from loompa.store import now_iso

if TYPE_CHECKING:
    from loompa.engine.context import EngineContext

PHASE_NAMES = {
    "discovery": "Descoberta",
    "mvp": "MVP",
    "validation": "Validação",
    "growth": "Crescimento",
    "maturity": "Maturidade",
    "in_use": "Em uso",
}
PHASE_REF = "lifecycle:phase:"  # + the phase proposed
EPICS_REF = "lifecycle:epics"
DISCOVERY_REF = "lifecycle:discovery"
EPIC_STATUSES = ("active", "done", "dropped")


@dataclass
class Progress:
    phase: str
    name: str
    done: int
    total: int

    @property
    def percent(self) -> int:
        return round(100 * self.done / self.total) if self.total else 0


class Lifecycle:
    def __init__(self, ctx: EngineContext):
        self.ctx = ctx
        self.store = ctx.store
        self.slug = ctx.slug

    # ------------------------------------------------------------------ phases
    def phases(self) -> list[dict[str, Any]]:
        """The kind's phases, created on first use; the first is current. A kind switched later
        adds the phases it lacks and keeps the ones already lived."""
        keys = profile(self.ctx.config).phases
        have = {p["key"]: p for p in self.store.list_phases(self.slug)}
        for i, key in enumerate(keys):
            if key not in have:
                self.store.upsert_phase(
                    self.slug,
                    key,
                    name=PHASE_NAMES.get(key, key),
                    position=i,
                    status="planned",
                )
            elif have[key]["position"] != i:
                self.store.upsert_phase(self.slug, key, position=i)
        rows = [p for p in self.store.list_phases(self.slug) if p["key"] in keys]
        if rows and not any(p["status"] == "current" for p in rows):
            first = next((p for p in rows if p["status"] != "done"), rows[-1])
            self.store.upsert_phase(self.slug, first["key"], status="current", started_at=now_iso())
            rows = [p for p in self.store.list_phases(self.slug) if p["key"] in keys]
        return rows

    def current(self) -> dict[str, Any] | None:
        return next((p for p in self.phases() if p["status"] == "current"), None)

    def set_phase(self, key: str, **fields: str) -> None:
        """The founder's words for a phase: goal, exit criterion, facts, how to measure."""
        if key not in {p["key"] for p in self.phases()}:
            raise ValueError(f"fase desconhecida: {key}")
        allowed = {
            k: str(v).strip()[:2000]
            for k, v in fields.items()
            if k in ("goal", "exit_criterion", "facts", "measure")
        }
        if allowed:
            self.store.upsert_phase(self.slug, key, **allowed)

    def next_phase(self) -> dict[str, Any] | None:
        rows = self.phases()
        cur = self.current()
        if cur is None:
            return None
        later = [p for p in rows if p["position"] > cur["position"]]
        return later[0] if later else None

    def move_to(self, key: str) -> None:
        """The founder's decision: the current phase ends and `key` starts."""
        rows = {p["key"]: p for p in self.phases()}
        if key not in rows:
            raise ValueError(f"fase desconhecida: {key}")
        cur = self.current()
        if cur and cur["key"] != key:
            self.store.upsert_phase(self.slug, cur["key"], status="done", ended_at=now_iso())
        self.store.upsert_phase(self.slug, key, status="current", started_at=now_iso())
        self.ctx.emit("lifecycle.phase", phase=key)

    def propose_move(
        self, evidence: str, missing: list[str] | None = None
    ) -> FounderMessage | None:
        """The product manager thinks the exit criterion holds: the founder decides (16.3)."""
        cur, nxt = self.current(), self.next_phase()
        if cur is None or nxt is None:
            return None
        if any(
            m.technical_ref == PHASE_REF + nxt["key"]
            for m in self.store.list_messages(self.slug, status="pending")
        ):
            return None  # already asked
        msg = FounderMessage(
            factory=self.slug,
            kind=MessageKind.DECISION,
            sender="Product Manager Loompa",
            title=f"A fase {cur['name']} parece cumprida: passar para {nxt['name']}?",
            context=(
                f"O critério de saída que você definiu: “{cur['exit_criterion'] or 'não definido'}”.\n\n"
                f"O que eu vi: {evidence}"
                + (f"\n\nO que ainda não sei: {'; '.join(missing)}" if missing else "")
            ),
            impact="Mudar de fase muda os épicos que a reunião de sprint propõe. Nada muda sem você.",
            options=[
                Option(key="move", label=f"Passar para {nxt['name']}", recommended=True),
                Option(key="stay", label=f"Continuar em {cur['name']}"),
            ],
            technical_ref=PHASE_REF + nxt["key"],
        )
        return self.ctx.inbox(msg)

    # ------------------------------------------------------------------- epics
    def epics(self, phase: str | None = None, *, active_only: bool = False) -> list[dict]:
        rows = self.store.list_epics(self.slug)
        if phase is not None:
            rows = [e for e in rows if e["phase"] == phase]
        if active_only:
            rows = [e for e in rows if e["status"] == "active"]
        return rows

    def epic_by_name(self, name: str) -> dict[str, Any] | None:
        key = name.strip().lower()
        if not key:
            return None
        return next(
            (
                e
                for e in self.epics()
                if e["name"].strip().lower() == key and e["status"] == "active"
            ),
            None,
        )

    def ask_epics(self, proposals: list[dict[str, str]]) -> FounderMessage | None:
        """The product manager's epics go to the founder as one batch: each is a question."""
        if not proposals:
            return None
        cur = self.current()
        msg = FounderMessage(
            factory=self.slug,
            kind=MessageKind.DECISION,
            sender="Product Manager Loompa",
            title=f"{len(proposals)} épico(s) para a fase {cur['name'] if cur else ''}".strip(),
            context=(
                "Um épico agrupa as histórias de um resultado do produto, com uma métrica de "
                "sucesso. Diga quais criar; os cards deles vêm depois, pelo Product Owner, na "
                "reunião de sprint."
            ),
            impact="Nada entra no backlog agora.",
            options=[
                Option(key="answer", label="Enviar as respostas", recommended=True),
                Option(key="defaults", label="Criar todos"),
            ],
            questions=[
                Question(
                    id=f"e{i}",
                    text=f"{p['name']}: {p.get('goal', '')}".strip(": "),
                    why=(f"Métrica: {p.get('metric', '')}. " if p.get("metric") else "")
                    + str(p.get("why") or ""),
                    options=[
                        Option(key="create", label="Criar", recommended=True),
                        Option(key="skip", label="Não criar"),
                    ],
                )
                for i, p in enumerate(proposals, 1)
            ],
            technical_ref=EPICS_REF,
        )
        self.store.set(f"{EPICS_REF}:{msg.id}", json.dumps(proposals, ensure_ascii=False))
        return self.ctx.inbox(msg)

    def create_epic(self, name: str, goal: str = "", metric: str = "", why: str = "") -> str:
        cur = self.current()
        epic_id = self.store.upsert_epic(
            {
                "factory": self.slug,
                "name": name.strip()[:80],
                "goal": goal.strip()[:500],
                "metric": metric.strip()[:300],
                "why": why.strip()[:500],
                "phase": cur["key"] if cur else "",
                "status": "active",
            }
        )
        self.ctx.emit("lifecycle.epic", epic_id=epic_id, name=name)
        return epic_id

    def set_epic_status(self, epic_id: str, status: str) -> None:
        if status not in EPIC_STATUSES:
            raise ValueError(f"situação desconhecida: {status}")
        epic = self.store.get_epic(epic_id)
        if epic is None or epic["factory"] != self.slug:
            raise ValueError(f"épico desconhecido: {epic_id}")
        self.store.upsert_epic({**epic, "status": status})

    def link(self, story_id: str, epic_text: str) -> str:
        """A card filed with an epic's name belongs to that epic (16.5)."""
        epic = self.epic_by_name(epic_text)
        if epic is None:
            return ""
        self.store.update_story(story_id, epic_id=epic["id"])
        return str(epic["id"])

    # ---------------------------------------------------------------- progress
    def progress(self, phase: str | None = None) -> Progress | None:
        cur = (
            self.current()
            if phase is None
            else next((p for p in self.phases() if p["key"] == phase), None)
        )
        if cur is None:
            return None
        ids = {e["id"] for e in self.epics(cur["key"]) if e["status"] != "dropped"}
        cards = [
            s
            for s in self.store.list_stories(self.slug)
            if s.get("epic_id") in ids and s["stage"] != "CANCELLED"
        ]
        done = sum(1 for s in cards if s["stage"] == "DONE")
        return Progress(cur["key"], cur["name"], done, len(cards))

    def brief(self) -> str:
        """The phase and its epics as prompt material (the PM, the PO's sprint proposal)."""
        cur = self.current()
        if cur is None:
            return ""
        lines = [
            f"## Product phase: {cur['name']} ({cur['key']})",
            f"- goal: {cur['goal'] or '(not written yet)'}",
            f"- exit criterion: {cur['exit_criterion'] or '(not written yet)'}",
        ]
        if cur["facts"]:
            lines.append(f"- facts the founder brought: {cur['facts']}")
        epics = self.epics(cur["key"], active_only=True)
        if epics:
            lines.append("## Product epics of this phase (cards may name them in `epic`)")
            for e in epics:
                lines.append(
                    f"- {e['name']}: {e['goal']}"
                    + (f" (metric: {e['metric']})" if e["metric"] else "")
                )
        return "\n".join(lines) + "\n\n"

    # ----------------------------------------------------------------- answers
    def on_answer(self, msg: FounderMessage, answer: FounderAnswer) -> bool:
        """Apply the founder's answer to a life-cycle message; False when it is not one."""
        ref = msg.technical_ref or ""
        if ref.startswith(PHASE_REF):
            if answer.option_key == "move":
                self.move_to(ref.removeprefix(PHASE_REF))
            return True
        if ref == EPICS_REF:
            proposals = json.loads(self.store.get(f"{EPICS_REF}:{msg.id}") or "[]")
            for i, p in enumerate(proposals, 1):
                pick = answer.answers.get(f"e{i}") or "create"
                if answer.option_key == "defaults" or pick == "create":
                    if self.epic_by_name(p["name"]) is None:
                        self.create_epic(
                            p["name"], p.get("goal", ""), p.get("metric", ""), p.get("why", "")
                        )
            return True
        if ref == DISCOVERY_REF:
            if answer.option_key == "save":
                from loompa.discovery import save_discovery

                save_discovery(self.ctx, msg.id)
            return True
        return False
