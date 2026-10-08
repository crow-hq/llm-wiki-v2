# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`summarize` by size: one call when the text fits the prompt, else notes per block and one combine call."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from llmw2 import InputError, ModelError, Source, StepContext, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.agents.state import source_state, split_blocks
from llmw2.config import ClassifierConfig, LLMConfig
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageTracker

READ = 8_000  # the smallest read_chars: the text of one call is 2 000 characters, a note 1 000
ROOM = 6_000
TEXT_ROOM = READ - ROOM
SUMMARIZE, PART, COMBINE = "librarian/summarize", "librarian/summarize_part", "librarian/summarize_combine"
DRAFT = {"title": "Whole note", "summary": "All of it.", "tags": ["t"], "body": "# Overview\n\nThe whole."}
FILLER = "The parties agree on the terms that follow and on nothing else at all, as the text says here."


class RecordingLLM(LLM):
    """Records every prompt and answers by `op` with a function of the user prompt (or a fixed reply)."""

    def __init__(self, usage: UsageTracker, **handlers: Callable[[str], Any] | dict[str, Any]) -> None:
        super().__init__(LLMConfig(model="fake/llm"), usage)
        self.handlers = handlers
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
        self.calls.append((op, [dict(m) for m in messages]))
        handler = self.handlers.get(op.rpartition("/")[2]) or self.handlers.get(op)
        if handler is None:
            raise AssertionError(f"RecordingLLM: no answer for {op!r}")
        reply = handler(messages[1]["content"]) if callable(handler) else handler
        return reply if isinstance(reply, str) else json.dumps(reply)

    @property
    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]

    def prompts(self, op: str) -> list[str]:
        return [msgs[1]["content"] for o, msgs in self.calls if o == op]


def context(llm: LLM, source: Source | None = None, **fields: Any) -> StepContext:
    llm_cfg = LLMConfig(read_chars=READ)
    classifier = ClassifierConfig(model="fake/jev", api_key="fake-key")
    cfg = WikiConfig(bundle=Path("wiki"), mode="classic", llm=llm_cfg, classifier=classifier, **fields)
    return StepContext(llm, None, Prompts(), cfg, "librarian/system", source)


def document(sections: int = 4, title: str = "Memo") -> str:
    """Markdown of about 6 400 characters for four sections: with a text room of 2 000 it is four blocks."""
    paragraph = " ".join([FILLER] * 4)
    sep = "\n\n"
    return sep.join(
        f"## {title} part {i}" + sep + sep.join(f"{paragraph} Fact {i}.{j}." for j in range(1, 5)) for i in range(1, sections + 1)
    )


def notes_for(user: str) -> str:
    block = re.search(r"part (\d+) of (\d+)", user, re.IGNORECASE)
    assert block is not None, "the prompt of a part says which part it is"
    return f"- notes of part {block.group(1)}: Code ZX-{block.group(1)}00."


@pytest.fixture
def long_text() -> str:
    text = document()
    assert TEXT_ROOM * 3 < len(text) <= TEXT_ROOM * 4
    return text


# -- short text: as before ----------------------------------------------------------------------------


def test_a_text_that_fits_is_summarized_in_one_call_with_the_whole_text(tracker: UsageTracker) -> None:
    llm = RecordingLLM(tracker, summarize=DRAFT)
    source = Source("Short text. " * 100, title="Memo", resource="/raw/memo.md")

    cand = classic.summarize(context(llm, source), source)

    assert llm.ops == [SUMMARIZE]
    assert source.text in llm.prompts(SUMMARIZE)[0]
    assert (cand.title, cand.summary, cand.body) == (DRAFT["title"], DRAFT["summary"], DRAFT["body"])


def test_a_text_of_exactly_the_text_room_is_still_one_call(tracker: UsageTracker) -> None:
    llm = RecordingLLM(tracker, summarize=DRAFT)
    source = Source("a" * TEXT_ROOM, title="Memo")

    classic.summarize(context(llm, source), source)

    assert llm.ops == [SUMMARIZE]


def test_a_text_one_character_over_the_text_room_goes_through_parts(tracker: UsageTracker) -> None:
    llm = RecordingLLM(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source("a" * (TEXT_ROOM + 1), title="Memo")

    classic.summarize(context(llm, source), source)

    assert llm.ops == [PART, PART, COMBINE]


# -- long text: map and reduce ------------------------------------------------------------------------


def test_a_long_text_is_summarized_by_parts_in_order_and_one_combine(tracker: UsageTracker, long_text: str) -> None:
    llm = RecordingLLM(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source(long_text, title="Memo", resource="/raw/memo.md")
    blocks = split_blocks(long_text, TEXT_ROOM)
    n = len(blocks)

    cand = classic.summarize(context(llm, source), source)

    assert n >= 4
    assert llm.ops == [PART] * n + [COMBINE]  # in sequence, nothing else, no whole-text call
    for k, (block, prompt) in enumerate(zip(blocks, llm.prompts(PART), strict=True), 1):
        assert block.text in prompt
        assert re.search(rf"part {k} of {n}", prompt, re.IGNORECASE)
        assert "Memo" in prompt
        if block.heading:
            assert block.heading in prompt
    assert (cand.title, cand.summary, cand.body) == (DRAFT["title"], DRAFT["summary"], DRAFT["body"])


def test_the_combine_prompt_holds_the_title_the_location_and_every_note_in_order(tracker: UsageTracker, long_text: str) -> None:
    llm = RecordingLLM(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source(long_text, title="Memo", resource="/raw/memo.md")
    n = len(split_blocks(long_text, TEXT_ROOM))

    classic.summarize(context(llm, source), source)

    [prompt] = llm.prompts(COMBINE)
    assert "Memo" in prompt and "/raw/memo.md" in prompt
    positions = [prompt.find(f"ZX-{k}00") for k in range(1, n + 1)]
    assert all(p >= 0 for p in positions) and positions == sorted(positions)
    for k in range(1, n + 1):
        assert re.search(rf"Part {k} of {n}", prompt)
    assert long_text[:200] not in prompt  # the source itself is not in the combine call
    assert len(prompt) < READ  # by construction it fits the budget


def test_no_prompt_of_a_long_summary_is_longer_than_read_chars(tracker: UsageTracker, long_text: str) -> None:
    llm = RecordingLLM(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source(long_text, title="Memo", resource="/raw/memo.md")

    classic.summarize(context(llm, source), source)

    for op, messages in llm.calls:
        assert len(messages[1]["content"]) <= READ, op


def test_a_part_prompt_states_the_cap_of_its_notes_and_the_combine_the_cap_of_the_note(tracker: UsageTracker, long_text: str) -> None:
    llm = RecordingLLM(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source(long_text, title="Memo")

    classic.summarize(context(llm, source), source)

    assert all("500" in p for p in llm.prompts(PART))  # min(1 000 // 2, 2 000 // n, len(block) // 2)
    assert "about 1000 characters" in llm.prompts(COMBINE)[0]


def test_notes_over_their_cap_are_cut_at_a_line_with_a_warning(
    tracker: UsageTracker, long_text: str, caplog: pytest.LogCaptureFixture
) -> None:
    long_notes = "\n".join(f"- line {i} of the notes with some words." for i in range(300)) + "\n- ENDMARK"
    llm = RecordingLLM(tracker, summarize_part=long_notes, summarize_combine=DRAFT)
    source = Source(long_text, title="Memo")
    n = len(split_blocks(long_text, TEXT_ROOM))

    with caplog.at_level(logging.WARNING):
        classic.summarize(context(llm, source), source)

    [prompt] = llm.prompts(COMBINE)
    assert "ENDMARK" not in prompt
    assert "line 0 of the notes" in prompt  # the start is kept
    assert prompt.count("line 0 of the notes") == n
    assert len(prompt) < READ
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) >= 1


def test_notes_within_their_cap_are_passed_whole_and_without_a_warning(
    tracker: UsageTracker, long_text: str, caplog: pytest.LogCaptureFixture
) -> None:
    llm = RecordingLLM(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source(long_text, title="Memo")

    with caplog.at_level(logging.WARNING):
        classic.summarize(context(llm, source), source)

    # no cut warning; the values check after the combine may warn
    assert [r for r in caplog.records if r.levelno >= logging.WARNING and "cut" in r.getMessage()] == []


def test_a_source_too_long_for_the_budget_is_an_input_error_naming_read_chars(tracker: UsageTracker) -> None:
    # 12 000 characters: 6 blocks of 2 000, and 2 000 // 6 = 333 < 500 characters for each part's notes
    llm = RecordingLLM(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source(document(sections=7) * 2, title="Memo")
    assert len(source.text) // TEXT_ROOM >= 5

    with pytest.raises(InputError, match="read_chars"):
        classic.summarize(context(llm, source), source)

    assert COMBINE not in llm.ops


def test_a_source_just_inside_the_budget_is_not_refused(tracker: UsageTracker) -> None:
    # four blocks leave 2 000 // 4 = 500 characters for each part's notes: the least allowed
    llm = RecordingLLM(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source("word " * (TEXT_ROOM * 4 // 5 - 10), title="Memo")
    assert TEXT_ROOM * 3 < len(source.text) <= TEXT_ROOM * 4

    classic.summarize(context(llm, source), source)

    assert llm.ops[-1] == COMBINE


def test_an_error_in_a_part_names_the_part_and_the_op_and_stops_there(tracker: UsageTracker, long_text: str) -> None:
    def second_part_fails(user: str) -> str:
        if re.search(r"part 2 of", user, re.IGNORECASE):
            raise ModelError("model went away")
        return notes_for(user)

    llm = RecordingLLM(tracker, summarize_part=second_part_fails, summarize_combine=DRAFT)
    source = Source(long_text, title="Memo")
    n = len(split_blocks(long_text, TEXT_ROOM))

    with pytest.raises(Exception, match=rf"part 2 of {n}") as raised:
        classic.summarize(context(llm, source), source)

    assert "summarize_part" in str(raised.value)
    assert llm.ops == [PART, PART]  # the parts after the failing one are not asked, and there is no combine


# -- state and verbatim ----------------------------------------------------------------------------------


def test_the_state_of_a_long_summary_is_the_state_of_the_whole_text(tracker: UsageTracker, long_text: str) -> None:
    llm = RecordingLLM(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    source = Source(long_text, title="Memo")
    ctx = context(llm, source)

    cand = classic.summarize(ctx, source)

    assert cand.state == source_state("Memo", long_text, ctx.cfg.classifier.state_chars)


def test_the_state_of_a_long_text_equals_the_state_of_the_same_text_summarized_in_one_call(tracker: UsageTracker, long_text: str) -> None:
    parts = RecordingLLM(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    whole = RecordingLLM(tracker, summarize=DRAFT)
    source = Source(long_text, title="Memo")
    big = StepContext(
        whole,
        None,
        Prompts(),
        WikiConfig(
            bundle=Path("wiki"), mode="classic", llm=LLMConfig(read_chars=100_000), classifier=ClassifierConfig(model="m", api_key="k")
        ),
        "librarian/system",
        source,
    )

    assert classic.summarize(context(parts, source), source).state == classic.summarize(big, source).state
    assert whole.ops == [SUMMARIZE]


def test_verbatim_mode_never_goes_through_parts(tracker: UsageTracker, long_text: str) -> None:
    llm = RecordingLLM(tracker)
    source = Source(long_text, title="Memo")
    ctx = context(llm, source, summarize=False)

    cand = classic.summarize(ctx, source)

    assert llm.calls == []
    assert cand.body == long_text
