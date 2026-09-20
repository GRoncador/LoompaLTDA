"""Model presets offered during onboarding and in the dashboard settings.

Each preset is a full ``models.tiers`` map plus the providers it needs. Founders pick one,
enter the matching keys, and can edit the result freely afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loompa.config.schema import LoompaConfig, ModelCandidate


@dataclass(frozen=True)
class ModelPreset:
    key: str
    label: str
    description: str  # pt-BR, founder-facing
    tiers: dict[str, list[ModelCandidate]]
    providers: tuple[str, ...]  # providers that need a key for this preset to work
    matrix: dict[str, dict[str, list[ModelCandidate]]] = field(default_factory=dict)
    optional_providers: tuple[str, ...] = field(default_factory=tuple)


def _c(provider: str, model: str) -> ModelCandidate:
    return ModelCandidate(provider=provider, model=model)


MODEL_PRESETS: dict[str, ModelPreset] = {
    # First entry: what onboarding offers by default. One key covers every tier, and the
    # spending cap lives in the OpenRouter dashboard as well as in `budget`.
    "openrouter": ModelPreset(
        key="openrouter",
        label="OpenRouter (recomendado)",
        description="Uma única chave para todos os modelos, com teto de gastos no painel da OpenRouter. GLM, Grok e GPT Sol no raciocínio; GLM Flash, GPT Luna e Gemini Flash na execução, sempre na versão mais nova de cada fabricante (ids -latest), então uma versão que sai do ar não trava a fábrica. Modelo gratuito como última reserva.",
        tiers={
            "tier1": [
                _c("openrouter", "~z-ai/glm-latest"),
                _c("openrouter", "~x-ai/grok-latest"),
                _c("openrouter", "~openai/gpt-sol-latest"),
            ],
            "tier2": [
                _c("openrouter", "~z-ai/glm-flash-latest"),
                _c("openrouter", "~openai/gpt-luna-latest"),
                _c("openrouter", "~google/gemini-flash-latest"),
                _c("openrouter", "openrouter/free"),
            ],
        },
        matrix={
            "strategy": {
                "tier1": [
                    _c("openrouter", "~z-ai/glm-latest"),
                    _c("openrouter", "~x-ai/grok-latest"),
                    _c("openrouter", "~openai/gpt-sol-latest"),
                ],
                "tier2": [
                    _c("openrouter", "~z-ai/glm-flash-latest"),
                    _c("openrouter", "~openai/gpt-luna-latest"),
                    _c("openrouter", "~google/gemini-flash-latest"),
                ],
                "tier3": [
                    _c("openrouter", "openrouter/free"),
                ],
            },
            "engineering": {
                "tier1": [
                    _c("openrouter", "~openai/gpt-sol-latest"),
                    _c("openrouter", "~z-ai/glm-latest"),
                    _c("openrouter", "~x-ai/grok-latest"),
                ],
                "tier2": [
                    _c("openrouter", "~z-ai/glm-flash-latest"),
                    _c("openrouter", "~openai/gpt-luna-latest"),
                    _c("openrouter", "~google/gemini-flash-latest"),
                ],
                "tier3": [
                    _c("openrouter", "openrouter/free"),
                ],
            },
            "routine": {
                "tier1": [
                    _c("openrouter", "~z-ai/glm-flash-latest"),
                    _c("openrouter", "~openai/gpt-luna-latest"),
                ],
                "tier2": [
                    _c("openrouter", "~google/gemini-flash-latest"),
                    _c("openrouter", "openrouter/free"),
                ],
                "tier3": [
                    _c("openrouter", "openrouter/free"),
                ],
            },
        },
        providers=("openrouter",),
    ),
    "gratuito": ModelPreset(
        key="gratuito",
        label="Gratuito",
        description="Gemini no plano gratuito, com Groq como reserva. Custo zero, limites de uso por minuto.",
        tiers={
            "tier1": [
                _c("gemini", "gemini-3.8-flash"),
                _c("groq", "llama-3.3-70b-versatile"),
            ],
            "tier2": [
                _c("gemini", "gemini-3.5-flash-lite"),
                _c("groq", "llama-3.1-8b-instant"),
            ],
        },
        matrix={
            "strategy": {
                "tier1": [_c("gemini", "gemini-3.8-flash"), _c("groq", "llama-3.3-70b-versatile")],
                "tier2": [_c("gemini", "gemini-3.5-flash-lite"), _c("groq", "llama-3.1-8b-instant")],
                "tier3": [_c("groq", "llama-3.1-8b-instant")],
            },
            "engineering": {
                "tier1": [_c("gemini", "gemini-3.8-flash"), _c("groq", "llama-3.3-70b-versatile")],
                "tier2": [_c("gemini", "gemini-3.5-flash-lite"), _c("groq", "llama-3.1-8b-instant")],
                "tier3": [_c("groq", "llama-3.1-8b-instant")],
            },
            "routine": {
                "tier1": [_c("gemini", "gemini-3.8-flash")],
                "tier2": [_c("gemini", "gemini-3.5-flash-lite")],
                "tier3": [_c("groq", "llama-3.1-8b-instant")],
            },
        },
        providers=("gemini",),
        optional_providers=("groq",),
    ),
    "economico": ModelPreset(
        key="economico",
        label="Econômico",
        description="DeepSeek para raciocínio e execução, Gemini Flash-Lite como reserva. Centavos por história.",
        tiers={
            "tier1": [_c("deepseek", "deepseek-reasoner"), _c("gemini", "gemini-3.8-flash")],
            "tier2": [_c("deepseek", "deepseek-chat"), _c("gemini", "gemini-3.5-flash-lite")],
        },
        matrix={
            "strategy": {
                "tier1": [_c("deepseek", "deepseek-reasoner"), _c("gemini", "gemini-3.8-flash")],
                "tier2": [_c("deepseek", "deepseek-chat"), _c("gemini", "gemini-3.5-flash-lite")],
                "tier3": [_c("gemini", "gemini-3.5-flash-lite")],
            },
            "engineering": {
                "tier1": [_c("deepseek", "deepseek-reasoner"), _c("gemini", "gemini-3.8-flash")],
                "tier2": [_c("deepseek", "deepseek-chat"), _c("gemini", "gemini-3.5-flash-lite")],
                "tier3": [_c("gemini", "gemini-3.5-flash-lite")],
            },
            "routine": {
                "tier1": [_c("gemini", "gemini-3.8-flash")],
                "tier2": [_c("deepseek", "deepseek-chat")],
                "tier3": [_c("gemini", "gemini-3.5-flash-lite")],
            },
        },
        providers=("deepseek",),
        optional_providers=("gemini",),
    ),
    "maximo": ModelPreset(
        key="maximo",
        label="Máximo",
        description="Claude Opus 5 no raciocínio e Claude Sonnet 5 na execução, com Gemini Flash como reserva. Melhor qualidade, maior custo.",
        tiers={
            "tier1": [_c("anthropic", "claude-opus-5"), _c("gemini", "gemini-3.8-flash")],
            "tier2": [_c("anthropic", "claude-sonnet-5"), _c("gemini", "gemini-3.8-flash")],
        },
        matrix={
            "strategy": {
                "tier1": [_c("anthropic", "claude-opus-5"), _c("gemini", "gemini-3.8-flash")],
                "tier2": [_c("anthropic", "claude-sonnet-5"), _c("gemini", "gemini-3.8-flash")],
                "tier3": [_c("gemini", "gemini-3.5-flash-lite")],
            },
            "engineering": {
                "tier1": [_c("anthropic", "claude-opus-5"), _c("gemini", "gemini-3.8-flash")],
                "tier2": [_c("anthropic", "claude-sonnet-5"), _c("gemini", "gemini-3.8-flash")],
                "tier3": [_c("gemini", "gemini-3.5-flash-lite")],
            },
            "routine": {
                "tier1": [_c("gemini", "gemini-3.8-flash")],
                "tier2": [_c("gemini", "gemini-3.5-flash-lite")],
                "tier3": [_c("gemini", "gemini-3.5-flash-lite")],
            },
        },
        providers=("anthropic",),
        optional_providers=("gemini",),
    ),
}


def apply_preset(config: LoompaConfig, key: str) -> ModelPreset:
    preset = MODEL_PRESETS[key]
    config.models.tiers = {t: [c.model_copy() for c in cs] for t, cs in preset.tiers.items()}
    if preset.matrix:
        config.models.matrix = {
            c: {t: [cand.model_copy() for cand in cs] for t, cs in t_dict.items()}
            for c, t_dict in preset.matrix.items()
        }
    else:
        config.models.matrix = {
            cluster: {
                "tier1": [c.model_copy() for c in preset.tiers.get("tier1", [])],
                "tier2": [c.model_copy() for c in preset.tiers.get("tier2", [])],
                "tier3": [c.model_copy() for c in preset.tiers.get("tier3", [])]
                or [
                    c.model_copy()
                    for c in preset.tiers.get("tier2", [])
                    if c.model == "openrouter/free" or c.model.endswith(":free")
                ],
            }
            for cluster in ("strategy", "engineering", "routine")
        }
    config.models.preset = key
    return preset


def preset_summaries() -> list[dict[str, object]]:
    return [
        {
            "key": p.key,
            "label": p.label,
            "description": p.description,
            "providers": list(p.providers),
            "optional_providers": list(p.optional_providers),
            "tiers": {t: [c.model_dump() for c in cs] for t, cs in p.tiers.items()},
            "matrix": {
                c: {t: [cand.model_dump() for cand in cs] for t, cs in t_dict.items()}
                for c, t_dict in p.matrix.items()
            },
        }
        for p in MODEL_PRESETS.values()
    ]
