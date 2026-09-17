"""Organizational memory: local vector RAG in SQLite (ADRs, constitution, learnings, bug fixes)."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from loompa.memory.embedders import Embedder, HashEmbedder

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    embedding BLOB NOT NULL,
    content_hash TEXT NOT NULL,
    embedder TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunks_doc ON memory_chunks(doc_id);
CREATE TABLE IF NOT EXISTS memory_docs (
    doc_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


@dataclass
class MemoryChunk:
    doc_id: str
    kind: str
    title: str
    chunk_index: int
    text: str
    metadata: dict[str, Any]


@dataclass
class MemoryHit:
    chunk: MemoryChunk
    score: float


def chunk_text(text: str, size: int = 800, overlap: int = 120) -> list[str]:
    """Split on paragraph/heading boundaries, then pack into ~size-char windows."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=#+ )", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= size:
            buf = f"{buf}\n\n{p}" if buf else p
            continue
        if buf:
            chunks.append(buf)
        while len(p) > size:
            chunks.append(p[:size])
            p = p[size - overlap :]
        buf = p
    if buf:
        chunks.append(buf)
    return chunks


class MemoryStore:
    def __init__(
        self, path: Path | str, embedder: Embedder | None = None, *, chunk_size: int = 800
    ):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder or HashEmbedder()
        self.chunk_size = chunk_size
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._lock = threading.RLock()
        self._cache: tuple[np.ndarray, list[int]] | None = None

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ indexing
    def upsert_document(
        self,
        doc_id: str,
        text: str,
        *,
        kind: str,
        title: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Index `text` under `doc_id`; unchanged content is skipped. Returns chunk count."""
        content_hash = hashlib.sha256(f"{self.embedder.name}\n{text}".encode()).hexdigest()
        with self._lock:
            row = self._conn.execute(
                "SELECT content_hash FROM memory_docs WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            if row and row[0] == content_hash:
                return self._conn.execute(
                    "SELECT COUNT(*) FROM memory_chunks WHERE doc_id = ?", (doc_id,)
                ).fetchone()[0]
            chunks = chunk_text(text, self.chunk_size)
            if not chunks:
                self.delete_document(doc_id)
                return 0
            vecs = self.embedder.embed([f"{title}\n{c}" for c in chunks])
            now = datetime.now(UTC).isoformat()
            self._conn.execute("DELETE FROM memory_chunks WHERE doc_id = ?", (doc_id,))
            self._conn.executemany(
                "INSERT INTO memory_chunks (doc_id, kind, title, chunk_index, text, metadata_json, embedding, content_hash, embedder, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        doc_id,
                        kind,
                        title,
                        i,
                        c,
                        json.dumps(metadata or {}, ensure_ascii=False),
                        vecs[i].astype(np.float32).tobytes(),
                        content_hash,
                        self.embedder.name,
                        now,
                    )
                    for i, c in enumerate(chunks)
                ],
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_docs (doc_id, kind, title, content_hash, updated_at) VALUES (?,?,?,?,?)",
                (doc_id, kind, title, content_hash, now),
            )
            self._conn.commit()
            self._cache = None
            return len(chunks)

    def delete_document(self, doc_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM memory_chunks WHERE doc_id = ?", (doc_id,))
            self._conn.execute("DELETE FROM memory_docs WHERE doc_id = ?", (doc_id,))
            self._conn.commit()
            self._cache = None

    def index_file(self, path: Path, *, kind: str, doc_id: str | None = None) -> int:
        path = Path(path)
        if not path.is_file():
            return 0
        return self.upsert_document(
            doc_id or str(path),
            path.read_text(encoding="utf-8", errors="ignore"),
            kind=kind,
            title=path.name,
            metadata={"path": str(path)},
        )

    def index_directory(
        self,
        directory: Path,
        *,
        kind: str,
        patterns: tuple[str, ...] = ("*.md",),
        relative_to: Path | None = None,
    ) -> int:
        total = 0
        directory = Path(directory)
        if not directory.is_dir():
            return 0
        for pattern in patterns:
            for file in sorted(directory.rglob(pattern)):
                rel = file.relative_to(relative_to) if relative_to else file
                total += self.index_file(file, kind=kind, doc_id=str(rel))
        return total

    def stats(self) -> dict[str, int]:
        with self._lock:
            docs = self._conn.execute("SELECT COUNT(*) FROM memory_docs").fetchone()[0]
            chunks = self._conn.execute("SELECT COUNT(*) FROM memory_chunks").fetchone()[0]
        return {"documents": docs, "chunks": chunks}

    # -------------------------------------------------------------------- search
    def _matrix(self) -> tuple[np.ndarray, list[int]]:
        if self._cache is None:
            rows = self._conn.execute(
                "SELECT id, embedding FROM memory_chunks WHERE embedder = ? ORDER BY id",
                (self.embedder.name,),
            ).fetchall()
            ids = [r[0] for r in rows]
            if rows:
                mat = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
            else:
                mat = np.zeros((0, self.embedder.dim), dtype=np.float32)
            self._cache = (mat, ids)
        return self._cache

    def search(
        self,
        query: str,
        *,
        top_k: int = 6,
        kinds: tuple[str, ...] | None = None,
        min_score: float = 0.0,
    ) -> list[MemoryHit]:
        with self._lock:
            mat, ids = self._matrix()
            if not ids:
                return []
            q = self.embedder.embed([query])[0]
            scores = mat @ q
            order = np.argsort(-scores)
            hits: list[MemoryHit] = []
            for idx in order:
                if len(hits) >= top_k:
                    break
                score = float(scores[idx])
                if score < min_score:
                    break
                row = self._conn.execute(
                    "SELECT doc_id, kind, title, chunk_index, text, metadata_json FROM memory_chunks WHERE id = ?",
                    (ids[idx],),
                ).fetchone()
                if row is None or (kinds and row[1] not in kinds):
                    continue
                hits.append(
                    MemoryHit(
                        MemoryChunk(row[0], row[1], row[2], row[3], row[4], json.loads(row[5])),
                        score,
                    )
                )
            return hits

    def recall(
        self,
        query: str,
        *,
        top_k: int = 6,
        kinds: tuple[str, ...] | None = None,
        max_chars: int = 3000,
    ) -> str:
        """Compact Markdown block with precedents, ready to inject into an agent prompt."""
        hits = self.search(query, top_k=top_k, kinds=kinds)
        if not hits:
            return ""
        lines = ["## Precedentes recuperados da memória organizacional"]
        used = 0
        for h in hits:
            snippet = h.chunk.text.strip()
            if used + len(snippet) > max_chars:
                snippet = snippet[: max(0, max_chars - used)]
            if not snippet:
                break
            used += len(snippet)
            lines.append(
                f"### [{h.chunk.kind}] {h.chunk.title} (relevância {h.score:.2f})\n{snippet}"
            )
        return "\n\n".join(lines)
