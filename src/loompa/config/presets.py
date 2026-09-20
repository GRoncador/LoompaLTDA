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
            # `~vendor/model-latest` follows the vendor's newest version, so a retired version
            # cannot leave a tier with dead ids. Ranked with the `loompa models sync` rules on
            # 2026-09-20 and each one answered a text and a tool call live. GLM leads both tiers:
            # it is the one Loompa's own flows were validated on.
            "tier1": [
                _c("openrouter", "~z-ai/glm-latest"),
                _c("openrouter", "~x-ai/grok-latest"),
                _c("openrouter", "~openai/gpt-sol-latest"),
            ],
            "tier2": [
                _c("openrouter", "~z-ai/glm-flash-latest"),
                _c("openrouter", "~openai/gpt-luna-latest"),
                _c("openrouter", "~google/gemini-flash-latest"),
                _c("openrouter", "openrouter/free"),  # a free model picked per request
            ],
        },
        providers=("openrouter",),
    ),
    "gratuito": ModelPreset(
        key="gratuito",
        label="Gratuito",
        description="Gemini no plano gratuito, com Groq como reserva. Custo zero, limites de uso por minuto.",
        tiers={
            "tier1": [
                _c("gemini", "gemini-2.5-flash"),
                _c("groq", "llama-3.3-70b-versatile"),
            ],
            "tier2": [
                _c("gemini", "gemini-3.5-flash-lite"),
                _c("groq", "llama-3.1-8b-instant"),
            ],
        },
        providers=("gemini",),
        optional_providers=("groq",),
    ),
    "economico": ModelPreset(
        key="economico",
        label="Econômico",
        description="DeepSeek para raciocínio e execução, Gemini Flash-Lite como reserva. Centavos por história.",
        tiers={
            "tier1": [_c("deepseek", "deepseek-reasoner"), _c("gemini", "gemini-2.5-pro")],
            "tier2": [_c("deepseek", "deepseek-chat"), _c("gemini", "gemini-3.5-flash-lite")],
        },
        providers=("deepseek",),
        optional_providers=("gemini",),
    ),
    "maximo": ModelPreset(
        key="maximo",
        label="Máximo",
        description="Claude Opus 5 e Gemini 2.5 Pro no tier de raciocínio; Claude Sonnet 5 e Gemini Flash na execução. Melhor qualidade, maior custo.",
        tiers={
            "tier1": [_c("anthropic", "claude-opus-5"), _c("gemini", "gemini-2.5-pro")],
            "tier2": [_c("anthropic", "claude-sonnet-5"), _c("gemini", "gemini-2.5-flash")],
        },
        providers=("anthropic",),
        optional_providers=("gemini",),
    ),
}


def apply_preset(config: LoompaConfig, key: str) -> ModelPreset:
    preset = MODEL_PRESETS[key]
    config.models.tiers = {t: [c.model_copy() for c in cs] for t, cs in preset.tiers.items()}
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
        }
        for p in MODEL_PRESETS.values()
    ]
