# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The HTTP base shared by both model clients: the missing-key guard, waits between attempts, closing."""

from __future__ import annotations

import httpx
import pytest

from llmw2.config import ClassifierConfig, LLMConfig
from llmw2.errors import ConfigError, ModelError
from llmw2.models import client as client_module
from llmw2.models.classifier import Classifier
from llmw2.models.client import HttpModel
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageTracker
from tests.wiki.test_llm import BASE_URL, MODEL, completion, make_llm, serve


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpModel, "backoff", 0)


# -- HttpModel: a missing API key ---------------------------------------------------------------


def test_complete_when_the_provider_needs_a_key_and_none_is_set_then_raises_before_any_request(
    tracker: UsageTracker,
) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, completion("ok"), api_key="", provider="openrouter")

    # ACT
    with pytest.raises(ModelError, match="no API key for the llm"):
        llm.complete("system", "user")

    # ASSERT
    assert (requests, tracker.snapshot().llm.calls) == ([], 0)


def test_complete_when_the_provider_takes_no_key_and_none_is_set_then_posts_without_auth(
    tracker: UsageTracker,
) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, completion("ok"), api_key="", provider="ollama")

    # ACT
    reply = llm.complete("system", "user")

    # ASSERT
    [request] = requests
    assert (reply, request.headers.get("authorization")) == ("ok", None)


def test_ask_when_the_classifier_has_no_key_then_a_config_error_says_where_to_put_it(tracker: UsageTracker) -> None:
    # ARRANGE
    client, requests = serve(httpx.Response(200, json={"answers": {}}))
    clf = Classifier(ClassifierConfig(base_url=BASE_URL, model=MODEL), tracker, client=client)

    # ACT
    with pytest.raises(ConfigError, match="no API key for the CROW classifier on OpenRouter: paste it in Settings → Advanced"):
        clf.ask("A note about cats.", {"q": {"type": "noul", "instructions": "The note is about cats."}})

    # ASSERT
    assert (requests, tracker.snapshot().classifier.calls) == ([], 0)


@pytest.mark.parametrize("status", [401, 403])
def test_ask_when_the_provider_refuses_the_classifier_key_then_a_config_error_not_a_fallback(tracker: UsageTracker, status: int) -> None:
    # ARRANGE
    client, _ = serve(httpx.Response(status, json={"error": "invalid key"}))
    clf = Classifier(ClassifierConfig(base_url=BASE_URL, model=MODEL, api_key="wrong"), tracker, client=client)

    # ACT / ASSERT
    with pytest.raises(ConfigError, match=f"refused its key \\({status}\\)"):
        clf.ask("A note about cats.", {"q": {"type": "noul", "instructions": "The note is about cats."}})


# -- HttpModel: the status on a failed request ------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "status"),
    [(httpx.Response(401, text="invalid key"), 401), (httpx.Response(404, text="no such model"), 404),
     (httpx.ConnectError("refused"), None)],
    ids=["401", "404", "transport-error"],
)
def test_complete_when_the_request_fails_then_the_error_carries_the_http_status(
    tracker: UsageTracker, failure: httpx.Response | Exception, status: int | None
) -> None:
    # ARRANGE
    llm, _ = make_llm(tracker, failure)

    # ACT
    with pytest.raises(ModelError) as raised:
        llm.complete("system", "user")

    # ASSERT
    assert raised.value.status == status


# -- HttpModel: waiting between attempts, and closing --------------------------------------------


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every wait of the retry loop, instead of sleeping."""
    waits: list[float] = []
    monkeypatch.setattr(client_module.time, "sleep", waits.append)
    return waits


def busy(status: int = 503, retry_after: str | None = None) -> httpx.Response:
    return httpx.Response(status, headers={"retry-after": retry_after} if retry_after else {}, text="busy")


def test_post_when_the_provider_sends_retry_after_then_waits_that_long(tracker: UsageTracker, slept: list[float]) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, busy(429, "2"), completion("ok"))

    # ACT
    reply = llm.complete("system", "user")

    # ASSERT
    assert (reply, len(requests), slept) == ("ok", 2, [2.0])


def test_post_when_retry_after_exceeds_the_cap_then_raises_without_waiting(tracker: UsageTracker, slept: list[float]) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, busy(503, str(int(HttpModel.max_retry_after) + 1)))

    # ACT
    with pytest.raises(ModelError, match="busy for"):
        llm.complete("system", "user")

    # ASSERT
    assert (len(requests), slept) == (1, [])


def test_post_when_retry_after_is_not_a_number_then_waits_the_backoff(
    tracker: UsageTracker, slept: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARRANGE
    monkeypatch.setattr(HttpModel, "backoff", 1.0)
    llm, _ = make_llm(tracker, busy(503, "Wed, 21 Oct 2026 07:28:00 GMT"), completion("ok"))

    # ACT
    llm.complete("system", "user")

    # ASSERT
    [wait] = slept
    assert 1.0 <= wait <= 2.0  # the backoff plus up to as much jitter


def test_post_when_backoff_is_zero_and_no_retry_after_then_never_waits(tracker: UsageTracker, slept: list[float]) -> None:
    # ARRANGE
    llm, _ = make_llm(tracker, busy(503), httpx.ConnectError("refused"), completion("ok"))

    # ACT
    llm.complete("system", "user")

    # ASSERT
    assert slept == [0.0, 0.0]


def test_close_when_the_model_made_its_client_then_closes_it_also_as_context_manager(tracker: UsageTracker) -> None:
    # ARRANGE
    llm = LLM(LLMConfig(base_url=BASE_URL, model=MODEL), tracker)

    # ACT
    with llm as entered:
        pass

    # ASSERT
    assert entered is llm and llm.client.is_closed


def test_close_when_the_client_was_passed_in_then_leaves_it_open(tracker: UsageTracker) -> None:
    # ARRANGE
    llm, _ = make_llm(tracker, completion("ok"))

    # ACT
    llm.close()

    # ASSERT
    assert not llm.client.is_closed
