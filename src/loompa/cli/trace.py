"""`loompa trace`: read a story's trace in the terminal (Fase 8.1).

A tool for developing Loompa, not for the founder: it shows what each round of a tool loop sent
and got back — model, tokens, why the answer ended, the tools and their arguments — and, for one
call, the whole prompt. `--stats` puts time and cost side by side per role and model, which is
how a tier-2 model is compared with another on the same work before a preset changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from loompa.cli.main import app, resolve_factory
from loompa.store import Store
from loompa.trace import Trace, TraceNode, read_trace

console = Console()


def _k(n: Any) -> str:
    n = int(n or 0)
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _dur(ms: Any) -> str:
    s = float(ms or 0) / 1000
    if s < 60:
        return f"{s:.1f}s"
    m, s = divmod(int(s), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _clock(iso: str) -> str:
    return iso[11:19] if len(iso) >= 19 else iso


def _args(args: Any, limit: int = 90) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    parts = []
    for key, value in args.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        text = text.replace("\n", "⏎")
        parts.append(f"{key}={text[:60]}" + ("…" if len(text) > 60 else ""))
    line = " ".join(parts)
    return line if len(line) <= limit else line[:limit] + "…"


def _llm_line(node: TraceNode) -> str:
    a = node.attrs
    attempts = a.get("attempts") or []
    # a call that never answered has no model or tokens of its own: its attempts tell
    tried = list(dict.fromkeys(str(x.get("model", "")).split("/", 1)[-1] for x in attempts))
    model = a.get("model") or ", ".join(m for m in tried if m) or "?"
    out = a.get("output_tokens") or sum(int(x.get("output_tokens") or 0) for x in attempts)
    tokens_in = a.get("input_tokens") or max(
        (int(x.get("input_tokens") or 0) for x in attempts), default=0
    )
    cost = a.get("cost_usd") or sum(float(x.get("cost_usd") or 0) for x in attempts)
    served = f" ({a['served_by']})" if a.get("served_by") else ""
    responded = f" → {a['responded']}" if a.get("responded") else ""
    cached = f" (cache {_k(a['cached_tokens'])})" if a.get("cached_tokens") else ""
    n_cuts = len([x for x in attempts if x.get("outcome") == "cut"]) or a.get("cuts")
    cuts = f" · [yellow]{n_cuts} corte(s)[/yellow]" if n_cuts else ""
    failed = len([x for x in attempts if x.get("outcome") not in ("ok", "cut")])
    fell = f" · [yellow]{failed} falha(s) antes[/yellow]" if failed else ""
    finish = a.get("finish_reason") or node.span.get("status")
    finish = f"[red]{finish}[/red]" if finish in ("length", "max_tokens", "error") else finish
    return (
        f"[cyan]{escape(str(a.get('agent') or node.span.get('name')))}[/cyan] · "
        f"{escape(str(model))}{escape(served)}{escape(responded)} · in {_k(tokens_in)}"
        f"{cached} · out {_k(out)} · {finish} · {_dur(node.span.get('ms'))} · "
        f"US$ {float(cost):.4f}{cuts}{fell}"
    )


def _tool_line(node: TraceNode) -> str:
    a = node.attrs
    flags = []
    if a.get("repeat"):
        flags.append("[yellow]repetida[/yellow]")
    if a.get("ok") is False:
        flags.append(f"[red]erro[/red] {escape(str(a.get('error') or ''))[:80]}")
    if a.get("note"):
        flags.append("[magenta]aviso do LoopGuard[/magenta]")
    return (
        f"[green]{escape(node.span.get('name', ''))}[/green] {escape(_args(a.get('args')))} → "
        f"{_k(a.get('chars'))} chars · {_dur(node.span.get('ms'))} " + " ".join(flags)
    ).rstrip()


def _label(node: TraceNode) -> str:
    s, a = node.span, node.attrs
    kind = node.kind
    status = "" if s.get("status") == "ok" else f" [red]{s.get('status')}[/red]"
    ident = f"[dim]{str(s.get('id', ''))[:8]}[/dim]"
    if kind == "node":
        nxt = f" → {a['next']}" if a.get("next") else ""
        return (
            f"[bold]{s.get('name')}[/bold] {_clock(s.get('start', ''))} · {_dur(s.get('ms'))} · "
            f"{a.get('tier', '')}{nxt}{status} {ident}"
        )
    if kind == "task":
        origin = escape("[" + str(a.get("origin", "?")) + "]")
        return (
            f"[bold magenta]{s.get('name')}[/bold magenta] {origin} "
            f"{_dur(s.get('ms'))} · {escape(str(a.get('text', ''))[:70])}{status} {ident}"
        )
    if kind == "round":
        label = f" {a['label']}" if a.get("label") and a.get("label") != "main" else ""
        return f"#{s.get('name')}{label}{status}"
    if kind == "llm":
        return f"{_llm_line(node)}{status} {ident}"
    if kind == "tool":
        return f"{_tool_line(node)}{status} {ident}"
    return f"{kind} {s.get('name')}{status} {ident}"


def _print(node: TraceNode, depth: int = 0) -> None:
    if node.kind == "round" and len(node.children) <= 2:
        # a round is its model call plus the tools it asked for: one header line is enough
        console.print("  " * depth + _label(node))
        for child in node.children:
            console.print("  " * (depth + 1) + _label(child))
            for grand in child.children:
                _print(grand, depth + 2)
        return
    console.print("  " * depth + _label(node))
    for child in node.children:
        _print(child, depth + 1)


def _summary(trace: Trace, story: str) -> None:
    calls = [s for s in trace.spans if s.get("kind") == "llm"]
    tools = [s for s in trace.spans if s.get("kind") == "tool"]
    cost = sum(float((s.get("attrs") or {}).get("cost_usd") or 0) for s in calls)
    cuts = sum(int((s.get("attrs") or {}).get("cuts") or 0) for s in calls)
    model_ms = sum(int(s.get("ms") or 0) for s in calls)
    tool_ms = sum(int(s.get("ms") or 0) for s in tools)
    repeats = sum(1 for s in tools if (s.get("attrs") or {}).get("repeat"))
    starts = [s.get("start", "") for s in trace.spans]
    ends = [s.get("end", "") for s in trace.spans]
    console.print(
        f"[bold]{story}[/bold] · {len(calls)} chamadas ao modelo ({cuts} cortes) · "
        f"{len(tools)} de ferramenta ({repeats} repetidas) · US$ {cost:.4f} · "
        f"modelo {_dur(model_ms)} · ferramentas {_dur(tool_ms)} · "
        f"{_clock(min(starts, default=''))} → {_clock(max(ends, default=''))}"
    )


def _show_span(trace: Trace, span: dict[str, Any], as_json: bool) -> None:
    if as_json:
        console.print_json(json.dumps(span, ensure_ascii=False))
        return
    attrs = dict(span.get("attrs") or {})
    hashes = attrs.pop("messages", []) or []
    response = attrs.pop("response", None)
    result = attrs.pop("result", None)
    console.print(
        f"[bold]{span.get('kind')} {escape(str(span.get('name')))}[/bold] "
        f"{span.get('id')} · {span.get('start')} · {_dur(span.get('ms'))} · {span.get('status')}"
    )
    if span.get("error"):
        console.print(f"[red]{escape(str(span['error']))}[/red]")
    console.print_json(json.dumps(attrs, ensure_ascii=False, default=str))
    for title, refs in (("mensagens", hashes), ("resposta", [response]), ("resultado", [result])):
        refs = [h for h in refs if h]
        if not refs:
            continue
        console.rule(title)
        for h in refs:
            msg = trace.messages.get(h)
            if msg is None:
                console.print(f"[dim]{h}: não está no arquivo[/dim]")
                continue
            head = msg.get("role", "?") + (f" ({msg['name']})" if msg.get("name") else "")
            console.print(f"[bold cyan]{head}[/bold cyan] [dim]{h}[/dim]")
            if msg.get("content"):
                console.print(escape(str(msg["content"])))
            for call in msg.get("tool_calls") or []:
                console.print(
                    f"[green]→ {escape(call.get('name', ''))}[/green] "
                    + escape(json.dumps(call.get("arguments"), ensure_ascii=False)[:2000])
                )


def _stats(store: Store, slug: str, story: str | None, since: str | None) -> None:
    rows = store.usage_stats(slug, story_id=story, since_iso=since)
    if not rows:
        console.print("Nenhuma chamada registrada nesse recorte.")
        return
    table = Table(title="Tempo e custo por papel e modelo" + (f" · {story}" if story else ""))
    # "cobrado": share of the calls whose cost came from the provider's bill, not the price table
    for col in (
        "papel",
        "modelo",
        "n",
        "US$",
        "US$/n",
        "médio",
        "p90",
        "total",
        "cortes",
        "in",
        "out",
        "cobrado",
    ):
        table.add_column(
            col, justify="left" if col in ("papel", "modelo") else "right", overflow="fold"
        )
    for r in rows:
        calls = int(r["calls"] or 0)
        table.add_row(
            str(r["role"]),
            str(r["model"]),
            str(calls),
            f"{float(r['cost_usd'] or 0):.4f}",
            f"{float(r['cost_usd'] or 0) / max(calls, 1):.5f}",
            _dur(r["avg_ms"]),
            _dur(r["p90_ms"]),
            _dur(r["total_ms"]),
            str(r["cuts"] or 0),
            _k(r["input_tokens"]),
            _k(r["output_tokens"]),
            f"{int(r['reported'] or 0) * 100 // max(calls, 1)}%",
        )
    console.print(table)


def _send_otlp(f: Any, path: Path, endpoint: str) -> None:
    """Replay a story's trace file to an OTLP viewer (Phoenix), with the factory's redaction."""
    from loompa.config.secrets import Secrets, redact_secrets
    from loompa.trace_export import OtlpExporter, OtlpUnavailable

    cfg = f.config.trace.otlp
    if endpoint != "config":
        cfg = cfg.model_copy(update={"endpoint": endpoint})
    if not cfg.endpoint:
        console.print("[red]Nenhum endpoint: passe a URL ou configure trace.otlp.endpoint.[/red]")
        raise typer.Exit(code=1)
    secrets = Secrets.load(f.root)
    values = secrets.values_for_redaction()
    try:
        exporter = OtlpExporter.from_config(
            cfg, factory=f.slug, secrets=secrets, redact=lambda t: redact_secrets(t, values)
        )
    except OtlpUnavailable as exc:
        console.print(f"[red]Exportação não instalada:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    assert exporter is not None
    sent = exporter.export_file(path)
    exporter.close()
    console.print(f"{sent} spans de {path.stem} enviados para {cfg.endpoint}.")


@app.command()
def trace(
    story: str | None = typer.Argument(None, help="História (ex.: S-031)."),
    task: str | None = typer.Option(None, "--task", "-t", help="Só esta tarefa (ex.: T5)."),
    span: str | None = typer.Option(
        None, "--span", help="Uma chamada inteira: mensagens enviadas e resposta (id ou prefixo)."
    ),
    stats: bool = typer.Option(
        False, "--stats", help="Tempo e custo por papel e modelo (compara modelos no mesmo papel)."
    ),
    since: str | None = typer.Option(None, "--since", help="Com --stats: desde (ISO, UTC)."),
    as_json: bool = typer.Option(False, "--json", help="Saída em JSON."),
    otlp: str | None = typer.Option(
        None,
        "--otlp",
        help="Envia o rastro desta história por OTLP (ex.: http://localhost:6006/v1/traces); "
        "'config' usa o trace.otlp da fábrica.",
    ),
    factory: str | None = typer.Option(None, "--factory", "-f"),
) -> None:
    """Rastro de uma história: rodadas, modelo, tokens, motivo do fim e ferramentas.

    Ferramenta de desenvolvimento do Loompa; o Founder não precisa dela."""
    f = resolve_factory(factory)
    if stats:
        store = Store(f.paths.state_db)
        _stats(store, f.slug, story, since)
        store.close()
        return
    if not story:
        files = sorted(f.paths.traces.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not files:
            console.print("Nenhum rastro gravado ainda.")
            raise typer.Exit()
        console.print("Rastros: " + ", ".join(p.stem for p in files[-30:]))
        return
    path = f.paths.traces / f"{story}.jsonl"
    if not path.is_file():
        console.print(f"[red]Sem rastro para {story}[/red] ({path})")
        raise typer.Exit(code=1)
    if otlp:
        _send_otlp(f, path, otlp)
        return
    data = read_trace(path)
    if span:
        found = data.find(span)
        if found is None:
            console.print(f"[red]Nenhum span começa com {span}[/red]")
            raise typer.Exit(code=1)
        _show_span(data, found, as_json)
        return
    roots = data.tree()
    if task:
        name = task.upper() if task.upper().startswith("T") else f"T{task}"
        roots = [n for r in roots for n in r.walk() if n.kind == "task" and n.span["name"] == name]
        if not roots:
            console.print(f"[red]{story} não tem a tarefa {name} no rastro[/red]")
            raise typer.Exit(code=1)
    if as_json:
        spans = [n.span for r in roots for n in r.walk()]
        console.print_json(json.dumps(spans, ensure_ascii=False, default=str))
        return
    _summary(data, story)
    for root in roots:
        _print(root)
