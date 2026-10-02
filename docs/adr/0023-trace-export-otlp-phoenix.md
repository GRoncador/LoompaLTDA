# ADR-0023: The trace can also go out by OTLP, with Phoenix as the viewer

Date: 2026-10-01 · Status: accepted (Plano set/2026 · item 9: Fase 8.1, "Export OTLP, com o Phoenix
como visor"; builds on the per-story trace of Fase 8a)

## Context

The per-story trace (`.loompa/traces/<story>.jsonl`) has every model and tool call, and `loompa trace`
reads it in the terminal. A long story (S-006 of `tamagotchi-retro`: 1,298 spans) is hard to follow
there: a tree, the messages of each call, the tool results. The founder decided on 2026-09-30 to keep
the file as the record and send a copy, when asked for, to an OpenTelemetry viewer: Arize Phoenix,
running on the factory's own machine (prompts carry product code), with Langfuse or any other OTLP
backend possible by changing the endpoint.

## Decisions

1. **Off by default, an extra, a config block.** `loompa-core[trace]` brings only the OpenTelemetry SDK
   and the OTLP/HTTP exporter. `trace.otlp.endpoint` empty = off; `trace.otlp.headers` maps a header to
   the *name* of a variable in the secrets files (never the value); `trace.otlp.project` defaults to the
   factory slug. With an endpoint but without the extra, the engine logs it once and the local trace
   goes on.
2. **One instrumentation point, the same ids.** `Tracer._write_span` hands each finished span record to
   `trace_export.OtlpExporter`, which builds an OpenTelemetry `ReadableSpan` with the trace's own trace
   and span ids and parent, and queues it in a `BatchSpanProcessor`. The viewer's tree is the file's
   tree; a span id from `loompa trace` or a factory-health finding finds the same span in Phoenix.
   Messages are resolved from the hashes the tracer writes (a bounded cache of the recent ones).
3. **Both vocabularies.** OpenTelemetry GenAI (`gen_ai.operation.name`, `gen_ai.request.model`,
   `gen_ai.provider.name`, `gen_ai.usage.*`, `gen_ai.response.finish_reasons`, `gen_ai.input/output.messages`,
   `gen_ai.tool.*`) and OpenInference, which is what Phoenix draws (`openinference.span.kind` AGENT/CHAIN/
   LLM/TOOL, `llm.input_messages.N.message.*`, `llm.token_count.*`, `llm.cost.total`, `input.value`/
   `output.value`, `session.id` = the story). Loompa's own attributes go under `loompa.*`.
4. **Same redaction, never in the way.** Every string attribute passes through the secret redaction of
   the logs and the file. An export failure is logged once; tracing never breaks the work.
5. **Replay.** `loompa trace S-031 --otlp <url|config>` sends a story's existing file, for stories
   traced before the export was on.
6. **`loompa doctor`** warns (never blocks) when an endpoint is set and the extra is missing or the
   viewer does not answer.

## Verified live (2026-10-01)

Phoenix (`uvx --from arize-phoenix phoenix serve`, port 6006) received S-006 of `tamagotchi-retro`
replayed: 1,298 of 1,298 spans (20 node, 391 round, 391 model, 496 tool), no orphan parents, the story as
the session, model, provider, tokens, finish reason and readable messages in the span view.

## Consequences

- Phoenix shows "Total Cost $0" for these models: it prices from its own table, which does not know
  OpenRouter's models; the real cost is in `llm.cost.total` and `loompa.cost_usd`.
- A model call sends at most its last 60 messages as attributes (the file keeps all).
- Phoenix is ELv2 and is not shipped with Loompa; Loompa only speaks OTLP.
