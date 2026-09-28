# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from okf_wiki import Wiki, WikiConfig
from okf_wiki.models.usage import UsageTracker
from tests.wiki.fakes import FakeClassifier, FakeLLM

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh home and no model settings from the shell: no test sees ~/.config/llm-wiki or a real key."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    for key in [k for k in os.environ if k.startswith("OKF_") or k.endswith("_API_KEY") or k == "XDG_CONFIG_HOME"]:
        monkeypatch.delenv(key)
    return home


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests talk to fakes only: a real model call would pass silently through the LLM fallback."""
    connect = socket.socket.connect

    def local_only(sock: socket.socket, address: Any) -> None:
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise OSError(f"network access in a test: {address}")
        connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_only)


@pytest.fixture
def tracker() -> UsageTracker:
    return UsageTracker()


@pytest.fixture
def llm(tracker: UsageTracker) -> FakeLLM:
    return FakeLLM(tracker)


@pytest.fixture
def classifier(tracker: UsageTracker) -> FakeClassifier:
    return FakeClassifier(tracker)


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return tmp_path / "wiki"


@pytest.fixture
def classic_wiki(bundle: Path, llm: FakeLLM) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm)
    wiki.init()
    return wiki


@pytest.fixture
def crow_wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier)
    wiki.init()
    return wiki


@pytest.fixture
def client(classic_wiki: Wiki) -> TestClient:
    """The HTTP API over the classic wiki; fastapi is imported here as it is an optional extra."""
    from fastapi.testclient import TestClient

    from okf_wiki.server import create_app

    return TestClient(create_app(classic_wiki))
