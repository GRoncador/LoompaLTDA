"""`loompa prompts`: the prompt bench (plan 14). Real cases from the traces, two variants, numbers.

    loompa prompts harvest                      # cases from every factory on this machine
    loompa prompts list                         # how many cases each prompt has
    loompa prompts compare deployer.SUMMARY_SYSTEM --candidate novo.txt
    loompa prompts compare SPRINT_REPORT --model-a openrouter:qwen/qwen3.8-27b:free \\
        --model-b xiaomi:mimo-v2.6-flash
    loompa prompts show                         # the last report again

A candidate is a text file with the whole prompt as the code holds it (`{language}` and doubled
braces included). The bench is for the people improving the factory: its output names prompts and
models as they are in the code.
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from loompa.cli.main import app, resolve_factory

console = Console()
prompts_app = typer.Typer(help="Bancada de prompts: casos reais, duas variantes, medidas.")
app.add_typer(prompts_app, name="prompts")


@prompts_app.command("harvest")
def harvest(
    factory: list[str] = typer.Option(
        None, "--factory", "-f", help="Fábricas (slug); padrão: todas as registradas."
    ),
    per_prompt: int = typer.Option(40, help="Casos por prompt e forma de chamada."),
) -> None:
    """Separa os casos reais dos rastros das fábricas (14.1)."""
    from loompa.bench import cases
    from loompa.config import ConfigStore
    from loompa.factory import Factory

    hub = ConfigStore()
    reg = hub.load()
    slugs = factory or [f.slug for f in reg.factories]
    for slug in slugs:
        root = hub.resolve(slug)
        if root is None or not (root / ".loompa" / "config.yaml").is_file():
            if factory:
                console.print(f"[yellow]{slug}: fábrica não encontrada[/yellow]")
            continue
        f = Factory.open(root)
        found = list(cases.harvest_factory(root, slug, f.config.factory.language))
        picked = cases.sample(found, per_prompt)
        counts = cases.save(picked, slug)
        console.print(
            f"[bold]{slug}[/bold]: {len(found)} chamadas reconhecidas, {len(picked)} casos em "
            f"{len(counts)} prompts → {cases.cases_dir() / slug}"
        )


@prompts_app.command("list")
def list_cases() -> None:
    """Casos por prompt (e forma de chamada) já separados."""
    from collections import Counter

    from loompa.bench import cases, registry

    loaded = cases.load()
    counts = Counter((c.prompt, c.mode) for c in loaded)
    table = Table(title="Casos da bancada")
    for col in ("prompt", "forma", "casos", "fábricas"):
        table.add_column(col)
    for key in registry.prompts():
        for mode in ("one_shot", "first", "final"):
            n = counts.get((key, mode), 0)
            if n:
                facs = sorted({c.factory for c in loaded if c.prompt == key and c.mode == mode})
                table.add_row(key, mode, str(n), ", ".join(facs))
    console.print(table)


@prompts_app.command("compare")
def compare_cmd(
    prompt: str = typer.Argument(
        ..., help="Prompt, ex.: deployer.SUMMARY_SYSTEM (ou parte do nome)."
    ),
    candidate: Path | None = typer.Option(None, "--candidate", "-c", help="Texto do prompt B."),
    candidate_a: Path | None = typer.Option(
        None, "--candidate-a", help="Texto do prompt A (padrão: o do código)."
    ),
    model_a: str | None = typer.Option(None, help="provedor:modelo da variante A."),
    model_b: str | None = typer.Option(None, help="provedor:modelo da variante B."),
    mode: str | None = typer.Option(None, help="one_shot | first | final (padrão: o mais comum)."),
    limit: int = typer.Option(20, help="Casos sorteados."),
    seed: int = typer.Option(14, help="Semente do sorteio (mesma semente, mesmos casos)."),
    judge: bool = typer.Option(True, "--judge/--no-judge", help="Juiz em pares, às cegas."),
    judge_model: str | None = typer.Option(None, help="provedor:modelo do juiz."),
    factory_slug: str | None = typer.Option(None, "--factory", help="Roteamento desta fábrica."),
    only: list[str] = typer.Option(None, "--from", help="Casos só destas fábricas."),
    as_json: bool = typer.Option(False, "--json", help="Imprime o relatório em JSON."),
) -> None:
    """Roda o prompt atual e o candidato (ou dois modelos) sobre os mesmos casos (14.2)."""
    from loompa.bench import cases as cases_mod
    from loompa.bench import registry
    from loompa.bench.compare import Variant, compare, save_report
    from loompa.config.secrets import Secrets

    try:
        p = registry.get(prompt)
    except KeyError as exc:
        console.print(f"[red]Prompt desconhecido:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    if candidate is None and candidate_a is None and not model_a and not model_b:
        console.print("[red]Nada a comparar:[/red] passe --candidate, --model-a ou --model-b.")
        raise typer.Exit(code=2)
    found = cases_mod.load(p.key, factories=only or None)
    if not found:
        console.print(
            f"[red]Sem casos para {p.key}.[/red] Rode [bold]loompa prompts harvest[/bold]."
        )
        raise typer.Exit(code=1)
    if mode is None:
        modes = [c.mode for c in found]
        mode = max(set(modes), key=modes.count)
    pool = [c for c in found if c.mode == mode]
    random.Random(seed).shuffle(pool)
    picked = pool[:limit]
    f = resolve_factory(factory_slug)
    a = Variant("A", candidate_a.read_text(encoding="utf-8") if candidate_a else None, model_a)
    if candidate_a:
        a.label = f"candidato {candidate_a.name}"
    b = Variant("B", candidate.read_text(encoding="utf-8") if candidate else None, model_b)
    if candidate:
        b.label = f"candidato {candidate.name}"
    console.print(
        f"[bold]{p.key}[/bold] · {len(picked)} casos ({mode}) · A = {a.describe()} · "
        f"B = {b.describe()}"
    )

    def progress(done: int, total: int) -> None:
        if done % 5 == 0 or done == total:
            console.print(f"  {done}/{total}", highlight=False)

    report = asyncio.run(
        compare(
            f.config,
            Secrets.load(f.root),
            p,
            picked,
            a,
            b,
            judge_model=judge_model,
            use_judge=judge,
            on_progress=progress,
        )
    )
    path = save_report(report)
    if as_json:
        console.print_json(json.dumps(report.as_dict()["summary"]))
    else:
        print_summary(report.as_dict())
    console.print(f"Relatório completo: {path}")


@prompts_app.command("show")
def show(report: Path | None = typer.Argument(None, help="Relatório (padrão: o último).")) -> None:
    """Mostra um relatório da bancada."""
    from loompa.bench.cases import bench_dir

    if report is None:
        runs = sorted((bench_dir() / "runs").glob("*.json"))
        if not runs:
            console.print("Nenhum relatório ainda.")
            raise typer.Exit(code=1)
        report = runs[-1]
    print_summary(json.loads(report.read_text(encoding="utf-8")))
    console.print(f"[dim]{report}[/dim]")


def print_summary(data: dict) -> None:
    summary = data["summary"]
    table = Table(title=f"{data['prompt']} · juiz {data.get('judge') or '—'}")
    table.add_column("medida")
    for name, desc in data["variants"].items():
        table.add_column(f"{name}: {desc}")
    rows = [
        ("casos (erros)", lambda s: f"{s['n']} ({s['errors']})"),
        ("JSON válido %", lambda s: s["json_ok"]),
        ("contrato ok %", lambda s: s["contract_ok"]),
        ("texto ao Founder ok %", lambda s: s["founder_ok"]),
        ("decisão = a gravada %", lambda s: s["agrees_recorded"]),
        ("juiz: vitórias", lambda s: s["judge_wins"]),
        ("juiz: nota média 1-5", lambda s: s["judge_score"]),
        ("custo US$", lambda s: s["cost_usd"]),
        ("tokens entrada/saída", lambda s: f"{s['avg_in']}/{s['avg_out']}"),
        ("segundos por caso", lambda s: s["avg_s"]),
        ("primeira ferramenta", lambda s: s["first_tools"] or None),
    ]
    for label, get in rows:
        values = [get(summary[name]) for name in data["variants"]]
        if all(v is None for v in values):
            continue
        table.add_row(label, *["—" if v is None else str(v) for v in values])
    console.print(table)
    first = summary[next(iter(summary))] if summary else {}
    ties, flips = first.get("judge_ties", 0), first.get("judge_flips", 0)
    console.print(
        f"Empates do juiz: {ties} (dos quais {flips} mudaram com a ordem) · "
        f"custo do juiz US$ {data.get('judge_cost_usd', 0)}",
        highlight=False,
    )


# ----------------------------------------------------------------------------- live A/B (ADR-0035)

ab_app = typer.Typer(
    help="A/B ao vivo de um prompt: uma variante para parte das histórias (ADR-0035).",
    invoke_without_command=True,
)
prompts_app.add_typer(ab_app, name="ab")

MIN_STORIES = 10  # per arm, before a difference is worth reading


@ab_app.callback()
def ab_status(
    ctx: typer.Context,
    factory_slug: str | None = typer.Option(None, "--factory", help="A fábrica (padrão: a ativa)."),
    days: int = typer.Option(180, help="Janela das histórias sorteadas."),
) -> None:
    """Os experimentos ligados, as variantes que existem e o resultado de cada braço."""
    if ctx.invoked_subcommand:
        return
    from loompa import prompt_ab
    from loompa.store import Store

    f = resolve_factory(factory_slug)
    on = f.config.schedule.prompt_ab
    console.print(f"[bold]{f.config.factory.name}[/bold] · experimentos ligados:")
    for e in on or []:
        console.print(f"  {e.prompt} → {e.variant} em {100 * e.share:.0f}% das histórias")
    if not on:
        console.print("  nenhum")
    console.print("Variantes disponíveis:")
    for (prompt, name), v in prompt_ab.VARIANTS.items():
        console.print(f"  {prompt} {name}: {v.title}", highlight=False)
    results = prompt_ab.report(Store(f.paths.state_db), f.config.factory.slug, days=days)
    if not results:
        console.print("Nenhuma história sorteada ainda.")
        return
    for r in results:
        _print_ab(r)


def _print_ab(r: dict) -> None:
    from loompa.prompt_ab import CONTROL, VARIANT

    t = Table(title=f"{r['prompt']} · {r['variant']} — {r['title']}")
    t.add_column("medida")
    t.add_column("variante")
    t.add_column("controle")
    v, c = r["arms"][VARIANT], r["arms"][CONTROL]
    rows = [
        ("histórias (encerradas)", lambda a: f"{a['stories']} ({a['settled']})"),
        ("entregues", lambda a: a["delivered"]),
        ("com retrabalho", lambda a: a["with_rework"]),
        ("retrabalho por história", lambda a: a["rework"]),
        ("reprovações do Inspector", lambda a: a["fails"]),
        ("achados TEST", lambda a: a["test_findings"]),
        ("bloqueios", lambda a: a["blocks"]),
        ("tarefas que não terminaram", lambda a: a["unfinished_tasks"]),
        ("tarefas por história", lambda a: a["tasks"]),
        ("US$ por história", lambda a: a["cost_usd"]),
    ]
    for label, get in rows:
        t.add_row(label, str(get(v)), str(get(c)))
    console.print(t)
    few = min(v["settled"], c["settled"]) < MIN_STORIES
    console.print(
        f"Histórias com retrabalho, Fisher p {r['rework_p']:.2f}. Observar: {r['watch']}."
        + (
            f" Com menos de {MIN_STORIES} histórias encerradas por braço, ainda não diz nada."
            if few
            else ""
        ),
        highlight=False,
    )


@ab_app.command("on")
def ab_on(
    prompt: str = typer.Argument(..., help="worker.SYSTEM ou architect.SYSTEM"),
    variant: str = typer.Argument(..., help="O nome da variante (veja `loompa prompts ab`)."),
    share: float = typer.Option(0.5, min=0.0, max=1.0, help="Parte das histórias na variante."),
    factory_slug: str | None = typer.Option(None, "--factory", help="A fábrica (padrão: a ativa)."),
) -> None:
    """Liga um experimento: a variante roda em `share` das histórias novas desse prompt."""
    from loompa import prompt_ab
    from loompa.bench import registry
    from loompa.config.schema import PromptAB
    from loompa.config.store import save_config

    if (prompt, variant) not in prompt_ab.VARIANTS:
        console.print(f"[red]Variante desconhecida:[/red] {prompt} {variant}")
        raise typer.Exit(code=2)
    try:
        prompt_ab.variant_text(registry.get(prompt).template, prompt, variant)
    except ValueError as exc:
        console.print(f"[red]A variante não cabe mais no prompt do código:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    f = resolve_factory(factory_slug)
    others = [e for e in f.config.schedule.prompt_ab if e.prompt != prompt]
    f.config.schedule.prompt_ab = [*others, PromptAB(prompt=prompt, variant=variant, share=share)]
    save_config(f.root, f.config)
    console.print(
        f"Ligado em {f.config.factory.name}: {prompt} → {variant} em {100 * share:.0f}% das "
        "histórias que começarem a partir de agora. Uma história em curso guarda o braço que tirou."
    )


@ab_app.command("off")
def ab_off(
    prompt: str = typer.Argument(..., help="worker.SYSTEM ou architect.SYSTEM"),
    factory_slug: str | None = typer.Option(None, "--factory", help="A fábrica (padrão: a ativa)."),
) -> None:
    """Desliga o experimento de um prompt; o resultado continua em `loompa prompts ab`."""
    from loompa.config.store import save_config

    f = resolve_factory(factory_slug)
    before = len(f.config.schedule.prompt_ab)
    f.config.schedule.prompt_ab = [e for e in f.config.schedule.prompt_ab if e.prompt != prompt]
    save_config(f.root, f.config)
    console.print(
        f"Desligado em {f.config.factory.name}: {prompt}."
        if len(f.config.schedule.prompt_ab) < before
        else f"Nenhum experimento ligado para {prompt}."
    )


@ab_app.command("show")
def ab_show(
    prompt: str = typer.Argument(...),
    variant: str = typer.Argument(...),
) -> None:
    """O diff da variante sobre o prompt do código."""
    import difflib

    from loompa import prompt_ab
    from loompa.bench import registry

    base = registry.get(prompt).template
    text = prompt_ab.variant_text(base, prompt, variant)
    for line in difflib.unified_diff(
        base.splitlines(), text.splitlines(), prompt, f"{prompt} · {variant}", lineterm=""
    ):
        console.print(line, highlight=False, markup=False)
