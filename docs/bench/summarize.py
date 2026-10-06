"""One table of the 14.3 bench round: every experiment's report, plus what each decision changed.

    uv run python docs/bench/summarize.py ~/.loompa/bench/logs/r2
"""

from __future__ import annotations

import json
import math
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


# What plan 14.3's founder-text and spec candidates aim to move (PROPOSTA-14.3.md, P1/P4/P6/P10).
FOUNDER_TEXT = ("master.EXEC_SYSTEM", "master.SPRINT_REPORT_SYSTEM", "deployer.SUMMARY_SYSTEM")
THIRD_PERSON = re.compile(r"\b(o senhor|a senhora|d[oa] senhora?|ao senhor|fundadora?)\b", re.I)
REASSURANCE = re.compile(r"normalmente|continua(m)? funcionando|continuam passando", re.I)
TASK_ID = re.compile(r"\bT\d{1,3}\b")
HAS_VALUE = re.compile(r"\d|[\"“'‘]")


def extra(report: dict, variant: str) -> str:
    """The number that tells what the change does, per prompt."""
    p, data = report["prompt"], answers(report, variant)
    if p in FOUNDER_TEXT:
        texts = [json.dumps(d, ensure_ascii=False) for d in data]
        return (
            f"3ª pessoa/gênero {sum(bool(THIRD_PERSON.search(t)) for t in texts)}, "
            f"tranquiliza {sum(bool(REASSURANCE.search(t)) for t in texts)}, "
            f"T# {sum(bool(TASK_ID.search(t)) for t in texts)}"
        )
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
        crit = [str(c) for d in data for c in d.get("acceptance") or []]
        return (
            f"needs_decision {sum(bool(d.get('needs_decision')) for d in data)}/{len(data)}, "
            f"critérios com valor {sum(bool(HAS_VALUE.search(c)) for c in crit)}/{len(crit)}"
        )
    if p == "worker.SYSTEM":
        first = Counter(
            (r["tool_calls"] or ["(texto)"])[0] for r in report["runs"] if r["variant"] == variant
        )
        return ", ".join(f"{k} {v}" for k, v in first.most_common(3))
    if p == "product_owner.PROPOSE_SYSTEM":
        picks = [p for d in data for p in d.get("picks") or [] if isinstance(p, dict)]
        return f"no sprint {sum(bool(p.get('in_sprint')) for p in picks)}/{len(picks)}"
    return ""


def sign_p(k: int, n: int) -> float:
    """Two-sided exact binomial p of k successes in n at 1/2: is a split of wins, or of the cases
    where only one variant passed a check, more than chance? (sign test; McNemar when paired)."""
    if n == 0:
        return 1.0
    k = min(k, n - k)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired_founder(report: dict) -> str:
    """Cases where only A or only B passed the founder-text checks, and the McNemar p."""
    ok: dict[str, dict[str, bool]] = {}
    for r in report["runs"]:
        f = (r.get("checked") or {}).get("founder_ok")
        if f is not None:
            ok.setdefault(r["case_id"], {})[r["variant"]] = bool(f)
    only_a = sum(1 for v in ok.values() if v.get("A") and v.get("B") is False)
    only_b = sum(1 for v in ok.values() if v.get("B") and v.get("A") is False)
    return f"só A {only_a}, só B {only_b} (p {sign_p(only_a, only_a + only_b):.2f})" if ok else "—"


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
        wins = a["judge_wins"] + b["judge_wins"]
        rows.append(
            f"| {log.stem} | {a['n']} | {a['judge_wins']}×{b['judge_wins']} (empates "
            f"{a['judge_ties']}, viradas {a.get('judge_flips', '—')}; p "
            f"{sign_p(b['judge_wins'], wins):.2f}) | {a['judge_score']}→{b['judge_score']} | "
            f"{a['contract_ok']}→{b['contract_ok']} | "
            f"{a['founder_ok'] if a['founder_ok'] is not None else '—'}→"
            f"{b['founder_ok'] if b['founder_ok'] is not None else '—'} "
            f"({paired_founder(rep)}) | "
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
