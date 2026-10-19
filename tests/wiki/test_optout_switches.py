# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from spec-optout-2026-10-09.md: `relate_rare_values` and `answer_conflicts`, the switches back to 0.3.0."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from llmw2 import CLASSIC, Wiki, WikiConfig
from llmw2.agents.conflicts import CONFLICT_HINT
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import decision_hash
from llmw2.config import ClassifierConfig
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_rare_value_links import CODE, HERE, LEVELS, SUMMARIZE, draft, relate_questions, seed
from tests.wiki.test_researcher_crow import ANSWER, add_note
from tests.wiki.test_researcher_crow import answer_prompt as crow_answer_prompt

CONFLICT_OP = "ask/conflict"
ASKED = "How is revenue booked?"
FIELDS = ("relate_rare_values", "answer_conflicts")
ENVS = {"relate_rare_values": "OKF_RELATE_RARE_VALUES", "answer_conflicts": "OKF_ANSWER_CONFLICTS"}
TRUE_VALUES = ["1", "TRUE", "yes", "On"]  # every word once, cases mixed: the parser is shared by the switches
FALSE_VALUES = ["0", "False", "NO", "off"]


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {"OKF_BUNDLE": str(tmp_path / "wiki"), **extra}


# -- config ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("field", FIELDS)
def test_switch_defaults_to_true(tmp_path: Path, field: str) -> None:
    assert getattr(WikiConfig(bundle=tmp_path), field) is True
    assert getattr(WikiConfig.from_env(env_for(tmp_path)), field) is True


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", TRUE_VALUES)
def test_env_true_variants(tmp_path: Path, field: str, value: str) -> None:
    assert getattr(WikiConfig.from_env(env_for(tmp_path, **{ENVS[field]: value})), field) is True


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", FALSE_VALUES)
def test_env_false_variants(tmp_path: Path, field: str, value: str) -> None:
    assert getattr(WikiConfig.from_env(env_for(tmp_path, **{ENVS[field]: value})), field) is False


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", ["maybe", "2"])
def test_env_invalid_value_is_rejected(tmp_path: Path, field: str, value: str) -> None:
    with pytest.raises(ValueError, match=f"(?i){field}"):
        WikiConfig.from_env(env_for(tmp_path, **{ENVS[field]: value}))


def test_the_two_switches_are_independent(tmp_path: Path) -> None:
    cfg = WikiConfig.from_env(env_for(tmp_path, OKF_RELATE_RARE_VALUES="false"))

    assert (cfg.relate_rare_values, cfg.answer_conflicts) == (False, True)


def test_a_switch_set_in_the_base_settings_is_read(tmp_path: Path) -> None:
    cfg = WikiConfig.from_env(env_for(tmp_path), base={"answer_conflicts": False})

    assert (cfg.relate_rare_values, cfg.answer_conflicts) == (True, False)


# -- relate_rare_values ---------------------------------------------------------------------------------------


def crow_ingest(bundle: Path, llm: FakeLLM, classifier: FakeClassifier, **switches: bool) -> Any:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", **switches), llm=llm, classifier=classifier, steps=LEVELS)
    wiki.init()
    seed(wiki)
    llm.add(SUMMARIZE, draft("Q3 revenue", f"# Overview\n\nInvoice {CODE} paid."))
    classifier.add_choice("route", "alpha", {"alpha": 0.9, "beta": 0.05, "New subfolder": 0.05}, 0.9)
    classifier.add_choice("route", HERE, {HERE: 0.9, "New subfolder": 0.05, "None of these": 0.05}, 0.9)
    classifier.noul_scores["relate"] = {'"Beta ledger"': 0.9}
    return wiki.ingest("Revenue grew.")


def test_relate_rare_values_true_offers_the_distant_note(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    result = crow_ingest(bundle, llm, classifier, relate_rare_values=True)

    assert any('"Beta ledger"' in q for q in relate_questions(classifier))
    assert "beta/beta-ledger.md" in result.related
    assert any(d.step == "relate_candidates" for d in result.decisions)


def test_relate_rare_values_false_does_not_offer_the_distant_note(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    result = crow_ingest(bundle, llm, classifier, relate_rare_values=False)

    assert not any('"Beta ledger"' in q for q in relate_questions(classifier))
    assert "beta/beta-ledger.md" not in result.related


def test_relate_rare_values_false_records_no_relate_candidates_decision(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    result = crow_ingest(bundle, llm, classifier, relate_rare_values=False)

    assert all(d.step != "relate_candidates" for d in result.decisions)


def test_answer_conflicts_false_does_not_change_the_rare_candidates(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    result = crow_ingest(bundle, llm, classifier, answer_conflicts=False)

    assert "beta/beta-ledger.md" in result.related
    assert any(d.step == "relate_candidates" for d in result.decisions)


# -- answer_conflicts -----------------------------------------------------------------------------------------


def two_note_wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier, **switches: bool) -> Wiki:
    """Overview at the root and Revenue policy in /finance: retrieval reads both, the conflict check says 0.9."""
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", **switches), llm=llm, classifier=classifier, steps=LEVELS)
    wiki.init()
    root = wiki.store.load()
    add_note(wiki, root, "Overview", "We sell widgets.")
    finance, _ = wiki.store.create_folder(root, "finance", "Everything about finance.")
    add_note(wiki, finance, "Revenue policy", "Revenue policy in detail.")
    classifier.noul_scores["retrieve_folder"] = {'folder "finance"': 0.9}
    classifier.noul_scores["retrieve_note"] = {'note titled "Overview"': 0.9, 'note titled "Revenue policy"': 0.9}
    classifier.noul_scores[CONFLICT_OP] = lambda text: 0.9
    llm.add(ANSWER, "Answer.")
    return wiki


def test_answer_conflicts_true_makes_the_call_and_adds_the_hint(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = two_note_wiki(bundle, llm, classifier, answer_conflicts=True)

    answer = wiki.ask(ASKED)

    assert len(answer.notes) == 2
    assert classifier.ops.count(CONFLICT_OP) == 1
    assert CONFLICT_HINT in crow_answer_prompt(llm)


def test_answer_conflicts_false_makes_no_call_and_adds_no_hint_with_two_notes_read(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = two_note_wiki(bundle, llm, classifier, answer_conflicts=False)

    answer = wiki.ask(ASKED)

    assert len(answer.notes) == 2
    assert CONFLICT_OP not in classifier.ops
    prompt = crow_answer_prompt(llm)
    assert CONFLICT_HINT not in prompt
    assert ASKED in prompt


def test_relate_rare_values_false_does_not_change_the_answer(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = two_note_wiki(bundle, llm, classifier, relate_rare_values=False)

    wiki.ask(ASKED)

    assert classifier.ops.count(CONFLICT_OP) == 1
    assert CONFLICT_HINT in crow_answer_prompt(llm)


# -- decision_hash --------------------------------------------------------------------------------------------


def cfg_of(**switches: bool) -> WikiConfig:
    return WikiConfig(
        bundle=Path("wiki"),
        mode="classic",
        classifier=ClassifierConfig(model="fake/jev", api_key="fake-key"),
        **switches,
    )


def hash_of(cfg: WikiConfig) -> str:
    return decision_hash(CLASSIC, cfg, Prompts())


def test_hash_with_both_true_equals_the_hash_of_a_config_that_does_not_set_them() -> None:
    assert hash_of(cfg_of(relate_rare_values=True, answer_conflicts=True)) == hash_of(cfg_of())


@pytest.mark.parametrize("field", FIELDS)
def test_hash_is_different_when_a_switch_is_false(field: str) -> None:
    assert hash_of(cfg_of(**{field: False})) != hash_of(cfg_of())


def test_hash_is_different_for_each_switch_and_for_both() -> None:
    cfgs = ({"relate_rare_values": False}, {"answer_conflicts": False}, dict.fromkeys(FIELDS, False))

    assert len({hash_of(cfg_of(**s)) for s in cfgs}) == 3


def test_hash_with_a_false_switch_is_stable() -> None:
    assert hash_of(cfg_of(answer_conflicts=False)) == hash_of(cfg_of(answer_conflicts=False))
