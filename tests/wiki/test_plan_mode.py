# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: the `plan` setting ("writer" or "groups", OKF_PLAN) decides how a CROW batch on a wiki with no folders is
planned, whatever the writer's provider; a wiki that already has folders and classic mode never use `plan_groups`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from llmw2 import Wiki, WikiConfig
from llmw2.models.usage import UsageTracker
from tests.wiki.test_crow_plan import (
    OLLAMA,
    PLAN,
    PLAN_MORE,
    PlanLLM,
    add_folder,
    filed_all,
    item,
    make_crow,
    plans,
)

OPENROUTER = {"provider": "openrouter", "model": "some/model"}
PROVIDERS = [
    pytest.param({}, id="default-provider"),
    pytest.param(OPENROUTER, id="openrouter"),
    pytest.param(OLLAMA, id="ollama"),
]


def spy_on_groups(wiki: Wiki, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replace `plan_groups` by a no-op that records the number of documents of each window."""
    calls: list[int] = []
    monkeypatch.setattr(wiki, "plan_groups", lambda documents, seen=None: calls.append(len(documents)) or {})
    return calls


def forbid_groups(wiki: Wiki, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiki, "plan_groups", lambda documents, seen=None: pytest.fail("this batch is not planned by groups"))


# -- the setting ----------------------------------------------------------------------


def test_plan_defaults_to_the_writer(tmp_path: Path) -> None:
    assert WikiConfig(bundle=tmp_path).plan == "writer"


@pytest.mark.parametrize("value", ["writer", "groups"])
def test_plan_is_read_from_the_okf_plan_variable(tmp_path: Path, value: str) -> None:
    # ARRANGE
    env = {"OKF_BUNDLE": str(tmp_path), "OKF_PLAN": value}

    # ACT
    config = WikiConfig.from_env(env)

    # ASSERT
    assert config.plan == value


def test_plan_without_the_variable_is_the_writer(tmp_path: Path) -> None:
    assert WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path)}).plan == "writer"
    assert WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_PLAN": ""}).plan == "writer"


def test_an_invalid_okf_plan_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="plan"):
        WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_PLAN": "oracle"})


def test_an_invalid_plan_is_rejected_when_the_config_is_built_directly(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="plan"):
        WikiConfig(bundle=tmp_path, plan="oracle")  # type: ignore[arg-type]


# -- plan="groups": an empty wiki is planned by groups, whatever the provider ---------------


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_batch_on_an_empty_wiki_with_plan_groups_is_planned_by_groups_whatever_the_provider(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, provider: dict[str, Any]
) -> None:
    # ARRANGE
    wiki, llm = make_crow(bundle, plan="groups", **({"llm": provider} if provider else {}))
    calls = spy_on_groups(wiki, monkeypatch)

    # ACT
    report = wiki.ingest_many([item(n) for n in range(52)])

    # ASSERT
    assert filed_all(report, 52)
    assert calls == [50, 2]  # every window, though the first one opened folders
    assert plans(llm) == []


@pytest.mark.parametrize("provider", PROVIDERS)
def test_plan_groups_files_the_documents_together_without_a_plan_prompt_whatever_the_provider(
    bundle: Path, provider: dict[str, Any]
) -> None:
    # ARRANGE
    wiki, llm = make_crow(bundle, plan="groups", **({"llm": provider} if provider else {}))

    # ACT
    report = wiki.ingest_many([item(n) for n in range(4)])

    # ASSERT
    assert filed_all(report, 4)
    assert plans(llm) == []
    assert len(wiki.store.load().all_notes()) == 4


# -- plan="writer" (the default): the writer plans, also a local one -------------------------------


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_batch_on_an_empty_wiki_with_the_default_plan_is_planned_by_the_writer_whatever_the_provider(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, provider: dict[str, Any]
) -> None:
    # ARRANGE
    wiki, llm = make_crow(bundle, **({"llm": provider} if provider else {}))
    forbid_groups(wiki, monkeypatch)

    # ACT
    report = wiki.ingest_many([item(n) for n in range(3)])

    # ASSERT
    assert filed_all(report, 3)
    assert plans(llm) == [PLAN]


def test_a_local_writer_with_plan_writer_asks_the_writers_plan_not_groups(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # ARRANGE
    wiki, llm = make_crow(bundle, plan="writer", llm=OLLAMA)
    forbid_groups(wiki, monkeypatch)

    # ACT
    report = wiki.ingest_many([item(n) for n in range(3)])

    # ASSERT
    assert filed_all(report, 3)
    assert plans(llm) == [PLAN]
    assert [f.label for f in wiki.store.load().walk()][1:] == ["/red"]  # the folder the writer's plan proposed


# -- a wiki with folders, and classic mode: never plan_groups ---------------------------------


@pytest.mark.parametrize("plan", ["writer", "groups"])
@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_batch_on_a_wiki_with_folders_gets_plan_more_never_groups_whatever_the_plan(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, plan: str, provider: dict[str, Any]
) -> None:
    # ARRANGE
    wiki, llm = make_crow(bundle, plan=plan, **({"llm": provider} if provider else {}))
    add_folder(wiki, "/", "red", "Red things.")
    llm.plan_more_reply = {"folders": []}
    forbid_groups(wiki, monkeypatch)

    # ACT
    report = wiki.ingest_many([item(n) for n in range(3)])

    # ASSERT
    assert filed_all(report, 3)
    assert plans(llm) == [PLAN_MORE]


@pytest.mark.parametrize("plan", ["writer", "groups"])
@pytest.mark.parametrize("provider", PROVIDERS)
def test_classic_mode_never_uses_plan_groups_whatever_the_plan(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, plan: str, provider: dict[str, Any]
) -> None:
    # ARRANGE
    llm = PlanLLM(UsageTracker())
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", concurrency=1, plan=plan, **({"llm": provider} if provider else {})), llm=llm)
    wiki.init()
    forbid_groups(wiki, monkeypatch)

    # ACT
    report = wiki.ingest_many([item(n) for n in range(4)])

    # ASSERT
    assert filed_all(report, 4)
    assert plans(llm) == []
