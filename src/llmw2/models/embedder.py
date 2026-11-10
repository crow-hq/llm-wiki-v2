# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The embedder of `Wiki.search` and `ask`, built from the settings: texts in, one vector each out.

Two protocols: an OpenAI-compatible `POST {base_url}/embeddings` (OpenAI, a gateway, vLLM, LM Studio, llama.cpp) and
Ollama's `POST {base_url}/api/embed`. The texts go in batches of `BATCH`.
"""

from __future__ import annotations

from typing import Any

import httpx

from llmw2.config import EmbedderConfig
from llmw2.errors import ModelError
from llmw2.models.client import HttpModel
from llmw2.models.usage import UsageTracker

BATCH = 16  # texts per request


class HttpEmbedder(HttpModel):
    """A callable from a list of texts to a list of vectors, in the order of the texts."""

    kind = "embedder"

    def __init__(self, cfg: EmbedderConfig, usage: UsageTracker, *, client: httpx.Client | None = None) -> None:
        super().__init__(cfg.base_url, cfg.model, usage, api_key=cfg.api_key, timeout=cfg.timeout, client=client)
        self.provider = cfg.provider

    def __call__(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            out += self._batch(texts[i : i + BATCH])
        return out

    def _batch(self, texts: list[str]) -> list[list[float]]:
        if self.provider == "ollama":  # truncate: a text longer than the model's window is cut, not refused
            data = self._post("/api/embed", {"model": self.model, "input": texts, "truncate": True})
            vectors = data.get("embeddings")
        else:
            data = self._post("/embeddings", {"model": self.model, "input": texts})
            rows = data.get("data")
            vectors = [r.get("embedding") if isinstance(r, dict) else None for r in sorted_rows(rows)]
        if not isinstance(vectors, list) or len(vectors) != len(texts) or not all(_vector(v) for v in vectors):
            raise ModelError(f"embedder {self.provider} answered {_count(vectors)} vectors for {len(texts)} texts")
        return [[float(x) for x in v] for v in vectors]


def sorted_rows(rows: Any) -> list[Any]:
    """The rows of an OpenAI-style answer in the order of the texts (`index`), as listed when they have none."""
    if not isinstance(rows, list):
        return []
    if all(isinstance(r, dict) and isinstance(r.get("index"), int) for r in rows):
        return sorted(rows, key=lambda r: r["index"])
    return rows


def _vector(v: Any) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)


def _count(vectors: Any) -> int | str:
    return len(vectors) if isinstance(vectors, list) else "no"
