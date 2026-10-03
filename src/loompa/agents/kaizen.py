"""Kaizen loop: every discovery becomes a learning entry, a backlog card and searchable memory.

Kaizen itself makes no model call: it collects learnings and technical debt and indexes them.
The one call on the way is the Product Owner's, which reads each finding before it becomes a
card (ADR-0017): nothing enters the backlog unread.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from loompa.agents.base import LoompaAgent
from loompa.agents.product_owner import ProductOwnerAgent
from loompa.backlog import TRIAGE_KEY, Triage
from loompa.engine.state import Stage, StoryState
from loompa.sprints import SprintBoard

KIND_LABEL = {
    "bug": "Bug colateral",
    "tech_debt": "Débito técnico",
    "opportunity": "Oportunidade",
    "architecture": "Inconsistência de arquitetura",
}


_LEADING_LABEL = re.compile(r"^\s*\[([^\]]{1,60})\]\s*")


def without_label(title: str) -> str:
    """The title with any kind label it already carries removed: the Product Owner sees the label
    and echoed it in its rewrite, and the card read "[Inconsistência de arquitetura]
    [Inconsistência de arquitetura] …" (contas Sprint 2, S-054)."""
    labels = {v.lower() for v in KIND_LABEL.values()} | {k.lower() for k in KIND_LABEL}
    while (m := _LEADING_LABEL.match(title)) and m.group(1).strip().lower() in labels:
        title = title[m.end() :]
    return title.strip()


class KaizenAgent(LoompaAgent):
    role = "kaizen"
    display = "Kaizen Loompa"

    async def capture(
        self, state: StoryState, items: list[dict[str, str]] | None = None
    ) -> list[str]:
        """File what this story found. The Product Owner reads every finding first (plan 10.1):
        it rewrites unclear wording, points a finding an open card already covers at that card,
        and slots each new card among the waiting ones. Still automatic and immediate, with no
        founder in the loop; when the model is unavailable the findings go in as written."""
        items = items if items is not None else state.learnings
        fresh = [
            item
            for item in items
            if not item.get("_captured") and str(item.get("title") or "").strip()
        ]
        if not fresh:
            return []
        po = ProductOwnerAgent(self.ctx)
        keyed = {f"F{i}": item for i, item in enumerate(fresh, 1)}
        verdicts = await po.triage_findings(
            state,
            [
                {
                    "key": key,
                    "label": KIND_LABEL.get(item.get("kind") or "opportunity", "Oportunidade"),
                    "title": str(item.get("title") or "").strip()[:140],
                    "detail": str(item.get("detail") or "").strip(),
                }
                for key, item in keyed.items()
            ],
        )
        filed: dict[str, str] = {}  # finding key -> card id, for findings that point at another
        created: list[str] = []
        lines = []
        for key, item in keyed.items():
            kind = item.get("kind") or "opportunity"
            label = KIND_LABEL.get(kind, kind)
            title = str(item.get("title") or "").strip()[:140]
            detail = str(item.get("detail") or "").strip()
            verdict = verdicts.get(key) or Triage(reviewed=bool(verdicts))
            covered = filed.get(verdict.duplicate_of, verdict.duplicate_of)
            if verdict.process_only:
                # tamagotchi SP-005: five research cards that only "verified and recorded the
                # closing" left eight cards asking to run `npm test` and attach the output
                self.ctx.emit(
                    "backlog.dropped",
                    story_id=state.story_id,
                    agent=po.name,
                    title=title,
                    reason="process_only",
                )
                self.ctx.store.add_learning(
                    story_id=state.story_id, kind=kind, title=title, detail=detail
                )
                lines.append(
                    f"- **{datetime.now(UTC).date().isoformat()} · {state.story_id} · {label}** — "
                    f"{title} _(não virou card: só processo, sem mudança no produto)_"
                )
                item["_captured"] = "1"
                continue
            if covered and covered not in keyed:
                self.ctx.emit(
                    "backlog.duplicate", story_id=covered, agent=po.name, title=title, judged=True
                )
                new_id = covered
            else:
                after = filed.get(verdict.after or "", verdict.after)
                text = (verdict.description or detail).strip()
                new_id = po.add_item(
                    f"[{label}] {without_label(verdict.title or title)}",
                    f"{text}\n\nDescoberto durante {state.story_id} ({state.title}).",
                    epic="kaizen",
                    priority=500 if kind != "bug" else 250,
                    origin="kaizen",
                    kind=verdict.kind or ("bugfix" if kind == "bug" else "feature"),
                    extra={
                        TRIAGE_KEY: verdict.record(
                            original=f"{title}\n{detail}".strip(),
                            rewritten=verdict.title not in ("", title)
                            or verdict.description not in ("", detail),
                        )
                    },
                    after=after if after not in keyed else None,
                ).story_id
            filed[key] = new_id
            self.ctx.store.add_learning(
                story_id=state.story_id,
                kind=kind,
                title=title,
                detail=detail,
                created_story_id=new_id,
            )
            lines.append(
                f"- **{datetime.now(UTC).date().isoformat()} · {state.story_id} · {label}** — {title}"
                + (f"\n  {detail}" if detail else "")
                + f" _(card {new_id})_"
            )
            self.ctx.emit(
                "kaizen.learning",
                story_id=state.story_id,
                agent=self.name,
                kind=kind,
                title=title,
                created_story_id=new_id,
            )
            item["_captured"] = "1"
            if new_id not in state.finding_cards:
                state.finding_cards.append(new_id)
            created.append(new_id)
        if lines:
            with self.ctx.factory.paths.learnings.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            self.ctx.memory.index_file(
                self.ctx.factory.paths.learnings, kind="learning", doc_id="learnings.md"
            )
        return created

    def suggested_cards(self, state: StoryState) -> list[dict]:
        """Backlog cards this story raised that the founder has not decided on yet: what a
        delivery (code or research) offers as independent decisions."""
        decided = {
            d.id
            for m in self.ctx.store.list_messages(self.ctx.slug)
            if m.story_id == state.story_id
            for d in m.decisions
            if d.chosen
        }
        board = SprintBoard(self.ctx.store, self.ctx.slug)
        cards = []
        for sid in state.finding_cards:
            row = self.ctx.store.get_story(sid)
            if not row or row["stage"] != Stage.BACKLOG or sid in decided:
                continue
            if board.sprint_of(sid) is not None:  # already planned into a sprint
                continue
            cards.append(
                {
                    "id": sid,
                    "title": row["title"],
                    "detail": (row["description"] or "").split("\n\n")[0],
                    "priority": row["priority"],
                }
            )
        return cards

    def record_resolution(self, state: StoryState, failure: str, fix: str) -> None:
        """Persist a past bug resolution so future workers can recall it."""
        title = f"Resolução em {state.story_id}: {state.title}"
        body = f"# {title}\n\nFalha (filtrada):\n{failure[:1500]}\n\nComo foi resolvido:\n{fix[:800]}\n"
        self.ctx.memory.upsert_document(
            f"resolution/{state.story_id}", body, kind="learning", title=title
        )
        self.ctx.store.add_learning(
            story_id=state.story_id, kind="resolution", title=title, detail=fix[:800]
        )
