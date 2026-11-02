# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A batch is one session of the store: the tree is read whole once, then only what changed. What a hand edit, or a note
another Wiki writes meanwhile, must never suffer: the store does not overwrite a note whose file changed since it read it."""

from __future__ import annotations

import math
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Item, Origin, Wiki
from llmw2.agents.librarian import IngestResult
from llmw2.agents.linking import RARE_MAX_SHARE, RARE_MIN_CAP, RARE_TOP, rare_scores
from llmw2.agents.steps_classic import incoming_values
from llmw2.bundle.store import WikiStore
from llmw2.bundle.tree import Folder, Note
from llmw2.errors import NoteChanged
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM
from tests.wiki.test_ingest_many import make_wiki

EDIT = "\n\nEdited by hand."


def source(n: int, topic: str) -> Item:
    return Item(f"doc-{n:02d}.md", data=f"topic: {topic}\nline of document {n}.".encode(), origin=Origin("test", "acct", f"doc-{n:02d}"))


def edit_in_place(path: Path) -> None:
    path.write_text(path.read_text(encoding="utf-8").rstrip("\n") + EDIT + "\n", encoding="utf-8")


def aged(store: WikiStore) -> None:
    """Every file and folder of the wiki an hour old: what a session trusts."""
    then = time.time_ns() - 3_600 * 10**9
    for path in [store.root, *store.root.rglob("*")]:
        os.utime(path, ns=(then, then))


# -- the session keeps the tree ----------------------------------------------------------------


def test_a_session_reuses_a_folder_that_did_not_change(tmp_path: Path) -> None:
    store = WikiStore(tmp_path / "w", actor="test")
    store.init()
    folder, _ = store.create_folder(store.load(), "area", "An area", log_it=False)
    store.write_note(folder, title="A", summary="s", body="Body.", tags=[], source={"resource": "/raw/x.md"})
    aged(store)
    with store.session():
        first = store.load()
        again = store.load()
        assert again.all_notes()[0] is first.all_notes()[0]
    assert store.load().all_notes()[0] is not first.all_notes()[0]  # outside a session each load is its own copy


def test_a_note_another_store_adds_is_seen_by_the_next_load(tmp_path: Path) -> None:
    store = WikiStore(tmp_path / "w", actor="test")
    store.init()
    folder, _ = store.create_folder(store.load(), "area", "An area", log_it=False)
    store.write_note(folder, title="A", summary="s", body="Body.", tags=[], source={"resource": "/raw/x.md"})
    aged(store)
    with store.session():
        store.load()
        other = WikiStore(tmp_path / "w", actor="other")
        there = other.load().subfolder("area")
        assert there is not None
        other.write_note(there, title="B", summary="s", body="Body.", tags=[], source={"resource": "/raw/y.md"})
        assert sorted(n.title for n in store.load().all_notes()) == ["A", "B"]


def test_a_note_edited_in_place_is_not_overwritten(tmp_path: Path) -> None:
    store = WikiStore(tmp_path / "w", actor="test")
    store.init()
    folder, _ = store.create_folder(store.load(), "area", "An area", log_it=False)
    one = store.write_note(folder, title="A", summary="s", body="Body.", tags=[], source={"resource": "/raw/x.md"})
    two = store.write_note(folder, title="B", summary="s", body="Body.", tags=[], source={"resource": "/raw/y.md"})
    aged(store)
    with store.session():
        notes = {n.rel: n for n in store.load().all_notes()}
        edit_in_place(one.path)  # the folder keeps its time: the session does not see it
        assert store.link(notes[one.rel], notes[two.rel])
    text = one.path.read_text(encoding="utf-8")
    assert EDIT.strip() in text and "(b.md)" in text  # the edit stays, and the link is added to what was there
    assert "(a.md)" in two.path.read_text(encoding="utf-8")


def test_a_merge_onto_a_changed_note_is_refused(tmp_path: Path) -> None:
    store = WikiStore(tmp_path / "w", actor="test")
    store.init()
    folder, _ = store.create_folder(store.load(), "area", "An area", log_it=False)
    note = store.write_note(folder, title="A", summary="s", body="Body.", tags=[], source={"resource": "/raw/x.md"})
    aged(store)
    with store.session():
        read = store.load().all_notes()[0]
        edit_in_place(note.path)
        with pytest.raises(NoteChanged):
            store.check_unchanged(read)
        with pytest.raises(NoteChanged):
            store.update_note(read, title="A", summary="s", body="New.", tags=[], source={"resource": "/raw/z.md"})
    assert EDIT.strip() in note.path.read_text(encoding="utf-8")


# -- a batch of documents -----------------------------------------------------------------------


class EditingLLM(ContentLLM):
    """A model that, when asked for `op`, first edits a note of the wiki in place: a hand edit in the middle of a document."""

    def __init__(self, op: str, path: Path) -> None:
        super().__init__(UsageTracker())
        self.op, self.path = op, path

    def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
        if op == self.op and self.path.exists() and EDIT.strip() not in self.path.read_text(encoding="utf-8"):
            edit_in_place(self.path)
        return super().chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)


def test_a_hand_edit_while_a_document_is_filed_survives_the_link(tmp_path: Path) -> None:
    bundle = tmp_path / "w"
    note = bundle / "red" / "about-red-0.md"
    wiki = make_wiki(bundle, llm=EditingLLM("librarian/relate", note))
    report = wiki.ingest_many([source(0, "red-0"), source(1, "red-1")])
    assert report.conflicts == 0 and all(isinstance(r, IngestResult) for _, r in report.results)
    text = note.read_text(encoding="utf-8")
    assert EDIT.strip() in text
    assert "About red-1" in text  # the link to the second document, added to the note as edited


def test_a_merge_onto_a_note_edited_meanwhile_fails_that_document_only(tmp_path: Path) -> None:
    bundle = tmp_path / "w"
    note = bundle / "red" / "about-red-0.md"
    wiki = make_wiki(bundle, llm=EditingLLM("librarian/merge", note))
    # doc-01 is about the same topic as doc-00: it would be merged into its note, which is edited while the merge is written
    report = wiki.ingest_many([source(0, "red-0"), source(1, "red-0"), source(2, "blue-0")])
    results = {i.filename: r for i, r in report.results}
    assert isinstance(results["doc-01.md"], NoteChanged) and report.conflicts == 1
    assert isinstance(results["doc-02.md"], IngestResult)  # the other documents go on
    assert EDIT.strip() in note.read_text(encoding="utf-8")
    assert len(list((bundle / "raw").glob("*.md"))) == 2  # no raw copy of the document that was not filed
    assert report.to_dict()["conflicts"] == 1
    again = wiki.ingest_many([source(1, "red-0")])  # the next run files it
    assert [r.action for _, r in again.results if isinstance(r, IngestResult)] == ["merged"]
    assert EDIT.strip() in note.read_text(encoding="utf-8")


def test_a_note_another_wiki_creates_meanwhile_stays_in_the_index(tmp_path: Path) -> None:
    wiki = make_wiki(tmp_path / "w")
    bundle = tmp_path / "w"
    made: list[str] = []

    def after(item: Item, result: IngestResult | Exception) -> None:
        if item.filename == "doc-00.md" and isinstance(result, IngestResult):
            other = Wiki(wiki.cfg, llm=wiki.llm, steps=wiki.steps)
            folder = other.store.load().find(result.folder)
            assert folder is not None
            fields: dict[str, Any] = {"summary": "s", "body": "Body.", "tags": [], "source": {"resource": "/raw/x.md"}}
            elsewhere = other.store.write_note(folder, title="Written elsewhere", **fields)
            made.append(elsewhere.rel)

    report = wiki.ingest_many([source(0, "red-0"), source(1, "red-1")], on_result=after)
    assert all(isinstance(r, IngestResult) for _, r in report.results)
    index = (bundle / "red" / "index.md").read_text(encoding="utf-8")
    assert "Written elsewhere" in index and "About red-0" in index and "About red-1" in index


def test_the_session_ends_with_the_batch(tmp_path: Path) -> None:
    wiki = make_wiki(tmp_path / "w")
    wiki.ingest_many([source(0, "red-0"), source(1, "red-1")])
    assert wiki.store._depth == 0 and not wiki.store._dirs


# -- rare values, kept between documents --------------------------------------------------------


def brute(root: Folder, note: Note, exclude: set[str]) -> list[tuple[str, float]]:
    """`rare_scores` as it was before the index: every note's values and their document frequency, from scratch."""
    notes = root.all_notes()
    held = {n.rel: frozenset(incoming_values(n.full_text())) for n in notes}
    df: dict[str, int] = {}
    for values in held.values():
        for v in values:
            df[v] = df.get(v, 0) + 1
    cap = max(RARE_MIN_CAP, RARE_MAX_SHARE * len(notes))
    weight = {v: math.log(len(notes) / c) for v, c in df.items() if c <= cap and not re.match(r"^(19|20)\d\d$", v)}
    mine = {v for v in held[note.rel] if v in weight}
    found = [(n.rel, sum(weight[v] for v in held[n.rel] & mine)) for n in notes if n.rel != note.rel and n.rel not in exclude]
    return sorted(((r, s) for r, s in found if s > 0), key=lambda item: (-item[1], item[0]))[:RARE_TOP]


def test_rare_scores_follow_the_notes_as_they_change() -> None:
    old = time.time_ns() - 3_600 * 10**9  # stamps old enough to be trusted
    folder = Folder(path=Path("/idx"), rel="")
    for i in range(30):
        body = f"Plain text {i}." + (f" Code QX-{i % 4} and ZK-4471." if i % 3 == 0 else "")
        stamp = (old, len(body), i + 1)
        folder.notes.append(Note(Path(f"/idx/n{i}.md"), f"n{i}.md", {"title": "T", "description": "D"}, body, folder, stamp))
    me = folder.notes[0]
    for step in range(4):
        got = [(n.rel, s) for n, s in rare_scores(folder, me, {"n3.md"})]
        assert [r for r, _ in got] == [r for r, _ in brute(folder, me, {"n3.md"})]
        assert [s for _, s in got] == pytest.approx([s for _, s in brute(folder, me, {"n3.md"})])
        if step == 0:  # a note gains the value: its file has a new stamp
            changed = folder.notes[7]
            changed.body += " Also ZK-4471."
            changed.stamp = (old, len(changed.body), 99)
        elif step == 1:  # a note is gone
            folder.notes.pop(9)
        else:  # a new one
            body = "New ZK-4471 and QX-1."
            folder.notes.append(Note(Path("/idx/new.md"), "new.md", {"title": "T", "description": "D"}, body, folder, (old, len(body), 77)))


# -- the session follows the store's own writes ---------------------------------------------------


def shape(root: Folder) -> list[tuple[Any, ...]]:
    """The tree as a reader sees it: every folder and note in order, with what they hold (a note the store wrote keeps the
    last line break of its body in memory, which a reading drops)."""
    out: list[tuple[Any, ...]] = []
    for folder in root.walk():
        out.append(("folder", folder.rel, folder.description, [f.rel for f in folder.subfolders], [n.rel for n in folder.notes]))
        out.extend(("note", n.rel, n.frontmatter, n.body.rstrip("\n"), n.folder.rel if n.folder else None) for n in folder.notes)
    return out


def test_the_tree_of_a_session_is_the_tree_a_fresh_store_reads(tmp_path: Path) -> None:
    wiki = make_wiki(tmp_path / "w")
    seen: list[int] = []

    def after(item: Item, result: IngestResult | Exception) -> None:
        assert wiki.store._depth
        assert shape(wiki.store.load()) == shape(WikiStore(tmp_path / "w", actor="fresh").load())
        if item.filename == "doc-03.md":  # someone else files a note between two documents: both stores see it
            other = WikiStore(tmp_path / "w", actor="other")
            there = other.load().find("red")
            assert there is not None
            fields: dict[str, Any] = {"summary": "s", "body": "Body.", "tags": [], "source": {"resource": "/raw/x.md"}}
            other.write_note(there, title="Aaa written elsewhere", **fields)
        seen.append(1)

    topics = ["red-0", "blue-1", "red-0", "red-3", "green-4", "blue-1", "red-6", "red-7"]
    report = wiki.ingest_many([source(n, t) for n, t in enumerate(topics)], on_result=after)
    assert all(isinstance(r, IngestResult) for _, r in report.results) and len(seen) == len(topics)
    names = [n.title for n in wiki.store.load().all_notes()]
    assert "Aaa written elsewhere" in names


def test_a_write_the_session_does_not_follow_lists_the_folder_again(tmp_path: Path) -> None:
    store = WikiStore(tmp_path / "w", actor="test")
    store.init()
    fields: dict[str, Any] = {"summary": "s", "body": "Body.", "tags": [], "source": {"resource": "/raw/x.md"}}
    folder, _ = store.create_folder(store.load(), "area", "An area", log_it=False)
    first = store.write_note(folder, title="Mmm", **fields)
    aged(store)
    with store.session():
        root = store.load()
        area = root.subfolder("area")
        assert area is not None
        store.write_note(area, title="Zzz", **fields)
        store.write_note(area, title="Aaa", **fields)
        assert [n.title for n in store.load().find("area").notes] == ["Aaa", "Mmm", "Zzz"]  # type: ignore[union-attr]
        store.delete_note("area/mmm.md")  # not followed: the folder is read again
        assert [n.title for n in store.load().find("area").notes] == ["Aaa", "Zzz"]  # type: ignore[union-attr]
        assert shape(store.load()) == shape(WikiStore(tmp_path / "w", actor="fresh").load())
    assert first.rel == "area/mmm.md"


def test_a_folder_the_store_wrote_in_is_not_listed_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WikiStore(tmp_path / "w", actor="test")
    store.init()
    fields: dict[str, Any] = {"summary": "s", "body": "Body.", "tags": [], "source": {"resource": "/raw/x.md"}}
    area, _ = store.create_folder(store.load(), "area", "An area", log_it=False)
    store.write_note(area, title="Mmm", **fields)
    with store.session():
        area = store.load().subfolder("area")  # type: ignore[assignment]
        listed: list[str] = []
        real = os.scandir
        monkeypatch.setattr(os, "scandir", lambda p: (listed.append(str(p)), real(p))[1])
        for title in ("Zzz", "Aaa", "Bbb"):
            note = store.write_note(area, title=title, **fields)
            store.append_log("Filed", title)
            store.archive_note(note)
            store.update_note(note, title=title, summary="t", body="New.", tags=[], source={"resource": "/raw/y.md"})
            store.load()
        assert listed == []
        monkeypatch.undo()
        assert [n.title for n in store.load().find("area").notes] == ["Aaa", "Bbb", "Mmm", "Zzz"]  # type: ignore[union-attr]
        assert shape(store.load()) == shape(WikiStore(tmp_path / "w", actor="fresh").load())
