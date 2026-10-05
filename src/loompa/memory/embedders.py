"""Local, zero-cost embedders. FastEmbed when installed, hashed n-grams otherwise."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Protocol

import numpy as np

_TOKEN = re.compile(r"[a-zà-ú0-9_]+", re.I)


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashEmbedder:
    """Deterministic hashed word + bigram embedding. Weak semantics, zero dependencies."""

    name = "hash-ngram"

    def __init__(self, dim: int = 384):
        self.dim = dim
        self.fallback_reason = ""

    def _features(self, text: str) -> list[str]:
        toks = [t.lower() for t in _TOKEN.findall(text)]
        feats = list(toks)
        feats += [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]
        # sub-word trigrams help with morphology (pt-BR/en) and identifiers
        for t in toks:
            if len(t) > 5:
                feats += [t[i : i + 4] for i in range(len(t) - 3)]
        return feats

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for f in self._features(text):
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
                sign = 1.0 if (h >> 63) else -1.0
                out[i, h % self.dim] += sign
            norm = np.linalg.norm(out[i])
            if norm:
                out[i] /= norm
        return out


# Set to use hashed n-grams whatever the configuration says (the test suite: no download).
HASH_ONLY_ENV = "LOOMPA_MEMORY_HASH_ONLY"


def model_cache_dir() -> Path:
    """Where embedding models are kept: the hub, never the system temp folder, which the OS
    empties (fastembed's default re-downloaded the model after every reboot)."""
    home = os.environ.get("LOOMPA_HOME")
    return (Path(home) if home else Path.home() / ".loompa") / "models"


class FastEmbedEmbedder:
    def __init__(self, model_name: str, *, cache_dir: Path | None = None):
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")  # not in the engine's log
        from fastembed import TextEmbedding  # type: ignore[import-not-found]

        self._model = TextEmbedding(
            model_name=model_name, cache_dir=str(cache_dir or model_cache_dir())
        )
        self.name = model_name
        self.dim = len(next(iter(self._model.embed(["dim probe"]))))

    def embed(self, texts: list[str]) -> np.ndarray:
        """Documents. Models trained with prefixes ("passage: ", "query: ") get theirs."""
        return _unit(self._model.passage_embed(texts))

    def embed_query(self, texts: list[str]) -> np.ndarray:
        return _unit(self._model.query_embed(texts))


def _unit(vectors) -> np.ndarray:
    vecs = np.asarray(list(vectors), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return vecs / norms


def get_embedder(model_name: str | None = None, *, prefer_local_hash: bool = False) -> Embedder:
    """The configured model, or hashed n-grams when it cannot load (no network for the first
    download, a broken install): memory is best-effort and never stops the factory. The fallback
    says why in `fallback_reason`, which `loompa doctor` shows."""
    if not prefer_local_hash and model_name and not os.environ.get(HASH_ONLY_ENV):
        try:
            return FastEmbedEmbedder(model_name)
        except Exception as exc:  # noqa: BLE001 - fastembed missing or model download failed
            fallback = HashEmbedder()
            fallback.fallback_reason = f"{type(exc).__name__}: {exc}"[:300]
            return fallback
    return HashEmbedder()
