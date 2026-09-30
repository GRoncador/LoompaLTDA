"""Per-story trace (Fase 8.1): what each model call and each tool call actually carried.

Events stay light because the dashboard reads them; the content goes here. It was what the
`contas` run of 2026-09-30 lacked to see *why* the Worker re-read the same files and with which
prompt the answers were cut, and what the Gemini 429 bug cost two runs of guessing to find. One
place instruments it all (the router, the tool loop and the engine's nodes) and spans nest by
context, so no caller passes a parent around:

    node → task → round of the tool loop → model call
                                         → tool call

A model call records the model asked for and the one that answered, the provider that served
it, the parameters, the messages it got and its answer, usage and cost, `finish_reason`, latency
and every attempt (cuts retried with more room, candidates that failed before the one that
answered). A tool call records its arguments, the size of what it put in the context, its error
and the LoopGuard's note. The `llm.call` and `tool.call` events carry the span id.

Storage: `.loompa/traces/<story>.jsonl`, one line per finished span (children before parents)
and one line per distinct message, written once and then referenced by hash: the tool loop
re-sends the same prefix every round, so storing each prompt whole would have cost ~140 MB for
the 3.4k calls of `contas`. Every line goes through the same secret redaction as the logs. Calls
that belong to no story (chats, meetings) go to `_factory-<date>.jsonl`. Files untouched for
`trace.retention_days` are removed when an engine context is built.

Always on, dry-run included. Developer-facing: `loompa trace` reads it; the founder never sees
it, not even in the dashboard.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FACTORY_TRACE = "_factory"
MAX_MESSAGE_CHARS = 100_000  # one message stored in the trace (a whole file written, say)
MAX_ATTR_CHARS = 2_000  # one string attribute of a span (tool arguments, errors)
_MSG_PREFIX = '{"t":"msg","h":"'

_current: ContextVar[Span | None] = ContextVar("loompa_trace_span", default=None)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _new_id(n_bytes: int) -> str:
    return os.urandom(n_bytes).hex()


def _cap(value: Any, limit: int = MAX_ATTR_CHARS) -> Any:
    """Attributes stay readable: long strings are cut with a note of how much was left out."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"… (+{len(value) - limit} chars)"
    if isinstance(value, dict):
        return {str(k): _cap(v, limit) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_cap(v, limit) for v in value]
    return value


@dataclass
class Span:
    id: str  # 8 bytes hex, the OpenTelemetry span id size
    trace_id: str  # 16 bytes hex, shared by every span under the same node
    parent_id: str | None
    story_id: str | None
    kind: str  # node | task | round | llm | tool
    name: str
    start: str
    attrs: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"  # ok | error | cancelled
    error: str = ""
    _t0: float = field(default_factory=time.monotonic, repr=False)

    def set(self, **attrs: Any) -> None:
        self.attrs.update({k: v for k, v in attrs.items() if v is not None})

    def fail(self, error: str) -> None:
        self.status = "error"
        self.error = error[:MAX_ATTR_CHARS]

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)


def message_doc(message: Any) -> dict[str, Any]:
    """A `loompa.llm.Message` as the trace stores it: only the fields that carry something."""
    doc: dict[str, Any] = {"role": getattr(message, "role", ""), "content": message.content or ""}
    calls = getattr(message, "tool_calls", None) or []
    if calls:
        doc["tool_calls"] = [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in calls]
    for key in ("tool_call_id", "name"):
        value = getattr(message, key, None)
        if value:
            doc[key] = value
    return doc


def message_hash(doc: dict[str, Any]) -> str:
    raw = json.dumps(doc, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _stem(story_id: str | None) -> str:
    if story_id:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", story_id)
    return f"{FACTORY_TRACE}-{datetime.now(UTC):%Y-%m-%d}"


class Tracer:
    """Hands out spans and writes them for one factory. Without a directory it still hands out
    spans (ids, nesting) and writes nothing, so a router built outside a factory keeps working.
    Tracing never breaks the work it observes: a write that fails is logged once and dropped."""

    def __init__(
        self, directory: Path | None = None, *, redact: Callable[[str], str] | None = None
    ):
        self.directory = directory
        self._redact = redact or (lambda text: text)
        self._lock = threading.Lock()
        self._seen: dict[Path, set[str]] = {}  # file -> message hashes it already holds
        self._ready = False
        self._warned = False

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def set_redaction(self, redact: Callable[[str], str]) -> None:
        self._redact = redact

    @staticmethod
    def current() -> Span | None:
        return _current.get()

    def path_for(self, story_id: str | None) -> Path | None:
        return self.directory / f"{_stem(story_id)}.jsonl" if self.directory else None

    # ------------------------------------------------------------------ spans
    @contextmanager
    def span(
        self, kind: str, name: str, *, story_id: str | None = None, **attrs: Any
    ) -> Generator[Span]:
        """Open a span under the current one (by context). A span for another story than its
        parent's starts a tree of its own in that story's file."""
        outer = _current.get()
        parent = outer
        if parent is not None and story_id is not None and parent.story_id != story_id:
            parent = None
        sp = Span(
            id=_new_id(8),
            trace_id=parent.trace_id if parent else _new_id(16),
            parent_id=parent.id if parent else None,
            story_id=story_id if story_id is not None else (parent.story_id if parent else None),
            kind=kind,
            name=name,
            start=_now(),
        )
        sp.set(**attrs)
        token = _current.set(sp)
        try:
            yield sp
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                sp.status = "cancelled"
            elif sp.status == "ok":
                sp.status = "error"
            sp.error = sp.error or f"{type(exc).__name__}: {exc}"[:MAX_ATTR_CHARS]
            raise
        finally:
            try:
                _current.reset(token)
            except ValueError:  # closed in another context than it was opened in
                _current.set(outer)
            self._write_span(sp)

    def _write_span(self, sp: Span) -> None:
        path = self.path_for(sp.story_id)
        if path is None:
            return
        record = {
            "t": "span",
            "id": sp.id,
            "trace": sp.trace_id,
            "parent": sp.parent_id,
            "story": sp.story_id,
            "kind": sp.kind,
            "name": sp.name,
            "start": sp.start,
            "end": _now(),
            "ms": sp.elapsed_ms,
            "status": sp.status,
            **({"error": sp.error} if sp.error else {}),
            "attrs": _cap(sp.attrs),
        }
        self._append(path, [self._line(record)])

    # --------------------------------------------------------------- messages
    def messages(self, story_id: str | None, messages: Sequence[Any]) -> list[str]:
        """Hashes of `messages` in order, writing the ones this story's file does not hold yet.
        The prompt of a round is then a list of references, not a copy of the history."""
        path = self.path_for(story_id)
        if path is None:
            return []
        hashes: list[str] = []
        lines: list[str] = []
        with self._lock:
            seen = self._seen_in(path)
            for m in messages:
                doc = message_doc(m)
                h = message_hash(doc)
                hashes.append(h)
                if h in seen:
                    continue
                seen.add(h)
                lines.append(self._line({"t": "msg", "h": h, **_cap(doc, MAX_MESSAGE_CHARS)}))
        if lines:
            self._append(path, lines)
        return hashes

    def _seen_in(self, path: Path) -> set[str]:
        """Hashes already in `path` (read once per process; the lock is held by the caller)."""
        seen = self._seen.get(path)
        if seen is None:
            seen = set()
            if path.is_file():
                try:
                    with path.open(encoding="utf-8") as fh:
                        for line in fh:
                            if line.startswith(_MSG_PREFIX):
                                seen.add(line[len(_MSG_PREFIX) : len(_MSG_PREFIX) + 16])
                except OSError:
                    pass
            self._seen[path] = seen
        return seen

    # ----------------------------------------------------------------- writing
    def _line(self, record: dict[str, Any]) -> str:
        text = json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str)
        return self._redact(text) + "\n"

    def _append(self, path: Path, lines: list[str]) -> None:
        try:
            with self._lock:
                if not self._ready:
                    self._prepare()
                with path.open("a", encoding="utf-8") as fh:
                    fh.write("".join(lines))
        except OSError:
            if not self._warned:
                self._warned = True
                log.exception("could not write the trace to %s; tracing continues off", path)

    def _prepare(self) -> None:
        assert self.directory is not None
        self.directory.mkdir(parents=True, exist_ok=True)
        # The trace carries product code and prompts: it never goes into the repository, and an
        # ignore file inside the folder covers factories onboarded before it existed.
        ignore = self.directory / ".gitignore"
        if not ignore.exists():
            ignore.write_text("# Loompa traces carry product code: never committed\n*\n")
        self._ready = True

    def prune(self, retention_days: int) -> int:
        """Remove trace files untouched for `retention_days`. Returns how many went."""
        if self.directory is None or not self.directory.is_dir():
            return 0
        cutoff = time.time() - retention_days * 86_400
        removed = 0
        for path in self.directory.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    self._seen.pop(path, None)
                    removed += 1
            except OSError:
                continue
        return removed


# ------------------------------------------------------------------------------ reading


@dataclass
class TraceNode:
    span: dict[str, Any]
    children: list[TraceNode] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return str(self.span.get("kind", ""))

    @property
    def attrs(self) -> dict[str, Any]:
        return self.span.get("attrs") or {}

    def walk(self) -> Iterator[TraceNode]:
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class Trace:
    spans: list[dict[str, Any]]
    messages: dict[str, dict[str, Any]]

    def tree(self) -> list[TraceNode]:
        """Root spans (nodes, and calls made outside any node) with their children, oldest first.
        A span whose parent never finished (a run that was killed) is shown as a root."""
        nodes = {s["id"]: TraceNode(s) for s in self.spans if s.get("id")}
        roots: list[TraceNode] = []
        for node in nodes.values():
            parent = nodes.get(node.span.get("parent") or "")
            (parent.children if parent else roots).append(node)
        for node in nodes.values():
            node.children.sort(key=lambda n: str(n.span.get("start", "")))
        roots.sort(key=lambda n: str(n.span.get("start", "")))
        return roots

    def find(self, span_id: str) -> dict[str, Any] | None:
        return next((s for s in self.spans if str(s.get("id", "")).startswith(span_id)), None)


def read_trace(path: Path) -> Trace:
    spans: list[dict[str, Any]] = []
    messages: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return Trace(spans, messages)
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except ValueError:
                continue  # a line cut by a crash mid-write
            if record.get("t") == "span":
                spans.append(record)
            elif record.get("t") == "msg" and record.get("h"):
                messages[record["h"]] = record
    return Trace(spans, messages)


def trace_dir(factory_root: Path) -> Path:
    return Path(factory_root) / ".loompa" / "traces"
