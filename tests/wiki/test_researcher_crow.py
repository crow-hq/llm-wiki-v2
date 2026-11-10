# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""CROW Researcher: the search finds the notes, one Noul per note after the first reorders them, `k` of them are read."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents import steps_crow
from llmw2.agents.researcher import NO_ANSWER
from llmw2.bundle.tree import Folder, Note
from llmw2.config import CrowConfig
from llmw2.errors import ModelError
from llmw2.models.classifier import Classifier
from tests.wiki.fakes import FakeClassifier, FakeLLM

ANSWER = "researcher/answer"
OP = "retrieve_note"
QUESTION = "revenue pricing tiers nda"  # each of the notes below but the overview has one of these words


def add_note(wiki: Wiki, folder: Folder, title: str, body: str, **fields: str) -> Note:
    source = {"resource": "https://example.com", "title": title}
    return wiki.store.write_note(folder, title=title, summary=f"About {title.lower()}.", body=body, tags=[], source=source, fields=fields)


def make_wiki(bundle: Path, llm: FakeLLM, classifier: Classifier, **crow: Any) -> Wiki:
    """
    /overview.md
    /finance/revenue-policy.md    /finance/pricing/pricing-tiers.md
    /legal/nda-template.md
    """
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", crow=CrowConfig(**crow)), llm=llm, classifier=classifier)
    wiki.init()
    store, root = wiki.store, wiki.store.load()
    add_note(wiki, root, "Overview", "We sell widgets. Revenue is booked at head office.")
    finance, _ = store.create_folder(root, "finance", "Everything about finance.")
    add_note(wiki, finance, "Revenue policy", "Revenue is booked monthly. Revenue policy in detail.", team="ops")
    pricing, _ = store.create_folder(finance, "pricing", "Everything about pricing.")
    add_note(wiki, pricing, "Pricing tiers", "Pricing tiers in detail: price per tier.", team="sales")
    legal, _ = store.create_folder(root, "legal", "Everything about legal.")
    add_note(wiki, legal, "NDA template", "NDA template in detail. Terms are never shared.", team="ops")
    return wiki


@pytest.fixture
def wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    return make_wiki(bundle, llm, classifier)


def answer_prompt(llm: FakeLLM) -> str:
    return next(msgs[-1]["content"] for op, msgs in llm.calls if op == ANSWER)


def note(title: str) -> str:
    """Needle matching the retrieve_note question about one note."""
    return f'note titled "{title}"'


def asked(classifier: FakeClassifier) -> list[list[str]]:
    """Per retrieve_note call: the titles it was asked about, in order."""
    pattern = re.compile(r'note titled "([^"]+)"')
    return [[pattern.search(q["instructions"])[1] for q in qs.values()] for o, _, qs in classifier.calls if o == OP]


def retrieval_ops(classifier: FakeClassifier) -> list[str]:
    return [o for o in classifier.ops if o == OP]


def test_the_first_note_of_the_search_stays_first_and_the_rest_are_reordered(
    wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM
) -> None:
    search = [h.path for h in wiki.search(QUESTION, k=16)]
    titles = {h.path: h.title for h in wiki.search(QUESTION, k=16)}
    assert len(search) == 4  # the overview, the revenue policy, the tiers and the NDA
    classifier.noul_scores = {OP: {note(titles[search[1]]): 0.2, note(titles[search[2]]): 0.9, note(titles[search[3]]): 0.5}}
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert answer.notes == [search[0], search[2], search[3], search[1]]
    assert asked(classifier) == [[titles[p] for p in search[1:]]]  # one call, the first note not in it
    assert retrieval_ops(classifier) == [OP]


def test_a_note_the_search_puts_first_is_kept_first_whatever_the_classifier_says(
    wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM
) -> None:
    first = wiki.search(QUESTION, k=1)[0]
    classifier.noul_scores = {OP: lambda text: 0.0 if first.title in text else 0.9}
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert answer.notes[0] == first.path


def test_at_most_k_notes_are_read(bundle: Path, classifier: FakeClassifier, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm, classifier, k=2)
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert len(answer.notes) == 2


def test_with_rerank_off_the_order_of_the_search_is_kept_and_the_classifier_is_not_asked(
    bundle: Path, classifier: FakeClassifier, llm: FakeLLM
) -> None:
    wiki = make_wiki(bundle, llm, classifier, rerank=False)
    classifier.noul_scores = {OP: lambda text: 0.9 if "NDA" in text else 0.1}
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert answer.notes == [h.path for h in wiki.search(QUESTION, k=16)]
    assert retrieval_ops(classifier) == []
    assert not any(d.fallback for d in answer.decisions)


def test_when_the_classifier_fails_the_order_of_the_search_is_kept(wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM) -> None:
    def down(_: str) -> float:
        raise ModelError("classifier down")

    classifier.noul_scores = {OP: down}
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert answer.notes == [h.path for h in wiki.search(QUESTION, k=16)]
    assert llm.ops == [ANSWER]  # no LLM navigation
    assert [(d.step, d.decider) for d in answer.decisions if d.fallback] == [("retrieve_note", "classifier")]


def test_where_keeps_the_search_and_the_reordering_to_the_notes_that_match(
    wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM
) -> None:
    classifier.noul_scores = {OP: {note("NDA template"): 0.9}}
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION, where={"team": "ops"})

    assert sorted(answer.notes) == ["finance/revenue-policy.md", "legal/nda-template.md"]  # not the overview, nor the sales tiers
    assert "Pricing tiers" not in " ".join(t for ts in asked(classifier) for t in ts)
    assert answer.notes[0] == wiki.search(QUESTION, k=1, where={"team": "ops"})[0].path


NAVIGATE = "researcher/navigate"


def test_nothing_found_by_the_search_and_nothing_found_by_the_llm_means_no_answer(
    wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM
) -> None:
    llm.add(NAVIGATE, {"reasoning": "Nothing here is about zebras."})

    answer = wiki.ask("zebra giraffe")

    assert answer.notes == [] and answer.text == NO_ANSWER
    assert llm.ops == [NAVIGATE] and classifier.ops == []
    assert [(d.step, d.decider, d.fallback) for d in answer.decisions] == [("select", "llm", True), ("navigate", "llm", False)]


def test_nothing_found_by_the_search_the_llm_walks_the_folders_and_the_answer_reads_what_it_picks(
    wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM
) -> None:
    llm.add(NAVIGATE, {"open": ["legal"], "select": ["overview.md"]}, {"select": ["legal/nda-template.md"]}).add(ANSWER, "Answer.")

    answer = wiki.ask("zebra giraffe")

    assert answer.notes == ["overview.md", "legal/nda-template.md"]
    assert llm.ops == [NAVIGATE, NAVIGATE, ANSWER]
    assert OP not in classifier.ops  # what the LLM picked is not reordered
    assert [d.step for d in answer.decisions if d.fallback] == ["select"]


def test_a_question_the_search_answers_never_makes_the_llm_navigate(wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM) -> None:
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert NAVIGATE not in llm.ops and llm.ops == [ANSWER]
    assert not any(d.fallback or d.step == "navigate" for d in answer.decisions)


def test_the_question_to_the_classifier_carries_the_passage_of_the_note_that_matches_it(
    wiki: Wiki, classifier: FakeClassifier, llm: FakeLLM
) -> None:
    llm.add(ANSWER, "Answer.")

    wiki.ask(QUESTION)

    texts = [q["instructions"] for o, _, qs in classifier.calls if o == OP for q in qs.values()]
    assert all('It contains: "' in t for t in texts)
    assert any('It contains: "NDA template in detail. Terms are never shared."' in t for t in texts)


def make_note(body: str) -> Note:
    return Note(path=Path("n.md"), rel="n.md", frontmatter={"title": "T", "summary": "S"}, body=body)


def test_passage_is_the_window_of_600_characters_with_the_best_match() -> None:
    body = " ".join(["filler"] * 150) + " the zebra crossing is here " + " ".join(["padding"] * 150)

    text = steps_crow.passage(make_note(body), "zebra crossing")

    assert "zebra crossing" in text and len(text) == steps_crow.PASSAGE_CHARS


def test_passage_reaches_the_end_of_the_body() -> None:
    body = "x" * 950 + " the zebra crossing"  # past the last window that starts on the step

    text = steps_crow.passage(make_note(body), "zebra crossing")

    assert text.endswith("zebra crossing") and len(text) == steps_crow.PASSAGE_CHARS


def test_passage_normalises_spaces_and_leaves_out_see_also() -> None:
    text = steps_crow.passage(make_note("One   two\n\nthree.\n\n# See also\n\n- [x](x.md)\n"), "two")

    assert text == "One two three."


def test_passage_of_a_short_note_is_the_whole_body_and_of_an_empty_one_is_empty() -> None:
    assert steps_crow.passage(make_note("A short body."), "short") == "A short body."
    assert steps_crow.passage(make_note(""), "anything") == ""
