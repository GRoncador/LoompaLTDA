"""`loompa setup` and `loompa doctor`: get every service a factory needs working, without knowing
in advance which ones there are.

`setup` is the onboarding wizard (`loompa init` ends in it) and can be re-run any time: each step
says what the thing is for, shows what is already in place and only asks for what is missing.
`doctor` is the same checklist without questions, for "is it all still working?".
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

import typer
from rich.console import Console
from rich.table import Table

from loompa.cli.main import app, resolve_factory
from loompa.cli.providers import (
    _probe_all,
    _secrets,
    print_providers,
    setup_providers_interactive,
    setup_web_search_interactive,
)
from loompa.config.services import Service, collect_services, gh_logged_in
from loompa.factory import Factory

console = Console()
STEPS = 4


def _step(out: Console, n: int, title: str) -> None:
    out.print(f"\n[bold cyan]Passo {n}/{STEPS} · {title}[/bold cyan]")


def setup_worker_interactive(f: Factory, *, out: Console) -> None:
    """Who writes the code: the built-in Worker (works now) or OpenCode (needs its own install)."""
    cfg = f.config.worker
    found = shutil.which(cfg.opencode_bin)
    out.print(
        "  [cyan]1[/cyan]) Worker embutido do Loompa — recomendado, já funciona\n"
        "  [cyan]2[/cyan]) OpenCode — agente de programação externo (experimental)"
        + ("" if found else " [dim](não instalado nesta máquina)[/dim]")
    )
    choice = typer.prompt("Escolha", default="2" if cfg.backend == "opencode" else "1").strip()
    if choice == "2":
        if found:
            out.print(
                "  [green]✔[/green] OpenCode encontrado. Ele usa o mesmo modelo e a mesma chave "
                "do tier de execução."
            )
        else:
            out.print(
                f"  [yellow]![/yellow] não achei o comando `{cfg.opencode_bin}`. Instale em "
                "https://opencode.ai, rode [bold]loompa setup[/bold] de novo e escolha 2. "
                "Por enquanto fica o Worker embutido."
            )
            choice = "1"
    backend = "opencode" if choice == "2" else "aci"
    if backend != cfg.backend:
        cfg.backend = backend  # type: ignore[assignment]
        f.save()
    out.print(f"  Worker: [bold]{'OpenCode' if backend == 'opencode' else 'embutido'}[/bold]")


def setup_delivery_interactive(f: Factory, *, out: Console) -> None:
    """GitHub: lets the Deployer open the Pull Request. Optional; without it delivery stays local."""
    out.print("  Deixa o Deployer abrir o Pull Request sozinho quando você aprova uma entrega.")
    if gh_logged_in():
        out.print("  [green]✔[/green] GitHub conectado")
        return
    if not shutil.which("gh"):
        out.print(
            "  Opcional. Para ligar: instale https://cli.github.com e rode "
            "[bold]loompa setup[/bold] de novo."
        )
        return
    if not typer.confirm(
        "  O GitHub não está conectado. Conectar agora (gh auth login)?", default=False
    ):
        out.print("  Tudo bem: a entrega fica só no git local.")
        return
    try:
        subprocess.run(["gh", "auth", "login"], check=False)
    except OSError as exc:
        out.print(f"  [yellow]![/yellow] não consegui abrir o gh: {exc}")
    out.print(
        "  [green]✔[/green] GitHub conectado"
        if gh_logged_in()
        else "  [yellow]![/yellow] ainda não conectado; a entrega fica só no git local."
    )


def print_checklist(
    services: list[Service],
    out: Console,
    probes: dict[str, tuple[bool, str]] | None = None,
) -> None:
    table = Table(title="Serviços", show_lines=False)
    table.add_column("", width=1)
    table.add_column("serviço", style="cyan")
    table.add_column("para quê")
    table.add_column("situação")
    for s in services:
        probe = (probes or {}).get(s.id)
        ok = s.ok and (probe is None or probe[0])
        if ok:
            mark = "[green]✔[/green]"
        else:
            mark = "[red]✘[/red]" if s.need == "required" else "[dim]○[/dim]"
        state = s.detail if probe is None or not s.ok else f"{s.detail} · {probe[1]}"
        if not ok and s.fix:
            state += f"\n[dim]{s.fix}[/dim]"
        optional = "" if s.need == "required" else " [dim](opcional)[/dim]"
        table.add_row(mark, s.label + optional, s.purpose, state)
    out.print(table)


def run_setup(
    f: Factory, *, preset: str | None, scope: str, out: Console, test: bool = True
) -> list[Service]:
    """The whole wizard. Returns the final checklist."""
    where = (
        "~/.loompa/secrets.env (valem para todas as fábricas)"
        if scope == "hub"
        else ".loompa/.env desta fábrica"
    )
    out.print(f"[dim]As chaves ficam em {where}, nunca no git nem na configuração.[/dim]")
    _step(out, 1, "Modelos de IA")
    setup_providers_interactive(f, preset=preset, scope=scope, out=out, test=test)
    _step(out, 2, "Pesquisa na web")
    setup_web_search_interactive(f, scope=scope, out=out, test=test)
    _step(out, 3, "Quem programa")
    setup_worker_interactive(f, out=out)
    _step(out, 4, "Entrega no GitHub")
    setup_delivery_interactive(f, out=out)
    services = collect_services(f.config, _secrets(f))
    out.print()
    print_checklist(services, out)
    missing = [s.label for s in services if s.blocking]
    if missing:
        out.print(
            f"\n[yellow]Falta:[/yellow] {', '.join(missing)}. "
            "Rode [bold]loompa setup[/bold] de novo quando tiver."
        )
    else:
        out.print("\n[green]Tudo o que é obrigatório está pronto.[/green]")
    return services


@app.command()
def setup(
    factory: str | None = typer.Option(None, "--factory", "-f"),
    preset: str | None = typer.Option(
        None,
        "--preset",
        "-p",
        help="Conjunto de modelos: openrouter | gratuito | economico | maximo.",
    ),
    scope: str = typer.Option("hub", "--scope", help="Onde guardar chaves: hub | factory."),
    test: bool = typer.Option(True, "--test/--no-test", help="Testa cada chave ao guardar."),
) -> None:
    """Assistente: modelos, chaves (OpenRouter, Tavily…), OpenCode e GitHub, um passo por vez."""
    f = resolve_factory(factory)
    run_setup(f, preset=preset, scope=scope, out=console, test=test)
    print_providers(f, console)


@app.command()
def doctor(
    factory: str | None = typer.Option(None, "--factory", "-f"),
    test: bool = typer.Option(
        True, "--test/--no-test", help="Faz uma chamada mínima em cada chave."
    ),
) -> None:
    """Confere se tudo o que a fábrica usa está pronto, sem perguntar nada."""
    f = resolve_factory(factory)
    services = collect_services(f.config, _secrets(f))
    probes: dict[str, tuple[bool, str]] = {}
    if test:
        keyed = [s.id for s in services if s.key_env and s.ok]
        if keyed:
            for name, ok, detail in asyncio.run(_probe_all(f, keyed)):
                probes[name] = (ok, detail)
    print_checklist(services, console, probes)
    failing = [
        s
        for s in services
        if s.blocking or (s.need == "required" and s.id in probes and not probes[s.id][0])
    ]
    if failing:
        console.print("\nCorrija com [bold]loompa setup[/bold].")
        raise typer.Exit(code=1)
    console.print("\n[green]Tudo certo.[/green]")
