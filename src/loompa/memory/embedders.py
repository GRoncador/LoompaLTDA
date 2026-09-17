"""Local, zero-cost embedders. FastEmbed when installed, hashed n-grams otherwise."""

from __future__ import annotations

import hashlib
import re
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


class FastEmbedEmbedder:
    def __init__(self, model_name: str):
        from fastembed import TextEmbedding  # type: ignore[import-not-found]

        self._model = TextEmbedding(model_name=model_name)
        self.name = model_name
        self.dim = len(next(iter(self._model.embed(["dim probe"]))))

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = np.asarray(list(self._model.embed(texts)), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return vecs / norms


def get_embedder(model_name: str | None = None, *, prefer_local_hash: bool = False) -> Embedder:
    if not prefer_local_hash and model_name:
        try:
            return FastEmbedEmbedder(model_name)
        except Exception:  # noqa: BLE001 - fastembed missing or model download failed
            pass
    return HashEmbedder()
