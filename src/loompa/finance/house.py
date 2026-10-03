"""The house score (plan 9.5): what this factory measured with each model in each role.

Beside the external index (Artificial Analysis), the factory's own record: calls, cost, answers cut
at the output limit, and for the Worker the tasks it ran and how they ended. A Worker task carries
no model, so it is tied to the model that did most of its calls: the usage rows of the same story
and role inside the task's window (tasks of one story run one after the other). Calls on a maker's
own API count under the OpenRouter id of the same model (the identity table of plan 9.3), so one
model is one row whatever the route.

With enough tasks (`MIN_TASKS`) the score weighs on the model suggestion: a model that finished
clearly fewer tasks than the best measured one goes to the end of its tier and is flagged. Before
that it is only shown. The AA scale stays untouched, so the tier-1 ceiling and the tier-2 floor
keep their meaning.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loompa.config.schema import LoompaConfig
from loompa.store import Store

MIN_TASKS = 8
# A model below this share of the best measured done rate is the house's "worse" (flagged).
FLAG_SHARE = 0.8
DONE = ("done", "salvaged")


@dataclass
class HouseScore:
    role: str
    model: str  # OpenRouter id when the identity table knows one
    calls: int = 0
    cost_usd: float = 0.0
    cut: int = 0  # answers cut at the output limit
    tasks: int = 0
    done: int = 0
    tool_calls: int = 0
    seconds: float = 0.0
    flagged: bool = False

    @property
    def done_rate(self) -> float | None:
        return round(self.done / self.tasks, 4) if self.tasks else None

    @property
    def enough(self) -> bool:
        return self.tasks >= MIN_TASKS

    @property
    def cost_per_done(self) -> float | None:
        return round(self.cost_usd / self.done, 6) if self.done else None

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "done_rate": self.done_rate,
            "enough": self.enough,
            "cost_per_done": self.cost_per_done,
            "minutes_per_task": round(self.seconds / self.tasks / 60, 1) if self.tasks else None,
        }


def _openrouter_id(config: LoompaConfig, provider: str, model: str) -> str:
    if provider == "openrouter":
        return model
    from loompa.llm.modelsdev import byok_table

    back = {own: or_id for or_id, own in byok_table(config, provider).items()}
    return back.get(model, f"{model} ({provider})")


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def house_scores(
    store: Store, factory: str, config: LoompaConfig, days: int = 30
) -> list[HouseScore]:
    end = datetime.now(UTC)
    start = (end - timedelta(days=days)).isoformat()
    rows = store.usage_window(factory, start, end.isoformat())
    events = store.events_between(factory, start, end.isoformat())
    scores: dict[tuple[str, str], HouseScore] = {}
    model_of: dict[int, str] = {}

    def score(role: str, model: str) -> HouseScore:
        return scores.setdefault((role, model), HouseScore(role=role, model=model))

    by_story: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        model = _openrouter_id(config, r["provider"], r["model"])
        model_of[r["id"]] = model
        s = score(r["role"], model)
        s.calls += 1
        if r.get("cost_source") in ("reported", "table"):  # older rows carry bad estimates
            s.cost_usd += float(r["cost_usd"] or 0)
        s.cut += r.get("finish_reason") == "length"
        if r["role"] == "worker" and r["story_id"]:
            by_story[r["story_id"]].append(r)
    salvaged = {
        (e["story_id"], (e.get("payload") or {}).get("task"))
        for e in events
        if e["type"] == "worker.salvaged"
    }
    for e in events:
        if e["type"] != "worker.task" or not e["story_id"]:
            continue
        p = e.get("payload") or {}
        finished = _ts(e["created_at"])
        began = finished - timedelta(seconds=float(p.get("duration_s") or 0) + 1)
        inside = [
            r for r in by_story.get(e["story_id"], []) if began <= _ts(r["created_at"]) <= finished
        ]
        if not inside:
            continue
        weight: dict[str, int] = defaultdict(int)
        for r in inside:
            weight[model_of[r["id"]]] += 1
        s = score("worker", max(weight, key=lambda m: (weight[m], m)))
        s.tasks += 1
        ended = "salvaged" if (e["story_id"], p.get("task")) in salvaged else p.get("ended_by")
        s.done += ended in DONE
        s.tool_calls += int(p.get("tool_calls") or 0)
        s.seconds += float(p.get("duration_s") or 0)
    workers = [s for s in scores.values() if s.role == "worker" and s.enough]
    best = max((s.done_rate or 0.0 for s in workers), default=0.0)
    for s in workers:
        s.flagged = best > 0 and (s.done_rate or 0.0) < FLAG_SHARE * best
    for s in scores.values():
        s.cost_usd = round(s.cost_usd, 6)
    return sorted(scores.values(), key=lambda s: (s.role, -s.calls, s.model))


def worker_house(scores: list[HouseScore]) -> dict[str, dict[str, Any]]:
    """The Worker's record by model id: what the model suggestion reads (the role that does most
    of the work and the one whose tasks have an outcome)."""
    return {s.model: s.as_dict() for s in scores if s.role == "worker"}
