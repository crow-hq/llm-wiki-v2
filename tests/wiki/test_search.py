# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki.search`: BM25 on the whole note, and with an embedder two by meaning and one by words."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2 import InputError, Wiki, WikiConfig
from llmw2.agents import lexical
from llmw2.models.usage import UsageTracker
from llmw2.search import VECTORS_FILE, NoteSearch, VectorCache
from tests.wiki.fakes import FakeLLM

NOTES = [
    ("Refunds", "How refunds work", "Customers ask for a refund within thirty days.", "ops"),
    ("Shipping", "Delivery times", "Parcels travel by road and arrive in three days.", "ops"),
    ("Hiring", "Open roles", "The company hires engineers and designers.", "hr"),
    ("Security", "Access rules", "Passwords rotate every quarter.", "it"),
]
# where each text lies for the fake embedder: the first word found decides
MEANING = {"parcels": [1.0, 0.0, 0.0], "hires": [0.9, 0.1, 0.0], "passwords": [0.5, 0.5, 0.0], "wire": [1.0, 0.0, 0.0]}


class Embedder:
    """A vector per text from MEANING (a far one for the rest); keeps what it was asked to embed."""

    def __init__(self, meaning: dict[str, list[float]] | None = None) -> None:
        self.meaning = meaning if meaning is not None else MEANING
        self.seen: list[str] = []

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.seen += texts
        return [next((v for w, v in self.meaning.items() if w in t.lower()), [0.0, 0.0, 1.0]) for t in texts]


def make(tmp_path: Path, embedder: Embedder | None = None) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=tmp_path / "wiki", mode="classic"), llm=FakeLLM(UsageTracker()), embedder=embedder)
    wiki.init()
    for title, summary, body, team in NOTES:
        wiki.store.write_note(
            wiki.store.load(), title=title, summary=summary, body=body, tags=[], source={"resource": "/raw/x.md"}, fields={"team": team}
        )
    return wiki


def paths(hits: list) -> list[str]:  # type: ignore[type-arg]
    return [h.path for h in hits]


def test_bm25_alone_finds_the_note_by_its_words(tmp_path: Path) -> None:
    hits = make(tmp_path).search("refund within thirty days", k=2)

    assert paths(hits) == ["refunds.md", "shipping.md"]
    assert (hits[0].title, hits[0].summary, hits[0].rank) == ("Refunds", "How refunds work", 1)


def test_bm25_reads_the_title_and_the_summary(tmp_path: Path) -> None:
    assert paths(make(tmp_path).search("delivery")) == ["shipping.md"]
    assert paths(make(tmp_path / "other").search("security")) == ["security.md"]


def test_bm25_leaves_out_notes_with_no_word_of_the_query(tmp_path: Path) -> None:
    assert paths(make(tmp_path).search("passwords rotate")) == ["security.md"]


def test_bm25_does_not_read_the_see_also_section(tmp_path: Path) -> None:
    wiki = make(tmp_path)
    wiki.store.link(*(n for n in wiki.store.load().all_notes() if n.rel in ("refunds.md", "hiring.md")))

    assert paths(wiki.search("hiring")) == ["hiring.md"]


def test_the_index_ranks_as_the_function_does() -> None:
    docs = {"x": "alpha beta beta", "y": "beta gamma", "z": "delta"}

    assert lexical.rank("beta alpha", docs) == ["x", "y", "z"]


def test_empty_query_raises_as_ask_does(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="empty question"):
        make(tmp_path).search("  ")


def test_where_restricts_the_notes(tmp_path: Path) -> None:
    wiki = make(tmp_path)

    assert sorted(paths(wiki.search("days", where={"team": "ops"}))) == ["refunds.md", "shipping.md"]
    assert wiki.search("passwords", where={"team": "ops"}) == []
    assert paths(wiki.search("days", k=1, where={"team": "hr"})) == []


def test_fusion_two_by_meaning_then_one_by_words(tmp_path: Path) -> None:
    wiki = make(tmp_path, Embedder())

    hits = wiki.search("wire refund", k=3)  # by meaning: shipping, hiring (a far query vector); by words: refunds

    assert paths(hits) == ["shipping.md", "hiring.md", "refunds.md"] and [h.rank for h in hits] == [1, 2, 3]


def test_fusion_does_not_repeat_a_note_and_fills_from_meaning(tmp_path: Path) -> None:
    wiki = make(tmp_path, Embedder())

    hits = wiki.search("arrive road wire", k=3)  # the best by words, shipping, is already first by meaning

    assert paths(hits) == ["shipping.md", "hiring.md", "security.md"]


def test_fusion_for_another_k_takes_k_minus_one_by_meaning(tmp_path: Path) -> None:
    wiki = make(tmp_path, Embedder())

    assert paths(wiki.search("wire refund", k=4)) == ["shipping.md", "hiring.md", "security.md", "refunds.md"]
    assert paths(wiki.search("wire refund", k=2)) == ["shipping.md", "refunds.md"]


def test_fusion_respects_where(tmp_path: Path) -> None:
    wiki = make(tmp_path, Embedder())

    assert paths(wiki.search("wire refund", k=3, where={"team": "ops"})) == ["shipping.md", "refunds.md"]


def test_vectors_are_kept_and_only_a_changed_note_is_embedded_again(tmp_path: Path) -> None:
    emb = Embedder()
    wiki = make(tmp_path, emb)
    wiki.search("parcels")
    assert len(emb.seen) == len(NOTES) + 1 and (tmp_path / "wiki" / VECTORS_FILE).exists()

    emb.seen.clear()
    wiki.search("parcels")
    assert emb.seen == ["parcels"]  # the query alone

    note = next(n for n in wiki.store.load().all_notes() if n.rel == "hiring.md")
    wiki.store.update_note(note, title="Hiring", summary="Open roles", body="The company now hires pilots.", tags=[], source={})
    emb.seen.clear()
    wiki.search("parcels")
    assert len(emb.seen) == 2 and "pilots" in emb.seen[1]

    emb.seen.clear()
    Wiki(WikiConfig(bundle=tmp_path / "wiki", mode="classic"), llm=FakeLLM(UsageTracker()), embedder=emb).search("parcels")
    assert emb.seen == ["parcels"]  # a new process: the file serves every vector


def test_only_the_first_3000_characters_of_the_body_are_embedded(tmp_path: Path) -> None:
    emb = Embedder()
    wiki = make(tmp_path, emb)
    note = next(n for n in wiki.store.load().all_notes() if n.rel == "hiring.md")
    wiki.store.update_note(note, title="Hiring", summary="Open roles", body="x" * 3000 + "TAIL", tags=[], source={})

    wiki.search("parcels")

    assert not any("TAIL" in text for text in emb.seen) and any("Hiring\nOpen roles\n\n" + "x" * 3000 in text for text in emb.seen)


def test_the_vectors_of_another_model_are_not_reused_and_those_of_the_same_model_are(tmp_path: Path) -> None:
    wiki = make(tmp_path)
    notes = wiki.store.load().all_notes()
    cache = VectorCache(tmp_path / "vectors.sqlite")

    def search(model: str, emb: Embedder) -> None:
        NoteSearch(cache, emb, model).search(notes, notes, "parcels", 3)

    first, again, other, old = Embedder(), Embedder(), Embedder(), Embedder()
    search("model-a", first)
    search("model-a", again)
    search("model-b", other)
    search("", old)  # an embedder whose model the library does not know: the keys of the note's text alone

    assert len(first.seen) == len(NOTES) + 1
    assert again.seen == ["parcels"]  # same model: every vector served from the file
    assert len(other.seen) == len(NOTES) + 1  # another model: embedded again
    assert len(old.seen) == len(NOTES) + 1  # entries written without a model simply miss
