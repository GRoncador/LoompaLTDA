"""Pydantic schemas for `.loompa/config.yaml` and the global hub registry."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    # Tool rounds a role may use before it must answer (ADR-0009); 0 = one-shot, no tools.
    agent_tool_iterations: int = Field(8, ge=0)  # Architect, Product
    research_max_iterations: int = Field(14, ge=0)  # Analyst: search, extract, read
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


REASONING_EFFORTS = ("minimal", "low", "medium", "high", "max")


class ModelCandidate(BaseModel):
    provider: str
    model: str
    temperature: float | None = None
    max_output_tokens: int | None = None
    # How much a reasoning model may think before answering. On such a model the output budget
    # pays for the thinking *and* the answer, so a short task with a small `max_output_tokens`
    # can spend all of it reasoning and return nothing. Empty keeps the provider's default.
    reasoning_effort: str = ""

    @field_validator("reasoning_effort")
    @classmethod
    def _known_effort(cls, v: str) -> str:
        if v and v not in REASONING_EFFORTS:
            raise ValueError(f"reasoning_effort deve ser um de {', '.join(REASONING_EFFORTS)}")
        return v


CLUSTERS: tuple[str, ...] = ("strategy", "engineering", "routine")
TIERS: tuple[str, ...] = ("tier1", "tier2", "tier3")

CLUSTER_ROLES: dict[str, tuple[str, ...]] = {
    "strategy": ("master", "architect", "product", "product_owner", "analyst"),
    "engineering": ("worker", "inspector"),
    "routine": ("deployer", "storyteller", "compliance", "metrics"),
}

ROLE_CLUSTERS: dict[str, str] = {
    role: cluster for cluster, roles in CLUSTER_ROLES.items() for role in roles
}


class _SyncedDict(dict):
    def __init__(self, owner: Any, initial: dict | None = None):
        super().__init__(initial or {})
        self._owner = owner

    def __setitem__(self, key: str, value: Any):
        super().__setitem__(key, value)
        owner = getattr(self, "_owner", None)
        if owner is not None:
            owner._on_tier_updated(key, value)


class ModelsConfig(BaseModel):
    matrix: dict[str, dict[str, list[ModelCandidate]]] = Field(default_factory=dict)
    tiers: dict[str, list[ModelCandidate]] = Field(default_factory=dict)
    roles: dict[str, str] = Field(default_factory=dict)
    temperature: float = 0.2
    max_output_tokens: int = 4096
    preset: str = ""  # last preset applied (informational; matrix is the source of truth)
    tier1_ceiling: float = Field(5.0, ge=0.0)
    tier2_floor: float = Field(0.80, ge=0.0, le=1.0)

    def _on_tier_updated(self, tier: str, cands: list[ModelCandidate]) -> None:
        if not hasattr(self, "matrix") or not self.matrix:
            self.matrix = {c: {} for c in CLUSTERS}
        for cluster in CLUSTERS:
            if cluster not in self.matrix:
                self.matrix[cluster] = {}
            self.matrix[cluster][tier] = [c.model_copy() for c in cands]

    def _sync_matrix_from_tiers(self, tiers_dict: dict[str, list[ModelCandidate]]) -> None:
        if not hasattr(self, "matrix") or not self.matrix:
            self.matrix = {c: {} for c in CLUSTERS}
        for cluster in CLUSTERS:
            if cluster not in self.matrix:
                self.matrix[cluster] = {}
            for t, cands in tiers_dict.items():
                self.matrix[cluster][t] = [c.model_copy() for c in cands]

    def _sync_tiers_from_matrix(self, matrix_dict: dict[str, dict[str, list[ModelCandidate]]]) -> None:
        if not matrix_dict:
            return
        t1 = (
            matrix_dict.get("strategy", {}).get("tier1")
            or matrix_dict.get("engineering", {}).get("tier1")
            or []
        )
        t2 = (
            matrix_dict.get("engineering", {}).get("tier2")
            or matrix_dict.get("strategy", {}).get("tier2")
            or []
        )
        t3 = (
            matrix_dict.get("routine", {}).get("tier3")
            or matrix_dict.get("engineering", {}).get("tier3")
            or []
        )
        synced = {
            "tier1": [c.model_copy() for c in t1],
            "tier2": [c.model_copy() for c in t2],
            "tier3": [c.model_copy() for c in t3],
        }
        super().__setattr__("tiers", _SyncedDict(self, synced))

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "tiers" and isinstance(value, dict) and not isinstance(value, _SyncedDict):
            synced = _SyncedDict(self, value)
            super().__setattr__("tiers", synced)
            self._sync_matrix_from_tiers(value)
            return
        super().__setattr__(name, value)
        if name == "matrix" and isinstance(value, dict):
            if not self.tiers:
                self._sync_tiers_from_matrix(value)

    def model_post_init(self, __context: Any) -> None:
        if not self.matrix and self.tiers:
            self.matrix = {
                cluster: {
                    "tier1": [c.model_copy() for c in self.tiers.get("tier1", [])],
                    "tier2": [c.model_copy() for c in self.tiers.get("tier2", [])],
                    "tier3": [c.model_copy() for c in self.tiers.get("tier3", [])]
                    if "tier3" in self.tiers
                    else [
                        c.model_copy()
                        for c in self.tiers.get("tier2", [])
                        if c.model.endswith(":free") or c.model == "openrouter/free"
                    ],
                }
                for cluster in CLUSTERS
            }
        elif self.matrix and not self.tiers:
            self._sync_tiers_from_matrix(self.matrix)
        if not isinstance(self.tiers, _SyncedDict):
            super().__setattr__("tiers", _SyncedDict(self, self.tiers))

    def cluster_for_role(self, role: str) -> str:
        return ROLE_CLUSTERS.get(role, "routine")

    def tier_for(self, role: str) -> str:
        return self.roles.get(role) or "tier2"

    def candidates_for_cluster_tier(self, cluster: str, tier: str) -> list[ModelCandidate]:
        if self.matrix and cluster in self.matrix:
            cands = self.matrix[cluster].get(tier, [])
            if cands:
                return cands
        return self.tiers.get(tier, [])

    def candidates_for(self, role: str, tier: str | None = None) -> list[ModelCandidate]:
        cluster = self.cluster_for_role(role)
        resolved_tier = tier or self.tier_for(role)
        return self.candidates_for_cluster_tier(cluster, resolved_tier)


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


class McpServerConfig(ToolProviderConfig):
    """An MCP server whose tools some roles may call (ADR-0009). Like every other key, the
    secret is only named here (`api_key_env`); the value comes from the secrets files."""

    transport: Literal["http", "stdio"] = "http"
    url: str = ""  # streamable-HTTP endpoint
    command: str = ""  # stdio: executable that speaks MCP on stdin/stdout
    args: list[str] = Field(default_factory=list)
    # How the key reaches the server: `bearer` = Authorization header, `query` = URL parameter
    # named `auth_name`, `env` = environment variable `auth_name` (stdio), `none` = no key.
    auth: Literal["bearer", "query", "env", "none"] = "bearer"
    auth_name: str = ""
    headers: dict[str, str] = Field(default_factory=dict)  # static and never secret
    roles: list[str] = Field(default_factory=lambda: ["analyst"])  # who may call its tools
    allow: list[str] = Field(default_factory=list)  # tool-name globs; empty = every tool
    timeout_s: float = Field(60.0, gt=0)


TAVILY_MCP_URL = "https://mcp.tavily.com/mcp/"
# Search and extract answer a research question; crawl/map/research burn credits on their own.
TAVILY_MCP_ALLOW = ["*search*", "*extract*"]
TAVILY_DEFAULT_PARAMETERS = (
    '{"include_images": false, "include_raw_content": false, "max_results": 5}'
)


def _tavily_default() -> McpServerConfig:
    return McpServerConfig(
        api_key_env="TAVILY_API_KEY",
        base_url="https://api.tavily.com",
        console_url="https://app.tavily.com",
        url=TAVILY_MCP_URL,
        allow=list(TAVILY_MCP_ALLOW),
        headers={"DEFAULT_PARAMETERS": TAVILY_DEFAULT_PARAMETERS},
    )


class ToolsConfig(BaseModel):
    tavily: McpServerConfig = Field(default_factory=_tavily_default)
    mcp: dict[str, McpServerConfig] = Field(default_factory=dict)  # any other MCP server

    @model_validator(mode="after")
    def _builtin_defaults(self) -> ToolsConfig:
        # config.yaml files written before MCP existed only carry Tavily's key fields
        if self.tavily.transport == "http" and not self.tavily.url:
            self.tavily.url = TAVILY_MCP_URL
        if not self.tavily.allow:
            self.tavily.allow = list(TAVILY_MCP_ALLOW)
        return self

    def servers(self) -> dict[str, McpServerConfig]:
        return {"tavily": self.tavily, **self.mcp}


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
