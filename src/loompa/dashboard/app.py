"""FastAPI backend for the Loompa LTDA HQ dashboard.

REST for factories, kanban, inbox, meeting, agents, finance and memory; one WebSocket (`/ws`)
fanning out engine events; a background engine loop per factory (watch mode) that can be
toggled. Serves the prebuilt Vite bundle from `dashboard/static/` when present.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from loompa import __version__
from loompa.comms import FounderAnswer
from loompa.config import ConfigStore
from loompa.config.settings import SettingsPatch
from loompa.engine import KANBAN_COLUMNS, EngineContext, Scheduler, kanban_column, load_state
from loompa.factory import Factory
from loompa.finance import month_start_iso, today_start_iso
from loompa.sprints import SprintBoard, SprintError, SprintStatus

log = logging.getLogger("loompa.dashboard")
STATIC_DIR = Path(__file__).parent / "static"

OFFICE_ROOMS = {
    "master": "meeting",
    "product": "meeting",
    "product_owner": "meeting",
    "analyst": "meeting",
    "architect": "meeting",
    "worker": "dev",
    "inspector": "qa",
    "deployer": "dev",
    "ops": "qa",
    "finance": "qa",
    "compliance": "lounge",
    "metrics": "lounge",
    "storyteller": "lounge",
    "kaizen": "lounge",
}
DEFAULT_AGENTS = [
    ("Master Loompa", "master"),
    ("Product Loompa", "product"),
    ("Product Owner Loompa", "product_owner"),
    ("Architect Loompa", "architect"),
    ("Worker Loompa", "worker"),
    ("Inspector Loompa", "inspector"),
    ("Deployer Loompa", "deployer"),
    ("Ops Loompa", "ops"),
    ("Finance Loompa", "finance"),
    ("Kaizen Loompa", "kaizen"),
    ("Storyteller Loompa", "storyteller"),
    ("Metrics Loompa", "metrics"),
]


class ReplyBody(BaseModel):
    option_key: str | None = None
    text: str | None = None
    decisions: dict[str, str] = {}


class ProbeBody(BaseModel):
    model: str | None = None


class MeetingBody(BaseModel):
    goals: str
    run: bool = False


class SprintBody(BaseModel):
    story_ids: list[str] = []
    goal: str = ""
    limit: int | None = None
    run: bool = True


class StoryBody(BaseModel):
    title: str
    description: str = ""
    priority: int = 3


class FactoryBody(BaseModel):
    path: str
    name: str | None = None
    stack: str = "custom"
    preset: str | None = None  # gratuito | economico | maximo
    keys: dict[str, str] = {}  # ENV_NAME -> value, written to the secrets file only
    secrets_scope: str = "hub"
    mission: str = ""


@dataclass
class FactoryRuntime:
    factory: Factory
    ctx: EngineContext
    engine_task: asyncio.Task | None = None
    listeners: set[asyncio.Queue] = field(default_factory=set)

    def broadcast(self, event: dict[str, Any]) -> None:
        for q in list(self.listeners):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)


class Hub:
    """Holds one runtime per open factory. Engines run in-process as background tasks."""

    def __init__(
        self, *, dry_run: bool = False, run_engine: bool = True, store: ConfigStore | None = None
    ):
        self.dry_run = dry_run
        self.run_engine = run_engine
        self.store = store or ConfigStore()
        self.runtimes: dict[str, FactoryRuntime] = {}

    def registry(self):
        return self.store.load()

    def get(self, slug: str) -> FactoryRuntime:
        if slug in self.runtimes:
            return self.runtimes[slug]
        ref = self.registry().get(slug)
        if ref is None or not (ref.path / ".loompa" / "config.yaml").is_file():
            raise HTTPException(404, f"fábrica desconhecida: {slug}")
        from loompa.cli.ops import build_context

        factory = Factory.open(ref.path)
        ctx = build_context(factory, dry_run=self.dry_run)
        rt = FactoryRuntime(factory=factory, ctx=ctx)
        ctx.listeners.append(rt.broadcast)
        self.runtimes[slug] = rt
        with contextlib.suppress(Exception):
            ctx.index_memory()  # idempotent (content-hashed); keeps recall fresh after edits
        return rt

    def active_slug(self, slug: str | None = None) -> str:
        if slug:
            return slug
        reg = self.registry()
        if reg.active:
            return reg.active
        if reg.factories:
            return reg.factories[0].slug
        raise HTTPException(404, "nenhuma fábrica registrada; rode `loompa init` em um projeto")

    async def start_engine(self, slug: str) -> None:
        rt = self.get(slug)
        if rt.engine_task and not rt.engine_task.done():
            return

        async def loop() -> None:
            sched = Scheduler(rt.ctx)
            rt.ctx.emit("engine.started")
            try:
                await sched.run(until_idle=False, poll_interval=2.0)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("engine loop for %s crashed", slug)
            finally:
                rt.ctx.emit("engine.stopped")

        rt.engine_task = asyncio.create_task(loop(), name=f"engine:{slug}")

    async def stop_engine(self, slug: str) -> None:
        rt = self.get(slug)
        if rt.engine_task and not rt.engine_task.done():
            rt.engine_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await rt.engine_task
        rt.engine_task = None

    def engine_running(self, slug: str) -> bool:
        rt = self.runtimes.get(slug)
        return bool(rt and rt.engine_task and not rt.engine_task.done())

    async def shutdown(self) -> None:
        for slug in list(self.runtimes):
            await self.stop_engine(slug)
            await self.runtimes[slug].ctx.aclose()
        self.runtimes.clear()


def _sprint_summary(ctx: EngineContext) -> dict[str, Any] | None:
    """The sprint the founder cares about now: the running one, else the one being planned."""
    board = SprintBoard(ctx.store, ctx.slug)
    active = [sp for sp in board.sprints() if sp.status != SprintStatus.CLOSED]
    if not active:
        return None
    sprint = next((sp for sp in active if sp.status == SprintStatus.RUNNING), active[-1])
    return {**sprint.model_dump(), "progress": board.progress(sprint)}


def create_app(
    *, dry_run: bool = False, run_engine: bool = True, store: ConfigStore | None = None
) -> FastAPI:
    hub = Hub(dry_run=dry_run, run_engine=run_engine, store=store)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        if hub.run_engine:
            with contextlib.suppress(HTTPException):
                await hub.start_engine(hub.active_slug())
        yield
        await hub.shutdown()

    app = FastAPI(title="Loompa LTDA HQ", version=__version__, lifespan=lifespan)
    app.state.hub = hub

    # ------------------------------------------------------------- factories
    @app.get("/api/factories")
    def factories() -> dict[str, Any]:
        reg = hub.registry()
        return {
            "active": reg.active,
            "factories": [
                {
                    "slug": f.slug,
                    "name": f.name,
                    "path": str(f.path),
                    "engine": hub.engine_running(f.slug),
                    "exists": (f.path / ".loompa" / "config.yaml").is_file(),
                }
                for f in reg.factories
            ],
            "dry_run": hub.dry_run,
        }

    @app.post("/api/factories/{slug}/activate")
    def activate(slug: str) -> dict[str, Any]:
        try:
            hub.store.set_active(slug)
        except KeyError:
            raise HTTPException(404, slug) from None
        return {"active": slug}

    @app.post("/api/factories")
    def add_factory(body: FactoryBody) -> dict[str, Any]:
        from loompa.factory import bootstrap_factory

        result = bootstrap_factory(
            Path(body.path).expanduser(),
            name=body.name,
            preset=body.stack,
            mission=body.mission,
            store=hub.store,
        )
        f = result.factory
        if body.preset or body.keys:
            from loompa.config import MODEL_PRESETS, apply_preset
            from loompa.config.settings import store_key

            if body.preset:
                if body.preset not in MODEL_PRESETS:
                    raise HTTPException(400, f"preset desconhecido: {body.preset}")
                apply_preset(f.config, body.preset)
                f.save()
            for env_name, value in body.keys.items():
                if value.strip():
                    store_key(f.root, env_name, value.strip(), scope=body.secrets_scope)
        return {"slug": f.slug, "mode": result.mode, "report": result.report}

    @app.post("/api/factories/{slug}/engine/{action}")
    async def engine(slug: str, action: str) -> dict[str, Any]:
        if action == "start":
            await hub.start_engine(slug)
        elif action == "stop":
            await hub.stop_engine(slug)
        else:
            raise HTTPException(400, "action must be start|stop")
        return {"engine": hub.engine_running(slug)}

    # -------------------------------------------------------------- overview
    @app.get("/api/factories/{slug}/overview")
    def overview(slug: str) -> dict[str, Any]:
        rt = hub.get(slug)
        ctx = rt.ctx
        stories = ctx.store.list_stories(slug)
        columns: dict[str, list[dict[str, Any]]] = {k: [] for k, _ in KANBAN_COLUMNS}
        for s in stories:
            columns[kanban_column(s["stage"])].append(_story_card(s))
        agents = {a["name"]: a for a in ctx.store.list_agents()}
        for name, role in DEFAULT_AGENTS:
            agents.setdefault(
                name,
                {
                    "name": name,
                    "role": role,
                    "state": "IDLE",
                    "story_id": None,
                    "model": "",
                    "detail": "",
                    "updated_at": "",
                },
            )
        for a in agents.values():
            a["room"] = OFFICE_ROOMS.get(a["role"], "lounge")
            a["cost_usd"] = _agent_cost(ctx, a["name"])
        pending = ctx.store.list_messages(slug, status="pending")
        budget = ctx.tracker.status()
        return {
            "factory": {
                "slug": slug,
                "name": ctx.config.factory.name,
                "mode": ctx.config.factory.mode,
                "language": ctx.config.factory.language,
                "engine": hub.engine_running(slug),
                "dry_run": hub.dry_run,
            },
            "columns": [
                {"key": k, "label": label, "stories": columns[k]} for k, label in KANBAN_COLUMNS
            ],
            "agents": sorted(agents.values(), key=lambda a: a["name"]),
            "inbox": [m.model_dump(mode="json") for m in pending],
            "finance": {
                "today_usd": budget.today_cost_usd,
                "month_usd": budget.month_cost_usd,
                "cap_usd": budget.cap_usd,
                "fraction": budget.fraction,
                "warn": budget.warn,
                "exhausted": budget.exhausted,
            },
            "kaizen_today": len(ctx.store.list_learnings(since_iso=today_start_iso())),
            "sprint": _sprint_summary(ctx),
            "last_event_id": _last_event_id(ctx),
        }

    @app.get("/api/factories/{slug}/stories/{story_id}")
    def story(slug: str, story_id: str) -> dict[str, Any]:
        ctx = hub.get(slug).ctx
        row = ctx.store.get_story(story_id)
        if row is None:
            raise HTTPException(404, story_id)
        state = load_state(ctx, story_id)
        specs = ctx.factory.paths.specs / story_id
        docs = {
            name: (specs / f"{name}.md").read_text(encoding="utf-8")
            for name in ("spec", "plan", "tasks", "research")
            if (specs / f"{name}.md").is_file()
        }
        return {
            "story": _story_card(row),
            "state": state.model_dump(mode="json"),
            "docs": docs,
            "checkpoints": ctx.store.checkpoints(story_id),
            "usage": ctx.store.usage_totals(slug, story_id=story_id),
            "commits": ctx.worktrees.log(ctx.worktrees.get(story_id))
            if ctx.worktrees.get(story_id)
            else [],
        }

    @app.post("/api/factories/{slug}/stories")
    def create_story(slug: str, body: StoryBody) -> dict[str, Any]:
        ctx = hub.get(slug).ctx
        from loompa.agents import ProductOwnerAgent

        added = ProductOwnerAgent(ctx).add_item(
            body.title,
            body.description,
            priority=max(1, min(5, body.priority)) * 100,
            origin="founder",
        )
        sid = added.story_id
        return {"id": sid}

    @app.post("/api/factories/{slug}/stories/{story_id}/promote")
    def promote(slug: str, story_id: str) -> dict[str, Any]:
        ctx = hub.get(slug).ctx
        Scheduler(ctx).promote(story_id)
        return {"id": story_id, "stage": ctx.store.get_story(story_id)["stage"]}

    # ------------------------------------------------------------------ inbox
    @app.get("/api/factories/{slug}/inbox")
    def inbox(slug: str, status: str | None = "pending") -> list[dict[str, Any]]:
        ctx = hub.get(slug).ctx
        return [
            m.model_dump(mode="json") for m in ctx.store.list_messages(slug, status=status or None)
        ]

    @app.post("/api/factories/{slug}/inbox/{message_id}/reply")
    async def reply(slug: str, message_id: str, body: ReplyBody) -> dict[str, Any]:
        ctx = hub.get(slug).ctx
        try:
            state = await Scheduler(ctx).aanswer(
                message_id,
                FounderAnswer(option_key=body.option_key, text=body.text, decisions=body.decisions),
            )
        except KeyError:
            raise HTTPException(404, message_id) from None
        return {
            "message_id": message_id,
            "story_id": state.story_id if state else None,
            "stage": state.stage.value if state else None,
        }

    @app.post("/api/factories/{slug}/inbox/{message_id}/archive")
    def archive(slug: str, message_id: str) -> dict[str, Any]:
        hub.get(slug).ctx.store.archive_message(message_id)
        return {"message_id": message_id, "status": "archived"}

    # ---------------------------------------------------------------- meeting
    @app.post("/api/factories/{slug}/meeting")
    async def meeting(slug: str, body: MeetingBody) -> dict[str, Any]:
        from loompa.agents import MasterAgent

        rt = hub.get(slug)
        rt.ctx.index_memory()
        master = MasterAgent(rt.ctx)
        result = await master.meeting(body.goals)
        if body.run:
            if result["stories"]:
                master.start_sprint([s["id"] for s in result["stories"]])
            await hub.start_engine(slug)
        return result

    # ----------------------------------------------------------------- sprints
    @app.get("/api/factories/{slug}/sprints")
    def sprints(slug: str) -> list[dict[str, Any]]:
        ctx = hub.get(slug).ctx
        board = SprintBoard(ctx.store, slug)
        return [{**sp.model_dump(), "progress": board.progress(sp)} for sp in board.sprints()]

    @app.post("/api/factories/{slug}/sprints/start")
    async def start_sprint(slug: str, body: SprintBody) -> dict[str, Any]:
        from loompa.agents import MasterAgent

        rt = hub.get(slug)
        try:
            sprint = MasterAgent(rt.ctx).start_sprint(
                body.story_ids or None, goal=body.goal, limit=body.limit
            )
        except SprintError as exc:
            raise HTTPException(409, str(exc)) from None
        if body.run:
            await hub.start_engine(slug)
        board = SprintBoard(rt.ctx.store, slug)
        return {**sprint.model_dump(), "progress": board.progress(sprint)}

    @app.post("/api/factories/{slug}/transcribe")
    async def transcribe(slug: str, audio: UploadFile = File(...)) -> dict[str, Any]:
        """Local voice dictation for the morning meeting (requires the `voice` extra)."""
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError:
            raise HTTPException(
                501, "ditado por voz requer `pip install loompa-core[voice]`"
            ) from None
        import tempfile

        data = await audio.read()
        with tempfile.NamedTemporaryFile(
            suffix=Path(audio.filename or "a.webm").suffix, delete=False
        ) as fh:
            fh.write(data)
            path = fh.name
        model = WhisperModel("small", device="cpu", compute_type="int8")
        segments, _ = await asyncio.to_thread(
            model.transcribe, path, language=hub.get(slug).ctx.config.factory.language[:2]
        )
        return {"text": " ".join(s.text.strip() for s in segments)}

    @app.post("/api/factories/{slug}/report")
    def report(slug: str) -> dict[str, Any]:
        from loompa.agents import MasterAgent

        msg = MasterAgent(hub.get(slug).ctx).end_of_day_report()
        return msg.model_dump(mode="json")

    # --------------------------------------------------------------- settings
    @app.get("/api/factories/{slug}/settings")
    def settings(slug: str) -> dict[str, Any]:
        from loompa.config.settings import describe_settings

        ctx = hub.get(slug).ctx
        return describe_settings(ctx.config, ctx.secrets)

    @app.put("/api/factories/{slug}/settings")
    def update_settings(slug: str, patch: SettingsPatch) -> dict[str, Any]:
        from loompa.config import SecretInConfigError
        from loompa.config.settings import apply_settings, describe_settings

        rt = hub.get(slug)
        ctx = rt.ctx
        try:
            notes = apply_settings(ctx.root, ctx.config, patch)
            rt.factory.save()
        except (ValueError, SecretInConfigError) as exc:
            raise HTTPException(400, str(exc)) from None
        ctx.reload_secrets()  # new keys/base URLs apply on the next LLM call, no restart
        ctx.emit("settings.updated", changes=notes)
        return {"changes": notes, "settings": describe_settings(ctx.config, ctx.secrets)}

    @app.post("/api/factories/{slug}/settings/providers/{name}/test")
    async def test_provider(slug: str, name: str, body: ProbeBody | None = None) -> dict[str, Any]:
        from loompa.llm import probe_provider, probe_tavily

        ctx = hub.get(slug).ctx
        ctx.reload_secrets()
        if name == "tavily":
            r = await probe_tavily(ctx.config.tools.tavily, secrets=ctx.secrets)
        else:
            r = await probe_provider(
                ctx.config, name, secrets=ctx.secrets, model=body.model if body else None
            )
        return r.as_dict()

    # ---------------------------------------------------------------- finance
    @app.get("/api/factories/{slug}/finance")
    def finance(slug: str) -> dict[str, Any]:
        ctx = hub.get(slug).ctx
        return {
            "daily": ctx.tracker.daily_report(),
            "month": {
                "totals": ctx.store.usage_totals(slug, since_iso=month_start_iso()),
                "by_model": ctx.store.usage_by("model", slug, month_start_iso()),
                "by_agent": ctx.store.usage_by("agent", slug, month_start_iso()),
            },
            "suggestions": ctx.tracker.suggestions(),
        }

    @app.get("/api/factories/{slug}/agents/{name}")
    def agent(slug: str, name: str) -> dict[str, Any]:
        ctx = hub.get(slug).ctx
        row = next((a for a in ctx.store.list_agents() if a["name"] == name), None) or {
            "name": name,
            "role": "",
            "state": "IDLE",
            "story_id": None,
            "model": "",
            "detail": "",
        }
        usage_rows = [
            r for r in ctx.store.usage_by("agent", slug, today_start_iso()) if r["key"] == name
        ]
        month_rows = [
            r for r in ctx.store.usage_by("agent", slug, month_start_iso()) if r["key"] == name
        ]
        story = ctx.store.get_story(row["story_id"]) if row.get("story_id") else None
        role = row.get("role") or next((r for n, r in DEFAULT_AGENTS if n == name), "")
        tier = ctx.config.models.tier_for(role)  # unknown roles map to tier2 like any new role
        return {
            **row,
            "role": role,
            "tier": tier,
            "tiers": list(ctx.config.models.tiers),
            "candidates": [c.model_dump() for c in ctx.config.models.tiers.get(tier, [])],
            "today": usage_rows[0] if usage_rows else None,
            "month": month_rows[0] if month_rows else None,
            "story": _story_card(story) if story else None,
            "worktree": story.get("worktree") if story else None,
        }

    @app.get("/api/factories/{slug}/kaizen")
    def kaizen(slug: str) -> list[dict[str, Any]]:
        return hub.get(slug).ctx.store.list_learnings()

    @app.get("/api/factories/{slug}/memory/search")
    def memory_search(slug: str, q: str, k: int = 5) -> list[dict[str, Any]]:
        ctx = hub.get(slug).ctx
        return [
            {
                "doc_id": h.chunk.doc_id,
                "kind": h.chunk.kind,
                "title": h.chunk.title,
                "score": h.score,
                "text": h.chunk.text[:600],
            }
            for h in ctx.memory.search(q, top_k=k)
        ]

    @app.get("/api/factories/{slug}/events")
    def events(slug: str, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        return hub.get(slug).ctx.store.events_since(after, limit=limit, factory=slug)

    # ------------------------------------------------------------- websocket
    @app.websocket("/ws")
    async def ws(websocket: WebSocket, factory: str | None = None) -> None:
        await websocket.accept()
        try:
            slug = hub.active_slug(factory)
            rt = hub.get(slug)
        except HTTPException as exc:
            await websocket.send_text(json.dumps({"type": "error", "detail": exc.detail}))
            await websocket.close()
            return
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        rt.listeners.add(queue)
        await websocket.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "factory": slug,
                    "engine": hub.engine_running(slug),
                    "ts": datetime.now(UTC).isoformat(),
                }
            )
        )
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                    await websocket.send_text(json.dumps(event, default=str))
                except TimeoutError:
                    await websocket.send_text(json.dumps({"type": "ping"}))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            rt.listeners.discard(queue)

    # ---------------------------------------------------------------- static
    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def index() -> Any:
        index_file = STATIC_DIR / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        return HTMLResponse(
            "<h1>Loompa LTDA HQ</h1><p>Interface não compilada. Rode <code>cd dashboard-ui && npm install && npm run build</code>."
            "<br>API disponível em <a href='/docs'>/docs</a>.</p>"
        )

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> Any:
        if path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = STATIC_DIR / path
        if candidate.is_file():
            return FileResponse(candidate)
        index_file = STATIC_DIR / "index.html"
        return (
            FileResponse(index_file)
            if index_file.is_file()
            else JSONResponse({"detail": "not found"}, status_code=404)
        )

    return app


def _story_card(s: dict[str, Any]) -> dict[str, Any]:
    st = s.get("state") or {}
    return {
        "id": s["id"],
        "title": s["title"],
        "epic": s.get("epic", ""),
        "stage": s["stage"],
        "column": kanban_column(s["stage"]),
        "priority": s.get("priority", 100),
        "origin": s.get("origin", "founder"),
        "cost_usd": round(float(s.get("cost_usd") or 0), 4),
        "blocked_reason": st.get("blocked_reason"),
        "blocked_message_id": st.get("blocked_message_id"),
        "current_tier": st.get("current_tier", "tier2"),
        "kind": st.get("kind", "feature"),
        "complexity": st.get("complexity", "STANDARD"),
        "phase": st.get("phase", ""),
        "route": st.get("route") or [],
        "qa_verdict": st.get("qa_verdict"),
        "attempts": {"tier2": s.get("attempts_tier2", 0), "tier1": s.get("attempts_tier1", 0)},
        "tasks_done": len(st.get("tasks_done") or []),
        "tasks_total": st.get("tasks_total", 0),
        "branch": s.get("branch", ""),
        "pr_url": st.get("pr_url"),
        "updated_at": s.get("updated_at", ""),
    }


def _agent_cost(ctx: EngineContext, name: str) -> float:
    rows = [r for r in ctx.store.usage_by("agent", ctx.slug, today_start_iso()) if r["key"] == name]
    return round(float(rows[0]["cost_usd"]), 4) if rows else 0.0


def _last_event_id(ctx: EngineContext) -> int:
    rows = ctx.store._q("SELECT COALESCE(MAX(id),0) AS id FROM events")  # noqa: SLF001 - cheap, internal
    return int(rows[0]["id"]) if rows else 0
