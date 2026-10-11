"""tamagotchi S-106 (11/10): a one-file test fix that asked the founder three times.

The self-check had the Worker undo a change to the story's other planned test file, a change the
suite needed; the Worker had no way to bring back the saved version and asked the founder; the
question called the change "three lines of comments" and recommended undoing it; the suite went
red. These tests replay the real texts."""

from __future__ import annotations

from loompa.agents.worker import WorkerAgent
from loompa.engine import StoryState
from loompa.factory import Factory
from loompa.hygiene import unsaved_changes
from test_engine import factory, make_ctx  # noqa: F401

FENCE = ["tests/suite_sem_registros.test.js", "tests/casca_body_scripts.test.js"]
TASK_2 = (
    "Consertar a causa em tests/casca_body_scripts.test.js: reescrever o teste AC6 para conferir "
    "só o markup de index.html. Arquivo único editado: tests/casca_body_scripts.test.js."
)
# the self-check's words at 18:17
DOD_S106 = (
    "The diff of this task also edits tests/suite_sem_registros.test.js (header comment changes), "
    "but the task explicitly states that tests/casca_body_scripts.test.js is the single file to be "
    "edited — revert the changes to tests/suite_sem_registros.test.js so that only "
    "tests/casca_body_scripts.test.js is modified (its committed version already works with the "
    "fixed AC6; only its now-stale comment text remains, which is outside this task's scope)."
)


def test_the_self_check_does_not_undo_the_storys_other_planned_file(factory: Factory):  # noqa: F811
    ctx = make_ctx(factory)
    state = StoryState(story_id="S-106", title="Consertar a versão principal", allowed_paths=FENCE)
    real_work = [
        "Restore the AC2 test that was removed from tests/casca_body_scripts.test.js",
        "Revert the edit to src/app.js, which the plan does not allow",
        "Add the negative case of the AC6 (the variant without the inline script)",
    ]
    kept = WorkerAgent(ctx)._keep_fenced_changes(state, 2, TASK_2, [DOD_S106, *real_work])
    assert kept == real_work
    events = [
        e for e in ctx.store.events_since(0, limit=1000) if e["type"] == "worker.dod_scope_kept"
    ]
    assert len(events) == 1


def test_unsaved_changes_say_whether_the_change_runs():
    diff = (
        "diff --git a/tests/suite_sem_registros.test.js b/tests/suite_sem_registros.test.js\n"
        "--- a/tests/suite_sem_registros.test.js\n+++ b/tests/suite_sem_registros.test.js\n"
        "@@ -3,3 +3,4 @@\n"
        "-// nenhuma entrada oculta — os registros internos da fábrica não são produto\n"
        "+// a cópia mantém o repositório e deixa de fora só os registros da fábrica\n"
        "-        (parte) => parte.startsWith('.') || parte === 'node_modules',\n"
        "+        (parte) => parte === '.loompa' || parte === 'node_modules',\n"
        "diff --git a/tests/casca_body_scripts.test.js b/tests/casca_body_scripts.test.js\n"
        "--- a/tests/casca_body_scripts.test.js\n+++ b/tests/casca_body_scripts.test.js\n"
        "@@ -1,3 +1,3 @@\n"
        "-// AC6: reprodutor do bug colateral da S-087\n"
        "+// AC6: o <body> termina com o registro do service worker\n"
        "+\n"
    )
    assert unsaved_changes(diff) == [
        "tests/suite_sem_registros.test.js (changed): 4 lines, code that runs, not only comments",
        "tests/casca_body_scripts.test.js (changed): 3 lines, comments or blank lines only",
    ]


async def test_the_question_carries_what_is_not_saved_yet(factory: Factory):  # noqa: F811
    import json
    from typing import Any

    from loompa.agents.dryrun import dry_run_script, role_of
    from loompa.engine import Scheduler
    from loompa.llm import Message, ToolCall
    from test_engine import seed_story, tool_results

    seen: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        if role == "worker" and not tool_results(messages):
            return [
                ToolCall(
                    "w",
                    "write_file",
                    {"path": "tests/test_copia.py", "content": "def test_ok():\n    assert 1\n"},
                )
            ]
        if role == "worker":
            return [
                ToolCall("q", "blocked", {"reason": "Desfazer ou manter?", "options": ["Manter"]})
            ]
        if role == "master" and "Problem:" in messages[-1].content:
            seen.append(messages[-1].content)
            return json.dumps({"title": "t", "context": "c", "impact": "i", "options": []})
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    seed_story(ctx, "Consertar a versão principal")
    await Scheduler(ctx).run()
    assert seen and "Changes in this story not saved yet" in seen[-1]
    assert "tests/test_copia.py (new file): 2 lines, code that runs" in seen[-1]
    await ctx.aclose()
