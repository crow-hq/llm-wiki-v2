# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`merge` note to note: the model gets the existing note and the incoming note, not the raw source."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import CLASSIC, Candidate, NoteDraft, Origin, Source, StepContext, Steps, Wiki, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.bundle.tree import Note
from llmw2.config import LLMConfig
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_steps import MERGING, o_route, o_summarize

MERGE, SUMMARIZE = "librarian/merge", "librarian/summarize"
MERGE_SOURCE = "librarian/merge_source"
MERGED = {"title": "Seed", "summary": "Merged.", "tags": [], "body": "# Overview\n\nMerged note. Price 3,85 EUR."}
INCOMING = {
    "title": "Incoming title",
    "summary": "INCOMING SUMMARY.",
    "tags": ["incoming-tag"],
    "body": "# Overview\n\nINCOMING BODY with a new price of 3,85 EUR.",
}
RAW = "RAWSOURCE " * 20 + "the raw text the summary came from."
MODIFIED = "2026-09-01T10:00:00+00:00"


def wiki_with(bundle: Path, llm: FakeLLM, steps: Steps, /, **cfg: Any) -> Wiki:
    """A wiki with /finance/seed.md ("Seed.") in it."""
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", **cfg), llm=llm, steps=steps)
    wiki.init()
    folder, _ = wiki.store.create_folder(wiki.store.load().find("/"), "finance", "Money in and out.")
    wiki.store.write_note(
        folder, title="Seed", summary="Seeded.", body="Seed.", tags=[], source={"resource": "/raw/seed.md", "title": "Seed"}
    )
    return wiki


# the classic summarize and merge (the model writes both), with the decisions made by rules
LLM_MERGING = replace(MERGING, summarize=CLASSIC.summarize, merge=CLASSIC.merge)


def merge_prompt(llm: FakeLLM, op: str = MERGE) -> str:
    [(_, messages)] = [call for call in llm.calls if call[0] == op]
    return messages[1]["content"]


# -- the incoming note, not the raw source -------------------------------------------------------------


def test_the_merge_prompt_holds_the_existing_note_and_the_incoming_note_not_the_raw_source(bundle: Path, llm: FakeLLM) -> None:
    wiki = wiki_with(bundle, llm, LLM_MERGING)
    llm.add(SUMMARIZE, INCOMING).add(MERGE, MERGED)

    result = wiki.ingest(RAW, title="Memo", resource="https://x.test/a")

    prompt = merge_prompt(llm)
    assert result.action == "merged"
    assert "Seed." in prompt  # the existing note
    assert "INCOMING BODY" in prompt and "INCOMING SUMMARY." in prompt and "incoming-tag" in prompt and "Incoming title" in prompt
    assert "RAWSOURCE" not in prompt  # the raw text is not sent
    assert "https://x.test/a" in prompt and "Memo" in prompt  # where the note was written from


def test_the_merge_prompt_no_longer_gives_the_modified_date_of_the_origin(bundle: Path, llm: FakeLLM) -> None:
    wiki = wiki_with(bundle, llm, LLM_MERGING)
    llm.add(SUMMARIZE, INCOMING).add(MERGE, MERGED)

    wiki.ingest(RAW, title="Memo", origin=Origin("local", "", "memo.md", modified=MODIFIED))

    assert "2026-09-01" not in merge_prompt(llm)  # round 5: a stored date is not the date of the facts


def test_the_merge_prompt_has_no_date_when_the_origin_has_none(bundle: Path, llm: FakeLLM) -> None:
    wiki = wiki_with(bundle, llm, LLM_MERGING)
    llm.add(SUMMARIZE, INCOMING).add(MERGE, MERGED)

    wiki.ingest(RAW, title="Memo", origin=Origin("local", "", "memo.md"))

    assert "2026-09-01" not in merge_prompt(llm)


def test_the_merge_result_is_still_what_rewrites_the_note(bundle: Path, llm: FakeLLM) -> None:
    wiki = wiki_with(bundle, llm, LLM_MERGING)
    llm.add(SUMMARIZE, INCOMING).add(MERGE, MERGED)

    result = wiki.ingest(RAW, title="Memo")

    assert (result.action, result.note) == ("merged", "finance/seed.md")
    assert "Merged note." in (bundle / "finance" / "seed.md").read_text(encoding="utf-8")


def test_the_context_of_an_ingest_carries_the_candidate_to_every_step_after_summarize(bundle: Path, llm: FakeLLM) -> None:
    seen: dict[str, Candidate | None] = {}

    def spy(name: str, inner: Any) -> Any:
        def step(ctx: StepContext, *args: Any) -> Any:
            seen[name] = ctx.candidate
            return inner(ctx, *args)

        return step

    def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        seen["merge"] = ctx.candidate
        return NoteDraft(title=note.title, summary=note.summary, body="Merged.")

    steps = replace(MERGING, summarize=spy("summarize", o_summarize), route=spy("route", o_route), merge=merge)
    wiki = wiki_with(bundle, llm, steps)

    wiki.ingest("Some text.", title="Memo")

    assert seen["summarize"] is None  # not written yet
    assert seen["route"] is not None and seen["route"].title == "Memo"
    assert seen["merge"] is not None and seen["merge"].body == "Some text."


def test_step_context_candidate_defaults_to_none(llm: FakeLLM) -> None:
    ctx = StepContext(llm, None, Prompts(), WikiConfig(bundle=Path("wiki"), mode="classic"), "librarian/system", None)

    assert ctx.candidate is None


# -- a custom step that calls merge itself: no candidate, the source at its budget ---------------------------


def merge_without_candidate(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
    ctx.candidate = None
    return classic.merge(ctx, note, source)


def test_merge_without_a_candidate_reads_the_source(bundle: Path, llm: FakeLLM) -> None:
    steps = replace(MERGING, merge=merge_without_candidate)
    wiki = wiki_with(bundle, llm, steps)
    llm.add(MERGE, MERGED)

    wiki.ingest("SOURCE TEXT of the document.", title="Memo")

    prompt = merge_prompt(llm)
    assert "SOURCE TEXT of the document." in prompt
    assert "Seed." in prompt


def test_merge_without_a_candidate_cuts_the_source_to_the_budget_with_a_warning(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    steps = replace(MERGING, merge=merge_without_candidate)
    wiki = wiki_with(bundle, llm, steps, llm=LLMConfig(read_chars=8_000))  # a text room of 2 000, less the existing note
    llm.add(MERGE_SOURCE, MERGED)  # round 6: a long source is merged from the text directly
    text = "HEAD " + "~" * 5_000 + " TAIL"

    with caplog.at_level(logging.WARNING):
        wiki.ingest(text, title="Memo")

    prompt = merge_prompt(llm, MERGE_SOURCE)
    assert "HEAD" in prompt and "TAIL" not in prompt
    assert 1_500 < prompt.count("~") <= 2_000
    assert len(prompt) <= 8_000
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_merge_without_a_candidate_and_a_source_that_fits_does_not_warn(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    steps = replace(MERGING, merge=merge_without_candidate)
    wiki = wiki_with(bundle, llm, steps, llm=LLMConfig(read_chars=8_000))
    llm.add(MERGE, MERGED)

    with caplog.at_level(logging.WARNING):
        wiki.ingest("Short source.", title="Memo")

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_a_verbatim_incoming_note_over_the_budget_is_cut_with_a_warning(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    wiki = wiki_with(bundle, llm, LLM_MERGING, summarize=False, llm=LLMConfig(read_chars=8_000))
    llm.add(MERGE_SOURCE, MERGED)  # round 6: a long source is merged from the text directly
    text = "HEAD " + "~" * 5_000 + " TAIL"

    with caplog.at_level(logging.WARNING):
        wiki.ingest(text, title="Memo")

    prompt = merge_prompt(llm, MERGE_SOURCE)
    assert "HEAD" in prompt and "TAIL" not in prompt
    assert 1_500 < prompt.count("~") < 3_000  # the body at its budget, and the summary a verbatim candidate takes from the text
    assert len(prompt) <= 8_000
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


# -- prompt overrides --------------------------------------------------------------------------------------


OVERRIDE = (
    "Old-style merge.\nTitle: {{title}}\nSummary: {{summary}}\nTags: {{tags}}\n"
    "<existing>\n{{body}}\n</existing>\n<source>\nSRC[{{source_text}}]\n</source>\n"
)


@pytest.fixture
def override(tmp_path: Path) -> Path:
    folder = tmp_path / "prompts"
    (folder / "librarian").mkdir(parents=True)
    (folder / "librarian" / "merge.md").write_text(OVERRIDE, encoding="utf-8")
    return folder


def test_an_override_of_merge_that_uses_source_text_does_not_fail_with_a_candidate(bundle: Path, llm: FakeLLM, override: Path) -> None:
    wiki = wiki_with(bundle, llm, LLM_MERGING, prompts_dir=override)
    llm.add(SUMMARIZE, INCOMING).add(MERGE, MERGED)

    result = wiki.ingest(RAW, title="Memo")

    assert result.action == "merged"
    assert "SRC[]" in merge_prompt(llm)  # source_text is the empty string beside a candidate


def test_an_override_of_merge_that_uses_source_text_gets_the_cut_source_without_a_candidate(
    bundle: Path, llm: FakeLLM, override: Path
) -> None:
    wiki = wiki_with(bundle, llm, replace(MERGING, merge=merge_without_candidate), prompts_dir=override)
    llm.add(MERGE, MERGED)

    wiki.ingest("The source itself.", title="Memo")

    assert "SRC[The source itself.]" in merge_prompt(llm)


# -- the wording ---------------------------------------------------------------------------------------------


def test_the_merge_prompt_no_longer_says_never_to_shorten(bundle: Path, llm: FakeLLM) -> None:
    wiki = wiki_with(bundle, llm, LLM_MERGING)
    llm.add(SUMMARIZE, INCOMING).add(MERGE, MERGED)

    wiki.ingest(RAW, title="Memo")

    assert "Never shorten" not in merge_prompt(llm)
    assert "Never shorten" not in Prompts().load(MERGE)


def test_the_merge_prompt_keeps_the_rules_that_matter_and_the_length_rule(bundle: Path, llm: FakeLLM) -> None:
    wiki = wiki_with(bundle, llm, LLM_MERGING, note_chars=2_500)
    llm.add(SUMMARIZE, INCOMING).add(MERGE, MERGED)

    wiki.ingest(RAW, title="Memo")

    prompt = merge_prompt(llm)
    assert "previously" in prompt  # the contrast rule
    assert "about 2500 characters" in prompt  # the length comes from note_rules
    assert "<note>" in prompt or "<source>" in prompt  # the incoming material sits in a tag the untrusted rule covers


def test_a_merge_longer_than_the_soft_cap_is_warned_and_kept(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    wiki = wiki_with(bundle, llm, LLM_MERGING, llm=LLMConfig(read_chars=8_000))  # the cap is 1 000
    llm.add(SUMMARIZE, INCOMING).add(MERGE, {**MERGED, "body": "# Overview\n\n" + "w" * 1_400 + " 3,85 EUR"})

    with caplog.at_level(logging.WARNING):
        wiki.ingest(RAW, title="Memo")

    assert "w" * 1_400 in (bundle / "finance" / "seed.md").read_text(encoding="utf-8")
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]
