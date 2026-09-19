"""`loompa providers`: keys, presets and connection tests for one factory (pt-BR, no secrets)."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from loompa.cli.main import app, resolve_factory
from loompa.config import MODEL_PRESETS, Secrets, apply_preset
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


def setup_providers_interactive(
    f: Factory, *, preset: str | None, scope: str, out: Console, test: bool = True
) -> None:
    """The "Provedores e modelos" onboarding step, shared with `loompa init`."""
    if preset is None:
        out.print("\n[bold]Provedores e modelos[/bold]")
        for key, p in MODEL_PRESETS.items():
            out.print(f"  [cyan]{key:10}[/cyan] {p.label} — {p.description}")
        out.print("  [cyan]{:10}[/cyan] manter a configuração atual".format("pular"))
        preset = typer.prompt("Preset", default=next(iter(MODEL_PRESETS))).strip().lower()
    if preset in MODEL_PRESETS:
        chosen = apply_preset(f.config, preset)
        f.save()
        out.print(f"[green]✔[/green] preset [bold]{chosen.label}[/bold] aplicado")
        wanted = [*chosen.providers, *chosen.optional_providers]
    else:
        wanted = sorted({c.provider for cs in f.config.models.tiers.values() for c in cs})
    secrets = _secrets(f)
    touched: list[str] = []
    for name in wanted:
        cfg = f.config.providers.get(name)
        if cfg is None or not cfg.api_key_env:
            continue
        label = cfg.label or name
        if secrets.get(cfg.api_key_env):
            out.print(f"  {label}: chave já {secrets.status(cfg.api_key_env)['label']}")
            touched.append(name)
            continue
        hint = f" (crie em {cfg.console_url})" if cfg.console_url else ""
        out.print(f"  {label}{hint}")
        value = _prompt_key(label, cfg.api_key_env)
        if value:
            store_key(f.root, cfg.api_key_env, value, scope=scope)
            touched.append(name)
    t = f.config.tools.tavily
    if t.enabled and t.api_key_env and not secrets.get(t.api_key_env):
        out.print(f"  Busca web do Analyst (Tavily, opcional; crie em {t.console_url})")
        value = _prompt_key("Tavily", t.api_key_env)
        if value:
            store_key(f.root, t.api_key_env, value, scope=scope)
            touched.append("tavily")
    if test and touched:
        out.print("Testando conexões…")
        print_probe(asyncio.run(_probe_all(f, touched)), out)
    if not touched:
        out.print("Nenhuma chave configurada: a fábrica funciona em modo simulação (--dry-run).")


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
