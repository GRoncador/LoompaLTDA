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
        help="Execução: fração da melhor nota do tier 1 (abaixo do teto) que o modelo precisa atingir.",
    ),
    picks: int = typer.Option(
        Policy.picks, "--picks", help="Modelos por tier (um por fabricante)."
    ),
) -> None:
    """Compara o catálogo da OpenRouter e propõe a lista de modelos ao Founder pelo inbox.

    Nada muda na configuração até a aprovação, e a troca só vale quando não há sprint em andamento.
    A proposta usa só ids fixos: os três candidatos de cada tier já cobrem um modelo fora do ar,
    e um apelido -latest mudaria de modelo (e de preço) sem ninguém decidir.
    """
    f = resolve_factory(factory)
    policy = Policy(tier1_ceiling=tier1_ceiling, tier2_floor=tier2_floor, picks=picks)
    from loompa.models_sync import house_for
    from loompa.store import Store

    db = f.paths.state_db
    house = house_for(Store(db), f.slug, f.config) if db.is_file() else {}
    try:
        proposal = plan(f.config, policy, house=house)
    except CatalogError as exc:
        console.print(f"[red]✘[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"Catálogo: {proposal.considered} modelos, {proposal.eligible} passaram nos critérios "
        "(sempre versões fixas: um apelido -latest troca de modelo sem ninguém aprovar)."
    )
    for reason, n in excluded_report(proposal):
        console.print(f"  [dim]{n:>4} fora: {reason}[/dim]")
    for tier, picked in proposal.summary.items():
        table = Table(title=TIER_LABEL.get(tier, tier), show_lines=False)
        table.add_column("modelo", style="cyan")
        table.add_column("nota", justify="right")
        table.add_column("US$/mi tokens", justify="right")
        table.add_column("na casa (Worker)", justify="right")
        for m in picked:
            h = m.get("house") or {}
            mark = ""
            if h.get("tasks"):
                mark = f"{h['done_rate']:.0%} de {h['tasks']} tarefas"
                mark += " [yellow]▼ abaixo dos outros[/yellow]" if h.get("flagged") else ""
                mark += "" if h.get("enough") else " [dim](amostra pequena)[/dim]"
            table.add_row(m["id"], f"{m['score']:.1f}", f"{m['price']:.2f}", mark)
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


@models_app.command("available")
def models_available(
    factory: str | None = typer.Option(None, "--factory", "-f"),
    refresh: bool = typer.Option(
        True,
        "--refresh/--cached",
        help="Pergunta aos provedores agora (ou mostra o último resultado).",
    ),
    show: str = typer.Option("", "--show", help="Lista os modelos deste provedor (ex.: xiaomi)."),
) -> None:
    """O que cada chave da fábrica consegue chamar: a lista de modelos de cada provedor com chave.

    Também confere se os modelos que vão direto ao fabricante (chave própria) existem na lista dele.
    """
    import asyncio

    from loompa.config.secrets import Secrets
    from loompa.llm.available import available, byok_checks, cached

    f = resolve_factory(factory)
    secrets = Secrets.load(f.root)
    results = asyncio.run(available(f.config, secrets)) if refresh else cached(f.config, secrets)
    if not results:
        console.print(
            "Nenhum provedor com chave nesta fábrica (ou nada guardado ainda: use --refresh)."
        )
        return
    table = Table(title="Modelos por provedor", show_lines=False)
    table.add_column("provedor")
    table.add_column("situação")
    for r in sorted(results, key=lambda r: r.name):
        mark = "[green]✔[/green]" if r.ok else "[red]✘[/red]"
        table.add_row(
            r.label, f"{mark} {r.detail}" + (f" [dim]({r.reason})[/dim]" if r.reason else "")
        )
    console.print(table)
    for check in byok_checks(f.config, results):
        if check.found is False:
            console.print(
                f"[yellow]![/yellow] {check.openrouter_id}: o id {check.own_id} não está na lista "
                f"de {f.config.providers[check.provider].label}; essas chamadas vão cair na OpenRouter"
            )
        elif check.found:
            console.print(f"[green]✔[/green] {check.openrouter_id} vai direto como {check.own_id}")
    from loompa.llm.modelsdev import identity

    for name, cfg in f.config.providers.items():
        if not (cfg.byok_models or cfg.id_prefix or cfg.models_dev_id):
            continue
        ident = identity(f.config, name)
        if ident is None:
            continue
        line = f"{cfg.label}: {len(ident.pairs)} modelos com par na OpenRouter, sem par: {len(ident.unmatched)}"
        console.print(f"[dim]{line}[/dim]")
        for or_id, own in ident.contradicted.items():
            console.print(
                f"[yellow]![/yellow] {or_id} → {own} não é o mesmo modelo segundo o models.dev; "
                "esse par fica de fora"
            )
    if show:
        hit = next((r for r in results if r.name == show), None)
        console.print("\n".join(hit.models) if hit else f"[red]✘[/red] sem resultado para {show}")


@models_app.command("aa")
def models_aa(
    factory: str | None = typer.Option(None, "--factory", "-f"),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Consulta a Artificial Analysis agora (1 das 100 consultas do dia).",
    ),
) -> None:
    """Sugestão de modelos pela Artificial Analysis: os 5 melhores de cada tier por perfil de papel,
    cada um num nível de esforço. Não olha chaves nem provedores: escolher e configurar é do Founder.
    """
    import asyncio

    from loompa.config.secrets import Secrets
    from loompa.llm import aa_rank

    f = resolve_factory(factory)
    tool = f.config.tools.artificial_analysis
    if refresh or aa_rank.stale():
        key = Secrets.load(f.root).get(tool.api_key_env)
        if not key:
            console.print(
                "[red]✘[/red] Configure a chave da Artificial Analysis em Configurações › Provedores."
            )
            raise typer.Exit(code=1)
        try:
            asyncio.run(aa_rank.refresh(key, tool.base_url))
        except Exception:  # noqa: BLE001 - the cached ranking (if any) is shown below
            console.print("[yellow]![/yellow] Não consegui consultar a Artificial Analysis agora.")
    from loompa.store import Store

    db = f.paths.state_db
    mix = aa_rank.work_mix(Store(db), f.slug, f.config) if db.is_file() else None
    result = aa_rank.suggest(f.config, mix=mix)
    if not result["entries"]:
        console.print("Ainda não há dados da Artificial Analysis: use --refresh.")
        return
    console.print(f"[dim]{result['price_note']}[/dim]")
    console.print(
        f"{result['entries']} modelos avaliados · teto do tier 1 US$ {result['ceiling']:.2f}/mi "
        f"· piso do tier 2 {result['floor_share']:.0%} do melhor · {result['attribution']}"
    )
    active = (
        {"engineering", "strategy", "routine"} if f.config.models.clusters_enabled else {"general"}
    )
    for p in [p for p in result["profiles"] if p["profile"] in active]:
        console.print(
            f"\n[bold]{p['label']}[/bold] [dim](fora por falta de benchmark: {p['partial']})[/dim]"
        )
        weights = ", ".join(f"{k} {v:.0%}" for k, v in p["weights"].items())
        console.print(f"[dim]pesos: {weights}[/dim]")
        if p.get("mix"):
            mix = ", ".join(f"{k} {v:.0%}" for k, v in p["mix"].items())
            console.print(f"[dim]mistura do trabalho da fábrica: {mix}[/dim]")
        for tier, title in (
            ("tier1", "Tier 1"),
            ("tier2", "Tier 2 (custo-benefício)"),
            ("tier3", "Tier 3 (grátis)"),
        ):
            table = Table(title=title, show_lines=False)
            for col in (
                "modelo",
                "esforço",
                "nota",
                "US$/mi",
                "pts/$",
                "tok/s",
                "onde é grátis" if tier == "tier3" else "",
            ):
                table.add_column(col)
            for x in p[tier]:
                table.add_row(
                    x["name"],
                    {"none": "sem raciocínio", "": "padrão"}.get(x["effort"], x["effort"]),
                    f"{x['score']:.1f}",
                    f"{x['price']:.2f}" if x["price"] else "—",
                    f"{x['value']:.0f}" if x.get("value") else "—",
                    f"{x['speed']:.0f}" if x.get("speed") else "—",
                    ", ".join(x["free_at"]) if tier == "tier3" else "",
                )
            if p[tier]:
                console.print(table)
