# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: CROW routes every ingest with one flat Choice over all the folders.

The per-level routing survives only as the handler of "None of these", of an empty tree and of a classifier error.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import CROW, Route, Wiki, WikiConfig
from llmw2.agents import steps_crow
from llmw2.agents.base import Decision
from llmw2.agents.prompts import Prompts
from llmw2.bundle.tree import Note
from llmw2.errors import ModelError
from tests.wiki.fakes import FakeClassifier, FakeLLM

HERE, NEW, NONE = "Here", "New subfolder", "None of these"
SUMMARIZE, NAME_FOLDER, ROUTE_FALLBACK = "librarian/summarize", "librarian/name_folder", "librarian/route_fallback"
INSTRUCTIONS = (
    "Which folder of the knowledge base is the most specific one whose description covers the subject of the incoming note? "
    "Prefer a subfolder over its parent when the subfolder covers it. If none covers it, pick None of these."
)
NONE_TEXT = "No listed folder covers the subject of the note."


# -- helpers ----------------------------------------------------------------------


def draft(title: str) -> dict[str, Any]:
    return {"title": title, "summary": f"What {title} says.", "tags": [], "body": "# Overview\n\nThe facts."}


def make_wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier, **cfg: Any) -> Wiki:
    cfg = {"strict_new_folders": False, **cfg}
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", **cfg), llm=llm, classifier=classifier)
    wiki.init()
    return wiki


def add_folder(wiki: Wiki, parent: str, name: str, description: str) -> None:
    wiki.store.create_folder(wiki.store.load().find(parent), name, description)


def add_note(wiki: Wiki, folder: str, title: str) -> Note:
    return wiki.store.write_note(
        wiki.store.load().find(folder),
        title=title,
        summary=f"About {title}.",
        body="# Overview\n\nSeeded.",
        tags=[],
        source={"resource": "/raw/seed.md", "title": "Seed"},
    )


@pytest.fixture
def wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    """/alpha (with /alpha/sub) and /beta, the last one without a description."""
    w = make_wiki(bundle, llm, classifier)
    add_folder(w, "/", "alpha", "Alpha things.")
    add_folder(w, "/alpha", "sub", "Sub things.")
    add_folder(w, "/", "beta", "")
    return w


@pytest.fixture
def noted(wiki: Wiki) -> Wiki:
    """The same, with one note in the root, in /alpha, in /alpha/sub and in /beta."""
    for folder, title in (("/", "Company overview"), ("/alpha", "Alpha note"), ("/alpha/sub", "Sub note"), ("/beta", "Beta note")):
        add_note(wiki, folder, title)
    return wiki


@pytest.fixture
def flat(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    """/alpha and /beta, no subfolder: the per-level routing of this tree is the shortest."""
    w = make_wiki(bundle, llm, classifier)
    add_folder(w, "/", "alpha", "Alpha things.")
    add_folder(w, "/", "beta", "Beta things.")
    return w


def criteria(classifier: FakeClassifier, call: int) -> dict[str, str]:
    return classifier.calls[call][2]["q"]["criteria"]


def match_questions(classifier: FakeClassifier) -> str:
    return " ".join(q["instructions"] for op, _, qs in classifier.calls if op == "match" for q in qs.values())


def steps_of(result: Any, step: str) -> list[Decision]:
    return [d for d in result.decisions if d.step == step]


def not_in_root(result: Any) -> bool:
    return result.folder != "/" and "/" in result.note


# -- the prompt -----------------------------------------------------------------------


def test_prompt_route_direct_renders_the_measured_instructions_and_none_text() -> None:
    # ACT
    sections = Prompts().render_sections("classifier/route_direct")

    # ASSERT
    assert sections["instructions"] == INSTRUCTIONS
    assert sections[NONE] == NONE_TEXT


def test_the_per_level_routing_and_the_audit_helper_are_named_as_the_spec_says() -> None:
    assert callable(steps_crow._route_by_levels)
    assert callable(steps_crow.audit_route)


# -- the flat Choice ------------------------------------------------------------------------


def test_route_offers_every_folder_label_then_none_of_these_with_the_measured_texts(
    wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    classifier.add_choice("route", "/alpha", {"/alpha": 0.9, "/beta": 0.1}, 0.9)

    # ACT
    result = wiki.ingest("Revenue grew.")

    # ASSERT
    op, _, questions = classifier.calls[0]
    assert op == "route"
    assert list(questions["q"]["criteria"]) == ["/alpha", "/alpha/sub", "/beta", NONE]  # root.walk() order, root left out
    assert questions["q"]["criteria"] == {
        "/alpha": "Alpha things.",
        "/alpha/sub": "Sub things.",
        "/beta": "beta",  # no description: the name
        NONE: NONE_TEXT,
    }
    assert questions["q"]["instructions"] == INSTRUCTIONS
    assert not steps_of(result, "route_cut")  # everything fits one request


def test_route_asks_the_flat_choice_once_and_the_chosen_folder_takes_the_note(wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    probabilities = {"/alpha/sub": 0.9, "/alpha": 0.1}
    classifier.add_choice("route", "/alpha/sub", probabilities, 0.9)

    # ACT
    result = wiki.ingest("Revenue grew.")

    # ASSERT
    assert classifier.ops.count("route") == 1 and classifier.ops[0] == "route"
    assert (result.folder, result.note) == ("/alpha/sub", "alpha/sub/q3-revenue.md")
    assert not_in_root(result)
    assert steps_of(result, "route") == [Decision("route", "classifier", "/alpha/sub", 0.9, scores=probabilities)]
    assert not steps_of(result, "route_direct")
    assert llm.ops == [SUMMARIZE]


@pytest.mark.parametrize("top", [0.9, 0.6])  # tau_route itself is not below tau_route
def test_candidates_are_the_ancestors_of_the_best_folder_when_it_is_confident(
    noted: Wiki, llm: FakeLLM, classifier: FakeClassifier, top: float
) -> None:
    # ARRANGE
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    classifier.add_choice("route", "/alpha/sub", {"/alpha/sub": top, "/beta": 1 - top}, 0.9)

    # ACT
    noted.ingest("Revenue grew.")

    # ASSERT
    pool = match_questions(classifier)
    assert all(f'"{t}"' in pool for t in ("Company overview", "Alpha note", "Sub note"))
    assert '"Beta note"' not in pool


def test_candidates_add_the_ancestors_of_the_second_folder_when_the_best_is_not_confident(
    noted: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    classifier.add_choice("route", "/alpha/sub", {"/alpha/sub": 0.5, "/beta": 0.4, NONE: 0.1}, 0.3)

    # ACT
    result = noted.ingest("Revenue grew.")

    # ASSERT
    pool = match_questions(classifier)
    assert all(f'"{t}"' in pool for t in ("Company overview", "Alpha note", "Sub note", "Beta note"))
    assert len([q for op, _, qs in classifier.calls if op == "match" for q in qs]) == 4  # the root once, not twice
    assert result.folder == "/alpha/sub"  # the best one takes the note, the second only widens the pool


def test_candidates_stay_with_the_best_when_no_second_folder_is_ranked(noted: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    classifier.add_choice("route", "/alpha/sub", {"/alpha/sub": 0.5}, 0.3)

    # ACT
    noted.ingest("Revenue grew.")

    # ASSERT
    assert '"Beta note"' not in match_questions(classifier)


# -- "None of these", an empty tree, a classifier error: the per-level routing ----------------------------


def test_none_of_these_on_top_runs_the_per_level_routing(flat: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    classifier.add_choice("route", NONE, {NONE: 0.7, "/alpha": 0.3}, 0.5)
    classifier.add_choice("route", "alpha", {"alpha": 0.8, "beta": 0.15, NEW: 0.05}, 0.0)
    classifier.add_choice("route", HERE, {HERE: 0.9, NEW: 0.05, NONE: 0.05}, 0.0)

    # ACT
    result = flat.ingest("Revenue grew.")

    # ASSERT
    assert classifier.ops[:3] == ["route", "route", "route"]
    assert list(criteria(classifier, 0)) == ["/alpha", "/beta", NONE]
    assert list(criteria(classifier, 1)) == ["alpha", "beta", NEW]  # the root level of the per-level routing
    assert result.folder == "/alpha" and not_in_root(result)
    direct = steps_of(result, "route_direct")
    assert direct == [Decision("route_direct", "classifier", NONE, 0.7, scores={NONE: 0.7, "/alpha": 0.3})]
    assert [d.choice for d in steps_of(result, "route")] == ["/alpha (Here)"]


def test_none_of_these_on_top_can_open_a_new_folder(flat: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    llm.add(SUMMARIZE, draft("Acme pricing")).add(NAME_FOLDER, {"name": "pricing", "description": "Supplier prices."})
    classifier.add_choice("route", NONE, {NONE: 0.9, "/alpha": 0.1}, 0.8)
    classifier.add_choice("route", NEW, {NEW: 0.8, "alpha": 0.1, "beta": 0.1}, 0.7)

    # ACT
    result = flat.ingest("Acme raised prices.")

    # ASSERT
    assert (result.created_folders, result.note) == (["/pricing"], "pricing/acme-pricing.md")
    assert llm.ops == [SUMMARIZE, NAME_FOLDER]


def test_none_of_these_then_a_low_per_level_score_falls_back_to_the_llm(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    wiki = make_wiki(bundle, llm, classifier)
    add_folder(wiki, "/", "alpha", "Alpha things.")
    llm.add(SUMMARIZE, draft("Q3 revenue")).add(ROUTE_FALLBACK, {"action": "select", "folder": "/alpha"})
    classifier.add_choice("route", NONE, {NONE: 0.9, "/alpha": 0.1}, 0.8)
    classifier.add_choice("route", "alpha", {"alpha": 0.45, NEW: 0.35}, 0.3)
    classifier.add_choice("route", HERE, {HERE: 0.4, NEW: 0.3, NONE: 0.3}, 0.9)

    # ACT
    result = wiki.ingest("Revenue grew.")

    # ASSERT
    assert llm.ops == [SUMMARIZE, ROUTE_FALLBACK]
    assert result.folder == "/alpha"
    assert steps_of(result, "route_direct")[0].choice == NONE
    assert any(d.step == "route" and d.fallback and d.decider == "llm" for d in result.decisions)


def fail_once(classifier: FakeClassifier) -> list[int]:
    """The first request raises a ModelError; the following ones are answered by the fake."""
    calls = [0]
    real = classifier.ask

    def ask(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        if calls[0] == 1:
            raise ModelError("classifier unreachable")
        return real(*args, **kwargs)

    classifier.ask = ask  # type: ignore[method-assign]
    return calls


def test_classifier_error_on_the_flat_choice_runs_the_per_level_routing(flat: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    calls = fail_once(classifier)
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    classifier.add_choice("route", "alpha", {"alpha": 0.8, "beta": 0.15, NEW: 0.05}, 0.0)
    classifier.add_choice("route", HERE, {HERE: 0.9, NEW: 0.05, NONE: 0.05}, 0.0)

    # ACT
    result = flat.ingest("Revenue grew.")

    # ASSERT
    assert calls[0] >= 3
    assert result.folder == "/alpha"
    assert steps_of(result, "route_direct") == [Decision("route_direct", "classifier", "error", None, fallback=True)]
    assert steps_of(result, "route_step")  # the per-level routing's own decisions follow


def test_empty_tree_runs_the_per_level_routing_without_a_flat_choice(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    llm.add(SUMMARIZE, draft("Acme pricing")).add(NAME_FOLDER, {"name": "pricing", "description": "Supplier prices."})

    # ACT
    result = crow_wiki.ingest("Acme raised prices.")

    # ASSERT
    assert classifier.ops == []
    assert (result.folder, result.note) == ("/pricing", "pricing/acme-pricing.md")
    assert not steps_of(result, "route_direct")


# -- capacity ------------------------------------------------------------------------------

FOLDERS = {
    "astronomy": "Telescopes, stars and the night sky.",
    "gardening": "Roses, tomatoes and compost in the garden.",
    "plumbing": "Pipes, taps and boilers in the house.",
    "sailing": "Boats, knots and harbours.",
    "violins": "Strings, bows and luthiers.",
}
PLUMBING_TEXT = "The boiler leaks and two taps drip: the pipes in the house need a plumber."


def over_capacity(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Wiki, FakeLLM, FakeClassifier]:
    from llmw2.models.usage import UsageTracker

    tracker = UsageTracker()
    llm, classifier = FakeLLM(tracker), FakeClassifier(tracker)
    wiki = make_wiki(bundle, llm, classifier)
    for name, description in FOLDERS.items():
        add_folder(wiki, "/", name, description)
    monkeypatch.setattr(steps_crow, "MAX_OPTIONS", 4)  # three folders and None of these
    llm.add(SUMMARIZE, {"title": "Leaking boiler", "summary": "Repairs.", "tags": [], "body": PLUMBING_TEXT})
    classifier.add_choice("route", "/plumbing", {"/plumbing": 0.9, NONE: 0.1}, 0.9)
    return wiki, llm, classifier


def test_over_capacity_the_best_bm25_folders_are_offered_and_none_of_these_stays_last(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARRANGE
    wiki, _, classifier = over_capacity(bundle, monkeypatch)

    # ACT
    result = wiki.ingest(PLUMBING_TEXT)

    # ASSERT
    options = list(criteria(classifier, 0))
    assert len(options) == 4 and options[0] == "/plumbing" and options[-1] == NONE
    assert criteria(classifier, 0)[NONE] == NONE_TEXT
    assert result.folder == "/plumbing"
    assert steps_of(result, "route_cut")


def test_over_capacity_the_cut_is_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # ARRANGE
    first = over_capacity(tmp_path / "one", monkeypatch)
    second = over_capacity(tmp_path / "two", monkeypatch)

    # ACT
    first[0].ingest(PLUMBING_TEXT)
    second[0].ingest(PLUMBING_TEXT)

    # ASSERT
    assert list(criteria(first[2], 0)) == list(criteria(second[2], 0))


# -- no note in the root, a custom route ---------------------------------------------------------


def test_no_note_is_filed_in_the_root_when_nothing_covers_it_and_the_llm_picks_the_root(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    wiki = make_wiki(bundle, llm, classifier)
    add_folder(wiki, "/", "alpha", "Alpha things.")
    llm.add(SUMMARIZE, draft("Q3 revenue")).add(ROUTE_FALLBACK, {"action": "select", "folder": "/"})
    llm.add(NAME_FOLDER, {"name": "revenue", "description": "Revenue reports."})
    classifier.add_choice("route", NONE, {NONE: 0.9, "/alpha": 0.1}, 0.8)
    classifier.add_choice("route", "alpha", {"alpha": 0.45, NEW: 0.35}, 0.3)
    classifier.add_choice("route", HERE, {HERE: 0.4, NEW: 0.3, NONE: 0.3}, 0.9)

    # ACT
    result = wiki.ingest("Revenue grew.")

    # ASSERT
    assert not_in_root(result)


def test_a_custom_route_step_still_works_in_crow(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    def my_route(ctx: Any, root: Any, cand: Any) -> Route:
        folder = root.find("/beta")
        return Route(folder, False, folder.ancestors())

    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier, steps=replace(CROW, name="custom", route=my_route))
    wiki.init()
    add_folder(wiki, "/", "alpha", "Alpha things.")
    add_folder(wiki, "/", "beta", "Beta things.")
    llm.add(SUMMARIZE, draft("Q3 revenue"))

    # ACT
    result = wiki.ingest("Revenue grew.")

    # ASSERT
    assert (result.folder, result.note) == ("/beta", "beta/q3-revenue.md")
    assert "route" not in classifier.ops
