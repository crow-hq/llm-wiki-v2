# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Round 2, step 2 (spec-giro2-passo2-keepcut): a reply cut by the output cap keeps what was written."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from llmw2 import Source, StepContext, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.agents.state import split_blocks
from llmw2.agents.steps_classic import cut_notes
from llmw2.config import ClassifierConfig, LLMConfig
from llmw2.errors import ModelError, ReplyCut
from llmw2.models.client import HttpModel
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeLLM

BASE_URL = "http://llm.test/v1"
MODEL = "openrouter/test/writer"
N = 1_000
READ = 8_000
TEXT_ROOM = READ - 6_000
PART, COMBINE = "librarian/summarize_part", "librarian/summarize_combine"
DRAFT = {"title": "Whole note", "summary": "All of it.", "tags": ["t"], "body": "# Overview\n\nThe whole."}
FILLER = "The parties agree on the terms that follow and on nothing else at all, as the text says here."
WRITTEN = "- the part so far, " + "x" * 40


class Title(BaseModel):
    title: str


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpModel, "backoff", 0)


def serve(*replies: httpx.Response) -> tuple[httpx.Client, list[httpx.Request]]:
    queue = list(replies)
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(reply.status_code, headers=reply.headers, content=reply.content)

    return httpx.Client(transport=httpx.MockTransport(handle)), requests


def make_llm(tracker: UsageTracker, *replies: httpx.Response) -> tuple[LLM, list[httpx.Request]]:
    client, requests = serve(*replies)
    cfg = LLMConfig(provider="openrouter", base_url=f"{BASE_URL}/", model=MODEL, api_key="test-key", temperature=0.3)
    return LLM(cfg, tracker, client=client), requests


def completion(content: str | None, finish_reason: str | None = None) -> httpx.Response:
    choice: dict[str, Any] = {"index": 0, "message": {"role": "assistant", "content": content}}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return httpx.Response(200, json={"choices": [choice]})


CUT = completion(WRITTEN, "length")
CUT_NULL = completion(None, "length")


# -- 1: ReplyCut ------------------------------------------------------------------------------------------


def test_replycut_is_a_model_error_and_its_text_defaults_to_empty() -> None:
    # 1: "ReplyCut(ModelError) carries the text written before the cut ... attribute .text"
    error = ReplyCut("cut")

    assert isinstance(error, ModelError)
    assert error.text == ""


def test_replycut_carries_the_text_it_is_given() -> None:
    # 1: "ReplyCut(message, *, text: str = "", status=None)"
    error = ReplyCut("cut", text="so far", status=None)

    assert error.text == "so far"
    assert "cut" in str(error)


# -- 2: chat ----------------------------------------------------------------------------------------------


def test_chat_when_the_reply_is_cut_then_replycut_holds_the_written_content(tracker: UsageTracker) -> None:
    # 2: "chat raises ReplyCut(..., text=<the content of the reply>) on finish_reason == length"
    llm, _ = make_llm(tracker, CUT)

    with pytest.raises(ReplyCut, match="length") as error:
        llm.chat([{"role": "user", "content": "hi"}], op="librarian/x", max_tokens=N)

    assert error.value.text == WRITTEN


def test_chat_when_the_cut_reply_has_null_content_then_the_text_is_empty(tracker: UsageTracker) -> None:
    # 2: "\"\" if missing"
    llm, _ = make_llm(tracker, CUT_NULL)

    with pytest.raises(ReplyCut) as error:
        llm.chat([{"role": "user", "content": "hi"}], op="librarian/x", max_tokens=N)

    assert error.value.text == ""


# -- 2: complete ------------------------------------------------------------------------------------------


def test_complete_with_keep_cut_returns_the_partial_text_after_one_request(tracker: UsageTracker) -> None:
    # 2: "with keep_cut=True a ReplyCut is not retried: complete returns e.text"
    llm, requests = make_llm(tracker, CUT, completion("short"))

    reply = llm.complete("sys", "Write it.", op="librarian/x", max_tokens=N, keep_cut=True)

    assert reply == WRITTEN
    assert len(requests) == 1


def test_complete_with_keep_cut_logs_a_warning_with_the_op_and_the_length(tracker: UsageTracker, caplog: pytest.LogCaptureFixture) -> None:
    # 2: warning "%s: reply cut at the output cap: kept the %d characters written" (op, len)
    llm, _ = make_llm(tracker, CUT)

    with caplog.at_level(logging.WARNING):
        llm.complete("sys", "Write it.", op="librarian/x", max_tokens=N, keep_cut=True)

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert f"librarian/x: reply cut at the output cap: kept the {len(WRITTEN)} characters written" in messages


def test_complete_with_keep_cut_and_a_null_content_returns_an_empty_string(tracker: UsageTracker) -> None:
    # 2: text is "" when the content is missing; complete returns e.text
    llm, requests = make_llm(tracker, CUT_NULL)

    assert llm.complete("sys", "Write it.", max_tokens=N, keep_cut=True) == ""
    assert len(requests) == 1


def test_complete_without_keep_cut_asks_once_more_shorter(tracker: UsageTracker) -> None:
    # 2: "With keep_cut=False, as today": one shorter retry
    llm, requests = make_llm(tracker, CUT, completion("short"))

    assert llm.complete("sys", "Write it.", max_tokens=N, keep_cut=False) == "short"
    assert len(requests) == 2


def test_complete_without_keep_cut_fails_when_the_retry_is_cut_too(tracker: UsageTracker) -> None:
    # 2: "With keep_cut=False, as today": a second cut is an error
    llm, requests = make_llm(tracker, CUT)

    with pytest.raises(ModelError, match="length"):
        llm.complete("sys", "Write it.", op="librarian/x", max_tokens=N)

    assert len(requests) == 2


def test_json_still_retries_and_fails_on_a_cut_reply(tracker: UsageTracker) -> None:
    # 2: "json unchanged"
    llm, requests = make_llm(tracker, CUT)

    with pytest.raises(ModelError, match="length"):
        llm.json("sys", "Name it.", Title, op="librarian/x", max_tokens=N)

    assert len(requests) == 2


# -- 3: ask_text ------------------------------------------------------------------------------------------


class KeepCutRecorder(FakeLLM):
    """Notes the `keep_cut` that reaches `complete`."""

    def __init__(self, usage: UsageTracker) -> None:
        super().__init__(usage)
        self.keep_cuts: list[bool] = []

    def complete(  # type: ignore[override]
        self, system: str, user: str, *, op: str = "", max_tokens: int | None = None, keep_cut: bool = False
    ) -> str:
        self.keep_cuts.append(keep_cut)
        return "plain"


def context(llm: LLM, source: Source | None = None, **fields: Any) -> StepContext:
    classifier = ClassifierConfig(model="fake/jev", api_key="fake-key")
    cfg = WikiConfig(bundle=Path("wiki"), mode="classic", llm=LLMConfig(read_chars=READ), classifier=classifier, **fields)
    return StepContext(llm, None, Prompts(), cfg, "librarian/system", source)


@pytest.mark.parametrize("keep_cut", [True, False])
def test_ask_text_passes_keep_cut_to_complete(tracker: UsageTracker, keep_cut: bool) -> None:
    # 3: "ask_text(prompt, *, reply_chars=None, keep_cut=False, **values) passes keep_cut to complete"
    llm = KeepCutRecorder(tracker)

    context(llm).ask_text("librarian/summarize", keep_cut=keep_cut, source_title="Memo", source_resource="-", source_text="Text.")

    assert llm.keep_cuts == [keep_cut]


# -- 4: summarize of a long source ------------------------------------------------------------------------


class RecordingLLM(LLM):
    """Answers each op by a function of the user prompt (or a fixed reply); a handler may raise."""

    def __init__(self, usage: UsageTracker, **handlers: Callable[[str], Any] | dict[str, Any] | str) -> None:
        super().__init__(LLMConfig(model="fake/llm"), usage)
        self.handlers = handlers
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
        import json

        self.calls.append((op, [dict(m) for m in messages]))
        handler = self.handlers[op.rpartition("/")[2]]
        reply = handler(messages[1]["content"]) if callable(handler) else handler
        return reply if isinstance(reply, str) else json.dumps(reply)

    @property
    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]

    def prompts(self, op: str) -> list[str]:
        return [msgs[1]["content"] for o, msgs in self.calls if o == op]


def document(sections: int = 4) -> str:
    paragraph = " ".join([FILLER] * 4)
    sep = "\n\n"
    return sep.join(f"## Memo part {i}" + sep + sep.join(f"{paragraph} Fact {i}.{j}." for j in range(1, 5)) for i in range(1, sections + 1))


@pytest.fixture
def long_text() -> str:
    return document()


def plain_notes(user: str) -> str:
    block = re.search(r"part (\d+) of (\d+)", user, re.IGNORECASE)
    assert block is not None
    return f"  \n- notes of part {block.group(1)}: Code ZX-{block.group(1)}00.\n\n"


def test_summary_version_is_three() -> None:
    # 4: "Bump SUMMARY_VERSION to 3"
    assert classic.SUMMARY_VERSION == 7


def test_the_part_notes_are_the_plain_text_reply_stripped(tracker: UsageTracker, long_text: str) -> None:
    # 4: "the reply text, .strip()-ed, is the part's notes" (no JSON parsing)
    llm = RecordingLLM(tracker, summarize_part=plain_notes, summarize_combine=DRAFT)
    source = Source(long_text, title="Memo")
    n = len(split_blocks(long_text, TEXT_ROOM))

    cand = classic.summarize(context(llm, source), source)

    assert llm.ops == [PART] * n + [COMBINE]
    [prompt] = llm.prompts(COMBINE)
    for k in range(1, n + 1):
        assert f"- notes of part {k}: Code ZX-{k}00." in prompt
    assert cand.title == DRAFT["title"]


def test_a_json_looking_reply_is_taken_as_plain_text_not_parsed(tracker: UsageTracker, long_text: str) -> None:
    # 4: "the part notes are the plain-text reply (no JSON parsing)"
    llm = RecordingLLM(tracker, summarize_part='{"notes": "- kept as written."}', summarize_combine=DRAFT)
    source = Source(long_text, title="Memo")

    classic.summarize(context(llm, source), source)

    [prompt] = llm.prompts(COMBINE)
    assert '{"notes": "- kept as written."}' in prompt


def test_a_cut_part_keeps_its_text_trimmed_to_the_cap_and_the_document_does_not_fail(
    tracker: UsageTracker, long_text: str, caplog: pytest.LogCaptureFixture
) -> None:
    # 4: "a cut part keeps its written text ... then cut_notes"; no failure; summarize_combine still runs
    written = "\n".join(f"- line {i} of the notes with some words." for i in range(300)) + "\n- ENDMARK"
    calls = {"n": 0}

    def part(user: str) -> str:
        calls["n"] += 1
        if re.search(r"part 2 of", user, re.IGNORECASE):
            raise ReplyCut("reply cut: finish_reason length", text=written)
        return plain_notes(user)

    llm = RecordingLLM(tracker, summarize_part=part, summarize_combine=DRAFT)
    source = Source(long_text, title="Memo")
    n = len(split_blocks(long_text, TEXT_ROOM))

    with caplog.at_level(logging.WARNING):
        cand = classic.summarize(context(llm, source), source)

    assert llm.ops == [PART] * n + [COMBINE]  # no retry of the cut part, the combine runs
    assert calls["n"] == n
    [prompt] = llm.prompts(COMBINE)
    cap = 500  # min(1 000 // 2, 2 000 // n, len(block) // 2), as in test_summarize_long
    assert cut_notes(written.strip(), cap) in prompt
    assert "ENDMARK" not in prompt
    assert "line 0 of the notes" in prompt
    assert cand.title == DRAFT["title"]


# -- 5: the prompt ----------------------------------------------------------------------------------------


def test_the_summarize_part_prompt_asks_for_the_notes_only_and_no_longer_for_json() -> None:
    # 5: replace "Reply with JSON: {"notes": "..."}" with "Reply with the notes only, in Markdown, with nothing before or after them."
    prompts = Prompts()
    values = {key: f"[[value of {key}]]" for key in prompts.placeholders(PART)}

    text = prompts.render(PART, **values)

    assert "Reply with the notes only, in Markdown, with nothing before or after them." in text
    assert "JSON" not in text
    assert '{"notes"' not in text
