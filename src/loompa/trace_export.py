"""Export the per-story trace by OTLP/HTTP (plan 8.1, "Export OTLP, com o Phoenix como visor").

The trace (`loompa/trace.py`) stays the record: `.loompa/traces/<story>.jsonl`, always on. This
sends the same spans, as they are written, to an OpenTelemetry collector — the recommended viewer is
Arize Phoenix running on this machine (`phoenix serve`, port 6006), so prompts that carry product
code never leave it. Langfuse or any OTLP backend works by changing the endpoint and headers.

Off by default. On only when `trace.otlp.endpoint` is set and the `loompa-core[trace]` extra is
installed; the config holds the endpoint and, for headers, the *names* of the variables whose values
live in the secrets files, like every other key.

Each span keeps the trace's own ids, so the tree in the viewer is the tree in the file (node → task
→ round → model call / tool call), and the story is the session. Attributes follow the OpenTelemetry
GenAI conventions (`gen_ai.*`) and, for Phoenix's views, OpenInference (`openinference.span.kind`,
`llm.input_messages…`, `input.value`/`output.value`, `session.id`). Every string goes through the
same secret redaction as the file. Exporting never breaks the work it observes: a failure is logged
once and the local trace goes on.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_VALUE_CHARS = 20_000  # one message or tool result as an attribute
MAX_MESSAGES = 60  # messages of one model call sent as attributes (the newest)
KEEP_DOCS = 5_000  # messages remembered to resolve the hashes of the spans still to come

KIND = {"node": "AGENT", "task": "CHAIN", "round": "CHAIN", "llm": "LLM", "tool": "TOOL"}


class OtlpUnavailable(RuntimeError):
    """The `trace` extra is not installed."""


def _ns(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1_000_000_000)


def _clip(text: str, limit: int = MAX_VALUE_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + f"… (+{len(text) - limit} chars)"


def _scalar(value: Any) -> Any:
    """An attribute value OpenTelemetry accepts: scalars, lists of strings, else JSON text."""
    if isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, list | tuple) and all(isinstance(v, str) for v in value):
        return list(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def _message_attrs(prefix: str, docs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for i, d in enumerate(docs):
        p = f"{prefix}.{i}.message"
        out[f"{p}.role"] = d.get("role", "")
        if d.get("content"):
            out[f"{p}.content"] = _clip(str(d["content"]))
        if d.get("name"):
            out[f"{p}.name"] = d["name"]
        if d.get("tool_call_id"):
            out[f"{p}.tool_call_id"] = d["tool_call_id"]
        for j, call in enumerate(d.get("tool_calls") or []):
            c = f"{p}.tool_calls.{j}.tool_call"
            out[f"{c}.id"] = call.get("id", "")
            out[f"{c}.function.name"] = call.get("name", "")
            out[f"{c}.function.arguments"] = _clip(
                json.dumps(call.get("arguments"), ensure_ascii=False, default=str)
            )
    return out


def _semconv(docs: list[dict[str, Any]]) -> str:
    """Messages in the GenAI semantic conventions' shape (`gen_ai.input.messages`)."""
    out = []
    for d in docs:
        parts: list[dict[str, Any]] = []
        if d.get("content"):
            if d.get("role") == "tool":
                parts.append(
                    {
                        "type": "tool_call_response",
                        "id": d.get("tool_call_id", ""),
                        "response": _clip(str(d["content"])),
                    }
                )
            else:
                parts.append({"type": "text", "content": _clip(str(d["content"]))})
        for call in d.get("tool_calls") or []:
            parts.append(
                {
                    "type": "tool_call",
                    "id": call.get("id", ""),
                    "name": call.get("name", ""),
                    "arguments": call.get("arguments"),
                }
            )
        out.append({"role": d.get("role", ""), "parts": parts})
    return json.dumps(out, ensure_ascii=False, default=str)


def span_attributes(
    record: dict[str, Any], resolve: Callable[[str], dict[str, Any] | None]
) -> tuple[str, dict[str, Any]]:
    """The OTLP name and attributes of one trace span record (`trace.py`'s JSONL line)."""
    kind, name, attrs = record["kind"], record["name"], record.get("attrs") or {}
    out: dict[str, Any] = {
        "openinference.span.kind": KIND.get(kind, "CHAIN"),
        "loompa.kind": kind,
    }
    if record.get("story"):
        out["session.id"] = record["story"]
        out["loompa.story"] = record["story"]
    for key, value in attrs.items():
        if key in ("messages", "response", "result", "args"):
            continue
        out[f"loompa.{key}"] = _scalar(value)
    span_name = f"{kind} {name}"
    if kind == "llm":
        model = attrs.get("model") or ""
        span_name = f"chat {model}".strip()
        sent = [d for h in attrs.get("messages") or [] if (d := resolve(h))][-MAX_MESSAGES:]
        answer = resolve(attrs["response"]) if attrs.get("response") else None
        out.update(
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": model,
                "gen_ai.response.model": attrs.get("responded") or model,
                "gen_ai.provider.name": attrs.get("provider") or "",
                "llm.model_name": attrs.get("responded") or model,
                "llm.provider": attrs.get("provider") or "",
                "gen_ai.agent.name": name,
            }
        )
        for key, otel in (
            ("max_tokens", "gen_ai.request.max_tokens"),
            ("temperature", "gen_ai.request.temperature"),
        ):
            if attrs.get(key) is not None:
                out[otel] = attrs[key]
        if attrs.get("finish_reason"):
            out["gen_ai.response.finish_reasons"] = [str(attrs["finish_reason"])]
        tin, tout = int(attrs.get("input_tokens") or 0), int(attrs.get("output_tokens") or 0)
        if tin or tout:
            out.update(
                {
                    "gen_ai.usage.input_tokens": tin,
                    "gen_ai.usage.output_tokens": tout,
                    "llm.token_count.prompt": tin,
                    "llm.token_count.completion": tout,
                    "llm.token_count.total": tin + tout,
                }
            )
        if attrs.get("cached_tokens"):
            out["llm.token_count.prompt_details.cache_read"] = int(attrs["cached_tokens"])
        if attrs.get("reasoning_tokens"):
            out["llm.token_count.completion_details.reasoning"] = int(attrs["reasoning_tokens"])
        if attrs.get("cost_usd") is not None:
            out["llm.cost.total"] = float(attrs["cost_usd"])
        if sent:
            out.update(_message_attrs("llm.input_messages", sent))
            out["gen_ai.input.messages"] = _semconv(sent)
            out["input.value"] = _clip(
                json.dumps({"messages": sent}, ensure_ascii=False, default=str), 200_000
            )
            out["input.mime_type"] = "application/json"
        if answer:
            out.update(_message_attrs("llm.output_messages", [answer]))
            out["gen_ai.output.messages"] = _semconv([answer])
            out["output.value"] = _clip(str(answer.get("content") or "")) or _clip(
                json.dumps(answer.get("tool_calls"), ensure_ascii=False, default=str)
            )
    elif kind == "tool":
        span_name = f"execute_tool {name}"
        args = attrs.get("args")
        out.update(
            {
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
                "tool.name": name,
                "input.value": _clip(json.dumps(args, ensure_ascii=False, default=str)),
                "input.mime_type": "application/json",
                "gen_ai.tool.call.arguments": _clip(
                    json.dumps(args, ensure_ascii=False, default=str)
                ),
            }
        )
        result = resolve(attrs["result"]) if attrs.get("result") else None
        if result and result.get("content"):
            out["output.value"] = _clip(str(result["content"]))
            out["gen_ai.tool.call.result"] = out["output.value"]
    return span_name, out


class OtlpExporter:
    """Turns trace span records into OpenTelemetry spans with the same ids and sends them in
    batches. Build it with `from_config`; `export` and `close` never raise."""

    def __init__(
        self,
        span_exporter: Any,
        *,
        factory: str,
        project: str = "",
        redact: Callable[[str], str] | None = None,
        batch: bool = True,
    ):
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

        self.resource = Resource.create(
            {
                "service.name": "loompa",
                "loompa.factory": factory,
                "openinference.project.name": project or factory,
            }
        )
        self.processor = (BatchSpanProcessor if batch else SimpleSpanProcessor)(span_exporter)
        self.redact = redact or (lambda text: text)
        self._docs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._warned = False

    @classmethod
    def from_config(
        cls,
        cfg: Any,
        *,
        factory: str,
        secrets: Any = None,
        redact: Callable[[str], str] | None = None,
    ) -> OtlpExporter | None:
        """None when no endpoint is configured. Raises `OtlpUnavailable` when one is but the
        extra is missing (the caller says so once and goes on without it)."""
        endpoint = (getattr(cfg, "endpoint", "") or "").strip()
        if not endpoint:
            return None
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise OtlpUnavailable("pip install 'loompa-core[trace]'") from exc
        headers = {
            name: value
            for name, env in (getattr(cfg, "headers", None) or {}).items()
            if (value := (secrets.get(env) if secrets is not None else None))
        }
        return cls(
            OTLPSpanExporter(endpoint=endpoint, headers=headers or None, timeout=10),
            factory=factory,
            project=getattr(cfg, "project", "") or "",
            redact=redact,
        )

    # ------------------------------------------------------------------ input
    def remember(self, h: str, doc: dict[str, Any]) -> None:
        """A message the trace wrote: kept a while to resolve the hashes of the spans to come."""
        self._docs[h] = doc
        self._docs.move_to_end(h)
        while len(self._docs) > KEEP_DOCS:
            self._docs.popitem(last=False)

    def export(self, record: dict[str, Any]) -> None:
        try:
            self.processor.on_end(self._span(record))
        except Exception:  # noqa: BLE001 - tracing never breaks the work
            if not self._warned:
                self._warned = True
                log.exception("could not export a trace span by OTLP; the local trace goes on")

    def _span(self, record: dict[str, Any]) -> Any:
        from opentelemetry.sdk.trace import ReadableSpan
        from opentelemetry.sdk.util.instrumentation import InstrumentationScope
        from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags

        trace_id = int(record["trace"], 16)
        ctx = SpanContext(trace_id, int(record["id"], 16), True, TraceFlags(TraceFlags.SAMPLED))
        parent = (
            SpanContext(trace_id, int(record["parent"], 16), True, TraceFlags(TraceFlags.SAMPLED))
            if record.get("parent")
            else None
        )
        name, attrs = span_attributes(record, self._docs.get)
        attrs = {
            k: self.redact(v)
            if isinstance(v, str)
            else [self.redact(x) for x in v]
            if isinstance(v, list)
            else v
            for k, v in attrs.items()
        }
        status = (
            Status(StatusCode.ERROR, self.redact(record.get("error") or record["status"]))
            if record.get("status") in ("error", "cancelled")
            else Status(StatusCode.OK)
        )
        return ReadableSpan(
            name=name,
            context=ctx,
            parent=parent,
            resource=self.resource,
            attributes=attrs,
            kind=SpanKind.CLIENT if record["kind"] in ("llm", "tool") else SpanKind.INTERNAL,
            status=status,
            start_time=_ns(record["start"]),
            end_time=_ns(record["end"]),
            instrumentation_scope=InstrumentationScope("loompa.trace"),
        )

    # ------------------------------------------------------------------ files
    def export_file(self, path: Path) -> int:
        """Send a story's trace file that was written before (or without) the export. Returns
        how many spans went."""
        sent = 0
        with path.open(encoding="utf-8") as fh:
            for line in fh:  # a message is always written before the spans that use it
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("t") == "msg":
                    self.remember(rec["h"], {k: v for k, v in rec.items() if k not in ("t", "h")})
                elif rec.get("t") == "span":
                    self.export(rec)
                    sent += 1
        return sent

    def close(self, timeout_ms: int = 10_000) -> None:
        try:
            self.processor.force_flush(timeout_ms)
            self.processor.shutdown()
        except Exception:  # noqa: BLE001
            log.exception("could not flush the OTLP export")
