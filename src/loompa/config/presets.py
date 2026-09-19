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
        description="Uma única chave para todos os modelos, com teto de gastos no painel da OpenRouter. GLM 5.3 no raciocínio, GLM Flash e DeepSeek Flash na execução — centavos por história, com modelos gratuitos como reserva.",
        tiers={
            "tier1": [
                _c("openrouter", "z-ai/glm-5.3"),
                _c("openrouter", "qwen/qwen3.8-max-0902"),
            ],
            "tier2": [
                _c("openrouter", "z-ai/glm-5.3-flash"),
                _c("openrouter", "deepseek/deepseek-v4-flash-0731"),
                _c("openrouter", "deepseek/deepseek-v4-flash-0731:free"),
            ],
        },
        providers=("openrouter",),
    ),
    "gratuito": ModelPreset(
        key="gratuito",
        label="Gratuito",
        description="Gemini 2.5 Flash-Lite e Flash no plano gratuito, com Groq e OpenRouter como reserva. Custo zero, limites de uso por minuto.",
        tiers={
            "tier1": [
                _c("gemini", "gemini-2.5-flash"),
                _c("groq", "llama-3.3-70b-versatile"),
                _c("openrouter", "deepseek/deepseek-r1:free"),
            ],
            "tier2": [
                _c("gemini", "gemini-3.5-flash-lite"),
                _c("groq", "llama-3.1-8b-instant"),
                _c("openrouter", "deepseek/deepseek-chat-v3-0324:free"),
            ],
        },
        providers=("gemini",),
        optional_providers=("groq", "openrouter"),
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
    config.models.tiers = {t: list(c) for t, c in preset.tiers.items()}
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
