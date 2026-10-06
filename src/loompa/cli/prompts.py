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
    ties = summary[next(iter(summary))]["judge_ties"] if summary else 0
    console.print(
        f"Empates do juiz: {ties} · custo do juiz US$ {data.get('judge_cost_usd', 0)}",
        highlight=False,
    )
