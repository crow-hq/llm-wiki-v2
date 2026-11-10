# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A note written from a source records, as `omitted`, the headings of the source that none of its words of four letters appear in."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents.base import OMITTED_MAX, omitted_headings, source_headings
from llmw2.agents.data import Candidate, NoteDraft, Route, Source
from llmw2.agents.steps import StepContext
from llmw2.bundle.tree import Folder, Note
from llmw2.models.usage import UsageTracker
from tests.wiki.areas import STEPS
from tests.wiki.fakes import FakeLLM

SOURCE = "# Overview\nThe pricing of the plans.\n## Warranty terms\nTwo years.\n### Returns policy\nBy mail.\n# FAQ\nShort."


def to_red(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
    folder = root.find("/red")
    assert folder is not None
    return Route(folder, False, [folder])


def omitted(wiki: Wiki, rel: str) -> object:
    return wiki.store.read(rel).frontmatter.get("omitted")


def with_body(bundle: Path, body: str) -> Wiki:
    steps = replace(STEPS, route=to_red, summarize=lambda ctx, source: Candidate("Plans", "The plans.", [], body))
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=FakeLLM(UsageTracker()), steps=steps)
    wiki.init()
    wiki.create_folder("/", "red", "Red things.")
    return wiki


# -- the rule ---------------------------------------------------------------------------------


def test_the_headings_of_a_source_are_the_markdown_lines_up_to_the_third_level() -> None:
    text = "# One\n## Two\n### Three\n#### Four\n```\n# in code\n```\n#hashtag\n\n   ## Indented  ##\n"

    assert source_headings(text) == ["One", "Two", "Three", "Indented"]


def test_a_heading_is_omitted_when_none_of_its_words_of_four_letters_is_in_the_body() -> None:
    found = omitted_headings(SOURCE, "The pricing of the plans, a long OVERVIEW. Returns: by mail.")

    assert found == ["Warranty terms"]  # "Returns policy" has "returns"; "FAQ" has no word to judge


def test_the_words_are_whole_words_in_any_case_and_letters_only() -> None:
    assert omitted_headings("# Delivery", "Deliveries are weekly.") == ["Delivery"]
    assert omitted_headings("# Prezzi e Garanzia", "la GARANZIA dura due anni") == []


def test_earlier_omissions_stay_while_the_body_still_leaves_them_out_and_the_list_is_capped() -> None:
    assert omitted_headings("# Alpha beta", "Alpha now", ["Gamma delta", "Alpha beta"]) == ["Gamma delta"]
    many = "\n".join(f"# Heading{chr(97 + i)}{chr(97 + i)}{chr(97 + i)}{chr(97 + i)}" for i in range(26))
    assert len(omitted_headings(many, "nothing")) == OMITTED_MAX


# -- in the notes -----------------------------------------------------------------------------


def test_a_new_note_records_the_headings_its_body_leaves_out(bundle: Path) -> None:
    wiki = with_body(bundle, "The pricing of the plans, in short.")

    rel = wiki.ingest(SOURCE, title="Plans").note

    assert omitted(wiki, rel) == ["Overview", "Warranty terms", "Returns policy"]


def test_a_note_that_leaves_nothing_out_has_no_field(bundle: Path) -> None:
    wiki = with_body(bundle, "Overview, pricing, warranty terms, returns policy.")

    rel = wiki.ingest(SOURCE, title="Plans").note

    assert "omitted" not in wiki.store.read(rel).frontmatter


def test_a_source_without_headings_adds_no_field(bundle: Path) -> None:
    wiki = with_body(bundle, "Anything.")

    rel = wiki.ingest("No headings, just a long paragraph of text.", title="Plans").note

    assert "omitted" not in wiki.store.read(rel).frontmatter


def test_the_field_is_information_only_the_body_and_its_hash_are_as_written(bundle: Path) -> None:
    wiki = with_body(bundle, "Short body.")

    rel = wiki.ingest("# Warranty terms\nTwo years.", title="Plans").note

    note = wiki.store.read(rel)
    assert note.body.strip() == "Short body." and omitted(wiki, rel) == ["Warranty terms"]
    assert wiki.check() == []


def test_a_merge_keeps_what_earlier_sources_left_out_that_the_note_still_leaves_out(bundle: Path) -> None:
    def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        return NoteDraft(title=note.title, summary=note.summary, body="Merged body about pricing and delivery.")

    steps = replace(
        STEPS,
        route=to_red,
        summarize=lambda ctx, source: Candidate("Plans", "The plans.", [], "Pricing only."),
        match=lambda ctx, pool, cand: pool[0] if pool else None,
        consolidate=lambda ctx, note, cand: True,
        merge=merge,
    )
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=FakeLLM(UsageTracker()), steps=steps)
    wiki.init()
    wiki.create_folder("/", "red", "Red things.")
    first = wiki.ingest("# Warranty terms\n# Delivery\nText.", title="Plans").note
    assert omitted(wiki, first) == ["Warranty terms", "Delivery"]

    wiki.ingest("# Support hours\nMore.", title="More")

    assert omitted(wiki, first) == ["Warranty terms", "Support hours"]  # delivery is in the merged body now


@pytest.mark.parametrize("text", ["", "# "])
def test_an_empty_heading_is_ignored(text: str) -> None:
    assert source_headings(text) == []
