# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The HTTP API over a Wiki built on the fakes: ingest, ask, the tree and the web UI."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from okf_wiki import Wiki
from okf_wiki.bundle.tree import parse_index
from okf_wiki.errors import ModelError
from okf_wiki.server import create_app
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_files import make_pdf

NOTE = "revenue-recognition.md"


def draft(title: str) -> dict[str, Any]:
    return {"title": title, "summary": f"About {title.lower()}.", "body": f"All about {title.lower()}."}


def script_ingest(llm: FakeLLM, title: str = "Revenue recognition") -> None:
    """A classic ingest into the root of an empty wiki: summarize, route here."""
    llm.add("librarian/summarize", draft(title)).add("librarian/route", {"action": "here"})


def test_health_reports_the_models_in_use(crow_wiki: Wiki) -> None:
    body = TestClient(create_app(crow_wiki)).get("/health").json()

    assert (body["llm"], body["classifier"]) == ("fake/llm", "fake/jev")  # as keyed in /usage by_model


def test_ingest_returns_the_result_as_json(client: TestClient, llm: FakeLLM) -> None:
    script_ingest(llm)

    response = client.post("/ingest", json={"text": "Booked on delivery.", "title": "Memo", "resource": "https://x.test/m"})

    assert response.status_code == 200
    body = response.json()
    assert (body["action"], body["note"], body["title"], body["folder"]) == ("created", NOTE, "Revenue recognition", "/")
    assert (body["created_folders"], body["related"]) == ([], [])
    assert [d["step"] for d in body["decisions"]] == ["route"]
    assert body["usage"]["llm"]["calls"] == 2


def test_ingest_of_empty_text_is_a_422(client: TestClient, llm: FakeLLM) -> None:
    response = client.post("/ingest", json={"text": "  \n "})

    assert response.status_code == 422
    assert response.json()["detail"] == "nothing to ingest: the source is empty"
    assert llm.calls == []


def test_ask_returns_the_answer_as_json(client: TestClient, classic_wiki: Wiki, llm: FakeLLM) -> None:
    script_ingest(llm)
    classic_wiki.ingest("Booked on delivery.")
    llm.add("researcher/navigate", {"select": [NOTE], "done": True})
    llm.add("researcher/answer", f"On delivery [{NOTE}].")

    response = client.post("/ask", json={"question": "When is revenue booked?"})

    assert response.status_code == 200
    body = response.json()
    assert (body["text"], body["citations"], body["notes"]) == (f"On delivery [{NOTE}].", [NOTE], [NOTE])


def test_ask_of_an_empty_question_is_a_422(client: TestClient) -> None:
    assert client.post("/ask", json={"question": " "}).status_code == 422


def test_model_error_during_ingest_is_a_502(
    client: TestClient, classic_wiki: Wiki, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: Any, **__: Any) -> Any:
        raise ModelError("llm 503 from http://llm.test/v1/chat/completions")

    monkeypatch.setattr(classic_wiki, "ingest", fail)

    response = client.post("/ingest", json={"text": "Booked on delivery."})

    assert response.status_code == 502
    assert response.json()["detail"] == "llm 503 from http://llm.test/v1/chat/completions"


def test_invalid_llm_json_during_ask_is_a_502(client: TestClient, classic_wiki: Wiki, llm: FakeLLM) -> None:
    script_ingest(llm)
    classic_wiki.ingest("Booked on delivery.")
    llm.add("researcher/navigate", "not json", "still not json")

    response = client.post("/ask", json={"question": "When is revenue booked?"})

    assert response.status_code == 502
    assert "researcher/navigate" in response.json()["detail"]


def test_tree_lists_folders_and_notes(client: TestClient, llm: FakeLLM) -> None:
    script_ingest(llm)
    client.post("/ingest", json={"text": "Booked on delivery."})

    tree = client.get("/tree").json()

    assert tree == {
        "path": "/",
        "description": "",
        "notes": [{"path": NOTE, "title": "Revenue recognition", "summary": "About revenue recognition."}],
        "subfolders": [],
    }


def test_usage_grows_with_each_operation(client: TestClient, llm: FakeLLM) -> None:
    before = client.get("/usage").json()
    script_ingest(llm)
    client.post("/ingest", json={"text": "Booked on delivery."})

    after = client.get("/usage").json()

    assert (before["llm"]["calls"], after["llm"]["calls"]) == (0, 2)
    assert after["by_model"]["llm:fake/llm"]["input_tokens"] == 200


def test_check_lists_problems_as_strings(client: TestClient, llm: FakeLLM, bundle: Path) -> None:
    script_ingest(llm)
    client.post("/ingest", json={"text": "Booked on delivery."})
    (bundle / NOTE).unlink()

    assert client.get("/check").json() == [f"index.md: lists missing {NOTE}"]


def test_concurrent_ingests_both_succeed(client: TestClient, llm: FakeLLM, bundle: Path) -> None:
    # The write lock serialises ingests, so the queued replies are consumed one ingest at a time.
    script_ingest(llm, "Alpha")
    llm.add("librarian/summarize", draft("Beta")).add("librarian/route", {"action": "here"})
    llm.add("librarian/find_match", {"match": None}).add("librarian/relate", {"related": []})
    start = threading.Barrier(2)

    def post(text: str) -> Any:
        start.wait()
        return client.post("/ingest", json={"text": text})

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(post, ["first source", "second source"]))

    assert [r.status_code for r in responses] == [200, 200]
    assert sorted(r.json()["usage"]["llm"]["calls"] for r in responses) == [2, 4]  # each counts only its own calls
    listed = {link for _, link, _ in parse_index((bundle / "index.md").read_text(encoding="utf-8"))}
    assert listed == {"alpha.md", "beta.md"}


# -- the web UI and its endpoints ---------------------------------------------------------


def test_home_serves_the_single_page_ui(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert "<h1>CROW</h1>" in response.text
    assert "/upload?filename=" in client.get("/web/app.js").text


def test_note_returns_frontmatter_and_body(client: TestClient, llm: FakeLLM) -> None:
    script_ingest(llm)
    client.post("/ingest", json={"text": "Revenue text."})

    note = client.get("/note", params={"path": NOTE}).json()

    assert (note["path"], note["title"], note["summary"]) == (NOTE, "Revenue recognition", "About revenue recognition.")
    assert note["frontmatter"]["type"] == "Note" and "All about revenue recognition." in note["body"]


def test_note_also_opens_raw_sources(client: TestClient, llm: FakeLLM) -> None:
    script_ingest(llm)
    client.post("/ingest", json={"text": "Revenue text.", "title": "Memo"})
    raw = client.get("/note", params={"path": NOTE}).json()["frontmatter"]["sources"][0]["resource"]

    source = client.get("/note", params={"path": raw}).json()

    assert source["frontmatter"]["type"] == "Source" and source["body"].strip() == "Revenue text."


@pytest.mark.parametrize(("path", "status"), [("../secret.md", 400), ("index.md", 400), ("log.md", 400), ("x.txt", 400), ("nope.md", 404)])
def test_note_refuses_what_is_not_a_wiki_document(client: TestClient, path: str, status: int) -> None:
    assert client.get("/note", params={"path": path}).status_code == status


def test_upload_catalogues_a_text_file(client: TestClient, llm: FakeLLM, bundle: Path) -> None:
    script_ingest(llm)

    response = client.post("/upload", params={"filename": "q3-memo.txt"}, content=b"Revenue is booked on delivery.")

    assert response.status_code == 200 and response.json()["note"] == NOTE
    assert "Source title: q3 memo" in llm.calls[0][1][-1]["content"]


def test_upload_catalogues_a_pdf(client: TestClient, llm: FakeLLM) -> None:
    script_ingest(llm)

    response = client.post("/upload", params={"filename": "minutes.pdf"}, content=make_pdf("Revenue grew 10%."))

    assert response.status_code == 200
    assert "Revenue grew 10%." in llm.calls[0][1][-1]["content"]


@pytest.mark.parametrize(("filename", "content", "status"), [("photo.png", b"\x89PNG", 415), ("empty.txt", b"   ", 422)])
def test_upload_rejects_what_cannot_be_catalogued(client: TestClient, filename: str, content: bytes, status: int) -> None:
    assert client.post("/upload", params={"filename": filename}, content=content).status_code == status


def test_upload_when_a_text_file_holds_a_nul_byte_then_422_names_it_and_asks_no_model(
    client: TestClient, llm: FakeLLM
) -> None:
    # ACT
    response = client.post("/upload", params={"filename": "photo.txt"}, content=b"\x89PNG\r\n\x1a\n\x00\x00")

    # ASSERT
    assert (response.status_code, response.json()["detail"]) == (422, "photo.txt: a binary file, not text")
    assert llm.calls == []


def test_graph_returns_nodes_and_links(client: TestClient, llm: FakeLLM) -> None:
    script_ingest(llm)
    client.post("/ingest", json={"text": "Revenue text."})

    g = client.get("/graph").json()

    assert {n["id"] for n in g["nodes"]} == {"/", NOTE}
    assert g["links"] == [{"source": "/", "target": NOTE, "kind": "contains"}]


def test_the_page_and_its_modules_load_nothing_from_a_cdn(client: TestClient) -> None:
    page = client.get("/").text
    local = re.findall(r'(?:src|href)="(/web/[^"]+)"', page)
    modules = [f"/web/{m}" for src in local if src.endswith(".js") for m in re.findall(r'from "\./([\w-]+\.js)"', client.get(src).text)]

    assert len(local) >= 4 and len(modules) >= 3
    for src in local + modules:
        response = client.get(src)
        assert response.status_code == 200, src
        assert "cdn." not in response.text, src
