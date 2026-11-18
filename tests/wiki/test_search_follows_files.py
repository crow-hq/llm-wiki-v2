# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki.search` follows the files without loading the tree: edits, new notes, deletions and other processes show at once."""

from __future__ import annotations

import os
import time
from pathlib import Path

from llmw2 import Wiki
from llmw2.bundle.store import RACY_NS
from llmw2.search import VECTORS_FILE, VectorCache, _hash, _text_of
from tests.wiki.test_search import Embedder, make, paths


def _age(path: Path) -> None:
    """Make a file old enough to be trusted by its time and size."""
    old = time.time_ns() - 3 * RACY_NS
    os.utime(path, ns=(old, old))


def test_search_does_not_load_the_tree(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    wiki = make(tmp_path)
    monkeypatch.setattr(wiki.store, "load", lambda: (_ for _ in ()).throw(AssertionError("tree loaded")))

    assert paths(wiki.search("refund within thirty days", k=1)) == ["refunds.md"]


def test_an_edit_in_place_by_another_program_is_seen(tmp_path: Path) -> None:
    wiki = make(tmp_path)
    for f in (tmp_path / "wiki").glob("*.md"):
        _age(f)
    assert paths(wiki.search("quarter passwords", k=1)) == ["security.md"]

    note = tmp_path / "wiki" / "hiring.md"
    note.write_text(note.read_text(encoding="utf-8") + "\nPasswords quarter quarter quarter quarter.\n", encoding="utf-8")

    assert paths(wiki.search("quarter passwords quarter", k=1)) == ["hiring.md"]


def test_new_deleted_and_moved_notes(tmp_path: Path) -> None:
    wiki = make(tmp_path)
    assert wiki.search("zeppelin", k=3) == []

    wiki.store.write_note(
        wiki.store.load(), title="Zeppelin", summary="Airships", body="A zeppelin is an airship.", tags=[], source={"resource": "/raw/z.md"}
    )
    assert paths(wiki.search("zeppelin", k=3)) == ["zeppelin.md"]

    (tmp_path / "wiki" / "zeppelin.md").unlink()
    assert wiki.search("zeppelin", k=3) == []


def test_another_wiki_object_on_the_same_files_agrees(tmp_path: Path) -> None:
    first = make(tmp_path)
    second = Wiki(first.cfg, llm=first.llm)

    for query in ("refund thirty days", "parcels road", "engineers designers", "quarter"):
        assert paths(first.search(query, k=4)) == paths(second.search(query, k=4))


def test_a_moved_note_is_found_in_its_new_place_and_not_in_the_old(tmp_path: Path) -> None:
    wiki = make(tmp_path)
    assert paths(wiki.search("quarter passwords", k=1)) == ["security.md"]
    wiki.store.create_folder(wiki.store.load(), "vault", "Moved notes")

    wiki.move_note("security.md", "/vault")

    assert paths(wiki.search("quarter passwords", k=1)) == ["vault/security.md"]
    assert wiki.search("quarter passwords", k=3)[0].path == "vault/security.md"
    assert "security.md" not in paths(wiki.search("quarter passwords", k=8))


def test_the_vector_goes_under_the_hash_of_the_text_it_was_made_from(tmp_path: Path) -> None:
    """Another process rewrites a note after the search listed it and before the search embeds it: the cache must hold the
    vector of the new text under the new text's hash, and nothing under the old one."""
    wiki_dir = tmp_path / "wiki"
    target = wiki_dir / "hiring.md"
    state = {"done": False}

    class Racing(Embedder):
        def __call__(self, texts: list[str]) -> list[list[float]]:
            if not state["done"]:  # the first call is the query: the other process writes now
                state["done"] = True
                text = target.read_text(encoding="utf-8")
                target.write_text(text.replace("engineers and designers", "wire wire pilots"), encoding="utf-8")
            return super().__call__(texts)

    emb = Racing()
    wiki = make(tmp_path, emb)
    from llmw2.bundle.document import OKFDocument
    from llmw2.bundle.tree import Note

    def text_hash() -> str:
        doc = OKFDocument.read(target)
        note = Note(target, "hiring.md", doc.frontmatter, doc.body)
        return _hash(_text_of(note))

    old = text_hash()
    wiki.search("parcels")
    new = text_hash()

    assert old != new
    cache = VectorCache(wiki_dir / VECTORS_FILE).get([old, new])
    assert old not in cache and new in cache
    assert any("wire wire pilots" in t for t in emb.seen)
    emb.seen.clear()
    wiki.search("parcels")
    assert emb.seen == ["parcels"]  # the next search finds the new vector where it looks for it
