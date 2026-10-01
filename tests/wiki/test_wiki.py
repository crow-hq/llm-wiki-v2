# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki` end to end with scripted models: ingest, ask, tree, graph, check, usage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents.librarian import IngestResult
from llmw2.agents.steps import CLASSIC, CROW
from llmw2.config import ClassifierConfig
from llmw2.models.classifier import Classifier
from tests.wiki.fakes import FakeClassifier, FakeLLM

REVENUE, PRICING = "finance/revenue-recognition.md", "finance/pricing-tiers.md"


def draft(title: str, summary: str) -> dict[str, Any]:
    """A `librarian/summarize` or `librarian/merge` reply."""
    return {"title": title, "summary": summary, "tags": ["finance"], "body": f"All about {title.lower()}."}


def ingest_classic(wiki: Wiki, llm: FakeLLM) -> list[IngestResult]:
    """New folder + note, a second note linked to it, then a merge into the first."""
    llm.add("librarian/summarize", draft("Revenue recognition", "When revenue is booked."))
    llm.add("librarian/route", {"action": "new"})
    llm.add("librarian/name_folder", {"name": "Finance", "description": "Money matters"})
    first = wiki.ingest("Revenue is booked on delivery.", title="Revenue memo")

    llm.add("librarian/summarize", draft("Pricing tiers", "The three price tiers."))
    llm.add("librarian/route", {"action": "descend", "subfolder": "finance"}, {"action": "here"})
    llm.add("librarian/find_match", {"match": None})
    llm.add("librarian/relate", {"related": [REVENUE]})
    second = wiki.ingest("Basic, Pro and Enterprise.")

    llm.add("librarian/summarize", draft("Revenue update", "Subscriptions are booked monthly."))
    llm.add("librarian/route", {"action": "descend", "subfolder": "finance"}, {"action": "here"})
    llm.add("librarian/find_match", {"match": REVENUE})
    llm.add("librarian/consolidate", {"decision": "modify"})
    llm.add("librarian/merge", draft("Revenue recognition", "When revenue is booked, monthly for subscriptions."))
    llm.add("librarian/relate", {"related": []})
    third = wiki.ingest("Subscriptions are recognised monthly.")
    return [first, second, third]


def ingest_crow(wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> list[IngestResult]:
    """The same three ingests, with the classifier taking every decision."""
    llm.add("librarian/summarize", draft("Revenue recognition", "When revenue is booked."))
    # an empty wiki: the root holds no notes, so a new folder is the one way and the classifier is not asked
    llm.add("librarian/name_folder", {"name": "Finance", "description": "Money matters"})
    first = wiki.ingest("Revenue is booked on delivery.")

    llm.add("librarian/summarize", draft("Pricing tiers", "The three price tiers."))
    classifier.add_choice("route", "finance", {"finance": 0.9, "New subfolder": 0.1}, 0.9)
    classifier.add_choice("route", "Here", {"Here": 0.8, "None of these": 0.2}, 0.8)
    classifier.noul_scores.update(match={"Revenue recognition": 0.1}, relate={"Revenue recognition": 0.9})
    second = wiki.ingest("Basic, Pro and Enterprise.")

    llm.add("librarian/summarize", draft("Revenue update", "Subscriptions are booked monthly."))
    classifier.add_choice("route", "finance", {"finance": 0.9, "New subfolder": 0.1}, 0.9)
    classifier.add_choice("route", "Here", {"Here": 0.8, "None of these": 0.2}, 0.8)
    classifier.noul_scores.update(match={"Revenue recognition": 0.9}, relate={})
    classifier.add_choice("consolidate", "Modify", {"Modify": 0.9, "New note": 0.1}, 0.9)
    llm.add("librarian/merge", draft("Revenue recognition", "When revenue is booked, monthly for subscriptions."))
    third = wiki.ingest("Subscriptions are recognised monthly.")
    return [first, second, third]


def unused_replies(llm: FakeLLM) -> dict[str, list[Any]]:
    return {op: r for op, r in llm.replies.items() if r}


# -- end to end ------------------------------------------------------------------


def test_classic_ingests_create_link_and_merge(classic_wiki: Wiki, llm: FakeLLM) -> None:
    results = ingest_classic(classic_wiki, llm)

    assert [(r.action, r.note) for r in results] == [("created", REVENUE), ("created", PRICING), ("merged", REVENUE)]
    assert results[0].created_folders == ["/finance"]
    assert results[1].related == [REVENUE]
    assert unused_replies(llm) == {}
    assert classic_wiki.check() == []


def test_crow_ingests_create_link_and_merge(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    results = ingest_crow(crow_wiki, llm, classifier)

    assert [(r.action, r.note) for r in results] == [("created", REVENUE), ("created", PRICING), ("merged", REVENUE)]
    assert results[1].related == [REVENUE]
    assert unused_replies(llm) == {}
    assert classifier.choices == {"route": [], "consolidate": []}
    assert crow_wiki.check() == []


# -- usage -----------------------------------------------------------------------------


def test_each_result_counts_only_its_own_calls(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    results = ingest_crow(crow_wiki, llm, classifier)
    classifier.noul_scores.update(retrieve_folder={"finance": 0.9}, retrieve_note={"Pricing tiers": 0.9})
    llm.add("researcher/answer", "Three tiers.")
    answer = crow_wiki.ask("What are the price tiers?")

    # (llm calls, classifier calls): summarize+name_folder | none (an empty wiki: one way); summarize | route x2, match, relate;
    # summarize+merge | route x2, match, consolidate, relate; answer | retrieve_folder, retrieve_note.
    per_op = [(r.usage.llm.calls, r.usage.classifier.calls) for r in [*results, answer]]
    assert per_op == [(2, 0), (1, 4), (2, 5), (1, 2)]
    assert answer.usage.llm.input_tokens == 100 and answer.usage.classifier.input_tokens == 100


def test_wiki_usage_accumulates_across_operations(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    before = crow_wiki.usage
    ingest_crow(crow_wiki, llm, classifier)

    total = crow_wiki.usage

    assert (before.llm.calls, before.classifier.calls) == (0, 0)  # a snapshot, not a live view
    assert (total.llm.calls, total.classifier.calls) == (len(llm.calls), len(classifier.calls)) == (5, 9)
    assert total.by_model["llm:fake/llm"].total_tokens == 5 * 110
    assert total.by_model["classifier:fake/jev"].input_tokens == 9 * 50


# -- construction ------------------------------------------------------------------


def test_crow_mode_without_a_classifier_builds_one_from_config(bundle: Path, llm: FakeLLM) -> None:
    clf = ClassifierConfig(base_url="https://classifier.invalid/v1/", model="typesafe/jev-test", api_key="sk-test")

    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", classifier=clf), llm=llm)

    assert type(wiki.classifier) is Classifier
    assert (wiki.classifier.base_url, wiki.classifier.model) == ("https://classifier.invalid/v1", "typesafe/jev-test")
    assert wiki.classifier.api_key == "sk-test"
    assert wiki.classifier.usage is wiki.tracker
    assert wiki.librarian().steps is CROW and wiki.researcher().steps is CROW
    assert wiki.usage.classifier.calls == 0


def test_close_when_used_as_context_manager_then_closes_the_llm_and_the_classifier(bundle: Path) -> None:
    # ARRANGE
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"))

    # ACT
    with wiki:
        pass

    # ASSERT
    assert wiki.classifier is not None
    assert wiki.llm.client.is_closed and wiki.classifier.client.is_closed


def test_classic_mode_builds_no_classifier(bundle: Path, llm: FakeLLM) -> None:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm)

    assert wiki.classifier is None
    assert wiki.librarian().steps is CLASSIC and wiki.researcher().steps is CLASSIC


def test_from_env_reads_okf_variables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OKF_BUNDLE", str(tmp_path / "env-wiki"))
    monkeypatch.setenv("OKF_MODE", "crow")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")

    wiki = Wiki.from_env()

    assert wiki.cfg.bundle == tmp_path / "env-wiki"
    assert wiki.cfg.mode == "crow"
    assert isinstance(wiki.classifier, Classifier) and wiki.classifier.api_key == "sk-env"


# -- input and bundle edge cases ---------------------------------------------------------


def test_ingest_of_whitespace_raises_without_calling_the_llm(classic_wiki: Wiki, llm: FakeLLM) -> None:
    with pytest.raises(ValueError, match="empty"):
        classic_wiki.ingest("  \n\t ")
    assert llm.calls == []


def test_ask_of_an_empty_question_raises(classic_wiki: Wiki, llm: FakeLLM) -> None:
    with pytest.raises(ValueError, match="empty question"):
        classic_wiki.ask("   ")
    assert llm.calls == []


def test_ingest_initialises_a_missing_bundle(bundle: Path, llm: FakeLLM) -> None:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm)  # no init()
    llm.add("librarian/summarize", draft("Revenue recognition", "When revenue is booked."))
    llm.add("librarian/route", {"action": "new"}).add("librarian/name_folder", {"name": "Finance", "description": "Money matters"})

    result = wiki.ingest("Revenue is booked on delivery.")

    assert (bundle / "index.md").read_text(encoding="utf-8").startswith('---\nokf_version: "0.2"\n---\n')
    assert (bundle / "log.md").is_file() and (bundle / "raw").is_dir()
    assert (bundle / result.note).is_file()
    assert wiki.check() == []
