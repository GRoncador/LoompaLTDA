"""Writes a prompt's bench-approved final text (`candidates/<key>.final.txt`) into its constant.

The constant's string literal is located with `ast`, checked to hold today's text, and replaced by
a triple-quoted literal of the final text; the file is then re-parsed and the new value compared
with the measured text, so what reaches the code is exactly what the bench measured.

    uv run python docs/bench/adopt_final.py master.EXEC_SYSTEM [more keys]
    uv run python docs/bench/adopt_final.py --stage p0-agnostic master.EXEC_SYSTEM   # an earlier step
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from loompa.bench import registry

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = Path(__file__).parent / "candidates"


def adopt(key: str, stage: str = "final") -> str:
    prompt = registry.get(key)
    final = (CANDIDATES / f"{key}.{stage}.txt").read_text(encoding="utf-8")
    if final == prompt.template:
        return f"{key}: já igual"
    if '"""' in final or "\\" in final:
        raise SystemExit(f"{key}: o texto final precisa de escape; trocar à mão")
    path = ROOT / "src" / Path(*prompt.module.split(".")).with_suffix(".py")
    source = path.read_text(encoding="utf-8")
    node = next(
        n
        for n in ast.parse(source).body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == prompt.const for t in n.targets)
    )
    lines = source.splitlines(keepends=True)
    start = sum(len(x) for x in lines[: node.value.lineno - 1]) + node.value.col_offset
    end = sum(len(x) for x in lines[: node.value.end_lineno - 1]) + node.value.end_col_offset
    if ast.literal_eval(source[start:end]) != prompt.template:
        raise SystemExit(f"{key}: o literal no código não é o texto que a bancada conhece")
    new_source = source[:start] + '"""' + final + '"""' + source[end:]
    tree = ast.parse(new_source)
    value = next(
        n.value
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == prompt.const for t in n.targets)
    )
    if ast.literal_eval(value) != final:
        raise SystemExit(f"{key}: o literal novo não reproduz o texto medido")
    path.write_text(new_source, encoding="utf-8")
    return f"{key}: {len(prompt.template)} → {len(final)} caracteres em {path.relative_to(ROOT)}"


if __name__ == "__main__":
    args = sys.argv[1:]
    stage = "final"
    if args[:1] == ["--stage"]:
        stage, args = args[1], args[2:]
    for key in args:
        print(adopt(key, stage))
