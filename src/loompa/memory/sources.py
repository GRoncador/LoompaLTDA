"""What the factory decided and delivered, as memory documents (plan 13.3).

The specs and ADRs were indexed; the founder's answers were not. Why an option was chosen, what a
delivery did and how it was received, and what each sprint report concluded are precedents too:
the next spec or plan for a similar story should find them.
"""

from __future__ import annotations

from collections.abc import Iterable

from loompa.comms import FounderMessage, MessageKind

ANSWERED_KINDS = (MessageKind.DECISION, MessageKind.BLOCKED, MessageKind.DELIVERY)


def answer_document(m: FounderMessage) -> tuple[str, str, str, str] | None:
    """`(doc_id, kind, title, text)` for an answered message, or None. Deliveries are kind
    `delivery`, every other question kind `decision`."""
    if m.answer is None or m.kind not in ANSWERED_KINDS:
        return None
    labels = {o.key: o.label for o in m.options}
    chosen = labels.get(m.answer.option_key or "", m.answer.option_key or "")
    story = f"{m.story_id}: " if m.story_id else ""
    lines = [f"# {story}{m.title}", "", m.context.strip()]
    if m.impact.strip():
        lines += ["", m.impact.strip()]
    if m.options:
        lines += ["", "Opções: " + "; ".join(o.label for o in m.options)]
    if chosen:
        lines += ["", f"Escolha do Founder: {chosen}"]
    if (m.answer.text or "").strip():
        lines += [f"Palavras do Founder: {m.answer.text.strip()}"]
    for d in m.decisions:
        pick = m.answer.decisions.get(d.id) or d.chosen
        if pick:
            label = next((o.label for o in d.options if o.key == pick), pick)
            lines.append(f"Card {d.id} ({d.title}): {label}")
    kind = "delivery" if m.kind == MessageKind.DELIVERY else "decision"
    title = f"{'Entrega' if kind == 'delivery' else 'Decisão'} {m.story_id or ''}".strip()
    return f"inbox/{m.id}", kind, title, "\n".join(lines).strip()


def answered_documents(messages: Iterable[FounderMessage]) -> list[tuple[str, str, str, str]]:
    return [doc for m in messages if (doc := answer_document(m)) is not None]
