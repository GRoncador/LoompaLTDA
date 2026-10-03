"""Plan 9.1: what each key of the factory can call."""

from __future__ import annotations

import httpx
import respx

from loompa.config.store import default_config
from loompa.llm.available import available, byok_checks, cached

SECRETS = {"OPENROUTER_API_KEY": "sk-or-x", "XIAOMI_API_KEY": "sk-x", "GEMINI_API_KEY": "g-x"}


@respx.mock
async def test_every_keyed_provider_is_asked_and_one_failing_never_hides_the_others(tmp_path):
    cfg = default_config()
    respx.get("https://openrouter.ai/api/v1/models").respond(
        json={"data": [{"id": "xiaomi/mimo-v2.6-pro"}, {"id": "z-ai/glm-5.3"}]}
    )
    respx.get("https://api.xiaomimimo.com/v1/models").respond(
        json={"data": [{"id": "mimo-v2.6-pro"}]}  # mimo-v2.6-flash is not there
    )
    gemini = respx.get(url__startswith="https://generativelanguage.googleapis.com").respond(401)
    results = {r.name: r for r in await available(cfg, SECRETS, cache_dir=tmp_path)}
    assert set(results) == {"openrouter", "xiaomi", "gemini"}  # no key, not asked
    assert results["openrouter"].ok and results["openrouter"].models == [
        "xiaomi/mimo-v2.6-pro",
        "z-ai/glm-5.3",
    ]
    assert not results["gemini"].ok and results["gemini"].detail == "a chave foi recusada"
    assert results["gemini"].reason == "HTTP 401" and gemini.called
    sent = next(c.request for c in respx.calls if "xiaomimimo" in str(c.request.url))
    assert sent.headers["Authorization"] == "Bearer sk-x"
    checks = {c.own_id: c.found for c in byok_checks(cfg, list(results.values()))}
    assert checks["mimo-v2.6-pro"] is True and checks["mimo-v2.6-flash"] is False
    assert checks["deepseek-flash"] is None  # no DeepSeek key: unknown, not missing
    # the screen opens from the cache, without a call
    again = {r.name: r for r in cached(cfg, SECRETS, cache_dir=tmp_path)}
    assert again["xiaomi"].models == ["mimo-v2.6-pro"] and not again["gemini"].ok


@respx.mock
async def test_google_ids_lose_the_models_prefix_and_a_timeout_says_so_plainly(tmp_path):
    cfg = default_config()
    respx.get(url__startswith="https://generativelanguage.googleapis.com").respond(
        json={"data": [{"id": "models/gemini-3.5-flash-lite"}]}
    )
    respx.get("https://openrouter.ai/api/v1/models").mock(side_effect=httpx.ReadTimeout("slow"))
    keys = {"GEMINI_API_KEY": "g-x", "OPENROUTER_API_KEY": "sk-or-x"}
    results = {r.name: r for r in await available(cfg, keys, cache_dir=tmp_path)}
    assert results["gemini"].models == ["gemini-3.5-flash-lite"]
    assert results["openrouter"].detail == "o provedor não respondeu a tempo"
