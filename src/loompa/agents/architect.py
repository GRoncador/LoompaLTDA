"""Architect Loompa: plan.md, tasks.md, ADRs and constitution stewardship (Tier 1)."""

from __future__ import annotations

import re
from datetime import date

from loompa.agents.base import EXPLORE_HINT, AgentResult, LoompaAgent
from loompa.engine.state import StoryState
from loompa.speckit import render_plan, render_tasks, story_dir

SYSTEM = """<!-- role:architect -->
You are the Architect Loompa of an autonomous software factory. Produce the technical plan and the
atomic task checklist for the story below, strictly inside the existing architecture.
Rules:
- Use only libraries already allowed by the constitution. New dependencies require an ADR: if one
  is unavoidable, add it under `adr_proposal` and keep the plan working without it if possible.
- `files` is the exhaustive list of paths (files or directories, relative to repo root) the Worker
  may create or edit. Include test paths. The Worker is physically blocked from touching anything else.
- `tasks` are 2-8 atomic steps; each becomes one commit and must leave the test suite green.
  Every task must include its tests. Order them so each builds on the previous one.
- Consider the precedents from organizational memory; do not repeat past mistakes.
Respond with JSON only:
{{"approach": str, "files": [str], "contracts": str, "risks": [str], "tasks": [str], "adr_proposal": str}}
Write in {language}.
"""

LESSON_SYSTEM = """<!-- role:architect -->
You maintain the project constitution. Given a bug that needed escalation and how it was fixed,
write ONE concise, general, actionable rule (max 2 sentences, {language}) that would have prevented
it. Respond with JSON: {{"rule": str, "generalizable": bool}} — generalizable=false when the fix was
purely local and no rule applies.
"""


class ArchitectAgent(LoompaAgent):
    role = "architect"
    display = "Architect Loompa"

    async def run(self, state: StoryState) -> AgentResult:
        self.set_state("WORKING", state, detail="escrevendo plan.md e tasks.md")
        paths = story_dir(self.ctx.root, state.story_id)
        spec = paths.spec.read_text(encoding="utf-8") if paths.spec.is_file() else ""
        precedents = self.precedents(
            f"{state.title}\n{spec[:1500]}", kinds=("adr", "learning", "constitution", "doc")
        )
        outline = self._repo_outline()
        user = (
            f"# Story {state.story_id}: {state.title}\n\n## Spec\n{spec[:6000]}\n\n"
            + (
                "## Orientações do Founder\n"
                + "\n".join(f"- {n}" for n in state.founder_notes)
                + "\n\n"
                if state.founder_notes
                else ""
            )
            + (
                "## Falhas anteriores (contexto filtrado)\n"
                + "\n".join(state.failure_history[-2:])
                + "\n\n"
                if state.failure_history
                else ""
            )
            + f"## Repository outline\n{outline}\n\n## Constitution (excerpt)\n{self.constitution(4000)}\n\n{precedents}"
        )
        # The plan's `files` are the only paths the Worker may touch: let the Architect check
        # them in the repository instead of guessing.
        data = await self.ask_json_with_tools(
            SYSTEM.format(language=self.language) + EXPLORE_HINT,
            user,
            self.explore_tools(),
            story=state,
        )
        tasks = self._list(data, "tasks") or [
            f"Implementar '{state.title}' com testes cobrindo os critérios de aceitação"
        ]
        files = self._list(data, "files")
        precedent_titles = [
            line[4:].split(" (relev")[0]
            for line in precedents.splitlines()
            if line.startswith("### ")
        ]
        paths.plan.write_text(
            render_plan(
                story_id=state.story_id,
                title=state.title,
                approach=str(data.get("approach") or ""),
                files=files,
                contracts=str(data.get("contracts") or ""),
                risks=self._list(data, "risks"),
                precedents=precedent_titles,
            ),
            encoding="utf-8",
        )
        paths.tasks.write_text(
            render_tasks(story_id=state.story_id, title=state.title, tasks=tasks), encoding="utf-8"
        )
        adr = str(data.get("adr_proposal") or "").strip()
        if adr and adr.lower() not in ("", "none", "nenhum", "n/a", "null"):
            self.write_adr(f"{state.story_id}: {state.title}", adr, status="proposed")
        state.allowed_paths = files + [f".loompa/specs/{state.story_id}/"]
        state.tasks_total = len(tasks)
        state.tasks_done = []
        state.plan_ready = True
        self.set_state("IDLE")
        return AgentResult(
            ok=True, summary=f"plano com {len(tasks)} tarefas e {len(files)} caminhos", data=data
        )

    def _repo_outline(self, max_entries: int = 80) -> str:
        skip = {".git", ".loompa", "node_modules", ".venv", "__pycache__", "dist", "build"}
        lines = []
        for p in sorted(self.ctx.root.rglob("*")):
            rel = p.relative_to(self.ctx.root)
            if any(part in skip for part in rel.parts) or len(rel.parts) > 3:
                continue
            if p.is_dir():
                lines.append(f"{rel}/")
            elif p.suffix in (
                ".py",
                ".ts",
                ".tsx",
                ".js",
                ".go",
                ".rs",
                ".md",
                ".toml",
                ".json",
                ".yaml",
                ".yml",
            ):
                lines.append(str(rel))
            if len(lines) >= max_entries:
                lines.append("…")
                break
        return "\n".join(lines)

    # --------------------------------------------------------------- stewardship
    def write_adr(self, title: str, body: str, *, status: str = "accepted") -> str:
        decisions = self.ctx.factory.paths.decisions
        decisions.mkdir(parents=True, exist_ok=True)
        n = len(list(decisions.glob("*.md"))) + 1
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50]
        path = decisions / f"{n:04d}-{slug}.md"
        path.write_text(
            f"# ADR-{n:04d}: {title}\n\n**Status:** {status} · **Date:** {date.today().isoformat()}\n\n{body.strip()}\n",
            encoding="utf-8",
        )
        self.ctx.memory.index_file(path, kind="adr", doc_id=f"decisions/{path.name}")
        return str(path)

    async def incorporate_lesson(
        self, state: StoryState, failure: str, fix_summary: str
    ) -> str | None:
        """After a Tier 1 fix, add a generalizable rule to the constitution (Loop Kaizen)."""
        try:
            data = await self.ask_json(
                LESSON_SYSTEM.format(language=self.language),
                f"Story: {state.title}\n\nFalha (filtrada):\n{failure[:1500]}\n\nCorreção:\n{fix_summary[:800]}",
                story=state,
            )
        except Exception:  # noqa: BLE001 - lesson capture must never break the pipeline
            return None
        rule = str(data.get("rule") or "").strip()
        if not rule or data.get("generalizable") is False:
            return None
        self.append_constitution_lesson(rule, state.story_id)
        return rule

    def append_constitution_lesson(self, rule: str, story_id: str) -> None:
        path = self.ctx.factory.paths.constitution
        text = (
            path.read_text(encoding="utf-8")
            if path.is_file()
            else "# Constitution\n\n## 7. Lições incorporadas (Loop Kaizen)\n"
        )
        line = f"- [{date.today().isoformat()} · {story_id}] {rule}"
        if line in text:
            return
        if "## 7. Lições incorporadas" in text:
            text = text.rstrip("\n") + "\n" + line + "\n"
        else:
            text += f"\n## 7. Lições incorporadas (Loop Kaizen)\n{line}\n"
        path.write_text(text, encoding="utf-8")
        self.ctx.memory.index_file(path, kind="constitution", doc_id="constitution.md")
        self.ctx.emit("constitution.lesson", story_id=story_id, agent=self.name, rule=rule)
