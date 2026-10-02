# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The HTTP API over a Wiki built on the fakes: ingest, ask, the tree and the web UI."""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from llmw2 import Wiki, WikiConfig
from llmw2.bundle.tree import parse_index
from llmw2.errors import ModelError
from llmw2.server import create_app
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_files import make_pdf
from tests.wiki.test_ingest_many import Probe, make_wiki

TIMEOUT = 5  # seconds a test waits for another thread before calling it a bug
FOLDER = {"name": "finance", "description": "Money in and out."}
NOTE = "finance/revenue-recognition.md"  # the root holds no notes: the first one opens a folder


def draft(title: str) -> dict[str, Any]:
    return {"title": title, "summary": f"About {title.lower()}.", "body": f"All about {title.lower()}."}


def script_ingest(llm: FakeLLM, title: str = "Revenue recognition") -> None:
    """A classic ingest into an empty wiki: summarize, route to a new folder (the root holds no notes), name it "finance"."""
    llm.add("librarian/summarize", draft(title)).add("librarian/route", {"action": "new"}).add("librarian/name_folder", FOLDER)


def test_health_reports_the_models_in_use(crow_wiki: Wiki) -> None:
    body = TestClient(create_app(crow_wiki)).get("/health").json()

    assert (body["llm"], body["classifier"]) == ("fake/llm", "fake/jev")  # as keyed in /usage by_model


def test_ingest_returns_the_result_as_json(client: TestClient, llm: FakeLLM) -> None:
    script_ingest(llm)

    response = client.post("/ingest", json={"text": "Booked on delivery.", "title": "Memo", "resource": "https://x.test/m"})

    assert response.status_code == 200
    body = response.json()
    assert (body["action"], body["note"], body["title"], body["folder"]) == ("created", NOTE, "Revenue recognition", "/finance")
    assert (body["created_folders"], body["related"]) == (["/finance"], [])
    assert [d["step"] for d in body["decisions"]] == ["route"]
    assert body["usage"]["llm"]["calls"] == 3


def test_ingest_of_empty_text_is_a_422(client: TestClient, llm: FakeLLM) -> None:
    response = client.post("/ingest", json={"text": "  \n "})

    assert response.status_code == 422
    assert response.json()["detail"] == "nothing to ingest: the source is empty"
    assert llm.calls == []


def test_ask_returns_the_answer_as_json(client: TestClient, classic_wiki: Wiki, llm: FakeLLM) -> None:
    script_ingest(llm)
    classic_wiki.ingest("Booked on delivery.")
    llm.add("researcher/navigate", {"open": ["finance"]}, {"select": [NOTE], "done": True})
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
        "notes": [],
        "subfolders": [{
            "path": "/finance",
            "description": "Money in and out.",
            "notes": [{"path": NOTE, "title": "Revenue recognition", "summary": "About revenue recognition."}],
            "subfolders": [],
        }],
    }


def test_create_folder_by_hand_lists_it_with_its_description_and_the_librarian_files_into_it(client: TestClient, llm: FakeLLM) -> None:
    top = client.post("/folder", json={"name": "Finance", "description": " Money in\nand out. "})
    sub = client.post("/folder", json={"parent": "/finance", "name": "pricing", "description": "What things cost."})
    llm.add("librarian/summarize", draft("Alpha")).add("librarian/route", {"action": "descend", "subfolder": "finance"}, {"action": "here"})
    llm.add("librarian/find_match", {"match": None}).add("librarian/relate", {"related": []})
    filed = client.post("/ingest", json={"text": "Booked on delivery."}).json()

    assert top.json() == {"path": "/finance", "description": "Money in and out.", "notes": [], "subfolders": []}
    assert sub.json()["path"] == "/finance/pricing"
    assert (filed["note"], filed["created_folders"]) == ("finance/alpha.md", [])
    finance = client.get("/tree").json()["subfolders"][0]
    assert [(f["path"], f["description"]) for f in finance["subfolders"]] == [("/finance/pricing", "What things cost.")]
    assert client.get("/check").json() == []


def test_create_folder_refuses_a_missing_name_or_description_a_taken_name_an_unknown_parent_and_max_depth(client: TestClient) -> None:
    parent = "/"
    for name in ("a", "b", "c", "d"):  # max_depth 4: /a/b/c/d is the deepest folder the librarian files to
        assert client.post("/folder", json={"parent": parent, "name": name, "description": "Deeper."}).status_code == 200
        parent = parent.rstrip("/") + "/" + name

    refused = {
        "no name": client.post("/folder", json={"name": " ?! ", "description": "Money."}),
        "no description": client.post("/folder", json={"name": "finance", "description": "  "}),
        "no description field": client.post("/folder", json={"name": "finance"}),
        "taken": client.post("/folder", json={"name": "A", "description": "Again."}),
        "too deep": client.post("/folder", json={"parent": parent, "name": "e", "description": "Deeper still."}),
        "no parent": client.post("/folder", json={"parent": "/missing", "name": "e", "description": "Lost."}),
    }

    assert {k: r.status_code for k, r in refused.items()} == {k: 404 if k == "no parent" else 422 for k in refused}
    assert "needs a name" in refused["no name"].json()["detail"]
    assert "needs a description" in refused["no description"].json()["detail"]
    assert refused["taken"].json()["detail"] == "/a already exists"
    assert "max_depth" in refused["too deep"].json()["detail"]
    assert [f["path"] for f in client.get("/tree").json()["subfolders"]] == ["/a"]


def test_usage_grows_with_each_operation(client: TestClient, llm: FakeLLM) -> None:
    before = client.get("/usage").json()
    script_ingest(llm)
    client.post("/ingest", json={"text": "Booked on delivery."})

    after = client.get("/usage").json()

    assert (before["llm"]["calls"], after["llm"]["calls"]) == (0, 3)
    assert after["by_model"]["llm:fake/llm"]["input_tokens"] == 300


def test_check_lists_problems_as_strings(client: TestClient, llm: FakeLLM, bundle: Path) -> None:
    script_ingest(llm)
    client.post("/ingest", json={"text": "Booked on delivery."})
    (bundle / NOTE).unlink()

    assert client.get("/check").json() == ["finance/index.md: lists missing revenue-recognition.md"]


def test_concurrent_ingests_both_succeed(client: TestClient, llm: FakeLLM, bundle: Path) -> None:
    # The write lock serialises ingests, so the queued replies are consumed one ingest at a time.
    script_ingest(llm, "Alpha")
    llm.add("librarian/summarize", draft("Beta")).add("librarian/route", {"action": "descend", "subfolder": "finance"}, {"action": "here"})
    llm.add("librarian/find_match", {"match": None}).add("librarian/relate", {"related": []})
    start = threading.Barrier(2)

    def post(text: str) -> Any:
        start.wait()
        return client.post("/ingest", json={"text": text})

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(post, ["first source", "second source"]))

    assert [r.status_code for r in responses] == [200, 200]
    assert sorted(r.json()["usage"]["llm"]["calls"] for r in responses) == [3, 5]  # each counts only its own calls
    listed = {link for _, link, _ in parse_index((bundle / "finance" / "index.md").read_text(encoding="utf-8"))}
    assert listed == {"alpha.md", "beta.md"}


# -- concurrent requests: prepared in parallel, filed in turn -----------------------------


def serve(bundle: Path, probe: Probe, concurrency: int) -> TestClient:
    return TestClient(create_app(make_wiki(bundle, concurrency=concurrency, steps=probe.steps)))


def upload(client: TestClient, n: int) -> Any:
    return client.post("/upload", params={"filename": f"doc-{n}.md"}, content=f"topic: red-{n}\nBody {n}.".encode())


def post_ingest(client: TestClient, n: int) -> Any:
    return client.post("/ingest", json={"text": f"topic: red-{n}\nBody {n}.", "title": f"doc {n}"})


@pytest.mark.parametrize("k", [1, 3])
def test_health_reports_the_concurrency(client: TestClient, bundle: Path, k: int) -> None:
    assert client.get("/health").json()["concurrency"] == 1

    assert serve(bundle / "k", Probe(), k).get("/health").json()["concurrency"] == k


@pytest.mark.parametrize("send", [upload, post_ingest], ids=["upload", "ingest"])
def test_two_concurrent_requests_with_two_workers_are_prepared_together_and_filed_in_turn(bundle: Path, send: Any) -> None:
    probe = Probe(pause=0.05)
    probe.want = 2  # each summarize waits until the other is in flight too: it never gets there if preparing is serial
    client = serve(bundle, probe, 2)
    start = threading.Barrier(2)

    def post(n: int) -> Any:
        start.wait(TIMEOUT)
        return send(client, n)

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(post, [0, 1]))

    assert [r.status_code for r in responses] == [200, 200]
    assert probe.prep_max == 2 and probe.serial_max == 1
    assert sorted(r.json()["note"] for r in responses) == ["red/about-red-0.md", "red/about-red-1.md"]
    assert client.get("/check").json() == []


@pytest.mark.parametrize("send", [upload, post_ingest], ids=["upload", "ingest"])
def test_concurrent_requests_with_one_worker_are_prepared_one_at_a_time(bundle: Path, send: Any) -> None:
    probe = Probe(pause=0.05)
    client = serve(bundle, probe, 1)
    start = threading.Barrier(3)

    def post(n: int) -> Any:
        start.wait(TIMEOUT)
        return send(client, n)

    with ThreadPoolExecutor(3) as pool:
        futures = [pool.submit(post, n) for n in range(3)]
        responses = [f.result(timeout=30) for f in futures]

    assert [r.status_code for r in responses] == [200] * 3
    assert probe.prep_max == 1 and probe.serial_max == 1
    assert len(probe.filed) == 3


def test_a_request_is_prepared_while_another_is_being_filed(bundle: Path) -> None:
    filing, second_prepared = threading.Event(), threading.Event()

    def hold(name: str, args: tuple[Any, ...]) -> None:
        if name == "route" and not filing.is_set():
            filing.set()  # the first request to reach filing waits for the other one's preparation to finish
            assert second_prepared.wait(TIMEOUT), "the second request was not prepared while the first was filed"

    def prepare(source: Any) -> None:
        if filing.wait(0.5) and source.text.startswith("topic: red-1"):
            second_prepared.set()

    probe = Probe(pause=0.0, hook=prepare, serial_hook=hold)
    client = serve(bundle, probe, 2)

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(upload, client, 0)
        assert _wait(lambda: probe.started, "the first upload never began")
        second = pool.submit(upload, client, 1)
        responses = [first.result(timeout=30), second.result(timeout=30)]

    assert [r.status_code for r in responses] == [200, 200]
    assert second_prepared.is_set()


def _wait(cond: Any, why: str) -> bool:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.005)
    raise AssertionError(why)


def test_two_concurrent_ingests_of_one_origin_file_it_once_and_the_other_is_unchanged(bundle: Path) -> None:
    probe = Probe(pause=0.02)
    probe.want = 2  # both pass the unlocked "unchanged" check before either is filed
    client = serve(bundle, probe, 2)
    body = {"text": "topic: red-1\nThe memo.", "origin": {"source": "test", "account": "me", "id": "memo", "version": "v1"}}

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: client.post("/ingest", json=body), range(2)))

    assert [r.status_code for r in responses] == [200, 200]
    assert sorted(r.json()["action"] for r in responses) == ["created", "unchanged"]
    assert len(list(bundle.rglob("about-*.md"))) == 1 and len(list((bundle / "raw").glob("*.md"))) == 1


def test_a_failing_request_does_not_block_the_next_one(bundle: Path) -> None:
    probe = Probe(pause=0.0)
    client = serve(bundle, probe, 1)

    bad = client.post("/upload", params={"filename": "empty.md"}, content=b"   ")
    good = upload(client, 0)

    assert (bad.status_code, good.status_code) == (422, 200)


# -- the web UI and its endpoints ---------------------------------------------------------


def test_home_serves_the_single_page_ui(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert "<h1>CROW</h1>" in response.text
    assert "/upload?filename=" in client.get("/web/queue.js").text


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

    assert {n["id"] for n in g["nodes"]} == {"/", "/finance", NOTE}
    assert g["links"] == [
        {"source": "/", "target": "/finance", "kind": "contains"},
        {"source": "/finance", "target": NOTE, "kind": "contains"},
    ]


def test_the_page_and_its_modules_load_nothing_from_a_cdn(client: TestClient) -> None:
    page = client.get("/").text
    local = re.findall(r'(?:src|href)="(/web/[^"]+)"', page)
    modules = [f"/web/{m}" for src in local if src.endswith(".js") for m in re.findall(r'from "\./([\w-]+\.js)"', client.get(src).text)]

    assert len(local) >= 4 and len(modules) >= 3
    for src in local + modules:
        response = client.get(src)
        assert response.status_code == 200, src
        assert "cdn." not in response.text, src


def test_ask_and_ingest_when_crow_has_no_classifier_key_then_503_saying_where_to_put_it(bundle: Path, llm: FakeLLM) -> None:
    # ARRANGE
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm)  # its classifier is built from the config: no key
    client = TestClient(create_app(wiki))

    # ACT
    asked = client.post("/ask", json={"question": "What is revenue recognition?"})
    filed = client.post("/ingest", json={"text": "Revenue is recognised on delivery."})

    # ASSERT
    assert asked.status_code == filed.status_code == 503
    assert "Settings → Advanced → Classifier key" in asked.json()["detail"]
    assert llm.calls == [] and not (bundle / "raw").exists()  # stopped before any model call or file
