# ADR-0002: LLM providers via OpenAI-compatible HTTP + native Anthropic; matrix in YAML

**Status:** accepted · **Date:** 2026-09-17

## Context

The brief lists a tiered model matrix (DeepSeek-R1/V3, Gemini Flash/Pro free tier,
Claude on critical demand) with a monthly budget of USD 10–30. Model names churn every few
months; the code must not hard-code them.

## Decision

- `loompa.llm.providers.OpenAICompatibleProvider` talks to any `/v1/chat/completions`
  endpoint with `httpx` (DeepSeek, Google AI Studio's OpenAI-compatible endpoint, Ollama,
  OpenRouter, Groq, vLLM). One adapter covers the whole Tier 1 / Tier 2 landscape.
- `loompa.llm.providers.AnthropicProvider` uses the Messages API directly (optional extra).
- The **model matrix** lives in `.loompa/config.yaml` under `models:` with named tiers
  (`tier1`, `tier2`) and an ordered list of candidates per tier. The router tries candidates
  in order and falls through on quota/5xx errors, so a free-tier exhaustion never stalls the
  day.
- Pricing per 1M tokens is also config (`pricing:`), consumed by the Finance Loompa. Defaults
  ship in `loompa/config/defaults.yaml` and are overridable per factory.
- Every call returns a `LLMResponse` carrying `usage` so cost accounting is exact, not
  estimated.

## Consequences

- No SDK lock-in, tiny dependency surface, deterministic tests with `respx`.
- Swapping DeepSeek for a local Ollama model is a YAML edit.
- Tier 3 (deterministic scripts: ruff, pytest, git) never touches this module — cost is $0.
