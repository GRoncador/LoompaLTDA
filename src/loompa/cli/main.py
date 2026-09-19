"""`loompa` command line: the Founder's terminal into every factory."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from loompa import __version__
from loompa.config import ConfigStore
from loompa.factory import Factory, bootstrap_factory
from loompa.onboarding import STACK_PRESETS, detect_mode

app = typer.Typer(
    name="loompa",
    help="Loompa LTDA — fábrica autônoma de software com governança assíncrona.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
factories_app = typer.Typer(help="Gerencia as múltiplas fábricas (produtos) do Founder.")
app.add_typer(factories_app, name="factories")
console = Console()


def _version(value: bool) -> None:
    if value:
        console.print(f"loompa-core {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version, is_eager=True, help="Mostra a versão."
    ),
) -> None:
    pass


def resolve_factory(slug: str | None = None, path: Path | None = None) -> Factory:
    root = (path.resolve() if path else None) or ConfigStore().resolve(slug)
    if root is None or not (root / ".loompa" / "config.yaml").is_file():
        console.print(
            "[red]Nenhuma fábrica encontrada.[/red] Rode [bold]loompa init[/bold] na raiz do projeto."
        )
        raise typer.Exit(code=2)
    return Factory.open(root)


# ----------------------------------------------------------------------------- init


@app.command()
def init(
    path: Path = typer.Argument(Path("."), help="Raiz do projeto (novo ou existente)."),
    name: str | None = typer.Option(None, "--name", "-n", help="Nome da fábrica/produto."),
    stack: str | None = typer.Option(
        None, "--stack", "-s", help=f"Preset greenfield: {', '.join(STACK_PRESETS)}."
    ),
    mission: str = typer.Option("", "--mission", "-m", help="Missão do produto em uma frase."),
    mode: str | None = typer.Option(
        None, "--mode", help="Força greenfield|brownfield (padrão: detecta)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Não perguntar nada (usa padrões)."),
    preset: str | None = typer.Option(
        None, "--preset", "-p", help="Preset de modelos: gratuito | economico | maximo."
    ),
    secrets_scope: str = typer.Option(
        "hub", "--secrets-scope", help="Onde guardar chaves: hub (todas as fábricas) | factory."
    ),
    overwrite_constitution: bool = typer.Option(
        False, "--overwrite-constitution", help="Regrava constitution.md."
    ),
) -> None:
    """Conecta a fábrica a um repositório novo (greenfield) ou existente (brownfield)."""
    path = path.resolve()
    detected = mode or detect_mode(path)
    console.print(
        Panel.fit(
            f"[bold]Loompa LTDA[/bold] · init · {path}\nModo detectado: [cyan]{detected}[/cyan]"
        )
    )
    if name is None:
        name = path.name if yes else typer.prompt("Nome da fábrica/produto", default=path.name)
    if detected == "greenfield" and stack is None:
        if yes:
            stack = "custom"
        else:
            console.print("Stacks disponíveis:")
            for key, preset in STACK_PRESETS.items():
                console.print(f"  [cyan]{key:15}[/cyan] {preset.label}")
            stack = typer.prompt("Stack", default="python-fastapi")
    if not mission and not yes:
        mission = typer.prompt("Missão do produto (uma frase)", default="", show_default=False)

    result = bootstrap_factory(
        path,
        name=name,
        preset=stack or "custom",
        mission=mission,
        force_mode=mode,
        overwrite_constitution=overwrite_constitution,
    )
    f = result.factory
    console.print(
        f"[green]✔[/green] Fábrica [bold]{f.config.factory.name}[/bold] ([cyan]{f.slug}[/cyan]) pronta em {f.paths.loompa}"
    )
    if result.created_git:
        console.print("[green]✔[/green] Repositório git inicializado (branch main)")
    if result.skeleton_files:
        console.print(
            f"[green]✔[/green] {len(result.skeleton_files)} arquivos de esqueleto criados"
        )
    if result.audit:
        a = result.audit
        table = Table(title="Auditoria Brownfield", show_header=False)
        table.add_row("Arquivos", str(a.file_count))
        table.add_row("Linguagens", ", ".join(a.stack.languages) or "-")
        table.add_row("Frameworks", ", ".join(a.stack.frameworks) or "-")
        table.add_row(
            "Testes",
            f"{a.test_file_count} arquivos · {', '.join(a.stack.test_frameworks) or 'sem framework detectado'}",
        )
        table.add_row("Linters", ", ".join(a.stack.linters) or "-")
        table.add_row(
            "Comandos", "; ".join(f"{k}: {v}" for k, v in a.suggested_commands.items()) or "-"
        )
        console.print(table)
        for w in a.warnings:
            console.print(f"[yellow]![/yellow] {w}")
    # Step "Provedores e modelos": presets + keys (hidden prompt) + connection test.
    from loompa.cli.providers import setup_providers_interactive
    from loompa.config import MODEL_PRESETS, apply_preset

    if yes:
        if preset:
            if preset not in MODEL_PRESETS:
                console.print(f"[red]Preset desconhecido:[/red] {preset}")
                raise typer.Exit(code=1)
            apply_preset(f.config, preset)
            f.save()
            console.print(
                f"[green]✔[/green] preset {preset} aplicado (chaves via loompa providers set-key)"
            )
    else:
        setup_providers_interactive(f, preset=preset, scope=secrets_scope, out=console)
    console.print(f"Relatório executivo: {f.paths.onboarding_report}")
    console.print('Próximo passo: [bold]loompa meeting "metas de hoje"[/bold]')


@app.command()
def scan(path: Path = typer.Argument(Path("."), help="Raiz do projeto.")) -> None:
    """Reexecuta a auditoria Brownfield e imprime o resumo executivo."""
    from loompa.onboarding import BrownfieldScanner, executive_onboarding_summary

    audit = BrownfieldScanner(path.resolve()).scan()
    console.print(executive_onboarding_summary(audit, path.resolve().name))


# ------------------------------------------------------------------------ factories


@factories_app.command("list")
def factories_list() -> None:
    """Lista todas as fábricas registradas no hub."""
    registry = ConfigStore().load()
    if not registry.factories:
        console.print("Nenhuma fábrica registrada. Rode [bold]loompa init[/bold] em um projeto.")
        return
    table = Table(title="Fábricas")
    table.add_column("ativa")
    table.add_column("slug", style="cyan")
    table.add_column("nome")
    table.add_column("caminho")
    for ref in registry.factories:
        table.add_row("●" if ref.slug == registry.active else "", ref.slug, ref.name, str(ref.path))
    console.print(table)


@factories_app.command("use")
def factories_use(slug: str) -> None:
    """Define a fábrica ativa para os próximos comandos."""
    try:
        ref = ConfigStore().set_active(slug)
    except KeyError:
        console.print(f"[red]Fábrica desconhecida:[/red] {slug}")
        raise typer.Exit(code=1) from None
    console.print(f"[green]✔[/green] Fábrica ativa: [cyan]{ref.slug}[/cyan] ({ref.path})")


@factories_app.command("remove")
def factories_remove(slug: str) -> None:
    """Remove a fábrica do hub (não apaga arquivos)."""
    store = ConfigStore()
    registry = store.load()
    if not registry.remove(slug):
        console.print(f"[red]Fábrica desconhecida:[/red] {slug}")
        raise typer.Exit(code=1)
    store.save(registry)
    console.print(f"[green]✔[/green] {slug} removida do hub")


# ---------------------------------------------------------------------------- status


@app.command()
def status(
    factory: str | None = typer.Option(None, "--factory", "-f", help="Slug da fábrica."),
) -> None:
    """Resumo da fábrica: configuração, stack e estado dos artefatos."""
    f = resolve_factory(factory)
    c = f.config
    table = Table(title=f"{c.factory.name} ({f.slug})", show_header=False)
    table.add_row("Raiz", str(f.root))
    table.add_row("Modo", c.factory.mode)
    table.add_row("Stack", ", ".join([*c.stack.languages, *c.stack.frameworks]) or "-")
    table.add_row("Testes", c.quality.test_command or "-")
    table.add_row("Lint", c.quality.lint_command or "-")
    table.add_row("Paralelismo", str(c.schedule.max_parallel))
    table.add_row("Teto mensal", f"US$ {c.budget.monthly_cap_usd:.2f}")
    table.add_row(
        "Tier 1", ", ".join(f"{m.provider}/{m.model}" for m in c.models.tiers.get("tier1", []))
    )
    table.add_row(
        "Tier 2", ", ".join(f"{m.provider}/{m.model}" for m in c.models.tiers.get("tier2", []))
    )
    table.add_row("Constituição", "ok" if f.paths.constitution.is_file() else "[red]ausente[/red]")
    console.print(table)
    try:
        from loompa.cli.ops import print_runtime_status

        print_runtime_status(f, console)
    except ImportError:
        pass


def main() -> None:  # pragma: no cover - console entry
    app()


try:  # extended commands (meeting/run/inbox/dashboard) registered when the engine is present
    from loompa.cli import chat as _chat  # noqa: F401
    from loompa.cli import ops as _ops  # noqa: F401
    from loompa.cli import providers as _providers  # noqa: F401
    from loompa.cli import worker as _worker  # noqa: F401
except ImportError:  # pragma: no cover
    pass
