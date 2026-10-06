"""Plan 13.1: organizational memory ranks by meaning and by words, fused. No network."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from loompa.config.store import _upgrade
from loompa.memory import MemoryStore
from loompa.memory.embedders import HASH_ONLY_ENV, HashEmbedder, get_embedder
from loompa.memory.store import fts_query


class Blind:
    """An embedder that sees nothing: every chunk scores the same, only words can rank."""

    name = "blind"
    dim = 4

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.ones((len(texts), self.dim), dtype=np.float32) / 2


def test_words_rank_what_the_vectors_cannot(tmp_path: Path):
    mem = MemoryStore(tmp_path / "m.db", Blind())
    mem.upsert_document("a", "Persistência local com localStorage.", kind="spec", title="S-006")
    mem.upsert_document("b", "Áudio sintetizado com a Web Audio API.", kind="spec", title="S-004")
    mem.upsert_document("c", "Carcaça em formato de ovo.", kind="spec", title="S-054")
    assert mem.search("como o jogo toca o audio?", top_k=1)[0].chunk.doc_id == "b"
    assert mem.search("ovo", top_k=1, kinds=("spec",))[0].chunk.doc_id == "c"
    mem.delete_document("c")  # gone from both rankings
    assert all(h.chunk.doc_id != "c" for h in mem.search("ovo"))
    mem.close()


def test_an_older_memory_gets_its_lexical_index(tmp_path: Path):
    mem = MemoryStore(tmp_path / "m.db", HashEmbedder())
    mem.upsert_document("b", "Áudio sintetizado.", kind="spec", title="S-004")
    mem._conn.execute("DELETE FROM memory_fts")
    mem._conn.commit()
    mem.close()
    again = MemoryStore(tmp_path / "m.db", HashEmbedder())
    assert again._lexical("audio") and again.search("audio")[0].chunk.doc_id == "b"
    again.close()


def test_the_query_never_carries_fts_syntax():
    assert fts_query('o "pet" NEAR(x) - AND como?') == '"pet" OR "near"'
    assert fts_query("o a de") == ""


def test_the_english_only_default_is_dropped_from_old_configs(monkeypatch):
    data = _upgrade(
        {"revision": 4, "models": {}, "memory": {"embedding_model": "BAAI/bge-small-en-v1.5"}}
    )
    assert "embedding_model" not in data["memory"] and data["revision"] == 5
    kept = _upgrade({"revision": 4, "models": {}, "memory": {"embedding_model": "x/y"}})
    assert kept["memory"]["embedding_model"] == "x/y"


def test_the_suite_never_downloads_a_model(monkeypatch):
    assert get_embedder("sentence-transformers/x").name == "hash-ngram"
    monkeypatch.delenv(HASH_ONLY_ENV)
    fallback = get_embedder("not/a-model")
    assert fallback.name == "hash-ngram" and fallback.fallback_reason


def test_one_document_never_fills_the_answer(tmp_path: Path):
    """12.1, S-061B: six chunks of one spec came back as six precedents."""
    mem = MemoryStore(tmp_path / "m.db", HashEmbedder(), chunk_size=100)
    long = "\n\n".join(f"Botão Novo ovo no fim de jogo, parte {i}." for i in range(8))
    mem.upsert_document("specs/S-061/spec.md", long, kind="spec", title="S-061")
    mem.upsert_document("specs/S-037/spec.md", "Recomeçar o jogo do zero.", kind="spec", title="S")
    hits = mem.search("botão novo ovo fim de jogo", top_k=6)
    docs = [h.chunk.doc_id for h in hits]
    assert docs.count("specs/S-061/spec.md") == 2 and "specs/S-037/spec.md" in docs
    skipped = mem.search("botão novo ovo", top_k=6, skip_docs=("specs/S-061/",))
    assert all(not h.chunk.doc_id.startswith("specs/S-061/") for h in skipped)
    mem.close()
