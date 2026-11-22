# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`delete_note` with the search index complete: the notes that link to the note, or cite its raw copies, come from the index's links
(`SearchIndex.linking`), the wiki listed under the lock, and only the note's folder and those notes are read. The files it changes, and
what it says it did, are those of the delete that reads the whole tree."""

from __future__ import annotations

import os
import random
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from llmw2 import Origin, Wiki, WikiConfig
from llmw2.bundle.store import WikiStore
from llmw2.bundle.tree import drop_links, link_targets
from llmw2.models.usage import UsageTracker
from llmw2.searchindex import SearchIndex
from tests.wiki.fakes import FakeLLM
from tests.wiki.problems import errors

NOTES = 18
WEB = {"resource": "https://example.com/doc", "title": "web"}  # a source that is no raw copy


def complete(wiki: Wiki) -> None:
    """The wiki's index file built whole and marked complete, as its build in the background leaves it."""
    index = wiki._search.index
    index.sync(wiki.store, wiki._search._entry)
    index.run(lambda: index._mark_built(index._open(), "complete"))
    assert wiki._links_indexed()


def make_corpus(bundle: Path) -> list[str]:
    """Notes in the root, in folders and subfolders, linked inline (relative, from the root, with anchors, to the web) and by See also,
    raw copies cited by one note or by several, an archived version, and two notes changed by hand after the index read them. The
    paths of the notes."""
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=FakeLLM(UsageTracker()))
    wiki.init()
    store = wiki.store
    rng = random.Random(7)
    root = store.load()
    a, _ = store.create_folder(root, "alpha", "First.")
    b, _ = store.create_folder(a, "beta", "Second.")
    c, _ = store.create_folder(root, "gamma", "Third.")
    folders = [root, a, b, c]
    raws = [store.save_raw(f"Source {i}.", title=f"Source {i}", resource=None, origin=Origin("test", "", f"doc-{i}")) for i in range(6)]
    notes = []
    for i in range(NOTES):
        folder = folders[i % len(folders)]
        body = f"Note {i}."
        for other in rng.sample(notes, min(len(notes), 2)):
            link = os.path.relpath(other.rel, folder.rel or ".").replace(os.sep, "/") if rng.random() < 0.7 else f"/{other.rel}"
            body += f" See [n{other.rel}]({link}{'#x' if rng.random() < 0.3 else ''})."
        body += " And [the web](https://example.com/alpha/x.md)."
        raw = raws[rng.randrange(len(raws))]
        source = {"resource": raw, "title": "s"}
        note = store.write_note(folder, title=f"Note {i}", summary=f"About {i}.", body=body, tags=[], source=source)
        notes.append(note)
    for x, y in [(0, 5), (3, 9), (9, 14), (14, 2), (6, 7)]:
        store.link(notes[x], notes[y])
    with store.lock():
        store.archive_note(notes[4])  # an archived version, which goes with its note
    complete(wiki)
    wiki.close()
    by_hand = [notes[11], notes[16]]  # a link added by hand to each, the index behind
    for one, target in zip(by_hand, [notes[1], notes[8]], strict=True):
        text = one.path.read_text(encoding="utf-8")
        link = os.path.relpath(target.rel, os.path.dirname(one.rel) or ".").replace(os.sep, "/")
        one.path.write_text(text.rstrip("\n") + f"\n\nBy hand: [x]({link}).\n", encoding="utf-8")
    return [n.rel for n in notes]


def files_of(bundle: Path) -> dict[str, bytes]:
    return {p.relative_to(bundle).as_posix(): p.read_bytes() for p in sorted(bundle.rglob("*")) if p.is_file() and ".llmw2" not in p.parts}


def delete_in_copy(
    source: Path, target: Path, rel: str, *, by_index: bool, monkeypatch: pytest.MonkeyPatch, purge: bool = False
) -> tuple[dict, dict]:
    shutil.copytree(source, target)
    wiki = Wiki(WikiConfig(bundle=target, mode="classic"), llm=FakeLLM(UsageTracker()))
    loads: list[int] = []
    real = WikiStore.load

    def load(self: WikiStore) -> object:
        loads.append(1)
        return real(self)

    with monkeypatch.context() as m:
        m.setattr(WikiStore, "load", load)
        if not by_index:
            m.setattr(wiki, "_links_indexed", lambda: False)
        deleted = wiki.purge(rel) if purge else wiki.delete_note(rel)
    wiki.close()
    assert (not loads) if by_index else loads  # the index's delete reads no tree; the other reads it
    assert deleted is not None
    return deleted.to_dict(), files_of(target)


def test_a_delete_by_the_index_changes_the_files_the_delete_of_the_whole_tree_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every note of the corpus deleted in two copies of the same wiki, by the index and by the whole tree: the same `Deleted` (the
    notes unlinked in the order of the tree) and the same files, byte for byte, the notes changed by hand after the index included."""
    source = tmp_path / "source"
    rels = make_corpus(source)
    unlinked = 0
    for i, rel in enumerate(rels):
        a = delete_in_copy(source, tmp_path / f"a{i}", rel, by_index=True, monkeypatch=monkeypatch)
        b = delete_in_copy(source, tmp_path / f"b{i}", rel, by_index=False, monkeypatch=monkeypatch)
        assert a == b, rel
        unlinked += len(a[0]["unlinked"])
    assert unlinked >= 20  # the corpus links: the comparison is not of deletes that change nothing


def test_a_purge_by_the_index_changes_the_files_the_purge_of_the_whole_tree_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every document of the corpus purged in two copies of the same wiki, by the index and by the whole tree: the same `Purged` and the
    same files, byte for byte (log.md with its lines made "[purged]" included)."""
    source = tmp_path / "source"
    make_corpus(source)
    notes = 0
    for i in range(6):
        key = Origin("test", "", f"doc-{i}").key
        a = delete_in_copy(source, tmp_path / f"a{i}", key, by_index=True, monkeypatch=monkeypatch, purge=True)
        b = delete_in_copy(source, tmp_path / f"b{i}", key, by_index=False, monkeypatch=monkeypatch, purge=True)
        assert a == b, key
        notes += len(a[0]["notes"])
    assert notes >= NOTES - 2  # the documents are cited: the comparison is not of purges that change nothing


def test_a_note_changed_by_hand_after_the_index_loses_its_link_too(classic_wiki: Wiki) -> None:
    store = classic_wiki.store
    root = store.load()
    target = store.write_note(root, title="Target", summary="t", body="The target.", tags=[], source=WEB)
    other = store.write_note(root, title="Other", summary="o", body="Nothing yet.", tags=[], source=WEB)
    complete(classic_wiki)
    other.path.write_text(other.path.read_text(encoding="utf-8").replace("Nothing yet.", "Now [t](target.md)."), encoding="utf-8")

    deleted = classic_wiki.delete_note(target.rel)

    assert deleted.unlinked == [other.rel]
    assert "target.md" not in other.path.read_text(encoding="utf-8")
    assert errors(classic_wiki.check()) == []


def test_a_note_the_index_holds_but_gone_by_hand_is_no_trouble(classic_wiki: Wiki) -> None:
    store = classic_wiki.store
    root = store.load()
    target = store.write_note(root, title="Target", summary="t", body="The target.", tags=[], source=WEB)
    other = store.write_note(root, title="Other", summary="o", body="See [t](target.md).", tags=[], source=WEB)
    complete(classic_wiki)
    other.path.unlink()

    deleted = classic_wiki.delete_note(target.rel)

    assert deleted.unlinked == [] and not target.path.exists()


def test_a_raw_copy_another_note_cites_stays_and_one_only_it_cites_goes(classic_wiki: Wiki) -> None:
    store = classic_wiki.store
    root = store.load()
    shared = store.save_raw("Shared.", title="Shared", resource=None)
    alone = store.save_raw("Alone.", title="Alone", resource=None)
    sub, _ = store.create_folder(root, "far", "Far away.")
    gone = store.write_note(root, title="Gone", summary="g", body="b", tags=[], source={"resource": shared, "title": "s"})
    store.update_note(gone, title="Gone", summary="g", body="b", tags=[], source={"resource": alone, "title": "a"})
    store.write_note(sub, title="Stays", summary="s", body="b", tags=[], source={"resource": shared, "title": "s"})
    complete(classic_wiki)

    deleted = classic_wiki.delete_note(gone.rel)

    assert deleted.sources == [alone]
    assert (store.root / shared.lstrip("/")).exists() and not (store.root / alone.lstrip("/")).exists()


def test_when_the_index_cannot_say_the_tree_is_read(classic_wiki: Wiki, monkeypatch: pytest.MonkeyPatch) -> None:
    store = classic_wiki.store
    root = store.load()
    target = store.write_note(root, title="Target", summary="t", body="The target.", tags=[], source=WEB)
    other = store.write_note(root, title="Other", summary="o", body="See [t](target.md).", tags=[], source=WEB)
    complete(classic_wiki)
    loads: list[int] = []
    real = WikiStore.load
    monkeypatch.setattr(WikiStore, "load", lambda self: loads.append(1) or real(self))
    monkeypatch.setattr(SearchIndex, "linking", lambda self, store, targets, wait=1.0: None)

    deleted = classic_wiki.delete_note(target.rel)

    assert loads and deleted.unlinked == [other.rel]


def test_an_index_never_complete_leaves_the_delete_to_the_whole_tree(classic_wiki: Wiki, monkeypatch: pytest.MonkeyPatch) -> None:
    store = classic_wiki.store
    root = store.load()
    target = store.write_note(root, title="Target", summary="t", body="The target.", tags=[], source=WEB)
    other = store.write_note(root, title="Other", summary="o", body="See [t](target.md).", tags=[], source=WEB)
    index = classic_wiki._search.index
    index.sync(store, classic_wiki._search._entry)  # built whole, not complete: a build in the background has not listed again yet
    assert index.path is not None and index.path.exists() and not index.ready()
    asked: list[int] = []
    monkeypatch.setattr(SearchIndex, "linking", lambda *a, **k: asked.append(1))

    deleted = classic_wiki.delete_note(target.rel)

    assert not asked and deleted.unlinked == [other.rel]


def test_a_missing_note_raises_as_before(classic_wiki: Wiki) -> None:
    store = classic_wiki.store
    store.write_note(store.load(), title="One", summary="o", body="b", tags=[], source=WEB)
    complete(classic_wiki)
    for path in ["nope.md", "raw/x.md", ".llmw2/x.md", "../x.md", "index.md"]:
        with pytest.raises(FileNotFoundError):
            classic_wiki.delete_note(path)


def _links(wiki: Wiki, rel: str) -> list[str]:
    path = wiki._search.index.path
    assert path is not None
    with closing(sqlite3.connect(path)) as db:
        query = "SELECT l.target FROM links l JOIN docs d ON d.id = l.doc WHERE d.rel = ? ORDER BY l.target"
        return [t for (t,) in db.execute(query, (rel,))]


def test_the_index_follows_the_links_of_a_note_see_also_alone_included(classic_wiki: Wiki) -> None:
    store = classic_wiki.store
    root = store.load()
    raw = store.save_raw("Text.", title="Text", resource=None)
    c = store.write_note(root, title="C", summary="c", body="Sea.", tags=[], source=WEB)
    b = store.write_note(root, title="B", summary="b", body="Bee, and [c](c.md).", tags=[], source=WEB)
    a = store.write_note(root, title="A", summary="a", body="See [b](b.md).", tags=[], source={"resource": raw, "title": "t"})
    complete(classic_wiki)
    assert _links(classic_wiki, a.rel) == [raw, "b.md"] and _links(classic_wiki, b.rel) == ["c.md"]

    with store.lock():
        store.link(store.note_at(a.rel), store.note_at(c.rel))  # the See-also section only: the text the index hashes is the same
    assert _links(classic_wiki, a.rel) == [raw, "b.md", "c.md"]

    classic_wiki.delete_note(b.rel)
    assert _links(classic_wiki, a.rel) == [raw, "c.md"]
    assert _links(classic_wiki, b.rel) == []
    with closing(sqlite3.connect(classic_wiki._search.index.path)) as db:  # no row of a note taken out stays (a purged one's names)
        assert db.execute("SELECT COUNT(*) FROM links WHERE doc NOT IN (SELECT id FROM docs)").fetchone()[0] == 0


BODIES = [
    "See [a](a.md).",
    "See [a](./a.md#part) and [b](b.md).",
    "See [site](https://x.test/a.md) and [top](#a).",
    "Body.\n\n# See also\n\n* [A](a.md) - one\n* [B](b.md) - two\n",
    "Body [c](../notes/c.md).\n\n# See also\n\n* [A](/notes/a.md) - one\n\nA paragraph [b](b.md) by hand.\n",
    "Odd [x](a.md \"title\") and [y]( a.md) and [[a]] and ![img](a.md).",
    "# See also\n\n- [A](a.md)\n# See also\n\n* [B](b.md)\n",
]


@pytest.mark.parametrize("body", BODIES)
def test_link_targets_are_the_paths_drop_links_takes_out(body: str) -> None:
    rng = random.Random(body)
    pool = ["notes/a.md", "notes/b.md", "notes/c.md", "a.md", "x.test/a.md", "notes"]
    for _ in range(3):  # and the same body with its lines in another order
        targets = link_targets(body, "notes/here.md")
        for t in pool:
            assert (drop_links(body, "notes/here.md", {t}.__contains__) != body) == (t in targets), (body, t)
        lines = body.split("\n")
        rng.shuffle(lines)
        body = "\n".join(lines)


def test_a_purge_that_files_again_by_the_index_changes_what_the_purge_of_the_whole_tree_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A note fused from two documents purged by one of them: the other is filed again, and its old raw copy stays because another note
    still cites it; a note that stays with the title of the purged one keeps its log line. By the index and by the whole tree alike."""
    from tests.wiki.test_ingest_many import make_wiki
    from tests.wiki.test_purge import doc

    source = tmp_path / "source"
    wiki = make_wiki(source)
    wiki.ingest_many([doc("a.md", "red-1", "the purple falcon"), doc("b.md", "red-1", "the green heron"), doc("c.md", "blue-1", "a whale")])
    store = wiki.store
    fused = next(n for n in store.load().all_notes() if "red" in n.rel)
    old_b = store.origin_raws("test:acct:b.md")[-1]
    with store.lock():
        cites_b = {"resource": old_b, "title": "b"}
        twin = store.write_note(store.load(), title=fused.title, summary="t", body="Same title.", tags=[], source=cites_b)
        store.append_log("Created", "by hand", twin.rel, twin.title)
    complete(wiki)
    wiki.close()

    def purge_in_copy(target: Path, by_index: bool) -> tuple[dict, dict]:
        shutil.copytree(source, target)
        copy = make_wiki(target)
        with monkeypatch.context() as m:
            if not by_index:
                m.setattr(copy, "_links_indexed", lambda: False)
            report = copy.purge("test:acct:a.md")
        copy.close()
        assert report is not None
        return report.to_dict(), files_of(target)

    a, b = purge_in_copy(tmp_path / "a", True), purge_in_copy(tmp_path / "b", False)
    assert a == b
    assert a[0]["refiled"] == ["test:acct:b.md"] and (tmp_path / "a" / old_b.lstrip("/")).exists()
    assert f"[{twin.title}]({twin.rel})".encode() in a[1]["log.md"]
