"""The trace's optional OTLP copy (plan 8.1, item 9): same tree, GenAI + OpenInference attributes."""

from __future__ import annotations

from pathlib import Path

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from loompa.config.schema import OtlpConfig
from loompa.config.secrets import Secrets
from loompa.factory import Factory
from loompa.llm import Message
from loompa.llm.providers import ToolCall
from loompa.trace import Tracer
from loompa.trace_export import OtlpExporter
from test_engine import factory, make_ctx  # noqa: F401


def traced(tmp_path: Path) -> tuple[Tracer, InMemorySpanExporter]:
    memory = InMemorySpanExporter()
    tracer = Tracer(tmp_path / "traces", redact=lambda t: t.replace("sk-secret-123", "***"))
    tracer.exporter = OtlpExporter(
        memory,
        factory="contas",
        batch=False,
        redact=lambda t: t.replace("sk-secret-123", "***"),
    )
    return tracer, memory


def run_story(tracer: Tracer) -> None:
    """A node, a round, one model call that asks for a tool, and the tool call."""
    with (
        tracer.span("node", "dev", story_id="S-031", stage="DEV"),
        tracer.span("round", "1", agent="Worker Loompa"),
    ):
        with tracer.span("llm", "Worker Loompa", role="worker", max_tokens=4000) as llm:
            sent = [
                Message("system", "You are the Worker. key sk-secret-123"),
                Message("user", "Fix the bug"),
            ]
            llm.set(messages=tracer.messages(llm.story_id, sent))
            answer = Message(
                "assistant", "", tool_calls=[ToolCall("c1", "read_file", {"path": "a.py"})]
            )
            llm.set(
                provider="openrouter",
                model="deepseek/deepseek-v4-flash",
                finish_reason="tool_calls",
                input_tokens=1200,
                output_tokens=30,
                cost_usd=0.0004,
                response=tracer.messages(llm.story_id, [answer])[0],
            )
        with tracer.span("tool", "read_file", args={"path": "a.py"}) as tool:
            out = Message("tool", "print('hi')", tool_call_id="c1", name="read_file")
            tool.set(ok=True, result=tracer.messages("S-031", [out])[0])


def test_spans_keep_the_tree_and_carry_genai_and_openinference(tmp_path: Path):
    tracer, memory = traced(tmp_path)
    run_story(tracer)
    spans = {s.attributes["loompa.kind"]: s for s in memory.get_finished_spans()}
    node, rnd, llm, tool = spans["node"], spans["round"], spans["llm"], spans["tool"]
    # the same ids as the local file, so the tree in the viewer is the tree in `loompa trace`
    assert llm.parent.span_id == rnd.context.span_id and rnd.parent.span_id == node.context.span_id
    assert tool.parent.span_id == rnd.context.span_id and node.parent is None
    assert len({s.context.trace_id for s in spans.values()}) == 1
    local = (tmp_path / "traces" / "S-031.jsonl").read_text()
    assert f"{llm.context.span_id:016x}" in local
    a = llm.attributes
    assert llm.name == "chat deepseek/deepseek-v4-flash"
    assert a["gen_ai.operation.name"] == "chat" and a["gen_ai.provider.name"] == "openrouter"
    assert a["gen_ai.usage.input_tokens"] == 1200 and a["llm.token_count.total"] == 1230
    assert list(a["gen_ai.response.finish_reasons"]) == ["tool_calls"]
    assert a["openinference.span.kind"] == "LLM" and a["session.id"] == "S-031"
    assert a["llm.input_messages.1.message.content"] == "Fix the bug"
    assert a["llm.output_messages.0.message.tool_calls.0.tool_call.function.name"] == "read_file"
    assert (
        "sk-secret-123" not in str(dict(a)) and "***" in a["llm.input_messages.0.message.content"]
    )
    t = tool.attributes
    assert tool.name == "execute_tool read_file" and t["openinference.span.kind"] == "TOOL"
    assert t["output.value"] == "print('hi')" and '"a.py"' in t["input.value"]
    assert llm.resource.attributes["openinference.project.name"] == "contas"


def test_a_failed_span_is_an_error_and_a_written_file_can_be_replayed(tmp_path: Path):
    tracer, memory = traced(tmp_path)
    run_story(tracer)
    try:
        with tracer.span("node", "test", story_id="S-031"):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    failed = [s for s in memory.get_finished_spans() if s.name == "node test"]
    assert failed[0].status.status_code == StatusCode.ERROR
    replay = InMemorySpanExporter()
    sent = OtlpExporter(replay, factory="contas", batch=False).export_file(
        tmp_path / "traces" / "S-031.jsonl"
    )
    assert sent == 5 == len(replay.get_finished_spans())
    llm = next(s for s in replay.get_finished_spans() if s.attributes["loompa.kind"] == "llm")
    assert llm.attributes["llm.input_messages.1.message.content"] == "Fix the bug"


async def test_off_by_default_and_headers_come_from_the_secrets(factory: Factory, monkeypatch):  # noqa: F811
    assert OtlpExporter.from_config(OtlpConfig(), factory="f") is None
    ctx = make_ctx(factory, dry_run=True)
    assert ctx.tracer.exporter is None  # nothing configured, nothing sent
    await ctx.aclose()
    from opentelemetry.exporter.otlp.proto.http import trace_exporter

    made: dict = {}

    class Fake(InMemorySpanExporter):
        def __init__(self, **kw):
            super().__init__()
            made.update(kw)

    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", Fake)
    cfg = OtlpConfig(
        endpoint="http://127.0.0.1:6006/v1/traces", headers={"Authorization": "PHX_KEY"}
    )
    exp = OtlpExporter.from_config(cfg, factory="f", secrets=Secrets({"PHX_KEY": "Bearer x"}))
    assert exp is not None and made["endpoint"] == cfg.endpoint
    assert made["headers"] == {
        "Authorization": "Bearer x"
    }  # the value from the secrets, never config
    exp.close()
