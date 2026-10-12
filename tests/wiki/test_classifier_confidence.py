# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Confidence is computed by the client from the probabilities, never read from the server."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from llmw2.config import ClassifierConfig
from llmw2.models.classifier import MAX_OPTIONS, Classifier, confidence
from llmw2.models.client import HttpModel, ModelError
from llmw2.models.usage import UsageTracker

BASE_URL = "https://router.test/api/v1"
MODEL = "typesafe/jev-test"


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpModel, "backoff", 0)


def make_classifier(tracker: UsageTracker, answer: Any) -> tuple[Classifier, list[httpx.Request]]:
    """A classifier whose server gives every question the same `answer`."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        import json

        requests.append(request)
        questions = json.loads(request.content)["questions"]
        body = {"answers": {key: answer for key in questions}, "usage": {"input_tokens": 1, "output_tokens": 1}}
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    cfg = ClassifierConfig(base_url=BASE_URL, model=MODEL, api_key="or-key", attempts=1)
    return Classifier(cfg, tracker, client=client), requests


def options_of(count: int) -> dict[str, str]:
    return {f"o{i}": f"option {i}" for i in range(count)}


# -- the formula ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("top", "n", "expected"),
    [
        (0.867, 2, 0.734),
        (0.5, 2, 0.0),
        (1.0, 2, 1.0),
        (0.52, 3, 0.28),
        (0.69, 4, 0.5867),
        (0.95, 7, 0.9417),
    ],
)
def test_confidence_when_worked_value_then_matches_the_formula(top: float, n: int, expected: float) -> None:
    probabilities = {"a": top, "b": 1 - top}

    assert confidence(probabilities, n) == pytest.approx(expected, abs=1e-3)


@pytest.mark.parametrize("n", [2, 3, 5, 10])
def test_confidence_when_uniform_then_zero(n: int) -> None:
    probabilities = {f"o{i}": 1 / n for i in range(n)}

    assert confidence(probabilities, n) == pytest.approx(0.0)


@pytest.mark.parametrize("n", [2, 3, 5, 10])
def test_confidence_when_certain_then_one(n: int) -> None:
    probabilities = {"o0": 1.0, **{f"o{i}": 0.0 for i in range(1, n)}}

    assert confidence(probabilities, n) == pytest.approx(1.0)


def test_confidence_when_top_probability_grows_then_grows_linearly() -> None:
    low = confidence({"a": 0.6, "b": 0.4}, 2)
    mid = confidence({"a": 0.7, "b": 0.3}, 2)
    high = confidence({"a": 0.8, "b": 0.2}, 2)

    assert mid - low == pytest.approx(high - mid)


def test_confidence_when_n_exceeds_the_keys_then_n_sets_the_floor() -> None:
    # the server omitted the options it gave 0: only one key, but four were asked
    assert confidence({"a": 0.7}, 4) == pytest.approx((0.7 - 0.25) / 0.75)


def test_confidence_when_n_exceeds_the_keys_then_differs_from_counting_the_keys() -> None:
    assert confidence({"a": 0.7, "b": 0.3}, 5) != pytest.approx(confidence({"a": 0.7, "b": 0.3}, 2))


# -- Classifier.choice ---------------------------------------------------------------------------


def test_choice_when_answered_then_confidence_is_computed_from_the_probabilities(tracker: UsageTracker) -> None:
    # ARRANGE
    answer = {"type": "choice", "choice": "o0", "probabilities": {"o0": 0.867, "o1": 0.133}, "confidence": 0.1}
    clf, _ = make_classifier(tracker, answer)

    # ACT
    result = clf.choice("state", "Pick one.", options_of(2))

    # ASSERT
    assert result.confidence == pytest.approx(0.734, abs=1e-3)


def test_choice_when_options_outnumber_the_keys_then_confidence_uses_the_option_count(tracker: UsageTracker) -> None:
    # ARRANGE
    answer = {"type": "choice", "choice": "o0", "probabilities": {"o0": 0.7}, "confidence": 0.0}
    clf, _ = make_classifier(tracker, answer)

    # ACT
    result = clf.choice("state", "Pick one.", options_of(4))

    # ASSERT
    assert result.confidence == pytest.approx((0.7 - 0.25) / 0.75)


@pytest.mark.parametrize(
    "server_confidence",
    [{"confidence": 0.01}, {"confidence": "high"}, {}],
    ids=["other-number", "string", "missing"],
)
def test_choice_when_server_confidence_varies_then_the_answer_confidence_is_unchanged(
    tracker: UsageTracker, server_confidence: dict[str, Any]
) -> None:
    # ARRANGE
    answer = {"type": "choice", "choice": "o0", "probabilities": {"o0": 0.52, "o1": 0.28, "o2": 0.2}, **server_confidence}
    clf, _ = make_classifier(tracker, answer)

    # ACT
    result = clf.choice("state", "Pick one.", options_of(3))

    # ASSERT
    assert result.confidence == pytest.approx(0.28, abs=1e-3)


def test_choice_when_answered_then_choice_is_passed_through(tracker: UsageTracker) -> None:
    answer = {"type": "choice", "choice": "o1", "probabilities": {"o0": 0.4, "o1": 0.6}}
    clf, _ = make_classifier(tracker, answer)

    assert clf.choice("state", "Pick one.", options_of(2)).choice == "o1"


def test_choice_when_answered_then_probabilities_are_floats_keyed_by_str(tracker: UsageTracker) -> None:
    # ARRANGE
    answer = {"type": "choice", "choice": "o0", "probabilities": {"o0": 1, "o1": 0}}
    clf, _ = make_classifier(tracker, answer)

    # ACT
    result = clf.choice("state", "Pick one.", options_of(2))

    # ASSERT
    assert result.probabilities == {"o0": 1.0, "o1": 0.0}
    assert all(isinstance(k, str) for k in result.probabilities)
    assert all(isinstance(p, float) for p in result.probabilities.values())


def test_choice_when_top_probability_is_one_then_confidence_is_one(tracker: UsageTracker) -> None:
    answer = {"type": "choice", "choice": "o0", "probabilities": {"o0": 1.0, "o1": 0.0}}
    clf, _ = make_classifier(tracker, answer)

    assert clf.choice("state", "Pick one.", options_of(2)).confidence == pytest.approx(1.0)


def test_choice_when_answer_is_a_coin_flip_then_confidence_is_zero(tracker: UsageTracker) -> None:
    answer = {"type": "choice", "choice": "o0", "probabilities": {"o0": 0.5, "o1": 0.5}}
    clf, _ = make_classifier(tracker, answer)

    assert clf.choice("state", "Pick one.", options_of(2)).confidence == pytest.approx(0.0)


# -- malformed answers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        {"type": "choice", "choice": "o0", "confidence": 0.9},
        {"type": "choice", "choice": "o0", "probabilities": [0.9, 0.1]},
        {"type": "choice", "choice": "o0", "probabilities": "o0"},
        {"type": "choice", "choice": "o0", "probabilities": {"o0": "most", "o1": 0.1}},
        {"type": "choice", "choice": "o0", "probabilities": {}},
    ],
    ids=["no-probabilities", "list-probabilities", "string-probabilities", "non-numeric-probability", "empty-probabilities"],
)
def test_choice_when_answer_is_malformed_then_raises_model_error(tracker: UsageTracker, answer: dict[str, Any]) -> None:
    clf, _ = make_classifier(tracker, answer)

    with pytest.raises(ModelError, match="malformed choice answer"):
        clf.choice("state", "Pick one.", options_of(2))


# -- option-count bounds -------------------------------------------------------------------------


@pytest.mark.parametrize("count", [2, MAX_OPTIONS])
def test_choice_when_option_count_is_within_bounds_then_sends_one_request(tracker: UsageTracker, count: int) -> None:
    # ARRANGE
    answer = {"type": "choice", "choice": "o0", "probabilities": {"o0": 0.9, "o1": 0.1}}
    clf, requests = make_classifier(tracker, answer)

    # ACT
    result = clf.choice("state", "Pick one.", options_of(count))

    # ASSERT
    assert len(requests) == 1
    assert result.confidence == pytest.approx((0.9 - 1 / count) / (1 - 1 / count))


@pytest.mark.parametrize("count", [0, 1, 256])
def test_choice_when_option_count_is_out_of_bounds_then_raises_before_any_request(tracker: UsageTracker, count: int) -> None:
    # ARRANGE
    answer = {"type": "choice", "choice": "o0", "probabilities": {"o0": 1.0}}
    clf, requests = make_classifier(tracker, answer)

    # ACT
    with pytest.raises(ModelError):
        clf.choice("state", "Pick one.", options_of(count))

    # ASSERT
    assert requests == []
