# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The settings endpoints: reading, saving, trying a provider, and who may change them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from llmw2 import Wiki, WikiConfig
from llmw2.errors import ModelError
from llmw2.server import NO_MCP, create_app
from llmw2.settings import Settings
from tests.wiki.fakes import FakeClassifier, FakeLLM

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
    assert set(body["classifier_providers"]) == {"openrouter", "typesafe", "custom"}
    assert body["classifier_providers"]["custom"]["crow_preset"] == "laya" and body["crow_presets"]["laya"]["tau_path"] == 0.65
    assert (body["values"]["temperature"], body["values"]["timeout"], body["values"]["classifier_attempts"]) == (0.1, 120.0, 1)
    assert body["values"]["crow_tau_path"] == 0.5  # the default classifier is Jev on OpenRouter
    assert body["values"]["classifier_provider"] == "openrouter" and body["values"]["classifier_model"] == "typesafe/jev-1.13"


def test_saving_settings_rebuilds_the_wiki(local: TestClient, rebuilt: list[WikiConfig], home: Path) -> None:
    response = local.post("/settings", json={"provider": "openrouter", "api_key": KEY, "model": "openai/gpt-4.1", "mode": "crow"})

    assert response.status_code == 200
    body = response.json()
    assert (body["ready"], body["values"]["api_key"], body["values"]["mode"]) == (True, "…cdef", "crow")
    assert KEY not in response.text
    assert (rebuilt[-1].mode, rebuilt[-1].llm.model, rebuilt[-1].llm.api_key) == ("crow", "openai/gpt-4.1", KEY)
    assert local.get("/health").json()["mode"] == "crow"
    assert (home / ".config" / "llm-wiki" / "config.json").is_file()


def test_saving_concurrency_rebuilds_the_wiki_with_it_and_health_reports_it(local: TestClient, rebuilt: list[WikiConfig]) -> None:
    assert local.get("/health").json()["concurrency"] == 4

    response = local.post("/settings", json={"provider": "ollama", "mode": "classic", "concurrency": "3"})

    assert response.status_code == 200
    assert response.json()["values"]["concurrency"] == 3
    assert rebuilt[-1].concurrency == 3
    assert local.get("/health").json()["concurrency"] == 3
    assert local.post("/settings", json={"concurrency": "99"}).status_code == 422


def test_invalid_settings_are_a_422_and_change_nothing(local: TestClient, rebuilt: list[WikiConfig]) -> None:
    response = local.post("/settings", json={"provider": "custom"})

    assert response.status_code == 422
    assert "needs a base_url and a model" in response.json()["detail"]
    assert rebuilt == []


def test_saving_a_wiki_folder_that_cannot_be_made_is_a_422_and_saves_nothing(local: TestClient, home: Path, tmp_path: Path) -> None:
    taken = tmp_path / "a-file"
    taken.write_text("not a folder", encoding="utf-8")

    response = local.post("/settings", json={"provider": "ollama", "mode": "classic", "bundle": str(taken)})

    assert response.status_code == 422
    assert "cannot be used" in response.json()["detail"]
    assert not (home / ".config" / "llm-wiki" / "config.json").exists()
    assert local.get("/tree").status_code == 200  # the wiki it had still answers


def test_saving_without_a_needed_key_makes_no_wiki_folder(local: TestClient, rebuilt: list[WikiConfig], home: Path, tmp_path: Path) -> None:
    new = tmp_path / "new-wiki"

    response = local.post("/settings", json={"provider": "openrouter", "mode": "classic", "bundle": str(new)})

    assert response.status_code == 422
    assert not new.exists()
    assert rebuilt == []
    assert not (home / ".config" / "llm-wiki" / "config.json").exists()


def test_the_settings_test_asks_the_model_without_saving(local: TestClient, llm: FakeLLM, home: Path) -> None:
    llm.add("settings/probe", "OK")

    body = local.post("/settings/test", json={"provider": "ollama", "model": "qwen2.5", "mode": "classic"}).json()

    assert body == {"ok": True, "model": "qwen2.5", "reply": "OK"}
    assert not (home / ".config" / "llm-wiki").exists()


def test_settings_test_when_crow_then_the_classifier_is_asked_too(local: TestClient, llm: FakeLLM) -> None:
    # ARRANGE
    llm.add("settings/probe", "OK")

    # ACT
    body = local.post("/settings/test", json={"provider": "ollama", "mode": "crow", "classifier_provider": "typesafe"}).json()

    # ASSERT
    assert body["ok"] is True
    assert body["classifier"]["ok"] is False and body["classifier"]["model"] == "typesafe/jev-1.13"
    assert body["classifier"]["error"].startswith("no API key for the CROW classifier on TypeSafe")


def test_settings_test_when_the_classifier_answers_then_it_is_ok(
    classic_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    llm.add("settings/probe", "OK")
    app = create_app(classic_wiki, settings=Settings({}), make_wiki=lambda cfg: Wiki(cfg, llm=llm, classifier=classifier))

    # ACT
    body = TestClient(app, **LOCAL).post("/settings/test", json={"provider": "ollama", "mode": "crow"}).json()

    # ASSERT
    assert body["classifier"] == {"ok": True, "model": "typesafe/jev-1.13"}
    assert classifier.ops == ["settings/probe"]


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
    assert app.post("/settings/models", json={"base_url": "http://evil.test/v1"}, headers=headers).status_code == 403
    assert app.get("/settings", headers=headers).json()["editable"] is False
    assert not (home / ".config" / "llm-wiki").exists()


def test_the_same_origin_page_may_change_settings(local: TestClient) -> None:
    assert local.post("/settings", json={"api_key": KEY}, headers={"origin": "http://localhost:8000"}).status_code == 200


def test_without_settings_the_endpoints_are_absent(client: TestClient) -> None:
    assert client.get("/settings").status_code == 404
    assert client.post("/settings", json={}).status_code == 404


def test_the_page_gets_the_models_of_the_provider_being_chosen(local: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from llmw2 import server

    asked: list[Any] = []
    monkeypatch.setattr(server, "live_models", lambda llm: asked.append(llm) or ["gemini-3.8-flash"])

    body = local.post("/settings/models", json={"provider": "gemini", "api_key": "g-key-0123456789"}).json()

    assert body == {"models": ["gemini-3.8-flash"]}
    assert (asked[0].provider, asked[0].api_key) == ("gemini", "g-key-0123456789")


def test_settings_post_when_a_field_is_unknown_then_it_is_ignored_and_the_rest_saved(local: TestClient, home: Path) -> None:
    # ACT
    response = local.post("/settings", json={"api_key": KEY, "usage_log": "/etc/passwd"})

    # ASSERT
    assert response.status_code == 200
    assert "usage_log" not in (home / ".config" / "llm-wiki" / "config.json").read_text(encoding="utf-8")


def test_settings_post_when_a_value_is_not_a_string_then_422_and_nothing_saved(local: TestClient, home: Path) -> None:
    # ACT
    response = local.post("/settings", json={"api_key": 12345})

    # ASSERT
    assert response.status_code == 422
    assert not (home / ".config" / "llm-wiki").exists()


@pytest.mark.parametrize("path", ["/settings/test", "/settings/models"])
def test_settings_try_when_changes_are_invalid_then_422(local: TestClient, path: str) -> None:
    # ACT
    response = local.post(path, json={"provider": "custom"})

    # ASSERT
    assert response.status_code == 422 and "needs a base_url and a model" in response.json()["detail"]



def test_saving_the_memory_switch_rebuilds_the_wiki_with_it(local: TestClient, rebuilt: list[WikiConfig]) -> None:
    body = local.post("/settings", json={"mcp": "true", "api_key": KEY}).json()

    assert (body["values"]["mcp"], rebuilt[-1].mcp) == (True, True)


def test_without_the_mcp_extra_mcp_says_what_to_install_and_the_page_knows(
    classic_wiki: Wiki, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARRANGE: a base install, without llmwiki2[mcp]
    monkeypatch.setattr("llmw2.server.mcp_installed", lambda: False)

    # ACT
    with TestClient(create_app(classic_wiki, settings=Settings({})), **LOCAL) as client:
        mcp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        view = client.get("/settings").json()

    # ASSERT
    assert (mcp.status_code, mcp.json()["detail"]) == (501, NO_MCP)
    assert view["mcp_installed"] is False
