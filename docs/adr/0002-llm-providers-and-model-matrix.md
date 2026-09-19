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

## Addendum (2026-09-19): provider metadata that must come back with a tool call

Gemini 3 models attach a *thought signature* to every function call they make (OpenAI-compatible
endpoint: `tool_calls[].extra_content.google.thought_signature`) and answer `400 Function call is
missing a thought_signature` when the next request carries the call without it. The first live
brainstorm hit this on its second request, right after a `list_dir`. The Worker's live cycle had
passed with the same model; the likely reason is that Gemini did not sign those simpler calls, but that
was not confirmed.

- `ToolCall.extra` keeps whatever the provider attached (`extra_content`), out of `repr` and of
  equality, and `OpenAICompatibleProvider` puts it back when the call is replayed. It is sent to Google
  endpoints only: other OpenAI-compatible servers may reject unknown message fields.
- A history the model did not write (the router fell through from another provider halfway through a
  loop) has calls without a signature. On exactly that 400, and only for those calls, the adapter
  retries once with Google's documented bypass value. The shared history is never rewritten.
- A failed chat turn tells the founder only "try again"; the technical reason is the
  `conversation.error` event, and the `live` tests print it when they fail.
