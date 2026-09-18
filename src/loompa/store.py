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
    created_at TEXT NOT NULL
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
        retry_locked(lambda: self._conn.executescript(SCHEMA), what="state.db schema")

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
        else:
            defaults = {
                "description": "",
                "epic": "",
                "priority": 100,
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
        rows = self._q("SELECT COUNT(*) AS n FROM stories WHERE factory = ?", (factory,))
        return f"S-{rows[0]['n'] + 1:03d}"

    @staticmethod
    def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["state"] = json.loads(row.pop("state_json") or "{}")
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

    # ------------------------------------------------------------------- events
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

    # ----------------------------------------------------------------------- kv
    def get(self, key: str, default: str | None = None) -> str | None:
        rows = self._q("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set(self, key: str, value: str) -> None:
        self._x("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
