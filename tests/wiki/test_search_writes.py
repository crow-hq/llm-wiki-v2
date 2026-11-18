# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""How the search index writes: one connection kept open (WAL, `synchronous=NORMAL`, rare checkpoints), tied to its file and its
process; a sequence of writes and searches ranks as the BM25 of the files, and a process that ends without closing loses nothing."""

from __future__ import annotations

import random
from pathlib import Path

from llmw2 import Wiki
from tests.wiki.test_search import paths
from tests.wiki.test_search_index import VOCAB, _age, _expected, _fresh_rows, _index_file, _random_wiki, _rows


def test_a_sequence_of_notes_with_searches_between_ranks_as_the_bm25_of_the_files(tmp_path: Path) -> None:
    wiki = _random_wiki(tmp_path, 25)
    rng = random.Random(9)
    wiki.search("alpha")
    folder = wiki.store.load().subfolders[0]
    for step in range(14):
        wiki.store.write_note(
            folder, title=f"Seq {step}", summary="seq", body=" ".join(rng.choices(VOCAB, k=30)), tags=[], source={"resource": "/raw/s.md"}
        )
        if step % 3 == 0:  # an edit of a note written before, a deletion of another
            notes = wiki.store.load().all_notes()
            victim = rng.choice(notes)
            victim.path.write_text(victim.path.read_text(encoding="utf-8") + " " + " ".join(rng.choices(VOCAB, k=10)), encoding="utf-8")
        if step % 4 == 3:
            rng.choice(wiki.store.load().all_notes()).path.unlink()
        _age(tmp_path / "wiki")
        for _ in range(2):
            q = " ".join(rng.sample(VOCAB, 3))
            assert paths(wiki.search(q, k=8)) == _expected(wiki, q, 8)
    wiki.close()
    assert _rows(_index_file(tmp_path)) == _fresh_rows(wiki)


def test_a_process_that_ends_without_closing_loses_nothing(tmp_path: Path) -> None:
    """The process ends with its connection open and its last commits in the WAL: a new one finds every note."""
    wiki = _random_wiki(tmp_path, 20)
    wiki.search("alpha")
    folder = wiki.store.load().subfolders[0]
    notes = [
        wiki.store.write_note(folder, title=f"Late {i}", summary="s", body=f"zzlate{i} alpha", tags=[], source={"resource": "/raw/l"})
        for i in range(3)
    ]
    _age(tmp_path / "wiki")
    assert paths(wiki.search("zzlate1", k=3))[:1] == [notes[1].rel]  # found at once, written or not
    survivor = Wiki(wiki.cfg, llm=wiki.llm)  # a new process: the old one is not closed

    for note in notes:
        assert paths(survivor.search(f"zz{note.title.lower().replace(' ', '')}", k=3))[:1] == [note.rel]
    survivor.close()
    assert _rows(_index_file(tmp_path)) == _fresh_rows(survivor)


def test_a_held_connection_is_opened_again_after_a_replaced_file_or_a_fork(tmp_path: Path) -> None:
    import os

    wiki = _random_wiki(tmp_path, 12)
    wiki.search("alpha")
    index = wiki._search.index
    first = index._held
    assert first is not None
    assert paths(wiki.search("alpha", 3))
    assert index._held is first  # nothing changed: the connection is kept

    class Spy:
        closed = False

        def __init__(self, db):  # type: ignore[no-untyped-def]
            self.db = db

        def close(self) -> None:
            Spy.closed = True

        def __getattr__(self, name):  # type: ignore[no-untyped-def]
            return getattr(self.db, name)

    index._held_id = (os.getpid(), 0, 0)  # the file is not the one the connection was opened on
    assert paths(wiki.search("alpha", 3))
    assert index._held is not first and index._held is not None

    spy = Spy(index._held)
    index._held = spy  # type: ignore[assignment]
    index._held_id = (os.getpid() + 1, *index._held_id[1:])  # a fork: the connection belongs to the parent
    assert paths(wiki.search("alpha", 3))
    assert not Spy.closed and index._held is not spy  # the parent's connection is left alone, a new one is used
    wiki.close()


def test_a_damaged_file_is_put_aside_with_its_journal_not_deleted(tmp_path: Path) -> None:
    wiki = _random_wiki(tmp_path, 12)
    wiki.search("alpha")
    path = _index_file(tmp_path)
    wiki.close()
    path.write_bytes(b"not a database" * 100)

    fresh = Wiki(wiki.cfg, llm=wiki.llm)

    assert paths(fresh.search("alpha", 3))
    assert path.with_name(path.name + ".bad").read_bytes().startswith(b"not a database")
    assert _rows(path) == _fresh_rows(fresh)
