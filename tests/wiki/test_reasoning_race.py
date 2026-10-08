# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The reasoning fallback holds for requests already in flight (spec-giro4-reasoning-race)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest

from llmw2.config import LLMConfig
from llmw2.errors import ModelError
from llmw2.models.client import HttpModel
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageTracker
from tests.wiki.test_llm import BASE_URL, MODEL, completion, sent

LEAST = {"effort": "minimal"}
OFF = {"enabled": False}
MESSAGES = [{"role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpModel, "backoff", 0)


def is_off(request: httpx.Request) -> bool:
    return sent(request).get("reasoning") == OFF


def mandatory() -> httpx.Response:
    return httpx.Response(400, json={"error": {"message": "Reasoning is mandatory for this endpoint and cannot be disabled."}})


def make_llm(handler: Callable[[httpx.Request], httpx.Response]) -> tuple[LLM, list[dict[str, Any]]]:
    """An OpenRouter LLM over `handler`; the bodies of all requests are recorded."""
    bodies: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(sent(request))  # list.append is atomic
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(handle))
    cfg = LLMConfig(provider="openrouter", base_url=BASE_URL, model=MODEL, api_key="test-key")
    return LLM(cfg, UsageTracker(), client=client), bodies


def picky(request: httpx.Request) -> httpx.Response:
    """400 to the off switch, 200 to anything else."""
    return mandatory() if is_off(request) else completion("ok")


def racing(n: int, refuse_all: bool = False) -> Callable[[httpx.Request], httpx.Response]:
    """Every off-body request waits for the others before its 400: all are built before any answer."""
    barrier = threading.Barrier(n, timeout=10)

    def handler(request: httpx.Request) -> httpx.Response:
        if is_off(request):
            barrier.wait()
        return mandatory() if refuse_all else picky(request)

    return handler


def test_chat_when_another_request_already_switched_then_this_one_is_resent_once_with_the_least_effort() -> None:
    # ARRANGE: while the outer request is in flight (built with the off switch), a nested call makes the switch
    nested = {"done": False}
    holder: dict[str, LLM] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if is_off(request) and not nested["done"]:
            nested["done"] = True
            assert holder["llm"].chat(MESSAGES) == "ok"  # this one switches
            return mandatory()
        return picky(request)

    llm, bodies = make_llm(handler)
    holder["llm"] = llm

    # ACT
    reply = llm.chat(MESSAGES)

    # ASSERT: outer (off), nested (off), nested resent (least), outer resent (least)
    assert reply == "ok"
    assert [b.get("reasoning") for b in bodies] == [OFF, OFF, LEAST, LEAST]


def test_chat_when_threads_race_on_the_off_switch_then_all_succeed_and_the_switch_happens_once() -> None:
    # ARRANGE
    n = 4
    llm, bodies = make_llm(racing(n))
    original = dict(llm.extra_body)

    # ACT
    with ThreadPoolExecutor(n) as pool:
        replies = list(pool.map(lambda _: llm.chat(MESSAGES), range(n)))

    # ASSERT: n requests with the off switch, n resent with the least effort, nothing else
    assert replies == ["ok"] * n
    assert [b.get("reasoning") for b in bodies].count(OFF) == n
    assert [b.get("reasoning") for b in bodies].count(LEAST) == n
    assert len(bodies) == 2 * n
    assert llm.extra_body == {**original, "reasoning": LEAST}
    assert llm.may_think is True


def test_chat_when_the_provider_refuses_both_bodies_then_raises_after_one_resend() -> None:
    # ARRANGE
    llm, bodies = make_llm(lambda request: mandatory())

    # ACT
    with pytest.raises(ModelError) as raised:
        llm.chat(MESSAGES)

    # ASSERT
    assert raised.value.status == 400
    assert [b.get("reasoning") for b in bodies] == [OFF, LEAST]


def test_chat_when_threads_race_and_the_provider_refuses_both_bodies_then_each_request_resends_at_most_once() -> None:
    # ARRANGE
    n = 4
    llm, bodies = make_llm(racing(n, refuse_all=True))

    # ACT
    with ThreadPoolExecutor(n) as pool:
        futures = [pool.submit(llm.chat, MESSAGES) for _ in range(n)]
    errors = [f.exception() for f in futures]

    # ASSERT
    assert all(isinstance(e, ModelError) for e in errors)
    assert len(bodies) == 2 * n


def test_chat_when_a_request_is_built_after_the_switch_and_refused_then_it_is_not_resent() -> None:
    # ARRANGE: the first call switches; the least body is refused too
    llm, bodies = make_llm(lambda request: mandatory())
    with pytest.raises(ModelError):
        llm.chat(MESSAGES)
    bodies.clear()

    # ACT
    with pytest.raises(ModelError):
        llm.chat(MESSAGES)

    # ASSERT: already built with the least body: one request only
    assert [b.get("reasoning") for b in bodies] == [LEAST]


def test_chat_when_no_request_gets_the_reasoning_400_then_nothing_is_resent_or_switched() -> None:
    # ARRANGE
    llm, bodies = make_llm(lambda request: completion("ok"))
    before = dict(llm.extra_body)

    # ACT
    replies = [llm.chat(MESSAGES) for _ in range(3)]

    # ASSERT
    assert replies == ["ok"] * 3
    assert [b.get("reasoning") for b in bodies] == [OFF] * 3
    assert llm.extra_body == before


def test_chat_when_the_400_is_not_about_reasoning_then_raises_without_resend() -> None:
    # ARRANGE
    llm, bodies = make_llm(lambda request: httpx.Response(400, json={"error": {"message": "bad request"}}))

    # ACT
    with pytest.raises(ModelError):
        llm.chat(MESSAGES)

    # ASSERT
    assert len(bodies) == 1


def test_chat_when_both_the_reasoning_and_the_output_cap_are_refused_then_both_fallbacks_apply() -> None:
    # ARRANGE
    def handler(request: httpx.Request) -> httpx.Response:
        if is_off(request):
            return mandatory()
        if "max_tokens" in sent(request):
            return httpx.Response(400, json={"error": {"message": "max_tokens is too large"}})
        return completion("ok")

    llm, bodies = make_llm(handler)

    # ACT
    reply = llm.chat(MESSAGES, max_tokens=100)

    # ASSERT: off+cap refused, least+cap refused, least without cap accepted
    assert reply == "ok"
    assert len(bodies) == 3
    assert bodies[-1].get("reasoning") == LEAST and "max_tokens" not in bodies[-1]
