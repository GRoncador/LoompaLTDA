"""Plan 9.5: the house score, what the factory measured with each model in each role."""

from __future__ import annotations

import json

from loompa.config.store import default_config
from loompa.finance.house import MIN_TASKS, house_scores, worker_house
from loompa.llm.modelsdev import hub_cache_dir
from loompa.store import Store


def call(store: Store, story: str, provider: str, model: str, cost: float = 0.01) -> None:
    store.record_usage(
        factory="f",
        story_id=story,
        agent="Worker",
        role="worker",
        provider=provider,
        model=model,
        tier="tier2",
        input_tokens=100,
        output_tokens=10,
        cached_tokens=0,
        cost_usd=cost,
        duration_ms=10,
        cost_source="reported",
    )


def task(store: Store, story: str, n: int, ended_by: str) -> None:
    store.emit(
        "f", "worker.task", story_id=story, task=n, ended_by=ended_by, tool_calls=4, duration_s=60
    )


def test_each_task_counts_for_the_model_that_did_most_of_its_calls_on_any_route():
    """The maker's own id counts under the OpenRouter id of the same model (plan 9.3)."""
    path = hub_cache_dir() / "models_dev.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "openrouter": {
                    "models": {"xiaomi/mimo-v2.6-flash": {"id": "xiaomi/mimo-v2.6-flash"}}
                },
                "xiaomi": {"models": {"mimo-v2.6-flash": {"id": "mimo-v2.6-flash"}}},
            }
        ),
        encoding="utf-8",
    )
    store = Store(":memory:")
    for i in range(MIN_TASKS):  # one story per task: tasks of a story run one after the other
        sid = f"S-{i:03}"
        call(store, sid, "xiaomi", "mimo-v2.6-flash")
        call(store, sid, "openrouter", "xiaomi/mimo-v2.6-flash")
        task(store, sid, 1, "done")
    for i in range(MIN_TASKS):
        sid = f"S-1{i:02}"
        call(store, sid, "openrouter", "z-ai/glm-5.3-flash", cost=0.05)
        task(store, sid, 1, "done" if i < 3 else "limit")
    store.emit("f", "worker.salvaged", story_id="S-107", task=1)  # a limit-ended task it saved
    scores = {s.model: s for s in house_scores(store, "f", default_config()) if s.role == "worker"}
    mimo, glm = scores["xiaomi/mimo-v2.6-flash"], scores["z-ai/glm-5.3-flash"]
    assert mimo.calls == 2 * MIN_TASKS and mimo.tasks == MIN_TASKS and mimo.done_rate == 1.0
    assert glm.tasks == MIN_TASKS and glm.done == 4  # 3 done + 1 salvaged
    assert glm.flagged and not mimo.flagged and mimo.enough
    assert worker_house(list(scores.values()))["z-ai/glm-5.3-flash"]["cost_per_done"] == 0.1


def test_a_flagged_model_goes_to_the_end_of_its_tier_without_touching_the_scale():
    from datetime import date

    from loompa.config.schema import ModelCandidate
    from loompa.llm.catalog import Policy, build_proposal, parse_model

    def entry(mid: str, intel: float) -> dict:
        return {
            "id": mid,
            "context_length": 200_000,
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
            "supported_parameters": ["tools"],
            "benchmarks": {"artificial_analysis": {"intelligence_index": intel}},
        }

    models = [
        parse_model(entry(m, s)) for m, s in (("a/strong", 45), ("b/weak", 40), ("c/third", 38))
    ]
    cfg = default_config()
    cfg.models.clusters_enabled = False
    cfg.models.tiers = {
        "tier2": [ModelCandidate(provider="openrouter", model="old/x")],
        "tier1": [ModelCandidate(provider="openrouter", model="old/y")],
    }
    house = {"a/strong": {"tasks": 20, "done_rate": 0.5, "enough": True, "flagged": True}}
    plain = build_proposal(cfg, models, Policy(tier1_ceiling=5), date(2026, 10, 3))
    weighed = build_proposal(cfg, models, Policy(tier1_ceiling=5), date(2026, 10, 3), house=house)
    assert [m["id"] for m in plain.summary["tier2"]] == ["a/strong", "b/weak", "c/third"]
    assert [m["id"] for m in weighed.summary["tier2"]] == ["b/weak", "c/third", "a/strong"]
    assert [c["model"] for c in weighed.tiers["tier2"]] == ["b/weak", "c/third", "a/strong"]
    strong = weighed.summary["tier2"][-1]
    assert strong["house"]["flagged"] and strong["score"] == plain.summary["tier2"][0]["score"]
