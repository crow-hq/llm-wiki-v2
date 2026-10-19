# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A routing level wider than one classifier request: its subfolders ranked by BM25 against the note, as many as fit."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents import lexical, steps_crow
from llmw2.agents.steps import CROW
from tests.wiki.fakes import FakeClassifier, FakeLLM

FOLDERS = {
    "astronomy": "Telescopes, stars and the night sky.",
    "gardening": "Roses, tomatoes and compost in the garden.",
    "plumbing": "Pipes, taps and boilers in the house.",
    "sailing": "Boats, knots and harbours.",
    "violins": "Strings, bows and luthiers.",
}
TEXT = "The boiler leaks and two taps drip: the pipes in the house need a plumber."


# These tests exercise the per-level routing, which handles "None of these": the flat Choice is left out.
LEVELS = replace(CROW, name="levels", route=steps_crow._route_by_levels)


@pytest.fixture
def wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier, steps=LEVELS)
    wiki.init()
    root = wiki.store.load()
    for name, description in FOLDERS.items():
        wiki.store.create_folder(root, name, description)
    llm.add("librarian/summarize", {"title": "Leaking boiler", "summary": "Repairs.", "tags": [], "body": TEXT})
    return wiki


def offered(classifier: FakeClassifier) -> list[str]:
    """The options of the first routing Choice."""
    _, _, questions = next(c for c in classifier.calls if c[0] == "route")
    return list(questions["q"]["criteria"])


def test_route_when_the_level_fits_one_request_then_every_subfolder_is_offered(wiki: Wiki, classifier: FakeClassifier) -> None:
    # ARRANGE
    classifier.add_choice("route", "plumbing", {"plumbing": 0.9}, 0.9)
    classifier.add_choice("route", "Here", {"Here": 0.9}, 0.9)

    # ACT
    result = wiki.ingest(TEXT)

    # ASSERT
    assert offered(classifier) == [*FOLDERS, "New subfolder"]
    assert not [d for d in result.decisions if d.step == "route_cut"]
    assert result.folder == "/plumbing"


def test_route_when_the_level_exceeds_request_chars_then_the_best_bm25_subfolders_that_fit_are_offered(
    wiki: Wiki, classifier: FakeClassifier
) -> None:
    # ARRANGE
    state_and_text = len(steps_crow.routing_state(wiki.librarian().ctx, _candidate())) + 400
    classifier.request_chars = state_and_text + 2 * (len("plumbing") + 40 + steps_crow.OPTION_CHARS)  # room for about two
    classifier.add_choice("route", "plumbing", {"plumbing": 0.9}, 0.9)
    classifier.add_choice("route", "Here", {"Here": 0.9}, 0.9)

    # ACT
    result = wiki.ingest(TEXT)

    # ASSERT
    options = offered(classifier)
    assert options[0] == "plumbing"  # the subfolder that shares the note's words comes first
    assert len(options) < len(FOLDERS) + 1 and options[-1] == "New subfolder"
    cut = [d.choice for d in result.decisions if d.step == "route_cut"]
    assert cut and cut[0].startswith(f"/: {len(options) - 1} of {len(FOLDERS)} subfolders")
    assert result.folder == "/plumbing"


def test_route_when_the_level_has_more_subfolders_than_options_then_max_options_are_offered(
    wiki: Wiki, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARRANGE
    monkeypatch.setattr(steps_crow, "MAX_OPTIONS", 3)  # New subfolder and two subfolders
    classifier.add_choice("route", "plumbing", {"plumbing": 0.9}, 0.9)
    classifier.add_choice("route", "Here", {"Here": 0.9}, 0.9)

    # ACT
    wiki.ingest(TEXT)

    # ASSERT
    options = offered(classifier)
    assert len(options) == 3 and options[0] == "plumbing" and options[-1] == "New subfolder"


def test_rank_when_words_match_then_best_first_and_ties_keep_their_order() -> None:
    # ARRANGE
    docs = {"a": "nothing shared", "b": "pipes and taps", "c": "only taps", "d": "nothing either"}

    # ACT
    ranked = lexical.rank("leaking pipes and taps", docs)

    # ASSERT
    assert ranked[:2] == ["b", "c"]
    assert ranked[2:] == ["a", "d"]


def _candidate():  # type: ignore[no-untyped-def]
    from llmw2 import Candidate

    return Candidate(title="Leaking boiler", summary="Repairs.", tags=[], body=TEXT)
