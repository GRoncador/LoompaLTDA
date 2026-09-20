"""`loompa providers`: keys, presets and connection tests for one factory (pt-BR, no secrets)."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from loompa.cli.main import app, resolve_factory
from loompa.config import MODEL_PRESETS, Secrets, apply_preset
from loompa.config.services import PROVIDER_PURPOSE
from loompa.config.settings import describe_settings, store_key
from loompa.factory import Factory
from loompa.llm import probe_provider, probe_tavily

console = Console()
providers_app = typer.Typer(help="Provedores de IA, modelos por tier e chaves de API.")
app.add_typer(providers_app, name="providers")


def _secrets(f: Factory) -> Secrets:
    return Secrets.load(f.root)


def print_providers(f: Factory, out: Console) -> None:
    info = describe_settings(f.config, _secrets(f))
    table = Table(title=f"Provedores · {f.config.factory.name}", show_lines=False)
    table.add_column("provedor", style="cyan")
    table.add_column("chave")
    table.add_column("origem")
    table.add_column("modelos nos tiers")
    for p in info["providers"]:
        table.add_row(
            p["label"],
            p["key"]["label"],
            p["key"]["source"] or "-",
            ", ".join(p["used_by"]) or "-",
        )
    t = info["tools"]["tavily"]
    table.add_row("Tavily (busca web)", t["key"]["label"], t["key"]["source"] or "-", "Analyst")
    out.print(table)
    if info["preset"]:
        out.print(f"Preset atual: [bold]{info['preset']}[/bold]")


def _prompt_key(label: str, env_name: str) -> str | None:
    value = typer.prompt(
        f"Chave de {label} ({env_name}) — Enter para pular",
        default="",
        hide_input=True,
        show_default=False,
    )
    return value.strip() or None


async def _probe_all(f: Factory, names: list[str]) -> list[tuple[str, bool, str]]:
    secrets = _secrets(f)
    out: list[tuple[str, bool, str]] = []
    for name in names:
        if name == "tavily":
            r = await probe_tavily(f.config.tools.tavily, secrets=secrets)
        else:
            r = await probe_provider(f.config, name, secrets=secrets)
        out.append((name, r.ok, r.detail + (f" · {r.model}" if r.model else "")))
    return out


def print_probe(results: list[tuple[str, bool, str]], out: Console) -> None:
    for name, ok, detail in results:
        mark = "[green]✔[/green]" if ok else "[red]✘[/red]"
        out.print(f"  {mark} {name}: {detail}")


def probe_one(f: Factory, name: str) -> tuple[bool, str]:
    """One connection test: (worked, pt-BR detail)."""
    _, ok, detail = asyncio.run(_probe_all(f, [name]))[0]
    return ok, detail


def collect_key(
    f: Factory,
    *,
    name: str,
    label: str,
    purpose: str,
    env: str,
    url: str,
    scope: str,
    out: Console,
    optional: bool = False,
    test: bool = True,
) -> bool:
    """Ask for one key the way a founder can follow: what it is for, where to create it (and offer
    to open the page), a hidden prompt, and an immediate connection test. A key the provider
    rejects is wiped and asked for again (three tries); a failure that is not the key's fault
    (rate limit, network) keeps it. Returns whether a working key is in place."""
    out.print(f"\n[bold]{label}[/bold]{' (opcional)' if optional else ''} — {purpose}")
    if Secrets.load(f.root).get(env):
        out.print(
            f"  [green]✔[/green] chave já configurada ({Secrets.load(f.root).status(env)['source']})"
        )
        return True
    if url:
        out.print(f"  Crie a chave em {url}")
        if typer.confirm("  Abrir a página no navegador agora?", default=not optional):
            try:
                typer.launch(url)
            except Exception:  # noqa: BLE001 - no browser (ssh, container): the link is printed
                pass
    for _ in range(3):
        value = typer.prompt(
            "  Cole a chave aqui (não aparece na tela; Enter para pular)",
            default="",
            hide_input=True,
            show_default=False,
        ).strip()
        if not value:
            out.print("  pulado. Rode [bold]loompa setup[/bold] quando tiver a chave.")
            return False
        store_key(f.root, env, value, scope=scope)
        if not test:
            return True
        ok, detail = probe_one(f, name)
        if ok:
            out.print(f"  [green]✔[/green] {detail}")
            return True
        if "recusada" not in detail:  # rate limit, network: the key is not what is wrong
            out.print(f"  [yellow]![/yellow] chave guardada, mas o teste não passou: {detail}")
            return True
        store_key(f.root, env, None, scope=scope)
        out.print(f"  [red]✘[/red] {detail}. Confira se copiou a chave inteira e tente de novo.")
    out.print(
        "  Não consegui validar a chave. Rode [bold]loompa setup[/bold] para tentar outra vez."
    )
    return False


def choose_preset(out: Console) -> str | None:
    """Numbered menu of the model presets; None keeps the current configuration."""
    out.print("\n[bold]Modelos de IA[/bold] — escolha um conjunto pronto (dá para mudar depois):")
    keys = list(MODEL_PRESETS)
    for i, p in enumerate(MODEL_PRESETS.values(), 1):
        out.print(f"  [cyan]{i}[/cyan]) {p.label} — {p.description}")
    out.print("  [cyan]0[/cyan]) manter a configuração atual")
    while True:
        raw = typer.prompt("Número ou nome", default="1").strip().lower()
        if raw in ("0", "pular", "manter"):
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            return keys[int(raw) - 1]
        if raw in MODEL_PRESETS:
            return raw
        out.print(f"  [red]Não entendi “{raw}”.[/red] Digite um número de 0 a {len(keys)}.")


def setup_providers_interactive(
    f: Factory, *, preset: str | None, scope: str, out: Console, test: bool = True
) -> None:
    """Models and their keys, then the optional web search: `loompa providers preset` and the
    first two steps of `loompa setup`."""
    if preset is None:
        preset = choose_preset(out)
    wanted: list[str]
    if preset in MODEL_PRESETS:
        chosen = apply_preset(f.config, preset)
        f.save()
        out.print(f"[green]✔[/green] conjunto [bold]{chosen.label}[/bold] aplicado")
        wanted = [*chosen.providers, *chosen.optional_providers]
        required = set(chosen.providers)
    else:
        wanted = sorted({c.provider for cs in f.config.models.tiers.values() for c in cs})
        required = set(wanted)
    working: list[str] = []
    for name in wanted:
        cfg = f.config.providers.get(name)
        if cfg is None or not cfg.api_key_env:
            continue
        ok = collect_key(
            f,
            name=name,
            label=cfg.label or name,
            purpose=PROVIDER_PURPOSE.get(name, "modelos de IA"),
            env=cfg.api_key_env,
            url=cfg.console_url,
            scope=scope,
            out=out,
            optional=name not in required,
            test=test,
        )
        if ok:
            working.append(name)
    if not working:
        out.print(
            "\nNenhuma chave configurada: a fábrica funciona em modo simulação (--dry-run) "
            "até você rodar [bold]loompa setup[/bold]."
        )


def setup_web_search_interactive(
    f: Factory, *, scope: str, out: Console, test: bool = True
) -> bool:
    t = f.config.tools.tavily
    if not t.enabled or not t.api_key_env:
        return False
    return collect_key(
        f,
        name="tavily",
        label="Tavily (busca na web)",
        purpose="deixa o Analyst pesquisar na internet e citar as fontes",
        env=t.api_key_env,
        url=t.console_url,
        scope=scope,
        out=out,
        optional=True,
        test=test,
    )


@providers_app.command("list")
def providers_list(factory: str | None = typer.Option(None, "--factory", "-f")) -> None:
    """Mostra provedores, status das chaves (nunca o valor) e modelos por tier."""
    print_providers(resolve_factory(factory), console)


@providers_app.command("set-key")
def providers_set_key(
    name: str = typer.Argument(..., help="Nome do provedor (ex.: gemini) ou 'tavily'."),
    factory: str | None = typer.Option(None, "--factory", "-f"),
    scope: str = typer.Option("hub", "--scope", help="hub (todas as fábricas) | factory"),
    clear: bool = typer.Option(False, "--clear", help="Remove a chave em vez de gravar."),
    test: bool = typer.Option(True, "--test/--no-test", help="Testa a conexão após gravar."),
) -> None:
    """Grava a chave de um provedor em prompt oculto (~/.loompa/secrets.env ou .loompa/.env)."""
    f = resolve_factory(factory)
    if name == "tavily":
        env_name, label = f.config.tools.tavily.api_key_env, "Tavily"
    else:
        cfg = f.config.providers.get(name)
        if cfg is None:
            console.print(f"[red]Provedor desconhecido:[/red] {name}")
            raise typer.Exit(code=1)
        if not cfg.api_key_env:
            console.print(f"{name} não precisa de chave.")
            return
        env_name, label = cfg.api_key_env, cfg.label or name
    if clear:
        for s in ("hub", "factory"):
            store_key(f.root, env_name, None, scope=s)
        console.print(f"[green]✔[/green] chave de {label} removida")
        return
    value = _prompt_key(label, env_name)
    if not value:
        console.print("Nada gravado.")
        return
    path = store_key(f.root, env_name, value, scope=scope)
    console.print(f"[green]✔[/green] chave de {label} guardada em {path}")
    if test:
        print_probe(asyncio.run(_probe_all(f, [name])), console)


@providers_app.command("test")
def providers_test(
    name: str | None = typer.Argument(None, help="Provedor específico (padrão: todos com chave)."),
    factory: str | None = typer.Option(None, "--factory", "-f"),
) -> None:
    """Faz uma chamada mínima para confirmar que cada chave funciona."""
    f = resolve_factory(factory)
    secrets = _secrets(f)
    if name:
        names = [name]
    else:
        names = [
            n
            for n, p in f.config.providers.items()
            if (not p.api_key_env or secrets.get(p.api_key_env))
            and any(c.provider == n for cs in f.config.models.tiers.values() for c in cs)
        ]
        if secrets.get(f.config.tools.tavily.api_key_env):
            names.append("tavily")
    if not names:
        console.print("Nenhuma chave configurada para testar.")
        return
    results = asyncio.run(_probe_all(f, names))
    print_probe(results, console)
    if not all(ok for _, ok, _ in results):
        raise typer.Exit(code=1)


@providers_app.command("preset")
def providers_preset(
    name: str = typer.Argument(..., help="openrouter | gratuito | economico | maximo"),
    factory: str | None = typer.Option(None, "--factory", "-f"),
    keys: bool = typer.Option(True, "--keys/--no-keys", help="Pergunta as chaves faltantes."),
    scope: str = typer.Option("hub", "--scope"),
) -> None:
    """Aplica um preset de modelos (tiers) e, opcionalmente, pede as chaves que faltam."""
    f = resolve_factory(factory)
    if name not in MODEL_PRESETS:
        console.print(f"[red]Preset desconhecido:[/red] {name}. Opções: {', '.join(MODEL_PRESETS)}")
        raise typer.Exit(code=1)
    if keys:
        setup_providers_interactive(f, preset=name, scope=scope, out=console)
    else:
        apply_preset(f.config, name)
        f.save()
        console.print(f"[green]✔[/green] preset {name} aplicado")
    print_providers(f, console)
