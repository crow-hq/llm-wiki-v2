# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Routing votes: a borderline Choice is asked `votes` times and the average decides; a path close to tau_path re-runs once."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from llmw2 import Wiki, WikiConfig
from llmw2.agents.base import Decision
from llmw2.agents.librarian import IngestResult
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import CROW, decision_hash
from llmw2.config import CrowConfig
from llmw2.settings import FIELDS
from tests.wiki.fakes import FakeClassifier, FakeLLM

HERE, NEW, NONE = "Here", "New subfolder", "None of these"
SUMMARIZE, ROUTE_FALLBACK = "librarian/summarize", "librarian/route_fallback"

# Defaults the tests lean on: tau_route 0.6, tau_path 0.5, vote_band 0.05.
ROOT_CLEAR = {"alpha": 0.8, "beta": 0.1, NEW: 0.1}  # a root answer that follows /alpha, sure of it
ALPHA_CLEAR = {HERE: 0.9, NEW: 0.05, NONE: 0.05}


# -- helpers ----------------------------------------------------------------------


def make_wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier, **crow: Any) -> Wiki:
    """/alpha and /beta; beam 1 unless said otherwise."""
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", crow=CrowConfig(**{"beam": 1, **crow})), llm=llm, classifier=classifier)
    wiki.init()
    root = wiki.store.load()
    wiki.store.create_folder(root, "alpha", "Alpha things.")
    wiki.store.create_folder(root, "beta", "Beta things.")
    llm.add(SUMMARIZE, {"title": "Q3 revenue", "summary": "Revenue.", "tags": [], "body": "# Overview\n\nFacts."})
    return wiki


def answer(classifier: FakeClassifier, probabilities: dict[str, float], confidence: float, *, times: int = 1) -> None:
    """Script `times` equal routing answers (the choice is the most probable option)."""
    for _ in range(times):
        classifier.add_choice("route", max(probabilities, key=lambda o: probabilities[o]), probabilities, confidence)


def steps(result: IngestResult) -> list[str]:
    return [d.choice for d in result.decisions if d.step == "route_step"]


def final(result: IngestResult) -> Decision:
    return next(d for d in result.decisions if d.step == "route")


def asked(classifier: FakeClassifier, folder: str) -> int:
    """Routing calls made at `folder` ("root" or "alpha"): only below the root there is a None of these option."""
    return sum(1 for op, _, qs in classifier.calls if op == "route" and (NONE in qs["q"]["criteria"]) == (folder == "alpha"))


# -- config -----------------------------------------------------------------------


def test_config_defaults_when_nothing_is_set_then_votes_are_off() -> None:
    crow = CrowConfig()

    assert (crow.votes, crow.vote_band) == (1, 0.05)


@pytest.mark.parametrize(
    ("variable", "value", "field", "expected"),
    [("OKF_VOTES", "3", "votes", 3), ("OKF_VOTE_BAND", "0.1", "vote_band", 0.1)],
)
def test_from_env_when_a_vote_variable_is_set_then_its_field_takes_it(
    tmp_path: Path, variable: str, value: str, field: str, expected: object
) -> None:
    config = WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), variable: value})

    assert getattr(config.crow, field) == expected


@pytest.mark.parametrize(
    "crow",
    [{"votes": 0}, {"votes": 10}, {"vote_band": -0.1}, {"vote_band": 1.1}],
    ids=["votes-0", "votes-10", "band-below-0", "band-above-1"],
)
def test_config_when_a_vote_setting_is_out_of_range_then_raises(crow: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CrowConfig(**crow)


@pytest.mark.parametrize("crow", [{"votes": 1}, {"votes": 9}, {"vote_band": 0}, {"vote_band": 1}])
def test_config_when_a_vote_setting_is_at_its_limit_then_accepted(crow: dict[str, Any]) -> None:
    assert CrowConfig(**crow)


def test_settings_fields_when_listed_then_the_votes_are_among_them() -> None:
    assert FIELDS["crow_votes"][-1] == "votes" and FIELDS["crow_vote_band"][-1] == "vote_band"


def test_decision_hash_changes_with_votes_and_vote_band(tmp_path: Path) -> None:
    def digest(**crow: Any) -> str:
        return decision_hash(CROW, WikiConfig(bundle=tmp_path, mode="crow", crow=CrowConfig(**crow)), Prompts())

    hashes = {digest(), digest(votes=3), digest(votes=3, vote_band=0.1), digest(vote_band=0.1)}

    assert digest() == digest(votes=1) and len(hashes) == 4


# -- votes off --------------------------------------------------------------------


@pytest.mark.parametrize("crow", [{}, {"votes": 1, "vote_band": 0.5}], ids=["default", "explicit-off"])
def test_route_when_votes_is_1_then_a_borderline_level_is_asked_once(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier, crow: dict[str, Any]
) -> None:
    # ARRANGE
    wiki = make_wiki(bundle, llm, classifier, **crow)
    answer(classifier, {"alpha": 0.5, "beta": 0.48, NEW: 0.02}, 0.62)  # near tau_route and a near tie
    answer(classifier, ALPHA_CLEAR, 0.9)

    # ACT
    result = wiki.ingest("Revenue grew.")

    # ASSERT
    assert classifier.ops == ["route", "route"]
    assert steps(result) == ["/: alpha", "/alpha: Here"]
    assert final(result).choice == "/alpha (Here)" and result.folder == "/alpha"


# -- one level --------------------------------------------------------------------


def test_route_when_votes_is_3_and_the_level_is_clear_then_it_is_asked_once(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, votes=3)
    answer(classifier, ROOT_CLEAR, 0.9)  # far from tau_route, wide gap
    answer(classifier, ALPHA_CLEAR, 0.9)

    result = wiki.ingest("Revenue grew.")

    assert classifier.ops == ["route", "route"]
    assert steps(result) == ["/: alpha", "/alpha: Here"]


def test_route_when_confidence_is_near_tau_route_then_the_average_decides(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    wiki = make_wiki(bundle, llm, classifier, votes=3)
    answer(classifier, {"alpha": 0.7, "beta": 0.2, NEW: 0.1}, 0.62)  # alone it would pick alpha; the gap is wide
    answer(classifier, {"alpha": 0.1, "beta": 0.8, NEW: 0.1}, 0.6, times=2)
    answer(classifier, ALPHA_CLEAR, 0.9)  # the beta level, asked once

    # ACT
    result = wiki.ingest("Revenue grew.")

    # ASSERT: alpha 0.3, beta 0.6, New 0.1; confidence 0.6067 is sure enough to keep beta alone
    assert (asked(classifier, "root"), asked(classifier, "alpha")) == (3, 1)
    assert result.folder == "/beta"
    assert steps(result) == ["/: beta (3 votes)", "/beta: Here"]
    scores = next(d.scores for d in result.decisions if d.step == "route_step")
    assert scores == pytest.approx({"alpha": 0.3, "beta": 0.6, NEW: 0.1})
    assert next(d.confidence for d in result.decisions if d.step == "route_step") == pytest.approx(0.6067, abs=1e-4)
    assert final(result).confidence == pytest.approx(math.sqrt(0.6 * 0.9))


def test_route_when_the_top_two_are_nearly_tied_then_the_average_decides(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, votes=3)
    answer(classifier, {"alpha": 0.4, "beta": 0.38, NEW: 0.22}, 0.9)  # confident, but 0.02 apart
    answer(classifier, {"alpha": 0.2, "beta": 0.6, NEW: 0.2}, 0.9, times=2)
    answer(classifier, ALPHA_CLEAR, 0.9)

    result = wiki.ingest("Revenue grew.")

    assert (asked(classifier, "root"), asked(classifier, "alpha")) == (3, 1)
    assert steps(result) == ["/: beta (3 votes)", "/beta: Here"] and result.folder == "/beta"


def test_route_when_confidence_is_exactly_one_band_from_tau_route_then_it_is_not_borderline(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # tau_route 0.5 and band 0.25 are exact in binary: 0.75 - 0.5 == 0.25
    wiki = make_wiki(bundle, llm, classifier, votes=3, tau_route=0.5, vote_band=0.25, tau_path=0.1)
    answer(classifier, {"alpha": 0.7, "beta": 0.1, NEW: 0.2}, 0.75)
    answer(classifier, ALPHA_CLEAR, 0.9)

    result = wiki.ingest("Revenue grew.")

    assert classifier.ops == ["route", "route"]
    assert steps(result) == ["/: alpha", "/alpha: Here"]


def test_route_when_the_top_two_are_exactly_one_band_apart_then_it_is_not_borderline(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier, votes=3, vote_band=0.25, tau_path=0.1)
    answer(classifier, {"alpha": 0.5, "beta": 0.25, NEW: 0.25}, 0.9)  # 0.5 - 0.25 == 0.25
    answer(classifier, ALPHA_CLEAR, 0.9)

    result = wiki.ingest("Revenue grew.")

    assert classifier.ops == ["route", "route"]
    assert steps(result) == ["/: alpha", "/alpha: Here"]


def test_route_when_an_option_is_missing_from_an_answer_then_it_counts_as_zero(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier, votes=3)
    answer(classifier, {"alpha": 0.45, "beta": 0.45, NEW: 0.1}, 0.9)  # a tie: borderline
    answer(classifier, {"alpha": 0.9, NEW: 0.1}, 0.9, times=2)  # no beta
    answer(classifier, ALPHA_CLEAR, 0.9)

    result = wiki.ingest("Revenue grew.")

    scores = next(d.scores for d in result.decisions if d.step == "route_step")
    assert {o: scores.get(o, 0.0) for o in ("alpha", "beta", NEW)} == pytest.approx({"alpha": 0.75, "beta": 0.15, NEW: 0.1})


def test_route_trace_when_levels_are_averaged_then_the_text_ends_with_the_number_of_votes(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier, votes=3, beam=2)
    answer(classifier, {NEW: 0.42, "alpha": 0.40, "beta": 0.18}, 0.5, times=3)  # a near tie, unsure: two options kept
    answer(classifier, ALPHA_CLEAR, 0.9)

    result = wiki.ingest("Revenue grew.")

    assert steps(result) == [f"/: {NEW} | alpha (3 votes)", "/alpha: Here"]


# -- the path re-run --------------------------------------------------------------------

# Root asked once (clear, alpha 0.8); the alpha level once too, Here at p. The path scores sqrt(0.8 * p).
# With p = 0.34 that is 0.5215, within the band of tau_path (0.5), so with votes the beam runs again.


def alpha_level(classifier: FakeClassifier, here: float, *, times: int = 1) -> None:
    answer(classifier, {HERE: here, NEW: 0.2, NONE: 0.2}, 0.9, times=times)


def test_route_when_the_best_path_is_close_to_tau_path_then_the_beam_runs_again_averaged_and_can_fall_back(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    wiki = make_wiki(bundle, llm, classifier, votes=3)
    llm.add(ROUTE_FALLBACK, {"action": "select", "folder": "/alpha"})
    answer(classifier, ROOT_CLEAR, 0.8)
    alpha_level(classifier, 0.34)  # first pass: 0.5215, over tau_path
    answer(classifier, ROOT_CLEAR, 0.8, times=2)  # second pass: root first, then alpha
    answer(classifier, {HERE: 0.2, NEW: 0.1, NONE: 0.1}, 0.9, times=2)  # Here averages 0.2467: 0.444, under tau_path

    # ACT
    result = wiki.ingest("Revenue grew.")

    # ASSERT
    assert (asked(classifier, "root"), asked(classifier, "alpha")) == (3, 3)  # never more than votes per folder
    assert steps(result) == ["/: alpha (3 votes)", "/alpha: Here (3 votes)"]  # the final pass only
    assert final(result).decider == "llm" and final(result).fallback
    assert final(result).confidence == pytest.approx(math.sqrt(0.8 * (0.34 + 0.2 + 0.2) / 3))
    assert result.folder == "/alpha"


def test_route_when_the_re_run_moves_the_score_over_tau_path_then_the_classifier_decides(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # No fallback reply is scripted: asking the LLM would fail the test.
    wiki = make_wiki(bundle, llm, classifier, votes=3)
    answer(classifier, ROOT_CLEAR, 0.8)
    alpha_level(classifier, 0.3)  # first pass: sqrt(0.8 * 0.3) = 0.49, under tau_path
    answer(classifier, ROOT_CLEAR, 0.8, times=2)
    answer(classifier, {HERE: 0.6, NEW: 0.1, NONE: 0.1}, 0.9, times=2)  # Here averages 0.5: 0.632

    result = wiki.ingest("Revenue grew.")

    assert (asked(classifier, "root"), asked(classifier, "alpha")) == (3, 3)
    assert steps(result) == ["/: alpha (3 votes)", "/alpha: Here (3 votes)"]
    assert final(result) == Decision("route", "classifier", "/alpha (Here)", pytest.approx(math.sqrt(0.8 * 0.5)))
    assert llm.ops == [SUMMARIZE]


def test_route_when_a_level_was_already_averaged_then_the_re_run_does_not_ask_it_again(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier, votes=3)
    llm.add(ROUTE_FALLBACK, {"action": "select", "folder": "/alpha"})
    answer(classifier, ROOT_CLEAR, 0.62, times=3)  # near tau_route: averaged in the first pass
    alpha_level(classifier, 0.34)  # clear, asked once; the path is close to tau_path
    answer(classifier, {HERE: 0.2, NEW: 0.1, NONE: 0.1}, 0.9, times=2)  # only the alpha level is asked again

    result = wiki.ingest("Revenue grew.")

    assert (asked(classifier, "root"), asked(classifier, "alpha")) == (3, 3)
    assert steps(result) == ["/: alpha (3 votes)", "/alpha: Here (3 votes)"]
    assert final(result).decider == "llm"


def test_route_when_the_best_path_is_far_from_tau_path_then_there_is_no_re_run(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier, votes=3)
    answer(classifier, ROOT_CLEAR, 0.8)
    answer(classifier, ALPHA_CLEAR, 0.9)  # sqrt(0.8 * 0.9) = 0.85

    result = wiki.ingest("Revenue grew.")

    assert classifier.ops == ["route", "route"]
    assert steps(result) == ["/: alpha", "/alpha: Here"]


def test_route_when_every_level_of_the_best_path_was_averaged_then_there_is_no_re_run(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier, votes=3)
    answer(classifier, ROOT_CLEAR, 0.62, times=3)  # near tau_route
    answer(classifier, {HERE: 0.34, NEW: 0.3, NONE: 0.3}, 0.9, times=3)  # a near tie; Here 0.34: 0.5215, close to tau_path

    result = wiki.ingest("Revenue grew.")

    assert (asked(classifier, "root"), asked(classifier, "alpha")) == (3, 3)  # nothing more to ask
    assert steps(result) == ["/: alpha (3 votes)", "/alpha: Here (3 votes)"]
    assert final(result) == Decision("route", "classifier", "/alpha (Here)", pytest.approx(math.sqrt(0.8 * 0.34)))


# -- usage ------------------------------------------------------------------------


def test_usage_when_votes_ask_again_then_every_classifier_call_is_counted(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, votes=3)
    llm.add(ROUTE_FALLBACK, {"action": "select", "folder": "/alpha"})
    answer(classifier, ROOT_CLEAR, 0.8)
    alpha_level(classifier, 0.34)
    answer(classifier, ROOT_CLEAR, 0.8, times=2)
    answer(classifier, {HERE: 0.2, NEW: 0.1, NONE: 0.1}, 0.9, times=2)

    wiki.ingest("Revenue grew.")

    assert len(classifier.calls) == 6
    assert wiki.usage.classifier.calls == len(classifier.calls)
