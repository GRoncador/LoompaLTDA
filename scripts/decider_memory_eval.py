"""Would the decision model filter organizational memory well? (13.6, offline)

Uses the 20 questions of `memory_recall_eval.py`, whose right document is known: the hybrid recall
returns the top passages, the decision model scores each one's usefulness (the same question the
shadow asks in `LoompaAgent.precedents`), and the script counts, per confidence threshold, how often
the right document would be kept and how many wrong passages would be dropped.

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
from loompa.decider import JevDecider, useful_question
from loompa.memory import MemoryStore
from loompa.memory.embedders import FastEmbedEmbedder

sys.path.insert(0, str(Path(__file__).parent))
from memory_recall_eval import QUESTIONS  # noqa: E402

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


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
    rows = []  # (is_right, level, confidence)
    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryStore(Path(tmp) / "m.db", FastEmbedEmbedder(MODEL))
        mem.index_file(loompa / "constitution.md", kind="constitution", doc_id="constitution.md")
        mem.index_file(loompa / "learnings.md", kind="learning", doc_id="learnings.md")
        mem.index_directory(loompa / "decisions", kind="adr", relative_to=loompa)
        mem.index_directory(loompa / "specs", kind="spec", relative_to=loompa)
        for qn, (q, want) in enumerate(QUESTIONS):
            hits = mem.search(q, top_k=6)
            state = f"Task:\n{q}\n\nPassages:\n" + "\n\n".join(
                f"[{i}] ({h.chunk.kind}) {h.chunk.text.strip()[:500]}"
                for i, h in enumerate(hits, 1)
            )
            got = await jev.decide(
                state,
                {f"p{i}": useful_question(i) for i in range(1, len(hits) + 1)},
                purpose="eval",
            )
            if not got:
                continue
            for i, h in enumerate(hits, 1):
                a = got[f"p{i}"]
                p_useful = a.probabilities.get("2", 1.0 if a.value == 2 else 0.0)
                rank = 1 - i / 10  # the hybrid search's own order: earlier is better
                rows.append(
                    (h.chunk.doc_id.startswith(want), a.value, a.confidence, p_useful, rank, qn)
                )
    right = [r for r in rows if r[0]]
    # as a re-ranker: how often a right passage gets a higher P(useful) than a wrong one (AUC)
    pairs = [(a[3], b[3]) for a in rows if a[0] for b in rows if not b[0] and b[5] == a[5]]
    auc = sum(1.0 if x > y else 0.5 if x == y else 0.0 for x, y in pairs) / max(len(pairs), 1)
    print(
        f"P(useful) ranks a right passage above a wrong one of the same question: {auc:.0%} (50% = chance)"
    )
    base = [(a[4], b[4]) for a in rows if a[0] for b in rows if not b[0] and b[5] == a[5]]
    auc0 = sum(1.0 if x > y else 0.5 if x == y else 0.0 for x, y in base) / max(len(base), 1)
    print(f"the search's own order does it: {auc0:.0%}")
    wrong = [r for r in rows if not r[0]]
    print(f"{len(rows)} passages: {len(right)} from the right document, {len(wrong)} not")
    for level in (0, 1, 2):
        print(
            f"  level {level}: right {sum(r[1] == level for r in right)}, "
            f"wrong {sum(r[1] == level for r in wrong)}"
        )
    for t in (0.5, 0.7, 0.9):
        # `decide` would drop a passage scored "unrelated" (0) with confidence >= t
        dropped_right = sum(1 for r in right if r[1] == 0 and r[2] >= t)
        dropped_wrong = sum(1 for r in wrong if r[1] == 0 and r[2] >= t)
        print(
            f"  drop if level 0 and confidence >= {t}: right lost {dropped_right}/{len(right)}, "
            f"wrong dropped {dropped_wrong}/{len(wrong)}"
        )


if __name__ == "__main__":
    asyncio.run(main())
