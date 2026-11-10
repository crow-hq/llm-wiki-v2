# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`where` on a field that is a list: the field holds the value when one of its items is equal to it (as text)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents.steps import StepContext
from llmw2.bundle.tree import Folder, Note, matching
from llmw2.models.usage import UsageTracker
from tests.wiki.areas import STEPS, file
from tests.wiki.fakes import FakeLLM


def select(ctx: StepContext, root: Folder, question: str) -> list[Note]:
    return root.all_notes()


def answer(ctx: StepContext, question: str, notes: list[Note]) -> str:
    return "Answer " + " ".join(f"[{n.rel}]" for n in notes)


def build(bundle: Path) -> Wiki:
    wiki = Wiki(
        WikiConfig(bundle=bundle, mode="classic"),
        llm=FakeLLM(UsageTracker()),
        steps=replace(STEPS, select=select, answer=answer),
    )
    wiki.init()
    wiki.create_folder("/", "red", "Red things.")
    file(wiki, "/red", "Alpha", "Pricing of the plans.", fields={"teams": ["sales", "ops"], "year": 2024, "owner": "ana"})
    file(wiki, "/red", "Beta", "Pricing of the shops.", fields={"teams": ["ops"], "year": [2023, 2024], "owner": "bruno"})
    file(wiki, "/red", "Gamma", "Pricing of the rest.", fields={"teams": [], "owner": "ana"})
    return wiki


def rels(root: Folder) -> list[str]:
    return sorted(n.rel for n in root.all_notes())


@pytest.mark.parametrize(
    ("where", "expected"),
    [
        ({"teams": "sales"}, ["red/alpha.md"]),
        ({"teams": "ops"}, ["red/alpha.md", "red/beta.md"]),
        ({"teams": "none"}, []),
        ({"year": 2024}, ["red/alpha.md", "red/beta.md"]),  # a scalar and a list of numbers, compared as text
        ({"year": "2023"}, ["red/beta.md"]),
        ({"teams": "ops", "owner": "bruno"}, ["red/beta.md"]),  # every key must hold
        ({"owner": "ana"}, ["red/alpha.md", "red/gamma.md"]),  # a scalar field, as before
        ({"teams": "ops", "owner": "ana"}, ["red/alpha.md"]),
    ],
)
def test_a_list_field_holds_a_value_when_one_of_its_items_is_equal_to_it(bundle: Path, where: dict[str, Any], expected: list[str]) -> None:
    wiki = build(bundle)

    assert rels(matching(wiki.tree(), where)) == expected


def test_a_list_is_not_compared_as_one_text(bundle: Path) -> None:
    wiki = build(bundle)

    assert rels(matching(wiki.tree(), {"teams": "['sales', 'ops']"})) == []
    assert rels(matching(wiki.tree(), {"teams": "sales, ops"})) == []


def test_search_and_ask_apply_it(bundle: Path) -> None:
    wiki = build(bundle)

    hits = wiki.search("pricing", k=5, where={"teams": "sales"})
    ans = wiki.ask("What is the pricing?", where={"teams": "ops"})

    assert [h.path for h in hits] == ["red/alpha.md"]
    assert sorted(ans.text.replace("[", "").replace("]", "").split()[1:]) == ["red/alpha.md", "red/beta.md"]
