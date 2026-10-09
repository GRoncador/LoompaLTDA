"""ADR-0036 §9: what goes into the constitution's lessons, and what an agent reads of it."""

from __future__ import annotations

from loompa.agents.architect import ArchitectAgent, lesson_refusal
from loompa.factory import Factory
from test_engine import factory, make_ctx  # noqa: F401

# the lessons tamagotchi's constitution had grown by 08/10
REREAD = (
    "Se você releu o mesmo arquivo sem nenhuma alteração no meio, pare de tentar e escale: chame "
    "blocked explicando o que não está ficando claro."
)
TASKS_MD = (
    "Ao implementar uma história, garanta que o roteiro de verificação (tasks.md) tenha uma "
    "tarefa que nomeia cada critério; se o arquivo não existir no checkout, crie-o."
)
EXTEND = (
    "Antes de criar um novo arquivo de teste, verifique se já existe teste cobrindo o critério e "
    "estenda-o em vez de duplicar o esforço."
)
MANIFEST = (
    "Todo asset referenciado pelo manifest PWA (ícones, start_url, offline) deve existir de fato "
    "no repositório e ser servido com o tipo MIME correto."
)
BOOT = (
    "Durante o boot, não re-leia estado persistido a partir do storage para alimentar gravações: "
    "mantenha o que já foi lido em memória."
)


def test_a_lesson_about_tools_records_or_test_files_or_a_copy_is_refused():
    assert "tool use" in lesson_refusal(REREAD, [])
    assert "records" in lesson_refusal(TASKS_MD, [])
    assert lesson_refusal(EXTEND, [])
    # lessons about the product stay, even one that says "re-leia" about its storage
    assert lesson_refusal(MANIFEST, []) == "" and lesson_refusal(BOOT, [MANIFEST]) == ""
    copy = (
        "Todo asset citado no manifest do PWA precisa existir no repositório e ser servido com "
        "o tipo MIME certo."
    )
    assert "repeats" in lesson_refusal(copy, [MANIFEST])


async def test_a_story_hit_by_tool_call_storms_teaches_the_constitution_nothing(
    factory: Factory,  # noqa: F811
):
    ctx = make_ctx(factory)
    arch = ArchitectAgent(ctx)
    ctx.emit("llm.storm", story_id="S-082", model="m", served_by="Makora")
    assert arch.append_constitution_lesson(MANIFEST, "S-082") is False
    assert arch.append_constitution_lesson(MANIFEST, "S-077") is True
    assert arch.append_constitution_lesson(REREAD, "S-086") is False
    text = factory.paths.constitution.read_text(encoding="utf-8")
    assert "S-077" in text and "S-082" not in text and "S-086" not in text
    await ctx.aclose()


async def test_an_agent_reads_the_rules_and_the_newest_lessons_that_fit(factory: Factory):  # noqa: F811
    """tamagotchi's Worker read the first 3000 characters of a 5900-character constitution:
    the rules and a lesson and a half, never any new one."""
    ctx = make_ctx(factory)
    arch = ArchitectAgent(ctx)
    for n in range(30):
        words = " ".join(f"p{n}w{k}" for k in range(6))
        assert arch.append_constitution_lesson(f"{words} zz{n}.", f"S-{n}")
    full = factory.paths.constitution.read_text(encoding="utf-8")
    head = full.split("## 7.")[0]
    seen = arch.constitution(len(head) + 400)
    assert seen.startswith(head) and "zz29." in seen and "zz0." not in seen
    assert "oldest lessons are left out" in seen and len(seen) <= len(head) + 400
    await ctx.aclose()
