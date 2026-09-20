"""`loompa models`: refresh the model list from the OpenRouter catalogue (ADR-0011)."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from loompa.cli.main import app, resolve_factory
from loompa.cli.ops import build_context
from loompa.llm.catalog import CatalogError, Policy
from loompa.models_sync import TIER_LABEL, AliasWatch, ModelSync, excluded_report, plan

console = Console()
models_app = typer.Typer(help="Lista de modelos de IA e o catálogo da OpenRouter.")
app.add_typer(models_app, name="models")


@models_app.command("sync")
def models_sync(
    factory: str | None = typer.Option(None, "--factory", "-f"),
    preview: bool = typer.Option(
        False, "--preview", help="Só mostra a proposta; não envia nada ao inbox."
    ),
    tier1_ceiling: float = typer.Option(
        Policy.tier1_ceiling,
        "--tier1-ceiling",
        help="Raciocínio: custo máximo em US$ por milhão de tokens (3 de entrada : 1 de saída).",
    ),
    tier2_floor: float = typer.Option(
        Policy.tier2_floor,
        "--tier2-floor",
        help="Execução: fração da melhor nota do catálogo que o modelo precisa atingir.",
    ),
    picks: int = typer.Option(
        Policy.picks, "--picks", help="Modelos por tier (um por fabricante)."
    ),
    ids: str = typer.Option(
        Policy.ids,
        "--ids",
        help="alias (~fabricante/modelo-latest) | pinned (versão fixa) | auto (o que a fábrica já usa).",
    ),
) -> None:
    """Compara o catálogo da OpenRouter e propõe a lista de modelos ao Founder pelo inbox.

    Nada muda na configuração até a aprovação, e a troca só vale quando não há sprint em andamento.
    """
    f = resolve_factory(factory)
    if ids not in ("alias", "pinned", "auto"):
        console.print("[red]✘[/red] --ids deve ser alias, pinned ou auto.")
        raise typer.Exit(code=1)
    policy = Policy(tier1_ceiling=tier1_ceiling, tier2_floor=tier2_floor, picks=picks, ids=ids)
    try:
        proposal = plan(f.config, policy)
    except CatalogError as exc:
        console.print(f"[red]✘[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"Catálogo: {proposal.considered} modelos, {proposal.eligible} passaram nos critérios "
        f"({'apelidos -latest' if proposal.mode == 'alias' else 'versões fixas'})."
    )
    for reason, n in excluded_report(proposal):
        console.print(f"  [dim]{n:>4} fora: {reason}[/dim]")
    for tier, picked in proposal.summary.items():
        table = Table(title=TIER_LABEL.get(tier, tier), show_lines=False)
        table.add_column("modelo", style="cyan")
        table.add_column("nota", justify="right")
        table.add_column("US$/mi tokens", justify="right")
        for m in picked:
            table.add_row(m["id"], f"{m['quality']:.1f}", f"{m['price']:.2f}")
        console.print(table)
    for label, ids in (("saem", proposal.removed), ("já fora do catálogo", proposal.gone)):
        if ids:
            console.print(f"[yellow]{label}:[/yellow] {', '.join(ids)}")
    if proposal.repriced:
        console.print(f"[yellow]preço a atualizar:[/yellow] {', '.join(proposal.repriced)}")
    if proposal.expiring:
        console.print(
            f"[yellow]serão desativados em breve:[/yellow] {', '.join(proposal.expiring)}"
        )

    if preview:
        console.print(
            "Prévia: nada foi enviado ao inbox."
            if proposal.changed
            else "[green]✔[/green] Os modelos atuais já são a melhor lista. Nada a trocar."
        )
        return
    # No model is called: dry-run only keeps the context from loading the embedder.
    ctx = build_context(f, dry_run=True)
    try:
        AliasWatch(ctx).check_targets(
            proposal.targets
        )  # an alias that moved is told even if the list holds
        msg = ModelSync(ctx).propose(proposal)
    finally:
        ctx.close()
    if msg is None:
        console.print("[green]✔[/green] Os modelos atuais já são a melhor lista. Nada a trocar.")
    else:
        console.print(
            f"[green]✔[/green] Proposta enviada ao inbox ({msg.id}). Para aprovar: "
            f"loompa inbox reply {msg.id} --option approve"
        )
