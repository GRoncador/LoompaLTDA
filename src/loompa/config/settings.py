"""Founder-facing view and edits of a factory's providers, models, tiers, budget and tool keys.

Shared by the CLI (`loompa init`, `loompa providers`) and the dashboard settings screen so both
apply exactly the same rules: keys go to the secrets files, config.yaml keeps names only, and
every response only ever says whether a key is configured.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from loompa.config.presets import MODEL_PRESETS, apply_preset, preset_summaries
from loompa.config.schema import (
    ROLES,
    LoompaConfig,
    ModelCandidate,
    ProviderConfig,
    ToolProviderConfig,
)
from loompa.config.secrets import (
    Secrets,
    factory_secrets_path,
    hub_secrets_path,
    looks_like_secret,
    write_dotenv_value,
)

Scope = str  # "hub" | "factory"


def store_key(root: Path, env_name: str, value: str | None, *, scope: Scope = "hub") -> Path:
    """Write (or clear) one key in the chosen secrets file. Returns the file written."""
    if scope not in ("hub", "factory"):
        raise ValueError("scope deve ser 'hub' ou 'factory'")
    path = factory_secrets_path(root) if scope == "factory" else hub_secrets_path()
    return write_dotenv_value(path, env_name, value)


def describe_settings(config: LoompaConfig, secrets: Secrets) -> dict[str, Any]:
    """Everything the settings screen shows. Contains no secret values."""
    used: dict[str, list[str]] = {}
    for tier, cands in config.models.tiers.items():
        for c in cands:
            used.setdefault(c.provider, []).append(f"{tier}:{c.model}")
    providers = []
    for name, p in config.providers.items():
        providers.append(
            {
                "name": name,
                "label": p.label or name,
                "kind": p.kind,
                "base_url": p.base_url,
                "api_key_env": p.api_key_env,
                "console_url": p.console_url,
                "needs_key": bool(p.api_key_env),
                "key": secrets.status(p.api_key_env)
                if p.api_key_env
                else {
                    "env": "",
                    "configured": True,
                    "label": "não precisa de chave",
                    "source": None,
                },
                "used_by": used.get(name, []),
            }
        )
    t = config.tools.tavily
    return {
        "preset": config.models.preset,
        "presets": preset_summaries(),
        "providers": providers,
        "tiers": {
            tier: [c.model_dump() for c in cands] for tier, cands in config.models.tiers.items()
        },
        "roles": {
            r: config.models.tier_for(r) for r in sorted(set(ROLES) | set(config.models.roles))
        },
        "budget": config.budget.model_dump(),
        "schedule": {"max_parallel": config.schedule.max_parallel},
        "worker": {"backend": config.worker.backend},
        "tools": {
            "tavily": {
                "enabled": t.enabled,
                "api_key_env": t.api_key_env,
                "console_url": t.console_url,
                "key": secrets.status(t.api_key_env),
            }
        },
        "secrets_files": {
            "hub": str(hub_secrets_path()),
            "factory": ".loompa/.env",
        },
    }


class ProviderPatch(BaseModel):
    kind: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    label: str | None = None
    api_key: str | None = None  # written to the secrets file, never to config
    clear_key: bool = False
    scope: Scope = "hub"


class ToolPatch(BaseModel):
    enabled: bool | None = None
    api_key: str | None = None
    clear_key: bool = False
    scope: Scope = "hub"


class BudgetPatch(BaseModel):
    monthly_cap_usd: float | None = None
    warn_at_fraction: float | None = None
    hard_stop: bool | None = None


class SettingsPatch(BaseModel):
    preset: str | None = None
    providers: dict[str, ProviderPatch] = Field(default_factory=dict)
    remove_providers: list[str] = Field(default_factory=list)
    tiers: dict[str, list[ModelCandidate]] | None = None
    roles: dict[str, str] | None = None
    budget: BudgetPatch | None = None
    max_parallel: int | None = None
    worker_backend: str | None = None  # "aci" | "opencode" (Fase 2 spike, ADR-0007)
    tools: dict[str, ToolPatch] = Field(default_factory=dict)


def apply_settings(root: Path, config: LoompaConfig, patch: SettingsPatch) -> list[str]:
    """Mutate `config` in place and write keys to the secrets files. Returns a list of
    founder-readable change notes. The caller saves config.yaml and reloads secrets."""
    notes: list[str] = []
    if patch.preset:
        if patch.preset not in MODEL_PRESETS:
            raise ValueError(f"preset desconhecido: {patch.preset}")
        preset = apply_preset(config, patch.preset)
        notes.append(f"preset “{preset.label}” aplicado aos tiers")
    for name, pp in patch.providers.items():
        cfg = config.providers.get(name) or ProviderConfig()
        if pp.kind is not None:
            cfg.kind = pp.kind  # type: ignore[assignment]
        if pp.base_url is not None:
            cfg.base_url = pp.base_url.strip()
        if pp.label is not None:
            cfg.label = pp.label.strip()
        if pp.api_key_env is not None:
            if looks_like_secret(pp.api_key_env):
                raise ValueError("api_key_env deve ser o nome da variável, não a chave")
            cfg.api_key_env = pp.api_key_env.strip()
        config.providers[name] = cfg
        if pp.clear_key and cfg.api_key_env:
            for scope in ("hub", "factory"):
                store_key(root, cfg.api_key_env, None, scope=scope)
            notes.append(f"chave de {name} removida")
        elif pp.api_key:
            if not cfg.api_key_env:
                raise ValueError(f"defina api_key_env para {name} antes de guardar a chave")
            store_key(root, cfg.api_key_env, pp.api_key.strip(), scope=pp.scope)
            notes.append(
                f"chave de {name} guardada ({'esta fábrica' if pp.scope == 'factory' else 'hub'})"
            )
    for name in patch.remove_providers:
        if config.providers.pop(name, None) is not None:
            notes.append(f"provedor {name} removido")
    if patch.tiers is not None:
        unknown = {c.provider for cs in patch.tiers.values() for c in cs} - set(config.providers)
        if unknown:
            raise ValueError("provedores desconhecidos nos tiers: " + ", ".join(sorted(unknown)))
        config.models.tiers = {t: list(cs) for t, cs in patch.tiers.items() if cs}
        notes.append("tiers atualizados")
    if patch.roles is not None:
        bad = {t for t in patch.roles.values() if t not in config.models.tiers}
        if bad:
            raise ValueError("tiers inexistentes no mapa de papéis: " + ", ".join(sorted(bad)))
        config.models.roles.update({r: t for r, t in patch.roles.items() if r})
        notes.append("mapa papel→tier atualizado")
    if patch.budget is not None:
        for k, v in patch.budget.model_dump(exclude_none=True).items():
            setattr(config.budget, k, v)
        notes.append("orçamento atualizado")
    if patch.max_parallel is not None:
        config.schedule.max_parallel = patch.max_parallel
    if patch.worker_backend is not None:
        if patch.worker_backend not in ("aci", "opencode"):
            raise ValueError("worker_backend deve ser 'aci' ou 'opencode'")
        config.worker.backend = patch.worker_backend  # type: ignore[assignment]
        notes.append(f"backend do Worker: {patch.worker_backend}")
    for name, tp in patch.tools.items():
        if name != "tavily":
            raise ValueError(f"ferramenta desconhecida: {name}")
        tool: ToolProviderConfig = config.tools.tavily
        if tp.enabled is not None:
            tool.enabled = tp.enabled
        if tp.clear_key:
            for scope in ("hub", "factory"):
                store_key(root, tool.api_key_env, None, scope=scope)
            notes.append("chave do Tavily removida")
        elif tp.api_key:
            store_key(root, tool.api_key_env, tp.api_key.strip(), scope=tp.scope)
            notes.append("chave do Tavily guardada")
    return notes
