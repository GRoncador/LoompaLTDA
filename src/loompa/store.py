"""SQLite persistence for one factory: stories, checkpoints, events, inbox, usage, agents.

One file (`.loompa/state.db`), plain rows a human can query. Synchronous `sqlite3` guarded by
a lock: every operation is a few milliseconds on a local file, so the asyncio engine calls it
directly without a thread pool.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loompa.comms import FounderAnswer, FounderMessage, MessageStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id TEXT PRIMARY KEY,
    factory TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    epic TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    priority_pinned INTEGER NOT NULL DEFAULT 0,
    depends_on TEXT NOT NULL DEFAULT '[]',
    origin TEXT NOT NULL DEFAULT 'founder',
    state_json TEXT NOT NULL DEFAULT '{}',
    attempts_tier2 INTEGER NOT NULL DEFAULT 0,
    attempts_tier1 INTEGER NOT NULL DEFAULT 0,
    branch TEXT NOT NULL DEFAULT '',
    worktree TEXT NOT NULL DEFAULT '',
    blocked_message_id TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT NOT NULL,
    node TEXT NOT NULL,
    stage TEXT NOT NULL,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_checkpoints_story ON checkpoints(story_id, id);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    factory TEXT NOT NULL,
    story_id TEXT,
    agent TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_id ON events(id);
CREATE INDEX IF NOT EXISTS ix_events_story ON events(story_id, id);
CREATE TABLE IF NOT EXISTS inbox (
    id TEXT PRIMARY KEY,
    factory TEXT NOT NULL,
    story_id TEXT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    message_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    answered_at TEXT
);
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    factory TEXT NOT NULL,
    story_id TEXT,
    agent TEXT NOT NULL,
    role TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    tier TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    served_by TEXT NOT NULL DEFAULT '',
    finish_reason TEXT NOT NULL DEFAULT '',
    cost_source TEXT NOT NULL DEFAULT '',
    span_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_usage_created ON usage(created_at);
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'IDLE',
    story_id TEXT,
    model TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS learnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id TEXT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_story_id TEXT,
    promoted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sprints (
    id TEXT PRIMARY KEY,
    factory TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    story_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    started_at TEXT,
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    factory TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


log = logging.getLogger("loompa.store")

# SQLite returns SQLITE_BUSY without honouring `busy_timeout` in two cases: a deferred
# transaction trying to upgrade to a write lock, and a WAL snapshot that another connection
# has moved on from. Both are transient; retrying the statement is the documented remedy.
LOCK_RETRIES = 6
LOCK_BACKOFF_S = 0.05


def is_lock_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in text or "busy" in text)


def retry_locked(fn: Callable[[], Any], *, what: str = "sqlite") -> Any:
    """Run `fn`, retrying with exponential backoff while SQLite reports a lock."""
    for attempt in range(LOCK_RETRIES + 1):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not is_lock_error(exc) or attempt >= LOCK_RETRIES:
                raise
            wait = LOCK_BACKOFF_S * (2**attempt)
            log.warning(
                "%s locked, retrying in %.2fs (%d/%d)", what, wait, attempt + 1, LOCK_RETRIES
            )
            time.sleep(wait)
    raise AssertionError("unreachable")


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _row(cur: sqlite3.Cursor, row: tuple) -> dict[str, Any]:
    return {d[0]: row[i] for i, d in enumerate(cur.description)}


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(path), check_same_thread=False, isolation_level=None, timeout=30.0
        )
        self._lock = threading.RLock()
        retry_locked(lambda: self._conn.execute("PRAGMA journal_mode=WAL"), what="state.db")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        retry_locked(self._migrate, what="state.db migration")
        retry_locked(lambda: self._conn.executescript(SCHEMA), what="state.db schema")

    # Columns added after a table first shipped: a factory's state.db from before gets them on
    # open, with the defaults new rows would have. `CREATE TABLE IF NOT EXISTS` never adds any.
    ADDED_COLUMNS = {
        # the founder dragged the card into place: the Product Owner's ranking leaves it there
        "stories": (
            ("priority_pinned", "INTEGER NOT NULL DEFAULT 0"),
            # the cards this one needs delivered first; only the Product Owner writes it (ADR-0021)
            ("depends_on", "TEXT NOT NULL DEFAULT '[]'"),
        ),
        "usage": (
            ("served_by", "TEXT NOT NULL DEFAULT ''"),
            ("finish_reason", "TEXT NOT NULL DEFAULT ''"),
            ("cost_source", "TEXT NOT NULL DEFAULT ''"),
            ("span_id", "TEXT NOT NULL DEFAULT ''"),
        ),
    }

    def _migrate(self) -> None:
        for table, columns in self.ADDED_COLUMNS.items():
            have = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if not have:
                continue  # a new database: the schema below creates the table whole
            for name, decl in columns:
                if name not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            # IMMEDIATE takes the write lock up front (and waits for it), so the transaction
            # can never hit the un-retryable "deferred upgrade" SQLITE_BUSY.
            retry_locked(lambda: self._conn.execute("BEGIN IMMEDIATE"), what="state.db tx")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def _q(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        def run() -> list[dict[str, Any]]:
            cur = self._conn.execute(sql, params)
            return [_row(cur, r) for r in cur.fetchall()]

        with self._lock:
            return retry_locked(run, what="state.db")

    def _x(self, sql: str, params: tuple = ()) -> int:
        def run() -> int:
            cur = self._conn.execute(sql, params)
            return cur.lastrowid or cur.rowcount

        with self._lock:
            return retry_locked(run, what="state.db")

    # ------------------------------------------------------------------ stories
    def upsert_story(self, story: dict[str, Any]) -> None:
        story = dict(story)
        story.setdefault("created_at", now_iso())
        story["updated_at"] = now_iso()
        story["state_json"] = json.dumps(story.pop("state", {}), ensure_ascii=False, default=str)
        cols = [
            "id",
            "factory",
            "title",
            "description",
            "epic",
            "stage",
            "priority",
            "priority_pinned",
            "depends_on",
            "origin",
            "state_json",
            "attempts_tier2",
            "attempts_tier1",
            "branch",
            "worktree",
            "blocked_message_id",
            "cost_usd",
            "created_at",
            "updated_at",
        ]
        existing = self.get_story(story["id"])
        if existing:
            for c in cols:
                story.setdefault(
                    c,
                    existing.get(c) if c != "state_json" else json.dumps(existing.get("state", {})),
                )
            story["created_at"] = existing["created_at"]
            if isinstance(story["depends_on"], list):
                story["depends_on"] = json.dumps(story["depends_on"])
        else:
            defaults = {
                "description": "",
                "epic": "",
                "priority": 100,
                "priority_pinned": 0,
                "depends_on": "[]",
                "origin": "founder",
                "attempts_tier2": 0,
                "attempts_tier1": 0,
                "branch": "",
                "worktree": "",
                "blocked_message_id": None,
                "cost_usd": 0.0,
            }
            for c in cols:
                story.setdefault(c, defaults.get(c))
        placeholders = ",".join("?" for _ in cols)
        self._x(
            f"INSERT OR REPLACE INTO stories ({','.join(cols)}) VALUES ({placeholders})",
            tuple(story[c] for c in cols),
        )

    def update_story(self, story_id: str, **fields: Any) -> None:
        if "state" in fields:
            fields["state_json"] = json.dumps(fields.pop("state"), ensure_ascii=False, default=str)
        if isinstance(fields.get("depends_on"), list):
            fields["depends_on"] = json.dumps(fields["depends_on"])
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._x(f"UPDATE stories SET {sets} WHERE id = ?", (*fields.values(), story_id))

    def get_story(self, story_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM stories WHERE id = ?", (story_id,))
        return self._hydrate(rows[0]) if rows else None

    def list_stories(
        self, factory: str | None = None, stage: str | None = None
    ) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM stories", []
        clauses = []
        if factory:
            clauses.append("factory = ?")
            params.append(factory)
        if stage:
            clauses.append("stage = ?")
            params.append(stage)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY priority ASC, created_at ASC"
        return [self._hydrate(r) for r in self._q(sql, tuple(params))]

    def next_story_id(self, factory: str) -> str:
        return f"S-{self._next_number('stories', 'S-', factory):03d}"

    def _next_number(self, table: str, prefix: str, factory: str) -> int:
        """The highest number already used + 1, never the row count: a deleted row would make
        the count hand out an id that still exists. Compared as integers, because as text
        "S-1000" sorts before "S-999". `:03d` is a minimum width, so S-1000 prints whole."""
        start = len(prefix) + 1
        rows = self._q(
            f"SELECT MAX(CAST(SUBSTR(id, {start}) AS INTEGER)) AS n FROM {table} "
            "WHERE factory = ? AND id LIKE ?",
            (factory, f"{prefix}%"),
        )
        return (rows[0]["n"] or 0) + 1

    @staticmethod
    def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["state"] = json.loads(row.pop("state_json") or "{}")
        if "depends_on" in row:
            row["depends_on"] = json.loads(row["depends_on"] or "[]")
        return row

    # -------------------------------------------------------------- checkpoints
    def checkpoint(self, story_id: str, node: str, stage: str, state: dict[str, Any]) -> int:
        return self._x(
            "INSERT INTO checkpoints (story_id, node, stage, state_json, created_at) VALUES (?,?,?,?,?)",
            (story_id, node, stage, json.dumps(state, ensure_ascii=False, default=str), now_iso()),
        )

    def last_checkpoint(self, story_id: str) -> dict[str, Any] | None:
        rows = self._q(
            "SELECT * FROM checkpoints WHERE story_id = ? ORDER BY id DESC LIMIT 1", (story_id,)
        )
        if not rows:
            return None
        row = rows[0]
        row["state"] = json.loads(row.pop("state_json"))
        return row

    def checkpoints(self, story_id: str) -> list[dict[str, Any]]:
        rows = self._q(
            "SELECT id, node, stage, created_at FROM checkpoints WHERE story_id = ? ORDER BY id",
            (story_id,),
        )
        return rows

    def checkpoint_states(self, story_id: str) -> list[dict[str, Any]]:
        """Every checkpoint with the state it saved, oldest first (the history tab, 11.3)."""
        rows = self._q(
            "SELECT id, node, stage, state_json, created_at FROM checkpoints WHERE story_id = ? "
            "ORDER BY id",
            (story_id,),
        )
        for r in rows:
            r["state"] = json.loads(r.pop("state_json") or "{}")
        return rows

    # ------------------------------------------------------------------- events
    def story_events(self, story_id: str, types: tuple[str, ...]) -> list[dict[str, Any]]:
        """One story's events of the given types, oldest first."""
        rows = self._q(
            f"SELECT id, type, payload_json, created_at FROM events WHERE story_id = ? AND type IN "
            f"({','.join('?' * len(types))}) ORDER BY id",
            (story_id, *types),
        )
        for r in rows:
            r["payload"] = json.loads(r.pop("payload_json") or "{}")
        return rows

    def emit(
        self,
        factory: str,
        type_: str,
        *,
        story_id: str | None = None,
        agent: str = "",
        **payload: Any,
    ) -> int:
        return self._x(
            "INSERT INTO events (factory, story_id, agent, type, payload_json, created_at) VALUES (?,?,?,?,?,?)",
            (
                factory,
                story_id,
                agent,
                type_,
                json.dumps(payload, ensure_ascii=False, default=str),
                now_iso(),
            ),
        )

    def events_since(
        self, after_id: int = 0, limit: int = 200, factory: str | None = None
    ) -> list[dict[str, Any]]:
        if factory:
            rows = self._q(
                "SELECT * FROM events WHERE id > ? AND factory = ? ORDER BY id LIMIT ?",
                (after_id, factory, limit),
            )
        else:
            rows = self._q(
                "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT ?", (after_id, limit)
            )
        for r in rows:
            r["payload"] = json.loads(r.pop("payload_json") or "{}")
        return rows

    def events_between(self, factory: str, start: str, end: str) -> list[dict[str, Any]]:
        """Every event of a factory in [start, end], oldest first: a sprint's window (8.2)."""
        rows = self._q(
            "SELECT id, story_id, agent, type, payload_json, created_at FROM events "
            "WHERE factory = ? AND created_at >= ? AND created_at <= ? AND type NOT IN "
            "('llm.progress', 'agent.state') ORDER BY id",
            (factory, start, end),
        )
        for r in rows:
            r["payload"] = json.loads(r.pop("payload_json") or "{}")
        return rows

    def usage_between(self, factory: str, start: str, end: str) -> list[dict[str, Any]]:
        """Every model call of a factory in [start, end]: a sprint's window (8.2)."""
        return self._q(
            "SELECT story_id, role, model, input_tokens, output_tokens, cost_usd, duration_ms, "
            "finish_reason, cost_source, span_id, created_at FROM usage "
            "WHERE factory = ? AND created_at >= ? AND created_at <= ? ORDER BY id",
            (factory, start, end),
        )

    def usage_window(self, factory: str, start: str, end: str) -> list[dict[str, Any]]:
        """Every model call of a factory in [start, end] with every column (`loompa costs`)."""
        return self._q(
            "SELECT * FROM usage WHERE factory = ? AND created_at >= ? AND created_at <= ? "
            "ORDER BY id",
            (factory, start, end),
        )

    # -------------------------------------------------------------------- inbox
    def put_message(self, msg: FounderMessage) -> None:
        self._x(
            "INSERT OR REPLACE INTO inbox (id, factory, story_id, kind, status, message_json, created_at, answered_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                msg.id,
                msg.factory,
                msg.story_id,
                msg.kind.value,
                msg.status.value,
                msg.model_dump_json(),
                msg.created_at.isoformat(),
                msg.answer.answered_at.isoformat() if msg.answer else None,
            ),
        )

    def get_message(self, message_id: str) -> FounderMessage | None:
        rows = self._q("SELECT message_json FROM inbox WHERE id = ?", (message_id,))
        return FounderMessage.model_validate_json(rows[0]["message_json"]) if rows else None

    def list_messages(
        self, factory: str | None = None, status: str | None = None, limit: int = 200
    ) -> list[FounderMessage]:
        sql, params, clauses = "SELECT message_json FROM inbox", [], []
        if factory:
            clauses.append("factory = ?")
            params.append(factory)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [
            FounderMessage.model_validate_json(r["message_json"])
            for r in self._q(sql, tuple(params))
        ]

    def answer_message(self, message_id: str, answer: FounderAnswer) -> FounderMessage:
        msg = self.get_message(message_id)
        if msg is None:
            raise KeyError(message_id)
        msg.answer = answer
        msg.status = MessageStatus.ANSWERED
        self.put_message(msg)
        return msg

    def archive_message(self, message_id: str) -> None:
        msg = self.get_message(message_id)
        if msg:
            msg.status = MessageStatus.ARCHIVED
            self.put_message(msg)

    # -------------------------------------------------------------------- usage
    def record_usage(self, **row: Any) -> int:
        row.setdefault("created_at", now_iso())
        cols = list(row)
        return self._x(
            f"INSERT INTO usage ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            tuple(row.values()),
        )

    def usage_totals(
        self, factory: str | None = None, since_iso: str | None = None, story_id: str | None = None
    ) -> dict[str, Any]:
        clauses, params = [], []
        if factory:
            clauses.append("factory = ?")
            params.append(factory)
        if since_iso:
            clauses.append("created_at >= ?")
            params.append(since_iso)
        if story_id:
            clauses.append("story_id = ?")
            params.append(story_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._q(
            f"SELECT COALESCE(SUM(cost_usd),0) AS cost_usd, COALESCE(SUM(input_tokens),0) AS input_tokens, "
            f"COALESCE(SUM(output_tokens),0) AS output_tokens, COALESCE(SUM(cached_tokens),0) AS cached_tokens, COUNT(*) AS calls FROM usage{where}",
            tuple(params),
        )
        return rows[0]

    def usage_by(
        self, column: str, factory: str | None = None, since_iso: str | None = None
    ) -> list[dict[str, Any]]:
        assert column in ("agent", "role", "model", "story_id", "tier", "provider")
        clauses, params = [], []
        if factory:
            clauses.append("factory = ?")
            params.append(factory)
        if since_iso:
            clauses.append("created_at >= ?")
            params.append(since_iso)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return self._q(
            f"SELECT {column} AS key, SUM(cost_usd) AS cost_usd, SUM(input_tokens) AS input_tokens, "
            f"SUM(output_tokens) AS output_tokens, COUNT(*) AS calls FROM usage{where} GROUP BY {column} ORDER BY cost_usd DESC",
            tuple(params),
        )

    def escalated_cost(self, factory: str, since_iso: str | None = None) -> list[dict[str, Any]]:
        """Tier 1 cost of each story after it was escalated, newest escalation first. Tier 1 by
        itself is not an escalation: the Master and the Architect run on it by role."""
        rows = self._q(
            "SELECT u.story_id AS story_id, SUM(u.cost_usd) AS cost_usd, COUNT(*) AS calls, "
            "e.at AS escalated_at FROM usage u JOIN (SELECT story_id, MIN(created_at) AS at "
            "FROM events WHERE factory = ? AND type = 'story.escalated' AND story_id IS NOT NULL "
            "GROUP BY story_id) e ON u.story_id = e.story_id WHERE u.factory = ? "
            "AND u.tier = 'tier1' AND u.created_at >= e.at AND u.created_at >= ? "
            "GROUP BY u.story_id ORDER BY e.at DESC",
            (factory, factory, since_iso or ""),
        )
        return rows

    def story_ids_with_event(self, factory: str, type_: str) -> set[str]:
        rows = self._q(
            "SELECT DISTINCT story_id FROM events WHERE factory = ? AND type = ? "
            "AND story_id IS NOT NULL",
            (factory, type_),
        )
        return {r["story_id"] for r in rows}

    def usage_by_day(
        self, factory: str | None = None, since_iso: str | None = None
    ) -> list[dict[str, Any]]:
        """Cost and calls per UTC day, oldest first, for the dashboard's cost chart."""
        clauses, params = [], []
        if factory:
            clauses.append("factory = ?")
            params.append(factory)
        if since_iso:
            clauses.append("created_at >= ?")
            params.append(since_iso)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return self._q(
            f"SELECT substr(created_at, 1, 10) AS day, SUM(cost_usd) AS cost_usd, COUNT(*) AS calls, "
            f"SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens "
            f"FROM usage{where} GROUP BY day ORDER BY day",
            tuple(params),
        )

    def usage_stats(
        self,
        factory: str | None = None,
        *,
        story_id: str | None = None,
        since_iso: str | None = None,
    ) -> list[dict[str, Any]]:
        """Time and cost of the model calls per role and model, with the 90th percentile of the
        latency and how many answers were cut: two tier-2 models compared on the same role."""
        clauses, params = [], []
        for column, value in (("factory", factory), ("story_id", story_id)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        if since_iso:
            clauses.append("created_at >= ?")
            params.append(since_iso)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._q(
            "SELECT role, model, duration_ms, cost_usd, input_tokens, output_tokens, "
            f"finish_reason, cost_source FROM usage{where}",
            tuple(params),
        )
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for r in rows:
            groups.setdefault((r["role"], r["model"]), []).append(r)
        out = []
        for (role, model), items in groups.items():
            times = sorted(int(i["duration_ms"] or 0) for i in items)
            out.append(
                {
                    "role": role,
                    "model": model,
                    "calls": len(items),
                    "cost_usd": sum(float(i["cost_usd"] or 0) for i in items),
                    "input_tokens": sum(int(i["input_tokens"] or 0) for i in items),
                    "output_tokens": sum(int(i["output_tokens"] or 0) for i in items),
                    "total_ms": sum(times),
                    "avg_ms": sum(times) // len(times),
                    "p90_ms": times[min(len(times) - 1, int(len(times) * 0.9))],
                    "cuts": sum(1 for i in items if i["finish_reason"] in ("length", "max_tokens")),
                    "reported": sum(1 for i in items if i["cost_source"] == "reported"),
                }
            )
        return sorted(out, key=lambda r: (r["role"], -r["cost_usd"]))

    def story_activity(self, story_id: str) -> dict[str, Any] | None:
        """What a running story is doing right now, from its latest events: the task it is on
        (and who put it there), how many tool calls that task made, the last one, and when the
        story last said anything. The dashboard's live line on the card (Fase 8.5)."""
        last = self._q(
            "SELECT id, type, agent, payload_json, created_at FROM events WHERE story_id = ? "
            "AND type != 'story.stalled' ORDER BY id DESC LIMIT 1",
            (story_id,),
        )
        if not last:
            return None
        out: dict[str, Any] = {
            "last_event": last[0]["type"],
            "last_agent": last[0]["agent"],
            "last_at": last[0]["created_at"],
        }
        if last[0]["type"] == "llm.progress":  # a streamed call still writing (ADR-0016 §4)
            out["thinking"] = json.loads(last[0]["payload_json"] or "{}").get("tokens")
        out.update(self._stage_since(story_id))
        started = self._q(
            "SELECT id, payload_json, created_at FROM events WHERE story_id = ? "
            "AND type = 'worker.task_started' ORDER BY id DESC LIMIT 1",
            (story_id,),
        )
        since = 0
        if started:
            p = json.loads(started[0]["payload_json"] or "{}")
            finished = self._q(
                "SELECT 1 FROM events WHERE story_id = ? AND type = 'worker.task_finished' "
                "AND id > ? LIMIT 1",
                (story_id, started[0]["id"]),
            )
            if not finished:
                since = started[0]["id"]
                out.update(task=p.get("task"), task_text=p.get("text"), origin=p.get("origin"))
                out["task_since"] = started[0]["created_at"]  # the card counts from here
        tools = self._q(
            "SELECT COUNT(*) AS n, MAX(id) AS last FROM events WHERE story_id = ? "
            "AND type = 'tool.call' AND id > ?",
            (story_id, since),
        )
        if since and tools and tools[0]["n"]:
            out["calls"] = int(tools[0]["n"])
            row = self._q("SELECT payload_json FROM events WHERE id = ?", (tools[0]["last"],))
            p = json.loads(row[0]["payload_json"] or "{}") if row else {}
            out.update(last_tool=p.get("tool"), last_target=p.get("path") or p.get("query"))
        stalled = self._q(
            "SELECT payload_json FROM events WHERE story_id = ? AND type = 'story.stalled' "
            "AND id > ? ORDER BY id DESC LIMIT 1",
            (story_id, last[0]["id"]),
        )
        out["stalled"] = bool(stalled)
        return out

    def _stage_since(self, story_id: str) -> dict[str, Any]:
        """The stage a story is in and when it entered it: the first `story.stage` after the last
        one that named another stage. The engine says the stage after every node, so a dev retry
        does not restart the clock; going back from TEST to DEV does."""
        last = self._q(
            "SELECT id, json_extract(payload_json, '$.stage') AS stage FROM events "
            "WHERE story_id = ? AND type = 'story.stage' ORDER BY id DESC LIMIT 1",
            (story_id,),
        )
        if not last or not last[0]["stage"]:
            return {}
        stage = last[0]["stage"]
        before = self._q(
            "SELECT MAX(id) AS id FROM events WHERE story_id = ? AND type = 'story.stage' "
            "AND json_extract(payload_json, '$.stage') != ?",
            (story_id, stage),
        )
        entered = self._q(
            "SELECT created_at FROM events WHERE story_id = ? AND type = 'story.stage' AND id > ? "
            "ORDER BY id LIMIT 1",
            (story_id, (before[0]["id"] if before else None) or 0),
        )
        return {"stage": stage, "stage_since": entered[0]["created_at"]} if entered else {}

    def tool_output_by(
        self, factory: str | None = None, since_iso: str | None = None
    ) -> list[dict[str, Any]]:
        """Tokens that tool results brought into agents' context, per agent and tool."""
        clauses, params = ["type = 'tool.call'"], []
        if factory:
            clauses.append("factory = ?")
            params.append(factory)
        if since_iso:
            clauses.append("created_at >= ?")
            params.append(since_iso)
        return self._q(
            "SELECT agent, json_extract(payload_json, '$.tool') AS tool, COUNT(*) AS calls, "
            "COALESCE(SUM(json_extract(payload_json, '$.tokens')), 0) AS tokens FROM events "
            f"WHERE {' AND '.join(clauses)} GROUP BY agent, tool ORDER BY tokens DESC",
            tuple(params),
        )

    # ------------------------------------------------------------------- agents
    def set_agent(
        self,
        name: str,
        role: str,
        state: str,
        *,
        story_id: str | None = None,
        model: str = "",
        detail: str = "",
    ) -> None:
        self._x(
            "INSERT OR REPLACE INTO agents (name, role, state, story_id, model, detail, updated_at) VALUES (?,?,?,?,?,?,?)",
            (name, role, state, story_id, model, detail, now_iso()),
        )

    def list_agents(self) -> list[dict[str, Any]]:
        return self._q("SELECT * FROM agents ORDER BY name")

    # ---------------------------------------------------------------- learnings
    def add_learning(
        self,
        *,
        story_id: str | None,
        kind: str,
        title: str,
        detail: str = "",
        created_story_id: str | None = None,
    ) -> int:
        return self._x(
            "INSERT INTO learnings (story_id, kind, title, detail, created_story_id, created_at) VALUES (?,?,?,?,?,?)",
            (story_id, kind, title, detail, created_story_id, now_iso()),
        )

    def list_learnings(self, since_iso: str | None = None) -> list[dict[str, Any]]:
        if since_iso:
            return self._q(
                "SELECT * FROM learnings WHERE created_at >= ? ORDER BY id DESC", (since_iso,)
            )
        return self._q("SELECT * FROM learnings ORDER BY id DESC")

    # ------------------------------------------------------------------ sprints
    def next_sprint_id(self, factory: str) -> str:
        return f"SP-{self._next_number('sprints', 'SP-', factory):03d}"

    def put_sprint(self, sprint: dict[str, Any]) -> None:
        row = {**sprint, "story_ids_json": json.dumps(sprint["story_ids"])}
        cols = (
            "id",
            "factory",
            "goal",
            "status",
            "story_ids_json",
            "created_at",
            "started_at",
            "closed_at",
        )
        self._x(
            f"INSERT OR REPLACE INTO sprints ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            tuple(row.get(c) for c in cols),
        )

    @staticmethod
    def _sprint(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["story_ids"] = json.loads(row.pop("story_ids_json") or "[]")
        return row

    def get_sprint(self, sprint_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM sprints WHERE id = ?", (sprint_id,))
        return self._sprint(rows[0]) if rows else None

    def list_sprints(
        self, factory: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        sql, params, clauses = "SELECT * FROM sprints", [], []
        if factory:
            clauses.append("factory = ?")
            params.append(factory)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return [self._sprint(r) for r in self._q(sql + " ORDER BY created_at, id", tuple(params))]

    # ------------------------------------------------------------ conversations
    def next_conversation_id(self, factory: str) -> str:
        return f"C-{self._next_number('conversations', 'C-', factory):03d}"

    def put_conversation(self, conv: dict[str, Any]) -> None:
        """Insert or replace a chat session. `turns`, `draft`, `result`... travel as one JSON
        document; the columns are only what the listings filter and sort on."""
        row = dict(conv)
        created = row.get("created_at") or now_iso()
        self._x(
            "INSERT OR REPLACE INTO conversations (id, factory, kind, status, title, data_json, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                row["id"],
                row["factory"],
                row["kind"],
                row["status"],
                row.get("title", ""),
                json.dumps(row, ensure_ascii=False, default=str),
                created,
                now_iso(),
            ),
        )

    @staticmethod
    def _conversation(row: dict[str, Any]) -> dict[str, Any]:
        data = json.loads(row["data_json"] or "{}")
        return {**data, "updated_at": row["updated_at"]}

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        return self._conversation(rows[0]) if rows else None

    def list_conversations(
        self, factory: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        sql, params, clauses = "SELECT * FROM conversations", [], []
        if factory:
            clauses.append("factory = ?")
            params.append(factory)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self._q(sql + " ORDER BY updated_at DESC, id DESC", tuple(params))
        return [self._conversation(r) for r in rows]

    # ----------------------------------------------------------------------- kv
    def get(self, key: str, default: str | None = None) -> str | None:
        rows = self._q("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set(self, key: str, value: str) -> None:
        self._x("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
