# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: `strict_new_folders`, CROW routing opens a new subfolder only when the classifier is sure of it."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents import steps_crow
from llmw2.agents.base import Decision
from llmw2.agents.librarian import IngestResult
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import CROW, decision_hash
from tests.wiki.fakes import FakeClassifier, FakeLLM

HERE, NEW, NONE = "Here", "New subfolder", "None of these"
SUMMARIZE, NAME_FOLDER = "librarian/summarize", "librarian/name_folder"
DRAFT = {"title": "Q3 revenue", "summary": "Revenue.", "tags": [], "body": "# Overview\n\nFacts."}
NAMED = {"name": "pricing", "description": "Supplier prices."}
TRUE_VALUES = ["1", "true", "True", "yes", "YES", "on", "On"]
FALSE_VALUES = ["0", "false", "False", "no", "NO", "off", "Off"]


# -- helpers ----------------------------------------------------------------------


# These tests exercise the per-level routing, which handles "None of these": the flat Choice is left out.
LEVELS = replace(CROW, name="levels", route=steps_crow._route_by_levels)


def make_wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier, *folders: str, **cfg: Any) -> Wiki:
    """A CROW wiki (beam 2, tau_route 0.6, tau_path 0.5 by default) with the given root folders."""
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", **cfg), llm=llm, classifier=classifier, steps=LEVELS)
    wiki.init()
    for name in folders:
        wiki.store.create_folder(wiki.store.load(), name, f"{name.title()} things.")
    llm.add(SUMMARIZE, DRAFT)
    return wiki


def route(classifier: FakeClassifier, choice: str, probabilities: dict[str, float], confidence: float) -> None:
    classifier.add_choice("route", choice, probabilities, confidence)


def steps(result: IngestResult) -> list[Decision]:
    return [d for d in result.decisions if d.step == "route_step"]


def final(result: IngestResult) -> Decision:
    return next(d for d in result.decisions if d.step == "route")


def options(classifier: FakeClassifier, call: int) -> list[str]:
    return list(classifier.calls[call][2]["q"]["criteria"])


# -- config ---------------------------------------------------------------------------


def test_config_defaults_to_strict(tmp_path: Path) -> None:
    assert WikiConfig(bundle=tmp_path).strict_new_folders is True
    assert WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path)}).strict_new_folders is True


@pytest.mark.parametrize(("values", "expected"), [(TRUE_VALUES, True), (FALSE_VALUES, False)])
def test_env_accepts_the_boolean_spellings(tmp_path: Path, values: list[str], expected: bool) -> None:
    for value in values:
        cfg = WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_STRICT_NEW_FOLDERS": value})
        assert cfg.strict_new_folders is expected, value


@pytest.mark.parametrize("value", ["maybe", "2", "enabled"])
def test_env_rejects_anything_else(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError, match=r"(?i)strict_new_folders"):
        WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_STRICT_NEW_FOLDERS": value})


def test_decision_hash_changes_only_when_the_switch_is_off(tmp_path: Path) -> None:
    def digest(**cfg: Any) -> str:
        return decision_hash(CROW, WikiConfig(bundle=tmp_path, mode="crow", **cfg), Prompts())

    assert digest(strict_new_folders=True) == digest()
    assert digest(strict_new_folders=False) != digest()
    assert digest(strict_new_folders=False) == digest(strict_new_folders=False)


# -- a weak New subfolder cannot end a path ----------------------------------------------


def test_weak_new_under_a_folder_files_the_note_here_with_the_summed_probability(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier, "alpha")
    route(classifier, "alpha", {"alpha": 1.0, NEW: 0.0}, 0.9)
    route(classifier, NEW, {NEW: 0.4, HERE: 0.35, NONE: 0.25}, 0.5)  # Here 0.35 + New 0.4 = 0.75

    result = wiki.ingest("Revenue grew.")

    assert NAME_FOLDER not in llm.ops and result.created_folders == []
    assert (result.folder, result.note) == ("/alpha", "alpha/q3-revenue.md")
    assert options(classifier, 1) == [HERE, NEW, NONE]
    assert final(result).confidence == pytest.approx(math.sqrt(0.75))
    assert steps(result)[1].choice == "/alpha: Here"


def test_the_trace_keeps_the_classifiers_original_probabilities(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, "alpha")
    route(classifier, "alpha", {"alpha": 1.0, NEW: 0.0}, 0.9)
    route(classifier, NEW, {NEW: 0.4, HERE: 0.35, NONE: 0.25}, 0.5)

    result = wiki.ingest("Revenue grew.")

    assert steps(result)[1].scores == pytest.approx({NEW: 0.4, HERE: 0.35, NONE: 0.25})


def test_weak_new_at_the_root_is_dropped_and_the_rest_renormalised(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, "alpha", "beta")
    # Renormalised: alpha 0.625, beta 0.375. The confidence is max(0.5, 0.625) >= tau_route: alpha alone is kept
    # (without the adjustment 0.5 < tau_route would keep two options).
    route(classifier, "alpha", {"alpha": 0.5, "beta": 0.3, NEW: 0.2}, 0.5)
    route(classifier, HERE, {HERE: 0.9, NEW: 0.05, NONE: 0.05}, 0.9)

    result = wiki.ingest("Revenue grew.")

    assert classifier.ops == ["route", "route"]  # beta was not explored
    assert steps(result)[0].choice == "/: alpha"
    assert steps(result)[0].scores == pytest.approx({"alpha": 0.5, "beta": 0.3, NEW: 0.2})
    assert result.folder == "/alpha" and result.created_folders == []
    assert final(result).confidence == pytest.approx(math.sqrt(0.625 * 0.95))  # Here: 0.9 + weak New 0.05


def test_unsure_level_with_a_weak_new_still_keeps_beam_options_when_no_adjusted_probability_is_high(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier, "alpha", "beta")
    # Renormalised: alpha 0.4/0.9 = 0.444, beta 0.5/0.9 = 0.556: both under tau_route, confidence 0.3: two options.
    route(classifier, "beta", {"alpha": 0.4, "beta": 0.5, NEW: 0.1}, 0.3)
    route(classifier, HERE, {HERE: 0.9, NEW: 0.05, NONE: 0.05}, 0.9)
    route(classifier, HERE, {HERE: 0.6, NEW: 0.2, NONE: 0.2}, 0.9)

    result = wiki.ingest("Revenue grew.")

    assert classifier.ops == ["route", "route", "route"]
    assert steps(result)[0].choice == "/: beta | alpha"
    # beta: sqrt(0.5556 * 0.95) = 0.726; alpha: sqrt(0.4444 * 0.6) = 0.516
    assert result.folder == "/beta"
    assert final(result).confidence == pytest.approx(math.sqrt(0.5 / 0.9 * 0.95))


# -- a strong New subfolder, or the only option, behaves as before --------------------------


def test_strong_new_under_a_folder_still_creates_a_folder(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, "alpha")
    llm.add(NAME_FOLDER, NAMED)
    route(classifier, "alpha", {"alpha": 1.0, NEW: 0.0}, 0.9)
    route(classifier, NEW, {NEW: 0.7, HERE: 0.2, NONE: 0.1}, 0.7)

    result = wiki.ingest("Revenue grew.")

    assert NAME_FOLDER in llm.ops
    assert result.created_folders == ["/alpha/pricing"]
    assert final(result).confidence == pytest.approx(math.sqrt(0.7))


def test_strong_new_at_the_root_still_creates_a_folder(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, "finance")
    llm.add(NAME_FOLDER, NAMED)
    route(classifier, NEW, {"finance": 0.2, NEW: 0.8}, 0.8)

    result = wiki.ingest("Revenue grew.")

    assert result.created_folders == ["/pricing"]
    assert final(result) == Decision("route", "classifier", f"/ ({NEW})", pytest.approx(0.8))


def test_new_as_the_only_option_in_an_empty_wiki_still_creates_a_folder(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    llm.add(NAME_FOLDER, NAMED)

    result = wiki.ingest("Revenue grew.")

    assert classifier.calls == [] and result.created_folders == ["/pricing"]


# -- the switch off: 0.3.0's routing ---------------------------------------------------------


def test_switch_off_the_same_weak_new_opens_a_subfolder(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, "alpha", strict_new_folders=False)
    llm.add(NAME_FOLDER, NAMED)
    route(classifier, "alpha", {"alpha": 1.0, NEW: 0.0}, 0.9)
    route(classifier, NEW, {NEW: 0.4, HERE: 0.35, NONE: 0.25}, 0.5)  # unsure: New and Here kept; New scores higher

    result = wiki.ingest("Revenue grew.")

    assert result.created_folders == ["/alpha/pricing"]
    assert final(result).confidence == pytest.approx(math.sqrt(0.4))


def test_switch_off_no_renormalisation_at_the_root(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, "alpha", "beta", strict_new_folders=False)
    route(classifier, "alpha", {"alpha": 0.5, "beta": 0.3, NEW: 0.2}, 0.5)  # unsure: alpha and beta both explored
    route(classifier, HERE, {HERE: 0.9, NEW: 0.05, NONE: 0.05}, 0.9)
    route(classifier, HERE, {HERE: 0.9, NEW: 0.05, NONE: 0.05}, 0.9)

    result = wiki.ingest("Revenue grew.")

    assert classifier.ops == ["route", "route", "route"]
    assert result.folder == "/alpha"
    assert final(result).confidence == pytest.approx(math.sqrt(0.5 * 0.9))


# -- the prompt ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("strict", "used", "unused"),
    [(True, "classifier/route", "classifier/route_open"), (False, "classifier/route_open", "classifier/route")],
)
def test_the_routing_choice_uses_the_prompt_of_the_switch(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch, strict: bool, used: str, unused: str
) -> None:
    names: list[str] = []
    original = Prompts.render_sections

    def spy(self: Prompts, name: str, **values: Any) -> dict[str, str]:
        names.append(name)
        return original(self, name, **values)

    monkeypatch.setattr(Prompts, "render_sections", spy)
    wiki = make_wiki(bundle, llm, classifier, "alpha", strict_new_folders=strict)
    route(classifier, "alpha", {"alpha": 1.0, NEW: 0.0}, 0.9)
    route(classifier, HERE, {HERE: 0.9, NEW: 0.05, NONE: 0.05}, 0.9)

    wiki.ingest("Revenue grew.")

    assert used in names and unused not in names
