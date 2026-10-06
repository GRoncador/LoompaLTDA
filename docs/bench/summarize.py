"""One table of the 14.3 bench round: every experiment's report, plus what each decision changed.

    uv run python docs/bench/summarize.py ~/.loompa/bench/logs/r2
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path


def report_of(log: Path) -> dict | None:
    text = re.sub(r"\x1b\[[0-9;]*m", "", log.read_text(encoding="utf-8"))
    m = re.search(r"Relatório completo:\s*(\S+\.json)", text.replace("\n", ""))
    return json.loads(Path(m.group(1)).read_text(encoding="utf-8")) if m else None


def answers(report: dict, variant: str) -> list[dict]:
    out = []
    for r in report["runs"]:
        if r["variant"] != variant or not r["text"]:
            continue
        t = r["text"]
        try:
            out.append(json.loads(t[t.find("{") : t.rfind("}") + 1]))
        except ValueError:
            out.append({})
    return out


def extra(report: dict, variant: str) -> str:
    """The number that tells what the change does, per prompt."""
    p, data = report["prompt"], answers(report, variant)
    if p == "product_owner.REVIEW_SYSTEM":
        return f"aprovou {sum(bool(d.get('approved')) for d in data)}/{len(data)}"
    if p == "inspector.JUDGE_SYSTEM":
        crit = [c for d in data for c in d.get("criteria") or [] if isinstance(c, dict)]
        return (
            f"achados {sum(len(d.get('findings') or []) for d in data)}, "
            f"critérios reprovados {sum(1 for c in crit if c.get('pass') is False)}"
        )
    if p.startswith("architect.") and "SYSTEM" in p:
        tasks = [len(d.get("tasks") or []) for d in data if d]
        blockers = sum(1 for d in data if str(d.get("blocker") or "").strip())
        return f"tarefas/plano {sum(tasks) / max(len(tasks), 1):.1f}, blocker {blockers}"
    if p == "product.SYSTEM":
        return f"needs_decision {sum(bool(d.get('needs_decision')) for d in data)}/{len(data)}"
    if p == "worker.SYSTEM":
        first = Counter(
            (r["tool_calls"] or ["(texto)"])[0] for r in report["runs"] if r["variant"] == variant
        )
        return ", ".join(f"{k} {v}" for k, v in first.most_common(3))
    if p == "product_owner.PROPOSE_SYSTEM":
        picks = [p for d in data for p in d.get("picks") or [] if isinstance(p, dict)]
        return f"no sprint {sum(bool(p.get('in_sprint')) for p in picks)}/{len(picks)}"
    return ""


def main() -> None:
    folder = Path(sys.argv[1]).expanduser()
    rows = []
    for log in sorted(folder.glob("*.log")):
        rep = report_of(log)
        if rep is None:
            rows.append(f"| {log.stem} | (sem relatório) |")
            continue
        s = rep["summary"]
        a, b = s["A"], s["B"]
        rows.append(
            f"| {log.stem} | {a['n']} | {a['judge_wins']}×{b['judge_wins']} (empates "
            f"{a['judge_ties']}) | {a['judge_score']}→{b['judge_score']} | "
            f"{a['contract_ok']}→{b['contract_ok']} | "
            f"{a['founder_ok'] if a['founder_ok'] is not None else '—'}→"
            f"{b['founder_ok'] if b['founder_ok'] is not None else '—'} | "
            f"{a['agrees_recorded'] if a['agrees_recorded'] is not None else '—'}→"
            f"{b['agrees_recorded'] if b['agrees_recorded'] is not None else '—'} | "
            f"{a['avg_in']}/{a['avg_out']}→{b['avg_in']}/{b['avg_out']} | "
            f"{extra(rep, 'A')} → {extra(rep, 'B')} |"
        )
    print(
        "| experimento | casos | juiz A×B | nota | contrato % | texto Founder % | "
        "= gravada % | tokens in/out | o que mudou |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    print("\n".join(rows))


if __name__ == "__main__":
    main()
