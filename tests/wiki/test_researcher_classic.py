# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Classic Researcher: the LLM navigates the folder tree, then answers from the notes it selected."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents.researcher import NO_ANSWER
from llmw2.bundle.tree import Folder, Note
from tests.wiki.fakes import FakeLLM

NAVIGATE, ANSWER = "researcher/navigate", "researcher/answer"

REVENUE_BODY = "Revenue is booked on delivery.\n\n## Exceptions\n\nPrepaid plans are booked monthly."


def add_note(wiki: Wiki, folder: Folder, title: str, summary: str, body: str) -> Note:
    source = {"resource": "https://example.com", "title": title}
    return wiki.store.write_note(folder, title=title, summary=summary, body=body, tags=[], source=source)


def make_wiki(bundle: Path, llm: FakeLLM, **cfg: Any) -> Wiki:
    """/overview.md, /finance/{revenue-policy,pricing-tiers}.md, /people/hiring-process.md"""
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", **cfg), llm=llm)
    wiki.init()
    root = wiki.store.load()
    add_note(wiki, root, "Overview", "What the company does.", "We sell widgets.")
    finance, _ = wiki.store.create_folder(root, "finance", "Money matters.")
    add_note(wiki, finance, "Revenue policy", "When revenue is booked.", REVENUE_BODY)
    add_note(wiki, finance, "Pricing tiers", "The three price plans.", "Basic, Pro and Enterprise.")
    people, _ = wiki.store.create_folder(root, "people", "Staff and hiring.")
    add_note(wiki, people, "Hiring process", "How we hire.", "Two interviews and a take-home task.")
    return wiki


@pytest.fixture
def wiki(bundle: Path, llm: FakeLLM) -> Wiki:
    return make_wiki(bundle, llm)


def browsed(llm: FakeLLM) -> list[str]:
    """The folders the LLM was shown, in order, one per navigate call."""
    pattern = re.compile(r"browsing the folder (\S+):")
    return [pattern.search(msgs[-1]["content"])[1] for op, msgs in llm.calls if op == NAVIGATE]


def prompt(llm: FakeLLM, op: str) -> str:
    return next(msgs[-1]["content"] for o, msgs in llm.calls if o == op)


def test_navigation_opens_sibling_folders_and_selects_from_each(wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(
        NAVIGATE,
        {"open": ["finance", "people"], "select": ["overview.md"]},
        {"select": ["finance/revenue-policy.md"]},
        {"select": ["people/hiring-process.md"]},
    ).add(ANSWER, "Answer.")

    answer = wiki.ask("How do we book revenue and hire people?")

    assert browsed(llm) == ["/", "/finance", "/people"]
    assert answer.notes == ["overview.md", "finance/revenue-policy.md", "people/hiring-process.md"]
    assert llm.ops[-1] == ANSWER
    assert {(d.step, d.decider) for d in answer.decisions} == {("navigate", "llm")}


def test_done_stops_navigation_before_the_frontier_is_empty(wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(
        NAVIGATE,
        {"open": ["finance", "people"]},
        {"select": ["finance/revenue-policy.md"], "done": True},
    ).add(ANSWER, "Answer.")

    answer = wiki.ask("When is revenue booked?")

    assert browsed(llm) == ["/", "/finance"]
    assert answer.notes == ["finance/revenue-policy.md"]


def test_select_when_a_folder_answers_done_with_nothing_selected_then_queued_folders_are_still_visited(
    bundle: Path, llm: FakeLLM
) -> None:
    # ARRANGE
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm)
    wiki.init()
    root = wiki.store.load()
    operations, _ = wiki.store.create_folder(root, "operations", "Running the kitchen.")
    recipes, _ = wiki.store.create_folder(root, "recipes", "Dishes we cook.")
    pricing, _ = wiki.store.create_folder(operations, "pricing", "What things cost.")
    add_note(wiki, recipes, "Risotto", "How to cook risotto.", "Stir for 18 minutes.")
    price_list = add_note(wiki, pricing, "Price list", "What each dish costs.", "Risotto costs 14 euros.")
    llm.add(
        NAVIGATE,
        {"open": ["operations", "recipes"]},
        {"open": ["pricing"]},
        {"done": True},  # /recipes: nothing here, but /operations/pricing is still queued
        {"select": [price_list.rel], "done": True},
    ).add(ANSWER, "Answer.")

    # ACT
    answer = wiki.ask("How much does the risotto cost?")

    # ASSERT
    assert browsed(llm) == ["/", "/operations", "/recipes", "/operations/pricing"]
    assert answer.notes == [price_list.rel]


def test_max_steps_caps_the_folders_opened(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm, max_steps=2)
    llm.add(
        NAVIGATE,
        {"open": ["finance", "people"]},
        {"select": ["finance/pricing-tiers.md"]},
    ).add(ANSWER, "Answer.")

    answer = wiki.ask("What do we sell and how do we hire?")

    assert browsed(llm) == ["/", "/finance"]
    assert answer.notes == ["finance/pricing-tiers.md"]


def test_max_notes_caps_the_notes_read(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm, max_notes=2)
    llm.add(
        NAVIGATE,
        {"open": ["finance", "people"], "select": ["overview.md"]},
        {"select": ["finance/revenue-policy.md", "finance/pricing-tiers.md"]},
    ).add(ANSWER, "Answer.")

    answer = wiki.ask("Tell me everything.")

    assert answer.notes == ["overview.md", "finance/revenue-policy.md"]
    assert browsed(llm) == ["/", "/finance"]  # the cap is reached, /people is never opened
    assert "finance/pricing-tiers.md" not in prompt(llm, ANSWER)


def test_empty_wiki_answers_no_answer_without_calling_the_llm(classic_wiki: Wiki, llm: FakeLLM) -> None:
    answer = classic_wiki.ask("Anything?")

    assert answer.text == NO_ANSWER
    assert answer.notes == [] and answer.citations == []
    assert llm.calls == []


def test_nothing_selected_answers_no_answer_without_an_answer_call(wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(NAVIGATE, {"reasoning": "Nothing here is about Mars."})

    answer = wiki.ask("What is the weather on Mars?")

    assert answer.text == NO_ANSWER
    assert answer.notes == []
    assert llm.ops == [NAVIGATE]


def test_answer_reads_the_full_text_of_the_selected_notes(wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(NAVIGATE, {"open": ["finance"]}, {"select": ["finance/revenue-policy.md"]}).add(ANSWER, "Answer.")

    wiki.ask("When is revenue booked?")

    user = prompt(llm, ANSWER)
    assert "Question: When is revenue booked?" in user
    full_text = f"Title: Revenue policy\nSummary: When revenue is booked.\n\n{REVENUE_BODY}"
    assert f"### finance/revenue-policy.md\n\n{full_text}" in user
    assert "Pricing tiers" not in user


def test_citations_keep_only_notes_that_were_read_once_each(wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(NAVIGATE, {"open": ["finance"], "select": ["overview.md"]}, {"select": ["finance/revenue-policy.md"]})
    text = (
        "Booked on delivery [finance/revenue-policy.md]; we sell widgets [overview.md]; "
        "prepaid is monthly [finance/revenue-policy.md]; plans [finance/pricing-tiers.md]; made up [finance/ghost.md]."
    )
    llm.add(ANSWER, text)

    answer = wiki.ask("When is revenue booked?")

    assert answer.text == text
    assert answer.citations == ["finance/revenue-policy.md", "overview.md"]


def test_unknown_notes_and_folders_in_the_reply_are_ignored(wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(
        NAVIGATE,
        {"open": ["finance", "archive"], "select": ["overview.md", "ghost.md", "finance/pricing-tiers.md"]},
        {"select": ["finance/pricing-tiers.md", "people/hiring-process.md"], "open": ["taxes"]},
    ).add(ANSWER, "Answer.")

    answer = wiki.ask("What do we sell?")

    assert browsed(llm) == ["/", "/finance"]
    # a path is only taken from the folder being browsed: pricing-tiers is picked in /finance, not in /
    assert answer.notes == ["overview.md", "finance/pricing-tiers.md"]


def test_a_folder_named_twice_is_opened_once(wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(
        NAVIGATE,
        {"open": ["finance", "/finance/"]},
        {"select": ["finance/revenue-policy.md"]},
        {"select": ["finance/revenue-policy.md"]},  # only consumed if /finance is browsed twice
    ).add(ANSWER, "Answer.")

    wiki.ask("When is revenue booked?")

    assert browsed(llm) == ["/", "/finance"]


def test_a_note_selected_twice_is_read_once(wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(NAVIGATE, {"select": ["overview.md", "/overview.md"], "done": True}).add(ANSWER, "Answer.")

    answer = wiki.ask("What do we do?")

    assert answer.notes == ["overview.md"]

