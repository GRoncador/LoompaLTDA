"""Product Owner Loompa: owns the backlog and reviews what feeds it.

Fase 1 (ADR-0006): reviews the spec with the "No Invention" gate — every acceptance criterion
must trace back to something the founder said, the constitution or an existing spec. Fase 3
makes this agent the only writer of the backlog.
"""

from __future__ import annotations

from loompa.agents.base import AgentResult, LoompaAgent
from loompa.engine.state import StoryState
from loompa.speckit import story_dir

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


class ProductOwnerAgent(LoompaAgent):
    role = "product_owner"
    display = "Product Owner Loompa"

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
        try:
            data = await self.ask_json(
                REVIEW_SYSTEM.format(language=self.language), user, story=state, max_tokens=1500
            )
        except Exception:  # noqa: BLE001 - the review is advisory when the model is unavailable
            self.set_state("IDLE")
            return AgentResult(ok=True, summary="revisão indisponível; spec seguiu")
        unsupported = self._list(data, "unsupported")
        missing = self._list(data, "missing")
        approved = bool(data.get("approved")) and not unsupported
        notes = str(data.get("notes") or "").strip()
        summary_parts = []
        if unsupported:
            summary_parts.append("Critérios sem origem rastreável: " + "; ".join(unsupported[:5]))
        if missing:
            summary_parts.append("Faltando: " + "; ".join(missing[:5]))
        if notes:
            summary_parts.append(notes)
        self.ctx.emit(
            "spec.reviewed",
            story_id=state.story_id,
            agent=self.name,
            approved=approved,
            unsupported=len(unsupported),
            missing=len(missing),
        )
        self.set_state("IDLE")
        return AgentResult(
            ok=approved,
            summary=" ".join(summary_parts) or "spec aprovada",
            data={"unsupported": unsupported, "missing": missing, "notes": notes},
        )
