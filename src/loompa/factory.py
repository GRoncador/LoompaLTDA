"""Factory: a single product/repository managed by Loompa LTDA (`<repo>/.loompa/`)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loompa.config import (
    LOOMPA_DIR,
    ConfigStore,
    LoompaConfig,
    default_config,
    load_config,
    save_config,
)
from loompa.config.schema import slugify
from loompa.onboarding import (
    BrownfieldScanner,
    GreenfieldInitializer,
    RepoAudit,
    detect_mode,
    draft_constitution_from_audit,
    executive_onboarding_summary,
)

LEARNINGS_HEADER = """# Learnings — Loop Kaizen

> Descobertas, bugs colaterais e oportunidades registradas automaticamente pelos Loompas.
> Cada item gera um card no Backlog; lições estruturais migram para a Constituição.

"""


@dataclass
class FactoryPaths:
    root: Path

    @property
    def loompa(self) -> Path:
        return self.root / LOOMPA_DIR

    @property
    def config(self) -> Path:
        return self.loompa / "config.yaml"

    @property
    def constitution(self) -> Path:
        return self.loompa / "constitution.md"

    @property
    def learnings(self) -> Path:
        return self.loompa / "learnings.md"

    @property
    def decisions(self) -> Path:
        return self.loompa / "decisions"

    @property
    def specs(self) -> Path:
        return self.loompa / "specs"

    @property
    def worktrees(self) -> Path:
        return self.loompa / "worktrees"

    @property
    def logs(self) -> Path:
        return self.loompa / "logs"

    @property
    def state_db(self) -> Path:
        return self.loompa / "state.db"

    @property
    def memory_db(self) -> Path:
        return self.loompa / "memory.db"

    @property
    def onboarding_report(self) -> Path:
        return self.loompa / "onboarding_report.md"

    @property
    def audit_json(self) -> Path:
        return self.loompa / "audit.json"

    def ensure(self) -> None:
        for d in (self.loompa, self.decisions, self.specs, self.worktrees, self.logs):
            d.mkdir(parents=True, exist_ok=True)


@dataclass
class Factory:
    root: Path
    config: LoompaConfig

    @property
    def paths(self) -> FactoryPaths:
        return FactoryPaths(self.root)

    @property
    def slug(self) -> str:
        return self.config.factory.slug

    @classmethod
    def open(cls, root: Path) -> Factory:
        root = Path(root).resolve()
        return cls(root=root, config=load_config(root))

    def save(self) -> None:
        save_config(self.root, self.config)

    def constitution_text(self) -> str:
        p = self.paths.constitution
        return p.read_text(encoding="utf-8") if p.is_file() else ""


@dataclass
class BootstrapResult:
    factory: Factory
    mode: str
    audit: RepoAudit | None
    created_git: bool
    skeleton_files: list[Path]
    report: str


def bootstrap_factory(
    root: Path,
    *,
    name: str | None = None,
    preset: str = "custom",
    mission: str = "",
    force_mode: str | None = None,
    store: ConfigStore | None = None,
    overwrite_constitution: bool = False,
) -> BootstrapResult:
    """Implementation of `loompa init`: detect stage, calibrate, create `.loompa/`, register."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    mode = force_mode or detect_mode(root)
    name = name or root.name
    slug = slugify(name)
    paths = FactoryPaths(root)
    paths.ensure()

    config = load_config(root) if paths.config.is_file() else default_config()
    config.factory.name = name
    config.factory.slug = slug
    config.factory.mode = mode  # type: ignore[assignment]

    audit: RepoAudit | None = None
    created_git = False
    skeleton: list[Path] = []
    if mode == "greenfield":
        init = GreenfieldInitializer(root, name=name, slug=slug, preset=preset, mission=mission)
        created_git = init.ensure_git()
        skeleton = init.write_skeleton()
        config.stack = init.preset.profile
        for key, cmd in init.preset.commands.items():
            setattr(config.quality, f"{key}_command", cmd)
        constitution = init.constitution()
        report = (
            f"# Relatório de Onboarding — {name}\n\n"
            f"Projeto novo criado com o preset **{init.preset.label}**. "
            "A Constituição está pronta para o primeiro épico.\n"
        )
    else:
        audit = BrownfieldScanner(root).scan()
        config.stack = audit.stack
        for key, cmd in audit.suggested_commands.items():
            attr = f"{key}_command"
            if not getattr(config.quality, attr):
                setattr(config.quality, attr, cmd)
        constitution = draft_constitution_from_audit(audit, name, mission)
        report = executive_onboarding_summary(audit, name)
        paths.audit_json.write_text(audit.model_dump_json(indent=2), encoding="utf-8")

    if overwrite_constitution or not paths.constitution.is_file():
        paths.constitution.write_text(constitution, encoding="utf-8")
    if not paths.learnings.is_file():
        paths.learnings.write_text(LEARNINGS_HEADER, encoding="utf-8")
    paths.onboarding_report.write_text(report, encoding="utf-8")
    gi = root / ".gitignore"
    marker = ".loompa/worktrees/"
    if not gi.is_file():
        gi.write_text(f"{marker}\n.loompa/*.db\n.loompa/*.db-*\n.loompa/logs/\n", encoding="utf-8")
    elif marker not in gi.read_text(encoding="utf-8"):
        with gi.open("a", encoding="utf-8") as fh:
            fh.write(
                f"\n# Loompa LTDA runtime state\n{marker}\n.loompa/*.db\n.loompa/*.db-*\n.loompa/logs/\n"
            )

    save_config(root, config)
    factory = Factory(root=root, config=config)
    (store or ConfigStore()).register(root, config)
    return BootstrapResult(
        factory=factory,
        mode=mode,
        audit=audit,
        created_git=created_git,
        skeleton_files=skeleton,
        report=report,
    )
