# Copyright 2026 Federico Cesarini
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

"""The writing model against a mocked OpenAI-compatible `/chat/completions` endpoint."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from okf_wiki.client import HttpModel, ModelError, reported_cost
from okf_wiki.config import LLMConfig
from okf_wiki.llm import LLM, extract_json
from okf_wiki.usage import UsageTracker

BASE_URL = "http://bifrost.test/v1"
MODEL = "openrouter/test/writer"

Reply = httpx.Response | Exception


class Title(BaseModel):
    title: str


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpModel, "backoff", 0)


def serve(*replies: Reply) -> tuple[httpx.Client, list[httpx.Request]]:
    """A real client over a mock transport: one reply per request, the last one repeating."""
    queue = list(replies)
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(reply.status_code, headers=reply.headers, content=reply.content)

    return httpx.Client(transport=httpx.MockTransport(handle)), requests


def make_llm(tracker: UsageTracker, *replies: Reply, api_key: str = "") -> tuple[LLM, list[httpx.Request]]:
    client, requests = serve(*replies)
    cfg = LLMConfig(base_url=f"{BASE_URL}/", model=MODEL, api_key=api_key, temperature=0.3)
    return LLM(cfg, tracker, client=client), requests


def completion(content: str | None, usage: dict[str, int] | None = None) -> httpx.Response:
    body: dict[str, Any] = {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


def sent(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


def test_complete_posts_system_and_user_messages(tracker: UsageTracker) -> None:
    llm, requests = make_llm(tracker, completion("Hello."))

    assert llm.complete("You are terse.", "Say hello.") == "Hello."

    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert sent(request) == {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "Say hello."},
        ],
        "temperature": 0.3,
    }


@pytest.mark.parametrize(("api_key", "authorization"), [("sk-test", "Bearer sk-test"), ("", None)])
def test_bearer_auth_is_sent_only_with_an_api_key(
    tracker: UsageTracker, api_key: str, authorization: str | None
) -> None:
    llm, requests = make_llm(tracker, completion("ok"), api_key=api_key)

    llm.complete("system", "user")

    assert requests[0].headers.get("authorization") == authorization


def test_usage_is_recorded_in_the_llm_ledger_of_the_model(tracker: UsageTracker) -> None:
    llm, _ = make_llm(tracker, completion("ok", {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}))

    llm.complete("system", "user")

    report = tracker.snapshot()
    assert (report.llm.calls, report.llm.input_tokens, report.llm.output_tokens, report.llm.missing) == (1, 12, 3, 0)
    assert report.by_model[f"llm:{MODEL}"].total_tokens == 15
    assert report.classifier.calls == 0


def test_a_reply_without_usage_counts_as_missing(tracker: UsageTracker) -> None:
    llm, _ = make_llm(tracker, completion("ok"))

    llm.complete("system", "user")

    llm_usage = tracker.snapshot().llm
    assert (llm_usage.calls, llm_usage.missing, llm_usage.total_tokens) == (1, 1, 0)


@pytest.mark.parametrize(
    "reply",
    [
        '```json\n{"title": "Cats"}\n```',
        'Sure, here it is:\n{"title": "Cats"}\nLet me know if you need anything else.',
    ],
    ids=["code-fence", "chatter"],
)
def test_json_parses_a_wrapped_object(tracker: UsageTracker, reply: str) -> None:
    llm, requests = make_llm(tracker, completion(reply))

    assert llm.json("system", "Name it.", Title) == Title(title="Cats")
    assert len(requests) == 1


def test_json_retries_once_with_the_validation_error(tracker: UsageTracker) -> None:
    llm, requests = make_llm(tracker, completion('{"name": "Cats"}'), completion('{"title": "Cats"}'))

    assert llm.json("system", "Name it.", Title) == Title(title="Cats")

    first, second = (sent(r)["messages"] for r in requests)
    assert "single JSON object" in first[-1]["content"]
    assert second[: len(first)] == first
    assert second[len(first)] == {"role": "assistant", "content": '{"name": "Cats"}'}
    correction = second[len(first) + 1]
    assert correction["role"] == "user"
    assert "not valid" in correction["content"]
    assert "title" in correction["content"] and "Field required" in correction["content"]
    assert len(second) == len(first) + 2


def test_json_raises_after_two_invalid_replies(tracker: UsageTracker) -> None:
    llm, requests = make_llm(tracker, completion("I would rather not answer in JSON."))

    with pytest.raises(ModelError, match="invalid"):
        llm.json("system", "Name it.", Title)

    assert len(requests) == 2


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(429, text="slow down"), httpx.Response(503, text="busy"), httpx.ConnectError("refused")],
    ids=["429", "503", "transport-error"],
)
def test_transient_failures_are_retried(tracker: UsageTracker, failure: Reply) -> None:
    llm, requests = make_llm(tracker, failure, completion("ok"))

    assert llm.complete("system", "user") == "ok"
    assert len(requests) == 2


def test_a_persistent_transport_error_raises_after_every_attempt(tracker: UsageTracker) -> None:
    llm, requests = make_llm(tracker, httpx.ConnectError("connection refused"))

    with pytest.raises(ModelError, match="connection refused"):
        llm.complete("system", "user")

    assert len(requests) == HttpModel.attempts


def test_a_persistent_rate_limit_raises_after_every_attempt(tracker: UsageTracker) -> None:
    llm, requests = make_llm(tracker, httpx.Response(429, text="slow down"))

    with pytest.raises(ModelError, match="429"):
        llm.complete("system", "user")

    assert len(requests) == HttpModel.attempts


def test_a_client_error_raises_without_retry(tracker: UsageTracker) -> None:
    llm, requests = make_llm(tracker, httpx.Response(400, json={"error": {"message": "unknown model"}}))

    with pytest.raises(ModelError, match="400"):
        llm.complete("system", "user")

    assert len(requests) == 1


@pytest.mark.parametrize(
    "payload",
    [{"choices": []}, {"error": {"message": "overloaded"}}, {"choices": [{"text": "legacy completion"}]}],
    ids=["empty-choices", "no-choices", "no-message"],
)
def test_a_reply_without_a_message_raises(tracker: UsageTracker, payload: dict[str, Any]) -> None:
    llm, _ = make_llm(tracker, httpx.Response(200, json=payload))

    with pytest.raises(ModelError):
        llm.complete("system", "user")


def test_a_non_json_reply_raises(tracker: UsageTracker) -> None:
    llm, _ = make_llm(tracker, httpx.Response(200, text="<html><body>Bifrost dashboard</body></html>"))

    with pytest.raises(ModelError, match="non-JSON"):
        llm.complete("system", "user")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        ('  {"a": 1}\n', '{"a": 1}'),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('Sure!\n```json\n{"a": 1}\n```\nAnything else?', '{"a": 1}'),
        ('Here you go: {"a": {"b": [1, 2]}} Hope it helps.', '{"a": {"b": [1, 2]}}'),
        ("no object here", "no object here"),
    ],
    ids=["bare", "whitespace", "json-fence", "plain-fence", "fence-and-chatter", "nested-with-chatter", "no-object"],
)
def test_extract_json(text: str, expected: str) -> None:
    assert extract_json(text) == expected


@pytest.mark.parametrize(("usage", "cost"), [({"cost": 0.0021}, 0.0021), ({"cost": {"total_cost": 3.4e-05}}, 3.4e-05), ({}, None), ({"cost": "x"}, None)])
def test_reported_cost_reads_openrouter_and_bifrost_shapes(usage: dict[str, Any], cost: float | None) -> None:
    assert reported_cost(usage) == cost


def test_extra_body_is_merged_and_bifrost_asked_to_forward_it() -> None:
    client, requests = serve(completion("ok"))
    cfg = LLMConfig(base_url=BASE_URL, model=MODEL, extra_body={"provider": {"order": ["together"]}})

    LLM(cfg, UsageTracker(), client=client).complete("sys", "user")

    body = json.loads(requests[0].content)
    assert body["provider"] == {"order": ["together"]} and body["model"] == MODEL
    assert requests[0].headers["x-bf-passthrough-extra-params"] == "true"


def test_no_extra_body_means_no_passthrough_header() -> None:
    client, requests = serve(completion("ok"))
    LLM(LLMConfig(base_url=BASE_URL, model=MODEL), UsageTracker(), client=client).complete("sys", "user")
    assert "x-bf-passthrough-extra-params" not in requests[0].headers


def test_cached_tokens_are_read_from_the_usage_block() -> None:
    usage = {"prompt_tokens": 900, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 256}}
    client, _ = serve(completion("ok", usage))
    tracker = UsageTracker()

    LLM(LLMConfig(base_url=BASE_URL, model=MODEL), tracker, client=client).complete("s", "u")

    assert tracker.snapshot().llm.cached_tokens == 256
