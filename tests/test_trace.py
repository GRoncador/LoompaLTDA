"""The per-story trace (Fase 8.1): spans nest by context, messages are stored once, secrets never
reach the file, and a scripted story leaves the whole tree behind — no network."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import pytest
import respx

from conftest import git
from loompa.agents import ProductOwnerAgent
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.config import default_config
from loompa.config.schema import ModelCandidate
from loompa.engine import EngineContext, Scheduler
from loompa.factory import Factory, bootstrap_factory
from loompa.finance import CostTracker
from loompa.llm import LLMError, Message, MockProvider, ModelRouter, ToolCall
from loompa.llm.providers import LLMResponse
from loompa.store import Store
from loompa.trace import Tracer, read_trace


def spans_of(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if '"t":"span"' in line]


# ----------------------------------------------------------------------------- tracer


def test_spans_nest_by_context_and_land_in_the_story_file(tmp_path: Path):
    tracer = Tracer(tmp_path / "traces")
    with tracer.span("node", "dev", story_id="S-001") as node:
        with tracer.span("task", "T1", task=1) as task, tracer.span("llm", "worker") as call:
            call.set(model="m")
        assert tracer.current() is node
    assert tracer.current() is None
    spans = {s["kind"]: s for s in spans_of(tmp_path / "traces" / "S-001.jsonl")}
    assert spans["task"]["parent"] == node.id and spans["llm"]["parent"] == task.id
    assert spans["node"]["parent"] is None
    assert len({s["trace"] for s in spans.values()}) == 1  # one tree, one trace id
    assert spans["llm"]["attrs"] == {"model": "m"} and call.story_id == "S-001"
    # the folder keeps itself out of the repository, in factories onboarded before it existed
    assert (tmp_path / "traces" / ".gitignore").read_text().strip().endswith("*")


def test_a_failing_span_records_the_error_and_calls_outside_a_story_go_to_the_factory_file(
    tmp_path: Path,
):
    tracer = Tracer(tmp_path)
    with pytest.raises(ValueError), tracer.span("llm", "master"):
        raise ValueError("boom")
    (factory_file,) = tmp_path.glob("_factory-*.jsonl")
    (span,) = spans_of(factory_file)
    assert span["status"] == "error" and "ValueError: boom" in span["error"]


def test_messages_are_written_once_and_referenced_by_hash(tmp_path: Path):
    tracer = Tracer(tmp_path)
    system = Message("system", "rules " * 500)
    first = tracer.messages("S-1", [system, Message("user", "task 1")])
    second = tracer.messages("S-1", [system, Message("user", "task 1"), Message("user", "more")])
    assert first == second[:2] and len(set(second)) == 3
    text = (tmp_path / "S-1.jsonl").read_text()
    assert text.count('"t":"msg"') == 3  # the system prompt is stored once, not per round
    # a new process reads back what the file already holds instead of writing it again
    again = Tracer(tmp_path)
    again.messages("S-1", [system])
    assert (tmp_path / "S-1.jsonl").read_text().count('"t":"msg"') == 3
    trace = read_trace(tmp_path / "S-1.jsonl")
    assert trace.messages[first[0]]["content"].startswith("rules")


def test_secrets_never_reach_the_file(tmp_path: Path):
    from loompa.config.secrets import redact_secrets

    tracer = Tracer(tmp_path, redact=lambda t: redact_secrets(t, ["my-own-secret-value"]))
    with tracer.span("tool", "read_file", story_id="S-1", args={"path": ".env"}) as sp:
        sp.set(output="OPENROUTER_API_KEY=my-own-secret-value")
    tracer.messages("S-1", [Message("tool", "found sk-or-v1-abcdefghijklmnopqrstuvwxyz0123")])
    text = (tmp_path / "S-1.jsonl").read_text()
    assert "my-own-secret-value" not in text and "abcdefghijklmnopqrstuvwxyz" not in text
    assert "…alue" in text and "…0123" in text


def test_old_traces_are_pruned(tmp_path: Path):
    tracer = Tracer(tmp_path)
    with tracer.span("node", "dev", story_id="S-old"):
        pass
    with tracer.span("node", "dev", story_id="S-new"):
        pass
    old = tmp_path / "S-old.jsonl"
    month_ago = time.time() - 31 * 86_400
    os.utime(old, (month_ago, month_ago))
    assert tracer.prune(30) == 1
    assert not old.exists() and (tmp_path / "S-new.jsonl").exists()


def test_a_tracer_without_a_folder_hands_out_spans_and_writes_nothing(tmp_path: Path):
    tracer = Tracer()
    with tracer.span("llm", "worker", story_id="S-1") as sp:
        assert tracer.messages("S-1", [Message("user", "x")]) == []
    assert sp.id and not list(tmp_path.iterdir())


# ----------------------------------------------------------------------------- router


def _one_model_config(model: str = "m1") -> object:
    cfg = default_config()
    cfg.models.tiers = {"tier2": [ModelCandidate(provider="p", model=model)]}
    cfg.models.matrix = {}
    cfg.models.full_output_tokens = cfg.models.light_output_tokens = 900  # room to double
    cfg.models.max_output_ceiling = 32768
    return cfg


async def test_a_call_that_never_answered_still_shows_what_it_cost_and_what_it_tried(
    tmp_path: Path,
):
    """Smoke run, 2026-09-30: six cut attempts on two models cost US$0.05, and `loompa trace`
    showed the failed call as '? · out 0 · US$ 0.0000'."""
    from loompa.cli.trace import _llm_line
    from loompa.trace import TraceNode

    cfg = _one_model_config()
    cfg.models.truncation_retries = 1
    router = ModelRouter(
        cfg,
        tracker=CostTracker(Store(":memory:"), cfg, "f"),
        providers={
            "p": MockProvider(
                "p",
                script=lambda *a: LLMResponse(
                    "", [], "m1", "p", 100, 900, finish_reason="length", cost_usd=0.01
                ),
            )
        },
        tracer=Tracer(tmp_path),
    )
    with pytest.raises(LLMError):
        await router.complete("architect", [Message("user", "go")], story_id="S-1", max_tokens=900)
    (span,) = spans_of(tmp_path / "S-1.jsonl")
    assert span["attrs"]["cost_usd"] == pytest.approx(0.02)
    line = _llm_line(TraceNode(span))
    assert "m1" in line and "out 1.8k" in line and "US$ 0.0200" in line and "2 corte(s)" in line


async def test_a_long_answer_keeps_the_tail_of_its_reasoning_even_when_it_finishes(
    tmp_path: Path,
):
    cfg = _one_model_config()
    long_one = LLMResponse(
        "veredito",
        [],
        "m1",
        "p",
        100,
        20000,
        finish_reason="stop",
        reasoning_tokens=19000,
        raw={"choices": [{"message": {"reasoning": "y" * 1000 + "and so, PASS"}}]},
    )
    short_one = LLMResponse("ok", [], "m1", "p", 100, 50, finish_reason="stop", reasoning_tokens=40)
    answers = iter([long_one, short_one])
    router = ModelRouter(
        cfg,
        tracker=CostTracker(Store(":memory:"), cfg, "f"),
        providers={"p": MockProvider("p", script=lambda *a: next(answers))},
        tracer=Tracer(tmp_path),
    )
    await router.complete("inspector", [Message("user", "a")], story_id="S-1")
    await router.complete("inspector", [Message("user", "b")], story_id="S-1")
    first, second = (s["attrs"]["attempts"][0] for s in spans_of(tmp_path / "S-1.jsonl"))
    assert first["reasoning_tail"].endswith("and so, PASS") and "reasoning_tail" not in second
    # the longer end is a message of the trace, by reference (a span attribute is capped at 2k)
    kept = read_trace(tmp_path / "S-1.jsonl").messages[first["reasoning"]]
    assert kept["content"] == "y" * 1000 + "and so, PASS"


async def test_a_cut_answer_and_its_retry_are_one_call_with_two_attempts(tmp_path: Path):
    cfg = _one_model_config()
    answers = iter(
        [
            LLMResponse(
                "",
                [],
                "m1",
                "p",
                100,
                900,
                finish_reason="length",
                reasoning_tokens=700,
                raw={  # the half-written tool call only exists in the raw message
                    "choices": [
                        {
                            "message": {
                                "reasoning": "x" * 2000 + "and again, the plan",
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "plan",
                                            "arguments": '{"tasks": [1, 1, 1',
                                        }
                                    }
                                ],
                            }
                        }
                    ]
                },
            ),
            LLMResponse("done", [], "m1", "p", 100, 50, finish_reason="stop"),
        ]
    )
    events: list[tuple[str, dict]] = []
    router = ModelRouter(
        cfg,
        tracker=CostTracker(Store(":memory:"), cfg, "f"),
        providers={"p": MockProvider("p", script=lambda *a: next(answers))},
        tracer=Tracer(tmp_path),
        on_event=lambda t, **kw: events.append((t, kw)),
    )
    rc = await router.complete("worker", [Message("user", "go")], story_id="S-1", max_tokens=900)
    assert rc.cuts == 1 and rc.attempts == 2 and rc.span_id
    (span,) = spans_of(tmp_path / "S-1.jsonl")
    attempts = span["attrs"]["attempts"]
    assert [a["outcome"] for a in attempts] == ["cut", "ok"]
    # what the cut attempt was writing, so a runaway plan can be told from a long thought
    assert attempts[0]["tail"] == '{"tasks": [1, 1, 1' and attempts[0]["reasoning_tokens"] == 700
    assert attempts[0]["reasoning_tail"].endswith("and again, the plan")
    assert len(attempts[0]["reasoning_tail"]) == 600 and "tail" not in attempts[1]
    first = attempts[0]["max_tokens"]
    assert attempts[1]["max_tokens"] == 2 * first  # more room after the cut
    assert [t for t, _ in events] == ["llm.cut"] and events[0][1]["next_max_tokens"] == 2 * first
    assert span["id"] == rc.span_id and span["attrs"]["finish_reason"] == "stop"
    # both attempts were paid for, and the call says so
    assert span["attrs"]["cost_usd"] == pytest.approx(sum(a["cost_usd"] for a in attempts))
    assert rc.cost_usd == pytest.approx(span["attrs"]["cost_usd"])
    trace = read_trace(tmp_path / "S-1.jsonl")
    assert trace.messages[span["attrs"]["response"]]["content"] == "done"


async def test_fall_through_is_an_attempt_and_an_event(tmp_path: Path):
    cfg = default_config()
    cfg.models.tiers = {
        "tier2": [
            ModelCandidate(provider="a", model="bad"),
            ModelCandidate(provider="b", model="ok"),
        ]
    }
    cfg.models.matrix = {}
    events: list[str] = []
    router = ModelRouter(
        cfg,
        providers={
            "a": MockProvider("a", script=lambda *a: LLMError("400 nope", status=400)),
            "b": MockProvider("b", script=lambda *a: "fine"),
        },
        tracer=Tracer(tmp_path),
        on_event=lambda t, **kw: events.append(t),
        max_retries=0,
    )
    rc = await router.complete("worker", [Message("user", "go")], story_id="S-2")
    assert rc.candidate.model == "ok" and events == ["llm.fallthrough"]
    (span,) = spans_of(tmp_path / "S-2.jsonl")
    assert [a["outcome"] for a in span["attrs"]["attempts"]] == ["error", "ok"]
    assert span["attrs"]["attempts"][0]["model"] == "a/bad"


async def test_a_call_no_model_answers_is_an_error_span(tmp_path: Path):
    router = ModelRouter(
        _one_model_config(),
        providers={"p": MockProvider("p", script=lambda *a: LLMError("boom"))},
        tracer=Tracer(tmp_path),
        max_retries=0,
    )
    with pytest.raises(LLMError):
        await router.complete("worker", [Message("user", "go")], story_id="S-3")
    (span,) = spans_of(tmp_path / "S-3.jsonl")
    assert span["status"] == "error" and span["attrs"]["attempts"][0]["outcome"] == "error"


# -------------------------------------------------------------------------- real cost


OPENROUTER_OK = {
    "id": "gen-1",
    "provider": "Wafer",
    "model": "deepseek/deepseek-v4-flash-0731",
    "choices": [{"finish_reason": "stop", "message": {"content": "hi"}}],
    "usage": {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "cost": 0.000105,
        "completion_tokens_details": {"reasoning_tokens": 40},
    },
}


@respx.mock
async def test_the_bill_openrouter_reports_is_the_cost(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    cfg = default_config()
    cfg.models.tiers = {
        "tier2": [ModelCandidate(provider="openrouter", model="deepseek/deepseek-v4-flash-0731")]
    }
    cfg.models.matrix = {}
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OPENROUTER_OK)
    )
    store = Store(":memory:")
    router = ModelRouter(cfg, tracker=CostTracker(store, cfg, "f"))
    rc = await router.complete("worker", [Message("user", "hi")], story_id="S-1")
    await router.aclose()
    assert rc.response.served_by == "Wafer" and rc.response.reasoning_tokens == 40
    assert rc.cost_usd == pytest.approx(0.000105)
    (row,) = store._q("SELECT cost_usd, cost_source, served_by, finish_reason FROM usage")
    assert row == {
        "cost_usd": pytest.approx(0.000105),
        "cost_source": "reported",
        "served_by": "Wafer",
        "finish_reason": "stop",
    }


def test_without_a_reported_bill_the_price_table_decides():
    cfg = default_config()
    store = Store(":memory:")
    tracker = CostTracker(store, cfg, "f")
    from loompa.finance import UsageRecord

    table = tracker.record(
        UsageRecord("a", "worker", "deepseek", "deepseek-chat", "tier2", 1000, 100)
    )
    zero = tracker.record(
        UsageRecord("a", "worker", "openrouter", "x:free", "tier3", 1000, 100, reported_cost=0.0)
    )
    rows = store._q("SELECT cost_source FROM usage ORDER BY id")
    assert table > 0 and zero == 0.0
    assert [r["cost_source"] for r in rows] == ["table", "reported"]


def test_byok_adds_what_the_upstream_provider_billed():
    from loompa.llm.providers import reported_cost

    assert reported_cost({"cost": 0.0005}) == pytest.approx(0.0005)
    byok = {"cost": 0.00005, "is_byok": True, "cost_details": {"upstream_inference_cost": 0.001}}
    assert reported_cost(byok) == pytest.approx(0.00105)
    assert reported_cost({}) is None and reported_cost({"cost": True}) is None


def test_old_usage_tables_gain_the_new_columns(tmp_path: Path):
    import sqlite3

    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, factory TEXT NOT NULL, "
        "story_id TEXT, agent TEXT NOT NULL, role TEXT NOT NULL, provider TEXT NOT NULL, "
        "model TEXT NOT NULL, tier TEXT NOT NULL, input_tokens INTEGER NOT NULL DEFAULT 0, "
        "output_tokens INTEGER NOT NULL DEFAULT 0, cached_tokens INTEGER NOT NULL DEFAULT 0, "
        "cost_usd REAL NOT NULL DEFAULT 0, duration_ms INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()
    store = Store(db)
    store.record_usage(
        factory="f", agent="a", role="r", provider="p", model="m", tier="t", served_by="X"
    )
    assert store._q("SELECT served_by FROM usage") == [{"served_by": "X"}]


# ------------------------------------------------------------------------ a whole story


@pytest.fixture
def factory(git_repo: Path, hub) -> Factory:
    (git_repo / "app").mkdir()
    (git_repo / "app" / "__init__.py").write_text("")
    (git_repo / "app" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git("add", ".", cwd=git_repo)
    git("commit", "-qm", "feat: calc", cwd=git_repo)
    f = bootstrap_factory(git_repo, name="Demo", store=hub).factory
    f.config.quality.test_command = ""
    f.config.quality.lint_command = ""
    f.save()
    return Factory.open(git_repo)


def worker_reads_then_finishes(model: str, messages: list[Message], tools) -> object:
    if role_of(messages) != "worker":
        return dry_run_script(model, messages, tools)
    if not any(m.role == "tool" for m in messages):
        return [ToolCall("c1", "search", {"pattern": "def add"})]
    if sum(m.role == "tool" for m in messages) == 1:
        return [ToolCall("c2", "read_file", {"path": "app/calc.py"})]
    return [ToolCall("c3", "done", {"summary": "ok"})]


async def test_a_story_leaves_its_tree_node_task_round_call_and_tool(factory: Factory):
    provider = MockProvider("mock", script=worker_reads_then_finishes)
    router = ModelRouter(
        factory.config, providers=dict.fromkeys(factory.config.providers, provider)
    )
    ctx = EngineContext.build(factory, router=router, dry_run=True)
    po = ProductOwnerAgent(ctx)
    sid = po.add_item("Somar números", "A calculadora soma dois números.").story_id
    po.admit(sid)
    await Scheduler(ctx).run(until_idle=True)
    trace = read_trace(factory.paths.traces / f"{sid}.jsonl")
    roots = trace.tree()
    assert {r.span["name"] for r in roots} >= {"intake", "spec", "plan", "dev", "test", "review"}
    assert all(r.kind == "node" for r in roots)
    dev = next(r for r in roots if r.span["name"] == "dev")
    (task,) = [c for c in dev.children if c.kind == "task"]
    assert task.attrs["origin"] == "plan" and task.attrs["task"] == 1
    rounds = [c for c in task.children if c.kind == "round"]
    assert len(rounds) == 3 and all(r.attrs.get("label") == "main" for r in rounds)
    first = rounds[0]
    assert [c.kind for c in first.children] == ["llm", "tool"]
    tool = first.children[1]
    assert tool.span["name"] == "search" and tool.attrs["args"] == {"pattern": "def add"}
    assert trace.messages[tool.attrs["result"]]["role"] == "tool"
    # the light events point into the trace
    events = ctx.store.events_since(0, limit=2000, factory=ctx.slug)
    span_ids = {s["id"] for s in trace.spans}
    tool_events = [e for e in events if e["type"] == "tool.call" and e["story_id"] == sid]
    llm_events = [e for e in events if e["type"] == "llm.call" and e["story_id"] == sid]
    assert tool_events and all(e["payload"]["span_id"] in span_ids for e in tool_events)
    assert llm_events and all(e["payload"]["span_id"] in span_ids for e in llm_events)
    assert tool_events[0]["payload"]["query"] == "def add"
    assert all("finish_reason" in e["payload"] for e in llm_events)
    started = [e for e in events if e["type"] == "worker.task_started" and e["story_id"] == sid]
    finished = [e for e in events if e["type"] == "worker.task_finished" and e["story_id"] == sid]
    assert started[0]["payload"]["origin"] == "plan"
    assert finished[0]["payload"]["outcome"] == "finished"
    assert finished[0]["payload"]["duration_s"] >= 0
    await ctx.aclose()


async def test_loompa_trace_reads_the_tree_one_task_one_call_and_the_stats(factory: Factory):
    from typer.testing import CliRunner

    from loompa.cli.main import app

    provider = MockProvider("mock", script=worker_reads_then_finishes)
    router = ModelRouter(
        factory.config, providers=dict.fromkeys(factory.config.providers, provider)
    )
    ctx = EngineContext.build(factory, router=router, dry_run=True)
    po = ProductOwnerAgent(ctx)
    sid = po.add_item("Somar números").story_id
    po.admit(sid)
    await Scheduler(ctx).run(until_idle=True)
    await ctx.aclose()
    runner = CliRunner()
    root = ["--factory", "demo"]
    out = runner.invoke(app, ["trace", sid, *root], terminal_width=200)
    assert out.exit_code == 0, out.stdout
    assert "chamadas ao modelo" in out.stdout and "dev" in out.stdout and "search" in out.stdout
    one = runner.invoke(app, ["trace", sid, "--task", "T1", *root], terminal_width=200)
    assert one.exit_code == 0 and "[plan]" in one.stdout and "intake" not in one.stdout
    trace = read_trace(factory.paths.traces / f"{sid}.jsonl")
    call = next(s for s in trace.spans if s["kind"] == "llm" and s["attrs"].get("tools"))
    full = runner.invoke(app, ["trace", sid, "--span", call["id"][:10], *root], terminal_width=200)
    assert full.exit_code == 0 and "mensagens" in full.stdout and "system" in full.stdout
    stats = runner.invoke(app, ["trace", "--stats", *root], terminal_width=200)
    assert stats.exit_code == 0 and "Tempo e custo por papel e modelo" in stats.stdout
    missing = runner.invoke(app, ["trace", "S-999", *root])
    assert missing.exit_code == 1


def test_usage_stats_compare_models_in_the_same_role():
    store = Store(":memory:")
    for ms, model, finish in ((1000, "a", "stop"), (3000, "a", "length"), (500, "b", "stop")):
        store.record_usage(
            factory="f",
            agent="w",
            role="worker",
            provider="p",
            model=model,
            tier="tier2",
            duration_ms=ms,
            cost_usd=0.01,
            finish_reason=finish,
            cost_source="reported",
        )
    rows = {r["model"]: r for r in store.usage_stats("f")}
    assert rows["a"]["calls"] == 2 and rows["a"]["avg_ms"] == 2000 and rows["a"]["p90_ms"] == 3000
    assert rows["a"]["cuts"] == 1 and rows["a"]["reported"] == 2 and rows["b"]["cuts"] == 0
    assert store.usage_stats("f", story_id="S-none") == []
