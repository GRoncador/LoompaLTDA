"""Plan 9.4, first piece: the Artificial Analysis key, set and tested from the settings screen."""

from __future__ import annotations

import httpx
import respx

from loompa.config.secrets import Secrets
from loompa.config.settings import SettingsPatch, apply_settings, describe_settings
from loompa.config.store import default_config
from loompa.llm.probe import probe_artificial_analysis

FAKE_AA = "aa-test-key-000000000000000000000000000000000001"
URL = "https://artificialanalysis.ai/api/v2/data/llms/models"


def test_the_key_is_saved_shown_masked_and_removed_like_tavilys(tmp_path, hub):
    root = tmp_path / "f"
    root.mkdir()
    cfg = default_config()
    patch = {"tools": {"artificial_analysis": {"api_key": FAKE_AA}}}
    notes = apply_settings(root, cfg, SettingsPatch.model_validate(patch))
    assert notes == ["chave da Artificial Analysis guardada"]
    view = describe_settings(cfg, Secrets.load(root, home=hub.home))
    aa = view["tools"]["artificial_analysis"]
    assert aa["api_key_env"] == "ARTIFICIAL_ANALYSIS_API_KEY" and aa["key"]["configured"]
    assert FAKE_AA not in repr(view)
    clear = {"tools": {"artificial_analysis": {"clear_key": True}}}
    apply_settings(root, cfg, SettingsPatch.model_validate(clear))
    view = describe_settings(cfg, Secrets.load(root, home=hub.home))
    assert not view["tools"]["artificial_analysis"]["key"]["configured"]


@respx.mock
async def test_the_probe_counts_the_rated_models_and_says_a_refused_key_plainly():
    cfg = default_config().tools.artificial_analysis
    route = respx.get(URL).respond(json={"data": [{"slug": "a"}, {"slug": "b"}]})
    ok = await probe_artificial_analysis(cfg, secrets={"ARTIFICIAL_ANALYSIS_API_KEY": FAKE_AA})
    assert ok.ok and ok.detail == "chave válida: 2 modelos com nota"
    assert route.calls[0].request.headers["x-api-key"] == FAKE_AA
    route.respond(401)
    bad = await probe_artificial_analysis(cfg, secrets={"ARTIFICIAL_ANALYSIS_API_KEY": FAKE_AA})
    assert not bad.ok and bad.detail == "chave recusada pela Artificial Analysis"
    route.mock(side_effect=httpx.ConnectError("down"))
    down = await probe_artificial_analysis(cfg, secrets={"ARTIFICIAL_ANALYSIS_API_KEY": FAKE_AA})
    assert not down.ok and FAKE_AA not in down.detail + down.reason
    missing = await probe_artificial_analysis(cfg, secrets={})
    assert not missing.ok and missing.detail == "chave não configurada"
