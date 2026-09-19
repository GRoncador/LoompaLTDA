"""Master Loompa (COO): morning meeting, executive translation, end-of-day report."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from loompa.agents.base import LoompaAgent
from loompa.agents.conversation import Conversations, TurnResult, run_turn
from loompa.agents.product_owner import ProductOwnerAgent
from loompa.comms import (
    FounderMessage,
    MessageKind,
    Option,
    audit_executive_text,
    compose_blocked_message,
    sanitize_for_founder,
)
from loompa.conversations import (
    CommitResult,
    Conversation,
    ConversationBoard,
    ConversationError,
    ConversationKind,
    from_scale,
    to_scale,
)
from loompa.engine.state import TERMINAL, Complexity, Stage, StoryKind, StoryState
from loompa.sprints import Sprint, SprintBoard, SprintError, SprintStatus

MEETING_SYSTEM = """<!-- role:master -->
You are the Master Loompa, COO of an autonomous software factory, running a Sprint Meeting with the
founder in a chat. Over several messages you turn what the founder says into a draft backlog and a
draft sprint, and you keep both drafts tidy as the conversation changes them.
Rules:
- Decompose goals into independent stories that separate engineers can build in parallel, each
  small enough to finish in a few hours with tests. As many as the goals genuinely need; a goal too
  big for one story becomes several stories sharing an `epic` name. Titles are short imperative
  phrases; descriptions carry every business detail the founder mentioned and nothing they did not
  say (no invented requirements).
- Goals the founder wants worked on in this sprint get `in_sprint: true`; ideas for later stay
  false. When the founder asks for an existing backlog card, reference it by its id with an
  `update` (`in_sprint: true`) instead of adding it again. Never duplicate a card that is already
  in the backlog or in progress.
- Follow the founder's corrections literally (drop, reorder, rename, move in or out of the sprint).
- Ask a question only when you cannot draft even one story without the answer; otherwise draft and
  state your assumption in the reply. When the draft looks complete, say the founder can start
  the sprint or save it to the backlog.
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
    async def converse(self, conv: Conversation, text: str) -> TurnResult:
        """One turn of a Sprint Meeting: the founder speaks, the draft changes, the Master answers."""
        self.set_state("WORKING", detail="reunião com o Founder")
        try:
            context = f"## Constitution (excerpt)\n{self.constitution(2500)}\n\n" + self.precedents(
                text, kinds=("constitution", "adr", "learning", "doc")
            )
            return await run_turn(
                self,
                ConversationBoard(self.ctx.store, self.ctx.slug),
                conv,
                text,
                system=MEETING_SYSTEM,
                context=context,
                toolbox=self.explore_tools(),
                fallback_ops=lambda goals: [{"op": "add", **g} for g in self._split_goals(goals)],
            )
        finally:
            self.set_state("IDLE")

    def commit_meeting(
        self, conv: Conversation, *, start_sprint: bool, goal: str = ""
    ) -> CommitResult:
        """End a Sprint Meeting: every card in the draft goes to the backlog through the Product
        Owner and, with `start_sprint`, the cards marked for the sprint start it. Nothing is
        written until every pick has been checked, so a stale draft fails before it changes anything."""
        store, po = self.ctx.store, ProductOwnerAgent(self.ctx)
        draft = conv.draft
        if not draft.items:
            raise ConversationError("o rascunho está vazio")
        if start_sprint and not draft.in_sprint():
            raise ConversationError("marque ao menos uma história para o sprint")
        if start_sprint:
            for item in draft.in_sprint():
                row = store.get_story(item.story_id) if item.story_id else None
                if item.story_id and (row is None or row["stage"] != Stage.BACKLOG):
                    raise ConversationError(f"{item.story_id} não está mais esperando no backlog")
        result = CommitResult()
        picks: list[str] = []
        for item in draft.items:
            if item.story_id:  # already a card: at most its priority changes
                row = store.get_story(item.story_id)
                if row is not None and to_scale(row["priority"]) != item.priority:
                    po.set_priority(item.story_id, from_scale(item.priority))
                result.existing.append(item.story_id)
            else:
                added = po.add_item(
                    item.title,
                    item.description,
                    epic=item.epic,
                    priority=from_scale(item.priority),
                    origin=item.origin,
                )
                item.story_id = added.story_id
                (result.created if added.created else result.existing).append(added.story_id)
            if item.in_sprint:
                row = store.get_story(item.story_id) or {}
                if row.get("stage") == Stage.BACKLOG:
                    picks.append(item.story_id)
                else:  # a repeated title matched work that already started
                    result.skipped.append(item.story_id)
        if start_sprint:
            if not picks:  # `start_sprint([])` would mean "everything in the backlog"
                raise ConversationError(
                    "nenhuma das histórias do sprint está esperando no backlog; "
                    "os cards já foram salvos"
                )
            sprint = self.start_sprint(picks, goal=goal or draft.goal)
            result.sprint_id = sprint.id
        return result

    async def meeting(self, goals: str) -> dict[str, Any]:
        """The morning meeting: a Sprint Meeting of a single turn, saved to the backlog. The
        session stays in the history like any other. Questions the Master could not do without
        go to the inbox, because nobody is at the keyboard to answer them."""
        convs = Conversations(self.ctx)
        conv = convs.open(ConversationKind.MEETING)
        turn = await self.converse(conv, goals)
        if convs.board.require(conv.id).draft.items:
            result = await convs.commit(conv.id)
        else:
            convs.discard(conv.id)
            result = CommitResult()
        store = self.ctx.store
        created = [row for sid in result.created if (row := store.get_story(sid)) is not None]
        for q in turn.questions[:3]:
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
        self.ctx.emit(
            "meeting.done",
            agent=self.name,
            created=len(created),
            clarifications=len(turn.questions),
        )
        return {
            "stories": [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "epic": r["epic"],
                    "priority": to_scale(r["priority"]),
                }
                for r in created
            ],
            "clarifications": turn.questions,
        }

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
