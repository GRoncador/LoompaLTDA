"""Would the decision model filter or re-order organizational memory well? (13.6, offline)

Uses the 20 questions of `memory_recall_eval.py`, whose right document is known: the hybrid recall
returns the top passages, and the decision model judges each one against the question in its own
request (the shape the shadow uses in `LoompaAgent.precedents`, after TypeSafe's re-ranking
cookbook): P(relevant) and P(applies). The script reports how each orders the passages against
the search's own order, how often the right document survives keeping the best k, and what
dropping the off-task ones would cost.

    set -a; source ~/.loompa/secrets.env; set +a
    uv run python scripts/decider_memory_eval.py "/Users/gustavo/Projects/Tamagotchi retrô"
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from loompa.config import default_config
from loompa.decider import PASSAGE_QUESTIONS, RELEVANT_MIN, JevDecider, p_true, passage_state
from loompa.memory import MemoryStore
from loompa.memory.embedders import FastEmbedEmbedder

sys.path.insert(0, str(Path(__file__).parent))
from memory_recall_eval import QUESTIONS  # noqa: E402

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def auc(rows: list[dict], key: str) -> float:
    """How often a right passage outranks a wrong one of the same question (50% = chance)."""
    pairs = [
        (a[key], b[key])
        for a in rows
        if a["right"]
        for b in rows
        if not b["right"] and b["q"] == a["q"]
    ]
    return sum(1.0 if x > y else 0.5 if x == y else 0.0 for x, y in pairs) / max(len(pairs), 1)


async def main() -> None:
    root = Path(sys.argv[1])
    loompa = root / ".loompa"
    store: dict = {}
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

    async def judge(q: str, h):  # noqa: ANN001
        c = h.chunk
        async with sem:
            return await jev.decide(
                passage_state(q, c.kind, c.title, c.text), PASSAGE_QUESTIONS, purpose="eval"
            )

    rows: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryStore(Path(tmp) / "m.db", FastEmbedEmbedder(MODEL))
        mem.index_file(loompa / "constitution.md", kind="constitution", doc_id="constitution.md")
        mem.index_file(loompa / "learnings.md", kind="learning", doc_id="learnings.md")
        mem.index_directory(loompa / "decisions", kind="adr", relative_to=loompa)
        mem.index_directory(loompa / "specs", kind="spec", relative_to=loompa)
        for qn, (q, want) in enumerate(QUESTIONS):
            hits = mem.search(q, top_k=6)
            got = await asyncio.gather(*(judge(q, h) for h in hits))
            for i, (h, g) in enumerate(zip(hits, got, strict=True), 1):
                if not g:
                    continue
                rows.append(
                    {
                        "q": qn,
                        "right": h.chunk.doc_id.startswith(want),
                        "search": 1 - i / 10,  # the hybrid search's own order: earlier is better
                        "applies": p_true(g["applies"]),
                        "relevant": p_true(g["relevant"]),
                    }
                )
    for r in rows:
        r["both"] = r["applies"] * r["relevant"]
    right = [r for r in rows if r["right"]]
    wrong = [r for r in rows if not r["right"]]
    print(f"{len(rows)} passages: {len(right)} from the right document, {len(wrong)} not")
    keys = ("search", "applies", "relevant", "both")
    print("right passage above a wrong one of the same question (50% = chance):")
    for k in keys:
        print(f"  {k:<9}{auc(rows, k):.0%}")
    by_q: dict[int, list] = {}
    for r in rows:
        by_q.setdefault(r["q"], []).append(r)
    for n in (1, 2, 3):
        kept = {
            k: sum(
                any(r["right"] for r in sorted(g, key=lambda r: -r[k])[:n]) for g in by_q.values()
            )
            for k in keys
        }
        print(
            f"keep the best {n}: right document kept in "
            + ", ".join(f"{kept[k]}/{len(by_q)} by {k}" for k in keys)
        )
    for t in (0.2, 0.3, RELEVANT_MIN):
        lost = sum(r["relevant"] < t for r in right)
        dropped = sum(r["relevant"] < t for r in wrong)
        print(
            f"drop if P(relevant) < {t}: right lost {lost}/{len(right)}, "
            f"wrong dropped {dropped}/{len(wrong)}"
        )


if __name__ == "__main__":
    asyncio.run(main())
