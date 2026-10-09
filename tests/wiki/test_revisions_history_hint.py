# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: a historical question gets HISTORY_HINT after the question text in the answer step; otherwise it is unchanged."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2.agents.revisions import HISTORY_HINT, HISTORY_MIN
from llmw2.errors import ModelError
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_revisions_replace import (
    ANSWER,
    NOTE_TITLE,
    QUESTION,
    answer_prompt,
    folder_of,
    make_wiki,
    priced_wiki,
    retrieval,
)


def down(text: str) -> float:
    raise ModelError("down")


def test_history_hint_is_a_non_empty_string() -> None:
    assert isinstance(HISTORY_HINT, str) and HISTORY_HINT.strip()


def test_hint_follows_the_question_when_historical(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki, archived = priced_wiki(bundle, llm, classifier)
    retrieval(classifier, lambda text: 0.9)
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert archived.lstrip("/") in answer.notes
    prompt = answer_prompt(llm)
    assert f"{QUESTION}\n\n{HISTORY_HINT}" in prompt
    assert prompt.count(HISTORY_HINT) == 1
    assert prompt.index(QUESTION) < prompt.index(HISTORY_HINT)


def test_hint_present_just_above_history_min(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki, _ = priced_wiki(bundle, llm, classifier)
    retrieval(classifier, lambda text: HISTORY_MIN + 0.01)
    llm.add(ANSWER, "Answer.")

    wiki.ask(QUESTION)

    assert HISTORY_HINT in answer_prompt(llm)


@pytest.mark.parametrize("p", [HISTORY_MIN, 0.5, 0.2, 0.0])
def test_hint_absent_when_not_above_history_min(bundle: Path, llm: FakeLLM, classifier: FakeClassifier, p: float) -> None:
    wiki, archived = priced_wiki(bundle, llm, classifier)
    retrieval(classifier, lambda text: p)
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert archived.lstrip("/") not in answer.notes
    prompt = answer_prompt(llm)
    assert HISTORY_HINT not in prompt
    assert QUESTION in prompt


def test_hint_absent_when_the_classifier_fails(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki, _ = priced_wiki(bundle, llm, classifier)
    retrieval(classifier, down)
    llm.add(ANSWER, "Answer.")

    wiki.ask(QUESTION)

    prompt = answer_prompt(llm)
    assert HISTORY_HINT not in prompt
    assert QUESTION in prompt


def test_hint_absent_with_revisions_merge(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki, _ = priced_wiki(bundle, llm, classifier, revisions="merge")
    retrieval(classifier, lambda text: 0.9)
    llm.add(ANSWER, "Answer.")

    wiki.ask(QUESTION)

    prompt = answer_prompt(llm)
    assert HISTORY_HINT not in prompt
    assert QUESTION in prompt


def test_hint_absent_without_a_note_with_previous_version(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    wiki.store.write_note(
        folder_of(wiki, "/finance"),
        title=NOTE_TITLE,
        summary="About pricing.",
        body="Price is 120.",
        tags=[],
        source={"resource": "https://example.com", "title": "S"},
    )
    retrieval(classifier, lambda text: 0.9)
    llm.add(ANSWER, "Answer.")

    wiki.ask(QUESTION)

    prompt = answer_prompt(llm)
    assert HISTORY_HINT not in prompt
    assert QUESTION in prompt


def test_hint_absent_in_classic_mode(bundle: Path, llm: FakeLLM) -> None:
    wiki, _ = priced_wiki(bundle, llm, None)
    llm.add("researcher/navigate", {"open": ["finance"]}, {"select": ["finance/pricing-tiers.md"], "done": True}).add(ANSWER, "Answer.")

    wiki.ask(QUESTION)

    prompt = answer_prompt(llm)
    assert HISTORY_HINT not in prompt
    assert QUESTION in prompt
