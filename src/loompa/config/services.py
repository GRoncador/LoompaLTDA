"""Everything a factory needs configured, in one list: what it is for, whether it is ready, and
what to do when it is not.

`loompa setup` walks it as a wizard, `loompa doctor` prints it, and both read it the same way so
they never disagree. Detection is a pure function of the config, the secrets and two probes
(`which`, `gh_logged_in`) that tests replace; nothing here reads or returns a key value.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from loompa.config.schema import LoompaConfig
from loompa.config.secrets import Secrets

# What each provider is for, in the words a founder would use (pt-BR, shown as is).
PROVIDER_PURPOSE = {
    "openrouter": "uma chave para todos os modelos de IA, com teto de gastos no painel deles",
    "gemini": "modelos Gemini do Google (tem plano gratuito)",
    "deepseek": "modelos DeepSeek, baratos para o trabalho do dia a dia",
    "groq": "modelos abertos muito rápidos (tem plano gratuito)",
    "anthropic": "modelos Claude",
    "ollama": "modelos rodando na sua própria máquina",
}


@dataclass(frozen=True)
class Service:
    id: str
    label: str
    purpose: str  # pt-BR, one line
    need: str  # "required" | "optional"
    ok: bool
    detail: str  # pt-BR: the state, never a key value
    fix: str = ""  # pt-BR: what to do when it is not ok
    key_env: str = ""  # the variable a key is stored under (kind "key")
    console_url: str = ""  # where the key is created

    @property
    def blocking(self) -> bool:
        return self.need == "required" and not self.ok


def gh_logged_in(which: Callable[[str], str | None] | None = None) -> bool:
    """True when the GitHub CLI is installed and logged in (`gh auth status` exits 0)."""
    if not (which or shutil.which)("gh"):
        return False
    try:
        return (
            subprocess.run(["gh", "auth", "status"], capture_output=True, timeout=10).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


def _key_service(
    *, id: str, label: str, purpose: str, need: str, env: str, url: str, secrets: Secrets
) -> Service:
    status = secrets.status(env)
    ok = bool(status["configured"])
    return Service(
        id=id,
        label=label,
        purpose=purpose,
        need=need,
        ok=ok,
        detail=f"chave {status['label']}" if ok else "chave não configurada",
        fix="" if ok else (f"crie a chave em {url}" if url else "informe a chave"),
        key_env=env,
        console_url=url,
    )


def collect_services(
    config: LoompaConfig,
    secrets: Secrets,
    *,
    which: Callable[[str], str | None] | None = None,
    gh_ok: Callable[[], bool] | None = None,
) -> list[Service]:
    """The factory's services in the order the wizard asks for them."""
    which = which or shutil.which
    out: list[Service] = []
    seen: set[str] = set()
    for cands in config.models.tiers.values():
        for c in cands:
            name = c.provider
            provider = config.providers.get(name)
            if provider is None or name in seen:
                continue
            seen.add(name)
            label = provider.label or name
            purpose = PROVIDER_PURPOSE.get(name, "modelos de IA")
            if not provider.api_key_env:
                out.append(Service(name, label, purpose, "required", True, "não precisa de chave"))
                continue
            out.append(
                _key_service(
                    id=name,
                    label=label,
                    purpose=purpose,
                    need="required",
                    env=provider.api_key_env,
                    url=provider.console_url,
                    secrets=secrets,
                )
            )
    for name, server in config.tools.servers().items():
        if not server.enabled or not server.api_key_env:
            continue
        web = name == "tavily"
        out.append(
            _key_service(
                id=name,
                label="Tavily (busca na web)" if web else f"Ferramenta {name}",
                purpose="deixa o Analyst pesquisar na internet e citar fontes"
                if web
                else "ferramenta extra que alguns Loompas podem usar",
                need="optional",
                env=server.api_key_env,
                url=server.console_url,
                secrets=secrets,
            )
        )
    worker = config.worker
    opencode = which(worker.opencode_bin)
    wants_opencode = worker.backend == "opencode"
    out.append(
        Service(
            id="opencode",
            label="OpenCode",
            purpose="programador alternativo, no lugar do Worker embutido do Loompa",
            need="required" if wants_opencode else "optional",
            ok=bool(opencode),
            detail=("instalado" if opencode else "não instalado")
            + (" · em uso" if wants_opencode else " · Worker embutido em uso"),
            fix="" if opencode else "instale em https://opencode.ai e rode este assistente de novo",
        )
    )
    github = (gh_ok or (lambda: gh_logged_in(which)))()
    out.append(
        Service(
            id="github",
            label="GitHub (gh)",
            purpose="abre o Pull Request sozinho quando uma entrega é aprovada",
            need="optional",
            ok=github,
            detail="conectado" if github else "não conectado; a entrega fica só no git local",
            fix="" if github else "instale em https://cli.github.com e rode: gh auth login",
        )
    )
    if config.quality.coderabbit.enabled and config.quality.coderabbit.mode == "cli":
        found = which("coderabbit")
        out.append(
            Service(
                id="coderabbit",
                label="CodeRabbit",
                purpose="segunda revisão automática do código antes da entrega",
                need="optional",
                ok=bool(found),
                detail="instalado" if found else "ligado na configuração, mas não instalado",
                fix="" if found else "instale o CLI do CodeRabbit ou desligue quality.coderabbit",
            )
        )
    return out
