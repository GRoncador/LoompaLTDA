"""Offline check of the decision model against what the Product Owner already decided (13.6).

Every Kaizen finding the PO triaged is a labelled pair: pointed at a card that existed before
it (the PO said "duplicate") or turned into its own card (the PO said "new"; paired with the
most similar older cards, so the negatives are not trivial). Jev answers "same problem?" for
each pair; the agreement with the PO is reported per confidence band, in pt-BR as the cards are.

    set -a; source ~/.loompa/secrets.env; set +a
    uv run python scripts/decider_offline_eval.py ~/Projects/contas "~/Projects/Tamagotchi retrô"

Costs about US$ 0.00002 per pair. `SINCE=2026-10-03` keeps only findings the Product Owner
triaged by meaning (10.1); older links came from title matching and are noisy labels.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from loompa.config import default_config
from loompa.decider import JevDecider, Question
from loompa.memory.embedders import HashEmbedder

SAME = Question(
    "noul",
    "Do the finding and the card describe the same problem, so that doing the card would also "
    "settle the finding? Judge by meaning, not by wording.",
    {
        "true": "Same defect or task: one card is enough for both.",
        "false": "Different problems, or the finding needs work the card does not cover.",
    },
)
BANDS = ((0.5, 0.7), (0.7, 0.9), (0.9, 1.01))
HARD_NEGATIVES = 2


def pairs(root: Path, since: str = "") -> list[tuple[str, str, bool]]:
    db = sqlite3.connect(root / ".loompa" / "state.db")
    cards = {
        r[0]: (r[1], r[2] or "", r[3])
        for r in db.execute("SELECT id, title, description, created_at FROM stories")
    }
    emb = HashEmbedder()
    out = []
    for title, detail, card, at in db.execute(
        "SELECT title, detail, created_story_id, created_at FROM learnings "
        "WHERE created_story_id IS NOT NULL AND created_story_id != ''"
    ):
        if card not in cards or at < since:
            continue
        finding = f"{title}\n{(detail or '')[:500]}"
        c_title, c_desc, c_at = cards[card]
        if c_at < at[:19]:  # the card existed before: the PO judged it a duplicate
            out.append((finding, f"{c_title}\n{c_desc[:500]}", True))
            continue
        older = [(cid, v) for cid, v in cards.items() if v[2] < c_at and cid != card]
        if not older:
            continue
        vecs = emb.embed([finding] + [f"{v[0]}\n{v[1][:300]}" for _, v in older])
        sims = vecs[1:] @ vecs[0]
        for i in np.argsort(-sims)[:HARD_NEGATIVES]:
            v = older[i][1]
            out.append((finding, f"{v[0]}\n{v[1][:500]}", False))
    return out


async def main() -> None:
    since = os.environ.get("SINCE", "")  # e.g. 2026-10-03: only the PO's triage era (10.1)
    data = [p for root in sys.argv[1:] for p in pairs(Path(root).expanduser(), since)]
    store = {}
    ctx = SimpleNamespace(
        config=default_config(),
        secrets={},
        store=SimpleNamespace(get=store.get, set=store.__setitem__),
        tracker=SimpleNamespace(record=lambda r: None),
        emit=lambda *a, **k: None,
        inbox=lambda m: None,
    )
    jev = JevDecider(ctx)
    sem = asyncio.Semaphore(8)

    async def ask(finding: str, card: str):
        async with sem:
            state = f"Finding:\n{finding}\n\nCard:\n{card}"
            return await jev.decide(state, {"same": SAME}, purpose="offline")

    answers = await asyncio.gather(*(ask(f, c) for f, c, _ in data))
    rows = [(a["same"], want) for a, (_, _, want) in zip(answers, data) if a]
    print(f"{len(rows)} pairs ({sum(w for _, w in rows)} duplicates by the PO), "
          f"{len(data) - len(rows)} unanswered")
    agree = sum(a.value == w for a, w in rows)
    print(f"overall agreement: {agree}/{len(rows)} = {agree / max(len(rows), 1):.0%}")
    for lo, hi in BANDS:
        band = [(a, w) for a, w in rows if lo <= a.confidence < hi]
        ok = sum(a.value == w for a, w in band)
        dup = [(a, w) for a, w in band if w]
        print(
            f"confidence {lo:.1f}-{min(hi, 1):.1f}: {len(band)} pairs, agreement "
            f"{ok}/{len(band)} = {ok / max(len(band), 1):.0%}; duplicates found "
            f"{sum(a.value for a, _ in dup)}/{len(dup)}"
        )


if __name__ == "__main__":
    asyncio.run(main())
