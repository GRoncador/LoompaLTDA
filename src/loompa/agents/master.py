"""Master Loompa (COO): morning meeting, executive translation, end-of-day report."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loompa.agents.base import LoompaAgent
from loompa.agents.product_owner import ProductOwnerAgent
from loompa.comms import (
    FounderMessage,
    MessageKind,
    Option,
    audit_executive_text,
    compose_blocked_message,
    sanitize_for_founder,
)
from loompa.engine.state import TERMINAL, Complexity, Stage, StoryKind, StoryState
from loompa.sprints import Sprint, SprintBoard, SprintError, SprintStatus

MEETING_SYSTEM = """<!-- role:master -->
You are the Master Loompa, COO of an autonomous software factory. The founder just gave you the goals
for today. Decompose them into independent user stories that can be developed in parallel by separate
engineers, each small enough to finish in a few hours with tests.
Rules: as many stories as the goals genuinely need (a goal too big for one story becomes an
`epic` with several stories), no duplicates of the existing backlog listed below, titles are short
imperative phrases, descriptions carry every business detail the founder mentioned, `priority` is
1 (urgent) to 5 (nice to have). Group related stories under an `epic` name.
Respond with JSON only: {{"stories": [{{"title": str, "description": str, "epic": str, "priority": int}}],
"clarifications": [str]}}. `clarifications` are questions ONLY if a goal is impossible to start without
an answer; keep them in plain {language}. Write everything in {language}.
"""

EXEC_SYSTEM = """<!-- role:master -->
Rewrite the technical problem below for a non-technical founder in {language}. Output JSON:
{{"title": str (one sentence), "context": str (2-3 plain sentences: what we were doing, what happened),
"impact": str (what it means for the product/business and what continues normally),
"options": [{{"key": str, "label": str, "description": str, "recommended": bool}}]}} with 2-3 options.
Absolutely no file names, code, error names or stack traces.
"""


CLASSIFY_SYSTEM = """<!-- role:master -->
Classify the story below for the factory pipeline. Respond with JSON only:
{{"kind": "feature"|"bugfix"|"research", "complexity": "SIMPLE"|"STANDARD"|"COMPLEX",
  "children": [{{"title": str, "description": str}}], "reason": str}}
- kind: `bugfix` repairs behaviour that already exists; `research` produces knowledge (a report,
  a comparison, a recommendation) instead of code; everything else is `feature`.
- complexity: SIMPLE = one obvious change, one or two files, no design decision; COMPLEX = touches
  several modules, needs architecture or product judgement, or has security/data-migration risk;
  otherwise STANDARD.
- children: ONLY when the request clearly bundles several independent deliverables that should
  be built and reviewed separately; then list 2-6 child stories, each buildable alone. Otherwise [].
Write titles and descriptions in {language}; keep `reason` to one sentence.
"""


@dataclass
class Classification:
    kind: StoryKind = StoryKind.FEATURE
    complexity: Complexity = Complexity.STANDARD
    children: list[dict[str, str]] = field(default_factory=list)
    reason: str = ""


class MasterAgent(LoompaAgent):
    role = "master"
    display = "Master Loompa"

    # ------------------------------------------------------------------- intake
    async def classify(self, state: StoryState) -> Classification:
        """Kind, complexity and (rarely) an epic split. Deterministic fallback keeps the line
        moving when the model is unavailable: STANDARD feature, no split."""
        self.set_state("WORKING", state, detail="classificando a história")
        user = (
            f"# Story {state.story_id}: {state.title}\n\n{state.description or '(sem descrição)'}\n\n"
            + (
                "## Founder's notes\n" + "\n".join(f"- {n}" for n in state.founder_notes) + "\n\n"
                if state.founder_notes
                else ""
            )
            + f"## Constitution (excerpt)\n{self.constitution(1500)}"
        )
        out = Classification()
        try:
            data = await self.ask_json(
                CLASSIFY_SYSTEM.format(language=self.language), user, story=state, max_tokens=800
            )
        except Exception:  # noqa: BLE001
            self.set_state("IDLE")
            return out
        try:
            out.kind = StoryKind(str(data.get("kind") or "feature").lower())
        except ValueError:
            pass
        try:
            out.complexity = Complexity(str(data.get("complexity") or "STANDARD").upper())
        except ValueError:
            pass
        out.children = [
            {
                "title": str(c.get("title", "")).strip()[:120],
                "description": str(c.get("description", "")).strip(),
            }
            for c in (data.get("children") or [])
            if isinstance(c, dict) and str(c.get("title", "")).strip()
        ][:6]
        out.reason = str(data.get("reason") or "")[:300]
        self.set_state("IDLE")
        return out

    def split_epic(self, parent: StoryState, children: list[dict[str, str]]) -> list[str]:
        """Create child stories under the parent (now an epic). Children run independently."""
        ids: list[str] = []
        epic = parent.epic or parent.title[:60]
        row = self.ctx.store.get_story(parent.story_id) or {}
        po = ProductOwnerAgent(self.ctx)
        board = SprintBoard(self.ctx.store, self.ctx.slug)
        parent_sprint = board.sprint_of(parent.story_id)
        for child in children:
            added = po.add_item(
                child["title"],
                child.get("description") or "",
                epic=epic,
                priority=row.get("priority", 300),
                origin="epic",
                founder_notes=list(parent.founder_notes),
            )
            if added.created:  # the parent was already cleared to run; so are its parts
                if parent_sprint is not None:
                    board.add(added.story_id, parent_sprint.id)
                po.admit(added.story_id)
            ids.append(added.story_id)
        self.ctx.inbox(
            FounderMessage(
                factory=self.ctx.slug,
                story_id=parent.story_id,
                kind=MessageKind.INFO,
                sender=self.name,
                title=f"“{parent.title}” virou um épico com {len(ids)} histórias",
                context="O pedido era grande demais para uma entrega só. Dividi em partes independentes que a equipe constrói e revisa separadamente.",
                impact="Cada parte chega para sua aprovação assim que ficar pronta.",
                allow_free_text=False,
            )
        )
        return ids

    # ------------------------------------------------------------------- sprint
    def start_sprint(
        self, story_ids: list[str] | None = None, *, goal: str = "", limit: int | None = None
    ) -> Sprint:
        """Sprint Meeting: gather the stories, let the Product Owner admit them, start the batch.

        With explicit ids they join the sprint being planned; with none, the sprint's draft is
        used or, when it is empty, the founder's cards in priority order (Kaizen findings wait
        for an explicit yes). Only cards waiting in the backlog can be picked."""
        self.set_state("WORKING", detail="reunião de sprint")
        try:
            store, po = self.ctx.store, ProductOwnerAgent(self.ctx)
            board = SprintBoard(store, self.ctx.slug)
            sprint = board.draft()
            picked = list(story_ids or [])
            if not picked and not sprint.story_ids:
                picked = [
                    s["id"]
                    for s in store.list_stories(self.ctx.slug, stage=Stage.BACKLOG)
                    if s["origin"] != "kaizen"
                ][: limit or None]
            for sid in picked:
                board.add(sid, sprint.id)
            sprint = board.get(sprint.id) or sprint
            if not sprint.story_ids:
                raise SprintError("não há histórias no backlog para começar um sprint")
            for sid in sprint.story_ids:
                po.admit(sid)
            sprint = board.start(sprint.id, goal)
            self.ctx.emit(
                "sprint.started",
                agent=self.name,
                sprint_id=sprint.id,
                stories=sprint.story_ids,
                goal=sprint.goal,
            )
            return sprint
        finally:
            self.set_state("IDLE")

    def close_finished_sprints(self) -> list[Sprint]:
        """Close every running sprint whose stories all reached a terminal state and tell the
        founder. A story waiting on the founder keeps its sprint open; nothing else waits."""
        board = SprintBoard(self.ctx.store, self.ctx.slug)
        closed: list[Sprint] = []
        for sprint in board.sprints(SprintStatus.RUNNING):
            rows = [self.ctx.store.get_story(sid) for sid in sprint.story_ids]
            if any(r is not None and r["stage"] not in TERMINAL for r in rows):
                continue
            counts = board.progress(sprint)
            sprint = board.close(sprint.id)
            self.ctx.emit(
                "sprint.done",
                agent=self.name,
                sprint_id=sprint.id,
                done=counts["done"],
                cancelled=counts["cancelled"],
            )
            self.ctx.inbox(
                FounderMessage(
                    factory=self.ctx.slug,
                    kind=MessageKind.INFO,
                    sender=self.name,
                    title=f"Sprint {sprint.id} concluído",
                    context=(
                        f"{counts['done']} entregas concluídas"
                        + (f" e {counts['cancelled']} canceladas" if counts["cancelled"] else "")
                        + "."
                        + (f" Meta: {sprint.goal}" if sprint.goal else "")
                    ),
                    impact="Escolha as próximas histórias do backlog para o próximo sprint.",
                    allow_free_text=False,
                )
            )
            closed.append(sprint)
        return closed

    # ------------------------------------------------------------------ meeting
    async def meeting(self, goals: str) -> dict[str, Any]:
        self.set_state("WORKING", detail="reunião matinal")
        existing = [
            s["title"]
            for s in self.ctx.store.list_stories(self.ctx.slug)
            if s["stage"] not in (Stage.DONE, Stage.CANCELLED)
        ]
        precedents = self.precedents(goals, kinds=("constitution", "adr", "learning", "doc"))
        user = (
            f"# Metas de hoje (Founder)\n{goals}\n\n## Backlog existente\n"
            + ("\n".join(f"- {t}" for t in existing) or "(vazio)")
            + f"\n\n## Constitution (excerpt)\n{self.constitution(2500)}\n\n{precedents}"
        )
        try:
            data = await self.ask_json(MEETING_SYSTEM.format(language=self.language), user)
            stories = [s for s in data.get("stories", []) if isinstance(s, dict) and s.get("title")]
            clarifications = self._list(data, "clarifications")
        except Exception:  # noqa: BLE001 - fall back to a deterministic split so the day still starts
            stories = self._split_goals(goals)
            clarifications = []
        created = []
        po = ProductOwnerAgent(self.ctx)
        for s in stories:
            priority = int(s.get("priority") or 3)
            added = po.add_item(
                str(s["title"]),
                str(s.get("description") or ""),
                epic=str(s.get("epic") or ""),
                priority=max(1, min(5, priority)) * 100,
                origin="founder",
            )
            if not added.created:
                continue
            row = self.ctx.store.get_story(added.story_id) or {}
            created.append(
                {
                    "id": added.story_id,
                    "title": row.get("title", ""),
                    "epic": row.get("epic", ""),
                    "priority": priority,
                }
            )
        for q in clarifications[:3]:
            self.ctx.inbox(
                FounderMessage(
                    factory=self.ctx.slug,
                    kind=MessageKind.DECISION,
                    sender=self.name,
                    title=sanitize_for_founder(q, max_chars=160),
                    context="Surgiu ao planejar as metas de hoje. Enquanto isso, as outras histórias seguem.",
                    options=[Option(key="answer", label="Responder abaixo", recommended=True)],
                )
            )
        self.set_state("IDLE")
        self.ctx.emit(
            "meeting.done",
            agent=self.name,
            created=len(created),
            clarifications=len(clarifications),
        )
        return {"stories": created, "clarifications": clarifications}

    @staticmethod
    def _split_goals(goals: str) -> list[dict[str, Any]]:
        parts = [
            p.strip(" -•*\t") for p in re.split(r"[;\n]+|\d+[.)]\s+", goals) if p.strip(" -•*\t")
        ]
        return [{"title": p[:100], "description": p, "epic": "", "priority": 3} for p in parts] or [
            {"title": goals[:100], "description": goals, "epic": "", "priority": 3}
        ]

    # --------------------------------------------------------- executive rewrite
    async def blocked_message(
        self,
        state: StoryState,
        technical_reason: str,
        *,
        options: list[str] | None = None,
        technical_ref: str | None = None,
        executive: str | None = None,
    ) -> FounderMessage:
        """Compose a BLOCKED inbox message; LLM rewrite when possible, deterministic filter always."""
        opts = [
            Option(key=f"opt{i + 1}", label=o[:120], recommended=i == 0)
            for i, o in enumerate(options or [])
        ]
        if not self.ctx.dry_run:
            try:
                data = await self.ask_json(
                    EXEC_SYSTEM.format(language=self.language),
                    f"Story: {state.title}\n\nProblem:\n{technical_reason[:3000]}\n\nSuggested options: {options or 'none'}",
                    story=state,
                    max_tokens=800,
                )
                llm_opts = [
                    Option(
                        key=str(o.get("key") or f"opt{i + 1}")[:20],
                        label=str(o.get("label") or "")[:120],
                        description=str(o.get("description") or "")[:300],
                        recommended=bool(o.get("recommended")),
                    )
                    for i, o in enumerate(data.get("options") or [])
                    if isinstance(o, dict) and o.get("label")
                ]
                title, context, impact = (
                    str(data.get("title") or ""),
                    str(data.get("context") or ""),
                    str(data.get("impact") or ""),
                )
                if title and not audit_executive_text(f"{title}\n{context}\n{impact}"):
                    msg = compose_blocked_message(
                        factory=self.ctx.slug,
                        story_id=state.story_id,
                        story_title=state.title,
                        reason=context,
                        impact=impact,
                        options=llm_opts or opts or None,
                        technical_ref=technical_ref,
                    )
                    msg.title = title[:200]
                    return msg
            except Exception:  # noqa: BLE001 - fall through to deterministic composer
                pass
        return compose_blocked_message(
            factory=self.ctx.slug,
            story_id=state.story_id,
            story_title=state.title,
            reason=executive or technical_reason,
            options=opts or None,
            technical_ref=technical_ref,
        )

    # ------------------------------------------------------------ daily report
    def end_of_day_report(self) -> FounderMessage:
        stories = self.ctx.store.list_stories(self.ctx.slug)
        by_stage: dict[str, int] = {}
        for s in stories:
            by_stage[s["stage"]] = by_stage.get(s["stage"], 0) + 1
        since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        learnings = self.ctx.store.list_learnings(since_iso=since)
        pending = [
            m
            for m in self.ctx.store.list_messages(self.ctx.slug, status="pending")
            if m.requires_action
        ]
        finance = self.ctx.tracker.executive_daily_summary()
        lines = [
            f"Entregas prontas para sua revisão: {by_stage.get('AWAITING_FOUNDER', 0)}.",
            f"Concluídas: {by_stage.get('DONE', 0)} · Em andamento: {sum(v for k, v in by_stage.items() if k in ('SPEC', 'PLAN', 'DEV', 'TEST', 'REVIEW'))} · No backlog: {by_stage.get('BACKLOG', 0)}.",
            f"Decisões aguardando você: {len(pending)}.",
            finance,
        ]
        if learnings:
            lines.append(
                f"Melhorias e problemas catalogados hoje (Loop Kaizen): {len(learnings)} — "
                + "; ".join(
                    sanitize_for_founder(item["title"], max_chars=80) for item in learnings[:3]
                )
                + "."
            )
        findings = [r for r in stories if r["origin"] == "kaizen" and r["stage"] == Stage.BACKLOG]
        if findings:
            lines.append(
                f"Achados aguardando um sprint no backlog: {len(findings)}. "
                "Decida na próxima entrega ou ao montar o próximo sprint."
            )
        for tip in self.ctx.tracker.suggestions()[:2]:
            lines.append(f"Dica de custo: {sanitize_for_founder(tip, max_chars=200)}")
        msg = FounderMessage(
            factory=self.ctx.slug,
            kind=MessageKind.INFO,
            sender=self.name,
            title=f"Resumo do dia {datetime.now(UTC).date().isoformat()}",
            context="\n".join(lines),
            impact="",
            options=[],
            allow_free_text=False,
        )
        return self.ctx.inbox(msg)
