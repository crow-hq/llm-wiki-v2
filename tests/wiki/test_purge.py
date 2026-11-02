# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki.purge`: a document taken out for good. Its raw copies, archived versions and notes go; a note it was fused into goes too,
and the other documents of that note are filed again from their raw copies, so no sentence of it survives in a text a model wrote."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2 import Item, LocalFolderSource, Origin, Wiki, cli, sync
from llmw2.bundle.tree import LOG
from llmw2.errors import ModelError
from tests.wiki.fakes import ContentLLM
from tests.wiki.test_ingest_many import make_wiki
from tests.wiki.test_sheets import workbook

SECRET = "the purple falcon lands at midnight"
OTHER = "the green heron sleeps at noon"
KEY_A, KEY_B, KEY_C = "test:acct:a.md", "test:acct:b.md", "test:acct:c.md"


def doc(name: str, topic: str, line: str, version: str = "v1") -> Item:
    return Item(name, text=f"topic: {topic}\n{line}", origin=Origin("test", "acct", name, version=version))


def everything(wiki: Wiki) -> str:
    """Every text file of the bundle but the model's cache, read as one text."""
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(wiki.cfg.bundle.rglob("*"))
        if p.is_file() and ".llmw2" not in p.relative_to(wiki.cfg.bundle).parts
    )


def raw_keys(wiki: Wiki) -> list[str]:
    return sorted(k for k in (wiki.store.origin_keys("")) if k)


def log_lines(wiki: Wiki) -> list[str]:
    return [line for line in (wiki.cfg.bundle / LOG).read_text(encoding="utf-8").splitlines() if line.startswith("* **")]


def file_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture
def wiki(bundle: Path) -> Wiki:
    return make_wiki(bundle)


# -- a note of one origin ------------------------------------------------------------------------------------------


def test_purge_deletes_the_origins_raw_copy_and_note_and_leaves_the_other_notes_as_they_were(wiki: Wiki) -> None:
    # ARRANGE
    wiki.ingest_many([doc("a.md", "red-1", SECRET), doc("b.md", "blue-1", OTHER)])
    notes = wiki.store.load().all_notes()
    blue, red = next(n for n in notes if "blue" in n.rel), next(n for n in notes if "red" in n.rel)
    wiki.store.link(blue, red)
    assert "About red-1" in file_text(blue.path)
    before_blue = file_text(blue.path)

    # ACT
    report = wiki.purge(KEY_A)

    # ASSERT
    assert report is not None and len(report.notes) == 1 and len(report.sources) == 1
    assert report.refiled == [] and report.errors == [] and report.stopped is None
    assert SECRET not in everything(wiki) and OTHER in everything(wiki)
    assert raw_keys(wiki) == [KEY_B] and [n.rel for n in wiki.store.load().all_notes()] == [blue.rel]
    assert wiki.check() == []
    assert "About red-1" not in file_text(blue.path)
    assert file_text(blue.path) != before_blue and report.unlinked == [blue.rel]  # the See-also entry to the deleted note went
    assert "red-1" not in file_text(wiki.cfg.bundle / "index.md")


def test_purge_of_an_unknown_key_is_none_and_changes_nothing(wiki: Wiki) -> None:
    # ARRANGE
    wiki.ingest_many([doc("a.md", "red-1", SECRET)])
    before = everything(wiki)

    # ACT / ASSERT
    assert wiki.purge("test:acct:nothing.md") is None
    assert everything(wiki) == before


def test_a_dry_run_says_what_would_go_and_changes_nothing(wiki: Wiki) -> None:
    # ARRANGE
    wiki.ingest_many([doc("a.md", "red-1", SECRET), doc("b.md", "red-1", OTHER)])
    before = everything(wiki)

    # ACT
    report = wiki.purge(KEY_A, dry_run=True)

    # ASSERT
    assert report is not None and len(report.notes) == 1 and len(report.sources) == 1 and report.refiled == [KEY_B]
    assert report.filed == [] and everything(wiki) == before


# -- a note fused from several origins -----------------------------------------------------------------------------


def test_a_fused_note_is_deleted_and_its_other_origins_are_filed_again_from_their_raw_copies(wiki: Wiki) -> None:
    # ARRANGE: a and b are about one topic, so one note holds both; c is elsewhere
    wiki.ingest_many([doc("a.md", "red-1", SECRET), doc("b.md", "red-1", OTHER), doc("c.md", "blue-1", "the blue whale sings")])
    fused = wiki.store.load().all_notes()
    assert len(fused) == 2 and SECRET in everything(wiki) and OTHER in everything(wiki)

    # ACT
    report = wiki.purge(KEY_A)

    # ASSERT
    assert report is not None and report.refiled == [KEY_B] and len(report.filed) == 1 and report.errors == []
    text = everything(wiki)
    assert SECRET not in text and OTHER in text and "the blue whale sings" in text
    assert raw_keys(wiki) == [KEY_B, KEY_C]  # one raw copy each: b's old one went when the new one was filed
    assert len(wiki.store.origin_raws(KEY_B)) == 1 and wiki.origin(KEY_B) is not None
    assert len(wiki.store.load().all_notes()) == 2 and wiki.check() == []
    assert report.sources and not any((wiki.cfg.bundle / s.lstrip("/")).exists() for s in report.sources)


def test_the_other_origins_are_filed_again_on_the_folders_there_are(wiki: Wiki) -> None:
    # ARRANGE
    wiki.ingest_many([doc("a.md", "red-1", SECRET), doc("b.md", "red-1", OTHER)])
    folders = sorted(f.label for f in wiki.store.load().subfolders)

    # ACT
    report = wiki.purge(KEY_A)

    # ASSERT: no folder made or removed, and no model asked to rewrite a text (only the other document is summarized)
    assert report is not None and sorted(f.label for f in wiki.store.load().subfolders) == folders
    assert isinstance(wiki.llm, ContentLLM) and wiki.llm.ops.count("librarian/summarize") == 3


def test_a_fused_note_is_deleted_even_when_the_purged_origin_is_the_second_one(wiki: Wiki) -> None:
    # ARRANGE
    wiki.ingest_many([doc("a.md", "red-1", SECRET), doc("b.md", "red-1", OTHER)])

    # ACT
    report = wiki.purge(KEY_B)

    # ASSERT
    assert report is not None and report.refiled == [KEY_A]
    assert OTHER not in everything(wiki) and SECRET in everything(wiki) and wiki.check() == []


def test_a_document_marked_removed_is_purged_as_any_other(wiki: Wiki) -> None:
    # ARRANGE
    wiki.ingest_many([doc("a.md", "red-1", SECRET), doc("b.md", "red-1", OTHER)])
    wiki.mark_removed(KEY_A)

    # ACT
    report = wiki.purge(KEY_A)

    # ASSERT
    assert report is not None and SECRET not in everything(wiki) and wiki.check() == []


def test_when_filing_the_others_again_fails_their_raw_copies_stay_and_the_purged_text_is_gone_all_the_same(bundle: Path) -> None:
    # ARRANGE
    wiki = make_wiki(bundle, llm=None)
    wiki.ingest_many([doc("a.md", "red-1", SECRET), doc("b.md", "red-1", OTHER)])
    assert isinstance(wiki.llm, ContentLLM)
    wiki.llm.fail_on[OTHER] = ModelError("down")

    # ACT
    report = wiki.purge(KEY_A)

    # ASSERT
    assert report is not None and report.stopped is not None
    assert SECRET not in everything(wiki)
    assert len(wiki.store.origin_raws(KEY_B)) == 1 and wiki.origin(KEY_B) is None  # its raw copy stays, no note cites it
    wiki.llm.fail_on.clear()
    assert len(wiki.rebuild(wiki.raw_items()).results) == 1 and wiki.origin(KEY_B) is not None  # a rebuild files it again


# -- sheets and the archive ----------------------------------------------------------------------------------------


def test_a_spreadsheet_is_purged_with_all_its_sheets(wiki: Wiki) -> None:
    # ARRANGE
    origin = Origin("test", "acct", "plan.xlsx", version="v1")
    wiki.ingest_many([Item("plan.xlsx", fetch=lambda: workbook(North=2, South=2), origin=origin), doc("a.md", "blue-1", OTHER)])
    assert wiki.store.origin_keys("test:acct:plan.xlsx#") == {"test:acct:plan.xlsx#North", "test:acct:plan.xlsx#South"}

    # ACT
    report = wiki.purge("test:acct:plan.xlsx")

    # ASSERT
    assert report is not None and len(report.notes) == 1 and len(report.sources) == 2  # the fake fuses both sheets in one note
    assert wiki.store.origin_keys("test:acct:plan.xlsx#") == set()
    assert "North-1" not in everything(wiki) and OTHER in everything(wiki) and wiki.check() == []


def test_the_archived_versions_of_the_notes_deleted_go_too(wiki: Wiki) -> None:
    # ARRANGE: a revision archives the note as it was
    wiki.ingest_many([doc("a.md", "red-1", SECRET), doc("b.md", "blue-1", OTHER)])
    for note in wiki.store.load().all_notes():
        wiki.store.archive_note(note)
    archive = wiki.cfg.bundle / ".archive"
    assert list(archive.rglob("*.md")), "the second version should have archived the first"

    # ACT
    report = wiki.purge(KEY_A)

    # ASSERT
    assert report is not None and len(report.archived) >= 1
    assert len(list(archive.rglob("*.md"))) == 1 and "falcon" not in everything(wiki) and OTHER in everything(wiki) and wiki.check() == []


def test_the_archived_version_of_a_note_that_moved_goes_with_it(wiki: Wiki) -> None:
    # ARRANGE
    wiki.ingest_many([doc("a.md", "red-1", SECRET)])
    note = wiki.store.load().all_notes()[0]
    wiki.store.archive_note(note)
    wiki.create_folder("/", "elsewhere", "Somewhere else.")
    wiki.move_note(note.rel, "/elsewhere")

    # ACT
    wiki.purge(KEY_A)

    # ASSERT
    assert not list((wiki.cfg.bundle / ".archive").rglob("*.md")) and "falcon" not in everything(wiki)


# -- the log -------------------------------------------------------------------------------------------------------


def test_the_log_lines_naming_what_was_deleted_say_purged_and_one_purged_line_is_added_without_the_title(wiki: Wiki) -> None:
    # ARRANGE
    wiki.ingest_many([doc("a.md", "red-1", SECRET), doc("b.md", "blue-1", OTHER)])
    created = [line for line in log_lines(wiki) if "red-1" in line]
    assert created, "the log should name the note about red-1"
    count = len(log_lines(wiki))

    # ACT
    report = wiki.purge(KEY_A, author="ada")

    # ASSERT
    lines = log_lines(wiki)
    assert report is not None and report.log_lines >= 1 and len(lines) == count + 1
    assert not any("red-1" in line or "a.md" in line or "falcon" in line for line in lines)
    assert any(line.startswith("* **Created**: [purged]") for line in lines)
    assert lines[0].startswith("* **Purged**: ") and lines[0].endswith(" · by ada") and "red" not in lines[0].lower()
    assert any("blue-1" in line for line in lines)  # the lines about the other note are as they were


def test_a_log_line_keeps_its_author_when_it_says_purged(bundle: Path) -> None:
    # ARRANGE
    wiki = make_wiki(bundle)
    wiki.author = "bob"
    wiki.ingest_many([doc("a.md", "red-1", SECRET)])

    # ACT
    wiki.purge(KEY_A, author="ada")

    # ASSERT
    lines = log_lines(wiki)
    assert any(line == "* **Created**: [purged] · by bob" for line in lines)
    assert lines[0].endswith(" · by ada")


def test_the_log_lines_of_a_note_that_moved_since_are_purged_by_its_title(wiki: Wiki) -> None:
    # ARRANGE
    wiki.ingest_many([doc("a.md", "red-1", SECRET)])
    note = wiki.store.load().all_notes()[0]
    wiki.create_folder("/", "elsewhere", "Somewhere else.")
    wiki.move_note(note.rel, "/elsewhere")

    # ACT
    wiki.purge(KEY_A)

    # ASSERT
    assert not any("red-1" in line for line in log_lines(wiki))


# -- sync ----------------------------------------------------------------------------------------------------------


def folder_of(path: Path) -> Path:
    path.mkdir()
    (path / "a.md").write_text(f"topic: red-1\n{SECRET}", encoding="utf-8")
    (path / "b.md").write_text(f"topic: red-1\n{OTHER}", encoding="utf-8")
    return path


def test_a_sync_marks_a_removed_document_as_removed_and_keeps_its_notes_unless_told_to_purge(wiki: Wiki, tmp_path: Path) -> None:
    # ARRANGE
    folder = folder_of(tmp_path / "docs")
    source = LocalFolderSource(folder)
    sync(wiki, source)
    (folder / "a.md").unlink()

    # ACT
    kept = sync(wiki, source)

    # ASSERT
    assert kept.removed == 1 and SECRET in everything(wiki)


def test_a_sync_with_purge_removed_purges_what_was_removed_at_the_source(wiki: Wiki, tmp_path: Path) -> None:
    # ARRANGE
    folder = folder_of(tmp_path / "docs")
    source = LocalFolderSource(folder)
    sync(wiki, source)
    (folder / "a.md").unlink()

    # ACT
    report = sync(wiki, source, purge_removed=True)
    again = sync(wiki, source, purge_removed=True)

    # ASSERT
    assert (report.removed, report.errors, report.stopped) == (1, [], None)
    assert SECRET not in everything(wiki) and OTHER in everything(wiki) and wiki.check() == []
    assert (again.removed, again.created, again.merged) == (0, 0, 0)  # b is filed again already; nothing is done twice


def test_a_sync_with_purge_removed_skips_a_removal_of_something_never_filed(wiki: Wiki, tmp_path: Path) -> None:
    # ARRANGE
    folder = folder_of(tmp_path / "docs")
    (folder / "c.md").write_text("topic: blue-1\nnot filed yet", encoding="utf-8")
    source = LocalFolderSource(folder)
    sync(wiki, source)
    (folder / "c.md").unlink()
    wiki.purge("local:" + str(folder.resolve()) + ":c.md")

    # ACT
    report = sync(wiki, source, purge_removed=True)

    # ASSERT
    assert (report.removed, report.skipped) == (0, 1)


# -- the command line ----------------------------------------------------------------------------------------------


def test_the_command_asks_for_yes_and_then_prints_what_it_deleted(
    bundle: Path, wiki: Wiki, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # ARRANGE
    monkeypatch.setattr(cli, "make_wiki", lambda **_: wiki)
    wiki.ingest_many([doc("a.md", "red-1", SECRET), doc("b.md", "red-1", OTHER)])
    capsys.readouterr()

    # ACT
    refused = cli.main(["--bundle", str(bundle), "purge", KEY_A])
    said = capsys.readouterr().err
    still = SECRET in everything(wiki)
    done = cli.main(["--bundle", str(bundle), "purge", KEY_A, "--yes"])
    out = capsys.readouterr().out

    # ASSERT
    assert refused == 2 and "Nothing was done" in said and "1 other document" in said and still
    assert done == 0 and "deleted note: " in out and f"filed again: {KEY_B}" in out and "1 document(s) filed again" in out
    assert SECRET not in everything(wiki)
    assert lines_by(wiki, "Purged")[0].endswith(" · by " + (wiki.author or ""))


def lines_by(wiki: Wiki, verb: str) -> list[str]:
    return [line for line in log_lines(wiki) if line.startswith(f"* **{verb}**")]


def test_the_command_says_so_when_no_raw_copy_has_the_key(
    bundle: Path, wiki: Wiki, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # ARRANGE
    monkeypatch.setattr(cli, "make_wiki", lambda **_: wiki)

    # ACT
    code = cli.main(["--bundle", str(bundle), "purge", "test:acct:none.md", "--yes"])

    # ASSERT
    assert code == 1 and "no raw copy" in capsys.readouterr().err


def test_sync_has_a_purge_removed_flag(
    bundle: Path, wiki: Wiki, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # ARRANGE
    monkeypatch.setattr(cli, "make_wiki", lambda **_: wiki)
    folder = folder_of(tmp_path / "docs")
    assert cli.main(["--bundle", str(bundle), "sync", str(folder)]) == 0
    (folder / "a.md").unlink()

    # ACT
    code = cli.main(["--bundle", str(bundle), "sync", str(folder), "--purge-removed"])

    # ASSERT
    assert code == 0 and "removed 1" in capsys.readouterr().out and SECRET not in everything(wiki)
