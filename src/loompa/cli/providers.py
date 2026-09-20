"""`loompa providers`: keys and connection tests for one factory (pt-BR, no secrets)."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from loompa.cli.main import app, resolve_factory
from loompa.config import Secrets
from loompa.config.services import PROVIDER_PURPOSE
from loompa.config.settings import PROVIDER_ORDER, describe_settings, store_key
from loompa.factory import Factory
from loompa.llm import ProbeResult, probe_provider, probe_tavily

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
    table.add_column("para que serve")
    for p in info["providers"]:
        table.add_row(
            p["label"] + (" [dim](recomendado)[/dim]" if p["recommended"] else ""),
            p["key"]["label"],
            p["key"]["source"] or "-",
            PROVIDER_PURPOSE.get(p["name"], "modelos de IA"),
        )
    t = info["tools"]["tavily"]
    table.add_row(
        "Tavily (busca web)", t["key"]["label"], t["key"]["source"] or "-", "pesquisa, não modelos"
    )
    out.print(table)


def _prompt_key(label: str, env_name: str) -> str | None:
    value = typer.prompt(
        f"Chave de {label} ({env_name}) — Enter para pular",
        default="",
        hide_input=True,
        show_default=False,
    )
    return value.strip() or None


async def _probe_all(f: Factory, names: list[str]) -> list[ProbeResult]:
    secrets = _secrets(f)
    out: list[ProbeResult] = []
    for name in names:
        if name == "tavily":
            r = await probe_tavily(f.config.tools.tavily, secrets=secrets)
        else:
            r = await probe_provider(f.config, name, secrets=secrets)
        out.append(r)
    return out


def _line(r: ProbeResult) -> str:
    return r.detail + (f" · {r.model}" if r.model else "")


def print_probe(results: list[ProbeResult], out: Console) -> None:
    for r in results:
        mark = "[green]✔[/green]" if r.ok else "[red]✘[/red]"
        out.print(f"  {mark} {r.name}: {_line(r)}")
        if r.reason and not r.ok:  # the technical cause, for a founder reporting a failure
            out.print(f"      [dim]detalhe técnico: {r.reason}[/dim]")


def probe_one(f: Factory, name: str) -> tuple[bool, str]:
    """One connection test: (worked, pt-BR detail)."""
    r = asyncio.run(_probe_all(f, [name]))[0]
    return r.ok, _line(r)


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


def ordered_providers(f: Factory) -> list[str]:
    """Providers that need a key, in the order the settings screen lists them."""
    named = [n for n in PROVIDER_ORDER if n in f.config.providers]
    named += sorted(set(f.config.providers) - set(named))
    return [n for n in named if (cfg := f.config.providers.get(n)) and cfg.api_key_env]


def setup_providers_interactive(f: Factory, *, scope: str, out: Console, test: bool = True) -> None:
    """The keys. Which models a factory uses is the founder's to choose afterwards, so this step
    only opens doors: OpenRouter first, because one key there reaches every model, and the rest
    only for a founder who says they want them — asking for six keys in a row helps nobody."""
    names = ordered_providers(f)
    if not names:
        return
    working: list[str] = []
    first = "openrouter" if "openrouter" in names else names[0]
    out.print(
        "\n[bold]Modelos de IA[/bold] — uma chave da OpenRouter já alcança todos os modelos. "
        "Enter pula; dá para adicionar ou trocar depois em Configurações."
    )
    cfg = f.config.providers[first]
    if collect_key(
        f,
        name=first,
        label=(cfg.label or first) + " (recomendado)",
        purpose=PROVIDER_PURPOSE.get(first, "modelos de IA"),
        env=cfg.api_key_env,
        url=cfg.console_url,
        scope=scope,
        out=out,
        optional=False,
        test=test,
    ):
        working.append(first)
    rest = [n for n in names if n != first]
    labels = ", ".join(f.config.providers[n].label or n for n in rest)
    if rest and typer.confirm(f"\nConfigurar mais algum provedor ({labels})?", default=False):
        for name in rest:
            cfg = f.config.providers[name]
            if collect_key(
                f,
                name=name,
                label=cfg.label or name,
                purpose=PROVIDER_PURPOSE.get(name, "modelos de IA"),
                env=cfg.api_key_env,
                url=cfg.console_url,
                scope=scope,
                out=out,
                optional=True,
                test=test,
            ):
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
    if not all(r.ok for r in results):
        raise typer.Exit(code=1)


@providers_app.command("keys")
def providers_keys(
    factory: str | None = typer.Option(None, "--factory", "-f"),
    scope: str = typer.Option("hub", "--scope"),
) -> None:
    """Pergunta as chaves dos provedores, uma por uma, e testa cada uma ao guardar."""
    f = resolve_factory(factory)
    setup_providers_interactive(f, scope=scope, out=console)
    print_providers(f, console)
