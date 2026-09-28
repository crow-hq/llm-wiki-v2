# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The HTTP API's guards and limits: cross-site and host checks, body sizes, schema and error mapping."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from llmw2 import Wiki, WikiConfig
from llmw2.errors import InputError, ModelError
from llmw2.server import create_app
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_server import script_ingest

# -- guards --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [("post", "/ingest", {"json": {"text": "x"}}), ("post", "/ask", {"json": {"question": "x"}}),
     ("post", "/upload?filename=a.txt", {"content": b"x"})],
)
def test_another_site_cannot_write_or_ask_through_the_browser(
    client: TestClient, llm: FakeLLM, method: str, path: str, body: dict[str, Any]
) -> None:
    response = getattr(client, method)(path, headers={"origin": "https://evil.test"}, **body)

    assert (response.status_code, response.json()["detail"]) == (403, "cross-site request refused")
    assert llm.calls == []


def test_the_page_itself_and_scripts_without_origin_may_write(client: TestClient, llm: FakeLLM) -> None:
    script_ingest(llm)
    assert client.post("/ingest", json={"text": "x"}, headers={"origin": "http://testserver"}).status_code == 200
    # curl or a script sends no Origin: past the guard (415 is the upload's own answer)
    assert client.post("/upload?filename=a.png", content=b"x").status_code == 415


def test_a_local_only_app_refuses_other_host_names(classic_wiki: Wiki) -> None:
    app = create_app(classic_wiki, local_only=True)

    rebound = TestClient(app, base_url="http://evil.test:8000")  # a hostile domain re-pointed at 127.0.0.1
    assert rebound.get("/tree").status_code == 403
    assert rebound.get("/").status_code == 403
    for name in ("localhost", "127.0.0.1", "[::1]"):
        assert TestClient(app, base_url=f"http://{name}:8000").get("/tree").status_code == 200, name


def test_serve_is_local_only_when_bound_to_localhost(classic_wiki: Wiki, monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    from llmw2 import server

    apps: list[Any] = []
    monkeypatch.setattr(uvicorn.Server, "run", lambda self: apps.append(self.config.app))
    for host in ("127.0.0.1", "0.0.0.0"):
        server.serve(classic_wiki, host=host)

    local, public = (TestClient(app, base_url="http://wiki.lan:8000") for app in apps)
    assert (local.get("/tree").status_code, public.get("/tree").status_code) == (403, 200)


# -- limits, schema and error mapping --------------------------------------------------------------


@pytest.fixture
def small(bundle: Path, llm: FakeLLM) -> TestClient:
    """An app whose wiki accepts request bodies up to 1 MB."""
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", upload_mb=1), llm=llm)
    wiki.init()
    return TestClient(create_app(wiki))


@pytest.mark.parametrize("path", ["/ingest", "/upload?filename=big.txt"])
def test_post_when_content_length_exceeds_upload_mb_then_413(small: TestClient, llm: FakeLLM, path: str) -> None:
    # ACT
    response = small.post(path, content=b"x" * 1_000_001, headers={"content-type": "application/json"})

    # ASSERT
    assert (response.status_code, llm.calls) == (413, [])


def test_upload_when_a_streamed_body_exceeds_upload_mb_then_413(small: TestClient, llm: FakeLLM) -> None:
    # ARRANGE
    chunks = (b"x" * 100_000 for _ in range(11))  # no Content-Length: sent chunked

    # ACT
    response = small.post("/upload?filename=big.txt", content=chunks)

    # ASSERT
    assert (response.status_code, llm.calls) == (413, [])


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_when_requested_then_404(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


@pytest.mark.parametrize(("error", "status"), [(ModelError("llm 503"), 502), (InputError("no text layer"), 422)])
def test_upload_when_the_wiki_raises_then_the_central_handler_answers(
    client: TestClient, classic_wiki: Wiki, monkeypatch: pytest.MonkeyPatch, error: Exception, status: int
) -> None:
    # ARRANGE
    def fail(*_: Any, **__: Any) -> Any:
        raise error

    monkeypatch.setattr(classic_wiki, "ingest_file", fail)

    # ACT
    response = client.post("/upload", params={"filename": "a.pdf"}, content=b"%PDF")

    # ASSERT
    assert (response.status_code, response.json()["detail"]) == (status, str(error))


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"q": 1}, "question: Field required"),
        ({"question": ["a"]}, "question: Input should be a valid string"),
    ],
    ids=["missing-field", "wrong-type"],
)
def test_ask_when_the_body_is_malformed_then_422_with_one_readable_detail(
    client: TestClient, llm: FakeLLM, body: dict[str, Any], detail: str
) -> None:
    # ACT
    response = client.post("/ask", json=body)

    # ASSERT
    assert (response.status_code, response.json()["detail"], llm.calls) == (422, detail, [])


def test_ask_when_the_body_is_not_an_object_then_422_names_the_body(client: TestClient) -> None:
    # ACT
    response = client.post("/ask", json=["When is revenue booked?"])

    # ASSERT
    assert response.status_code == 422
    assert response.json()["detail"].startswith("body: ")  # no field to name: the location falls back to the body
