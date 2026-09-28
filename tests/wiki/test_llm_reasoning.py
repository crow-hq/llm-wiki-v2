# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The writing model's reasoning switch: off by default on OpenRouter, the least effort when a model must think."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from llmw2.config import LLMConfig
from llmw2.errors import ModelError
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageTracker
from tests.wiki.test_llm import BASE_URL, MODEL, completion, sent, serve

MANDATORY = httpx.Response(
    400, json={"error": {"message": "Reasoning is mandatory for this endpoint and cannot be disabled."}}
)


def make_llm(*replies: httpx.Response, **cfg: Any) -> tuple[LLM, list[httpx.Request]]:
    client, requests = serve(*replies)
    fields: dict[str, Any] = {"provider": "openrouter", "base_url": BASE_URL, "model": MODEL, "api_key": "test-key", **cfg}
    return LLM(LLMConfig(**fields), UsageTracker(), client=client), requests


@pytest.mark.parametrize(
    ("provider", "reasoning", "extra_body", "sent_reasoning"),
    [
        ("openrouter", False, {}, {"enabled": False}),
        ("openrouter", True, {}, None),
        ("openrouter", False, {"reasoning": {"effort": "low"}}, {"effort": "low"}),
        ("ollama", False, {}, None),
        ("openai", False, {}, None),
    ],
    ids=["openrouter-default-off", "openrouter-on", "extra-body-wins", "ollama", "openai"],
)
def test_complete_when_reasoning_is_configured_then_sends_the_provider_switch_unless_it_is_on(
    provider: str, reasoning: bool, extra_body: dict[str, Any], sent_reasoning: dict[str, Any] | None
) -> None:
    # ARRANGE
    client, requests = serve(completion("ok"))
    cfg = LLMConfig(
        provider=provider, base_url=BASE_URL, model=MODEL, api_key="test-key", reasoning=reasoning, extra_body=extra_body
    )

    # ACT
    LLM(cfg, UsageTracker(), client=client).complete("sys", "user")

    # ASSERT
    assert sent(requests[0]).get("reasoning") == sent_reasoning


def test_complete_when_the_model_refuses_to_stop_thinking_then_retries_once_with_the_least_effort() -> None:
    # ARRANGE
    llm, requests = make_llm(MANDATORY, completion("ok"))

    # ACT
    reply = llm.complete("sys", "user")

    # ASSERT
    assert reply == "ok"
    assert [sent(r)["reasoning"] for r in requests] == [{"enabled": False}, {"effort": "minimal"}]


def test_complete_when_the_least_effort_was_learnt_then_the_next_call_sends_it_directly() -> None:
    # ARRANGE
    llm, requests = make_llm(MANDATORY, completion("first"), completion("second"))
    llm.complete("sys", "user")

    # ACT
    reply = llm.complete("sys", "again")

    # ASSERT
    assert reply == "second"
    assert len(requests) == 3 and sent(requests[2])["reasoning"] == {"effort": "minimal"}


@pytest.mark.parametrize(
    ("reply", "cfg"),
    [
        (httpx.Response(400, json={"error": {"message": "max_tokens is too large"}}), {}),
        (MANDATORY, {"reasoning": True}),
        (MANDATORY, {"extra_body": {"reasoning": {"effort": "high"}}}),
        (MANDATORY, {"provider": "custom"}),
    ],
    ids=["other-400", "reasoning-on", "caller-reasoning", "no-switch-provider"],
)
def test_complete_when_a_400_is_not_about_our_switch_then_raises_without_retry(
    reply: httpx.Response, cfg: dict[str, Any]
) -> None:
    # ARRANGE
    llm, requests = make_llm(reply, completion("ok"), **cfg)

    # ACT
    with pytest.raises(ModelError) as raised:
        llm.complete("sys", "user")

    # ASSERT
    assert (raised.value.status, len(requests)) == (400, 1)
