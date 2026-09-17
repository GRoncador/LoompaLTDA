# ADR-0003: Hybrid memory — SQLite vectors + local embeddings, lexical/AST for code

**Status:** accepted · **Date:** 2026-09-17

## Context

The brief asks for two memory layers: exact lexical/AST search for source code (no
hallucinated symbols) and a local, zero-cost semantic RAG over organizational memory (ADRs,
constitution, learnings, business rules, past bug resolutions).

## Decision

- **Vector store:** plain SQLite table (`memory_chunks`) with embeddings stored as float32
  blobs; similarity is brute-force cosine in NumPy. Organizational memory is small (thousands
  of chunks at most), so this beats shipping ChromaDB/LanceDB or a compiled SQLite extension.
- **Embeddings:** `fastembed` (`BAAI/bge-small-en-v1.5` or `all-MiniLM-L6-v2` via ONNX) when
  the `embeddings` extra is installed. Without it, a deterministic **hashed n-gram embedder**
  keeps the API working offline (weaker recall, zero setup) and tests deterministic.
- **Lexical layer:** `ripgrep` when a real binary exists, otherwise a pure-Python scanner;
  Python symbols are indexed with the stdlib `ast` module (functions, classes, signatures),
  other languages fall back to regex symbol extraction.
- One `MemoryStore` per factory (`.loompa/memory.db`), never shared across factories.

## Consequences

- Fully offline, $0.00, single-file backup.
- Upgrading to sqlite-vec or a dedicated vector DB later is confined to `memory/vector.py`.
