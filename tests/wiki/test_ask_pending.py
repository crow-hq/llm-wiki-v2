# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki.ask` in CROW mode reads what `remember` keeps before it is filed, as `search` finds it (`pending:<id>`), and cites it; a purge
takes it away from the next answer; in classic mode the answer navigates the folders and does not see it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki
from llmw2.agents.researcher import Researcher
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_researcher_classic import NAVIGATE
from tests.wiki.test_researcher_classic import make_wiki as classic_wiki
from tests.wiki.test_researcher_crow import ANSWER, answer_prompt, make_wiki

ZEBRA = "topic: zebra-1\nThe zebra of the savanna keeps code ZB-7781."
ASKED = "Which code does the zebra keep?"


def queued(wiki: Wiki) -> str:
    wiki._stop_filer()  # nobody files: the entry stays in the journal
    return f"pending:{wiki.remember(ZEBRA, key='zebra').id}"


def test_an_answer_reads_and_cites_an_entry_not_filed_yet(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    path = queued(wiki)
    llm.add(ANSWER, f"The zebra keeps ZB-7781 [{path}].")

    answer = wiki.ask(ASKED)

    assert path in answer.notes and answer.citations == [path]
    assert answer.titles == {path: "topic: zebra-1"}
    assert "ZB-7781" in answer_prompt(llm)  # the entry's text was read
    assert wiki.read_note(path).body == ZEBRA
    wiki.close()


def test_an_entry_purged_between_two_questions_is_neither_read_nor_cited(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    path = queued(wiki)
    llm.add(ANSWER, f"ZB-7781 [{path}].")
    assert wiki.ask(ASKED).citations == [path]
    before = len(llm.calls)

    assert wiki.purge("zebra") is not None
    llm.add(NAVIGATE, {"select": []})  # the search finds nothing now: the answer navigates the folders, and finds nothing either
    answer = wiki.ask(ASKED)

    assert path not in answer.notes and answer.citations == []
    assert all("ZB-7781" not in msgs[-1]["content"] for _, msgs in llm.calls[before:])  # nothing of it read again
    wiki.close()


def test_in_classic_mode_the_answer_does_not_see_the_entries(bundle: Path, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = classic_wiki(bundle, llm)
    path = queued(wiki)
    given: list[int] = []
    real = Researcher.ask

    def ask(self: Researcher, question: str, where: Any = None, pending: Any = ()) -> Any:
        given.append(len(pending))
        return real(self, question, where, pending)

    monkeypatch.setattr(Researcher, "ask", ask)
    llm.add(NAVIGATE, {"select": [path, "overview.md"]}).add(ANSWER, f"ZB-7781 [{path}] [overview.md].")

    answer = wiki.ask(ASKED)

    assert answer.notes == ["overview.md"] and answer.citations == ["overview.md"]
    assert given == [0]  # not given at all: the navigation of the folders would not see them anyway
    assert all("ZB-7781" not in msgs[-1]["content"] for _, msgs in llm.calls)
    wiki.close()


def test_an_answer_reads_an_entry_as_long_as_a_note_may_be(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    """A long entry is read as a note is (`effective_note_chars`): the prompt of the answer stays within what a call reads."""
    wiki = make_wiki(bundle, llm, classifier)
    wiki._stop_filer()
    body = "topic: zebra-1\nThe zebra keeps code ZB-7781. " + ("filler words about the savanna and its animals. " * 4000)
    path = f"pending:{wiki.remember(body, key='big').id}"
    llm.add(ANSWER, f"ZB-7781 [{path}].")

    answer = wiki.ask(ASKED)

    assert answer.citations == [path]
    assert len(answer_prompt(llm)) < wiki.cfg.effective_read_chars < len(body)
    assert wiki.read_note(path).body == body  # `read_note` gives it whole
    wiki.close()
