"""Embeddings, behind one interface. The real model runs locally (ADR-001: report text never leaves).

BgeM3Embedder : BAAI/bge-m3 via sentence-transformers, 1024 dims, normalised (cosine = dot product).
HashEmbedder  : deterministic bag-of-words test double, so tests run without torch or a 2 GB model.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

DIM = 1024


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class BgeM3Embedder:
    def __init__(self, model_name: str = "BAAI/bge-m3", batch_size: int = 64,
                 local_files_only: bool = False):
        from sentence_transformers import SentenceTransformer   # imported lazily: heavy
        self.model = SentenceTransformer(model_name, local_files_only=local_files_only)
        self.batch_size = batch_size                             # deck p.26: batch of 64

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = self.model.encode(texts, batch_size=self.batch_size, normalize_embeddings=True,
                                 show_progress_bar=len(texts) > self.batch_size)
        return [v.tolist() for v in vecs]


class HashEmbedder:
    """Each word lands in a hashed bucket; texts sharing words have higher cosine. Tests only."""
    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * DIM
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


def to_pgvector(v: list[float]) -> str:
    """pgvector text literal, so no extra Python package is needed: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
