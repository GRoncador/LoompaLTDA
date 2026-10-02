"""`loompa factory-health`: the factory's own findings (Fase 8.3 and 8.4).

Findings live in the hub and are about Loompa, not the product: list them, read one with its
evidence, scan a sprint or a period again, mark one resolved with the commit that fixed it, and
export them as `factory-improvements.md` to bring to a Loompa development session.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from loompa.cli.main import app, resolve_factory
from loompa.cli.ops import build_context
from loompa.factory_health import export_markdown, hub_book, scan
from loompa.sprints import SprintBoard

console = Console()
health_app = typer.Typer(
    help="Autodiagnóstico da fábrica: achados sobre o próprio Loompa, com evidência.",
    invoke_without_command=True,
)
app.add_typer(health_app, name="factory-health")

SEVERITY = {"high": "[red]alta[/red]", "medium": "[yellow]média[/yellow]", "low": "baixa"}


@health_app.callback()
def health_main(
    ctx: typer.Context,
    all_: bool = typer.Option(False, "--all", "-a", help="Inclui os resolvidos."),
    export: Path | None = typer.Option(
        None,
        "--export",
        help="Escreve os achados abertos neste arquivo (factory-improvements.md).",
    ),
    factory: str | None = typer.Option(None, "--factory", "-f", help="Só os desta fábrica."),
) -> None:
    """Lista os achados abertos (todas as fábricas)."""
    if ctx.invoked_subcommand:
        return
    rows = hub_book().findings(status=None if all_ else "open", factory=factory)
    if export is not None:
        export.write_text(export_markdown(rows), encoding="utf-8")
        console.print(f"[green]✔[/green] {len(rows)} achados em {export}")
        return
    if not rows:
        console.print("Nenhum achado aberto. Rode [bold]loompa factory-health scan[/bold].")
        return
    table = Table(show_header=True)
    for col in ("assinatura", "gravidade", "área", "achado", "visto", "tendência"):
        table.add_column(col)
    for r in rows:
        trend = "".join("●" if t["seen"] else "○" for t in r["trend"])
        state = "" if r["status"] == "open" else " [green](resolvido)[/green]"
        table.add_row(
            r["signature"],
            SEVERITY.get(r["severity"], r["severity"]),
            r["area"],
            r["title"] + (" [dim](provisório)[/dim]" if r["provisional"] else "") + state,
            ", ".join(r["factories"]),
            trend,
        )
    console.print(table)
    console.print("[dim]● visto na varredura · ○ ausente — o mais recente à direita[/dim]")


@health_app.command("show")
def health_show(signature: str) -> None:
    """Um achado com a evidência e os comandos `loompa trace` que abrem cada uma."""
    row = hub_book().get(signature)
    if row is None:
        console.print(f"[red]Nenhum achado começa com {signature}.[/red]")
        raise typer.Exit(1)
    console.print(f"[bold]{row['title']}[/bold]  ({row['signature']})")
    console.print(
        f"gravidade {SEVERITY.get(row['severity'], row['severity'])} · área {row['area']} · "
        f"{row['status']} · visto em {row['seen_in']}"
        + (" · limiar provisório" if row["provisional"] else "")
    )
    console.print(row["detail"])
    if row.get("impact"):
        console.print("impacto: " + ", ".join(f"{k} {v}" for k, v in row["impact"].items()))
    if row.get("hypothesis"):
        console.print(f"[cyan]hipótese:[/cyan] {row['hypothesis']}")
    if row.get("fix"):
        console.print(f"[cyan]correção sugerida (hipótese):[/cyan] {row['fix']}")
    for ev in row.get("evidence") or []:
        cmd = ev.get("command")
        rest = {k: v for k, v in ev.items() if k != "command"}
        console.print(f"  • {json.dumps(rest, ensure_ascii=False)}" + (f"  → {cmd}" if cmd else ""))
    if row.get("resolved_commit"):
        console.print(f"[green]resolvido em {row['resolved_commit']}[/green]")


@health_app.command("scan")
def health_scan(
    sprint_id: str | None = typer.Argument(None, help="O sprint (padrão: os últimos 7 dias)."),
    since: str | None = typer.Option(None, "--since", help="Desde esta data (AAAA-MM-DD)."),
    factory: str | None = typer.Option(None, "--factory", "-f"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Sem chamada de IA para as hipóteses."),
) -> None:
    """Procura os sinais de novo, sobre um sprint ou um período, e guarda no hub."""
    f = resolve_factory(factory)
    ctx = build_context(f, dry_run=dry_run)
    try:
        sprint = SprintBoard(ctx.store, f.slug).get(sprint_id) if sprint_id else None
        if sprint_id and sprint is None:
            console.print(f"[red]{sprint_id} não existe.[/red]")
            raise typer.Exit(1)
        result = asyncio.run(scan(ctx, sprint=sprint, since=since))
    finally:
        ctx.close()
    console.print(
        f"{len(result.findings)} achados ({len(result.new)} novos, {len(result.back)} voltaram, "
        f"{len(result.confirmed)} correções confirmadas) entre {result.since[:16]} e "
        f"{result.until[:16]}."
    )
    for fnd in result.findings:
        mark = " [green]novo[/green]" if fnd.signature in result.new else ""
        mark += " [red]voltou[/red]" if fnd.signature in result.back else ""
        console.print(f"  [{fnd.severity}] {fnd.signature}: {fnd.title}{mark}")


@health_app.command("resolve")
def health_resolve(
    signature: str,
    commit: str = typer.Option(..., "--commit", "-c", help="O commit do Loompa que corrigiu."),
) -> None:
    """Marca um achado como resolvido. A próxima varredura sem ele confirma a correção."""
    row = hub_book().get(signature)
    if row is None or not hub_book().resolve(row["signature"], commit):
        console.print(f"[red]Nenhum achado começa com {signature}.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]✔[/green] {row['signature']} resolvido em {commit}")
