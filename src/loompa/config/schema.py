"""Pydantic schemas for `.loompa/config.yaml` and the global hub registry."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

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
    tier1_max_attempts: int = Field(2, ge=1)
    worker_max_iterations: int = Field(40, ge=1)
    worker_keep_tool_results: int = Field(6, ge=1)
    # Older file reads kept verbatim (newest first, while still current), in characters, and the
    # streak of reads without a change after which the Worker is told to stop exploring.
    worker_keep_file_chars: int = Field(16000, ge=0)
    worker_explore_nudge: int = Field(10, ge=0)  # 0 = never
    # Lookups answered from memory (nothing changed since the same call) after which the task
    # stops with a diagnosis instead of running to the tool-call limit (Fase 8.5); 0 = never.
    worker_repeat_limit: int = Field(6, ge=0)
    # Tool rounds a role may use before it must answer (ADR-0009); 0 = one-shot, no tools.
    agent_tool_iterations: int = Field(8, ge=0)  # Architect, Product
    research_max_iterations: int = Field(14, ge=0)  # Analyst: search, extract, read
    work_hours: str = "09:00-17:30"
    ops_max_recoveries: int = Field(3, ge=0)  # transient crashes the Ops Loompa retries per story
    ops_retry_base_s: float = Field(60.0, ge=0)  # first wait; doubles each retry, capped at 10 min
    # A running story with no event for this long is stalled (Fase 8.1): the Ops Loompa restarts
    # it from its last checkpoint. Above the longest silent step (a 15-minute test run), and the
    # Mac sleeping never counts: silence is measured on the clock that stops in sleep. 0 = off.
    stall_minutes: float = Field(20.0, ge=0)
    # How much ceremony a story gets (Fase 7, 7.5). `auto`: SIMPLE runs yolo, COMPLEX (or a plan
    # touching schemas, migrations or contracts) runs preflight, the rest standard.
    autonomy: Literal["auto", "yolo", "standard", "preflight"] = "auto"


class WorkerConfig(BaseModel):
    backend: Literal["aci", "opencode"] = "aci"
    opencode_bin: str = "opencode"
    opencode_agent: str = "loompa-worker"
    opencode_timeout_s: int = Field(900, ge=1)


BudgetPeriod = Literal["weekly", "monthly"]
# What the factory does once the cap is reached. Binary on purpose: either every agent drops to
# the free tier and the line keeps moving, or the line stops until the next period.
OnExceed = Literal["pause", "tier3"]


class BudgetConfig(BaseModel):
    """The spending cap of one period. A week by default: a founder notices a bad week, and a
    month of drift is a month of drift."""

    period: BudgetPeriod = "weekly"
    cap_usd: float = Field(5.0, ge=0)
    warn_at_fraction: float = Field(0.8, ge=0, le=1)
    on_exceed: OnExceed = "pause"

    @model_validator(mode="before")
    @classmethod
    def _from_legacy(cls, data: Any) -> Any:
        """config.yaml files written before the weekly budget carry `monthly_cap_usd`/`hard_stop`.
        Their numbers were chosen for a month, so the period stays monthly and nothing changes
        under the founder until they open the budget tab."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        cap = data.pop("monthly_cap_usd", None)
        hard_stop = data.pop("hard_stop", None)
        if cap is not None and "cap_usd" not in data:
            data["cap_usd"] = cap
            data.setdefault("period", "monthly")
        if hard_stop is not None and "on_exceed" not in data:
            data["on_exceed"] = "pause" if hard_stop else "tier3"
        return data


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
# The single cluster every role shares when `models.clusters_enabled` is off: one 3-tier list to
# fill instead of three, ranked on the plain mean of the three indices.
GENERAL_CLUSTER = "general"
ALL_CLUSTERS: tuple[str, ...] = (*CLUSTERS, GENERAL_CLUSTER)

CLUSTER_ROLES: dict[str, tuple[str, ...]] = {
    "strategy": ("master", "architect", "product", "product_owner", "analyst"),
    "engineering": ("worker", "inspector"),
    "routine": ("deployer", "storyteller", "compliance", "metrics"),
}

ROLE_CLUSTERS: dict[str, str] = {
    role: cluster for cluster, roles in CLUSTER_ROLES.items() for role in roles
}


class RoleTask(BaseModel):
    """A named call a role makes that does not deserve its role's default tier.

    They are declared here, not discovered: an agent asks for a tier by task key, so the settings
    screen can show exactly the ones that exist instead of every prompt in the codebase. Tasks
    with the same profile share a key rather than getting one each."""

    key: str  # "<role>.<task>"
    role: str
    label: str  # pt-BR, founder-facing
    hint: str  # why this task is not the role's default tier
    default_tier: str


ROLE_TASKS: tuple[RoleTask, ...] = (
    RoleTask(
        key="master.classify",
        role="master",
        label="Classificar histórias",
        hint="lê a história e diz o tipo e o tamanho; não precisa do tier de decisão",
        default_tier="tier2",
    ),
    RoleTask(
        key="master.exec_options",
        role="master",
        label="Opções para um bloqueio",
        hint="transforma um problema técnico em opções para o Founder escolher",
        default_tier="tier2",
    ),
    RoleTask(
        key="deployer.summary",
        role="deployer",
        label="Resumo da entrega",
        hint="reescreve as notas do Worker em uma frase para o Founder",
        default_tier="tier2",
    ),
)

ROLE_TASKS_BY_KEY: dict[str, RoleTask] = {t.key: t for t in ROLE_TASKS}


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
    # `<role>.<task>` -> tier, for the calls in ROLE_TASKS whose profile differs from the role's
    # default. A key that is absent uses the task's `default_tier`.
    role_tasks: dict[str, str] = Field(default_factory=dict)
    temperature: float = 0.2
    # Output room is a backstop, not an estimate (ADR-0016): a call pays only for what it writes,
    # and room that is too small cuts it and pays for the discarded attempt. `low` calls get the
    # light room, every other call the full one; a cut doubles it up to the ceiling, then the next
    # candidate at the same effort, `truncation_retries` times per model.
    light_output_tokens: int = Field(16384, ge=256)
    full_output_tokens: int = Field(96000, ge=256)
    max_output_ceiling: int = Field(96000, ge=256)
    truncation_retries: int = Field(2, ge=0, le=5)
    # Legacy, no longer used to size calls (ADR-0016); kept so older config files still load.
    max_output_tokens: int = 4096
    output_scale: dict[str, float] = Field(
        default_factory=lambda: {"SIMPLE": 1.0, "STANDARD": 1.5, "COMPLEX": 3.0}
    )
    # A streamed call fails (retryable) when nothing at all arrives for `stream_idle_s` (the
    # connection is gone), or when the server keeps saying it is processing (keep-alive comments)
    # but no token comes for `stream_token_idle_s`. Live, a healthy judge call waited ~100 s for
    # its first token while the provider worked. No wall-clock limit: the output room ends a
    # runaway.
    stream_idle_s: float = Field(300.0, ge=10)
    stream_token_idle_s: float = Field(900.0, ge=10)
    # Wall-clock limit for a call to a provider that is not streamed.
    call_timeout_s: float = Field(600.0, ge=10)
    # Off: every role shares the `general` cluster, one 3-tier list instead of three.
    clusters_enabled: bool = True
    tier1_ceiling: float = Field(1.25, ge=0.0)
    tier2_floor: float = Field(0.75, ge=0.0, le=1.0)

    def _on_tier_updated(self, tier: str, cands: list[ModelCandidate]) -> None:
        if not hasattr(self, "matrix") or not self.matrix:
            self.matrix = {c: {} for c in ALL_CLUSTERS}
        for cluster in ALL_CLUSTERS:
            if cluster not in self.matrix:
                self.matrix[cluster] = {}
            self.matrix[cluster][tier] = [c.model_copy() for c in cands]

    def _sync_matrix_from_tiers(self, tiers_dict: dict[str, list[ModelCandidate]]) -> None:
        if not hasattr(self, "matrix") or not self.matrix:
            self.matrix = {c: {} for c in ALL_CLUSTERS}
        for cluster in ALL_CLUSTERS:
            if cluster not in self.matrix:
                self.matrix[cluster] = {}
            for t, cands in tiers_dict.items():
                self.matrix[cluster][t] = [c.model_copy() for c in cands]

    def _sync_tiers_from_matrix(
        self, matrix_dict: dict[str, dict[str, list[ModelCandidate]]]
    ) -> None:
        if not matrix_dict:
            return
        general = matrix_dict.get(GENERAL_CLUSTER, {})
        t1 = (
            general.get("tier1")
            or matrix_dict.get("strategy", {}).get("tier1")
            or matrix_dict.get("engineering", {}).get("tier1")
            or []
        )
        t2 = (
            general.get("tier2")
            or matrix_dict.get("engineering", {}).get("tier2")
            or matrix_dict.get("strategy", {}).get("tier2")
            or []
        )
        t3 = (
            general.get("tier3")
            or matrix_dict.get("routine", {}).get("tier3")
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
                for cluster in ALL_CLUSTERS
            }
        elif self.matrix and not self.tiers:
            self._sync_tiers_from_matrix(self.matrix)
        if not isinstance(self.tiers, _SyncedDict):
            super().__setattr__("tiers", _SyncedDict(self, self.tiers))

    def cluster_for_role(self, role: str) -> str:
        if not self.clusters_enabled:
            return GENERAL_CLUSTER
        return ROLE_CLUSTERS.get(role, "routine")

    def tier_for(self, role: str) -> str:
        return self.roles.get(role) or "tier2"

    def tier_for_task(self, role: str, task: str | None) -> str:
        """The tier of one named call: what the founder set for it, else the task's own default,
        else the role's. An unknown task is the role's tier, never an error."""
        if not task:
            return self.tier_for(role)
        key = task if "." in task else f"{role}.{task}"
        declared = ROLE_TASKS_BY_KEY.get(key)
        if declared is None:
            return self.tier_for(role)
        return self.role_tasks.get(key) or declared.default_tier

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
    # The vendor's own list of model ids. A provider that is not OpenRouter has no catalogue this
    # factory can read, so the settings screen links here instead of offering a picker.
    models_url: str = ""
    # A cheap, long-lived id to test the key with when this provider is in no tier yet. Empty
    # means the founder has to name a model first: guessing an id would make a working key look
    # broken, which is worse than asking.
    probe_model: str = ""
    # OpenAI-compatible providers stream their answers (ADR-0016 §4): no wall-clock limit, only
    # silence, and progress while the model writes. Off reads each answer whole.
    stream: bool = True

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
# Exact names, not globs: `*search*` also matched `tavily_research`, and a glob silently admits
# whatever the vendor adds next. A renamed tool shows up as an empty allow list in the probe.
TAVILY_MCP_ALLOW = ["tavily_search", "tavily_extract"]
_TAVILY_LEAKY_ALLOW = ["*search*", "*extract*"]  # the old default, corrected on load
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
        elif self.tavily.allow == _TAVILY_LEAKY_ALLOW:
            # Factories onboarded before 2026-09-20 carry the glob that also admitted
            # `tavily_research`. Nobody chose it deliberately — it was the default — so it is
            # corrected on load instead of leaving those factories with the expensive tool.
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
    # webhook mode: GitHub signs each call with this secret (the NAME of the variable; the value
    # lives in the secrets files). Reviews from `bot_login` that ask for changes send the
    # delivery back to the Worker, at most `max_rounds` times per story.
    webhook_secret_env: str = "GITHUB_WEBHOOK_SECRET"
    bot_login: str = "coderabbitai[bot]"
    max_rounds: int = Field(2, ge=0, le=5)


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


class OtlpConfig(BaseModel):
    """Optional export of the trace by OTLP/HTTP (`loompa/trace_export.py`). Off while
    `endpoint` is empty. `headers` maps a header to the NAME of a variable in the secrets files,
    never to its value."""

    endpoint: str = ""  # e.g. http://localhost:6006/v1/traces (Phoenix)
    headers: dict[str, str] = Field(default_factory=dict)
    project: str = ""  # the viewer's project; the factory slug when empty


class TraceConfig(BaseModel):
    """The per-story trace (`loompa/trace.py`). Always on; only how long it is kept is a choice:
    long enough to compare a sprint with the one before it."""

    retention_days: int = Field(30, ge=1)
    otlp: OtlpConfig = Field(default_factory=OtlpConfig)


class StackProfile(BaseModel):
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    test_frameworks: list[str] = Field(default_factory=list)
    linters: list[str] = Field(default_factory=list)
    ci: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)


# Providers that were shipped once and are not offered any more. A factory keeps one only while
# it is actually using it: a key it holds, or a model in some tier. Otherwise the list on the
# settings screen would keep growing with names nobody chose.
RETIRED_PROVIDERS: tuple[str, ...] = ("groq",)


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
    trace: TraceConfig = Field(default_factory=TraceConfig)
    stack: StackProfile = Field(default_factory=StackProfile)

    @model_validator(mode="after")
    def _drop_unused_retired_providers(self) -> LoompaConfig:
        in_use = {
            c.provider
            for tier_map in self.models.matrix.values()
            for cands in tier_map.values()
            for c in cands
        } | {c.provider for cands in self.models.tiers.values() for c in cands}
        for name in RETIRED_PROVIDERS:
            if name in self.providers and name not in in_use:
                del self.providers[name]
        return self

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
