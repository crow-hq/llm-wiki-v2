# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`WikiStore.find_origin`, `save_raw`'s seq and `mark_origin_removed` against files that change under the store.

The answers must equal a brute-force reading of raw/ and the note tree, however the files got there:
the store itself, a second store on the same bundle, or a hand edit. A query with nothing changed parses nothing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Origin
from llmw2.bundle.document import OKFDocument, OKFDocumentError
from llmw2.bundle.origin import OriginRecord
from llmw2.bundle.store import WikiStore
from llmw2.bundle.tree import Folder

ACTOR = "llmw2/test"
A, B, C = (Origin("local", "", name) for name in ("a.md", "b.md", "c.md"))
KEYS = [A.key, B.key, C.key, "local::nope.md"]


@pytest.fixture
def store(bundle: Path) -> WikiStore:
    wiki = WikiStore(bundle, actor=ACTOR)
    wiki.init()
    return wiki


def _put(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _raw(store: WikiStore, origin: Origin, title: str = "Doc") -> str:
    return store.save_raw(f"text of {title}", title=title, resource=None, origin=origin)


def _note(store: WikiStore, folder: Folder, title: str, *raws: str) -> str:
    """A note citing `raws` (the first at creation, the others through update_note); its bundle path."""
    note = store.write_note(folder, title=title, summary="S.", body="Body.", tags=[], source={"resource": raws[0], "title": title})
    for extra in raws[1:]:
        store.update_note(note, title=title, summary="S.", body="Body.", tags=[], source={"resource": extra, "title": title})
    return note.rel


def _note_text(*raws: str, **frontmatter: Any) -> str:
    fm = {"type": "Note", "title": "Hand", "sources": [{"id": f"s{n}", "resource": r} for n, r in enumerate(raws, 1)]}
    return OKFDocument(fm | frontmatter, "Body.\n").serialize()


def _touch(path: Path, text: str) -> None:
    """Rewrite a file by hand: the size changes, and the mtime moves on even on a coarse clock."""
    before = path.stat().st_mtime_ns
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(before + 10**9, before + 10**9))


def _bump(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))


def _folder(store: WikiStore, name: str = "finance") -> Folder:
    return store.create_folder(store.load().find("/"), name, "Money.")[0]


def _parse(path: Path) -> dict[str, Any] | None:
    try:
        return OKFDocument.parse(path.read_text(encoding="utf-8")).frontmatter
    except OKFDocumentError:
        return None


def expected(bundle: Path, key: str) -> OriginRecord | None:
    """The record the rules give, read straight from the files."""
    copies = []
    for path in (bundle / "raw").glob("*.md"):
        fm = _parse(path)
        origin = fm.get("origin") if fm else None
        has_key = isinstance(origin, dict) and all(isinstance(origin.get(k), str) for k in ("source", "account", "id"))
        if has_key and f"{origin['source']}:{origin['account']}:{origin['id']}" == key:
            copies.append((path, fm))

    def order(copy: tuple[Path, dict[str, Any]]) -> tuple[int, str, str]:
        seq = copy[1].get("seq")
        return (seq if isinstance(seq, int) else 0, str(copy[1].get("generated", {}).get("at", "")), copy[0].stem)

    copies.sort(key=order)
    rels = [f"/raw/{p.name}" for p, _ in copies]
    newest: dict[str, int] = {}
    for path in sorted(bundle.rglob("*.md")):
        rel = path.relative_to(bundle)
        if rel.parts[0] == "raw" or any(p.startswith(".") for p in rel.parts) or path.name in ("index.md", "log.md"):
            continue
        fm = _parse(path)
        sources = fm.get("sources") if fm else None
        entries = sources if isinstance(sources, list) else []
        cited = [rels.index(s["resource"]) for s in entries if isinstance(s, dict) and s.get("resource") in rels]
        if cited:
            newest[rel.as_posix()] = max(cited)
    if not newest:
        return None
    top = max(newest.values())
    latest = copies[top][1]
    return OriginRecord(
        raw=rels[top],
        hash=str(latest["hash"]) if latest.get("hash") else None,
        version=str(latest["origin"]["version"]) if latest["origin"].get("version") else None,
        removed=bool(latest.get("removed")),
        notes=sorted(newest, key=lambda r: (newest[r], r)),
    )


def assert_matches_reference(store: WikiStore, keys: list[str] = KEYS) -> None:
    for key in keys:
        assert store.find_origin(key) == expected(store.root, key), key


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The text of every `OKFDocument.parse` call from now on."""
    seen: list[str] = []
    real = OKFDocument.parse.__func__  # type: ignore[attr-defined]

    def counting(cls: type[OKFDocument], text: str) -> OKFDocument:
        seen.append(text)
        return real(cls, text)

    monkeypatch.setattr(OKFDocument, "parse", classmethod(counting))
    return seen


# -- equivalence with a brute-force reading -----------------------------------------------


def test_every_key_matches_a_brute_force_reading_of_the_files(store: WikiStore, bundle: Path) -> None:
    finance, ops = _folder(store), _folder(store, "ops")
    a1, a2, a3 = (_raw(store, Origin(A.source, A.account, A.id, version=f"v{n}"), f"a{n}") for n in (1, 2, 3))
    b1, b2 = _raw(store, B, "b1"), _raw(store, B, "b2")
    c1 = _raw(store, C, "c1")
    _raw(store, Origin("local", "", "orphan-only.md"), "orphan")  # a key with no cited copy at all
    _raw(store, A, "a-orphan-newest")  # an orphan newer than every cited copy of A
    store.save_raw("plain", title="Plain", resource=None)  # no origin
    _note(store, finance, "Both versions", a1, a2)
    _note(store, finance, "Only first", a1)
    _note(store, ops, "Third", a3)
    _note(store, ops, "Mixed", b1, c1)  # cites a copy of another key too
    _note(store, ops, "B older", b1)
    store.mark_origin_removed(C.key)
    _note(store, finance, "B newer", b2)
    store.mark_origin_removed(B.key)
    assert_matches_reference(store, [*KEYS, "local::orphan-only.md"])

    record = store.find_origin(A.key)
    assert record is not None and (record.raw, record.version) == (a3, "v3")
    assert record.notes == ["finance/only-first.md", "finance/both-versions.md", "ops/third.md"]
    record_b = store.find_origin(B.key)
    assert record_b is not None and record_b.removed
    assert store.find_origin("local::orphan-only.md") is None


def test_raw_text_and_hash_of_the_latest_cited_copy_are_reported(store: WikiStore) -> None:
    folder = _folder(store)
    first = _raw(store, Origin(A.source, A.account, A.id, version="v1"), "one")
    second = _raw(store, Origin(A.source, A.account, A.id, version="v2"), "two")
    _note(store, folder, "N", first, second)

    record = store.find_origin(A.key)

    assert record is not None
    assert (record.raw, record.version) == (second, "v2")
    assert record.hash == _parse(store.root / second.lstrip("/"))["hash"]  # type: ignore[index]


# -- external changes, seen from a warm index ---------------------------------------------


def _warm(store: WikiStore) -> tuple[str, str]:
    """One folder, one raw copy of A and a note citing it; the index has seen them."""
    folder = _folder(store)
    raw = _raw(store, A)
    rel = _note(store, folder, "Cites A", raw)
    assert store.find_origin(A.key) is not None
    return raw, rel


def test_a_note_hand_edited_to_stop_citing_the_copy_drops_the_record(store: WikiStore, bundle: Path) -> None:
    _, rel = _warm(store)

    _touch(bundle / rel, _note_text("/raw/elsewhere.md", extra="a longer file than before"))

    assert store.find_origin(A.key) is None
    assert_matches_reference(store)


def test_a_note_hand_edited_to_cite_a_newer_copy_moves_the_record(store: WikiStore, bundle: Path) -> None:
    raw, rel = _warm(store)
    newer = _raw(store, Origin(A.source, A.account, A.id, version="v2"), "newer")  # saved after the index warmed up
    assert store.find_origin(A.key).raw == raw  # type: ignore[union-attr]

    _touch(bundle / rel, _note_text(raw, newer))

    record = store.find_origin(A.key)
    assert record is not None and (record.raw, record.version) == (newer, "v2")
    assert_matches_reference(store)


def test_a_raw_copy_deleted_by_hand_is_gone(store: WikiStore, bundle: Path) -> None:
    older = _raw(store, Origin(A.source, A.account, A.id, version="v1"), "old")
    newer = _raw(store, Origin(A.source, A.account, A.id, version="v2"), "new")
    _note(store, _folder(store), "Cites both", older, newer)
    assert store.find_origin(A.key).version == "v2"  # type: ignore[union-attr]

    (bundle / newer.lstrip("/")).unlink()

    record = store.find_origin(A.key)
    assert record is not None and (record.raw, record.version) == (older, "v1")
    (bundle / older.lstrip("/")).unlink()
    assert store.find_origin(A.key) is None


def test_a_raw_copy_rewritten_by_hand_is_reread(store: WikiStore, bundle: Path) -> None:
    raw, _ = _warm(store)
    path = bundle / raw.lstrip("/")

    _touch(path, path.read_text(encoding="utf-8").replace("hash: sha256:", "removed: 2026-10-01T00:00:00+00:00\nhash: sha256:"))

    record = store.find_origin(A.key)
    assert record is not None and record.removed
    assert_matches_reference(store)


def test_a_note_added_by_hand_cites_a_copy_that_was_an_orphan(store: WikiStore, bundle: Path) -> None:
    raw = _raw(store, A)
    assert store.find_origin(A.key) is None

    _put(bundle / "hand" / "deep" / "note.md", _note_text(raw))

    record = store.find_origin(A.key)
    assert record is not None and (record.raw, record.notes) == (raw, ["hand/deep/note.md"])
    assert_matches_reference(store)


def test_a_note_deleted_by_hand_uncites_its_copy(store: WikiStore, bundle: Path) -> None:
    _, rel = _warm(store)

    (bundle / rel).unlink()

    assert store.find_origin(A.key) is None


def test_a_second_store_saving_a_copy_and_a_note_is_seen(store: WikiStore, bundle: Path) -> None:
    first, _ = _warm(store)
    other = WikiStore(bundle, actor="llmw2/other")
    newer = _raw(other, Origin(A.source, A.account, A.id, version="v2"), "newer")
    assert store.find_origin(A.key).raw == first  # type: ignore[union-attr]  # an orphan: not yet cited

    _note(other, other.load().find("finance"), "Second", newer)  # type: ignore[arg-type]

    record = store.find_origin(A.key)
    assert record is not None and (record.raw, record.version) == (newer, "v2")
    assert record.notes[-1] == "finance/second.md"
    assert_matches_reference(store)


def test_a_second_store_marking_the_key_removed_is_seen(store: WikiStore, bundle: Path) -> None:
    _warm(store)
    assert store.find_origin(A.key).removed is False  # type: ignore[union-attr]

    WikiStore(bundle, actor="llmw2/other").mark_origin_removed(A.key)

    record = store.find_origin(A.key)
    assert record is not None and record.removed
    assert_matches_reference(store)


def test_a_second_store_deleting_a_note_is_seen(store: WikiStore, bundle: Path) -> None:
    _, rel = _warm(store)

    WikiStore(bundle, actor="llmw2/other").delete_note(rel)

    assert store.find_origin(A.key) is None


def test_marking_removed_after_an_external_change_stamps_the_copies_the_disk_has(store: WikiStore, bundle: Path) -> None:
    _warm(store)
    other = WikiStore(bundle, actor="llmw2/other")
    extra = _raw(other, Origin(A.source, A.account, A.id, version="v2"), "extra")  # an orphan the first store never saw

    record = store.mark_origin_removed(A.key)

    assert record is not None and record.removed
    assert _parse(bundle / extra.lstrip("/"))["removed"]  # type: ignore[index]
    assert store.mark_origin_removed("local::nope.md") is None


def test_changes_that_keep_the_size_are_seen_when_the_mtime_moves(store: WikiStore, bundle: Path) -> None:
    raw, rel = _warm(store)
    path = bundle / rel
    same_size = path.read_text(encoding="utf-8").replace(raw, raw.replace("/raw/", "/rav/"))

    _touch(path, same_size)
    _bump(path)

    assert len(same_size) == len(path.read_text(encoding="utf-8"))
    assert store.find_origin(A.key) is None


# -- no useless parsing ---------------------------------------------------------------------


def test_a_second_query_with_nothing_changed_parses_nothing(store: WikiStore, parses: list[str]) -> None:
    _warm(store)
    store.find_origin(A.key)
    parses.clear()

    for key in KEYS:
        store.find_origin(key)

    assert parses == []


def test_one_raw_saved_by_another_store_costs_one_parse(store: WikiStore, bundle: Path, parses: list[str]) -> None:
    _warm(store)
    other = WikiStore(bundle, actor="llmw2/other")
    _raw(other, Origin(A.source, A.account, A.id, version="v2"), "newer")
    parses.clear()

    store.find_origin(A.key)
    store.find_origin(A.key)

    assert len(parses) == 1


def test_one_note_written_by_another_store_costs_one_parse(store: WikiStore, bundle: Path, parses: list[str]) -> None:
    raw, _ = _warm(store)
    other = WikiStore(bundle, actor="llmw2/other")
    _note(other, other.load().find("finance"), "Another", raw)  # type: ignore[arg-type]
    parses.clear()

    record = store.find_origin(A.key)

    assert record is not None and len(record.notes) == 2
    assert len(parses) == 1


def test_one_note_edited_by_hand_costs_one_parse(store: WikiStore, bundle: Path, parses: list[str]) -> None:
    raw, rel = _warm(store)
    parses.clear()
    _touch(bundle / rel, _note_text(raw, extra="more"))

    store.find_origin(A.key)

    assert len(parses) == 1


def test_a_deleted_file_costs_no_parse(store: WikiStore, bundle: Path, parses: list[str]) -> None:
    _, rel = _warm(store)
    parses.clear()
    (bundle / rel).unlink()

    assert store.find_origin(A.key) is None
    assert parses == []


# -- seq ----------------------------------------------------------------------------------------


def test_two_stores_saving_alternately_number_the_copies_in_save_order(store: WikiStore, bundle: Path) -> None:
    other = WikiStore(bundle, actor="llmw2/other")
    paths = [_raw(s, A, f"copy{n}") for n, s in enumerate([store, other, store, store, other, other], 1)]

    assert [_parse(bundle / p.lstrip("/"))["seq"] for p in paths] == [1, 2, 3, 4, 5, 6]  # type: ignore[index]


def test_seq_counts_per_key_and_ignores_cited_or_not(store: WikiStore, bundle: Path) -> None:
    other = WikiStore(bundle, actor="llmw2/other")
    a1, b1, a2, b2 = _raw(store, A), _raw(other, B), _raw(other, A), _raw(store, B)
    _note(store, _folder(store), "Cites a1", a1)

    seqs = [_parse(bundle / p.lstrip("/"))["seq"] for p in (a1, b1, a2, b2)]  # type: ignore[index]

    assert seqs == [1, 1, 2, 2]


def test_seq_continues_after_a_hand_deleted_copy(store: WikiStore, bundle: Path) -> None:
    first, second = _raw(store, A, "one"), _raw(store, A, "two")
    assert store.find_origin(A.key) is None

    (bundle / second.lstrip("/")).unlink()
    third = _raw(store, A, "three")

    assert (_parse(bundle / first.lstrip("/"))["seq"], _parse(bundle / third.lstrip("/"))["seq"]) == (1, 2)  # type: ignore[index]


# -- files that are not what they look like ------------------------------------------------------


def test_malformed_and_foreign_files_are_ignored(store: WikiStore, bundle: Path) -> None:
    raw = _raw(store, A)
    rel = _note(store, _folder(store), "Real", raw)
    before = store.find_origin(A.key)
    raw_dir = bundle / "raw"
    _put(raw_dir / "broken.md", "---\ntype: Source\norigin: [unclosed\n---\nbody")
    _put(raw_dir / "no-frontmatter.md", "just text, no frontmatter")
    _put(raw_dir / "no-origin.md", "---\ntype: Source\ntitle: Old\n---\nold text")
    _put(raw_dir / "bad-origin.md", "---\ntype: Source\norigin:\n  source: local\n---\nmissing account and id")
    _put(raw_dir / "notes.txt", "---\norigin: {source: local, account: '', id: a.md}\n---\n")
    _put(raw_dir / ".hidden.md", _note_text())
    _put(bundle / "finance" / "broken.md", "---\ntitle: [unclosed\n---\nbody")
    _put(bundle / "finance" / "plain.txt", _note_text(raw))
    _put(bundle / "finance" / ".hidden.md", _note_text(raw))
    _put(bundle / ".hidden-dir" / "note.md", _note_text(raw))
    _put(bundle / "finance" / "index.md", _note_text(raw))
    _put(bundle / "index.md", _note_text(raw))
    _put(bundle / "log.md", _note_text(raw))

    assert store.find_origin(A.key) == before
    assert before is not None and before.notes == [rel]
    assert_matches_reference(store)
    assert store.find_origin(B.key) is None


def test_files_turning_malformed_or_well_formed_by_hand_are_followed(store: WikiStore, bundle: Path) -> None:
    raw, rel = _warm(store)

    _touch(bundle / rel, "---\ntitle: [unclosed\n---\nbody that is longer than before")
    assert store.find_origin(A.key) is None

    _touch(bundle / rel, _note_text(raw))
    assert store.find_origin(A.key) is not None


# -- which notes count ----------------------------------------------------------------------------


def test_a_note_inside_the_raw_tree_is_not_a_note(store: WikiStore, bundle: Path) -> None:
    raw = _raw(store, A)
    _put(bundle / "raw" / "nested" / "note.md", _note_text(raw))
    _put(bundle / "raw" / "cites.md", _note_text(raw))

    assert store.find_origin(A.key) is None
    assert_matches_reference(store)


def test_a_note_two_folders_deep_cites_a_copy(store: WikiStore, bundle: Path) -> None:
    top = _folder(store, "top")
    mid, _ = store.create_folder(top, "mid", "Middle.")
    leaf, _ = store.create_folder(mid, "leaf", "Leaf.")
    raw = _raw(store, A)
    rel = _note(store, leaf, "Deep", raw)

    record = store.find_origin(A.key)

    assert rel == "top/mid/leaf/deep.md"
    assert record is not None and record.notes == [rel]
    assert_matches_reference(store)


def test_notes_are_ordered_by_the_newest_copy_they_cite_then_by_path(store: WikiStore) -> None:
    folder = _folder(store)
    first = _raw(store, Origin(A.source, A.account, A.id, version="v1"), "one")
    second = _raw(store, Origin(A.source, A.account, A.id, version="v2"), "two")
    zed = _note(store, folder, "Zed", first)
    mid = _note(store, folder, "Mid", first, second)
    alpha = _note(store, folder, "Alpha", second)

    record = store.find_origin(A.key)

    assert record is not None and record.notes == [zed, alpha, mid]
