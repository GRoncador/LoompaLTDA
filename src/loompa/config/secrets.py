"""API keys and other secrets: where they live, how they are read, how they are never shown.

Rules (PLANO-2026-09, Fase 0b):

* Values live in dotenv files only: ``~/.loompa/secrets.env`` (hub, every factory on this
  machine) and ``<repo>/.loompa/.env`` (one factory, overrides the hub). Both are created with
  mode 0600 and the factory file is gitignored by ``loompa init``.
* ``config.yaml`` stores just the *name* of the variable (``api_key_env``). ``save_config``
  refuses any value that looks like a key.
* Nothing founder-facing or persisted (logs, events, inbox, API responses) ever carries a
  value: only "configurada" plus the last four characters.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

HUB_SECRETS_FILE = "secrets.env"
FACTORY_SECRETS_FILE = ".env"

# Prefixes used by the providers we ship presets for, plus the generic long-token shape.
_KEY_PREFIXES = (
    "sk-",  # OpenAI-style (DeepSeek, OpenRouter "sk-or-", Anthropic "sk-ant-")
    "AIza",  # Google AI Studio / Gemini
    "tvly-",  # Tavily
    "gsk_",  # Groq
    "xai-",
    "hf_",
    "ghp_",
    "github_pat_",
)
_LONG_TOKEN = re.compile(r"^[A-Za-z0-9_\-]{40,}$")


def looks_like_secret(value: object) -> bool:
    """True for strings that have the shape of an API key (never for env var names)."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or v.isupper():  # ``GEMINI_API_KEY`` is a name, not a key
        return False
    return v.startswith(_KEY_PREFIXES) or bool(_LONG_TOKEN.match(v))


def find_secrets(data: object, path: str = "") -> list[str]:
    """Paths inside a nested dict/list whose values look like keys."""
    found: list[str] = []
    if isinstance(data, Mapping):
        for k, v in data.items():
            found += find_secrets(v, f"{path}.{k}" if path else str(k))
    elif isinstance(data, list | tuple):
        for i, v in enumerate(data):
            found += find_secrets(v, f"{path}[{i}]")
    elif looks_like_secret(data):
        found.append(path or "<root>")
    return found


def mask(value: str | None) -> str:
    """Founder-facing status: never the value, only that it exists."""
    if not value:
        return "não configurada"
    tail = value[-4:] if len(value) >= 8 else ""
    return f"configurada (…{tail})" if tail else "configurada"


# ---------------------------------------------------------------------- dotenv files


def parse_dotenv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] in ('"', "'"):
            quote = value[0]
            end = value.find(quote, 1)
            value = value[1:end] if end > 0 else value[1:]
        else:
            value = value.split(" #", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            out[key] = value
    return out


def read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return parse_dotenv(path.read_text(encoding="utf-8"))


def write_dotenv_value(path: Path, name: str, value: str | None) -> Path:
    """Set (or remove, when value is None/empty) one variable, keeping other lines intact."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"nome de variável inválido: {name!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    prefix = f"{name}="
    kept = [ln for ln in lines if not ln.strip().startswith(prefix)]
    if value:
        kept.append(f"{name}={value}")
    text = "\n".join(kept).rstrip("\n")
    path.write_text((text + "\n") if text else "", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - exotic filesystems
        pass
    return path


# ---------------------------------------------------------------------------- loading


def hub_home() -> Path:
    env = os.environ.get("LOOMPA_HOME")
    return Path(env) if env else Path.home() / ".loompa"


def hub_secrets_path(home: Path | None = None) -> Path:
    return (home or hub_home()) / HUB_SECRETS_FILE


def factory_secrets_path(root: Path) -> Path:
    return Path(root) / ".loompa" / FACTORY_SECRETS_FILE


class Secrets(Mapping[str, str]):
    """Resolved secrets for one factory with their origin, so the UI can say where a key
    came from without ever showing it. Process environment is the last fallback."""

    def __init__(self, values: dict[str, str] | None = None, sources: dict[str, str] | None = None):
        self._values = dict(values or {})
        self._sources = dict(sources or {})

    @classmethod
    def load(cls, root: Path | None, *, home: Path | None = None) -> Secrets:
        values: dict[str, str] = {}
        sources: dict[str, str] = {}
        for origin, path in (
            ("hub", hub_secrets_path(home)),
            ("factory", factory_secrets_path(root) if root else None),
        ):
            if path is None:
                continue
            for k, v in read_dotenv(path).items():
                if v:
                    values[k] = v
                    sources[k] = origin
        return cls(values, sources)

    def __getitem__(self, key: str) -> str:
        if key in self._values:
            return self._values[key]
        env = os.environ.get(key)
        if env:
            return env
        raise KeyError(key)

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def get(self, key: str, default: str | None = None) -> str | None:  # type: ignore[override]
        try:
            return self[key]
        except KeyError:
            return default

    def source(self, key: str) -> str | None:
        if key in self._sources:
            return self._sources[key]
        return "env" if os.environ.get(key) else None

    def status(self, key: str) -> dict[str, str | bool | None]:
        value = self.get(key) if key else None
        return {
            "env": key,
            "configured": bool(value),
            "label": mask(value),
            "source": self.source(key) if key else None,
        }

    def values_for_redaction(self) -> list[str]:
        return [v for v in self._values.values() if len(v) >= 8] + [
            v for k, v in os.environ.items() if k.endswith("_API_KEY") and len(v) >= 8
        ]


# -------------------------------------------------------------------------- redaction


def redact(text: str, values: Iterable[str]) -> str:
    for v in values:
        if v and v in text:
            text = text.replace(v, f"…{v[-4:]}" if len(v) >= 8 else "…")
    return text


class SecretRedactor(logging.Filter):
    """Logging filter that masks known secret values in every record it sees."""

    def __init__(self, values: Iterable[str]):
        super().__init__("loompa.secrets")
        self.values = [v for v in values if v]

    def filter(self, record: logging.LogRecord) -> bool:
        if self.values:
            try:
                record.msg = redact(str(record.getMessage()), self.values)
                record.args = ()
            except Exception:  # noqa: BLE001 - never break logging
                pass
        return True


def install_log_redaction(values: Iterable[str]) -> None:
    root = logging.getLogger()
    for f in list(root.filters):
        if isinstance(f, SecretRedactor):
            root.removeFilter(f)
    vals = [v for v in values if v]
    if vals:
        root.addFilter(SecretRedactor(vals))
        for h in root.handlers:
            for f in list(h.filters):
                if isinstance(f, SecretRedactor):
                    h.removeFilter(f)
            h.addFilter(SecretRedactor(vals))
