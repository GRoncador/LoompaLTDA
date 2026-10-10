"""Kaizen loop: every discovery becomes a learning entry, a backlog card and searchable memory.

Kaizen itself makes no model call: it collects learnings and technical debt and indexes them.
The one call on the way is the Product Owner's, which reads each finding before it becomes a
card (ADR-0017): nothing enters the backlog unread.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

from loompa.agents.base import _SHADOW, LoompaAgent
from loompa.agents.product_owner import ProductOwnerAgent
from loompa.backlog import TRIAGE_KEY, Triage
from loompa.comms.executive import clip
from loompa.decider import (
    FINDING_QUESTIONS,
    NO_CARD,
    SAME_PROBLEM,
    YES,
    build_decider,
    finding_state,
    p_true,
    pair_state,
    process_only,
)
from loompa.engine.state import TERMINAL, Stage, StoryState
from loompa.sprints import SprintBoard, SprintStatus

KIND_LABEL = {
    "bug": "Bug colateral",
    "tech_debt": "Débito técnico",
    "opportunity": "Oportunidade",
    "architecture": "Inconsistência de arquitetura",
}


# "[Oportunidade] …", and the rewrite that lost its "[": "Oportunidade] …" (tamagotchi S-100)
_LEADING_LABEL = re.compile(r"^\s*\[?([^\]\[]{1,60})\]\s*")
# A finding whose title asks for the factory's records: the one fix the factory never delivers
# (tamagotchi S-098: "criar os dois documentos em .loompa/decisions/", ADR-0037 §8)
_RECORDS_TITLE = re.compile(r"(?<![\w/.-])\.loompa/(?:decisions|specs|learnings|constitution)")


def without_label(title: str) -> str:
    """The title with any kind label it already carries removed: the Product Owner sees the label
    and echoed it in its rewrite, and the card read "[Inconsistência de arquitetura]
    [Inconsistência de arquitetura] …" (contas Sprint 2, S-054)."""
    labels = {v.lower() for v in KIND_LABEL.values()} | {k.lower() for k in KIND_LABEL}
    while (m := _LEADING_LABEL.match(title)) and m.group(1).strip().lower() in labels:
        title = title[m.end() :]
    return title.strip()


def _never_a_card(item: dict) -> bool:
    """A finding about how the story went, or one asking for the factory's records."""
    return bool(item.get("process")) or bool(_RECORDS_TITLE.search(str(item.get("title") or "")))


SHADOW_CARDS = 8  # the open cards the shadow's duplicate question chooses among


def _nearest(ctx, text: str, cards: dict[str, str], k: int) -> dict[str, str]:
    """The `k` open cards whose titles are closest to `text` (the memory's embedder): a state of
    eight short titles, not the whole backlog — the decision model gets worse with noise."""
    if not cards:
        return {}
    ids = list(cards)
    try:
        vecs = ctx.memory.embedder.embed([text, *(cards[i] for i in ids)])
    except Exception:  # noqa: BLE001 - shadow only
        return {}
    scores = vecs[1:] @ vecs[0]
    order = sorted(range(len(ids)), key=lambda n: -float(scores[n]))[:k]
    return {ids[n]: cards[ids[n]] for n in order}


def _normal(title: str) -> str:
    return " ".join(without_label(title).lower().split())


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
        # tamagotchi S-048: four tasks of one story each reported the same stale comment; the
        # Product Owner pointed all four at S-051, yet every round paid a triage call and wrote
        # another learning line (plan 12.2). A finding this story already filed is folded in.
        own = self._filed_by(state.story_id)
        created: list[str] = []
        for item in list(fresh):
            card = own.get(_normal(str(item.get("title") or "")))
            if card:
                self._fold_repeat(state, item, card)
                fresh.remove(item)
                created.append(card)
        if not fresh:
            return created
        po = ProductOwnerAgent(self.ctx)
        keyed = {f"F{i}": item for i, item in enumerate(fresh, 1)}
        verdicts = await po.triage_findings(
            state,
            [
                {
                    "key": key,
                    "label": KIND_LABEL.get(item.get("kind") or "opportunity", "Oportunidade"),
                    "title": clip(str(item.get("title") or ""), 140),
                    "detail": str(item.get("detail") or "").strip(),
                }
                for key, item in keyed.items()
                if not _never_a_card(item)  # known without asking (ADR-0037 §8)
            ],
        )
        self._shadow_triage(state, keyed, verdicts)
        filed: dict[str, str] = {}  # finding key -> card id, for findings that point at another
        lines = []
        for key, item in keyed.items():
            kind = item.get("kind") or "opportunity"
            label = KIND_LABEL.get(kind, kind)
            title = clip(str(item.get("title") or ""), 140)
            detail = str(item.get("detail") or "").strip()
            verdict = verdicts.get(key) or Triage(reviewed=bool(verdicts))
            covered = filed.get(verdict.duplicate_of, verdict.duplicate_of)
            # how the story went (a review's reservations) or the factory's own records: never a
            # card about the product, whatever the triage said (ADR-0037 §8)
            if verdict.process_only or _never_a_card(item) or _RECORDS_TITLE.search(verdict.title):
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
            if covered and covered not in keyed and covered in own.values():
                filed[key] = covered
                self._fold_repeat(state, item, covered)
                created.append(covered)
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
                    judged=verdict.reviewed,  # the PO read it: its "not a duplicate" stands
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

    def _shadow_triage(self, state: StoryState, keyed: dict, verdicts: dict) -> None:
        """13.6 in shadow: the decision model answers the triage's two narrow questions — process
        only? the same problem as which nearby open card? — beside the Product Owner, whose verdict stands;
        both go to the trace (`decider.shadow`) to be compared. Fire and forget."""
        decider = build_decider(self.ctx)
        if decider is None or not keyed or not verdicts:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        cards = {
            r["id"]: r["title"]
            for r in self.ctx.store.list_stories(self.ctx.slug)
            if r["stage"] not in TERMINAL and r["id"] != state.story_id
        }

        async def one(key: str, item: dict) -> None:
            text = f"{item.get('title') or ''}\n{str(item.get('detail') or '')[:800]}"
            near = _nearest(self.ctx, text, cards, SHADOW_CARDS)
            sid = state.story_id
            # one request about the finding alone, one per nearby card: no card is an option
            # beside the others, so their order cannot lean the answer (ADR-0032)
            asked = await asyncio.gather(
                decider.decide(
                    finding_state(text), FINDING_QUESTIONS, purpose="kaizen_triage", story_id=sid
                ),
                *(
                    decider.decide(
                        pair_state(text, cid, title),
                        {"same": SAME_PROBLEM},
                        purpose="kaizen_triage",
                        story_id=sid,
                    )
                    for cid, title in near.items()
                ),
            )
            finding, same = asked[0], asked[1:]
            if not finding or not all(same):
                return
            p_same = {cid: p_true(g["same"]) for cid, g in zip(near, same, strict=True)}
            best = max(p_same, key=p_same.get) if p_same else NO_CARD
            dup = best if best != NO_CARD and p_same[best] > YES else NO_CARD
            po = verdicts.get(key)
            self.ctx.emit(
                "decider.shadow",
                story_id=sid,
                purpose="kaizen_triage",
                finding=str(item.get("title") or "")[:140],
                process_only=[
                    process_only(finding),
                    round(p_true(finding["asks_process"]), 3),
                    round(p_true(finding["asks_change"]), 3),
                ],
                duplicate=[dup, round(p_same.get(best, 0.0), 3)],
                p_same={cid: round(p, 3) for cid, p in p_same.items()},
                po_process_only=bool(po and po.process_only),
                po_duplicate=(po.duplicate_of if po else "") or NO_CARD,
            )

        for key, item in keyed.items():
            task = loop.create_task(one(key, item))
            _SHADOW.add(task)
            task.add_done_callback(_SHADOW.discard)

    def _filed_by(self, story_id: str) -> dict[str, str]:
        """The findings this story already turned into (or pointed at) a card: title -> card."""
        return {
            _normal(row["title"]): row["created_story_id"]
            for row in self.ctx.store.list_learnings()
            if row["story_id"] == story_id and row["created_story_id"]
        }

    def _fold_repeat(self, state: StoryState, item: dict[str, str], card: str) -> None:
        """A finding this story already filed: no second learning, line or card."""
        self.ctx.emit(
            "backlog.duplicate",
            story_id=card,
            agent=self.name,
            title=clip(str(item.get("title") or ""), 140),
            repeat=True,
        )
        item["_captured"] = "1"
        if card not in state.finding_cards:
            state.finding_cards.append(card)

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
        own = board.sprint_of(state.story_id)
        running = own.id if own is not None and own.status == SprintStatus.RUNNING else ""
        # findings a delivery already put into this sprint count against its room (ADR-0036 §7)
        room = 0
        if own is not None and running:
            decided_ids = self.ctx.store.story_ids_with_event(self.ctx.slug, "finding.decided")
            joined = [
                sid
                for sid in own.story_ids
                if sid in decided_ids
                and (self.ctx.store.get_story(sid) or {}).get("origin") == "kaizen"
            ]
            room = max(0, self.ctx.config.schedule.kaizen_sprint_cap - len(joined))
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
                    "sprint": running,  # the sprint it may join; never the next one
                }
            )
        # the sprint is offered to the most urgent ones only, as many as there is room for
        for n, card in enumerate(sorted(cards, key=lambda c: int(c["priority"] or 500))):
            if n >= room:
                card["sprint"] = ""
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
