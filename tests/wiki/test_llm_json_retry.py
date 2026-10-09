# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`LLM.json` retry (original request, no broken reply, JSON mode) and `finish_reason` tracking."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from llmw2.config import LLMConfig
from llmw2.errors import ModelError
from llmw2.models.client import HttpModel
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageTracker

BASE_URL = "http://llm.test/v1"
MODEL = "openrouter/test/writer"
NOTE = "A previous reply to this request was not valid: "

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


def make_llm(tracker: UsageTracker, *replies: Reply, provider: str = "openrouter", **config: Any) -> tuple[LLM, list[httpx.Request]]:
    client, requests = serve(*replies)
    cfg = LLMConfig(provider=provider, base_url=f"{BASE_URL}/", model=MODEL, api_key="test-key", temperature=0.3, **config)
    return LLM(cfg, tracker, client=client), requests


def completion(content: str | None, finish_reason: str | None = None) -> httpx.Response:
    choice: dict[str, Any] = {"index": 0, "message": {"role": "assistant", "content": content}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return httpx.Response(200, json={"choices": [choice]})


def sent(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


GOOD = completion('{"title": "Cats"}')
BAD = completion('{"name": "Cats"}')
NOT_JSON = completion("I would rather not answer in JSON.")


def test_json_when_first_request_is_sent_then_it_has_no_response_format(tracker: UsageTracker) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, GOOD)

    # ACT
    result = llm.json("system", "Name it.", Title)

    # ASSERT
    assert result == Title(title="Cats")
    assert len(requests) == 1
    assert "response_format" not in sent(requests[0])


@pytest.mark.parametrize("broken", [BAD, NOT_JSON], ids=["schema-violation", "not-json"])
def test_json_when_reply_is_broken_then_retries_once_without_the_broken_reply(tracker: UsageTracker, broken: httpx.Response) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, broken, GOOD)

    # ACT
    result = llm.json("the system", "Name it.", Title)

    # ASSERT
    assert result == Title(title="Cats")
    assert len(requests) == 2
    first, second = (sent(r)["messages"] for r in requests)
    assert second[0] == first[0] == {"role": "system", "content": "the system"}
    assert [m["role"] for m in second] == ["system", "user"]
    assert second[1]["content"].startswith(first[-1]["content"])
    assert NOTE in second[1]["content"]
    assert not any(m["role"] == "assistant" for m in second)


def test_json_when_retrying_then_the_body_asks_for_json_object(tracker: UsageTracker) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, BAD, GOOD)

    # ACT
    llm.json("system", "Name it.", Title)

    # ASSERT
    assert sent(requests[1])["response_format"] == {"type": "json_object"}


def test_json_when_provider_is_openrouter_then_retry_requires_parameters(tracker: UsageTracker) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, BAD, GOOD)

    # ACT
    llm.json("system", "Name it.", Title)

    # ASSERT
    assert sent(requests[1])["provider"]["require_parameters"] is True


def test_json_when_provider_is_pinned_then_retry_keeps_the_pin(tracker: UsageTracker) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, BAD, GOOD, pin_provider=["Novita"])

    # ACT
    llm.json("system", "Name it.", Title)

    # ASSERT
    provider = sent(requests[1])["provider"]
    assert provider["order"] == ["Novita"]
    assert provider["allow_fallbacks"] is False
    assert provider["require_parameters"] is True


def test_json_when_extra_body_has_provider_then_retry_keeps_its_keys(tracker: UsageTracker) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, BAD, GOOD, extra_body={"provider": {"order": ["X"]}})

    # ACT
    llm.json("system", "Name it.", Title)

    # ASSERT
    provider = sent(requests[1])["provider"]
    assert provider["order"] == ["X"]
    assert provider["require_parameters"] is True


@pytest.mark.parametrize("provider", ["custom", "ollama"])
def test_json_when_provider_is_not_openrouter_then_retry_has_no_provider_key(tracker: UsageTracker, provider: str) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, BAD, GOOD, provider=provider)

    # ACT
    llm.json("system", "Name it.", Title)

    # ASSERT
    body = sent(requests[1])
    assert body["response_format"] == {"type": "json_object"}
    assert "provider" not in body


def test_json_when_retry_is_still_invalid_then_raises_after_two_requests(tracker: UsageTracker) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, BAD)

    # ACT
    with pytest.raises(ModelError, match="reply invalid after retry"):
        llm.json("system", "Name it.", Title)

    # ASSERT
    assert len(requests) == 2


@pytest.mark.parametrize("status", [400, 404])
@pytest.mark.parametrize(
    "text",
    [
        "Unsupported parameter: response_format",
        "json_object is not supported",
        "No endpoints found",
        "RESPONSE_FORMAT unsupported",
    ],
    ids=["response_format", "json_object", "no-endpoints", "uppercase"],
)
def test_json_when_retry_is_rejected_for_json_mode_then_repeats_it_without_response_format(
    tracker: UsageTracker, status: int, text: str
) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, BAD, httpx.Response(status, text=text), GOOD)

    # ACT
    result = llm.json("system", "Name it.", Title)

    # ASSERT
    assert result == Title(title="Cats")
    assert len(requests) == 3
    second, third = sent(requests[1]), sent(requests[2])
    assert "response_format" in second
    assert "response_format" not in third
    assert "require_parameters" not in third.get("provider", {})
    assert [m["role"] for m in third["messages"]] == ["system", "user"]
    assert NOTE in third["messages"][1]["content"]


def test_json_when_fallback_and_provider_pinned_then_the_pin_keeps_require_parameters(tracker: UsageTracker) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, BAD, httpx.Response(400, text="response_format not supported"), GOOD, pin_provider=["Novita"])

    # ACT
    llm.json("system", "Name it.", Title)

    # ASSERT
    third = sent(requests[2])
    assert "response_format" not in third
    assert third["provider"]["require_parameters"] is True
    assert third["provider"]["order"] == ["Novita"]


def test_json_when_fallback_was_needed_then_the_same_instance_remembers_it(tracker: UsageTracker) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, BAD, httpx.Response(400, text="response_format not supported"), GOOD, BAD, GOOD)

    # ACT
    llm.json("system", "Name it.", Title)
    result = llm.json("system", "Name it again.", Title)

    # ASSERT
    assert result == Title(title="Cats")
    assert len(requests) == 5
    assert "response_format" not in sent(requests[4])


def test_json_when_retry_gets_an_unrelated_400_then_raises_without_a_third_request(tracker: UsageTracker) -> None:
    # ARRANGE
    llm, requests = make_llm(tracker, BAD, httpx.Response(400, text="unknown model"))

    # ACT
    with pytest.raises(ModelError):
        llm.json("system", "Name it.", Title)

    # ASSERT
    assert len(requests) == 2


@pytest.mark.parametrize(("reason", "expected"), [("stop", "stop"), (None, None)])
def test_chat_when_a_call_is_made_then_usage_log_records_finish_reason(tmp_path: Path, reason: str | None, expected: str | None) -> None:
    # ARRANGE
    log_path = tmp_path / "usage.jsonl"
    llm, _ = make_llm(UsageTracker(log_path), completion("ok", reason))

    # ACT
    llm.chat([{"role": "user", "content": "hi"}], op="test/op")

    # ASSERT
    [line] = [json.loads(row) for row in log_path.read_text(encoding="utf-8").splitlines()]
    assert "finish_reason" in line
    assert line["finish_reason"] == expected


def test_chat_when_finish_reason_is_length_then_raises_naming_the_op(tracker: UsageTracker) -> None:
    # ARRANGE
    llm, _ = make_llm(tracker, completion("cut off", "length"))

    # ACT
    with pytest.raises(ModelError) as error:
        llm.chat([{"role": "user", "content": "hi"}], op="librarian/summarize")

    # ASSERT
    assert "length" in str(error.value)
    assert "librarian/summarize" in str(error.value)


def test_json_when_finish_reason_is_length_then_does_not_retry_and_still_logs(tmp_path: Path) -> None:
    # ARRANGE
    log_path = tmp_path / "usage.jsonl"
    llm, requests = make_llm(UsageTracker(log_path), completion('{"title": "Ca', "length"), GOOD)

    # ACT
    with pytest.raises(ModelError, match="length"):
        llm.json("system", "Name it.", Title, op="librarian/title")

    # ASSERT
    assert len(requests) == 1
    lines = [json.loads(row) for row in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["finish_reason"] == "length"
