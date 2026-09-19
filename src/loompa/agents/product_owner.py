"""Product Owner Loompa: owns the backlog and reviews what feeds it.

Fase 1 (ADR-0006): reviews the spec with the "No Invention" gate — every acceptance criterion
must trace back to something the founder said, the constitution or an existing spec. Fase 3
makes this agent the only writer of the backlog: the methods below are the only door to
`loompa.backlog.Backlog`, and Master, Kaizen, the dashboard and the founder's answers all
come through them.
"""

from __future__ import annotations

from loompa.agents.base import AgentResult, LoompaAgent
from loompa.backlog import DEFAULT_PRIORITY, Admission, Backlog
from loompa.engine.state import Stage, StoryState
from loompa.speckit import story_dir
from loompa.sprints import SprintBoard, SprintError

REVIEW_SYSTEM = """<!-- role:product_owner -->
You are the Product Owner Loompa. Review the specification written for the story below before
engineering starts. Apply the "No Invention" gate:
- Every acceptance criterion must trace to the founder's request, the founder's notes, the
  constitution or an existing spec. Anything else is invented scope and must be flagged.
- The spec must be complete enough to build from (goal, in/out of scope, testable criteria) and
  must not contradict the constitution.
Respond with JSON only:
{{"approved": bool, "unsupported": [str], "missing": [str], "notes": str}}
`unsupported` lists criteria that cannot be traced (quote them); `missing` lists what a builder
would still need. Approve when there is nothing unsupported and nothing critical missing.
Write in {language}.
"""


RESEARCH_REVIEW_SYSTEM = """<!-- role:product_owner -->
You are the Product Owner Loompa. The Analyst wrote the research report below for the founder's
request. Review it before it reaches the founder:
- It must answer the question that was asked, not a nearby one.
- Every finding must be backed by the sources cited next to it; a claim without a source is an
  opinion and must be flagged. Sources marked as unverified do not count.
- The limitations must be stated honestly (for example when the web could not be searched).
- The recommendation must follow from the findings and say what would change it.
Respond with JSON only:
{{"approved": bool, "unsupported": [str], "missing": [str], "notes": str}}
`unsupported` quotes the claims that are not backed by their sources; `missing` lists what the
founder would still need to decide. Approve when nothing is unsupported and nothing critical is
missing. Write in {language}.
"""


class ProductOwnerAgent(LoompaAgent):
    role = "product_owner"
    display = "Product Owner Loompa"

    # ---------------------------------------------------------------- backlog
    @property
    def backlog(self) -> Backlog:
        return Backlog(self.ctx, owner=self)

    def add_item(
        self,
        title: str,
        description: str = "",
        *,
        epic: str = "",
        priority: int = DEFAULT_PRIORITY,
        origin: str = "founder",
        founder_notes: list[str] | None = None,
    ) -> Admission:
        return self.backlog.add_item(
            title,
            description,
            epic=epic,
            priority=priority,
            origin=origin,
            founder_notes=founder_notes,
        )

    def set_priority(self, story_id: str, priority: int) -> None:
        self.backlog.set_priority(story_id, priority)

    def set_status(self, story_id: str, status: Stage) -> None:
        self.backlog.set_status(story_id, status)

    def admit(self, story_id: str) -> bool:
        return self.backlog.admit(story_id)

    def resolve_finding(self, story_id: str, choice: str) -> bool:
        """Apply the founder's decision on a suggested card: `sprint` puts it in the sprint
        being planned, `backlog` leaves it where it is, `drop` cancels it. Returns False when the
        card is no longer waiting in the backlog (someone already decided)."""
        row = self.ctx.store.get_story(story_id)
        if row is None or row["stage"] != Stage.BACKLOG:
            return False
        if choice == "sprint":
            try:
                SprintBoard(self.ctx.store, self.ctx.slug).add(story_id)
            except SprintError:  # already in a sprint: nothing to plan
                return False
        elif choice == "drop":
            self.set_status(story_id, Stage.CANCELLED)
        elif choice != "backlog":
            raise ValueError(f"decisão desconhecida: {choice}")
        self.ctx.emit("finding.decided", story_id=story_id, agent=self.name, choice=choice)
        return True

    # ------------------------------------------------------------------- spec
    async def review_spec(self, state: StoryState) -> AgentResult:
        self.set_state("WORKING", state, detail="revisando a spec (No Invention)")
        paths = story_dir(self.ctx.root, state.story_id)
        spec = paths.spec.read_text(encoding="utf-8") if paths.spec.is_file() else ""
        user = (
            f"# Story {state.story_id}: {state.title}\n\n## Founder's request\n{state.description or state.title}\n\n"
            + (
                "## Founder's notes\n" + "\n".join(f"- {n}" for n in state.founder_notes) + "\n\n"
                if state.founder_notes
                else ""
            )
            + f"## Spec under review\n{spec[:6000]}\n\n## Constitution (excerpt)\n{self.constitution(3000)}"
        )
        return await self._verdict(
            state,
            REVIEW_SYSTEM,
            user,
            event="spec.reviewed",
            unavailable="revisão indisponível; spec seguiu",
        )

    # --------------------------------------------------------------- research
    async def review_research(self, state: StoryState) -> AgentResult:
        """Sources and honesty gate on the Analyst's report. Two checks the model cannot talk
        its way past come first: a report that used the web must have at least one verified
        source, and a report with no findings says nothing."""
        self.set_state("WORKING", state, detail="revisando a pesquisa")
        report = state.extra.get("research") or {}
        paths = story_dir(self.ctx.root, state.story_id)
        text = paths.research.read_text(encoding="utf-8") if paths.research.is_file() else ""
        problems: list[str] = []
        if not report.get("findings_total"):
            problems.append("o relatório não traz nenhum achado")
        elif report.get("web_used") and not report.get("sourced"):
            problems.append("a pesquisa usou a web, mas nenhum achado tem fonte verificada")
        if report.get("dropped_sources"):
            problems.append(
                f"{len(report['dropped_sources'])} fonte(s) citada(s) não foram encontradas nas consultas"
            )
        user = (
            f"# Story {state.story_id}: {state.title}\n\n## Founder's request\n{state.description or state.title}\n\n"
            + (
                "## Founder's notes\n" + "\n".join(f"- {n}" for n in state.founder_notes) + "\n\n"
                if state.founder_notes
                else ""
            )
            + "## Automatic checks\n"
            + ("\n".join(f"- {p}" for p in problems) or "- all sources verified")
            + f"\n\n## Report under review\n{text[:7000]}"
        )
        res = await self._verdict(
            state,
            RESEARCH_REVIEW_SYSTEM,
            user,
            event="research.reviewed",
            unavailable="revisão indisponível; pesquisa seguiu",
            unsupported_label="Afirmações sem fonte que as sustente",
        )
        hard = [p for p in problems if "nenhum achado" in p]
        if hard:
            res.ok = False
            res.summary = " ".join(
                ["Problemas objetivos: " + "; ".join(hard) + ".", res.summary]
            ).strip()
        return res

    async def _verdict(
        self,
        state: StoryState,
        system: str,
        user: str,
        *,
        event: str,
        unavailable: str,
        unsupported_label: str = "Critérios sem origem rastreável",
    ) -> AgentResult:
        """One review call: `{approved, unsupported, missing, notes}` folded into an AgentResult.
        The review is advisory when the model is unavailable, so a provider outage never holds
        the line."""
        try:
            data = await self.ask_json(
                system.format(language=self.language), user, story=state, max_tokens=1500
            )
        except Exception:  # noqa: BLE001
            self.set_state("IDLE")
            return AgentResult(ok=True, summary=unavailable)
        unsupported = self._list(data, "unsupported")
        missing = self._list(data, "missing")
        approved = bool(data.get("approved")) and not unsupported
        notes = str(data.get("notes") or "").strip()
        summary_parts = []
        if unsupported:
            summary_parts.append(f"{unsupported_label}: " + "; ".join(unsupported[:5]))
        if missing:
            summary_parts.append("Faltando: " + "; ".join(missing[:5]))
        if notes:
            summary_parts.append(notes)
        self.ctx.emit(
            event,
            story_id=state.story_id,
            agent=self.name,
            approved=approved,
            unsupported=len(unsupported),
            missing=len(missing),
        )
        self.set_state("IDLE")
        return AgentResult(
            ok=approved,
            summary=" ".join(summary_parts) or "aprovado",
            data={"unsupported": unsupported, "missing": missing, "notes": notes},
        )
