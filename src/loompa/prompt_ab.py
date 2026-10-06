"""Live prompt A/B (plan 14.3, ADR-0035): what the prompt bench cannot measure, measured on stories.

The bench replays one recorded call. An idea whose effect shows at the end of the Worker's tool loop
(the tests it writes, a value tuned until a check passes) or in a story's rework (the order of the
plan's tasks) cannot be seen there. Here a factory runs a variant of a prompt for a share of its
stories, and `loompa prompts ab` compares the stories of the two arms by what happened to them.

- A variant is a set of edits on the code's prompt, kept in this module and checked by the tests
  like a bench candidate: every edit found once, the JSON contract and the dry-run phrases kept.
- `schedule.prompt_ab` in the factory's config turns one on: `{prompt, variant, share}`.
- The arm is drawn per story (a hash of story, prompt and variant), stored in the story's state the
  first time, so every task and every retry of the story reads the same text, and announced once
  with a `prompt.ab` event. A story that already has commits when the experiment starts stays out.
- Only the prompts wired below take part: `worker.SYSTEM` (the built-in Worker; the OpenCode
  backend has its own prompt) and `architect.SYSTEM` (the full plan, not the SIMPLE one).
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from loompa.engine.context import EngineContext
    from loompa.engine.state import StoryState
    from loompa.store import Store

AB_KEY = "prompt_ab"  # state.extra: {prompt key: "variant" | "control"}
VARIANT, CONTROL = "variant", "control"


@dataclass(frozen=True)
class PromptVariant:
    prompt: str  # worker.SYSTEM
    name: str  # general-solution
    title: str  # pt-BR, for the report
    edits: tuple[tuple[str, str], ...]
    watch: str  # pt-BR: the numbers of the report that should move


GENERAL_SOLUTION = (
    "3. Each write reports syntax errors",
    "   Write the general logic that is right for every valid input: tests and the factory's checks\n"
    "   verify the behaviour, they do not define it. So no special case for a test's values, and no\n"
    "   number (a pixel, a timeout) nudged just until a check passes; when a test or a check itself\n"
    "   looks wrong, call `blocked` and say why instead of working around it. Why: a value tuned to a\n"
    "   check hides the defect from every later check.\n"
    "3. Each write reports syntax errors",
)
TEST_SEAMS = (
    "maximum and one past it, invalid input). Leave out the same path",
    "maximum and one past it, invalid input). Test through the interface the user or the calling\n"
    "   code uses, mocking only what the project does not own (network, clock, randomness), and write\n"
    "   each expected value as a literal from the spec or worked out by hand, never recomputed the way\n"
    "   the code computes it. Leave out the same path",
)
TRACER = (
    "   Tests: new tests for this story go in a new test file of its own",
    "   Order the tasks as a tracer bullet when the story crosses layers (data, logic, what the user\n"
    "   sees): the first task builds one thin path through all of them for one case of the main\n"
    "   criterion, with its test, and the next tasks widen it case by case. A change of one name or\n"
    "   unit across many modules keeps the one-module-at-a-time order above. Why: a path that works\n"
    "   end to end shows a wrong assumption at the first task; a layer built alone is proven only when\n"
    "   the last task joins it.\n"
    "   Tests: new tests for this story go in a new test file of its own",
)

VARIANTS: dict[tuple[str, str], PromptVariant] = {
    (v.prompt, v.name): v
    for v in (
        PromptVariant(
            "worker.SYSTEM",
            "general-solution",
            "Worker: a lógica geral, não o valor que passa na checagem (P5)",
            (GENERAL_SOLUTION,),
            "retrabalho, reprovação do Inspector, bloqueios",
        ),
        PromptVariant(
            "worker.SYSTEM",
            "test-seams",
            "Worker: testar pela interface, com valor esperado independente (P7)",
            (TEST_SEAMS,),
            "achados TEST do Inspector, retrabalho",
        ),
        PromptVariant(
            "worker.SYSTEM",
            "tests-and-general",
            "Worker: P5 e P7 juntos",
            (TEST_SEAMS, GENERAL_SOLUTION),
            "achados TEST, retrabalho, reprovação do Inspector",
        ),
        PromptVariant(
            "architect.SYSTEM",
            "tracer",
            "Architect: tracer bullet (P2)",
            (TRACER,),
            "retrabalho, tarefas que não terminam, tarefas por plano",
        ),
    )
}


def variant_text(template: str, prompt: str, name: str) -> str:
    """`template` (the code's prompt) with the variant's edits; each must be found exactly once,
    or the variant no longer fits the prompt and says so."""
    text = template
    for old, new in VARIANTS[(prompt, name)].edits:
        if text.count(old) != 1:
            raise ValueError(f"{prompt}:{name} no longer fits the prompt: {old[:60]!r}")
        text = text.replace(old, new, 1)
    return text


def draw(story_id: str, prompt: str, variant: str, share: float) -> str:
    h = hashlib.sha1(f"{story_id}:{prompt}:{variant}".encode()).hexdigest()[:8]
    return VARIANT if int(h, 16) / 0xFFFFFFFF < share else CONTROL


def prompt_for(
    ctx: EngineContext, state: StoryState, prompt: str, template: str
) -> tuple[str, str]:
    """The template this story runs for `prompt`, and its arm ("" when no experiment is on). A
    story keeps the arm it drew first, even if the share changes while it runs."""
    exp = next((e for e in ctx.config.schedule.prompt_ab if e.prompt == prompt), None)
    if exp is None or exp.share <= 0 or (prompt, exp.variant) not in VARIANTS:
        return template, ""
    arms = state.extra.setdefault(AB_KEY, {})
    if prompt not in arms:
        # a story already under way when the experiment began stays out of it: switching its
        # rules halfway would mix the arms
        arm = "" if state.commits else draw(state.story_id, prompt, exp.variant, exp.share)
        arms[prompt] = arm
        if arm:
            ctx.emit(
                "prompt.ab", story_id=state.story_id, prompt=prompt, variant=exp.variant, arm=arm
            )
    arm = arms[prompt]
    if arm != VARIANT:
        return template, arm
    try:
        return variant_text(template, prompt, exp.variant), arm
    except ValueError:  # the code's prompt moved under the variant: run the code's
        return template, ""


# ----------------------------------------------------------------------------- the report

STORY_EVENTS = (
    "story.retry",
    "story.replanned",
    "story.restarted",
    "story.blocked",
    "inspector.verdict",
    "worker.task",
    "story.stage",
)


def _story(store: Store, sid: str) -> dict[str, Any]:
    by: dict[str, list[dict[str, Any]]] = {}
    for e in store.story_events(sid, STORY_EVENTS):
        by.setdefault(e["type"], []).append(e)
    row = store.get_story(sid) or {}
    verdicts = [e["payload"] for e in by.get("inspector.verdict", [])]
    tasks = [e["payload"] for e in by.get("worker.task", [])]
    tests = sum(int((v.get("prefixes") or {}).get("TEST", 0)) for v in verdicts)
    return {
        "id": sid,
        "stage": str(row.get("stage") or ""),
        "rework": len([e for e in by.get("story.retry", []) if e["payload"].get("tier")])
        + len(by.get("story.replanned", []))
        + len(by.get("story.restarted", [])),
        "fails": sum(1 for v in verdicts if v.get("verdict") == "FAIL"),
        "test_findings": tests,
        "blocks": len(by.get("story.blocked", [])),
        "tasks": len(tasks),
        "unfinished": sum(1 for t in tasks if t.get("ended_by") not in ("done", "blocked")),
        "tool_calls": sum(int(t.get("tool_calls") or 0) for t in tasks),
        "cost_usd": float(row.get("cost_usd") or 0.0),
    }


def fisher_p(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact test of the 2×2 table [[a, b], [c, d]]: with a handful of stories per
    arm, a share is compared by exact counts, not by a normal approximation."""
    n, row1, col1 = a + b + c + d, a + b, a + c

    def prob(x: int) -> float:
        return math.comb(col1, x) * math.comb(n - col1, row1 - x) / math.comb(n, row1) if n else 1.0

    observed = prob(a)
    lo, hi = max(0, row1 + col1 - n), min(row1, col1)
    return min(1.0, sum(p for x in range(lo, hi + 1) if (p := prob(x)) <= observed * (1 + 1e-9)))


def report(store: Store, factory: str, days: int = 180) -> list[dict[str, Any]]:
    """One entry per experiment that assigned stories in the window: its two arms, each with its
    stories and their totals, and the Fisher p of "the story needed rework"."""
    end = datetime.now(UTC)
    events = store.events_between(
        factory, (end - timedelta(days=days)).isoformat(), end.isoformat()
    )
    assigned: dict[tuple[str, str], dict[str, list[str]]] = {}
    for e in events:
        if e["type"] != "prompt.ab":
            continue
        p = e["payload"]
        arms = assigned.setdefault((p["prompt"], p["variant"]), {VARIANT: [], CONTROL: []})
        if e["story_id"] not in arms[p["arm"]]:
            arms[p["arm"]].append(e["story_id"])
    out = []
    for (prompt, name), arms in sorted(assigned.items()):
        totals = {}
        for arm, sids in arms.items():
            stories = [_story(store, sid) for sid in sids]
            done = [s for s in stories if s["stage"] in ("DONE", "CANCELLED")]
            n = len(done) or 1
            totals[arm] = {
                "stories": len(stories),
                "settled": len(done),
                "delivered": sum(1 for s in done if s["stage"] == "DONE"),
                "with_rework": sum(1 for s in done if s["rework"]),
                "rework": round(sum(s["rework"] for s in done) / n, 2),
                "fails": round(sum(s["fails"] for s in done) / n, 2),
                "test_findings": round(sum(s["test_findings"] for s in done) / n, 2),
                "blocks": round(sum(s["blocks"] for s in done) / n, 2),
                "unfinished_tasks": round(sum(s["unfinished"] for s in done) / n, 2),
                "tasks": round(sum(s["tasks"] for s in done) / n, 1),
                "cost_usd": round(sum(s["cost_usd"] for s in done) / n, 4),
                "story_ids": sids,
            }
        v, c = totals[VARIANT], totals[CONTROL]
        out.append(
            {
                "prompt": prompt,
                "variant": name,
                "title": VARIANTS[(prompt, name)].title if (prompt, name) in VARIANTS else name,
                "watch": VARIANTS[(prompt, name)].watch if (prompt, name) in VARIANTS else "",
                "arms": totals,
                "rework_p": fisher_p(
                    v["with_rework"],
                    v["settled"] - v["with_rework"],
                    c["with_rework"],
                    c["settled"] - c["with_rework"],
                ),
            }
        )
    return out


def finding_prefixes(findings: list[dict[str, Any]]) -> dict[str, int]:
    """How many findings of each kind (SEC, PERF, TEST, ARCH) a verdict had, for the A/B report."""
    return dict(
        Counter(
            (str(f.get("prefix") or "") or str(f.get("id") or "?").split("-")[0]).upper()
            for f in findings
        )
    )
