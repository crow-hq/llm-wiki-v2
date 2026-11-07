# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`llm.note_copy_words`: with `note_steps`, a short source is its own body and only the metadata is asked for."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2 import Source, StepContext, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.config import LLMConfig
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeLLM

TEXT = "The contract was signed by the company that has not paid the invoices, and the customer said so in May."
WORDS = len(TEXT.split())
META = {"title": "Contract", "summary": "What the contract says.", "tags": ["contracts"]}


def context(copy_words: int) -> tuple[StepContext, FakeLLM]:
    llm = FakeLLM(UsageTracker())
    cfg = WikiConfig(bundle=Path("wiki"), mode="classic", llm=LLMConfig(model="fake/llm", note_steps=True, note_copy_words=copy_words))
    return StepContext(llm, None, Prompts(), cfg, "librarian/system"), llm


def test_a_source_under_the_threshold_is_the_body_and_only_the_metadata_is_asked() -> None:
    ctx, llm = context(WORDS + 1)
    llm.add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=TEXT, title="Note"))

    assert llm.ops == ["librarian/summarize_meta"] and cand.body == TEXT and cand.title == "Contract"


@pytest.mark.parametrize("copy_words", [WORDS, WORDS - 3, 0])
def test_a_source_at_or_over_the_threshold_gets_a_written_body(copy_words: int) -> None:
    ctx, llm = context(copy_words)
    llm.add("librarian/summarize_body", "The contract.").add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=TEXT, title="Note"))

    assert llm.ops == ["librarian/summarize_body", "librarian/summarize_meta"] and cand.body == "The contract."


def test_the_default_is_off() -> None:
    assert LLMConfig(model="fake/llm").note_copy_words == 0
