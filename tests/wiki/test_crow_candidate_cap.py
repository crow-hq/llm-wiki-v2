# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`match` and `relate` of CROW ask about a bounded number of notes: the folder's own first, then the closest by BM25."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2 import Candidate, Wiki, WikiConfig
from llmw2.agents import linking, steps_classic, steps_crow
from llmw2.agents.steps import CROW, StepContext, decision_hash
from llmw2.bundle.tree import Note
from llmw2.errors import ModelError
from tests.wiki.fakes import FakeClassifier, FakeLLM

CAND = Candidate("Invoice VAT", "VAT on invoices.", [], "The invoice carries VAT at the standard rate.")
HOME = ["Boat hull", "Boat sail"]  # in the folder the document goes to, about something else
# the other notes, from the one that shares most words with the document to the one that shares none
OTHERS = ["Invoice VAT invoice VAT rate", "Invoice VAT rate", "Invoice rate", "VAT", "Harbour", "Anchor"]
TITLES = [*HOME, *(f"Note {i}" for i in range(len(OTHERS)))]


@pytest.fixture
def wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier)
    wiki.init()
    root = wiki.store.load()
    boats, _ = wiki.store.create_folder(root, "boats", "Boats.")
    taxes, _ = wiki.store.create_folder(root, "taxes", "Taxes.")
    for title in HOME:
        wiki.store.write_note(boats, title=title, summary=title, body=title, tags=[], source={"resource": "/raw/a.md", "title": title})
    for i, text in enumerate(OTHERS):
        source = {"resource": "/raw/a.md", "title": "t"}
        wiki.store.write_note(taxes, title=f"Note {i}", summary="Other.", body=text, tags=[], source=source)
    return wiki


def pool_of(wiki: Wiki) -> list[Note]:
    return wiki.store.load().all_notes()


def ctx_of(wiki: Wiki, home: str | None = "boats") -> StepContext:
    ctx = wiki.librarian().ctx
    ctx.home = home
    return ctx


def cuts(ctx: StepContext, step: str) -> list[str]:
    return [d.choice for d in ctx.decisions if d.step == step]


def test_match_when_the_pool_fits_the_cap_then_every_note_is_asked_in_its_order_and_nothing_is_recorded(
    wiki: Wiki, classifier: FakeClassifier
) -> None:
    ctx, pool = ctx_of(wiki), pool_of(wiki)
    assert len(pool) <= steps_crow.MATCH_TOP

    steps_crow.match(ctx, pool, CAND)

    questions = next(q for op, _, q in classifier.calls if op == "match")
    assert [q["instructions"] for q in questions.values()] == [
        ctx.prompts.render("classifier/note_relevance", title=n.title, summary=n.summary) for n in pool
    ]
    assert not cuts(ctx, "match_cut")


def test_match_when_the_pool_exceeds_the_cap_then_the_folders_notes_come_first_and_then_the_best_by_bm25(
    wiki: Wiki, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steps_crow, "MATCH_TOP", 5)  # the 2 of the folder and the 3 closest of the other 6
    ctx = ctx_of(wiki)

    steps_crow.match(ctx, pool_of(wiki), CAND)

    titles = _titles_asked(classifier, "match")
    assert sorted(titles) == sorted([*HOME, "Note 0", "Note 1", "Note 2"])
    assert cuts(ctx, "match_cut") == ["5 of 8 notes, by BM25"]


def test_match_when_the_folder_alone_exceeds_the_cap_then_its_notes_are_ranked_by_bm25(
    wiki: Wiki, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steps_crow, "MATCH_TOP", 3)
    ctx = ctx_of(wiki, home="taxes")  # 6 notes of the folder: the 3 closest

    steps_crow.match(ctx, pool_of(wiki), CAND)

    assert sorted(_titles_asked(classifier, "match")) == ["Note 0", "Note 1", "Note 2"]


def test_match_when_the_classifier_fails_then_the_llm_fallback_gets_the_cut_pool(
    wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steps_crow, "MATCH_TOP", 3)

    def fail(*args: object, **kwargs: object) -> None:
        raise ModelError("down")

    monkeypatch.setattr(classifier, "nouls", fail)
    llm.add("librarian/find_match", {"match": ""})
    pool = pool_of(wiki)

    steps_crow.match(ctx_of(wiki), pool, CAND)

    user = next(m["content"] for op, msgs in llm.calls if op == "librarian/find_match" for m in msgs if m["role"] == "user")
    assert len([n for n in pool if n.rel in user]) == 3


def test_relate_when_the_candidates_fit_the_cap_then_every_one_is_asked_and_nothing_is_recorded(
    wiki: Wiki, classifier: FakeClassifier
) -> None:
    ctx, notes = ctx_of(wiki), pool_of(wiki)

    steps_crow.relate(ctx, notes[0], notes[1:])

    assert len(next(q for op, _, q in classifier.calls if op == "relate")) == len(notes) - 1
    assert not cuts(ctx, "relate_cut")


def test_relate_when_the_candidates_exceed_the_cap_then_the_rare_ones_are_kept_apart_and_the_rest_cut(
    wiki: Wiki, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steps_crow, "RELATE_TOP", 3)
    notes = pool_of(wiki)
    note = next(n for n in notes if n.title == "Boat hull")
    others = [n for n in notes if n is not note]  # 1 in its folder, 6 elsewhere
    rare = [n for n in others if n.title in {"Note 4", "Note 5"}]  # the least like the note: BM25 alone would drop them
    ctx = ctx_of(wiki)
    ctx.decide("relate_candidates", "code", "2 by rare values", scores={n.rel: 1.0 for n in rare})

    steps_crow.relate(ctx, note, others)

    kept = _titles_asked(classifier, "relate")
    assert len(kept) == 3 + 2 and {"Note 4", "Note 5", "Boat sail"} <= set(kept)  # its folder's note first, the rare ones on top
    assert cuts(ctx, "relate_cut") == ["3 of 5 notes, by BM25"]


def test_relate_when_match_gave_probabilities_then_the_candidates_are_ordered_by_them_before_bm25(
    wiki: Wiki, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steps_crow, "RELATE_TOP", 3)
    classifier.noul_scores["match"] = {"Note 5": 0.4, "Note 4": 0.7, "Note 3": 0.1}  # the notes BM25 would rank last
    notes = pool_of(wiki)
    note = next(n for n in notes if n.title == "Boat hull")
    ctx = ctx_of(wiki)
    steps_crow.match(ctx, notes, CAND)  # records its probabilities by the note's path
    classifier.calls.clear()

    steps_crow.relate(ctx, note, [n for n in notes if n is not note])

    assert sorted(_titles_asked(classifier, "relate")) == ["Note 3", "Note 4", "Note 5"]
    assert cuts(ctx, "relate_cut") == ["3 of 7 notes, by match probability, then BM25"]


def test_relate_when_match_scored_fewer_than_the_cap_then_bm25_with_the_folder_first_fills_the_rest(
    wiki: Wiki, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steps_crow, "RELATE_TOP", 3)
    notes = pool_of(wiki)
    note = next(n for n in notes if n.title == "Note 0")  # in "taxes", with the notes about invoices
    others = [n for n in notes if n is not note]
    scored = next(n for n in others if n.title == "Boat sail")
    ctx = ctx_of(wiki)
    ctx.decide("match", "classifier", "none", 0.9, scores={scored.rel: 0.9})

    steps_crow.relate(ctx, note, others)

    assert sorted(_titles_asked(classifier, "relate")) == ["Boat sail", "Note 1", "Note 2"]  # scored, then its folder by BM25


def test_relate_when_there_is_no_match_decision_then_it_is_bm25_alone(
    wiki: Wiki, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steps_crow, "RELATE_TOP", 3)
    notes = pool_of(wiki)
    note = next(n for n in notes if n.title == "Note 0")
    ctx = ctx_of(wiki)  # a merge or a rebuild: match did not run

    steps_crow.relate(ctx, note, [n for n in notes if n is not note])

    assert sorted(_titles_asked(classifier, "relate")) == ["Note 1", "Note 2", "Note 3"]
    assert cuts(ctx, "relate_cut") == ["3 of 7 notes, by BM25"]


def test_the_rare_value_group_is_capped_at_ten() -> None:
    assert linking.RARE_TOP == 10


def test_classic_match_and_relate_are_not_cut(wiki: Wiki, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(steps_crow, "MATCH_TOP", 3)
    monkeypatch.setattr(steps_crow, "RELATE_TOP", 3)
    llm.add("librarian/find_match", {"match": ""})
    llm.add("librarian/relate", {"related": []})
    ctx, notes = ctx_of(wiki), pool_of(wiki)

    steps_classic.match(ctx, notes, CAND)
    steps_classic.relate(ctx, notes[0], notes[1:])

    assert not cuts(ctx, "match_cut") and not cuts(ctx, "relate_cut")
    for op in ("librarian/find_match", "librarian/relate"):
        user = next(m["content"] for o, msgs in llm.calls if o == op for m in msgs if m["role"] == "user")
        assert all(n.rel in user for n in notes[1:])


def test_decision_hash_follows_the_caps(wiki: Wiki, monkeypatch: pytest.MonkeyPatch) -> None:
    before = decision_hash(CROW, wiki.cfg, wiki.prompts)
    for module, name in ((steps_crow, "MATCH_TOP"), (steps_crow, "RELATE_TOP"), (linking, "RARE_TOP")):
        with monkeypatch.context() as m:
            m.setattr(module, name, getattr(module, name) + 1)
            assert decision_hash(CROW, wiki.cfg, wiki.prompts) != before, name
    assert decision_hash(CROW, wiki.cfg, wiki.prompts) == before


def test_ingest_over_the_caps_records_both_cuts(
    wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(steps_crow, "MATCH_TOP", 3)
    monkeypatch.setattr(steps_crow, "RELATE_TOP", 3)
    llm.add("librarian/summarize", {"title": "Invoice VAT", "summary": "VAT.", "tags": [], "body": "VAT on the invoice."})
    classifier.add_choice("route", "/taxes", {"/taxes": 0.9}, 0.9)

    result = wiki.ingest("The invoice carries VAT.")

    steps = [d.step for d in result.decisions]
    assert "match_cut" in steps and "relate_cut" in steps


def _titles_asked(classifier: FakeClassifier, op: str) -> list[str]:
    """The titles of the notes of the first request of `op`, read from its questions."""
    _, _, questions = next(c for c in classifier.calls if c[0] == op)
    return [next(t for t in TITLES if t in q["instructions"]) for q in questions.values()]
