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
    assert cfg.models.tier_for("master") == "tier1"
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
    assert cfg.models.tier_for("master") == "tier1"
    assert cfg.models.tier_for_task("master", "master.classify") == "tier2"
    cfg.models.role_tasks["master.classify"] = "tier3"
    assert cfg.models.tier_for_task("master", "master.classify") == "tier3"
    # an unknown task is the role's tier, never an error
    assert cfg.models.tier_for_task("master", "master.inventada") == "tier1"


def test_a_legacy_monthly_budget_keeps_its_numbers():
    """A config.yaml written before the weekly budget must not silently become a US$ 5 week."""
    cfg = LoompaConfig.model_validate({"budget": {"monthly_cap_usd": 30.0, "hard_stop": False}})
    assert (cfg.budget.period, cfg.budget.cap_usd) == ("monthly", 30.0)
    assert cfg.budget.on_exceed == "tier3"
