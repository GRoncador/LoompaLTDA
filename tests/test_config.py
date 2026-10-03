from pathlib import Path

from loompa.config import (
    LoompaConfig,
    default_config,
    find_factory_root,
    load_config,
    save_config,
)
from loompa.config.schema import FactoryRef, HubRegistry, slugify


def test_defaults_load_and_validate():
    cfg = default_config()
    assert cfg.schedule.max_parallel == 3
    assert (cfg.budget.period, cfg.budget.cap_usd) == ("weekly", 5.0)
    assert cfg.budget.on_exceed == "pause"
    assert cfg.models.tier_for("master") == "tier2"
    assert cfg.models.tier_for("worker") == "tier2"
    # fixed ids, never `~...-latest`: an alias would change model and price without approval
    assert cfg.models.candidates_for("worker")[0].model == "z-ai/glm-5.3-flash"
    assert not any(
        c.model.startswith("~") for t in cfg.models.matrix.values() for cs in t.values() for c in cs
    )
    assert cfg.models.tier_for("analyst") == "tier2" and cfg.models.tier_for("novo") == "tier2"
    assert cfg.providers["openrouter"].kind == "openai_compatible"
    assert cfg.price_for("deepseek-chat").output == 1.10
    assert cfg.price_for("unknown-model").input == 1.0


def test_save_load_roundtrip_and_merge_with_defaults(tmp_path: Path):
    cfg = default_config()
    cfg.factory.name = "Minha Loja"
    cfg.factory.slug = "Minha Loja"
    cfg.schedule.max_parallel = 5
    save_config(tmp_path, cfg)
    loaded = load_config(tmp_path)
    assert loaded.factory.slug == "minha-loja"
    assert loaded.schedule.max_parallel == 5
    # a user file with only a subset of keys still yields the full config
    (tmp_path / ".loompa" / "config.yaml").write_text("factory:\n  name: x\n")
    partial = load_config(tmp_path)
    assert isinstance(partial, LoompaConfig)
    assert partial.models.tiers["tier2"]


def test_find_factory_root_walks_up(tmp_path: Path):
    save_config(tmp_path, default_config())
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_factory_root(nested) == tmp_path.resolve()
    assert find_factory_root(tmp_path.parent / "nope") is None


def test_slugify():
    assert slugify("SaaS Finanças!") == "saas-financas"
    assert slugify("") == "factory"


def test_hub_registry(hub, tmp_path: Path):
    cfg = default_config()
    cfg.factory.name = "A"
    cfg.factory.slug = "a"
    hub.register(tmp_path / "a", cfg)
    cfg.factory.name, cfg.factory.slug = "B", "b"
    hub.register(tmp_path / "b", cfg)
    reg = hub.load()
    assert [f.slug for f in reg.factories] == ["a", "b"]
    assert reg.active == "a"
    hub.set_active("b")
    assert hub.active().slug == "b"
    assert hub.resolve("a") == (tmp_path / "a").resolve()
    assert hub.resolve(cwd=tmp_path / "elsewhere") == (tmp_path / "b").resolve()
    reg = hub.load()
    assert reg.remove("b")
    assert reg.active == "a"


def test_registry_model_upsert_replaces():
    reg = HubRegistry()
    reg.upsert(FactoryRef(slug="x", name="X", path=Path("/x")))
    reg.upsert(FactoryRef(slug="x", name="X2", path=Path("/x2")))
    assert len(reg.factories) == 1 and reg.factories[0].name == "X2"


def test_every_configured_model_has_its_own_price():
    """`price_for` falls back to a generic `default` entry, so asking it proves nothing: a $0.09
    model billed at the $1.00 default is 10x off. Every id in the matrix needs its own line."""
    cfg = default_config()
    for cluster, tiers in cfg.models.matrix.items():
        for tier, candidates in tiers.items():
            for c in candidates:
                assert c.provider in cfg.providers, f"{cluster}/{tier}: {c.provider} não existe"
                assert c.model in cfg.pricing, f"{cluster}/{tier}: {c.model} sem preço próprio"


def test_openrouter_leads_the_provider_list():
    """One key there reaches every model, so it is the first thing the settings screen offers."""
    cfg = default_config()
    assert next(iter(cfg.providers)) == "openrouter"
    assert cfg.providers["openrouter"].api_key_env == "OPENROUTER_API_KEY"
    for name, p in cfg.providers.items():
        assert p.models_url, f"{name} não diz onde ficam os modelos disponíveis"


def test_turning_clusters_off_sends_every_role_to_the_general_cluster():
    cfg = default_config()
    assert cfg.models.cluster_for_role("worker") == "engineering"
    cfg.models.clusters_enabled = False
    assert cfg.models.cluster_for_role("worker") == "general"
    assert cfg.models.cluster_for_role("master") == "general"


def test_a_named_task_can_have_its_own_tier():
    """The Master decides on tier 1, but classifying a story is not a decision."""
    cfg = default_config()
    assert cfg.models.tier_for("architect") == "tier1"
    assert cfg.models.tier_for_task("architect", "architect.preflight") == "tier2"
    cfg.models.role_tasks["architect.preflight"] = "tier3"
    assert cfg.models.tier_for_task("architect", "architect.preflight") == "tier3"
    # an unknown task is the role's tier, never an error
    assert cfg.models.tier_for_task("architect", "architect.inventada") == "tier1"


def test_the_plan_is_tier_one_only_on_a_complex_story():
    """Cost review 2026-10-03: a STANDARD plan on tier 1 cost 10% of the spend for nothing the
    re-plan of an escalation does not already cover."""
    m = default_config().models
    assert m.task_tier("architect", "architect.plan", "STANDARD") == "tier2"
    assert m.task_tier("architect", "architect.plan", "COMPLEX") == "tier1"
    assert m.task_tier("architect", "architect.inventada", "COMPLEX") is None
    m.role_tasks["architect.plan"] = "tier1"  # the founder's choice wins over the rule
    assert m.task_tier("architect", "architect.plan", "STANDARD") == "tier1"


def test_an_old_config_drops_the_defaults_the_cost_review_changed(tmp_path):
    """Every save writes the whole config, so the old defaults would stay forever: revision 1
    drops the ones written word for word and keeps what the founder changed."""
    import yaml

    from loompa.config.store import load_config, save_config

    (tmp_path / ".loompa").mkdir()
    old = {
        "models": {
            "full_output_tokens": 96000,
            "roles": {"master": "tier1", "storyteller": "tier1"},
            "role_tasks": {"deployer.summary": "tier2", "master.classify": "tier2"},
        }
    }
    (tmp_path / ".loompa" / "config.yaml").write_text(yaml.safe_dump(old), encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.revision == 1
    assert cfg.models.tier_for("master") == "tier2"
    assert cfg.models.tier_for("storyteller") == "tier1"  # changed by the founder: kept
    assert cfg.models.tier_for_task("deployer", "deployer.summary") == "tier3"
    # saved and loaded again, a choice made after the upgrade is not undone
    cfg.models.role_tasks["deployer.summary"] = "tier2"
    save_config(tmp_path, cfg)
    assert load_config(tmp_path).models.tier_for_task("deployer", "deployer.summary") == "tier2"


def test_a_legacy_monthly_budget_keeps_its_numbers():
    """A config.yaml written before the weekly budget must not silently become a US$ 5 week."""
    cfg = LoompaConfig.model_validate({"budget": {"monthly_cap_usd": 30.0, "hard_stop": False}})
    assert (cfg.budget.period, cfg.budget.cap_usd) == ("monthly", 30.0)
    assert cfg.budget.on_exceed == "tier3"


def test_turning_clusters_off_points_the_flat_tiers_at_the_general_list():
    """`models.tiers` is the fallback the doctor and the probe read. With the split off it must
    mirror the list every role actually uses, not the per-cluster ones nobody reads."""
    from pathlib import Path
    from tempfile import mkdtemp

    from loompa.config.schema import ModelCandidate
    from loompa.config.settings import SettingsPatch, apply_settings

    cfg = default_config()
    cfg.models.matrix["general"]["tier1"] = [
        ModelCandidate(provider="openrouter", model="so/geral")
    ]
    apply_settings(
        Path(mkdtemp()),
        cfg,
        SettingsPatch(matrix=cfg.models.matrix, clusters_enabled=False),
    )
    assert [c.model for c in cfg.models.tiers["tier1"]] == ["so/geral"]
    assert cfg.models.cluster_for_role("worker") == "general"


def test_a_config_written_before_adr_0016_gets_the_new_room_and_tries(tmp_path: Path):
    """Every save writes the whole config, so `contas` carried the old 32k ceiling and the single
    tier-1 attempt word for word: kept, they would cap the 96k room and cut a try."""
    import yaml

    from loompa.config.store import CONFIG_FILE, LOOMPA_DIR, load_config

    cfg = default_config().model_dump(mode="json")
    for key in (
        "full_output_tokens",
        "light_output_tokens",
        "stream_idle_s",
        "stream_token_idle_s",
    ):
        cfg["models"].pop(key)
    cfg["models"]["max_output_ceiling"] = 32768
    cfg["schedule"]["tier1_max_attempts"] = 1
    (tmp_path / LOOMPA_DIR).mkdir()
    (tmp_path / LOOMPA_DIR / CONFIG_FILE).write_text(yaml.safe_dump(cfg))
    old = load_config(tmp_path)
    assert old.models.max_output_ceiling == 96000 and old.models.full_output_tokens == 96000
    assert old.schedule.tier1_max_attempts == 2
    # a value someone changed on purpose stays, and a file written after ADR-0016 is left alone
    cfg["models"]["max_output_ceiling"] = 20000
    cfg["schedule"]["tier1_max_attempts"] = 3
    (tmp_path / LOOMPA_DIR / CONFIG_FILE).write_text(yaml.safe_dump(cfg))
    kept = load_config(tmp_path)
    assert kept.models.max_output_ceiling == 20000 and kept.schedule.tier1_max_attempts == 3
    cfg["models"].update(full_output_tokens=96000, max_output_ceiling=32768)
    (tmp_path / LOOMPA_DIR / CONFIG_FILE).write_text(yaml.safe_dump(cfg))
    assert load_config(tmp_path).models.max_output_ceiling == 32768
