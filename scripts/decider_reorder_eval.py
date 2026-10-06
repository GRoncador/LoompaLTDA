"""Plan 14.5: what the precedents filter would change in the answers of real spec and plan calls.

Each real call (from the bench's cases) is replayed twice with today's prompt and the same model:
A with the precedents block as it was pushed, B with the block Jev would give in `decide` mode —
passages under P(relevant) 0.3 left out, the rest ordered by P(relevant). The prompt text is the
same; only that block of the material differs. Each pair of answers goes to the bench's blind
judge, and the checks in code run on both.

    set -a; source ~/.loompa/secrets.env; set +a
    uv run python scripts/decider_reorder_eval.py [limit] > out.json
"""

from __future__ import annotations

import asyncio
import copy
import json
import random
import re
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import decider_shadow_eval as dse  # noqa: E402

from loompa.bench import cases, registry  # noqa: E402
from loompa.bench.compare import Variant, judge, pinned, replay  # noqa: E402
from loompa.config import default_config  # noqa: E402
from loompa.config.secrets import Secrets  # noqa: E402
from loompa.decider import PASSAGE_QUESTIONS, RELEVANT_MIN, p_true, passage_state  # noqa: E402
from loompa.llm.router import ModelRouter  # noqa: E402

MODEL = "openrouter:xiaomi/mimo-v2.6-flash"
JUDGE = "openrouter:z-ai/glm-5.3-flash"


def split_block(user: str) -> tuple[str, str, str, list[tuple[re.Match, str]]] | None:
    at = user.find(dse.BLOCK)
    if at < 0:
        return None
    rest = user[at:]
    end = re.search(r"\n## (?!#)", rest[len(dse.BLOCK) :])
    block = rest[: len(dse.BLOCK) + end.start()] if end else rest
    after = rest[len(block) :]
    marks = list(dse.PASSAGE.finditer(block))
    parts = [
        (m, block[m.start() : marks[i + 1].start() if i + 1 < len(marks) else len(block)])
        for i, m in enumerate(marks)
    ]
    return user[:at], block, after, parts


async def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    asker = dse.Asker()
    secrets = Secrets.load(None)
    router = ModelRouter(pinned(default_config(), MODEL), secrets=secrets)
    jrouter = ModelRouter(pinned(default_config(), JUDGE), secrets=secrets)
    pool = [
        c
        for p in ("product.SYSTEM", "architect.SYSTEM")
        for c in cases.load(p, mode="final")
        if split_block(str(c.messages[1].get("content") or ""))
    ]
    random.Random(dse.SEED).shuffle(pool)
    sem = asyncio.Semaphore(4)
    rows: list[dict] = []

    async def one(case) -> None:  # noqa: ANN001
        user = str(case.messages[1]["content"])
        head, block, after, parts = split_block(user)
        task = head.strip()
        answers = await asyncio.gather(
            *(
                asker.jev_answer(
                    passage_state(task, m.group(1), m.group(2), text[m.end() - m.start() :]),
                    PASSAGE_QUESTIONS,
                )
                for m, text in parts
            )
        )
        rel = [p_true(a["relevant"]) if a else 0.5 for a in answers]
        keep = sorted(
            (i for i in range(len(parts)) if rel[i] >= RELEVANT_MIN), key=lambda i: -rel[i]
        )
        if keep == list(range(len(parts))):
            rows.append({"case": case.id, "changed": False, "p": rel})
            return  # Jev would leave this block as it is: nothing to compare
        new_block = (
            dse.BLOCK
            + block[len(dse.BLOCK) : block.find("\n", len(dse.BLOCK))]
            + "\n\n"
            + "\n".join(parts[i][1].rstrip() + "\n" for i in keep)
            if keep
            else ""
        )
        mod = copy.deepcopy(case)
        mod.messages[1]["content"] = head + new_block + after
        prompt = registry.get(case.prompt)
        async with sem:
            ra, rb = await asyncio.gather(
                replay(router, prompt, case, Variant("A")),
                replay(router, prompt, mod, Variant("B")),
            )
            verdict = await judge(jrouter, prompt, case, ra, rb)
        rows.append(
            {
                "case": case.id,
                "prompt": case.prompt,
                "changed": True,
                "p": [round(x, 3) for x in rel],
                "kept": keep,
                "dropped_chars": len(block) - len(new_block),
                "a": asdict(ra),
                "b": asdict(rb),
                "verdict": asdict(verdict),
            }
        )
        dse.log(f"{len(rows)} {case.id}: {verdict.winner} ({verdict.reason[:100]})")

    await asyncio.gather(*(one(c) for c in pool[:limit]))
    changed = [r for r in rows if r["changed"]]
    wins = {
        k: sum(1 for r in changed if r["verdict"]["winner"] == k)
        for k in ("A", "B", "tie", "error")
    }
    summary = {
        "calls": len(rows),
        "block_changed": len(changed),
        "judge": wins,
        "contract_ok": {
            v: sum(1 for r in changed if r[v.lower()]["checked"].get("contract_ok"))
            for v in ("A", "B")
        },
        "avg_input_tokens": {
            v: int(sum(r[v.lower()]["input_tokens"] for r in changed) / max(len(changed), 1))
            for v in ("A", "B")
        },
        "cost_usd": {
            v: round(sum(r[v.lower()]["cost_usd"] for r in changed), 4) for v in ("A", "B")
        },
        "passages_dropped": sum(len(r["p"]) - len(r["kept"]) for r in changed),
        "passages_total": sum(len(r["p"]) for r in rows),
    }
    dse.log(json.dumps(summary, indent=1))
    print(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
