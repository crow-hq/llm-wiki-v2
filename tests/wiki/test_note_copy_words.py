# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`llm.note_copy_words`: with `note_steps`, a short source is its own body and only the metadata is asked for."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2 import Source, StepContext, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.agents.state import split_blocks
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


def test_a_copied_markdown_file_leaves_its_front_matter_out_of_the_note() -> None:
    ctx, llm = context(100)
    llm.add("librarian/summarize_meta", META)
    text = f"---\ntype: Note\ntitle: Contract\ntags: [contracts]\n---\n# Contract\n\n{TEXT}"

    cand = classic.summarize(ctx, Source(text=text, title="Note"))

    assert cand.body == f"# Contract\n\n{TEXT}"  # an Obsidian note's YAML is not its body


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("---\na: 1\n---\nBody", "Body"),
        ("﻿---\na: 1\n...\n\nBody", "Body"),
        ("Body\n---\nmore", "Body\n---\nmore"),  # a rule in the text is not front matter
        ("---\nonly: yaml\n---\n", "---\nonly: yaml\n---\n"),  # nothing else: the text as it is
        ("---\nnot closed\nBody", "---\nnot closed\nBody"),
    ],
)
def test_front_matter_is_only_the_yaml_block_a_file_opens_with(text: str, kept: str) -> None:
    from llmw2.agents.note_checks import without_frontmatter

    assert without_frontmatter(text) == kept


def test_the_default_is_off() -> None:
    assert LLMConfig(model="fake/llm").note_copy_words == 0


def test_a_long_source_under_the_threshold_is_copied_without_calls_on_its_parts() -> None:
    # over the 2 000 characters of text one call holds at the smallest read_chars
    long_text = "\n\n".join(f"## Section {i}\n\n{TEXT}" for i in range(40))
    llm = FakeLLM(UsageTracker())
    llm_cfg = LLMConfig(model="fake/llm", note_steps=True, note_copy_words=len(long_text.split()) + 1, read_chars=8_000)
    ctx = StepContext(llm, None, Prompts(), WikiConfig(bundle=Path("wiki"), mode="classic", llm=llm_cfg), "librarian/system")
    llm.add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=long_text, title="Note"))

    assert len(long_text) > 2_000 and llm.ops == ["librarian/summarize_meta"] and cand.body == long_text.strip()


@pytest.mark.parametrize(
    ("text", "line", "kept"),
    [
        ("# Spelling\n\nPart 1 of 2 — Spelling\nUse US spelling.\nThis is part 2 of 2, starting in: Words\nWords.", True,
         "# Spelling\n\nUse US spelling.\nWords."),
        ("Spelling - Part 2 of 2", False, "Spelling"),
        ("Part 1 of 2: Local setup for dbt", False, "Local setup for dbt"),
        ("No label here.", True, "No label here."),
    ],
)
def test_the_part_labels_a_small_writer_copies_are_removed(text: str, line: bool, kept: str) -> None:
    assert classic._without_part_labels(text, "A source with no such words.", line=line) == kept


def test_part_labels_the_source_itself_has_are_kept() -> None:
    title = "Module 3 - Part 2 of 2"

    assert classic._without_part_labels(title, "Module 3 - Part 2 of 2 (Practice)", line=False) == title


def _long_context(copy_parts: bool) -> tuple[StepContext, FakeLLM, str]:
    # over the 2 000 characters of text one call holds at the smallest read_chars, and over `note_copy_words`
    long_text = "\n\n".join(f"## Section {i}\n\n{TEXT}" for i in range(40))
    llm = FakeLLM(UsageTracker())
    llm_cfg = LLMConfig(model="fake/llm", note_steps=True, note_copy_words=50, note_copy_parts=copy_parts, read_chars=8_000)
    ctx = StepContext(llm, None, Prompts(), WikiConfig(bundle=Path("wiki"), mode="classic", llm=llm_cfg), "librarian/system")
    return ctx, llm, long_text


def test_with_note_copy_parts_a_source_longer_than_one_call_is_its_own_body() -> None:
    ctx, llm, long_text = _long_context(True)
    llm.add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=long_text, title="Note"))

    assert llm.ops == ["librarian/summarize_meta"] and cand.body == long_text.strip()


def test_with_note_copy_parts_a_source_that_fits_one_call_is_still_written() -> None:
    ctx, llm, _ = _long_context(True)
    text = " ".join([TEXT] * 4)  # over `note_copy_words`, within one call
    llm.add("librarian/summarize_body", "The contract.").add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=text, title="Note"))

    assert llm.ops == ["librarian/summarize_body", "librarian/summarize_meta"] and cand.body == "The contract."


def test_without_note_copy_parts_a_long_source_goes_by_parts() -> None:
    ctx, llm, long_text = _long_context(False)
    for _ in split_blocks(long_text, 2_000):
        llm.add("librarian/summarize_part", "Notes.")
    llm.add("librarian/summarize_combine_body", "The whole.").add("librarian/summarize_meta", META)

    classic.summarize(ctx, Source(text=long_text, title="Note"))

    assert "librarian/summarize_part" in llm.ops and "librarian/summarize_combine_body" in llm.ops


def test_note_copy_parts_is_off_by_default_and_read_from_the_environment() -> None:
    assert LLMConfig(model="fake/llm").note_copy_parts is False
    assert WikiConfig.from_env({"OKF_LLM_NOTE_COPY_PARTS": "1"}, bundle=Path("w")).llm.note_copy_parts is True
