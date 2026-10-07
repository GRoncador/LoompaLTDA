"""Two versions of the same cases for the glossary question of 14.3 (o5): does telling the roles to
use the glossary's terms change what they write?

The company documents reach the prompts only since 13.2, so few recorded calls carry them. Each case
here gets today's company block for its role where `grounding()` puts it (right after the
constitution excerpt), in two versions (a case recorded after 13.2 keeps the block it has):
- `gloss0-<factory>`: the block as the code wrote it before the change (plain heading);
- `gloss1-<factory>`: the glossary heading followed by the instruction to use its terms.

A case whose constitution excerpt cannot be found whole is left out and counted.

    uv run python docs/bench/glossary_cases.py
    loompa prompts compare product.SYSTEM --mode final --from gloss0-tamagotchi-retro \\
        --b-from gloss1-tamagotchi-retro --model-a ... --model-b ...
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict

from loompa.bench import cases, registry
from loompa.cli.main import resolve_factory
from loompa.company import company_block

FACTORY = "tamagotchi-retro"
PROMPTS = ("product.SYSTEM", "product_owner.REVIEW_SYSTEM", "master.CLASSIFY_SYSTEM")
EXCERPT = "## Constitution (excerpt)\n"
OLD_HEADING = "### Domain glossary (glossary.md)\n"
NEW_HEADING = (
    "### Domain glossary (glossary.md) — name things of the product with these terms, in the "
    "meaning given\n"
)
# The glossary's own "never" words (tamagotchi): the measure of whether its terms were used.
AVOIDED = re.compile(r"\b(morte|morre\w*|morr\w+|morto|personagem|avatar|assinatura)\b", re.I)


def with_block(text: str, constitution: str, block: str) -> str | None:
    if OLD_HEADING in text:  # recorded after 13.2: the block is there as the code wrote it
        return text
    at = text.find(EXCERPT)
    if at < 0:
        return None
    start = at + len(EXCERPT)
    common = 0
    for a, b in zip(text[start:], constitution, strict=False):
        if a != b:
            break
        common += 1
    if not text.startswith("\n\n", start + common) and start + common != len(text):
        return None  # the excerpt changed since it was recorded: no safe place for the block
    return text[: start + common] + "\n\n" + block + text[start + common :]


def main() -> None:
    f = resolve_factory(FACTORY)
    constitution = f.constitution_text()
    for key in PROMPTS:
        role = registry.get(key).role
        block = company_block(f.paths.loompa, role)
        if OLD_HEADING not in block:
            print(f"{key}: o papel {role} não lê o glossário")
            continue
        out0, out1, skipped = [], [], 0
        for case in cases.load(key, factories=[FACTORY]):
            # the block sits in the user message, or after the prompt in the system one (Worker)
            index = next(
                (
                    i
                    for i, m in enumerate(case.messages)
                    if m.get("role") in ("user", "system")
                    and EXCERPT in str(m.get("content") or "")
                ),
                None,
            )
            if index is None:
                skipped += 1
                continue
            text = str(case.messages[index]["content"])
            v0 = with_block(text, constitution, block)
            if v0 is None:
                skipped += 1
                continue
            v1 = v0.replace(OLD_HEADING, NEW_HEADING, 1)
            for out, version in ((out0, v0), (out1, v1)):
                c = copy.deepcopy(case)
                c.messages[index]["content"] = version
                out.append(c)
        for name, out in ((f"gloss0-{FACTORY}", out0), (f"gloss1-{FACTORY}", out1)):
            folder = cases.cases_dir() / name
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / f"{key}.jsonl").open("w", encoding="utf-8") as fh:
                for c in out:
                    fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
        print(f"{key}: {len(out0)} casos, {skipped} deixados de fora")


if __name__ == "__main__":
    main()
