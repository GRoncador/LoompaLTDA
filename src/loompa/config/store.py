"""Loading, saving and locating factory configuration and the global hub registry."""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path

import yaml

from loompa.config.schema import FactoryRef, HubRegistry, LoompaConfig

LOOMPA_DIR = ".loompa"
CONFIG_FILE = "config.yaml"
HUB_ENV = "LOOMPA_HOME"


def _defaults_text() -> str:
    return resources.files("loompa.config").joinpath("defaults.yaml").read_text(encoding="utf-8")


def default_config() -> LoompaConfig:
    return LoompaConfig.model_validate(yaml.safe_load(_defaults_text()))


def find_factory_root(start: Path | None = None) -> Path | None:
    """Walk upwards from `start` until a directory containing `.loompa/config.yaml` is found."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / LOOMPA_DIR / CONFIG_FILE).is_file():
            return candidate
    return None


def load_config(root: Path) -> LoompaConfig:
    path = root / LOOMPA_DIR / CONFIG_FILE
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # Layer user config over defaults so new keys get sane values after upgrades.
    merged = _deep_merge(yaml.safe_load(_defaults_text()), data)
    return LoompaConfig.model_validate(merged)


def save_config(root: Path, config: LoompaConfig) -> Path:
    path = root / LOOMPA_DIR / CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json")
    path.write_text(
        "# Loompa LTDA factory configuration (see loompa/config/defaults.yaml for docs)\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class ConfigStore:
    """Global hub: registry of every factory the Founder manages on this machine."""

    def __init__(self, home: Path | None = None):
        env = os.environ.get(HUB_ENV)
        self.home = home or (Path(env) if env else Path.home() / LOOMPA_DIR)
        self.registry_path = self.home / "factories.yaml"

    def load(self) -> HubRegistry:
        if not self.registry_path.is_file():
            return HubRegistry()
        data = yaml.safe_load(self.registry_path.read_text(encoding="utf-8")) or {}
        return HubRegistry.model_validate(data)

    def save(self, registry: HubRegistry) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(
            yaml.safe_dump(registry.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def register(self, root: Path, config: LoompaConfig) -> FactoryRef:
        registry = self.load()
        ref = FactoryRef(slug=config.factory.slug, name=config.factory.name, path=root.resolve())
        registry.upsert(ref)
        self.save(registry)
        return ref

    def set_active(self, slug: str) -> FactoryRef:
        registry = self.load()
        ref = registry.get(slug)
        if ref is None:
            raise KeyError(f"unknown factory: {slug}")
        registry.active = slug
        self.save(registry)
        return ref

    def active(self) -> FactoryRef | None:
        registry = self.load()
        return registry.get(registry.active) if registry.active else None

    def resolve(self, slug: str | None = None, cwd: Path | None = None) -> Path | None:
        """Resolve the factory root: explicit slug > cwd ancestor > active factory."""
        if slug:
            ref = self.load().get(slug)
            return ref.path if ref else None
        found = find_factory_root(cwd)
        if found:
            return found
        ref = self.active()
        return ref.path if ref else None
