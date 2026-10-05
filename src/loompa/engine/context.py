"""Runtime context shared by nodes and agents for one factory."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loompa.aci import ACI
from loompa.comms import FounderMessage
from loompa.config import LoompaConfig, Secrets
from loompa.config.secrets import install_log_redaction, redact_secrets
from loompa.factory import Factory
from loompa.finance import CostTracker
from loompa.llm import ModelRouter
from loompa.mcp import McpHub
from loompa.memory import MemoryStore, get_embedder
from loompa.store import Store
from loompa.trace import Tracer
from loompa.worktrees import WorktreeManager

log = logging.getLogger("loompa.engine")

Listener = Callable[[dict[str, Any]], None]


@dataclass
class EngineContext:
    factory: Factory
    store: Store
    memory: MemoryStore
    router: ModelRouter
    tracker: CostTracker
    worktrees: WorktreeManager
    dry_run: bool = False
    listeners: list[Listener] = field(default_factory=list)
    secrets: Secrets = field(default_factory=Secrets)
    tracer: Tracer = field(default_factory=Tracer)
    closed: bool = False
    woke_at: float | None = None  # time.monotonic() when the engine last saw the machine wake
    _mcp: McpHub | None = field(default=None, repr=False)

    @property
    def mcp(self) -> McpHub:
        """The factory's MCP servers (web search for the Analyst); built on first use."""
        if self._mcp is None:
            self._mcp = McpHub(self.config, self.secrets)
        return self._mcp

    @mcp.setter
    def mcp(self, hub: McpHub) -> None:
        self._mcp = hub

    @property
    def config(self) -> LoompaConfig:
        return self.factory.config

    @property
    def slug(self) -> str:
        return self.factory.slug

    @property
    def root(self) -> Path:
        return self.factory.root

    # ---------------------------------------------------------------- factory
    @classmethod
    def build(
        cls,
        factory: Factory,
        *,
        router: ModelRouter | None = None,
        store: Store | None = None,
        memory: MemoryStore | None = None,
        dry_run: bool = False,
    ) -> EngineContext:
        store = store or Store(factory.paths.state_db)
        tracker = CostTracker(store, factory.config, factory.slug)
        secrets = Secrets.load(factory.root)
        install_log_redaction(secrets.values_for_redaction())
        if memory is None:
            embedder = get_embedder(
                factory.config.memory.embedding_model, prefer_local_hash=dry_run
            )
            memory = MemoryStore(
                factory.paths.memory_db, embedder, chunk_size=factory.config.memory.chunk_size
            )
        ctx = cls(
            factory=factory,
            store=store,
            memory=memory,
            router=router or ModelRouter(factory.config, tracker=tracker, secrets=secrets),
            tracker=tracker,
            worktrees=WorktreeManager(factory.root, factory.paths.worktrees),
            dry_run=dry_run,
            secrets=secrets,
            tracer=Tracer(factory.paths.traces),
        )
        ctx._redact_traces()
        ctx._export_traces()
        ctx._remember_rested()
        ctx.tracer.prune(factory.config.trace.retention_days)
        if not ctx.router.tracer.enabled:
            ctx.router.tracer = ctx.tracer
        if router is not None and router.tracker is None:
            router.tracker = tracker
        if ctx.router.on_call is None:
            ctx.router.on_call = ctx._on_llm_call
        if ctx.router.on_model_gone is None:
            ctx.router.on_model_gone = ctx._on_model_gone
        if ctx.router.on_event is None:
            ctx.router.on_event = ctx.emit
        return ctx

    def reload_secrets(self) -> Secrets:
        """Re-read the secrets files and rebuild provider adapters (settings changed)."""
        self.secrets = Secrets.load(self.factory.root)
        install_log_redaction(self.secrets.values_for_redaction())
        self._redact_traces()
        self.router.reset_providers(self.secrets)
        if self._mcp is not None:
            self._mcp.secrets = self.secrets
        return self.secrets

    def _redact_traces(self) -> None:
        """The trace passes through the same redaction as the logs: this machine's keys, and
        anything shaped like one that a file or a tool result carried in."""
        values = self.secrets.values_for_redaction()
        self.tracer.set_redaction(lambda text: redact_secrets(text, values))
        if self.tracer.exporter is not None:
            self.tracer.exporter.redact = lambda text: redact_secrets(text, values)

    def _remember_rested(self) -> None:
        """A candidate rested for a role (it ran away there) stays rested across an engine
        restart: contas Sprint 2 restarted and the Product Owner went back to its runaway."""
        from datetime import UTC, datetime, timedelta

        from loompa.store import now_iso

        since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        rested = [
            e
            for e in self.store.events_between(self.slug, since, now_iso())
            if e["type"] == "llm.rested"
        ]
        if rested and hasattr(self.router, "remember_rested"):
            self.router.remember_rested(rested, now_iso())

    def _export_traces(self) -> None:
        """The optional OTLP copy of the trace (`trace.otlp`, off by default). Without the
        `trace` extra it says so once in the log and the local trace goes on."""
        from loompa.trace_export import OtlpExporter, OtlpUnavailable

        values = self.secrets.values_for_redaction()
        try:
            self.tracer.exporter = OtlpExporter.from_config(
                self.config.trace.otlp,
                factory=self.slug,
                secrets=self.secrets,
                redact=lambda text: redact_secrets(text, values),
            )
        except OtlpUnavailable as exc:
            log.warning("trace.otlp.endpoint is set but the export is not installed: %s", exc)

    def _on_llm_call(self, role: str, agent: str, routed: Any) -> None:
        self._watch_aliases(role, agent, routed)
        resp = routed.response
        self.emit(
            "llm.call",
            story_id=routed.story_id,
            agent=agent,
            role=role,
            model=routed.candidate.model,
            tier=routed.tier,
            cost_usd=routed.cost_usd,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cached_tokens=resp.cached_tokens,
            # why the answer ended: `length`/`max_tokens` is a cut (only the log said so before)
            finish_reason=resp.finish_reason,
            duration_ms=resp.duration_ms,
            span_id=routed.span_id,
            **({"cuts": routed.cuts} if routed.cuts else {}),
            **({"served_by": resp.served_by} if resp.served_by else {}),
        )

    def _on_model_gone(self, candidate: Any, detail: str) -> None:
        from loompa.models_sync import ModelWatch

        try:
            ModelWatch(self).model_gone(candidate.provider, candidate.model)
        except Exception:  # noqa: BLE001 - a notice must never break the call that is failing
            log.exception("could not report a model the provider does not have")

    def _watch_aliases(self, role: str, agent: str, routed: Any) -> None:
        from loompa.models_sync import AliasWatch

        try:
            AliasWatch(self).observe(role, agent, routed)
        except Exception:  # noqa: BLE001 - a notice must never cost the call that already answered
            log.exception("could not check whether a model alias moved")

    # ----------------------------------------------------------------- events
    def emit(
        self, type_: str, *, story_id: str | None = None, agent: str = "", **payload: Any
    ) -> None:
        event_id = self.store.emit(self.slug, type_, story_id=story_id, agent=agent, **payload)
        event = {
            "id": event_id,
            "factory": self.slug,
            "type": type_,
            "story_id": story_id,
            "agent": agent,
            "payload": payload,
        }
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:  # noqa: BLE001
                log.exception("listener failed")

    def agent_state(
        self,
        name: str,
        role: str,
        state: str,
        *,
        story_id: str | None = None,
        model: str = "",
        detail: str = "",
    ) -> None:
        self.store.set_agent(name, role, state, story_id=story_id, model=model, detail=detail)
        self.emit(
            "agent.state",
            story_id=story_id,
            agent=name,
            role=role,
            state=state,
            model=model,
            detail=detail,
        )

    def inbox(self, msg: FounderMessage) -> FounderMessage:
        if msg.story_id and msg.kind.value in ("blocked", "decision", "delivery"):
            from loompa.dependencies import note_dependents

            note_dependents(self.store, self.slug, msg)  # one answer may unblock several
        violations = msg.executive_audit()
        if violations:  # never let technical noise through, even from an LLM rewrite
            from loompa.comms import sanitize_for_founder

            msg.context = sanitize_for_founder(msg.context)
            msg.impact = sanitize_for_founder(msg.impact)
            msg.title = sanitize_for_founder(msg.title, max_chars=160)
            for d in msg.decisions:
                d.title = sanitize_for_founder(d.title, max_chars=160)
                d.context = sanitize_for_founder(d.context)
        msg.factory = self.slug
        self.store.put_message(msg)
        self.emit(
            "inbox.new",
            story_id=msg.story_id,
            agent=msg.sender,
            message_id=msg.id,
            kind=msg.kind.value,
            title=msg.title,
        )
        return msg

    # ------------------------------------------------------------------- misc
    def aci_for(
        self, root: Path, allowed_paths: list[str] | None = None, *, diff_base: str | None = None
    ) -> ACI:
        q = self.config.quality
        return ACI(
            root,
            test_command=q.test_command,
            lint_command=q.lint_command,
            typecheck_command=q.typecheck_command,
            format_command=q.format_command,
            allowed_paths=allowed_paths,
            diff_base=diff_base,
        )

    def index_memory(self) -> dict[str, int]:
        """(Re)index constitution, ADRs, learnings, specs and docs into organizational memory."""
        p = self.factory.paths
        n = 0
        n += self.memory.index_file(p.constitution, kind="constitution", doc_id="constitution.md")
        n += self.memory.index_file(p.learnings, kind="learning", doc_id="learnings.md")
        n += self.memory.index_directory(p.decisions, kind="adr", relative_to=p.loompa)
        n += self.memory.index_directory(p.specs, kind="spec", relative_to=p.loompa)
        for name in ("docs", "doc", "adr", "adrs"):
            n += self.memory.index_directory(self.root / name, kind="doc", relative_to=self.root)
        for name in ("README.md", "CONTRIBUTING.md", "ARCHITECTURE.md"):
            n += self.memory.index_file(self.root / name, kind="doc", doc_id=name)
        return {"chunks": n, **self.memory.stats()}

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.tracer.close()  # what the OTLP export still holds
        rt = getattr(self, "_graph_runtime", None)
        if rt is not None:
            import asyncio

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(rt.close())
            self._graph_runtime = None  # type: ignore[attr-defined]
        self.store.close()
        self.memory.close()

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        rt = getattr(self, "_graph_runtime", None)
        if rt is not None:
            await rt.close()
            self._graph_runtime = None  # type: ignore[attr-defined]
        self.store.close()
        self.memory.close()
