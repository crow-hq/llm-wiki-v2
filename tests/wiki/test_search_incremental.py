# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The BM25 index of `search` follows the notes as they change, and ranks as an index built from scratch would."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import random
from collections import Counter
from pathlib import Path

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents import lexical
from llmw2.bundle.store import WikiStore
from llmw2.bundle.tree import Folder
from llmw2.search import NoteSearch

VOCAB = [f"word{i}" for i in range(60)] + ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]


def sentence(rng: random.Random, n: int) -> str:
    return " ".join(rng.choice(VOCAB) for _ in range(n))


def counts(text: str) -> Counter[str]:
    return Counter(lexical.words(text))


# -- the index -------------------------------------------------------------------------------------------------------


def test_an_index_changed_by_add_and_remove_scores_exactly_as_one_built_whole() -> None:
    rng = random.Random(1)
    docs = {f"d{i}": sentence(rng, rng.randint(1, 40)) for i in range(80)}
    index = lexical.Bm25({k: counts(t) for k, t in docs.items()})
    for step in range(300):
        key = f"d{rng.randrange(120)}"
        if key in docs and rng.random() < 0.4:
            del docs[key]
            index.remove(key)
        else:
            docs[key] = sentence(rng, rng.randint(1, 40))
            index.add(key, counts(docs[key]))
        if step % 10:
            continue
        whole = lexical.Bm25({k: counts(t) for k, t in docs.items()})
        order = list(docs)
        for _ in range(5):
            query = sentence(rng, rng.randint(1, 4))
            assert index.scores(query, order) == whole.scores(query)  # the same floats, not nearly the same
            assert index.rank(query, order) == whole.rank(query)


def test_scores_over_a_subset_are_those_of_an_index_of_the_subset() -> None:
    rng = random.Random(2)
    docs = {f"d{i}": sentence(rng, rng.randint(1, 30)) for i in range(60)}
    index = lexical.Bm25({k: counts(t) for k, t in docs.items()})
    for _ in range(40):
        subset = rng.sample(list(docs), rng.randint(1, 40))
        query = sentence(rng, rng.randint(1, 3))
        alone = lexical.Bm25({k: counts(docs[k]) for k in subset})
        assert index.scores(query, subset) == alone.scores(query)
        assert index.rank(query, subset) == alone.rank(query)


def test_ties_keep_the_order_of_the_keys_given() -> None:
    index = lexical.Bm25({"a": counts("same words here"), "b": counts("same words here"), "c": counts("same words here")})
    assert index.rank("words", ["c", "a", "b"]) == ["c", "a", "b"]
    assert index.rank("words") == ["a", "b", "c"]


def test_lengths_are_summed_as_integers() -> None:
    index = lexical.Bm25({})
    for i in range(1000):
        index.add(f"d{i}", counts("alpha " * (i % 7 + 1)))
    for i in range(0, 1000, 3):
        index.remove(f"d{i}")
    assert index.total == sum(index.length.values()) and isinstance(index.total, int)
    assert index.scores("alpha") == lexical.Bm25({k: counts("alpha " * (int(k[1:]) % 7 + 1)) for k in index.length}).scores("alpha")


def test_an_empty_index_and_a_missing_word_score_zero() -> None:
    assert lexical.Bm25({}).scores("anything") == {}
    index = lexical.Bm25({"a": counts("alpha beta gamma")})
    assert index.scores("delta") == {"a": 0.0} and index.rank("delta") == ["a"]


# -- the search ------------------------------------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> WikiStore:
    s = WikiStore(tmp_path / "w", actor="test")
    s.init()
    return s


def write(store: WikiStore, folder: Folder, title: str, text: str) -> None:
    store.write_note(folder, title=title, summary=text[:60], body=f"# Overview\n\n{text}", tags=[], source={"resource": "/raw/x.md"})


def hits(search: NoteSearch, store: WikiStore, query: str, k: int = 8, folder: str | None = None) -> list[str]:
    root = store.load()
    notes = (root.find(folder) or root).all_notes() if folder else root.all_notes()
    return [n.rel for n in search.search(notes, root.all_notes(), query, k)]


def test_a_search_after_random_changes_gives_what_a_new_search_gives(store: WikiStore) -> None:
    rng = random.Random(3)
    root = store.load()
    folders = [store.create_folder(root, name, f"Folder {name}.")[0] for name in ("north", "south", "east")]
    for i in range(40):
        write(store, rng.choice(folders), f"Note {i:03d}", sentence(rng, rng.randint(5, 40)))
    kept = NoteSearch(None)
    for step in range(60):
        tree = store.load()
        notes = tree.all_notes()
        action = rng.random()
        if action < 0.3:
            write(store, tree.find(rng.choice(["north", "south", "east"])), f"Added {step:03d}", sentence(rng, rng.randint(5, 40)))  # type: ignore[arg-type]
        elif action < 0.55 and notes:  # a note edited by another program
            victim = rng.choice(notes)
            victim.path.write_text(victim.path.read_text(encoding="utf-8") + "\n\n" + sentence(rng, 20) + "\n", encoding="utf-8")
        elif action < 0.75 and notes:  # a note forgotten
            rng.choice(notes).path.unlink()
        for _ in range(3):
            query = sentence(rng, rng.randint(1, 3))
            folder = rng.choice([None, "north", "south", "east"])
            assert hits(kept, store, query, folder=folder) == hits(NoteSearch(None), store, query, folder=folder), (step, query, folder)


def test_a_note_deleted_leaves_the_results_at_once(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    write(store, folder, "Zebra stripes", "the zebra has stripes")
    write(store, folder, "Other", "something else entirely")
    kept = NoteSearch(None)
    assert hits(kept, store, "zebra") == ["sales/zebra-stripes.md"]
    (folder.path / "zebra-stripes.md").unlink()
    assert hits(kept, store, "zebra") == []


def test_a_note_written_by_another_process_is_found_by_the_next_search(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    write(store, folder, "First", "nothing of note")
    kept = NoteSearch(None)
    assert hits(kept, store, "quokka") == []
    other = WikiStore(store.root, actor="other")
    write(other, other.load().find("sales"), "Quokka", "the quokka smiles")  # type: ignore[arg-type]
    assert hits(kept, store, "quokka") == ["sales/quokka.md"]


def test_a_note_rewritten_in_place_is_ranked_by_its_new_words(store: WikiStore) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    write(store, folder, "One", "apples apples apples")
    write(store, folder, "Two", "pears")
    kept = NoteSearch(None)
    assert hits(kept, store, "apples")[0] == "sales/one.md"
    path = folder.path / "one.md"
    path.write_text(path.read_text(encoding="utf-8").replace("apples", "pears"), encoding="utf-8")
    assert hits(kept, store, "apples") == [] and set(hits(kept, store, "pears")) == {"sales/one.md", "sales/two.md"}


def test_an_unchanged_wiki_is_not_indexed_again(store: WikiStore, monkeypatch: pytest.MonkeyPatch) -> None:
    folder, _ = store.create_folder(store.load(), "sales", "Sales.")
    for i in range(5):
        write(store, folder, f"Note {i}", f"words {i}")
    kept = NoteSearch(None)
    hits(kept, store, "words")
    added = []
    real = lexical.Bm25.add
    monkeypatch.setattr(lexical.Bm25, "add", lambda self, key, c: (added.append(key), real(self, key, c))[1])
    hits(kept, store, "words")
    hits(kept, store, "words", folder="sales")
    assert added == []
    write(store, folder, "Note 9", "words nine")
    hits(kept, store, "words")
    assert added == ["sales/note-9.md"]  # only the note that is new


def test_wiki_search_with_where_uses_the_index_of_all_the_notes(tmp_path: Path) -> None:
    wiki = Wiki(WikiConfig(bundle=tmp_path / "w", mode="classic"))
    wiki.init()
    folder, _ = wiki.store.create_folder(wiki.store.load(), "sales", "Sales.")
    for i in range(6):
        wiki.store.write_note(
            folder, title=f"Note {i}", summary="s", body="# Overview\n\ncommon words", tags=[], source={"resource": "/raw/x.md"},
            fields={"client": "a" if i % 2 else "b"},
        )
    everyone = [h.path for h in wiki.search("common")]
    only_a = [h.path for h in wiki.search("common", where={"client": "a"})]
    assert len(everyone) == 6 and only_a == [p for p in everyone if p in only_a] and len(only_a) == 3
    assert [h.path for h in wiki.search("common")] == everyone
