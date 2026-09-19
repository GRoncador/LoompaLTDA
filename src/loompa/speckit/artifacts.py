"""GitHub Spec Kit protocol: constitution.md, spec.md, plan.md and tasks.md.

No line of code is generated without the triad (`spec`, `plan`, `tasks`) plus the factory
`constitution`. Templates are plain Markdown with `{placeholders}` so the artifacts remain
human-editable and diff-friendly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, Field

_TASK_LINE = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(?:(T\d+)[:.)]?\s*)?(.+?)\s*$")


def _template(name: str) -> str:
    return resources.files("loompa.speckit.templates").joinpath(name).read_text(encoding="utf-8")


def _bullets(items: list[str] | str, empty: str = "_(nenhum)_") -> str:
    if isinstance(items, str):
        return items.strip() or empty
    return "\n".join(f"- {i}" for i in items) if items else empty


def render_constitution(
    *,
    name: str,
    mission: str,
    stack: list[str],
    allowed_libraries: list[str],
    conventions: list[str],
    quality: list[str],
    rules: list[str],
) -> str:
    base_rules = [
        "Nenhum código sem `spec.md`, `plan.md` e `tasks.md` aprovados para a história.",
        "Workers só editam arquivos listados no `plan.md`; achados fora do escopo viram card no backlog (Loop Kaizen).",
        "Todo commit é semântico (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`).",
        "Toda funcionalidade tem testes automatizados cobrindo os critérios de aceitação.",
        "Segredos nunca entram no repositório; usar variáveis de ambiente.",
    ]
    return _template("constitution.md").format(
        name=name,
        mission=mission.strip() or "_(a definir na primeira reunião matinal)_",
        stack=_bullets(stack),
        allowed_libraries=_bullets(allowed_libraries),
        conventions=_bullets(conventions),
        quality=_bullets(quality),
        rules=_bullets([*base_rules, *rules]),
    )


def render_spec(
    *,
    story_id: str,
    title: str,
    goal: str,
    in_scope: list[str],
    out_of_scope: list[str],
    acceptance: list[str],
    rules: list[str],
    questions: list[str],
) -> str:
    return _template("spec.md").format(
        story_id=story_id,
        title=title,
        date=date.today().isoformat(),
        goal=goal.strip(),
        in_scope=_bullets(in_scope),
        out_of_scope=_bullets(out_of_scope),
        acceptance=_bullets(acceptance, "_(a definir)_"),
        rules=_bullets(rules),
        questions=_bullets(questions),
    )


def render_plan(
    *,
    story_id: str,
    title: str,
    approach: str,
    files: list[str],
    contracts: str,
    risks: list[str],
    precedents: list[str],
) -> str:
    return _template("plan.md").format(
        story_id=story_id,
        title=title,
        date=date.today().isoformat(),
        approach=approach.strip(),
        files=_bullets(files),
        contracts=contracts.strip() or "_(nenhum contrato novo)_",
        risks=_bullets(risks),
        precedents=_bullets(precedents),
    )


def render_tasks(*, story_id: str, title: str, tasks: list[str]) -> str:
    lines = [f"- [ ] T{i + 1}: {t}" for i, t in enumerate(tasks)]
    return _template("tasks.md").format(story_id=story_id, title=title, tasks="\n".join(lines))


def render_research(
    *,
    story_id: str,
    title: str,
    question: str,
    summary: str,
    findings: list[dict[str, object]],
    recommendation: str,
    limitations: list[str],
    sources: list[str],
    status: str = "draft",
) -> str:
    """The Analyst's report. Each finding lists the sources that back it; findings without one
    say so, so a reader never mistakes a guess for a cited fact."""
    lines = []
    for i, f in enumerate(findings, 1):
        cited = [str(u) for u in f.get("sources") or []]  # type: ignore[union-attr]
        backing = ", ".join(cited) if cited else "_sem fonte verificada_"
        lines.append(f"{i}. {str(f.get('text', '')).strip()}\n   - Fontes: {backing}")
    return _template("research.md").format(
        story_id=story_id,
        title=title,
        date=date.today().isoformat(),
        status=status,
        question=question.strip() or title,
        summary=summary.strip() or "_(sem resumo)_",
        findings="\n".join(lines) or "_(nenhum achado)_",
        recommendation=recommendation.strip() or "_(sem recomendação)_",
        limitations=_bullets(limitations),
        sources=_bullets(sources),
    )


class TaskItem(BaseModel):
    number: int
    text: str
    done: bool = False


def tasks_from_markdown(text: str) -> list[TaskItem]:
    items: list[TaskItem] = []
    for line in text.splitlines():
        m = _TASK_LINE.match(line)
        if not m:
            continue
        done = m.group(1).lower() == "x"
        number = int(m.group(2)[1:]) if m.group(2) else len(items) + 1
        items.append(TaskItem(number=number, text=m.group(3), done=done))
    return items


def mark_task_done(text: str, number: int) -> str:
    out = []
    for line in text.splitlines():
        m = _TASK_LINE.match(line)
        if m and m.group(2) == f"T{number}":
            line = line.replace("[ ]", "[x]", 1)
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


@dataclass
class StorySpecPaths:
    root: Path
    spec: Path
    plan: Path
    tasks: Path
    research: Path  # research stories (Analyst) end here instead of in code

    def all_present(self) -> bool:
        return self.spec.is_file() and self.plan.is_file() and self.tasks.is_file()


def story_dir(factory_root: Path, story_id: str) -> StorySpecPaths:
    root = factory_root / ".loompa" / "specs" / story_id
    return StorySpecPaths(
        root=root,
        spec=root / "spec.md",
        plan=root / "plan.md",
        tasks=root / "tasks.md",
        research=root / "research.md",
    )


class SpecArtifacts(BaseModel):
    """Structured content the Product/Architect Loompas produce before rendering."""

    goal: str = ""
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    approach: str = ""
    files: list[str] = Field(default_factory=list)
    contracts: str = ""
    risks: list[str] = Field(default_factory=list)
    precedents: list[str] = Field(default_factory=list)
    tasks: list[str] = Field(default_factory=list)

    def write(self, paths: StorySpecPaths, story_id: str, title: str) -> None:
        paths.root.mkdir(parents=True, exist_ok=True)
        paths.spec.write_text(
            render_spec(
                story_id=story_id,
                title=title,
                goal=self.goal,
                in_scope=self.in_scope,
                out_of_scope=self.out_of_scope,
                acceptance=self.acceptance,
                rules=self.rules,
                questions=self.questions,
            ),
            encoding="utf-8",
        )
        paths.plan.write_text(
            render_plan(
                story_id=story_id,
                title=title,
                approach=self.approach,
                files=self.files,
                contracts=self.contracts,
                risks=self.risks,
                precedents=self.precedents,
            ),
            encoding="utf-8",
        )
        paths.tasks.write_text(
            render_tasks(story_id=story_id, title=title, tasks=self.tasks), encoding="utf-8"
        )
