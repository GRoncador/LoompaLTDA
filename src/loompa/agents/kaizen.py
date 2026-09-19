"""Kaizen loop: every discovery becomes a learning entry, a backlog card and searchable memory.

The card is proposed to the Product Owner, the only writer of the backlog: it lands as a new card
or, when an open card already says the same, points at that one. Either way the story that raised
it remembers the card (`finding_cards`), so the delivery can ask the founder what to do with it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loompa.agents.base import LoompaAgent
from loompa.agents.product_owner import ProductOwnerAgent
from loompa.engine.state import StoryState

KIND_LABEL = {
    "bug": "Bug colateral",
    "tech_debt": "Débito técnico",
    "opportunity": "Oportunidade",
    "architecture": "Inconsistência de arquitetura",
}


class KaizenAgent(LoompaAgent):
    role = "kaizen"
    display = "Kaizen Loompa"

    def capture(self, state: StoryState, items: list[dict[str, str]] | None = None) -> list[str]:
        items = items if items is not None else state.learnings
        created: list[str] = []
        if not items:
            return created
        path = self.ctx.factory.paths.learnings
        lines = []
        for item in items:
            if item.get("_captured"):
                continue
            kind = item.get("kind") or "opportunity"
            title = str(item.get("title") or "").strip()[:140]
            detail = str(item.get("detail") or "").strip()
            if not title:
                continue
            added = ProductOwnerAgent(self.ctx).add_item(
                f"[{KIND_LABEL.get(kind, kind)}] {title}",
                f"{detail}\n\nDescoberto durante {state.story_id} ({state.title}).",
                epic="kaizen",
                priority=500 if kind != "bug" else 250,
                origin="kaizen",
            )
            new_id = added.story_id
            self.ctx.store.add_learning(
                story_id=state.story_id,
                kind=kind,
                title=title,
                detail=detail,
                created_story_id=new_id,
            )
            lines.append(
                f"- **{datetime.now(UTC).date().isoformat()} · {state.story_id} · {KIND_LABEL.get(kind, kind)}** — {title}"
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
            with path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            self.ctx.memory.index_file(path, kind="learning", doc_id="learnings.md")
        return created

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
