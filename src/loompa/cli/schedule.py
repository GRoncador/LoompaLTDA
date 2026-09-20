"""`loompa schedule`: the cron / launchd recipe for the nightly cycle and the monthly model refresh."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console

from loompa.cli.main import app, resolve_factory
from loompa.config.secrets import hub_home
from loompa.scheduling import (
    Job,
    ScheduleError,
    cron_block,
    jobs,
    label,
    launchd_plist,
    loompa_bin,
)

console = Console(soft_wrap=True)


@app.command()
def schedule(
    factory: str | None = typer.Option(None, "--factory", "-f"),
    kind: str = typer.Option(
        "auto", "--for", help="launchd (macOS) | cron (Linux) | auto: o do seu sistema."
    ),
    run_at: str = typer.Option("02:00", "--run-at", help="Ciclo noturno (`loompa run`), HH:MM."),
    sync_day: int = typer.Option(
        1, "--sync-day", help="Dia do mês da atualização dos modelos (1-28)."
    ),
    sync_at: str = typer.Option("09:00", "--sync-at", help="Horário da atualização dos modelos."),
    write: Path | None = typer.Option(
        None, "--write", help="launchd: grava os .plist nesta pasta (ex.: ~/Library/LaunchAgents)."
    ),
) -> None:
    """Mostra como rodar o ciclo noturno e a atualização mensal de modelos sozinhos.

    Só imprime (ou grava os arquivos que você pedir); não liga nada. As chaves não entram no
    agendamento: o Loompa as lê dos próprios arquivos de segredos.
    """
    f = resolve_factory(factory)
    kind = ("launchd" if sys.platform == "darwin" else "cron") if kind == "auto" else kind
    if kind not in ("launchd", "cron"):
        console.print("[red]✘[/red] --for deve ser launchd, cron ou auto.")
        raise typer.Exit(code=1)
    try:
        todo = jobs(f.slug, run_at=run_at, sync_day=sync_day, sync_at=sync_at)
    except ScheduleError as exc:
        console.print(f"[red]✘[/red] {exc}")
        raise typer.Exit(code=1) from exc
    binary, log_dir = loompa_bin(), hub_home() / "logs"
    console.print(f"[bold]Agendamento para {f.config.factory.name}[/bold] ({kind})")
    for job in todo:
        console.print(f"  • {job.what}")
    console.print(f"Os registros ficam em {log_dir}.\n")
    if kind == "cron":
        _show_cron(f.slug, f.root, log_dir, binary, todo)
    else:
        _show_launchd(f.slug, f.root, log_dir, binary, todo, write)


def _show_cron(slug: str, root: Path, log_dir: Path, binary: str, todo: list[Job]) -> None:
    console.print(
        f"Crie a pasta dos registros e abra o crontab:  mkdir -p {log_dir} && crontab -e\n"
    )
    console.print(cron_block(slug, root, log_dir, binary, todo), markup=False, highlight=False)
    console.print(
        "\nCole as linhas acima. No cron nada impede dois ciclos ao mesmo tempo: "
        "se o seu costuma passar de um dia, use o launchd (no macOS) ou espace os horários."
    )


def _show_launchd(
    slug: str, root: Path, log_dir: Path, binary: str, todo: list[Job], write: Path | None
) -> None:
    if write is None:
        for job in todo:
            console.print(f"[dim]# {label(slug, job)}.plist[/dim]")
            console.print(
                launchd_plist(slug, root, log_dir, binary, job), markup=False, highlight=False
            )
        console.print(
            "Para gravar os arquivos: [bold]loompa schedule --write ~/Library/LaunchAgents[/bold]"
        )
        return
    write = write.expanduser()
    write.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    for job in todo:
        path = write / f"{label(slug, job)}.plist"
        path.write_text(launchd_plist(slug, root, log_dir, binary, job), encoding="utf-8")
        console.print(f"[green]✔[/green] {path}")
    console.print("\nPara ligar (uma vez por arquivo):")
    for job in todo:
        console.print(f"  launchctl bootstrap gui/$(id -u) {write / (label(slug, job) + '.plist')}")
    console.print("Para desligar:  launchctl bootout gui/$(id -u)/<Label>")
