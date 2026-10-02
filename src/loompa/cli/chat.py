"""`loompa chat`: conversations with the Loompas (ADR-0010).

`chat meeting` is a Sprint Meeting with the Master, `chat brainstorm` a brainstorm led by the
Master, who calls in the Analyst or the Architect when the idea needs them (ADR-0020). Both keep a
draft inside the session; nothing reaches the backlog until `/sprint` or `/backlog` (in a
brainstorm, `/aprovar` first: the Product Owner proposes the cards, `/backlog` files them). A session survives the terminal: `chat resume C-001`.
A review with the Product Owner (ADR-0017) is born from `loompa story add` when the Product
Owner does not file the request, and is resumed the same way.
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from loompa.agents import Conversations
from loompa.cli.main import app, resolve_factory
from loompa.cli.ops import _run, build_context
from loompa.conversations import (
    Conversation,
    ConversationError,
    ConversationKind,
    ConversationStatus,
    MeetingMode,
)
from loompa.engine import EngineContext
from loompa.sprints import SprintError

console = Console()
chat_app = typer.Typer(help="Conversas: reunião de sprint e brainstorm, com o Master Loompa.")
app.add_typer(chat_app, name="chat")

INTRO = {
    ConversationKind.MEETING: (
        "Reunião de sprint com o Master Loompa",
        "Conte o que você quer fazer; o rascunho do backlog e do sprint muda a cada mensagem.",
    ),
    ConversationKind.BRAINSTORM: (
        "Brainstorm com o Master Loompa",
        "Pense em voz alta. O Master chama o Analyst ou o Architect quando a ideia pede; os "
        "pareceres são preliminares. Aprove o rumo (1º OK), o Product Owner propõe os cards e "
        "você grava (2º OK).",
    ),
    ConversationKind.REVIEW: (
        "Revisão do pedido com o Product Owner Loompa",
        "O Product Owner não gravou o card. Esclareça ou corrija o pedido; ele revê a cada mensagem.",
    ),
}
HELP_COMMON = (
    "/rascunho          mostra o rascunho\n"
    "/tirar D1 D2       tira cards do rascunho\n"
    "/meta <texto>      define a meta do sprint\n"
    "/descartar         encerra a conversa sem salvar nada\n"
    "/sair              guarda a conversa para retomar depois"
)
MODE_HELP = (
    "/atual             ajustar o sprint em andamento\n"
    "/proxima           pré-montar o próximo sprint (não recomendado)"
)
CURRENT_HELP = (
    "/incluir S-004     o card entra no sprint (o Product Owner avalia antes)\n"
    "/excluir S-002     o card sai do sprint e volta ao backlog\n"
    "/recomecar S-002 [motivo]  a história recomeça do zero\n"
    "/cancelar-sprint [motivo]  cancela o sprint (as histórias voltam ao backlog)\n"
    "/proposta          o Product Owner avalia o que entraria\n"
    "/aplicar           aplica as mudanças no sprint\n"
    "/descartar         encerra sem mudar nada\n"
    "/sair              guarda a conversa para retomar depois"
)
HELP = {
    ConversationKind.MEETING: (
        "/incluir D1 S-004  põe cards no sprint (e /excluir tira do sprint)\n"
        "/aposentar S-010 [motivo]  cancela um card do backlog ao salvar\n"
        "/proposta          o Product Owner propõe o sprint (vem antes de /sprint)\n"
        "/sprint [meta]     salva no backlog e começa o sprint\n"
        "/montar [meta]     salva e deixa o sprint montado, esperando o atual terminar\n"
        "/backlog           só salva no backlog\n" + HELP_COMMON
    ),
    ConversationKind.BRAINSTORM: (
        "/consultar analyst|architect <pergunta>  pede um parecer preliminar\n"
        "/deixar O1         deixa um parecer de lado (e /retomar O1 o traz de volta)\n"
        "/aprovar           aprova o rumo (1º OK): o Product Owner propõe os cards\n"
        "/voltar            descarta a proposta de cards e volta à conversa\n"
        "/backlog           grava a proposta do Product Owner (2º OK)\n"
        "/rascunho          mostra o rumo, as ideias, os pareceres e a proposta\n"
        "/tirar D1 | C1     tira uma ideia (ou um card da proposta)\n"
        "/descartar         encerra a conversa sem salvar nada\n"
        "/sair              guarda a conversa para retomar depois"
    ),
    ConversationKind.REVIEW: (
        "/gravar            grava o card mesmo com a objeção do Product Owner\n"
        "/descartar         desiste do pedido\n"
        "/sair              guarda a conversa para retomar depois"
    ),
}


def print_brainstorm(conv: Conversation) -> None:
    draft = conv.draft
    console.print(f"[bold]Rumo:[/bold] {draft.direction or '[dim](ainda não definido)[/dim]'}")
    for i in draft.items:
        console.print(
            f"  {i.key} · P{i.priority} · {i.title}"
            + (f" [yellow]({i.note})[/yellow]" if i.note else "")
        )
    if not draft.items:
        console.print("  [dim]Nenhuma ideia ainda.[/dim]")
    for o in draft.consults:
        state = (
            " [dim](deixado de lado)[/dim]"
            if o.dismissed
            else " [red](falhou)[/red]"
            if o.failed
            else ""
        )
        console.print(f"  [cyan]{o.key}[/cyan] {o.name}{state}: {o.summary[:120]}")
    if draft.split is not None:
        table = Table(title="Proposta do Product Owner (2º OK grava)", show_header=True)
        for col in ("", "card", "prio", "de", "obs"):
            table.add_column(col)
        for c in draft.split:
            what = (
                f"acrescenta a {c.story_id}"
                if c.amend
                else (f"já é {c.story_id}" if c.story_id else c.title)
            )
            table.add_row(c.key, what, f"P{c.priority}", " ".join(c.ideas), c.note)
        console.print(table)
    elif draft.ready:
        console.print(
            "[green]O Master acha que o rumo está claro: /aprovar quando concordar.[/green]"
        )


def print_draft(conv: Conversation) -> None:
    draft = conv.draft
    if conv.kind == ConversationKind.BRAINSTORM:
        print_brainstorm(conv)
        return
    if conv.kind == ConversationKind.MEETING and conv.mode is None:
        console.print(MODE_HELP)
        return
    if draft.goal:
        console.print(f"[bold]Meta do sprint:[/bold] {draft.goal}")
    if draft.cancel_sprint:
        console.print(f"[red]O {draft.sprint_id} será cancelado ao aplicar.[/red]")
    if not draft.items:
        console.print("[dim]O rascunho está vazio.[/dim]")
        return
    table = Table(title="Rascunho", show_header=True)
    for col in ("", "título", "prio", "sprint", "obs"):
        table.add_column(col)
    for i in draft.items:
        obs = i.note or ("já no backlog" if i.story_id else "")
        if i.story_id in draft.members:
            obs = i.stage + (" · recomeça" if i.restart else "") + ("" if i.in_sprint else " · sai")
        table.add_row(i.key, i.title, f"P{i.priority}", "✔" if i.in_sprint else "", obs)
    console.print(table)


def say_aloud(convs: Conversations, conv: Conversation, turn=None) -> None:
    """Print what the Loompas said since the founder's last message: the agent who answered and
    anyone it called in (the Product Owner's sprint proposal follows the Master's turn)."""
    last = max((i for i, t in enumerate(conv.turns) if t.who == "founder"), default=-1)
    for t in conv.turns[last + 1 :]:
        console.print(Panel(Text(t.text), title=t.name or "Loompa", border_style="cyan"))
        for change in t.changes:
            console.print(f"  [green]•[/green] {change}")
        for note in t.ignored:
            console.print(f"  [yellow]![/yellow] {note}")
    for limit in conv.limits:
        console.print(f"  [dim]{limit}[/dim]")
    if conv.kind == ConversationKind.REVIEW:
        print_review(conv)
    else:
        print_draft(conv)


def print_review(conv: Conversation) -> None:
    if not conv.draft.items:
        return
    item, review = conv.draft.items[0], conv.draft.review
    kind = f" · {review.kind}" if review and review.kind else ""
    console.print(f"[bold]Card como o Product Owner gravaria[/bold]{kind}: {item.title}")
    if item.description:
        console.print(f"[dim]{item.description}[/dim]")


async def session(
    ctx: EngineContext, conv: Conversation, first: str | None, *, run_after: bool
) -> None:
    convs = Conversations(ctx)
    title, hint = INTRO[conv.kind]
    console.print(Panel.fit(f"[bold]{title}[/bold] · {conv.id}\n{hint}\n\n{_help(conv)}"))
    if conv.turns:
        say_aloud(convs, conv)
    text = first
    while True:
        if text:
            try:
                await convs.say(conv.id, text)
            except ConversationError as exc:  # e.g. the meeting's subject is not chosen yet
                console.print(f"[yellow]{exc}[/yellow]")
            else:
                conv = convs.board.require(conv.id, open_only=False)
                say_aloud(convs, conv)
                if not conv.open:  # the Product Owner approved the request and filed it
                    return
        try:
            text = typer.prompt("Você", default="", show_default=False).strip()
        except typer.Abort:  # Ctrl-D / end of piped input: keep the session for later
            text = "/sair"
        if not text or not text.startswith("/"):
            continue  # back to the top: a plain message becomes the next turn
        command, _, rest = text.partition(" ")
        args = rest.split()
        try:
            if await handle(convs, ctx, conv, command.lower(), rest.strip(), args, run_after):
                return
        except (ConversationError, SprintError) as exc:
            console.print(f"[red]{exc}[/red]")
        conv = convs.board.get(conv.id) or conv
        text = None


async def handle(
    convs: Conversations,
    ctx: EngineContext,
    conv: Conversation,
    command: str,
    rest: str,
    args: list[str],
    run_after: bool,
) -> bool:
    """Run one slash command. True ends the session."""
    meeting = conv.kind == ConversationKind.MEETING
    brainstorm = conv.kind == ConversationKind.BRAINSTORM
    current = meeting and conv.mode == MeetingMode.CURRENT
    if command in ("/sair", "/quit", "/q"):
        console.print(f"Conversa guardada. Retome com [bold]loompa chat resume {conv.id}[/bold]")
        return True
    if command in ("/ajuda", "/help"):
        console.print(_help(conv))
    elif command in ("/atual", "/proxima") and meeting and conv.mode is None:
        mode = MeetingMode.CURRENT if command == "/atual" else MeetingMode.NEXT
        say_aloud(convs, convs.choose(conv.id, mode))
    elif command == "/recomecar" and current and args:
        _show(
            convs.edit(conv.id, [{"op": "restart", "ref": args[0], "reason": " ".join(args[1:])}])
        )
    elif command == "/cancelar-sprint" and current:
        if typer.confirm(f"Cancelar o {conv.draft.sprint_id} ao aplicar?", default=False):
            _show(convs.edit(conv.id, [{"op": "cancel_sprint", "reason": rest}]))
    elif command == "/aplicar" and current:
        result = await convs.commit(conv.id)
        say_aloud(convs, convs.board.require(conv.id, open_only=False))
        if run_after and (result.joined or result.restarted):
            await _run(ctx, until_idle=True)
        return True
    elif command == "/montar" and meeting and not current:
        result = await convs.commit(conv.id, plan_next=True, goal=rest)
        say_aloud(convs, convs.board.require(conv.id, open_only=False))
        return True
    elif command in ("/rascunho", "/draft"):
        print_draft(convs.board.require(conv.id))
    elif command == "/consultar" and brainstorm and len(args) >= 2:
        await convs.consult(conv.id, args[0], rest.partition(" ")[2])
        say_aloud(convs, convs.board.require(conv.id))
    elif command in ("/deixar", "/retomar") and brainstorm and args:
        op = "dismiss" if command == "/deixar" else "restore"
        _show(convs.edit(conv.id, [{"op": op, "ref": a} for a in args]))
    elif command == "/aprovar" and brainstorm:
        await convs.approve(conv.id)
        say_aloud(convs, convs.board.require(conv.id))
    elif command == "/voltar" and brainstorm:
        say_aloud(convs, convs.reopen(conv.id))
    elif command in ("/tirar", "/drop") and args:
        split = brainstorm and all(a.upper().startswith("C") for a in args)
        report = convs.edit(conv.id, [{"op": "drop", "ref": a} for a in args], split=split)
        _show(report)
    elif command == "/aposentar" and args and meeting:
        reason = rest.partition(" ")[2]
        _show(convs.edit(conv.id, [{"op": "retire", "ref": args[0], "reason": reason}]))
    elif command in ("/incluir", "/excluir") and args and meeting:
        flag = command == "/incluir"
        report = convs.edit(conv.id, [{"op": "update", "ref": a, "in_sprint": flag} for a in args])
        _show(report)
    elif command == "/meta" and conv.kind != ConversationKind.REVIEW:
        _show(convs.edit(conv.id, [{"op": "goal", "text": rest}]))
    elif command == "/proposta" and meeting:
        await convs.propose(conv.id)
        say_aloud(convs, convs.board.require(conv.id))
    elif command == "/gravar" and conv.kind == ConversationKind.REVIEW:
        result = await convs.commit(conv.id, force=True)
        say_aloud(convs, convs.board.require(conv.id, open_only=False))
        console.print(f"[green]✔[/green] {', '.join(result.created or result.existing)}")
        return True
    elif command == "/sprint" and meeting and not current:
        result = await convs.commit(conv.id, start_sprint=True, goal=rest)
        console.print(
            f"[green]✔[/green] {result.sprint_id} iniciado · "
            f"{len(result.created) + len(result.existing)} histórias no backlog"
        )
        if run_after:
            await _run(ctx, until_idle=True)
        else:
            console.print("Para executar: [bold]loompa run[/bold]")
        return True
    elif command == "/backlog" and conv.kind != ConversationKind.REVIEW and not current:
        result = await convs.commit(conv.id)
        made = ", ".join(result.created) or "nenhuma nova"
        console.print(f"[green]✔[/green] backlog atualizado ({made})")
        if result.amended:
            console.print(f"  acrescentado a: {', '.join(result.amended)}")
        after = convs.board.require(conv.id, open_only=False)
        for idea in after.draft.items if brainstorm else []:
            console.print(f"  [yellow]![/yellow] “{idea.title}” ficou de fora: {idea.note}")
        return after.status != ConversationStatus.OPEN
    elif command == "/descartar":
        convs.discard(conv.id)
        console.print("Conversa descartada; nada foi salvo.")
        return True
    else:
        console.print("[yellow]Comando desconhecido.[/yellow] /ajuda lista os comandos.")
    return False


def _help(conv: Conversation) -> str:
    if conv.kind == ConversationKind.MEETING and conv.mode is None:
        return MODE_HELP
    if conv.kind == ConversationKind.MEETING and conv.mode == MeetingMode.CURRENT:
        return CURRENT_HELP
    return HELP[conv.kind]


def _show(report) -> None:
    for change in report.changes:
        console.print(f"  [green]•[/green] {change}")
    for note in report.ignored:
        console.print(f"  [yellow]![/yellow] {note}")


def _start(
    kind: ConversationKind, text: list[str] | None, factory: str | None, dry_run: bool, run: bool
):
    f = resolve_factory(factory)
    ctx = build_context(f, dry_run=dry_run)

    async def go() -> None:
        convs = Conversations(ctx)
        conv = convs.open(kind)
        if kind == ConversationKind.MEETING:
            await convs.brief(conv.id)  # the Master opens with where the project stands
            conv = convs.board.require(conv.id)
        await session(ctx, conv, " ".join(text or []).strip() or None, run_after=run)

    try:
        ctx.index_memory()
        asyncio.run(go())
    finally:
        ctx.close()


@chat_app.command("meeting")
def chat_meeting(
    text: list[str] = typer.Argument(None, help="Primeira mensagem (opcional)."),
    factory: str | None = typer.Option(None, "--factory", "-f"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Sem chamadas de IA (simulação)."),
    run: bool = typer.Option(False, "--run", help="Executa o sprint assim que ele começar."),
) -> None:
    """Reunião de sprint com o Master: monta o backlog e o sprint conversando."""
    _start(ConversationKind.MEETING, text, factory, dry_run, run)


@chat_app.command("brainstorm")
def chat_brainstorm(
    text: list[str] = typer.Argument(None, help="Tema ou primeira mensagem (opcional)."),
    factory: str | None = typer.Option(None, "--factory", "-f"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Sem chamadas de IA (simulação)."),
) -> None:
    """Brainstorm com o Master, que chama o Analyst ou o Architect; o PO propõe os cards."""
    _start(ConversationKind.BRAINSTORM, text, factory, dry_run, False)


@chat_app.command("resume")
def chat_resume(
    conversation_id: str,
    factory: str | None = typer.Option(None, "--factory", "-f"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    run: bool = typer.Option(False, "--run", help="Executa o sprint assim que ele começar."),
) -> None:
    """Retoma uma conversa guardada."""
    f = resolve_factory(factory)
    ctx = build_context(f, dry_run=dry_run)
    try:
        try:
            conv = Conversations(ctx).board.require(conversation_id)
        except ConversationError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from None
        asyncio.run(session(ctx, conv, None, run_after=run))
    finally:
        ctx.close()


@chat_app.command("list")
def chat_list(
    all_: bool = typer.Option(False, "--all", "-a", help="Inclui as encerradas."),
    factory: str | None = typer.Option(None, "--factory", "-f"),
) -> None:
    """Conversas da fábrica."""
    f = resolve_factory(factory)
    ctx = build_context(f, dry_run=True)
    try:
        convs = Conversations(ctx).board.list(None if all_ else ConversationStatus.OPEN)
        if not convs:
            console.print(
                "Nenhuma conversa em aberto. Comece com [bold]loompa chat meeting[/bold]."
            )
            return
        table = Table(show_header=True)
        for col in ("id", "tipo", "estado", "título", "cards", "mensagens"):
            table.add_column(col)
        label = {"meeting": "reunião", "brainstorm": "brainstorm", "review": "revisão do PO"}
        state = {"open": "aberta", "committed": "salva", "discarded": "descartada"}
        for c in convs:
            table.add_row(
                c.id,
                label[c.kind.value],
                state[c.status.value],
                c.title[:50],
                str(len(c.draft.items)),
                str(len(c.turns)),
            )
        console.print(table)
    finally:
        ctx.close()


# ------------------------------------------------------------------------- quick story

story_app = typer.Typer(help="Histórias: um pedido rápido que o Product Owner lê antes de gravar.")
app.add_typer(story_app, name="story")


@story_app.command("add")
def story_add(
    text: list[str] = typer.Argument(..., help="O pedido, como você diria (título e detalhes)."),
    description: str = typer.Option("", "--desc", "-d", help="Detalhes do pedido."),
    factory: str | None = typer.Option(None, "--factory", "-f"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Sem chamadas de IA (simulação)."),
) -> None:
    """Pede um card ao Product Owner: ele classifica e grava, ou explica por que não."""
    f = resolve_factory(factory)
    ctx = build_context(f, dry_run=dry_run)
    try:
        out = asyncio.run(Conversations(ctx).quick_story(" ".join(text), description))
        if out.story_id:
            row = ctx.store.get_story(out.story_id) or {}
            triage = out.triage
            kind = (row.get("state") or {}).get("kind", "feature")
            console.print(
                f"[green]✔[/green] {out.story_id} gravada pelo Product Owner: "
                f"“{row.get('title', '')}” · {kind}"
            )
            extra = ((row.get("state") or {}).get("extra") or {}).get("po_triage") or {}
            if extra.get("rewritten"):
                console.print("  [dim]O texto foi reescrito; o seu original ficou no card.[/dim]")
            if triage and triage.reason:
                console.print(f"  [dim]{triage.reason}[/dim]")
            return
        conv, triage = out.conversation, out.triage
        console.print(
            f"[yellow]✘ O Product Owner não gravou o card.[/yellow] {conv.turns[-1].text}"
        )
        console.print(
            f"Converse com ele: [bold]loompa chat resume {conv.id}[/bold] (ou no painel, 💬)."
        )
        if triage and triage.reason_code == "too_big":
            console.print(
                f'Ou leve ao brainstorm: [bold]loompa chat brainstorm "{" ".join(text)}"[/bold]'
            )
    except ConversationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    finally:
        ctx.close()
