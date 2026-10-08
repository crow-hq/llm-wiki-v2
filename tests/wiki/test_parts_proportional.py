# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Round 8, blind from spec-fase2b.md: the notes of the parts keep what they write under `note_length="proportional"`."""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Source, StepContext, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.agents.state import split_blocks
from llmw2.config import ClassifierConfig, LLMConfig
from llmw2.models.llm import reply_tokens
from llmw2.models.usage import UsageTracker
from tests.wiki.test_summarize_long import COMBINE, PART, RecordingLLM, document

READ = 20_000  # a text room of 14 000: the document below takes two blocks of about 7 800 characters
ROOM = READ - 6_000
PART_REPLY_FACTOR = 1.3
MIN_PART = 500
TITLE = "Whole note"


class CapLLM(RecordingLLM):
    """A RecordingLLM that also keeps the output cap (max_tokens) of each call."""

    def __init__(self, usage: UsageTracker, **handlers: Any) -> None:
        super().__init__(usage, **handlers)
        self.caps: list[tuple[str, int | None]] = []

    def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
        self.caps.append((op, max_tokens))
        return super().chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)

    def cap_of(self, op: str) -> list[int | None]:
        return [cap for o, cap in self.caps if o == op]


def stated_cap(prompt: str) -> int:
    found = re.search(r"At most (\d+) characters", prompt)
    assert found is not None, "the prompt of a part states its cap"
    return int(found.group(1))


def part_number(prompt: str) -> int:
    found = re.search(r"part (\d+) of \d+", prompt, re.IGNORECASE)
    assert found is not None
    return int(found.group(1))


def full_notes(prompt: str) -> str:
    """Notes of exactly the stated cap: a code of the part, then plain words, ending on a letter."""
    k, cap = part_number(prompt), stated_cap(prompt)
    head = f"- Code ZX-{k}00: "
    return head + "w" * (cap - len(head))


def codes_notes(prompt: str) -> str:
    k = part_number(prompt)
    return f"- Code ZX-{k}00 and code QR-{k}11."


def plain_notes(prompt: str) -> str:
    return "- the part says only things in plain words, with nothing to measure."


def draft(body: str) -> dict[str, Any]:
    return {"title": TITLE, "summary": "All of it.", "tags": ["t"], "body": body}


def run(
    tracker: UsageTracker, note_length: str, part: Any, body: str = "Body.", note_chars: int = 1_000, sections: int = 10
) -> tuple[CapLLM, Any, list[int]]:
    """Summarize a long document; return (llm, candidate, the expected cap of each part under proportional)."""
    text = document(sections=sections)
    llm = CapLLM(tracker, summarize_part=part, summarize_combine=draft(body))
    source = Source(text, title="Memo", resource="/raw/memo.md")
    cand = classic.summarize(context(llm, source, note_length=note_length, note_chars=note_chars), source)
    blocks = split_blocks(text, ROOM)
    n = len(blocks)
    assert n >= 2
    return llm, cand, [min(ROOM // n, len(b.text) // 2) for b in blocks]


def context(llm: CapLLM, source: Source, **fields: Any) -> StepContext:
    classifier = ClassifierConfig(model="fake/jev", api_key="fake-key")
    cfg = WikiConfig(bundle=Path("wiki"), mode="classic", llm=LLMConfig(read_chars=READ), classifier=classifier, **fields)
    return StepContext(llm, None, Prompts(), cfg, "librarian/system", source)


def warnings_of(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING and "lacks" in r.getMessage()]


# -- the cap of a part ----------------------------------------------------------------------------------


@pytest.mark.parametrize("note_length", ["fixed", "proportional"])
def test_the_prompt_of_each_part_states_the_cap_of_its_mode(tracker: UsageTracker, note_length: str) -> None:
    llm, _, room_caps = run(tracker, note_length, plain_notes)

    stated = [stated_cap(p) for p in llm.prompts(PART)]

    if note_length == "fixed":
        blocks = split_blocks(document(sections=10), ROOM)
        assert stated == [min(1_000 // 2, ROOM // len(blocks), len(b.text) // 2) for b in blocks]
        assert stated == [500] * len(blocks)  # as today
    else:
        assert stated == room_caps
        assert all(cap > 500 for cap in stated)


def test_under_fixed_the_cap_is_still_cut_by_the_half_of_the_note_chars(tracker: UsageTracker) -> None:
    llm, _, _ = run(tracker, "fixed", plain_notes, note_chars=2_000)

    assert [stated_cap(p) for p in llm.prompts(PART)] == [1_000] * len(llm.prompts(PART))


def test_under_proportional_the_note_chars_do_not_cut_the_cap(tracker: UsageTracker) -> None:
    caps = []
    for note_chars in (1_000, 2_000, 4_000):
        llm, _, expected = run(tracker, "proportional", plain_notes, note_chars=note_chars)
        caps.append([stated_cap(p) for p in llm.prompts(PART)])
        assert caps[-1] == expected

    assert caps[0] == caps[1] == caps[2]


@pytest.mark.parametrize("note_length", ["fixed", "proportional"])
def test_the_reply_cap_of_a_part_follows_the_cap_with_the_same_rule(tracker: UsageTracker, note_length: str) -> None:
    llm, _, _ = run(tracker, note_length, plain_notes)

    stated = [stated_cap(p) for p in llm.prompts(PART)]

    assert llm.cap_of(PART) == [reply_tokens(max(MIN_PART, math.ceil(PART_REPLY_FACTOR * cap))) for cap in stated]


def test_the_cap_is_never_more_than_half_of_the_block_nor_the_share_of_the_room(tracker: UsageTracker) -> None:
    llm, _, _ = run(tracker, "proportional", plain_notes, sections=9)  # 14 074 characters: just two blocks
    blocks = split_blocks(document(sections=9), ROOM)

    stated = [stated_cap(p) for p in llm.prompts(PART)]

    assert stated == [min(ROOM // len(blocks), len(b.text) // 2) for b in blocks]
    assert all(cap <= len(b.text) // 2 for cap, b in zip(stated, blocks, strict=True))
    assert sum(stated) <= ROOM


# -- the notes reach the combine --------------------------------------------------------------------------


def test_under_proportional_notes_up_to_the_cap_reach_the_combine_uncut(tracker: UsageTracker, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        llm, _, _ = run(tracker, "proportional", full_notes)

    [combine] = llm.prompts(COMBINE)
    for prompt in llm.prompts(PART):
        notes = full_notes(prompt)
        assert len(notes) > 500  # more than the cut of today
        assert notes in combine
    assert not [r for r in caplog.records if "cut from" in r.getMessage()]  # nothing was cut


def test_under_fixed_notes_longer_than_the_half_of_the_note_chars_are_cut(tracker: UsageTracker) -> None:
    def long_notes(prompt: str) -> str:
        return full_notes(prompt) + " " + "v" * 3_000

    llm, _, _ = run(tracker, "fixed", long_notes)

    [combine] = llm.prompts(COMBINE)
    assert "v" * 600 not in combine
    assert max(len(line) for line in combine.splitlines()) <= 3_000


def test_under_proportional_notes_over_the_cap_are_still_cut_to_it(tracker: UsageTracker) -> None:
    def long_notes(prompt: str) -> str:
        return full_notes(prompt) + " " + "v" * 9_000

    llm, _, caps = run(tracker, "proportional", long_notes)

    [combine] = llm.prompts(COMBINE)
    assert "v" * (max(caps) + 1) not in combine
    assert "v" * 9_000 not in combine


# -- the check after the combine ---------------------------------------------------------------------------


@pytest.mark.parametrize("note_length", ["fixed", "proportional"])
def test_it_warns_with_the_counts_when_the_body_lacks_values_of_the_part_notes(
    tracker: UsageTracker, caplog: pytest.LogCaptureFixture, note_length: str
) -> None:
    with caplog.at_level(logging.WARNING):
        run(tracker, note_length, codes_notes, body="A body with ZX-100 and nothing else.")

    notes = classic.incoming_values("ZX-100 QR-111 ZX-200 QR-211")  # a code counts as itself and as its number
    lacking = len(notes - classic.incoming_values("A body with ZX-100 and nothing else."))
    [message] = warnings_of(caplog)
    assert f"lacks {lacking} of {len(notes)} values" in message
    assert repr(TITLE) in message


@pytest.mark.parametrize("note_length", ["fixed", "proportional"])
def test_it_warns_when_the_body_has_none_of_the_values(tracker: UsageTracker, caplog: pytest.LogCaptureFixture, note_length: str) -> None:
    with caplog.at_level(logging.WARNING):
        run(tracker, note_length, codes_notes, body="Nothing of the codes.")

    total = len(classic.incoming_values("ZX-100 QR-111 ZX-200 QR-211"))
    [message] = warnings_of(caplog)
    assert f"lacks {total} of {total} values" in message


@pytest.mark.parametrize("note_length", ["fixed", "proportional"])
def test_it_logs_nothing_when_the_body_has_every_value(tracker: UsageTracker, caplog: pytest.LogCaptureFixture, note_length: str) -> None:
    body = "All of them: ZX-100, QR-111, ZX-200, QR-211."
    with caplog.at_level(logging.WARNING):
        run(tracker, note_length, codes_notes, body=body)

    assert warnings_of(caplog) == []


@pytest.mark.parametrize("note_length", ["fixed", "proportional"])
def test_it_logs_nothing_when_the_part_notes_have_no_values(
    tracker: UsageTracker, caplog: pytest.LogCaptureFixture, note_length: str
) -> None:
    with caplog.at_level(logging.WARNING):
        run(tracker, note_length, plain_notes, body="A body of plain words.")

    assert warnings_of(caplog) == []


@pytest.mark.parametrize("note_length", ["fixed", "proportional"])
def test_the_draft_is_the_combine_reply_with_one_call_whatever_the_check_finds(tracker: UsageTracker, note_length: str) -> None:
    body = "A body with ZX-100 and nothing else."
    llm, cand, _ = run(tracker, note_length, codes_notes, body=body)

    assert llm.ops.count(COMBINE) == 1
    assert llm.ops[-1] == COMBINE
    assert (cand.title, cand.summary, cand.body) == (TITLE, "All of it.", body)


@pytest.mark.parametrize("note_length", ["fixed", "proportional"])
def test_the_returned_draft_is_the_same_when_nothing_is_missing(tracker: UsageTracker, note_length: str) -> None:
    body = "ZX-100 QR-111 ZX-200 QR-211"
    llm, cand, _ = run(tracker, note_length, codes_notes, body=body)

    assert llm.ops.count(COMBINE) == 1
    assert cand.body == body
