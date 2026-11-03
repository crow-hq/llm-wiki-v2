# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: the prompt that names a new folder carries no folder name a small writer could copy."""

from __future__ import annotations

import pytest

from llmw2 import Wiki
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_librarian_classic import NAME_FOLDER, ROUTE, SUMMARIZE, add_folder, add_note, draft

COPYABLE = ("engineering", "finance", "customers")
NAMING = {"name": "harbour", "description": "Ships and tides."}


def naming_prompt(llm: FakeLLM) -> str:
    """Every message the writer got in the call that names a folder."""
    [messages] = [m for op, m in llm.calls if op == NAME_FOLDER]
    return "\n".join(m["content"] for m in messages)


def root_rule(llm: FakeLLM) -> str:
    """The lines of the naming prompt about the root: there an example name is copied as the name of the area.

    The shared folder rules keep their synonym examples ("clients" and "customers"): they teach reuse, not names."""
    return "\n".join(line for line in naming_prompt(llm).lower().splitlines() if "root" in line)


def file_at_root(wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(SUMMARIZE, draft("Tide tables")).add(ROUTE, {"action": "new"}).add(NAME_FOLDER, NAMING)
    wiki.ingest("Tide tables for the harbour.", title="Harbour memo")


def file_in_a_subfolder(wiki: Wiki, llm: FakeLLM) -> None:
    add_folder(wiki, "/", "harbour", "Ships and tides.")
    add_note(wiki, "/harbour", "Moorings")
    llm.add(SUMMARIZE, draft("Tide tables")).add(ROUTE, {"action": "descend", "subfolder": "harbour"}, {"action": "new"})
    llm.add(NAME_FOLDER, {"name": "tides", "description": "Tide tables."})
    llm.add("librarian/find_match", {"match": None}).add("librarian/relate", {"related": []})
    wiki.ingest("Tide tables for the harbour.", title="Harbour memo")


@pytest.mark.parametrize("word", COPYABLE)
def test_the_prompt_that_names_a_root_folder_has_no_copyable_example(classic_wiki: Wiki, llm: FakeLLM, word: str) -> None:
    file_at_root(classic_wiki, llm)

    assert word not in root_rule(llm)


@pytest.mark.parametrize("word", COPYABLE)
def test_the_prompt_that_names_a_subfolder_has_no_copyable_example(classic_wiki: Wiki, llm: FakeLLM, word: str) -> None:
    file_in_a_subfolder(classic_wiki, llm)

    assert word not in root_rule(llm)


def test_the_root_rule_still_asks_for_the_broad_area_not_the_specific_topic(classic_wiki: Wiki, llm: FakeLLM) -> None:
    file_at_root(classic_wiki, llm)

    prompt = naming_prompt(llm).lower()

    assert "broad" in prompt and "specific" in prompt


def test_naming_a_root_folder_still_files_the_note(classic_wiki: Wiki, llm: FakeLLM) -> None:
    file_at_root(classic_wiki, llm)

    assert [f.label for f in classic_wiki.tree().walk()][1:] == ["/harbour"]
