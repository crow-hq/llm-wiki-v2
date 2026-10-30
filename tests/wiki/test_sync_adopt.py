# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: a sync recognises a document already filed by a single ingest and adopts its raw copy instead of filing it again."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2 import LocalFolderSource, Origin, SyncReport, Wiki, sync
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.origin import content_hash
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_sources import script, seed

TEXT = "Alpha."


@pytest.fixture
def wiki(classic_wiki: Wiki) -> Wiki:
    return seed(classic_wiki)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "docs"
    path.mkdir()
    (path / "a.md").write_text(TEXT, encoding="utf-8")
    return path


def key_of(folder: Path, item: str = "a.md") -> str:
    return f"local:{folder.resolve()}:{item}"


def raws(wiki: Wiki) -> list[Path]:
    return sorted((wiki.cfg.bundle / "raw").glob("*.md"))


def read(path: Path) -> OKFDocument:
    return OKFDocument.parse(path.read_text(encoding="utf-8"))


def note_count(wiki: Wiki) -> int:
    return len(wiki.tree().all_notes())


def single_ingest(wiki: Wiki, llm: FakeLLM) -> Path:
    """`a.md` filed on its own: one note, one raw copy with no origin. Returns the raw copy."""
    script(llm)
    wiki.ingest_file(TEXT.encode(), "a.md")
    [raw] = raws(wiki)
    assert "origin" not in read(raw).frontmatter
    script(llm, "Spare")  # if the sync files the file again anyway, the writer has something to answer
    return raw


# -- adoption ---------------------------------------------------------------------------------------------


def test_a_sync_of_a_file_already_filed_by_a_single_ingest_counts_it_as_unchanged(wiki: Wiki, llm: FakeLLM, folder: Path) -> None:
    single_ingest(wiki, llm)

    report = sync(wiki, LocalFolderSource(folder))

    assert report == SyncReport(unchanged=1)


def test_adoption_creates_no_note_and_writes_no_raw_copy(wiki: Wiki, llm: FakeLLM, folder: Path) -> None:
    raw = single_ingest(wiki, llm)
    notes = note_count(wiki)

    sync(wiki, LocalFolderSource(folder))

    assert note_count(wiki) == notes
    assert raws(wiki) == [raw]


def test_adoption_makes_no_writer_call(wiki: Wiki, llm: FakeLLM, folder: Path) -> None:
    single_ingest(wiki, llm)
    calls = len(llm.calls)

    sync(wiki, LocalFolderSource(folder))

    assert len(llm.calls) == calls


def test_adoption_records_the_syncs_origin_in_the_raw_copy(wiki: Wiki, llm: FakeLLM, folder: Path) -> None:
    raw = single_ingest(wiki, llm)

    sync(wiki, LocalFolderSource(folder))

    origin = read(raw).frontmatter["origin"]
    assert (origin["source"], origin["account"], origin["id"]) == ("local", str(folder.resolve()), "a.md")
    record = wiki.origin(key_of(folder))
    assert record is not None and not record.removed


def test_adoption_leaves_the_raw_copys_text_and_hash_unchanged(wiki: Wiki, llm: FakeLLM, folder: Path) -> None:
    raw = single_ingest(wiki, llm)
    before = read(raw)

    sync(wiki, LocalFolderSource(folder))

    after = read(raw)
    assert after.body == before.body
    assert after.frontmatter["hash"] == before.frontmatter["hash"] == content_hash(TEXT)


def test_adoption_leaves_the_wiki_without_problems(wiki: Wiki, llm: FakeLLM, folder: Path) -> None:
    single_ingest(wiki, llm)

    sync(wiki, LocalFolderSource(folder))

    assert wiki.check() == []


# -- a later sync -----------------------------------------------------------------------------------------


def test_a_second_sync_of_the_same_folder_writes_no_raw_copy_and_makes_no_writer_call(wiki: Wiki, llm: FakeLLM, folder: Path) -> None:
    single_ingest(wiki, llm)
    sync(wiki, LocalFolderSource(folder))
    files, calls = raws(wiki), len(llm.calls)

    report = sync(wiki, LocalFolderSource(folder))

    assert (report.created, report.merged, report.errors) == (0, 0, [])
    assert raws(wiki) == files and len(llm.calls) == calls


def test_a_sync_that_starts_from_scratch_still_recognises_the_adopted_raw_copy_by_origin(wiki: Wiki, llm: FakeLLM, folder: Path) -> None:
    single_ingest(wiki, llm)
    sync(wiki, LocalFolderSource(folder))
    for cursor in (wiki.cfg.bundle / ".llmw2" / "sync").glob("*.json"):
        cursor.unlink()  # the next sync lists every file again
    files, calls, notes = raws(wiki), len(llm.calls), note_count(wiki)

    report = sync(wiki, LocalFolderSource(folder))

    assert (report.created, report.merged, report.errors) == (0, 0, [])
    assert raws(wiki) == files and len(llm.calls) == calls and note_count(wiki) == notes


# -- not adopted ------------------------------------------------------------------------------------------


def test_a_file_with_a_different_text_is_ingested_as_before(wiki: Wiki, llm: FakeLLM, folder: Path) -> None:
    raw = single_ingest(wiki, llm)
    (folder / "a.md").write_text("Alpha, but rewritten.", encoding="utf-8")
    script(llm, "Alpha rewritten")

    report = sync(wiki, LocalFolderSource(folder))

    assert report.created + report.merged == 1 and report.unchanged == 0
    assert len(raws(wiki)) == 2
    assert "origin" not in read(raw).frontmatter  # the first raw copy was not adopted
    [new] = [p for p in raws(wiki) if p != raw]
    assert read(new).frontmatter["hash"] == content_hash("Alpha, but rewritten.")
    assert read(new).frontmatter["origin"]["id"] == "a.md"


def test_a_raw_copy_with_an_origin_of_another_key_is_not_adopted(wiki: Wiki, llm: FakeLLM, folder: Path) -> None:
    script(llm)
    other = Origin("onedrive", "me", "item-1")
    wiki.ingest(TEXT, origin=other)
    [raw] = raws(wiki)
    script(llm, "Alpha again")

    report = sync(wiki, LocalFolderSource(folder))

    assert report.created + report.merged == 1 and report.unchanged == 0
    assert len(raws(wiki)) == 2
    assert read(raw).frontmatter["origin"]["id"] == "item-1"  # still the other origin's
    assert wiki.origin(other.key) is not None


def test_a_raw_copy_that_no_live_note_cites_is_not_adopted(wiki: Wiki, llm: FakeLLM, folder: Path) -> None:
    orphan = Path(wiki.cfg.bundle / wiki.store.save_raw(TEXT, title="a", resource=None).lstrip("/"))
    assert read(orphan).body.strip() == TEXT
    script(llm)

    report = sync(wiki, LocalFolderSource(folder))

    assert report.created + report.merged == 1 and report.unchanged == 0
    assert "origin" not in read(orphan).frontmatter
    assert len(raws(wiki)) == 2
