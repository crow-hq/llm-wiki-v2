# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Output cap per LLM call (spec-giro2-passo1, part A): `reply_tokens`, the request body, the one retry on a cut reply."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from llmw2 import NoteDraft, Source, StepContext, Wiki, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.config import ClassifierConfig, LLMConfig
from llmw2.errors import ModelError, ReplyCut
from llmw2.models import llm as llm_module
from llmw2.models.client import HttpModel
from llmw2.models.llm import (
    CHARS_PER_TOKEN,
    LLM,
    MIN_REPLY_TOKENS,
    REASONING_ROOM,
    REPLY_FACTOR,
    reply_tokens,
)
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM, FakeLLM

BASE_URL = "http://llm.test/v1"
MODEL = "openrouter/test/writer"
NOTE = "A previous reply to this request was not valid: "
OP = "librarian/title"
CUT_NOTE = (
    "\n\nYour previous reply to this request was cut off at the length limit ({n} tokens). "
    "Reply again, complete and much shorter; do not repeat yourself."
)

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


def user_of(request: httpx.Request) -> str:
    return str(sent(request)["messages"][-1]["content"])


GOOD = completion('{"title": "Cats"}')
BAD = completion('{"name": "Cats"}')
CUT_TEXT = "THE-CUT-REPLY"
CUT = completion(CUT_TEXT, "length")
MANDATORY = httpx.Response(400, json={"error": {"message": "Reasoning is mandatory for this endpoint and cannot be disabled."}})
N = 1_000


# -- A1: constants and reply_tokens -----------------------------------------------------------------------


def test_the_constants_have_the_values_of_the_spec() -> None:
    # A1
    assert (CHARS_PER_TOKEN, REPLY_FACTOR, MIN_REPLY_TOKENS, REASONING_ROOM) == (3.5, 3, 1_024, 32_000)


@pytest.mark.parametrize(("chars", "tokens"), [(10_000, 8_572), (2_000, 1_715), (300, 1_024), (0, 1_024)])
def test_reply_tokens_follows_the_examples_of_the_spec(chars: int, tokens: int) -> None:
    # A1: "10,000 chars -> 8,572; 2,000 -> 1,715; 300 -> 1,024"
    assert reply_tokens(chars) == tokens


def test_reply_tokens_never_goes_below_the_minimum_and_grows_with_the_length() -> None:
    # A1: "no cap is ever lower"
    assert reply_tokens(1) == MIN_REPLY_TOKENS
    assert reply_tokens(1_000_000) > reply_tokens(100_000) > reply_tokens(10_000)


def test_context_warning_literal_is_replaced_by_the_constant() -> None:
    # A1: "the same ratio `context_warning` already uses" (the module exposes the constant)
    assert llm_module.CHARS_PER_TOKEN == CHARS_PER_TOKEN


# -- A2: the request body ---------------------------------------------------------------------------------


def test_complete_when_max_tokens_is_none_then_no_cap_field_is_sent(tracker: UsageTracker) -> None:
    # A2: "None (the default): exactly today's request, no field added"
    llm, requests = make_llm(tracker, completion("ok"))

    llm.complete("sys", "user")

    body = sent(requests[0])
    assert "max_tokens" not in body and "max_completion_tokens" not in body


def test_complete_when_reasoning_is_off_then_the_cap_is_exactly_max_tokens(tracker: UsageTracker) -> None:
    # A2 + A3: the openrouter preset's off switch applies by default: no room
    llm, requests = make_llm(tracker, completion("ok"))

    llm.complete("sys", "user", max_tokens=N)

    body = sent(requests[0])
    assert body["max_tokens"] == N
    assert "max_completion_tokens" not in body
    assert llm.may_think is False


def test_complete_when_reasoning_is_on_then_the_room_is_added(tracker: UsageTracker) -> None:
    # A3: "cfg.reasoning true"
    llm, requests = make_llm(tracker, completion("ok"), reasoning=True)

    llm.complete("sys", "user", max_tokens=N)

    assert sent(requests[0])["max_tokens"] == N + REASONING_ROOM
    assert llm.may_think is True


def test_complete_when_the_caller_sets_reasoning_then_the_room_is_added(tracker: UsageTracker) -> None:
    # A3: "the caller's extra_body sets reasoning"
    llm, requests = make_llm(tracker, completion("ok"), extra_body={"reasoning": {"effort": "low"}})

    llm.complete("sys", "user", max_tokens=N)

    assert sent(requests[0])["max_tokens"] == N + REASONING_ROOM
    assert llm.may_think is True


@pytest.mark.parametrize("provider", ["gemini", "ollama", "custom"])
def test_complete_when_the_preset_has_no_off_switch_then_the_room_is_added(tracker: UsageTracker, provider: str) -> None:
    # A3: "a preset with no switch: openai, gemini, ollama, custom"; A2: the others keep "max_tokens"
    llm, requests = make_llm(tracker, completion("ok"), provider=provider)

    llm.complete("sys", "user", max_tokens=N)

    body = sent(requests[0])
    assert body["max_tokens"] == N + REASONING_ROOM
    assert "max_completion_tokens" not in body
    assert llm.may_think is True


def test_complete_when_the_provider_is_openai_then_the_field_is_max_completion_tokens(tracker: UsageTracker) -> None:
    # A2: "`openai` uses `max_completion_tokens`"; A3: no off switch, so the room is added
    llm, requests = make_llm(tracker, completion("ok"), provider="openai")

    llm.complete("sys", "user", max_tokens=N)

    body = sent(requests[0])
    assert body["max_completion_tokens"] == N + REASONING_ROOM
    assert "max_tokens" not in body


@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens"])
@pytest.mark.parametrize("provider", ["openrouter", "openai"])
def test_complete_when_extra_body_has_a_cap_then_it_wins_and_nothing_is_added(tracker: UsageTracker, provider: str, field: str) -> None:
    # A2: "The caller's extra_body wins if it already has either max_tokens or max_completion_tokens"
    llm, requests = make_llm(tracker, completion("ok"), provider=provider, extra_body={field: 777})

    llm.complete("sys", "user", max_tokens=N)

    body = sent(requests[0])
    assert body[field] == 777
    assert {"max_tokens", "max_completion_tokens"} & body.keys() == {field}


def test_json_and_chat_also_send_the_cap(tracker: UsageTracker) -> None:
    # A2: `chat`, `json` and `complete` all take `max_tokens`
    llm, requests = make_llm(tracker, GOOD, completion("ok"))

    llm.json("sys", "Name it.", Title, max_tokens=N)
    llm.chat([{"role": "user", "content": "hi"}], max_tokens=N)

    assert [sent(r)["max_tokens"] for r in requests] == [N, N]


# -- A3: the cap after the reasoning_least switch -----------------------------------------------------------


def test_complete_when_the_model_refuses_to_stop_thinking_then_the_repost_carries_the_room(tracker: UsageTracker) -> None:
    # A3: "the re-post after the reasoning_least switch gets the room"
    llm, requests = make_llm(tracker, MANDATORY, completion("ok"))

    llm.complete("sys", "user", max_tokens=N)

    first, second = (sent(r) for r in requests)
    assert first["reasoning"] == {"enabled": False} and first["max_tokens"] == N
    assert second["reasoning"] == {"effort": "minimal"} and second["max_tokens"] == N + REASONING_ROOM
    assert llm.may_think is True


def test_complete_when_the_least_effort_was_learnt_then_next_calls_send_the_room(tracker: UsageTracker) -> None:
    # A3: "and not after the switch to reasoning_least"
    llm, requests = make_llm(tracker, MANDATORY, completion("first"), completion("second"))
    llm.complete("sys", "user", max_tokens=N)

    llm.complete("sys", "again", max_tokens=N)

    assert sent(requests[2])["max_tokens"] == N + REASONING_ROOM


# -- A3b: a provider that refuses the cap ---------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 404])
@pytest.mark.parametrize(
    "text",
    [
        "Unsupported parameter: max_tokens",
        "max_completion_tokens is not supported",
        "This model's maximum context length is 4096 tokens",
        "Requested maximum context exceeded",
        "prompt + max_tokens exceeds max_model_len",
    ],
    ids=["max_tokens", "max_completion_tokens", "context-length", "maximum-context", "max_model_len"],
)
def test_complete_when_the_provider_refuses_the_cap_then_it_repeats_the_request_without_it(
    tracker: UsageTracker, status: int, text: str
) -> None:
    # A3b: status 400 or 404 whose text names the cap: same request again, once, without the field
    llm, requests = make_llm(tracker, httpx.Response(status, text=text), completion("ok"))

    reply = llm.complete("sys", "user", max_tokens=N)

    assert reply == "ok"
    assert len(requests) == 2
    assert "max_tokens" in sent(requests[0])
    assert "max_tokens" not in sent(requests[1]) and "max_completion_tokens" not in sent(requests[1])
    assert sent(requests[1])["messages"] == sent(requests[0])["messages"]


def test_complete_when_the_cap_was_refused_then_later_calls_do_not_send_it(tracker: UsageTracker) -> None:
    # A3b: "stop sending the cap field from then on (an instance flag)"
    llm, requests = make_llm(tracker, httpx.Response(400, text="max_tokens too large"), completion("a"), completion("b"))
    llm.complete("sys", "user", max_tokens=N)

    llm.complete("sys", "again", max_tokens=N)

    assert len(requests) == 3
    assert "max_tokens" not in sent(requests[2])


def test_complete_when_a_400_does_not_name_the_cap_then_it_raises_without_a_second_request(tracker: UsageTracker) -> None:
    # A3b: only an error that names the cap triggers the fallback
    llm, requests = make_llm(tracker, httpx.Response(400, text="unknown model"), completion("ok"))

    with pytest.raises(ModelError):
        llm.complete("sys", "user", max_tokens=N)

    assert len(requests) == 1


def test_a_refused_cap_is_not_recorded_in_usage(tmp_path: Path) -> None:
    # A3b: "The refused request is not counted ... (it is not recorded in usage)"
    log_path = tmp_path / "usage.jsonl"
    llm, _ = make_llm(UsageTracker(log_path), httpx.Response(400, text="max_tokens too large"), completion("ok"))

    llm.complete("sys", "user", max_tokens=N)

    assert len(log_path.read_text(encoding="utf-8").splitlines()) == 1


def test_json_when_the_retry_is_refused_for_the_cap_then_it_is_sent_again_in_json_mode_without_the_cap(
    tracker: UsageTracker,
) -> None:
    # A3b: the cap check comes before the response_format check in `_retry`
    llm, requests = make_llm(tracker, BAD, httpx.Response(400, text="max_tokens too large"), GOOD)

    result = llm.json("sys", "Name it.", Title, max_tokens=N)

    assert result == Title(title="Cats")
    assert len(requests) == 3
    assert sent(requests[1])["response_format"] == {"type": "json_object"} and "max_tokens" in sent(requests[1])
    assert sent(requests[2])["response_format"] == {"type": "json_object"} and "max_tokens" not in sent(requests[2])


def test_json_when_a_cap_refusal_comes_in_the_middle_then_it_does_not_count_against_the_three_requests(
    tracker: UsageTracker,
) -> None:
    # A4: "Requests refused with a 400 before being answered (A3b ...) do not count against the limits"
    llm, requests = make_llm(tracker, CUT, BAD, httpx.Response(400, text="max_tokens too large"), GOOD)

    result = llm.json("sys", "Name it.", Title, max_tokens=N)

    assert result == Title(title="Cats")
    assert len(requests) == 4


# -- A4: one retry when the reply is cut, `complete` ----------------------------------------------------------


def test_replycut_is_a_model_error() -> None:
    # A4: "`ReplyCut(ModelError)`"
    assert issubclass(ReplyCut, ModelError)


def test_chat_when_the_reply_is_cut_then_it_raises_a_reply_cut_naming_the_op(tracker: UsageTracker) -> None:
    # A4: "`chat` keeps raising ModelError on finish_reason == "length" ... raise that" (ReplyCut)
    llm, _ = make_llm(tracker, CUT)

    with pytest.raises(ReplyCut, match="length") as error:
        llm.chat([{"role": "user", "content": "hi"}], op="librarian/summarize", max_tokens=N)

    assert "librarian/summarize" in str(error.value)


def test_complete_when_the_reply_is_cut_then_it_asks_once_more_with_the_shorter_note(tracker: UsageTracker) -> None:
    # A4: the user message extended by the note, the cut reply never sent back
    llm, requests = make_llm(tracker, CUT, completion("short"))

    reply = llm.complete("the system", "Write it.", max_tokens=N)

    assert reply == "short"
    assert len(requests) == 2
    first, second = (sent(r)["messages"] for r in requests)
    assert second[0] == first[0] == {"role": "system", "content": "the system"}
    assert [m["role"] for m in second] == ["system", "user"]
    assert second[-1]["content"] == "Write it." + CUT_NOTE.format(n=N)
    assert CUT_TEXT not in json.dumps(second)


def test_complete_when_the_retry_is_cut_too_then_it_raises_naming_the_op_and_the_reason(tracker: UsageTracker) -> None:
    # A4: "A second cut raises ModelError whose message contains the op and `finish_reason length`"
    llm, requests = make_llm(tracker, CUT)

    with pytest.raises(ModelError) as error:
        llm.complete("sys", "Write it.", op="librarian/summarize", max_tokens=N)

    assert len(requests) == 2
    assert "librarian/summarize" in str(error.value)
    assert "finish_reason length" in str(error.value)


def test_complete_when_max_tokens_is_none_then_a_cut_reply_propagates_without_a_retry(tracker: UsageTracker) -> None:
    # A4: "With max_tokens=None a ReplyCut propagates immediately, as today"
    llm, requests = make_llm(tracker, CUT, completion("short"))

    with pytest.raises(ModelError, match="length"):
        llm.complete("sys", "Write it.", op=OP)

    assert len(requests) == 1


def test_json_when_max_tokens_is_none_then_a_cut_reply_propagates_without_a_retry(tracker: UsageTracker) -> None:
    # A4: same, for `json`
    llm, requests = make_llm(tracker, CUT, GOOD)

    with pytest.raises(ModelError, match="length"):
        llm.json("sys", "Name it.", Title, op=OP)

    assert len(requests) == 1


def test_every_request_of_a_cut_retry_is_recorded_in_usage(tmp_path: Path) -> None:
    # A4: "one record per HTTP call"
    log_path = tmp_path / "usage.jsonl"
    llm, _ = make_llm(UsageTracker(log_path), CUT, completion("short"))

    llm.complete("sys", "Write it.", max_tokens=N)

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [row["finish_reason"] for row in rows] == ["length", None]


# -- A4: `json` sequences ---------------------------------------------------------------------------------


def test_json_sequence_1_cut_then_valid_returns_after_two_requests(tracker: UsageTracker) -> None:
    # A4.1: cut -> shorter retry (same json_mode as the cut request) -> valid: return
    llm, requests = make_llm(tracker, CUT, GOOD)

    result = llm.json("sys", "Name it.", Title, max_tokens=N)

    assert result == Title(title="Cats")
    assert len(requests) == 2
    assert user_of(requests[1]) == user_of(requests[0]) + CUT_NOTE.format(n=N)
    assert "response_format" not in sent(requests[1])
    assert CUT_TEXT not in json.dumps(sent(requests[1]))


def test_json_sequence_1_cut_then_invalid_then_valid_keeps_the_shorter_note_and_adds_the_invalid_one(
    tracker: UsageTracker,
) -> None:
    # A4.1: invalid after the shorter retry -> the invalid-JSON retry (json_mode), keeping the shorter note
    llm, requests = make_llm(tracker, CUT, BAD, GOOD)

    result = llm.json("sys", "Name it.", Title, max_tokens=N)

    assert result == Title(title="Cats")
    assert len(requests) == 3
    third = user_of(requests[2])
    assert third.startswith(user_of(requests[0]) + CUT_NOTE.format(n=N))
    assert NOTE in third
    assert sent(requests[2])["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in sent(requests[2])["messages"]] == ["system", "user"]


@pytest.mark.parametrize("last", [BAD, CUT], ids=["invalid", "cut"])
def test_json_sequence_1_cut_then_invalid_then_a_bad_reply_raises_after_three_requests(tracker: UsageTracker, last: httpx.Response) -> None:
    # A4.1: "cut or invalid: ModelError"; "never more than 3 requests"; "at most one length retry"
    llm, requests = make_llm(tracker, CUT, BAD, last)

    with pytest.raises(ModelError):
        llm.json("sys", "Name it.", Title, max_tokens=N)

    assert len(requests) == 3


def test_json_sequence_2_invalid_then_cut_then_valid_retries_the_shorter_one_in_json_mode(tracker: UsageTracker) -> None:
    # A4.2: invalid -> invalid-JSON retry (json_mode) -> cut -> shorter retry (json_mode) -> valid
    llm, requests = make_llm(tracker, BAD, CUT, GOOD)

    result = llm.json("sys", "Name it.", Title, max_tokens=N)

    assert result == Title(title="Cats")
    assert len(requests) == 3
    assert sent(requests[1])["response_format"] == {"type": "json_object"}
    assert sent(requests[2])["response_format"] == {"type": "json_object"}
    assert CUT_NOTE.format(n=N) in user_of(requests[2])
    assert CUT_TEXT not in json.dumps(sent(requests[2]))


@pytest.mark.parametrize("last", [BAD, CUT], ids=["invalid", "cut"])
def test_json_sequence_2_invalid_then_cut_then_a_bad_reply_raises_after_three_requests(tracker: UsageTracker, last: httpx.Response) -> None:
    # A4.2: "cut or invalid: ModelError"
    llm, requests = make_llm(tracker, BAD, CUT, last)

    with pytest.raises(ModelError):
        llm.json("sys", "Name it.", Title, max_tokens=N)

    assert len(requests) == 3


def test_json_sequence_3_two_cuts_raise_after_two_requests_naming_the_op(tracker: UsageTracker) -> None:
    # A4.3: cut -> shorter retry -> cut: ModelError with the op and `finish_reason length`
    llm, requests = make_llm(tracker, CUT)

    with pytest.raises(ModelError) as error:
        llm.json("sys", "Name it.", Title, op=OP, max_tokens=N)

    assert len(requests) == 2
    assert OP in str(error.value) and "finish_reason length" in str(error.value)


def test_json_when_the_reply_is_only_invalid_then_the_path_is_today_s(tracker: UsageTracker) -> None:
    # A4: "the existing invalid-JSON retry stays as it is": two requests, then ModelError
    llm, requests = make_llm(tracker, BAD)

    with pytest.raises(ModelError, match="reply invalid after retry"):
        llm.json("sys", "Name it.", Title, max_tokens=N)

    assert len(requests) == 2


# -- A5: StepContext.ask_json / ask_text --------------------------------------------------------------------


class CapRecorder(FakeLLM):
    """A FakeLLM that notes the `max_tokens` of every call, by op."""

    def __init__(self, usage: UsageTracker) -> None:
        super().__init__(usage)
        self.caps: list[tuple[str, int | None]] = []

    def chat(  # type: ignore[override]
        self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None
    ) -> str:
        self.caps.append((op, max_tokens))
        return super().chat(messages, op=op, json_mode=json_mode)


class ContentCapRecorder(ContentLLM):
    def __init__(self, usage: UsageTracker) -> None:
        super().__init__(usage)
        self.caps: list[tuple[str, int | None]] = []

    def chat(  # type: ignore[override]
        self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None
    ) -> str:
        self.caps.append((op, max_tokens))
        return super().chat(messages, op=op, json_mode=json_mode)


SUMMARIZE = "librarian/summarize"
BODY = {"title": "T", "summary": "S.", "tags": [], "body": "B."}
ASK = {"source_title": "Memo", "source_resource": "-", "source_text": "Text."}


def context(llm: LLM, tmp_path: Path, source: Source | None = None, /, **fields: Any) -> StepContext:
    classifier = ClassifierConfig(model="fake/jev", api_key="fake-key")
    cfg = WikiConfig(bundle=tmp_path / "wiki", mode="classic", classifier=classifier, **fields)
    return StepContext(llm, None, Prompts(), cfg, "librarian/system", source)


def test_ask_json_when_no_reply_chars_then_the_cap_is_for_a_note(tracker: UsageTracker, tmp_path: Path) -> None:
    # A5: "None means self.cfg.effective_note_chars"
    llm = CapRecorder(tracker).add(SUMMARIZE, BODY)
    ctx = context(llm, tmp_path, note_chars=3_000)

    ctx.ask_json(SUMMARIZE, NoteDraft, **ASK)

    assert llm.caps == [(SUMMARIZE, reply_tokens(ctx.cfg.effective_note_chars))]
    assert llm.caps[0][1] == 2_572


@pytest.mark.parametrize(("reply_chars", "tokens"), [(4_000, 3_429), (500, 1_024)])
def test_ask_json_when_reply_chars_is_given_then_the_cap_is_for_that_length(
    tracker: UsageTracker, tmp_path: Path, reply_chars: int, tokens: int
) -> None:
    # A5: "call llm.json with max_tokens=reply_tokens(reply_chars)"
    llm = CapRecorder(tracker).add(SUMMARIZE, BODY)

    context(llm, tmp_path, note_chars=3_000).ask_json(SUMMARIZE, NoteDraft, reply_chars=reply_chars, **ASK)

    assert llm.caps == [(SUMMARIZE, tokens)]


def test_ask_text_when_no_reply_chars_then_the_cap_is_for_a_note(tracker: UsageTracker, tmp_path: Path) -> None:
    # A5: ask_text calls llm.complete with the same rule
    llm = CapRecorder(tracker).add(SUMMARIZE, "plain reply")

    context(llm, tmp_path, note_chars=3_000).ask_text(SUMMARIZE, **ASK)

    assert llm.caps == [(SUMMARIZE, 2_572)]


def test_ask_text_when_reply_chars_is_given_then_the_cap_is_for_that_length(tracker: UsageTracker, tmp_path: Path) -> None:
    # A5
    llm = CapRecorder(tracker).add(SUMMARIZE, "plain reply")

    context(llm, tmp_path, note_chars=3_000).ask_text(SUMMARIZE, reply_chars=4_000, **ASK)

    assert llm.caps == [(SUMMARIZE, 3_429)]


def test_a_direct_call_to_the_llm_stays_uncapped(tracker: UsageTracker, tmp_path: Path) -> None:
    # A5: "direct calls to ctx.llm.complete/json/chat ... stay uncapped unless they pass max_tokens"
    llm = CapRecorder(tracker).add(SUMMARIZE, "plain reply")

    context(llm, tmp_path).llm.complete("sys", "user", op=SUMMARIZE)

    assert llm.caps == [(SUMMARIZE, None)]


# -- A6: the callers ----------------------------------------------------------------------------------------


def test_the_classic_steps_use_the_reply_chars_of_the_table(tracker: UsageTracker, bundle: Path) -> None:
    # A6: summarize default; route, name_folder, find_match, consolidate, relate: DECIDE_CHARS; merge revised with the
    # user on 2026-10-06: the length of the existing note plus note_chars, so never under the note cap
    assert classic.DECIDE_CHARS == 2_000
    llm = ContentCapRecorder(tracker)
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm)
    wiki.init()

    wiki.ingest("topic: alpha-1\nFirst source about alpha.")
    wiki.ingest("topic: alpha-2\nSecond source about alpha.")

    note = reply_tokens(wiki.cfg.effective_note_chars)
    decide = reply_tokens(classic.DECIDE_CHARS)
    expected = {
        "librarian/summarize": note,
        "librarian/route": decide,
        "librarian/name_folder": decide,
        "librarian/find_match": decide,
        "librarian/consolidate": decide,
        "librarian/relate": decide,
    }
    seen = {op for op, _ in llm.caps}
    assert {"librarian/summarize", "librarian/route", "librarian/name_folder", "librarian/find_match"} <= seen
    for op, cap in llm.caps:
        if op in expected:
            assert cap == expected[op], op
        if op == "librarian/merge":
            assert cap is not None and cap > note, op


class PartRecorder(CapRecorder):
    def __init__(self, usage: UsageTracker) -> None:
        super().__init__(usage)
        self.add("librarian/summarize_part", "- a note.", "- a note.")
        self.add("librarian/summarize_combine", BODY)


def test_summarize_part_and_the_combine_are_capped_by_a_note(tracker: UsageTracker, tmp_path: Path) -> None:
    # A6, revised after the 2026-10-06 data (user's decision): summarize_part -> effective_note_chars, not its `cap`
    # (the model never keeps to `cap`; cut_notes trims); summarize_combine -> default (effective_note_chars)
    filler = "The parties agree on the terms that follow and on nothing else at all, as the text says here."
    text = "\n\n".join([filler] * 1_600)  # ~150k characters: two parts of the 94k text room
    llm = PartRecorder(tracker)
    ctx = context(llm, tmp_path, llm={"read_chars": 100_000}, note_length="fixed")
    source = Source(text, title="Memo")

    classic.summarize(ctx, source)

    parts = [cap for op, cap in llm.caps if op == "librarian/summarize_part"]
    combine = [cap for op, cap in llm.caps if op == "librarian/summarize_combine"]
    assert len(parts) == 2
    # round 6 (P1): the cap follows `cap` (half a note) times 1.3, not a whole note
    assert parts == [reply_tokens(math.ceil(1.3 * (ctx.cfg.effective_note_chars // 2)))] * 2
    assert combine == [reply_tokens(ctx.cfg.effective_note_chars)]
