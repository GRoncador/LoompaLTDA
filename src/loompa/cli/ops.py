"""Operational commands: meeting, run, inbox, kaizen, memory, report, dashboard."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from loompa.cli.main import app, resolve_factory
from loompa.comms import FounderAnswer
from loompa.engine import KANBAN_COLUMNS, EngineContext, Scheduler, Stage, kanban_column
from loompa.factory import Factory
from loompa.llm import ModelRouter
from loompa.sprints import SprintBoard, SprintError, SprintStatus
from loompa.store import Store

console = Console()
inbox_app = typer.Typer(help="Caixa de Entrada do Founder (decisões em lote).")
app.add_typer(inbox_app, name="inbox")
memory_app = typer.Typer(help="Memória organizacional (RAG local).")
app.add_typer(memory_app, name="memory")
sprint_app = typer.Typer(help="Sprints: lotes de histórias que a fábrica executa juntos.")
app.add_typer(sprint_app, name="sprint")


def build_context(f: Factory, *, dry_run: bool = False) -> EngineContext:
    router = None
    if dry_run:
        from loompa.agents.dryrun import dry_run_provider

        provider = dry_run_provider()
        router = ModelRouter(
            f.config, providers=dict.fromkeys(f.config.providers, provider) | {"dry-run": provider}
        )
    return EngineContext.build(f, router=router, dry_run=dry_run)


def print_runtime_status(f: Factory, out: Console) -> None:
    if not f.paths.state_db.is_file():
        return
    store = Store(f.paths.state_db)
    stories = store.list_stories(f.slug)
    counts: dict[str, int] = {}
    for s in stories:
        col = kanban_column(s["stage"])
        counts[col] = counts.get(col, 0) + 1
    table = Table(title="Kanban", show_header=True)
    for _key, label in KANBAN_COLUMNS:
        table.add_column(label, justify="center")
    table.add_row(*[str(counts.get(key, 0)) for key, _ in KANBAN_COLUMNS])
    out.print(table)
    pending = [m for m in store.list_messages(f.slug, status="pending") if m.requires_action]
    totals = store.usage_totals(f.slug)
    out.print(
        f"Decisões pendentes: [bold]{len(pending)}[/bold] · Gasto acumulado: US$ {totals['cost_usd']:.2f} em {totals['calls']} consultas"
    )
    store.close()


# ---------------------------------------------------------------------------- meeting


@app.command()
def meeting(
    goals: list[str] = typer.Argument(
        None, help="Metas do dia (texto livre; separe com ';' ou linhas)."
    ),
    factory: str | None = typer.Option(None, "--factory", "-f"),
    file: Path | None = typer.Option(
        None, "--file", help="Arquivo de texto com as metas (ex.: transcrição de voz)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Sem chamadas de IA (simulação)."),
    run_after: bool = typer.Option(False, "--run", help="Já inicia a execução após a reunião."),
) -> None:
    """Reunião matinal: transforma metas em histórias no Kanban."""
    f = resolve_factory(factory)
    text = file.read_text(encoding="utf-8") if file else " ".join(goals or [])
    if not text.strip():
        text = typer.prompt("Metas de hoje")
    ctx = build_context(f, dry_run=dry_run)
    from loompa.agents import MasterAgent

    ctx.index_memory()
    result = asyncio.run(MasterAgent(ctx).meeting(text))
    table = Table(title="Histórias criadas")
    table.add_column("id", style="cyan")
    table.add_column("título")
    table.add_column("épico")
    table.add_column("prio")
    for s in result["stories"]:
        table.add_row(s["id"], s["title"], s["epic"], str(s["priority"]))
    console.print(table)
    for q in result["clarifications"]:
        console.print(f"[yellow]?[/yellow] {q}  (enviado à Caixa de Entrada)")
    if run_after:
        from loompa.agents import MasterAgent as Master

        if result["stories"]:
            sprint = Master(ctx).start_sprint([s["id"] for s in result["stories"]])
            console.print(
                f"[green]✔[/green] {sprint.id} iniciado com {len(sprint.story_ids)} histórias"
            )
        asyncio.run(_run(ctx, until_idle=True))
    elif result["stories"]:
        console.print("Histórias no backlog. Para executar: [bold]loompa sprint start[/bold]")
    ctx.close()


# -------------------------------------------------------------------------------- run


async def _run(
    ctx: EngineContext, *, until_idle: bool, max_parallel: int | None = None, poll: float = 1.0
) -> list[str]:
    def printer(ev: dict) -> None:
        t = ev["type"]
        p = ev.get("payload", {})
        sid = ev.get("story_id") or ""
        if t == "story.stage":
            console.print(f"[dim]{sid}[/dim] → [cyan]{p.get('stage')}[/cyan]")
        elif t == "inbox.new":
            console.print(f"[magenta]📬 {p.get('kind')}[/magenta] {p.get('title')}")
        elif t in ("story.escalated", "story.retry"):
            console.print(f"[yellow]{t}[/yellow] {sid} {p}")
        elif t == "llm.call":
            console.print(
                f"[dim]{ev.get('agent')} · {p.get('model')} · ${p.get('cost_usd', 0):.4f}[/dim]"
            )
        elif t == "scheduler.paused":
            console.print(f"[red]Esteira pausada: {p.get('reason')}[/red]")

    ctx.listeners.append(printer)
    sched = Scheduler(ctx, max_parallel=max_parallel)
    try:
        return await sched.run(until_idle=until_idle, poll_interval=poll)
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print(
            "[yellow]Interrompido — estado salvo nos checkpoints; rode `loompa run` para retomar.[/yellow]"
        )
        return sched.completed


@app.command()
def run(
    factory: str | None = typer.Option(None, "--factory", "-f"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simula o pipeline sem chamadas de IA."),
    watch: bool = typer.Option(
        False, "--watch", "-w", help="Continua rodando e pega novas histórias/respostas."
    ),
    parallel: int | None = typer.Option(None, "--parallel", "-p", help="Histórias simultâneas."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Execução contínua em lote: despacha as histórias em worktrees isolados."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(name)s %(levelname)s %(message)s",
    )
    f = resolve_factory(factory)
    ctx = build_context(f, dry_run=dry_run)
    ctx.index_memory()
    console.print(
        Panel.fit(
            f"[bold]{f.config.factory.name}[/bold] · execução {'simulada' if dry_run else 'real'} · paralelismo {parallel or f.config.schedule.max_parallel}"
        )
    )
    waiting = len(ctx.store.list_stories(f.slug, stage=Stage.BACKLOG))
    if waiting:
        console.print(
            f"[dim]{waiting} histórias esperam no backlog; `loompa sprint start` as coloca para rodar.[/dim]"
        )
    done = asyncio.run(_run(ctx, until_idle=not watch, max_parallel=parallel))
    console.print(f"[green]✔[/green] ciclo encerrado · {len(done)} histórias processadas")
    print_runtime_status(f, console)
    ctx.close()


# ----------------------------------------------------------------------------- sprint


@sprint_app.command("start")
def sprint_start(
    ids: list[str] = typer.Argument(
        None, help="Histórias do backlog (padrão: o rascunho do sprint ou o backlog do Founder)."
    ),
    goal: str = typer.Option("", "--goal", "-g", help="Meta do sprint em uma frase."),
    limit: int | None = typer.Option(None, "--limit", "-n", help="No máximo N histórias."),
    factory: str | None = typer.Option(None, "--factory", "-f"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Sem chamadas de IA (simulação)."),
    run_after: bool = typer.Option(False, "--run", help="Já executa o sprint."),
) -> None:
    """Começa um sprint: o Product Owner admite as histórias e a esteira passa a rodá-las."""
    from loompa.agents import MasterAgent

    f = resolve_factory(factory)
    ctx = build_context(f, dry_run=dry_run)
    try:
        sprint = MasterAgent(ctx).start_sprint(ids or None, goal=goal, limit=limit)
    except SprintError as exc:
        console.print(f"[red]{exc}[/red]")
        ctx.close()
        raise typer.Exit(code=1) from None
    console.print(
        f"[green]✔[/green] {sprint.id} iniciado · {len(sprint.story_ids)} histórias: "
        + ", ".join(sprint.story_ids)
    )
    if run_after:
        asyncio.run(_run(ctx, until_idle=True))
    ctx.close()


@sprint_app.command("add")
def sprint_add(
    ids: list[str] = typer.Argument(..., help="Histórias do backlog para o próximo sprint."),
    factory: str | None = typer.Option(None, "--factory", "-f"),
) -> None:
    """Monta o rascunho do próximo sprint sem começá-lo."""
    f = resolve_factory(factory)
    store = Store(f.paths.state_db)
    board = SprintBoard(store, f.slug)
    try:
        for sid in ids:
            sprint = board.add(sid)
    except SprintError as exc:
        console.print(f"[red]{exc}[/red]")
        store.close()
        raise typer.Exit(code=1) from None
    console.print(f"[green]✔[/green] {sprint.id} (aberto): {', '.join(sprint.story_ids)}")
    store.close()


@sprint_app.command("status")
def sprint_status(factory: str | None = typer.Option(None, "--factory", "-f")) -> None:
    """Sprints da fábrica e o andamento de cada história."""
    f = resolve_factory(factory)
    if not f.paths.state_db.is_file():
        console.print("Nenhum sprint ainda.")
        return
    store = Store(f.paths.state_db)
    board = SprintBoard(store, f.slug)
    sprints = board.sprints()
    if not sprints:
        console.print("Nenhum sprint ainda. Comece com [bold]loompa sprint start[/bold].")
    label = {"open": "aberto", "running": "rodando", "closed": "encerrado"}
    for sp in sprints:
        counts = board.progress(sp)
        console.print(
            f"[bold]{sp.id}[/bold] · {label[sp.status.value]} · {counts['done']}/{counts['total']} concluídas"
            + (f" · {counts['waiting']} aguardando você" if counts["waiting"] else "")
            + (f" · meta: {sp.goal}" if sp.goal else "")
        )
        if sp.status == SprintStatus.CLOSED:
            continue
        table = Table(show_header=True, box=None)
        for col in ("id", "título", "etapa"):
            table.add_column(col)
        for sid in sp.story_ids:
            row = store.get_story(sid)
            if row:
                table.add_row(sid, row["title"][:60], row["stage"])
        console.print(table)
    store.close()


# ------------------------------------------------------------------------------ inbox


@inbox_app.command("list")
def inbox_list(
    factory: str | None = typer.Option(None, "--factory", "-f"),
    all_: bool = typer.Option(False, "--all", "-a", help="Inclui respondidas."),
) -> None:
    """Lista as mensagens da Caixa de Entrada em linguagem executiva."""
    f = resolve_factory(factory)
    store = Store(f.paths.state_db)
    msgs = store.list_messages(f.slug, status=None if all_ else "pending")
    if not msgs:
        console.print("Caixa de entrada vazia. 🎉")
        return
    for m in msgs:
        body = f"[bold]{m.title}[/bold]\n\n{m.context}"
        if m.impact:
            body += f"\n\n[italic]{m.impact}[/italic]"
        if m.options:
            body += "\n\n" + "\n".join(
                f"  [{o.key}] {o.label}{' ★' if o.recommended else ''}" for o in m.options
            )
        for d in m.decisions:
            body += f"\n\n[bold]{d.id}[/bold] {d.title}" + (f"\n{d.context}" if d.context else "")
            body += "\n" + "\n".join(
                f"  {d.id}={o.key}  {o.label}{' ★' if o.recommended else ''}" for o in d.options
            )
            if d.chosen:
                body += f"\n  [green]decidido:[/green] {d.chosen}"
        if m.answer:
            body += (
                f"\n\n[green]Respondido:[/green] {m.answer.option_key or ''} {m.answer.text or ''}"
            )
        console.print(
            Panel(
                body,
                title=f"{m.kind.value.upper()} · {m.id} · {m.sender}"
                + (f" · {m.story_id}" if m.story_id else ""),
                subtitle=m.created_at.strftime("%d/%m %H:%M"),
            )
        )
    store.close()


@inbox_app.command("reply")
def inbox_reply(
    message_id: str,
    option: str | None = typer.Option(
        None, "--option", "-o", help="Chave da opção (ex.: approve, retry, skip)."
    ),
    text: str | None = typer.Option(None, "--text", "-t", help="Resposta livre / orientação."),
    decision: list[str] = typer.Option(
        None,
        "--decision",
        "-d",
        help="Decisão sobre um card sugerido, no formato S-007=sprint|backlog|drop (repetível).",
    ),
    factory: str | None = typer.Option(None, "--factory", "-f"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Responde uma mensagem; a história correspondente é destravada automaticamente."""
    decisions: dict[str, str] = {}
    for item in decision or []:
        card, sep, choice = item.partition("=")
        if not sep or not card.strip() or not choice.strip():
            console.print(f"[red]Decisão inválida: {item!r}. Use S-007=sprint|backlog|drop.[/red]")
            raise typer.Exit(code=1)
        decisions[card.strip()] = choice.strip()
    f = resolve_factory(factory)
    ctx = build_context(f, dry_run=dry_run)
    state = Scheduler(ctx).answer(
        message_id, FounderAnswer(option_key=option, text=text, decisions=decisions)
    )
    if state is None:
        console.print("[green]✔[/green] resposta registrada")
    else:
        console.print(f"[green]✔[/green] {state.story_id} → [cyan]{state.stage}[/cyan]")
    ctx.close()


@inbox_app.command("batch")
def inbox_batch(
    factory: str | None = typer.Option(None, "--factory", "-f"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Revisão de fim de dia: responde todas as pendências em sequência."""
    f = resolve_factory(factory)
    ctx = build_context(f, dry_run=dry_run)
    sched = Scheduler(ctx)
    pending = [
        m
        for m in ctx.store.list_messages(f.slug, status="pending")
        if m.requires_action or m.options
    ]
    if not pending:
        console.print("Nada pendente. 🎉")
        return
    for m in pending:
        console.print(
            Panel(
                f"[bold]{m.title}[/bold]\n\n{m.context}\n\n[italic]{m.impact}[/italic]",
                title=f"{m.kind.value.upper()} · {m.story_id or ''}",
            )
        )
        for o in m.options:
            console.print(f"  [{o.key}] {o.label}{' ★' if o.recommended else ''}")
        default = next(
            (o.key for o in m.options if o.recommended), m.options[0].key if m.options else ""
        )
        choice = typer.prompt("Opção (ou 'pular')", default=default)
        if choice == "pular":
            continue
        decisions: dict[str, str] = {}
        for d in m.decisions:
            console.print(f"  [bold]{d.id}[/bold] {d.title}")
            for o in d.options:
                console.print(f"    [{o.key}] {o.label}{' ★' if o.recommended else ''}")
            d_default = next((o.key for o in d.options if o.recommended), "backlog")
            decisions[d.id] = typer.prompt(f"  {d.id}", default=d_default)
        text = (
            typer.prompt("Orientação adicional", default="", show_default=False)
            if m.allow_free_text
            else None
        )
        state = sched.answer(
            m.id,
            FounderAnswer(
                option_key=choice if choice else None, text=text or None, decisions=decisions
            ),
        )
        console.print(f"  [green]✔[/green] {state.stage if state else 'ok'}")
    ctx.close()


# ----------------------------------------------------------------------- kaizen/report


@app.command()
def report(
    factory: str | None = typer.Option(None, "--factory", "-f"),
    dry_run: bool = typer.Option(True, "--dry-run/--live"),
) -> None:
    """Relatório executivo do dia (Master + Finance + Kaizen) enviado à Caixa de Entrada."""
    f = resolve_factory(factory)
    ctx = build_context(f, dry_run=dry_run)
    from loompa.agents import MasterAgent

    msg = MasterAgent(ctx).end_of_day_report()
    console.print(Panel(msg.context, title=msg.title))
    ctx.close()


@app.command()
def kaizen(factory: str | None = typer.Option(None, "--factory", "-f")) -> None:
    """Mostra os aprendizados capturados (learnings.md + cards gerados)."""
    f = resolve_factory(factory)
    store = Store(f.paths.state_db)
    rows = store.list_learnings()
    if not rows:
        console.print("Nenhum aprendizado registrado ainda.")
        return
    table = Table(title="Loop Kaizen")
    table.add_column("quando")
    table.add_column("origem")
    table.add_column("tipo")
    table.add_column("título")
    table.add_column("card")
    for r in rows[:50]:
        table.add_row(
            r["created_at"][:16],
            r["story_id"] or "",
            r["kind"],
            r["title"][:70],
            r["created_story_id"] or "",
        )
    console.print(table)
    store.close()


@app.command()
def ask(
    role: str = typer.Argument(..., help="compliance | metrics | storyteller"),
    request: list[str] = typer.Argument(..., help="Pedido em linguagem livre."),
    factory: str | None = typer.Option(None, "--factory", "-f"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    out: Path | None = typer.Option(None, "--out", help="Salva o resultado neste arquivo."),
) -> None:
    """Loompas de suporte sob demanda (LGPD, métricas/SQL, copy & documentação)."""
    from loompa.agents.support import SupportAgent

    f = resolve_factory(factory)
    ctx = build_context(f, dry_run=dry_run)
    try:
        agent = SupportAgent(ctx, role)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    msg = asyncio.run(agent.ask(" ".join(request)))
    console.print(Panel(msg.context, title=msg.title))
    if out:
        out.write_text(f"# {msg.title}\n\n{msg.context}\n", encoding="utf-8")
        console.print(f"[green]✔[/green] salvo em {out}")
    ctx.close()


# ----------------------------------------------------------------------------- memory


@memory_app.command("index")
def memory_index(factory: str | None = typer.Option(None, "--factory", "-f")) -> None:
    """Indexa constituição, ADRs, learnings, specs e docs na memória local."""
    f = resolve_factory(factory)
    ctx = build_context(f, dry_run=True)
    stats = ctx.index_memory()
    console.print(
        f"[green]✔[/green] memória: {stats['documents']} documentos · {stats['chunks']} trechos"
    )
    ctx.close()


@memory_app.command("search")
def memory_search(
    query: str,
    factory: str | None = typer.Option(None, "--factory", "-f"),
    k: int = typer.Option(5, "--top", "-k"),
) -> None:
    """Consulta semântica na memória organizacional."""
    f = resolve_factory(factory)
    ctx = build_context(f, dry_run=True)
    for h in ctx.memory.search(query, top_k=k):
        console.print(
            Panel(h.chunk.text[:600], title=f"[{h.chunk.kind}] {h.chunk.title} · {h.score:.2f}")
        )
    ctx.close()


# --------------------------------------------------------------------------- dashboard


@app.command()
def dashboard(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Motor em modo simulado."),
    no_engine: bool = typer.Option(
        False, "--no-engine", help="Só a interface; não executa histórias."
    ),
) -> None:
    """Painel da fábrica em localhost (escritório 2D + inbox + kanban + multi-fábrica)."""
    import uvicorn

    from loompa.dashboard.app import create_app

    ref_host = host or "127.0.0.1"
    ref_port = port or 8765
    try:
        f = resolve_factory(None)
        ref_host = host or f.config.dashboard.host
        ref_port = port or f.config.dashboard.port
    except typer.Exit:
        pass
    console.print(Panel.fit(f"[bold]Loompa LTDA HQ[/bold] → http://{ref_host}:{ref_port}"))
    uvicorn.run(
        create_app(dry_run=dry_run, run_engine=not no_engine),
        host=ref_host,
        port=ref_port,
        log_level="warning",
    )
