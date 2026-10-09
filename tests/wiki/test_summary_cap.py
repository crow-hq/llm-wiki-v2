# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The one-call summary's output cap follows the source (spec-giro5b-summary-cap)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from llmw2 import Source, StepContext, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.config import ClassifierConfig
from llmw2.models.llm import reply_tokens
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeLLM

SUMMARIZE = "librarian/summarize"
PART = "librarian/summarize_part"
COMBINE = "librarian/summarize_combine"
BODY = {"title": "T", "summary": "S.", "tags": [], "body": "B."}
NOTE = 3_000


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


def context(fake: Any, tmp_path: Path, /, **fields: Any) -> StepContext:
    classifier = ClassifierConfig(model="fake/jev", api_key="fake-key")
    cfg = WikiConfig(bundle=tmp_path / "wiki", mode="classic", classifier=classifier, note_chars=NOTE, **fields)
    return StepContext(fake, None, Prompts(), cfg, "librarian/system", None)


def test_summary_version_is_6() -> None:
    assert classic.SUMMARY_VERSION == 7


def test_one_call_summarize_of_a_short_source_is_capped_by_a_note(tracker: UsageTracker, tmp_path: Path) -> None:
    llm = CapRecorder(tracker).add(SUMMARIZE, BODY)
    ctx = context(llm, tmp_path, llm={"read_chars": 100_000})
    text = "A short memo. " * 30
    assert len(text) < ctx.cfg.effective_note_chars

    classic.summarize(ctx, Source(text, title="Memo"))

    assert llm.caps == [(SUMMARIZE, reply_tokens(ctx.cfg.effective_note_chars))]


def test_one_call_summarize_of_a_long_source_is_capped_by_its_length(tracker: UsageTracker, tmp_path: Path) -> None:
    llm = CapRecorder(tracker).add(SUMMARIZE, BODY)
    ctx = context(llm, tmp_path, llm={"read_chars": 100_000})
    text = "The parties agree on the terms. " * 1_500  # ~48k characters: one call, far above the note cap
    assert ctx.cfg.effective_note_chars < len(text) <= ctx.cfg.effective_read_chars - classic.PROMPT_ROOM

    classic.summarize(ctx, Source(text, title="Memo"))

    assert llm.caps == [(SUMMARIZE, reply_tokens(len(text)))]
    assert llm.caps[0][1] > reply_tokens(ctx.cfg.effective_note_chars)


def test_one_call_summarize_when_the_source_equals_the_note_cap_uses_that_cap(tracker: UsageTracker, tmp_path: Path) -> None:
    llm = CapRecorder(tracker).add(SUMMARIZE, BODY)
    ctx = context(llm, tmp_path, llm={"read_chars": 100_000})

    classic.summarize(ctx, Source("x" * ctx.cfg.effective_note_chars, title="Memo"))

    assert llm.caps == [(SUMMARIZE, reply_tokens(ctx.cfg.effective_note_chars))]


def test_map_reduce_parts_and_combine_keep_their_caps(tracker: UsageTracker, tmp_path: Path) -> None:
    llm = CapRecorder(tracker).add(PART, "- a note.", "- a note.").add(COMBINE, BODY)
    ctx = context(llm, tmp_path, llm={"read_chars": 100_000}, note_length="fixed")
    filler = "The parties agree on the terms that follow and on nothing else at all, as the text says here."
    text = "\n\n".join([filler] * 1_600)  # ~150k characters: two parts

    classic.summarize(ctx, Source(text, title="Memo"))

    note = reply_tokens(ctx.cfg.effective_note_chars)
    part = reply_tokens(max(classic.MIN_PART, math.ceil(classic.PART_REPLY_FACTOR * (ctx.cfg.effective_note_chars // 2))))  # round 6
    assert [cap for op, cap in llm.caps if op == PART] == [part] * 2
    assert [cap for op, cap in llm.caps if op == COMBINE] == [note]
    assert SUMMARIZE not in [op for op, _ in llm.caps]
