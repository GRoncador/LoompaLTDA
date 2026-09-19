from pathlib import Path

from loompa.config import LoompaConfig, default_config, find_factory_root, load_config, save_config
from loompa.config.schema import FactoryRef, HubRegistry, slugify


def test_defaults_load_and_validate():
    cfg = default_config()
    assert cfg.schedule.max_parallel == 3
    assert cfg.budget.monthly_cap_usd == 30.0
    assert cfg.models.tier_for("master") == "tier1"
    assert cfg.models.tier_for("worker") == "tier2"
    assert cfg.models.candidates_for("worker")[0].model == "gemini-3.5-flash-lite"
    assert cfg.models.tier_for("analyst") == "tier2" and cfg.models.tier_for("novo") == "tier2"
    assert cfg.providers["deepseek"].kind == "openai_compatible"
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
