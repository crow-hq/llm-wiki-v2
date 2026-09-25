# Copyright 2026 Federico Cesarini, Marco Sassarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The HTTP API over a Wiki built on the fakes."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from okf_wiki import Wiki, WikiConfig  # noqa: E402
from okf_wiki.client import ModelError  # noqa: E402
from okf_wiki.server import create_app  # noqa: E402
from okf_wiki.settings import Settings  # noqa: E402
from okf_wiki.store import parse_index  # noqa: E402
from tests.wiki.fakes import FakeLLM  # noqa: E402
from tests.wiki.test_files import make_pdf  # noqa: E402

NOTE = "revenue-recognition.md"


def draft(title: str) -> dict[str, Any]:
    return {"title": title, "summary": f"About {title.lower()}.", "body": f"All about {title.lower()}."}


def script_ingest(llm: FakeLLM, title: str = "Revenue recognition") -> None:
    """A classic ingest into the root of an empty wiki: summarize, route here."""
    llm.add("librarian/summarize", draft(title)).add("librarian/route", {"action": "here"})


@pytest.fixture
def client(classic_wiki: Wiki) -> TestClient:
    return TestClient(create_app(classic_wiki))


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


def test_check_is_empty_on_a_clean_wiki(client: TestClient, llm: FakeLLM) -> None:
    script_ingest(llm)
    client.post("/ingest", json={"text": "Booked on delivery."})

    assert client.get("/check").json() == []


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
    assert "llm<span>·</span>wiki" in response.text
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

    response = client.post("/upload", params={"filename": "q3-memo.txt"}, content="Revenue is booked on delivery.".encode())

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


def test_graph_returns_nodes_and_links(client: TestClient, llm: FakeLLM) -> None:
    script_ingest(llm)
    client.post("/ingest", json={"text": "Revenue text."})

    g = client.get("/graph").json()

    assert {n["id"] for n in g["nodes"]} == {"/", NOTE}
    assert g["links"] == [{"source": "/", "target": NOTE, "kind": "contains"}]


def test_home_links_to_the_brain_view(client: TestClient) -> None:
    page = client.get("/").text
    assert 'id="brain"' in page and "/web/app.js" in page
    assert "#/graph" in client.get("/web/app.js").text and "/web/vendor/d3.min.js" in client.get("/web/graph.js").text


# -- settings ------------------------------------------------------------------------------

KEY = "sk-or-v1-0123456789abcdef"
LOCAL = {"base_url": "http://localhost:8000", "client": ("127.0.0.1", 50000)}


@pytest.fixture
def rebuilt(llm: FakeLLM) -> list[WikiConfig]:
    """Every config the app rebuilds its wiki from (on the fake LLM)."""
    return []


@pytest.fixture
def local(classic_wiki: Wiki, llm: FakeLLM, rebuilt: list[WikiConfig]) -> TestClient:
    def make_wiki(cfg: WikiConfig) -> Wiki:
        rebuilt.append(cfg)
        return Wiki(cfg, llm=llm)

    return TestClient(create_app(classic_wiki, settings=Settings({}), make_wiki=make_wiki), **LOCAL)


def test_settings_show_masked_values_and_readiness(local: TestClient) -> None:
    body = local.get("/settings").json()

    assert (body["ready"], body["editable"], body["managed"]) == (False, True, [])
    assert body["values"]["provider"] == "openrouter" and body["values"]["api_key"] == ""
    assert set(body["providers"]) == {"openrouter", "openai", "gemini", "ollama", "custom"}


def test_saving_settings_rebuilds_the_wiki(local: TestClient, rebuilt: list[WikiConfig], home: Path) -> None:
    response = local.post("/settings", json={"provider": "openrouter", "api_key": KEY, "model": "openai/gpt-4.1", "mode": "crow"})

    assert response.status_code == 200
    body = response.json()
    assert (body["ready"], body["values"]["api_key"], body["values"]["mode"]) == (True, "…cdef", "crow")
    assert KEY not in response.text
    assert (rebuilt[-1].mode, rebuilt[-1].llm.model, rebuilt[-1].llm.api_key) == ("crow", "openai/gpt-4.1", KEY)
    assert local.get("/health").json()["mode"] == "crow"
    assert (home / ".config" / "llm-wiki" / "config.json").is_file()


def test_invalid_settings_are_a_422_and_change_nothing(local: TestClient, rebuilt: list[WikiConfig]) -> None:
    response = local.post("/settings", json={"provider": "custom"})

    assert response.status_code == 422
    assert "needs a base_url and a model" in response.json()["detail"]
    assert rebuilt == []


def test_the_settings_test_asks_the_model_without_saving(local: TestClient, llm: FakeLLM, home: Path) -> None:
    llm.add("settings/probe", "OK")

    body = local.post("/settings/test", json={"provider": "ollama", "model": "qwen2.5"}).json()

    assert body == {"ok": True, "model": "qwen2.5", "reply": "OK"}
    assert not (home / ".config" / "llm-wiki").exists()


def test_a_failed_settings_test_reports_the_provider_error(local: TestClient, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch) -> None:
    def chat(*_: Any, **__: Any) -> str:
        raise ModelError("llm 401 from https://openrouter.ai/api/v1/chat/completions: invalid key")

    monkeypatch.setattr(llm, "chat", chat)

    body = local.post("/settings/test", json={"api_key": "wrong"}).json()

    assert body["ok"] is False and body["error"].endswith("invalid key")


@pytest.mark.parametrize(
    ("client", "headers"),
    [
        ({"client": ("192.168.1.20", 50000), "base_url": "http://localhost:8000"}, {}),  # another machine
        (LOCAL, {"origin": "https://evil.test"}),  # a web page the user visits, posting cross-site
        ({"client": ("127.0.0.1", 50000), "base_url": "http://evil.test:8000"}, {"origin": "http://evil.test:8000"}),  # DNS rebinding
    ],
    ids=["remote", "cross-origin", "rebinding"],
)
def test_settings_change_only_from_this_machine(
    classic_wiki: Wiki, client: dict[str, Any], headers: dict[str, str], home: Path
) -> None:
    app = TestClient(create_app(classic_wiki, settings=Settings({})), **client)

    assert app.post("/settings", json={"api_key": KEY}, headers=headers).status_code == 403
    assert app.post("/settings/test", json={"base_url": "http://evil.test/v1"}, headers=headers).status_code == 403
    assert app.get("/settings", headers=headers).json()["editable"] is False
    assert not (home / ".config" / "llm-wiki").exists()


def test_the_same_origin_page_may_change_settings(local: TestClient) -> None:
    assert local.post("/settings", json={"api_key": KEY}, headers={"origin": "http://localhost:8000"}).status_code == 200


def test_without_settings_the_endpoints_are_absent(client: TestClient) -> None:
    assert client.get("/settings").status_code == 404
    assert client.post("/settings", json={}).status_code == 404


def test_the_page_and_its_modules_load_nothing_from_a_cdn(client: TestClient) -> None:
    page = client.get("/").text
    local = re.findall(r'(?:src|href)="(/web/[^"]+)"', page)
    modules = [f"/web/{m}" for src in local if src.endswith(".js") for m in re.findall(r'from "\./([\w-]+\.js)"', client.get(src).text)]

    assert len(local) == 4 and len(modules) >= 3
    for src in local + modules:
        response = client.get(src)
        assert response.status_code == 200, src
        assert "cdn." not in response.text, src


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

    from okf_wiki import server

    apps: list[Any] = []
    monkeypatch.setattr(uvicorn.Server, "run", lambda self: apps.append(self.config.app))
    for host in ("127.0.0.1", "0.0.0.0"):
        server.serve(classic_wiki, host=host)

    local, public = (TestClient(app, base_url="http://wiki.lan:8000") for app in apps)
    assert (local.get("/tree").status_code, public.get("/tree").status_code) == (403, 200)


def test_the_page_gets_the_models_of_the_provider_being_chosen(local: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from okf_wiki import server

    asked: list[Any] = []
    monkeypatch.setattr(server, "live_models", lambda llm: asked.append(llm) or ["gemini-3.8-flash"])

    body = local.post("/settings/models", json={"provider": "gemini", "api_key": "g-key-0123456789"}).json()

    assert body == {"models": ["gemini-3.8-flash"]}
    assert (asked[0].provider, asked[0].api_key) == ("gemini", "g-key-0123456789")


def test_only_this_machine_may_ask_for_models_with_the_saved_key(classic_wiki: Wiki) -> None:
    remote = TestClient(create_app(classic_wiki, settings=Settings({})), client=("192.168.1.20", 50000))
    assert remote.post("/settings/models", json={"provider": "custom", "base_url": "http://evil.test/v1"}).status_code == 403
