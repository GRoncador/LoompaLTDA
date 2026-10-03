"""Where the money went (`loompa costs`): the cost review of 2026-10-03, kept as a command.

Counted from the `usage` table and the events, never from a model. Only calls whose cost the
provider reported or the price table gave are real; the report says how many there were. The
questions it answers: which role, tier and model spend; whether OpenRouter switching provider
mid-task cost the prompt cache; whether escalating to tier 1 rescues stories; how the effort
experiment's two arms compare; and what the cost cap, the language check and the free tier did.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from loompa.store import Store

DONE_STAGES = ("DONE",)
LOST_STAGES = ("CANCELLED",)


def _group(rows: list[dict[str, Any]], key) -> list[dict[str, Any]]:
    total = sum(float(r["cost_usd"] or 0) for r in rows) or 1.0
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    out = []
    for k, items in groups.items():
        cost = sum(float(r["cost_usd"] or 0) for r in items)
        tokens_in = sum(int(r["input_tokens"] or 0) for r in items)
        cached = sum(int(r["cached_tokens"] or 0) for r in items)
        out.append(
            {
                "key": k,
                "calls": len(items),
                "cost_usd": round(cost, 6),
                "share": round(cost / total, 4),
                "per_call": round(cost / len(items), 6),
                "cache_hit": round(cached / tokens_in, 4) if tokens_in else 0.0,
            }
        )
    return sorted(out, key=lambda g: -g["cost_usd"])


def _provider_switches(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Consecutive calls of one agent on one story and model: did the upstream provider change,
    and what was the cache hit on those calls compared with the ones that stayed?"""
    last: dict[tuple, str] = {}
    acc = {True: [0, 0, 0], False: [0, 0, 0]}  # stayed?: calls, input, cached
    for r in rows:
        if not r.get("served_by"):
            continue
        k = (r["story_id"], r["agent"], r["model"])
        if k in last:
            a = acc[last[k] == r["served_by"]]
            a[0] += 1
            a[1] += int(r["input_tokens"] or 0)
            a[2] += int(r["cached_tokens"] or 0)
        last[k] = r["served_by"]

    def side(a: list[int]) -> dict[str, Any]:
        return {"calls": a[0], "cache_hit": round(a[2] / a[1], 4) if a[1] else 0.0}

    return {"stayed": side(acc[True]), "switched": side(acc[False])}


def _escalations(store: Store, events: list[dict[str, Any]], rows: list[dict[str, Any]]):
    escalated = {e["story_id"] for e in events if e["type"] == "story.escalated" and e["story_id"]}
    spent = defaultdict(float)
    for r in rows:
        if r["story_id"] in escalated and r["tier"] == "tier1":
            spent[r["story_id"]] += float(r["cost_usd"] or 0)
    gave_up_ids = {
        e["story_id"]
        for e in events
        if e["type"] == "story.blocked"
        and e["story_id"] in escalated
        and (e.get("payload") or {}).get("reason") == "persistent_failure"
    }
    stories = []
    for sid in sorted(escalated):
        row = store.get_story(sid) or {}
        stage = str(row.get("stage") or "")
        # one outcome per story: delivered, cancelled, stopped asking for guidance, or still open
        outcome = (
            "done"
            if stage in DONE_STAGES
            else "cancelled"
            if stage in LOST_STAGES
            else "gave_up"
            if sid in gave_up_ids
            else "open"
        )
        stories.append(
            {
                "story_id": sid,
                "title": row.get("title", ""),
                "stage": stage,
                "outcome": outcome,
                "tier1_cost_usd": round(spent[sid], 6),
            }
        )
    done = sum(1 for s in stories if s["outcome"] == "done")
    lost = sum(1 for s in stories if s["outcome"] == "cancelled")
    blocked = sum(1 for s in stories if s["outcome"] == "gave_up")
    settled = done + lost + blocked
    return {
        "stories": stories,
        "done": done,
        "cancelled": lost,
        "gave_up": blocked,
        "rescue_rate": round(done / settled, 4) if settled else None,
        "tier1_cost_usd": round(sum(spent.values()), 6),
    }


def _effort_ab(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    arms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        p = e.get("payload") or {}
        if e["type"] == "worker.task" and p.get("effort_ab"):
            arms[p["effort_ab"]].append(p)
    out = {}
    for arm, tasks in sorted(arms.items()):
        n = len(tasks)
        out[arm] = {
            "tasks": n,
            "done": round(sum(1 for t in tasks if t.get("ended_by") == "done") / n, 4),
            "raised": round(sum(1 for t in tasks if t.get("raised")) / n, 4),
            "cost_per_task": round(sum(float(t.get("cost_usd") or 0) for t in tasks) / n, 6),
            "tool_calls": round(sum(int(t.get("tool_calls") or 0) for t in tasks) / n, 1),
        }
    return out


def cost_report(store: Store, factory: str, days: int = 7) -> dict[str, Any]:
    end = datetime.now(UTC)
    start = (end - timedelta(days=days)).isoformat()
    rows = store.usage_window(factory, start, end.isoformat())
    events = store.events_between(factory, start, end.isoformat())
    real = [r for r in rows if r.get("cost_source") in ("reported", "table")]
    count = defaultdict(int)
    for e in events:
        count[e["type"]] += 1
    free_fallbacks = sum(
        1
        for e in events
        if e["type"] == "llm.fallthrough" and (e.get("payload") or {}).get("reason") == "free_tier"
    )
    by_story = _group([r for r in rows if r["story_id"]], lambda r: r["story_id"])[:10]
    for s in by_story:
        row = store.get_story(s["key"]) or {}
        s["title"] = row.get("title", "")
        s["stage"] = str(row.get("stage") or "")
    return {
        "days": days,
        "calls": len(rows),
        "real_cost_calls": len(real),
        "cost_usd": round(sum(float(r["cost_usd"] or 0) for r in rows), 6),
        "by_role": _group(rows, lambda r: r["role"]),
        "by_tier": _group(rows, lambda r: r["tier"]),
        "by_role_tier": _group(rows, lambda r: f"{r['role']} · {r['tier']}"),
        "by_model": _group(
            rows,
            lambda r: (
                r["model"] if r["provider"] == "openrouter" else f"{r['model']} ({r['provider']})"
            ),
        ),
        "by_story": by_story,
        "provider_switches": _provider_switches(rows),
        "escalations": _escalations(store, events, rows),
        "effort_ab": _effort_ab(events),
        "cost_caps": count["story.cost_cap"],
        "foreign_plans": count["plan.foreign_language"],
        "free_tier_calls": sum(1 for r in rows if r["tier"] == "tier3"),
        "free_tier_fallbacks": free_fallbacks,
    }
