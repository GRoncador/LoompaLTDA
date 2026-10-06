"""The Inspector's bench cases as they read after 9667287: the whole spec instead of its head.

Recorded judge calls carry the first 3000 characters of the spec ("## Spec excerpt"), so a
candidate that judges scope (P3) would be measured on specs whose out-of-scope section is missing.
This rewrites each case's spec block the way the Inspector builds it now, from the spec file in
the factory, and saves the cases under `cases/fullspec-<factory>/`. A case whose spec changed
since the recording (its head no longer matches) is left out and counted.

    uv run python docs/bench/fullspec_cases.py
    loompa prompts compare inspector.JUDGE_SYSTEM --from fullspec-contas --from fullspec-tamagotchi-retro ...
"""

from __future__ import annotations

import json
from dataclasses import asdict

from loompa.bench import cases
from loompa.cli.main import resolve_factory
from loompa.speckit import story_dir
from loompa.speckit.artifacts import SPEC_FOR_JUDGING, SPEC_JUDGE_ROOM, fit_sections

PROMPT = "inspector.JUDGE_SYSTEM"
OLD = "## Spec excerpt\n"
NEW = "## Spec (whole sections; the criteria above are the current ones)\n"


def main() -> None:
    for slug in ("contas", "tamagotchi-retro"):
        root = resolve_factory(slug).root
        out, skipped = [], 0
        for case in cases.load(PROMPT, factories=[slug]):
            user = str(case.messages[1].get("content") or "")
            spec_file = story_dir(root, case.story).spec
            at = user.find(OLD)
            if at < 0 or not spec_file.is_file():
                skipped += 1
                continue
            spec = spec_file.read_text(encoding="utf-8")
            head = spec[:3000]
            start = at + len(OLD)
            if user[start : start + len(head)] != head:
                skipped += 1
                continue
            whole = fit_sections(spec, SPEC_JUDGE_ROOM, SPEC_FOR_JUDGING)
            case.messages[1]["content"] = user[:at] + NEW + whole + user[start + len(head) :]
            out.append(case)
        folder = cases.cases_dir() / f"fullspec-{slug}"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{PROMPT}.jsonl").open("w", encoding="utf-8") as fh:
            for case in out:
                fh.write(json.dumps(asdict(case), ensure_ascii=False) + "\n")
        print(f"{slug}: {len(out)} casos reescritos, {skipped} deixados de fora")


if __name__ == "__main__":
    main()
