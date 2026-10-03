"""Plan 9.2/9.3: models.dev cards and one model across routes, never by name similarity."""

from __future__ import annotations

import json

from loompa.config.store import default_config
from loompa.llm import ModelRouter
from loompa.llm.modelsdev import byok_table, card, hub_cache_dir, identity

# The shape of https://models.dev/api.json, trimmed to what the rules need (real ids of 2026-10-03).
DATA = {
    "openrouter": {
        "models": {
            "z-ai/glm-5.3": {"id": "z-ai/glm-5.3", "canonical_model_id": "zhipuai/glm-5.3"},
            "qwen/qwen3.8-max-0902": {
                "id": "qwen/qwen3.8-max-0902",
                "canonical_model_id": "alibaba/qwen3.8-max-0902",
            },
            "xiaomi/mimo-v2.6-pro": {"id": "xiaomi/mimo-v2.6-pro"},
            "xiaomi/mimo-v2.6-flash": {"id": "xiaomi/mimo-v2.6-flash"},
        }
    },
    "zai": {
        "models": {
            "glm-5.3": {"id": "glm-5.3", "canonical_model_id": "zhipuai/glm-5.3"},
            "glm-5.3-air": {"id": "glm-5.3-air"},  # no canonical and no prefix rule: unmatched
        }
    },
    "alibaba": {
        "models": {
            "qwen3.8-max": {"id": "qwen3.8-max", "canonical_model_id": "alibaba/qwen3.8-max"}
        }
    },
    "xiaomi": {
        "models": {
            "mimo-v2.6-pro": {
                "id": "mimo-v2.6-pro",
                "tool_call": True,
                "reasoning": True,
                "reasoning_options": [{"type": "toggle"}],
                "limit": {"context": 1048576, "output": 131072},
                "cost": {"input": 0.435, "output": 0.87, "cache_read": 0.0036},
            },
            "mimo-v2.6-flash": {"id": "mimo-v2.6-flash"},
        }
    },
}


def write_cache() -> None:
    path = hub_cache_dir() / "models_dev.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DATA), encoding="utf-8")


def test_one_model_is_tied_by_canonical_id_or_the_explicit_prefix_only():
    cfg = default_config()
    zai = identity(cfg, "zai", DATA)
    assert zai.pairs == {"z-ai/glm-5.3": "glm-5.3"} and zai.unmatched == ["glm-5.3-air"]
    xiaomi = identity(cfg, "xiaomi", DATA)  # no canonical id: "xiaomi/" + own id, exactly
    assert xiaomi.pairs == {
        "xiaomi/mimo-v2.6-pro": "mimo-v2.6-pro",
        "xiaomi/mimo-v2.6-flash": "mimo-v2.6-flash",
    }
    cfg.providers["alibaba"].byok_models["qwen/qwen3.8-max-0902"] = "qwen3.8-max"
    alibaba = identity(cfg, "alibaba", DATA)
    # the pinned release and the moving alias are two models: the hand-written pair is refuted
    assert alibaba.pairs == {} and alibaba.contradicted == {"qwen/qwen3.8-max-0902": "qwen3.8-max"}


def test_the_card_carries_the_makers_price_limits_and_a_thinking_toggle():
    c = card(default_config(), "xiaomi", "mimo-v2.6-pro", DATA)
    assert c.price.input == 0.435 and c.price.cached_input == 0.0036
    assert c.context == 1048576 and c.tools and c.toggle_only and c.efforts == ()
    assert card(default_config(), "xiaomi", "mimo-v9", DATA) is None


def test_the_router_routes_by_the_table_and_drops_a_refuted_pair():
    write_cache()
    cfg = default_config()
    cfg.providers["alibaba"].byok_models["qwen/qwen3.8-max-0902"] = "qwen3.8-max"
    cfg.providers["zai"].byok_models.clear()  # the canonical id alone ties GLM 5.3
    cfg.providers["zai"].models_dev_id = "zai"  # opted in without a hand-written pair
    assert "qwen/qwen3.8-max-0902" not in byok_table(cfg, "alibaba")
    assert byok_table(cfg, "zai") == {"z-ai/glm-5.3": "glm-5.3"}
    assert byok_table(cfg, "gemini") == {}  # never opted in: a key alone does not reroute
    from loompa.config.schema import ModelCandidate

    router = ModelRouter(cfg, providers={}, secrets={"ZAI_API_KEY": "z", "DASHSCOPE_API_KEY": "d"})
    cands = [
        ModelCandidate(provider="openrouter", model="z-ai/glm-5.3"),
        ModelCandidate(provider="openrouter", model="qwen/qwen3.8-max-0902"),
    ]
    assert [(c.provider, c.model) for c in router.direct_first(cands)] == [
        ("zai", "glm-5.3"),
        ("openrouter", "z-ai/glm-5.3"),
        ("openrouter", "qwen/qwen3.8-max-0902"),
    ]
