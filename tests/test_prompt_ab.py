"""ADR-0035: the live prompt A/B. A variant fits the code's prompt and keeps its contract, a story
keeps the arm it drew, the Worker reads the variant, and the report compares the arms."""

from __future__ import annotations

from types import SimpleNamespace

from loompa import prompt_ab
from loompa.bench import registry
from loompa.config import default_config
from loompa.config.schema import PromptAB
from loompa.engine import Scheduler
from loompa.engine.state import StoryState
from loompa.factory import Factory
from loompa.llm import ToolCall
from loompa.store import Store
from test_speed import factory, run_story  # noqa: F401

# The phrases the dry-run provider routes on (agents/dryrun.py): a variant keeps them.
MARKERS = ("Classify the story", "Sprint Meeting", "Pre-flight", "guardian of the spec")


def test_every_variant_fits_the_code_s_prompt_and_keeps_its_contract():
    for (key, name), variant in prompt_ab.VARIANTS.items():
        prompt = registry.get(key)
        text = prompt_ab.variant_text(prompt.template, key, name)
        assert text != prompt.template, name
        assert registry.parse_contract(prompt.render(template=text)) == prompt.contract, name
        assert all(m in text for m in MARKERS if m in prompt.template), name
        text.format(language="pt-BR")  # `{language}` and the doubled braces still format
        assert variant.watch and variant.title


def _ctx(entries: list[PromptAB]) -> SimpleNamespace:
    emitted: list[tuple[str, dict]] = []
    config = default_config()
    config.schedule.prompt_ab = entries
    return SimpleNamespace(
        config=config, emit=lambda kind, **kw: emitted.append((kind, kw)), emitted=emitted
    )


def test_a_story_keeps_the_arm_it_drew_and_the_split_follows_the_share():
    template = registry.get("worker.SYSTEM").template
    off = _ctx([])
    state = StoryState(story_id="S-1", title="t")
    assert prompt_ab.prompt_for(off, state, "worker.SYSTEM", template) == (template, "")
    on = _ctx([PromptAB(prompt="worker.SYSTEM", variant="test-seams", share=1.0)])
    text, arm = prompt_ab.prompt_for(on, state, "worker.SYSTEM", template)
    assert arm == "variant" and "Test through the interface" in text
    # the share drops to 0 while the story runs: it keeps its arm, and is announced once
    on.config.schedule.prompt_ab[0].share = 0.0001
    assert prompt_ab.prompt_for(on, state, "worker.SYSTEM", template)[1] == "variant"
    assert [k for k, _ in on.emitted] == ["prompt.ab"]
    assert on.emitted[0][1] == {
        "story_id": "S-1",
        "prompt": "worker.SYSTEM",
        "variant": "test-seams",
        "arm": "variant",
    }
    # another prompt is not in the experiment
    plan = registry.get("architect.SYSTEM").template
    assert prompt_ab.prompt_for(on, state, "architect.SYSTEM", plan) == (plan, "")
    arms = [prompt_ab.draw(f"S-{i}", "worker.SYSTEM", "test-seams", 0.5) for i in range(400)]
    assert 160 < arms.count("variant") < 240


def test_fisher_exact_matches_the_textbook():
    # Fisher's lady tasting tea: 3 of 4 right in each column, two-sided p = 0.4857
    assert round(prompt_ab.fisher_p(3, 1, 1, 3), 4) == 0.4857
    assert prompt_ab.fisher_p(0, 0, 0, 0) == 1.0
    assert prompt_ab.fisher_p(10, 0, 0, 10) < 0.001


def test_the_report_compares_the_arms_by_what_happened_to_the_stories():
    store = Store(":memory:")
    for i in range(6):
        sid = f"S-{i}"
        arm = "variant" if i < 3 else "control"
        # the test writes the rows directly: it checks the report, not who may create stories
        store.upsert_story(
            {"id": sid, "factory": "f", "title": sid, "stage": "DONE", "cost_usd": 0.1}
        )  # noqa
        store.emit(
            "f", "prompt.ab", story_id=sid, prompt="worker.SYSTEM", variant="test-seams", arm=arm
        )
        store.emit("f", "worker.task", story_id=sid, ended_by="done", tool_calls=10)
        if arm == "control":
            store.emit("f", "story.retry", story_id=sid, tier="tier2", attempt=1)
            store.emit(
                "f",
                "inspector.verdict",
                story_id=sid,
                verdict="FAIL",
                findings=1,
                prefixes={"TEST": 1},
            )
    [r] = prompt_ab.report(store, "f")
    v, c = r["arms"]["variant"], r["arms"]["control"]
    assert (v["stories"], v["with_rework"], v["test_findings"]) == (3, 0, 0.0)
    assert (c["stories"], c["with_rework"], c["fails"], c["test_findings"]) == (3, 3, 1.0, 1.0)
    assert v["cost_usd"] == 0.1 and v["tasks"] == 1.0
    assert r["rework_p"] == prompt_ab.fisher_p(0, 3, 3, 0)


def test_finding_prefixes_count_each_kind():
    findings = [{"prefix": "TEST"}, {"prefix": "TEST"}, {"id": "SEC-1"}]
    assert prompt_ab.finding_prefixes(findings) == {"TEST": 2, "SEC": 1}


async def test_the_worker_reads_the_variant_and_every_task_says_its_arm(factory: Factory):  # noqa: F811
    factory.config.schedule.prompt_ab = [
        PromptAB(prompt="worker.SYSTEM", variant="tests-and-general", share=1.0)
    ]
    ctx, sid, prompts = run_story(
        factory, lambda msgs: [ToolCall(f"d{len(msgs)}", "done", {"summary": "pronto"})]
    )
    await Scheduler(ctx).run(until_idle=True)
    assert prompts and all("Test through the interface" in p[0].content for p in prompts)
    assert all("Write the general logic" in p[0].content for p in prompts)
    events = [e for e in ctx.store.events_since(0, limit=5000) if e["story_id"] == sid]
    assert [e["payload"]["arm"] for e in events if e["type"] == "prompt.ab"] == ["variant"]
    tasks = [e["payload"] for e in events if e["type"] == "worker.task"]
    assert tasks and {t["prompt_ab"] for t in tasks} == {"variant"}
    await ctx.aclose()
