"""`loompa worker`: pick the Worker backend (ACI vs OpenCode spike, ADR-0007)."""

from __future__ import annotations

import typer
from rich.console import Console

from loompa.cli.main import app, resolve_factory

console = Console()
worker_app = typer.Typer(help="Backend do Worker (aci | opencode) para esta fábrica.")
app.add_typer(worker_app, name="worker")


@worker_app.command("backend")
def worker_backend(
    value: str = typer.Argument(
        None, help="aci | opencode — omitido mostra o backend atual"
    ),
    factory: str | None = typer.Option(None, "--factory", "-f"),
) -> None:
    """Mostra ou define `worker.backend`. `opencode` exige o binário `opencode` no PATH."""
    f = resolve_factory(factory)
    if value is None:
        console.print(f"backend atual: [bold]{f.config.worker.backend}[/bold]")
        return
    if value not in ("aci", "opencode"):
        console.print(f"[red]valor inválido:[/red] {value}. Use aci ou opencode.")
        raise typer.Exit(code=1)
    f.config.worker.backend = value  # type: ignore[assignment]
    f.save()
    console.print(f"[green]✔[/green] backend do Worker definido como [bold]{value}[/bold]")
