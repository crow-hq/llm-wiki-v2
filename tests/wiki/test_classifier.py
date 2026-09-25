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

"""The typed classifier against a mocked System One endpoint (POST {base_url}/systemone)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from okf_wiki.classifier import MAX_OPTIONS, ChoiceAnswer, Classifier
from okf_wiki.client import HttpModel, ModelError
from okf_wiki.config import ClassifierConfig
from okf_wiki.usage import UsageTracker

BASE_URL = "https://router.test/api/v1"
MODEL = "typesafe/jev-test"
USAGE = {"input_tokens": 40, "output_tokens": 2}

Handler = Callable[[httpx.Request], httpx.Response]
Reply = httpx.Response | Exception | Handler


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpModel, "backoff", 0)


def serve(*replies: Reply) -> tuple[httpx.Client, list[httpx.Request]]:
    """A real client over a mock transport: one reply per request, the last one repeating.

    A reply is a response, an exception to raise, or a function of the request.
    """
    queue = list(replies)
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        if not isinstance(reply, httpx.Response):
            return reply(request)
        return httpx.Response(reply.status_code, headers=reply.headers, content=reply.content)

    return httpx.Client(transport=httpx.MockTransport(handle)), requests


def make_classifier(
    tracker: UsageTracker, *replies: Reply, request_chars: int = 90_000
) -> tuple[Classifier, list[httpx.Request]]:
    client, requests = serve(*replies)
    cfg = ClassifierConfig(base_url=BASE_URL, model=MODEL, api_key="or-key", request_chars=request_chars)
    return Classifier(cfg, tracker, client=client), requests


def sent(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


def answered(answers: Any, usage: dict[str, int] | None = USAGE) -> httpx.Response:
    body: dict[str, Any] = {"answers": answers}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def answer_each(answer: Callable[[str, dict[str, Any]], Any]) -> Handler:
    """A reply that answers every question of the request with `answer(key, question)`."""

    def reply(request: httpx.Request) -> httpx.Response:
        questions = sent(request)["questions"]
        return answered({key: answer(key, question) for key, question in questions.items()})

    return reply


def always(answer: Any) -> Handler:
    """A reply that gives every question of the request the same answer."""
    return answer_each(lambda key, question: answer)


def noul(p: Any) -> Handler:
    return always({"type": "noul", "noul": p})


ROUTE_OPTIONS = {"science": "Science notes", "Here": "File it here", "New subfolder": "Open a new folder"}
ROUTE_ANSWER = {
    "type": "choice",
    "choice": "science",
    "probabilities": {"science": 0.7, "Here": 0.2, "New subfolder": 0.1},
    "confidence": 0.65,
}


def test_ask_posts_model_state_and_questions_to_systemone(tracker: UsageTracker) -> None:
    questions = {"q": {"type": "noul", "instructions": "The note is about cats."}}
    clf, requests = make_classifier(tracker, answered({"q": {"type": "noul", "noul": 0.8}}))

    assert clf.ask("A note about cats.", questions) == {"q": {"type": "noul", "noul": 0.8}}

    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/systemone"
    assert request.headers["authorization"] == "Bearer or-key"
    assert sent(request) == {"model": MODEL, "state": "A note about cats.", "questions": questions}


def test_choice_sends_one_choice_question(tracker: UsageTracker) -> None:
    clf, requests = make_classifier(tracker, always(ROUTE_ANSWER))

    clf.choice("A note about photosynthesis.", "Where does it belong?", ROUTE_OPTIONS, op="route")

    [request] = requests
    assert sent(request)["state"] == "A note about photosynthesis."
    assert list(sent(request)["questions"].values()) == [
        {"type": "choice", "instructions": "Where does it belong?", "criteria": ROUTE_OPTIONS}
    ]


def test_choice_parses_choice_probabilities_and_confidence(tracker: UsageTracker) -> None:
    clf, _ = make_classifier(tracker, always(ROUTE_ANSWER))

    answer = clf.choice("state", "Where does it belong?", ROUTE_OPTIONS)

    assert answer == ChoiceAnswer("science", {"science": 0.7, "Here": 0.2, "New subfolder": 0.1}, 0.65)


@pytest.mark.parametrize("count", [2, MAX_OPTIONS])
def test_choice_accepts_two_to_255_options(tracker: UsageTracker, count: int) -> None:
    options = {f"o{i}": f"option {i}" for i in range(count)}
    picked = {"choice": "o1", "probabilities": {"o0": 0.1, "o1": 0.9}, "confidence": 0.8}
    clf, requests = make_classifier(tracker, always(picked))

    assert clf.choice("state", "Pick one.", options).choice == "o1"
    assert len(requests) == 1


@pytest.mark.parametrize("count", [0, 1, MAX_OPTIONS + 1])
def test_choice_rejects_an_option_count_out_of_range_before_any_request(tracker: UsageTracker, count: int) -> None:
    options = {f"o{i}": f"option {i}" for i in range(count)}
    clf, requests = make_classifier(tracker, always(ROUTE_ANSWER))

    with pytest.raises(ModelError):
        clf.choice("state", "Pick one.", options)

    assert requests == []


def test_nouls_send_noul_questions_and_return_floats(tracker: UsageTracker) -> None:
    questions = {"cats": "The note is about cats.", "dogs": "The note is about dogs."}
    scores = {"cats": 1, "dogs": 0.25}
    clf, requests = make_classifier(tracker, answer_each(lambda key, q: {"type": "noul", "noul": scores[key]}))

    result = clf.nouls("A note about cats.", questions, op="match")

    assert result == {"cats": 1.0, "dogs": 0.25}
    assert all(isinstance(p, float) for p in result.values())
    [request] = requests
    assert sent(request)["questions"] == {
        "cats": {"type": "noul", "instructions": "The note is about cats."},
        "dogs": {"type": "noul", "instructions": "The note is about dogs."},
    }


def test_nouls_without_questions_make_no_request(tracker: UsageTracker) -> None:
    clf, requests = make_classifier(tracker, noul(0.5))

    assert clf.nouls("state", {}) == {}
    assert requests == []


def test_nouls_are_split_over_requests_that_fit_request_chars(tracker: UsageTracker) -> None:
    state = "S" * 20
    questions = {f"n{i}": f"Statement number {i}." for i in range(5)}  # 19 characters each
    clf, requests = make_classifier(
        tracker, answer_each(lambda key, q: {"type": "noul", "noul": int(key[1:]) / 10}), request_chars=65
    )

    result = clf.nouls(state, questions, op="relate")

    batches = [sent(r)["questions"] for r in requests]
    assert [sorted(b) for b in batches] == [["n0", "n1"], ["n2", "n3"], ["n4"]]
    assert all(sent(r)["state"] == state for r in requests)
    assert all(len(state) + sum(len(q["instructions"]) for q in b.values()) <= 65 for b in batches)
    assert result == {"n0": 0.0, "n1": 0.1, "n2": 0.2, "n3": 0.3, "n4": 0.4}


def test_batched_nouls_record_usage_once_per_request(tracker: UsageTracker) -> None:
    questions = {f"n{i}": f"Statement number {i}." for i in range(5)}
    clf, requests = make_classifier(tracker, noul(0.5), request_chars=40)

    clf.nouls("state", questions)

    classifier = tracker.snapshot().classifier
    assert len(requests) > 1
    n = len(requests)
    assert (classifier.calls, classifier.input_tokens, classifier.output_tokens) == (n, 40 * n, 2 * n)
    assert tracker.snapshot().llm.calls == 0


def test_usage_is_recorded_in_the_classifier_ledger_of_the_model(tracker: UsageTracker) -> None:
    clf, _ = make_classifier(tracker, noul(0.5))

    clf.nouls("state", {"a": "A statement."})

    report = tracker.snapshot()
    assert (report.classifier.calls, report.classifier.input_tokens, report.classifier.output_tokens) == (1, 40, 2)
    assert report.by_model[f"classifier:{MODEL}"].total_tokens == 42
    assert report.llm.calls == 0


def test_answers_without_usage_count_as_missing(tracker: UsageTracker) -> None:
    clf, _ = make_classifier(tracker, answered({"a": {"type": "noul", "noul": 0.5}}, usage=None))

    clf.nouls("state", {"a": "A statement."})

    classifier = tracker.snapshot().classifier
    assert (classifier.calls, classifier.missing) == (1, 1)


def test_answers_missing_a_question_raise(tracker: UsageTracker) -> None:
    clf, _ = make_classifier(tracker, answered({"a": {"type": "noul", "noul": 0.5}}))

    with pytest.raises(ModelError):
        clf.nouls("state", {"a": "First statement.", "b": "Second statement."})


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<!doctype html><html><body>OpenRouter</body></html>"),
        answered(["a"]),
        httpx.Response(200, json={"error": {"message": "unknown model"}}),
        httpx.Response(200, json=[{"a": {"type": "noul", "noul": 0.5}}]),
    ],
    ids=["html-page", "answers-list", "no-answers", "top-level-list"],
)
def test_answers_that_are_not_a_dict_raise(tracker: UsageTracker, response: httpx.Response) -> None:
    clf, _ = make_classifier(tracker, response)

    with pytest.raises(ModelError):
        clf.nouls("state", {"a": "A statement."})


@pytest.mark.parametrize(
    "answer",
    [{"type": "noul"}, {"type": "noul", "noul": "likely"}, {"type": "noul", "noul": None}, "yes", None],
    ids=["missing", "not-a-number", "null", "string", "none"],
)
def test_a_malformed_noul_answer_raises(tracker: UsageTracker, answer: Any) -> None:
    clf, _ = make_classifier(tracker, always(answer))

    with pytest.raises(ModelError, match="malformed noul"):
        clf.nouls("state", {"a": "A statement."})


@pytest.mark.parametrize(
    "answer",
    [
        {"choice": "Here", "confidence": 0.9},
        {"probabilities": {"Here": 0.9}, "confidence": 0.9},
        {"choice": "Here", "probabilities": {"Here": 0.9}},
        {"choice": "Here", "probabilities": [0.9, 0.1], "confidence": 0.9},
        {"choice": "Here", "probabilities": {"Here": "most"}, "confidence": 0.9},
        {"choice": "Here", "probabilities": {"Here": 0.9}, "confidence": "high"},
        "Here",
    ],
    ids=[
        "no-probabilities",
        "no-choice",
        "no-confidence",
        "list-probabilities",
        "text-probability",
        "text-confidence",
        "string",
    ],
)
def test_a_malformed_choice_answer_raises(tracker: UsageTracker, answer: Any) -> None:
    clf, _ = make_classifier(tracker, always(answer))

    with pytest.raises(ModelError, match="malformed choice"):
        clf.choice("state", "Where does it belong?", ROUTE_OPTIONS)


@pytest.mark.parametrize("status", [429, 529])
def test_overload_is_retried(tracker: UsageTracker, status: int) -> None:
    clf, requests = make_classifier(tracker, httpx.Response(status, text="overloaded"), noul(0.7))

    assert clf.nouls("state", {"a": "A statement."}) == {"a": 0.7}
    assert len(requests) == 2
