# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The embedder built from the settings: OpenAI-compatible `/embeddings` and Ollama `/api/embed`, 16 texts a request."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from llmw2 import Wiki, WikiConfig
from llmw2.config import EmbedderConfig
from llmw2.errors import ModelError
from llmw2.models.embedder import BATCH, HttpEmbedder
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeLLM


def serve(handler: Callable[[httpx.Request], httpx.Response]) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def run(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(run)), seen


def vector_of(text: str) -> list[float]:
    return [float(len(text)), 1.0]


def openai_reply(request: httpx.Request) -> httpx.Response:
    texts = json.loads(request.content)["input"]
    rows = [{"index": i, "embedding": vector_of(t)} for i, t in enumerate(texts)]
    return httpx.Response(200, json={"data": rows[::-1]})  # out of order: the index decides


def ollama_reply(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"embeddings": [vector_of(t) for t in json.loads(request.content)["input"]]})


def test_openai_posts_to_embeddings_with_the_key_and_orders_by_index() -> None:
    cfg = EmbedderConfig(provider="openai", model="emb-1", api_key="sk-x")
    client, seen = serve(openai_reply)

    vectors = HttpEmbedder(cfg, UsageTracker(), client=client)(["a", "bbb"])

    assert vectors == [[1.0, 1.0], [3.0, 1.0]]
    [request] = seen
    assert str(request.url) == "https://api.openai.com/v1/embeddings"
    assert request.headers["authorization"] == "Bearer sk-x"
    assert json.loads(request.content) == {"model": "emb-1", "input": ["a", "bbb"]}


def test_ollama_posts_to_api_embed_with_truncate_and_no_key() -> None:
    cfg = EmbedderConfig(provider="ollama", model="embeddinggemma")
    client, seen = serve(ollama_reply)

    vectors = HttpEmbedder(cfg, UsageTracker(), client=client)(["ab"])

    assert vectors == [[2.0, 1.0]]
    [request] = seen
    assert str(request.url) == "http://localhost:11434/api/embed" and "authorization" not in request.headers
    assert json.loads(request.content) == {"model": "embeddinggemma", "input": ["ab"], "truncate": True}


@pytest.mark.parametrize("provider", ["openai", "ollama"])
def test_texts_go_in_batches_of_sixteen(provider: str) -> None:
    cfg = EmbedderConfig(provider=provider, model="m", base_url="http://emb.test")  # type: ignore[arg-type]
    client, seen = serve(openai_reply if provider == "openai" else ollama_reply)
    texts = [f"t{n}" for n in range(BATCH * 2 + 3)]

    vectors = HttpEmbedder(cfg, UsageTracker(), client=client)(texts)

    assert [len(json.loads(r.content)["input"]) for r in seen] == [16, 16, 3]
    assert vectors == [vector_of(t) for t in texts]


def test_a_wrong_number_of_vectors_is_a_model_error() -> None:
    cfg = EmbedderConfig(provider="ollama", model="m")
    client, _ = serve(lambda r: httpx.Response(200, json={"embeddings": [[1.0]]}))

    with pytest.raises(ModelError, match="1 vectors for 2 texts"):
        HttpEmbedder(cfg, UsageTracker(), client=client)(["a", "b"])


def test_env_variables_fill_the_config(tmp_path: Path) -> None:
    env = {
        "OKF_BUNDLE": str(tmp_path), "OKF_EMBEDDER_PROVIDER": "ollama", "OKF_EMBEDDER_MODEL": "embeddinggemma",
        "OKF_EMBEDDER_BASE_URL": "http://box:11434", "OKF_EMBEDDER_API_KEY": "k", "OKF_EMBEDDER_TIMEOUT": "9",
    }

    emb = WikiConfig.from_env(env).embedder

    assert (emb.provider, emb.model, emb.base_url, emb.api_key, emb.timeout) == (
        "ollama", "embeddinggemma", "http://box:11434", "k", 9.0
    )


def test_default_is_none_and_a_provider_needs_a_model(tmp_path: Path) -> None:
    assert not WikiConfig(bundle=tmp_path).embedder.enabled
    with pytest.raises(ValidationError, match="needs a model"):
        EmbedderConfig(provider="openai")


def test_wiki_builds_the_embedder_from_the_settings_and_a_callable_wins(tmp_path: Path) -> None:
    def make(path: Path, **kw: object) -> Wiki:
        cfg = WikiConfig(bundle=path, mode="classic", embedder=EmbedderConfig(provider="ollama", model="m"))
        return Wiki(cfg, llm=FakeLLM(UsageTracker()), **kw)  # type: ignore[arg-type]

    assert isinstance(make(tmp_path / "a")._search.embedder, HttpEmbedder)

    def mine(texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    assert make(tmp_path / "b", embedder=mine)._search.embedder is mine
    plain = Wiki(WikiConfig(bundle=tmp_path / "c", mode="classic"), llm=FakeLLM(UsageTracker()))
    assert plain._search.embedder is None
