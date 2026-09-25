# Copyright 2026 Federico Cesarini, Marco Sassarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CROW Researcher (§5.2): a Noul per folder and per note picks what the LLM reads to answer."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from okf_wiki import Wiki, WikiConfig
from okf_wiki.classifier import Classifier
from okf_wiki.client import HttpModel, ModelError
from okf_wiki.config import ClassifierConfig, CrowConfig
from okf_wiki.store import Folder, Note
from tests.wiki.fakes import CLASSIFIER_TOKENS, LLM_TOKENS, FakeClassifier, FakeLLM

NAVIGATE, ANSWER = "researcher/navigate", "researcher/answer"


def add_note(wiki: Wiki, folder: Folder, title: str, body: str) -> Note:
    source = {"resource": "https://example.com", "title": title}
    summary = f"About {title.lower()}."
    return wiki.store.write_note(folder, title=title, summary=summary, body=body, tags=[], source=source)


def make_wiki(bundle: Path, llm: FakeLLM, classifier: Classifier, **crow: Any) -> Wiki:
    """
    /overview.md
    /finance/revenue-policy.md    /finance/pricing/pricing-tiers.md    /finance/tax/vat-returns.md
    /legal/nda-template.md        /legal/contracts/supplier-contracts.md
    /people/hiring-process.md     /people/onboarding/first-day.md
    """
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", crow=CrowConfig(**crow)), llm=llm, classifier=classifier)
    wiki.init()
    store, root = wiki.store, wiki.store.load()
    add_note(wiki, root, "Overview", "We sell widgets.")
    for name, title, children in [
        ("finance", "Revenue policy", [("pricing", "Pricing tiers"), ("tax", "VAT returns")]),
        ("legal", "NDA template", [("contracts", "Supplier contracts")]),
        ("people", "Hiring process", [("onboarding", "First day")]),
    ]:
        top, _ = store.create_folder(root, name, f"Everything about {name}.")
        add_note(wiki, top, title, f"{title} in detail.")
        for sub_name, sub_title in children:
            sub, _ = store.create_folder(top, sub_name, f"Everything about {sub_name}.")
            add_note(wiki, sub, sub_title, f"{sub_title} in detail.")
    return wiki


@pytest.fixture
def wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    return make_wiki(bundle, llm, classifier)


def folder(name: str) -> str:
    """Needle matching the retrieve_folder question about one folder."""
    return f'folder "{name}"'


def note(title: str) -> str:
    """Needle matching the retrieve_note question about one note."""
    return f'note titled "{title}"'


def scored(classifier: FakeClassifier, op: str) -> list[list[str]]:
    """Per classifier call of `op`: the folder names or note titles it was asked about."""
    pattern = re.compile(r'(?:folder|note titled) "([^"]+)"')
    return [[pattern.search(q["instructions"])[1] for q in qs.values()] for o, _, qs in classifier.calls if o == op]


def answer_prompt(llm: FakeLLM) -> str:
    return next(msgs[-1]["content"] for op, msgs in llm.calls if op == ANSWER)


def test_every_subfolder_of_the_explored_level_gets_one_noul(
    wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM
) -> None:
    classifier.noul_scores = {
        "retrieve_folder": {folder("finance"): 0.9, folder("people"): 0.8},
        "retrieve_note": {note("Revenue policy"): 0.9},
    }
    llm.add(ANSWER, "Answer.")

    wiki.ask("How is revenue booked?")

    # level 2 scores only the children of the folders explored at level 1: legal/contracts is skipped
    assert scored(classifier, "retrieve_folder") == [["finance", "legal", "people"], ["pricing", "tax", "onboarding"]]
    assert {state for _, state, _ in classifier.calls} == {"How is revenue booked?"}


def test_folders_at_or_below_tau_fold_are_not_explored(wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM) -> None:
    classifier.noul_scores = {
        "retrieve_folder": {folder("finance"): 0.9, folder("people"): 0.5, folder("legal"): 0.2},
        "retrieve_note": {note("Revenue policy"): 0.9},
    }
    llm.add(ANSWER, "Answer.")

    wiki.ask("How is revenue booked?")

    assert scored(classifier, "retrieve_note") == [["Overview", "Revenue policy"]]


def test_at_most_beam_folders_are_explored_per_level(wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM) -> None:
    classifier.noul_scores = {
        "retrieve_folder": {folder("finance"): 0.7, folder("legal"): 0.9, folder("people"): 0.8},
        "retrieve_note": {note("NDA template"): 0.9},
    }
    llm.add(ANSWER, "Answer.")

    wiki.ask("What do we sign?")

    # beam = 2: the two best folders (legal, people) are explored, finance is dropped although it clears tau_fold
    assert scored(classifier, "retrieve_note") == [["Overview", "NDA template", "Hiring process"]]


def test_nested_folders_are_explored_level_by_level(wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM) -> None:
    classifier.noul_scores = {
        "retrieve_folder": {folder("finance"): 0.9, folder("pricing"): 0.8},
        "retrieve_note": {note("Pricing tiers"): 0.9},
    }
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask("Which plans do we sell?")

    assert scored(classifier, "retrieve_note") == [["Overview", "Revenue policy", "Pricing tiers"]]
    assert answer.notes == ["finance/pricing/pricing-tiers.md"]


def test_root_notes_are_candidates_even_when_no_folder_clears(
    wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM
) -> None:
    classifier.noul_scores = {"retrieve_note": {note("Overview"): 0.9}}
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask("What do we do?")

    assert scored(classifier, "retrieve_note") == [["Overview"]]
    assert answer.notes == ["overview.md"]
    assert not any(d.fallback for d in answer.decisions)


def test_notes_are_ranked_across_folders_and_capped_at_k(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier, k=2)
    classifier.noul_scores = {
        "retrieve_folder": {folder("finance"): 0.9, folder("people"): 0.8},
        "retrieve_note": {note("Overview"): 0.6, note("Revenue policy"): 0.7, note("Hiring process"): 0.95},
    }
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask("How do we hire and book revenue?")

    # three notes clear tau_ret; k = 2 keeps the two most probable, most probable first
    assert answer.notes == ["people/hiring-process.md", "finance/revenue-policy.md"]


def test_notes_at_or_below_tau_ret_are_not_read(wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM) -> None:
    classifier.noul_scores = {
        "retrieve_folder": {folder("finance"): 0.9},
        "retrieve_note": {note("Overview"): 0.5, note("Revenue policy"): 0.51, note("Pricing tiers"): 0.2},
    }
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask("How is revenue booked?")

    assert answer.notes == ["finance/revenue-policy.md"]


def test_answer_reads_the_retrieved_notes_in_rank_order(wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM) -> None:
    classifier.noul_scores = {
        "retrieve_folder": {folder("finance"): 0.9, folder("people"): 0.8},
        "retrieve_note": {note("Revenue policy"): 0.7, note("Hiring process"): 0.9},
    }
    llm.add(ANSWER, "Answer.")

    wiki.ask("How do we hire and book revenue?")

    user = answer_prompt(llm)
    assert user.index("### people/hiring-process.md") < user.index("### finance/revenue-policy.md")
    assert "Hiring process in detail." in user and "Revenue policy in detail." in user
    assert "Overview" not in user
    assert llm.ops == [ANSWER]


def test_nothing_clearing_falls_back_to_llm_navigation(wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM) -> None:
    llm.add(NAVIGATE, {"select": ["overview.md"], "done": True}).add(ANSWER, "Answer.")

    answer = wiki.ask("What do we do?")

    assert classifier.ops == ["retrieve_folder", "retrieve_note"]
    assert llm.ops == [NAVIGATE, ANSWER]
    assert answer.notes == ["overview.md"]
    assert [(d.step, d.decider) for d in answer.decisions if d.fallback] == [("select", "llm")]


@pytest.mark.parametrize("failing", ["retrieve_folder", "retrieve_note"])
def test_classifier_error_falls_back_to_llm_navigation(
    wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM, failing: str
) -> None:
    def down(_: str) -> float:
        raise ModelError("classifier down")

    classifier.noul_scores = {"retrieve_folder": {folder("finance"): 0.9}, failing: down}
    llm.add(NAVIGATE, {"select": ["overview.md"], "done": True}).add(ANSWER, "Answer.")

    answer = wiki.ask("What do we do?")

    assert classifier.ops[-1] == failing
    assert llm.ops == [NAVIGATE, ANSWER]
    assert answer.notes == ["overview.md"]
    assert [(d.step, d.decider) for d in answer.decisions if d.fallback] == [("select", "llm")]


def test_unreachable_classifier_falls_back_to_llm_navigation(
    bundle: Path, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(HttpModel, "backoff", 0)
    requests: list[httpx.Request] = []

    def busy(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, text="busy")

    client = httpx.Client(transport=httpx.MockTransport(busy))
    wiki = make_wiki(bundle, llm, Classifier(ClassifierConfig(), llm.usage, client=client))
    llm.add(NAVIGATE, {"select": ["overview.md"], "done": True}).add(ANSWER, "Answer.")

    answer = wiki.ask("What do we do?")

    assert len(requests) == HttpModel.attempts
    assert answer.notes == ["overview.md"]
    assert answer.usage.classifier.calls == 0  # a failed request carries no usage


def test_usage_is_split_between_the_two_ledgers(wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM) -> None:
    classifier.noul_scores = {
        "retrieve_folder": {folder("finance"): 0.9},
        "retrieve_note": {note("Revenue policy"): 0.9},
    }
    llm.add(ANSWER, "Answer.")

    usage = wiki.ask("How is revenue booked?").usage

    assert classifier.ops == ["retrieve_folder", "retrieve_folder", "retrieve_note"]
    assert (usage.classifier.calls, usage.classifier.input_tokens) == (3, 3 * CLASSIFIER_TOKENS[0])
    assert (usage.llm.calls, usage.llm.input_tokens) == (1, LLM_TOKENS[0])
    assert set(usage.by_model) == {"llm:fake/llm", "classifier:fake/jev"}
    assert usage.by_model["classifier:fake/jev"].calls == 3
