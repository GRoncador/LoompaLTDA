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
