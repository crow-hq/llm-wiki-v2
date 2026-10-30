# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: several note paths in one bracket are each a citation, and an Answer carries the titles of what it cites."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from mcp import Client

from llmw2 import Wiki
from llmw2.memory import Memory
from llmw2.server import create_app
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_researcher_classic import ANSWER, NAVIGATE, make_wiki

OVERVIEW, REVENUE, PRICING = "overview.md", "finance/revenue-policy.md", "finance/pricing-tiers.md"
TITLES = {OVERVIEW: "Overview", REVENUE: "Revenue policy", PRICING: "Pricing tiers"}


@pytest.fixture
def wiki(bundle: Path, llm: FakeLLM) -> Wiki:
    return make_wiki(bundle, llm)


def ask(wiki: Wiki, llm: FakeLLM, text: str, *, read: tuple[str, ...] = (OVERVIEW, REVENUE, PRICING)) -> Any:
    """Ask with a navigation that reads the notes in `read`, and a model that answers `text`."""
    here = [p for p in read if "/" not in p]
    finance = [p for p in read if p.startswith("finance/")]
    llm.add(NAVIGATE, {"open": ["finance"], "select": here}, {"select": finance, "done": True}).add(ANSWER, text)
    return wiki.ask("What do we know?")


# -- several paths in one bracket -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bracket",
    [
        f"[{OVERVIEW}; {REVENUE}]",
        f"[{OVERVIEW}, {REVENUE}]",
        f"[{OVERVIEW};{REVENUE}]",
        f"[{OVERVIEW},{REVENUE}]",
        f"[{OVERVIEW}  ;   {REVENUE}]",
        f"[{OVERVIEW} ,  {REVENUE}]",
    ],
)
def test_a_bracket_with_two_paths_cites_both(wiki: Wiki, llm: FakeLLM, bracket: str) -> None:
    answer = ask(wiki, llm, f"We sell widgets and book on delivery {bracket}.")

    assert answer.citations == [OVERVIEW, REVENUE]


def test_a_bracket_with_three_paths_and_both_separators_cites_all_three(wiki: Wiki, llm: FakeLLM) -> None:
    answer = ask(wiki, llm, f"Everything [{PRICING}; {OVERVIEW}, {REVENUE}].")

    assert answer.citations == [PRICING, OVERVIEW, REVENUE]


def test_citations_keep_the_order_of_first_appearance_across_brackets_without_duplicates(wiki: Wiki, llm: FakeLLM) -> None:
    answer = ask(wiki, llm, f"One [{REVENUE}; {OVERVIEW}]. Two [{OVERVIEW}]. Three [{PRICING}, {REVENUE}].")

    assert answer.citations == [REVENUE, OVERVIEW, PRICING]


def test_a_path_repeated_inside_one_bracket_is_cited_once(wiki: Wiki, llm: FakeLLM) -> None:
    answer = ask(wiki, llm, f"Twice [{OVERVIEW}; {OVERVIEW}].")

    assert answer.citations == [OVERVIEW]


def test_in_a_bracket_only_the_notes_the_answer_read_count(wiki: Wiki, llm: FakeLLM) -> None:
    answer = ask(wiki, llm, f"Mixed [{OVERVIEW}; finance/ghost.md; {PRICING}].", read=(OVERVIEW, REVENUE))

    assert answer.citations == [OVERVIEW]  # a made-up path and a real note that was not read are both left out


def test_a_bracket_holding_other_words_is_not_a_citation(wiki: Wiki, llm: FakeLLM) -> None:
    text = f"See [see {OVERVIEW}], [{OVERVIEW} and {REVENUE}] or [note: {PRICING}]."

    answer = ask(wiki, llm, text)

    assert answer.citations == []


def test_a_bracket_with_a_path_that_does_not_end_in_md_is_not_a_citation(wiki: Wiki, llm: FakeLLM) -> None:
    answer = ask(wiki, llm, "See [overview; finance/revenue-policy] and [overview.txt].")

    assert answer.citations == []


def test_a_bracket_with_one_path_is_still_a_citation(wiki: Wiki, llm: FakeLLM) -> None:
    answer = ask(wiki, llm, f"Booked on delivery [{REVENUE}].")

    assert answer.citations == [REVENUE]


def test_two_brackets_side_by_side_are_each_a_citation(wiki: Wiki, llm: FakeLLM) -> None:
    answer = ask(wiki, llm, f"Both [{OVERVIEW}][{REVENUE}].")

    assert answer.citations == [OVERVIEW, REVENUE]


def test_the_answer_text_is_left_as_the_model_wrote_it(wiki: Wiki, llm: FakeLLM) -> None:
    text = f"Both [{OVERVIEW}; {REVENUE}]."

    assert ask(wiki, llm, text).text == text


# -- Answer.titles ----------------------------------------------------------------------------------------


def test_titles_maps_each_cited_path_to_its_note_title(wiki: Wiki, llm: FakeLLM) -> None:
    answer = ask(wiki, llm, f"Both [{OVERVIEW}; {REVENUE}].")

    assert answer.titles == {OVERVIEW: "Overview", REVENUE: "Revenue policy"}


def test_titles_holds_only_the_cited_notes_not_every_note_read(wiki: Wiki, llm: FakeLLM) -> None:
    answer = ask(wiki, llm, f"Only one [{PRICING}].")

    assert answer.notes == [OVERVIEW, REVENUE, PRICING]
    assert answer.titles == {PRICING: "Pricing tiers"}


def test_titles_is_empty_when_nothing_is_cited(wiki: Wiki, llm: FakeLLM) -> None:
    answer = ask(wiki, llm, "No citation here.")

    assert answer.titles == {}


def test_titles_is_empty_when_the_wiki_has_nothing_to_answer_with(classic_wiki: Wiki) -> None:
    assert classic_wiki.ask("Anything?").titles == {}


def test_titles_keys_are_the_citations(wiki: Wiki, llm: FakeLLM) -> None:
    answer = ask(wiki, llm, f"A [{REVENUE}, {PRICING}] and B [{OVERVIEW}].")

    assert list(answer.titles) == answer.citations


def test_titles_is_in_to_dict(wiki: Wiki, llm: FakeLLM) -> None:
    answer = ask(wiki, llm, f"Both [{OVERVIEW}; {REVENUE}].")

    as_dict = answer.to_dict()

    assert as_dict["titles"] == {OVERVIEW: "Overview", REVENUE: "Revenue policy"}
    assert as_dict["citations"] == [OVERVIEW, REVENUE]


def test_the_http_ask_reply_carries_the_titles(wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(NAVIGATE, {"open": ["finance"], "select": [OVERVIEW]}, {"select": [REVENUE], "done": True})
    llm.add(ANSWER, f"Both [{OVERVIEW}; {REVENUE}].")

    response = TestClient(create_app(wiki)).post("/ask", json={"question": "What do we know?"})

    assert response.status_code == 200
    body = response.json()
    assert body["citations"] == [OVERVIEW, REVENUE]
    assert body["titles"] == {OVERVIEW: "Overview", REVENUE: "Revenue policy"}


# -- the MCP recall ---------------------------------------------------------------------------------------


def recall(wiki: Wiki, question: str) -> Any:
    wiki.cfg.mcp = True

    async def go() -> Any:
        async with Client(Memory(lambda: wiki).server) as client:
            return await client.call_tool("recall", {"question": question})

    return asyncio.run(go())


def test_recall_keeps_answer_and_notes_and_adds_the_titles(wiki: Wiki, llm: FakeLLM) -> None:
    text = f"Both [{OVERVIEW}; {REVENUE}]."
    llm.add(NAVIGATE, {"open": ["finance"], "select": [OVERVIEW]}, {"select": [REVENUE], "done": True}).add(ANSWER, text)

    found = recall(wiki, "What do we know?")

    assert not found.is_error
    assert found.structured_content["answer"] == text
    assert set(found.structured_content["notes"]) >= {OVERVIEW, REVENUE}
    assert found.structured_content["titles"] == {OVERVIEW: "Overview", REVENUE: "Revenue policy"}


def test_recall_with_nothing_to_cite_has_empty_titles(classic_wiki: Wiki) -> None:
    found = recall(classic_wiki, "Anything?")

    assert not found.is_error
    assert found.structured_content["notes"] == []
    assert found.structured_content["titles"] == {}
