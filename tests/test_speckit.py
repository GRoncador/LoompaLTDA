from pathlib import Path

from loompa.speckit import SpecArtifacts, render_constitution, story_dir, tasks_from_markdown
from loompa.speckit.artifacts import mark_task_done


def test_constitution_renders_all_sections():
    text = render_constitution(
        name="Demo",
        mission="Vender bolos",
        stack=["Python"],
        allowed_libraries=["fastapi"],
        conventions=["src/"],
        quality=["pytest"],
        rules=["Sem magia"],
    )
    for heading in (
        "Missão do produto",
        "Stack oficial",
        "Bibliotecas permitidas",
        "Regras invioláveis",
        "Loop Kaizen",
    ):
        assert heading in text
    assert "- fastapi" in text and "- Sem magia" in text and "spec.md" in text


def test_spec_artifacts_write_triad_and_tasks_parse(tmp_path: Path):
    paths = story_dir(tmp_path, "S-7")
    assert not paths.all_present()
    SpecArtifacts(
        goal="g", acceptance=["Dado X, quando Y, então Z"], tasks=["criar modelo", "criar endpoint"]
    ).write(paths, "S-7", "Cadastro")
    assert paths.all_present()
    items = tasks_from_markdown(paths.tasks.read_text())
    assert [t.text for t in items] == ["criar modelo", "criar endpoint"]
    assert not items[0].done
    updated = mark_task_done(paths.tasks.read_text(), 1)
    assert tasks_from_markdown(updated)[0].done and not tasks_from_markdown(updated)[1].done


def test_tasks_parse_tolerates_plain_checkboxes():
    items = tasks_from_markdown("- [ ] first\n* [x] second\nnot a task\n")
    assert [(t.number, t.done) for t in items] == [(1, False), (2, True)]


def test_a_long_spec_keeps_whole_sections_by_priority_and_says_what_was_left_out():
    from loompa.speckit.artifacts import SPEC_FOR_BUILDING, fit_sections

    spec = (
        "# Spec — S-1: x\n\n**Status:** draft\n\n"
        "## Objetivo de negócio\n" + "o" * 900 + "\n\n"
        "## Critérios de aceitação (BDD)\n1. Dado A, quando B, então C.\n" + "c" * 1500 + "\n\n"
        "## Entidades principais\n" + "e" * 2000 + "\n\n"
        "## Perguntas em aberto\n" + "p" * 2000 + "\n\n"
        "## Critérios revistos pelo Product Owner\n- critério 1 retirado pelo Founder\n"
    )
    out = fit_sections(spec, 3500, SPEC_FOR_BUILDING)
    assert len(out) <= 3500
    # the revision at the very end survives; what does not fit is named, never cut mid-word
    assert "critério 1 retirado pelo Founder" in out and "Dado A, quando B" in out
    assert "(left out to fit: Entidades principais; Perguntas em aberto)" in out
    assert (
        out.index("## Objetivo")
        < out.index("## Critérios de aceitação")
        < out.index("## Critérios revistos")
    )  # original order
    assert fit_sections("short", 100) == "short"
    huge = "## Critérios de aceitação (BDD)\n" + "\n".join(f"{i}. linha" for i in range(2000))
    cut = fit_sections(huge, 1200, SPEC_FOR_BUILDING)
    assert "section cut here" in cut and len(cut) <= 1200


def test_the_inspector_gets_the_scope_and_edge_cases_before_a_second_copy_of_the_criteria():
    """14.3: the judge read the first 3000 characters of the spec, so the scope of a median 8k
    spec never reached it. Its criteria come whole in their own block; the spec gives the bounds."""
    from loompa.speckit.artifacts import SPEC_FOR_JUDGING, SPEC_JUDGE_ROOM, fit_sections

    spec = (
        "# Spec — S-1: x\n\n"
        "## Objetivo de negócio\n" + "o" * 600 + "\n\n"
        "## Escopo\n**Fora do escopo:** exportar em PDF.\n" + "s" * 1500 + "\n\n"
        "## Critérios de aceitação (BDD)\n" + "c" * 6000 + "\n\n"
        "## Casos de borda\n- lista vazia\n" + "b" * 1500 + "\n\n"
        "## Entidades principais\n" + "e" * 2500 + "\n"
    )
    out = fit_sections(spec, SPEC_JUDGE_ROOM, SPEC_FOR_JUDGING)
    assert len(out) <= SPEC_JUDGE_ROOM
    assert "exportar em PDF" in out and "lista vazia" in out
    assert "s" * 1500 in out and "b" * 1500 in out  # scope and edge cases whole
    assert "c" * 1500 not in out  # the second copy of the criteria gives way
    assert "(left out to fit: Entidades principais)" in out


def test_a_dependency_spec_gives_its_decisions_before_its_criteria():
    """14.3: the Spec Loompa read the first 6000 characters of each spec its story builds on, and
    32 of 40 tamagotchi specs ran past that, losing the entities, rules and premises at the end."""
    from loompa.speckit.artifacts import DEPENDENCY_ROOM, SPEC_FOR_DEPENDENTS, fit_sections

    spec = (
        "# Spec — S-1: x\n\n"
        "## Objetivo de negócio\n" + "o" * 800 + "\n\n"
        "## Critérios de aceitação (BDD)\n" + "c" * 4500 + "\n\n"
        "## Entidades principais\n- Limite: guardado em limites.json, em centavos\n\n"
        "## Premissas\n- valores em centavos\n" + "p" * 600 + "\n\n"
        "## Perguntas em aberto\n" + "q" * 900 + "\n"
    )
    out = fit_sections(spec, DEPENDENCY_ROOM, SPEC_FOR_DEPENDENTS)
    assert len(out) <= DEPENDENCY_ROOM
    assert "guardado em limites.json" in out and "valores em centavos" in out
    assert "Perguntas em aberto" in out.split("(left out")[-1]


def test_a_research_redone_keeps_its_answer_before_its_sources():
    from loompa.speckit.artifacts import RESEARCH_FOR_REDOING, RESEARCH_ROOM, fit_sections

    report = (
        "# Pesquisa — S-9: x\n\n## Pergunta\nqual gateway?\n\n## Resumo\n" + "r" * 400 + "\n\n"
        "## Achados\n" + "a" * 2500 + "\n\n## Recomendação\nusar o gateway B\n\n"
        "## Limitações\nsem web\n\n## Fontes consultadas\n" + "f" * 3000 + "\n"
    )
    out = fit_sections(report, RESEARCH_ROOM, RESEARCH_FOR_REDOING)
    assert "usar o gateway B" in out and "sem web" in out
    assert "(left out to fit: Fontes consultadas)" in out
