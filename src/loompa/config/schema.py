"""Pydantic schemas for `.loompa/config.yaml` and the global hub registry."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Mode = Literal["greenfield", "brownfield"]
# Roles are an open set: any agent role maps to a tier through `models.roles`, defaulting to
# tier2. `ROLES` lists the ones shipped today (used for defaults and the dashboard office).
Role = str
ROLES: tuple[str, ...] = (
    "master",
    "product",
    "product_owner",
    "architect",
    "analyst",
    "worker",
    "inspector",
    "deployer",
    "ops",
    "finance",
    "compliance",
    "metrics",
    "storyteller",
    "kaizen",
)


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return value or "factory"


class FactoryConfig(BaseModel):
    name: str = ""
    slug: str = ""
    mode: Mode = "greenfield"
    language: str = "pt-BR"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("slug", mode="before")
    @classmethod
    def _slug(cls, v: str) -> str:
        return slugify(v) if v else ""


class ScheduleConfig(BaseModel):
    max_parallel: int = Field(3, ge=1, le=32)
    tier2_max_attempts: int = Field(2, ge=1)
    tier1_max_attempts: int = Field(1, ge=1)
    worker_max_iterations: int = Field(40, ge=1)
    worker_keep_tool_results: int = Field(6, ge=1)
    work_hours: str = "09:00-17:30"
    ops_max_recoveries: int = Field(3, ge=0)  # transient crashes the Ops Loompa retries per story
    ops_retry_base_s: float = Field(60.0, ge=0)  # first wait; doubles each retry, capped at 10 min


class WorkerConfig(BaseModel):
    backend: Literal["aci", "opencode"] = "aci"
    opencode_bin: str = "opencode"
    opencode_agent: str = "loompa-worker"
    opencode_timeout_s: int = Field(900, ge=1)


class BudgetConfig(BaseModel):
    monthly_cap_usd: float = Field(30.0, ge=0)
    warn_at_fraction: float = Field(0.8, ge=0, le=1)
    hard_stop: bool = True


class ModelCandidate(BaseModel):
    provider: str
    model: str
    temperature: float | None = None
    max_output_tokens: int | None = None


class ModelsConfig(BaseModel):
    tiers: dict[str, list[ModelCandidate]] = Field(default_factory=dict)
    roles: dict[str, str] = Field(default_factory=dict)
    temperature: float = 0.2
    max_output_tokens: int = 4096
    preset: str = ""  # last preset applied (informational; tiers are the source of truth)

    def tier_for(self, role: str) -> str:
        return self.roles.get(role) or "tier2"

    def candidates_for(self, role: str) -> list[ModelCandidate]:
        return self.tiers.get(self.tier_for(role), [])


class ProviderConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    kind: Literal["openai_compatible", "anthropic", "mock"] = "openai_compatible"
    base_url: str = ""
    api_key_env: str = ""  # NAME of the variable; the value lives in the secrets files
    extra_headers: dict[str, str] = Field(default_factory=dict)
    label: str = ""
    console_url: str = ""  # where the founder creates the key

    @field_validator("api_key_env")
    @classmethod
    def _env_name_only(cls, v: str) -> str:
        from loompa.config.secrets import looks_like_secret

        if looks_like_secret(v):
            raise ValueError("api_key_env deve ser o NOME da variável, nunca a chave")
        return v


class ToolProviderConfig(BaseModel):
    """An external tool (web search, ...) that needs its own key."""

    enabled: bool = True
    api_key_env: str = ""
    base_url: str = ""
    console_url: str = ""


class ToolsConfig(BaseModel):
    tavily: ToolProviderConfig = Field(
        default_factory=lambda: ToolProviderConfig(
            api_key_env="TAVILY_API_KEY",
            base_url="https://api.tavily.com",
            console_url="https://app.tavily.com",
        )
    )


class Price(BaseModel):
    input: float = 0.0
    output: float = 0.0
    cached_input: float = 0.0


class CodeRabbitConfig(BaseModel):
    enabled: bool = False
    mode: Literal["cli", "webhook"] = "cli"


class QualityConfig(BaseModel):
    test_command: str = ""
    lint_command: str = ""
    typecheck_command: str = ""
    format_command: str = ""
    coverage_min: int = Field(0, ge=0, le=100)
    coderabbit: CodeRabbitConfig = Field(default_factory=CodeRabbitConfig)
    require_spec_before_code: bool = True


class MemoryConfig(BaseModel):
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    chunk_size: int = Field(800, ge=100)
    top_k: int = Field(6, ge=1)


class DashboardConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1, le=65535)


class StackProfile(BaseModel):
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    test_frameworks: list[str] = Field(default_factory=list)
    linters: list[str] = Field(default_factory=list)
    ci: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)


class LoompaConfig(BaseModel):
    factory: FactoryConfig = Field(default_factory=FactoryConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    pricing: dict[str, Price] = Field(default_factory=dict)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    stack: StackProfile = Field(default_factory=StackProfile)

    def price_for(self, model: str) -> Price:
        return self.pricing.get(model) or self.pricing.get("default") or Price()


class FactoryRef(BaseModel):
    """Entry in the global hub registry (~/.loompa/factories.yaml)."""

    slug: str
    name: str
    path: Path
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class HubRegistry(BaseModel):
    active: str | None = None
    factories: list[FactoryRef] = Field(default_factory=list)

    def get(self, slug: str) -> FactoryRef | None:
        return next((f for f in self.factories if f.slug == slug), None)

    def upsert(self, ref: FactoryRef) -> None:
        self.factories = [f for f in self.factories if f.slug != ref.slug] + [ref]
        if self.active is None:
            self.active = ref.slug

    def remove(self, slug: str) -> bool:
        before = len(self.factories)
        self.factories = [f for f in self.factories if f.slug != slug]
        if self.active == slug:
            self.active = self.factories[0].slug if self.factories else None
        return len(self.factories) != before
