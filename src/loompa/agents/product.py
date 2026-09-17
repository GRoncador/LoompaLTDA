"""Product Loompa (PO): writes spec.md with BDD acceptance criteria."""

from __future__ import annotations

from loompa.agents.base import AgentResult, LoompaAgent
from loompa.engine.state import StoryState
from loompa.speckit import SpecArtifacts, story_dir

SYSTEM = """<!-- role:product -->
You are the Product Loompa, product owner of an autonomous software factory.
Turn the user story into a precise, minimal specification following the GitHub Spec Kit protocol.
Rules:
- Stay strictly within the story; list anything tempting but unrelated under out_of_scope.
- Acceptance criteria are BDD sentences ("Dado ..., quando ..., então ..."), testable and binary.
- Respect the project constitution below. Never invent libraries or scope.
- If a genuinely blocking product decision exists (two viable paths with business impact), set
  needs_decision=true and phrase `question`, `context` and 2-3 short `options` in plain,
  non-technical {language} for a founder. Otherwise needs_decision=false and leave questions
  as informational.
Respond with JSON only:
{{"goal": str, "in_scope": [str], "out_of_scope": [str], "acceptance": [str], "rules": [str],
  "questions": [str], "needs_decision": bool, "question": str, "context": str, "options": [str]}}
Write all strings in {language}.
"""


class ProductAgent(LoompaAgent):
    role = "product"
    display = "Product Loompa"

    async def run(self, state: StoryState) -> AgentResult:
        self.set_state("WORKING", state, detail="escrevendo spec.md")
        precedents = self.precedents(
            f"{state.title}\n{state.description}",
            kinds=("constitution", "adr", "learning", "spec", "doc"),
        )
        user = (
            f"# Story {state.story_id}: {state.title}\n\n{state.description or '(sem descrição adicional)'}\n\n"
            + (
                "## Orientações do Founder\n"
                + "\n".join(f"- {n}" for n in state.founder_notes)
                + "\n\n"
                if state.founder_notes
                else ""
            )
            + f"## Constitution (excerpt)\n{self.constitution(4000)}\n\n{precedents}"
        )
        data = await self.ask_json(SYSTEM.format(language=self.language), user, story=state)
        if data.get("needs_decision") and not state.founder_notes:
            self.set_state("BLOCKED", state, detail="decisão de produto pendente")
            return AgentResult(
                ok=False,
                blocked_reason=f"{data.get('question', '')}\n\n{data.get('context', '')}".strip(),
                blocked_options=self._list(data, "options")[:3] or None,
            )
        artifacts = SpecArtifacts(
            goal=str(data.get("goal") or state.title),
            in_scope=self._list(data, "in_scope"),
            out_of_scope=self._list(data, "out_of_scope"),
            acceptance=self._list(data, "acceptance")
            or [
                f"Dado o sistema, quando a história '{state.title}' é usada, então funciona conforme descrito."
            ],
            rules=self._list(data, "rules"),
            questions=self._list(data, "questions"),
        )
        paths = story_dir(self.ctx.root, state.story_id)
        paths.root.mkdir(parents=True, exist_ok=True)
        from loompa.speckit import render_spec

        paths.spec.write_text(
            render_spec(
                story_id=state.story_id,
                title=state.title,
                goal=artifacts.goal,
                in_scope=artifacts.in_scope,
                out_of_scope=artifacts.out_of_scope,
                acceptance=artifacts.acceptance,
                rules=artifacts.rules,
                questions=artifacts.questions,
            ),
            encoding="utf-8",
        )
        state.acceptance = artifacts.acceptance
        state.spec_ready = True
        self.set_state("IDLE")
        return AgentResult(
            ok=True,
            summary=f"spec com {len(artifacts.acceptance)} critérios",
            data=artifacts.model_dump(),
        )
