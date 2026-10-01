# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`sync` with a scripted connector, `LocalFolderSource`, and the `sync` command and `origin` over HTTP."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from llmw2 import Change, ChangeBatch, InputError, LocalFolderSource, ModelError, Origin, SyncReport, Wiki, WikiConfig, cli, sync
from tests.wiki.fakes import FakeClassifier, FakeLLM

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def script(llm: FakeLLM, title: str = "Revenue recognition") -> None:
    """One classic ingest into a wiki that already has a /finance folder with a note."""
    llm.add("librarian/summarize", {"title": title, "summary": f"About {title}.", "body": "Body."})
    llm.add("librarian/route", {"action": "descend", "subfolder": "finance"}, {"action": "here"})
    llm.add("librarian/find_match", {"match": None}).add("librarian/relate", {"related": []})


def seed(wiki: Wiki) -> Wiki:
    wiki.init()
    store = wiki.store
    folder, _ = store.create_folder(store.load().find("/"), "finance", "Money in and out.")
    store.write_note(folder, title="Seed", summary="Seeded.", body="Seed.", tags=[], source={"resource": "/raw/seed.md", "title": "Seed"})
    return wiki


@pytest.fixture
def wiki(classic_wiki: Wiki) -> Wiki:
    return seed(classic_wiki)


class Fake:
    """A connector serving scripted batches; records what was fetched and which cursors it was asked with."""

    name = "fake"
    account = "Team A"

    def __init__(self, *batches: ChangeBatch, files: dict[str, bytes] | None = None) -> None:
        self.batches = list(batches)
        self.files = files or {}
        self.cursors: list[str | None] = []
        self.fetched: list[str] = []

    def changes(self, cursor: str | None) -> ChangeBatch:
        self.cursors.append(cursor)
        return self.batches.pop(0)

    def fetch(self, change: Change) -> bytes:
        self.fetched.append(change.origin.id)
        return self.files[change.origin.id]


def upsert(item: str, version: str | None = "v1", name: str | None = None) -> Change:
    return Change("upsert", Origin("fake", "Team A", item, version=version), name or f"{item}.md")


def remove(item: str) -> Change:
    return Change("remove", Origin("fake", "Team A", item), f"{item}.md")


def cursor_file(wiki: Wiki) -> Path:
    [path] = (wiki.cfg.bundle / ".llmw2" / "sync").glob("*.json")
    return path


def saved_cursor(wiki: Wiki) -> str:
    return str(json.loads(cursor_file(wiki).read_text(encoding="utf-8"))["cursor"])


# -- sync -------------------------------------------------------------------------


def test_sync_ingests_each_upsert_and_reports_created(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm, "One")
    script(llm, "Two")
    source = Fake(ChangeBatch([upsert("a"), upsert("b")], "c1"), files={"a": b"Text a.", "b": b"Text b."})

    report = sync(wiki, source)

    assert report == SyncReport(created=2)
    assert source.cursors == [None]
    assert wiki.origin("fake:Team A:a") is not None and wiki.origin("fake:Team A:b") is not None
    assert report.to_dict() == {"created": 2, "merged": 0, "unchanged": 0, "removed": 0, "skipped": 0, "errors": []}


def test_sync_skips_a_known_version_without_fetching(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)
    sync(wiki, Fake(ChangeBatch([upsert("a")], "c1"), files={"a": b"Text a."}))
    again = Fake(ChangeBatch([upsert("a")], "c2"), files={"a": b"Text a."})

    report = sync(wiki, again)

    assert report == SyncReport(skipped=1)
    assert again.fetched == []


def test_sync_counts_a_fetched_item_with_known_content_as_unchanged(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)
    sync(wiki, Fake(ChangeBatch([upsert("a", "v1")], "c1"), files={"a": b"Text a."}))
    calls = len(llm.calls)

    report = sync(wiki, Fake(ChangeBatch([upsert("a", "v2")], "c2"), files={"a": b"Text a."}))

    assert report == SyncReport(unchanged=1)
    assert len(llm.calls) == calls


def test_sync_records_an_input_error_and_goes_on(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)
    source = Fake(
        ChangeBatch([upsert("bad", name="bad.exe"), upsert("good")], "c1"), files={"bad": b"MZ", "good": b"Good text."}
    )

    report = sync(wiki, source)

    assert (report.created, len(report.errors)) == (1, 1)
    assert report.errors[0][0] == "fake:Team A:bad"
    assert ".exe" in report.errors[0][1]
    assert saved_cursor(wiki) == "c1"


def test_sync_stops_on_a_model_error_and_keeps_the_previous_cursor(wiki: Wiki, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch) -> None:
    script(llm)
    source = Fake(
        ChangeBatch([upsert("a")], "c1", more=True),
        ChangeBatch([upsert("b")], "c2"),
        files={"a": b"Text a.", "b": b"Text b."},
    )
    real_chat = llm.chat

    def chat(messages: list[dict[str, str]], *, op: str = "") -> str:
        if source.fetched == ["a", "b"]:
            raise ModelError("llm 401")
        return real_chat(messages, op=op)

    monkeypatch.setattr(llm, "chat", chat)

    with pytest.raises(ModelError):
        sync(wiki, source)

    assert saved_cursor(wiki) == "c1"


def test_sync_loops_while_more_and_hands_back_the_cursor(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm, "One")
    script(llm, "Two")
    source = Fake(
        ChangeBatch([upsert("a")], "c1", more=True),
        ChangeBatch([upsert("b")], "c2"),
        files={"a": b"Text a.", "b": b"Text b."},
    )

    report = sync(wiki, source)

    assert report.created == 2
    assert source.cursors == [None, "c1"]
    assert saved_cursor(wiki) == "c2"


def test_a_second_run_resumes_from_the_saved_cursor_file(wiki: Wiki) -> None:
    sync(wiki, Fake(ChangeBatch([], "c1")))
    assert cursor_file(wiki).parent == wiki.cfg.bundle / ".llmw2" / "sync"
    later = Fake(ChangeBatch([], "c2"))

    sync(wiki, later)

    assert later.cursors == ["c1"]
    assert saved_cursor(wiki) == "c2"
    assert wiki.check() == []


def test_accounts_sharing_a_long_prefix_keep_separate_cursors(wiki: Wiki, tmp_path: Path) -> None:
    base = str(tmp_path / "Clienti")
    one, two = Fake(ChangeBatch([], "cursor-one")), Fake(ChangeBatch([], "cursor-two"))
    one.account, two.account = f"{base}/ACME-progetto-uno", f"{base}/ACME-progetto-due"

    sync(wiki, one)
    sync(wiki, two)

    files = sorted((wiki.cfg.bundle / ".llmw2" / "sync").glob("*.json"))
    assert len(files) == 2
    assert sorted(json.loads(f.read_text(encoding="utf-8"))["cursor"] for f in files) == ["cursor-one", "cursor-two"]
    again = Fake(ChangeBatch([], "cursor-one-b"))
    again.account = one.account
    sync(wiki, again)
    assert again.cursors == ["cursor-one"]
    assert sorted(json.loads(f.read_text(encoding="utf-8"))["cursor"] for f in files) == ["cursor-one-b", "cursor-two"]


def test_sync_marks_a_removed_item_and_counts_an_unknown_removal_as_skipped(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)
    sync(wiki, Fake(ChangeBatch([upsert("a")], "c1"), files={"a": b"Text a."}))

    report = sync(wiki, Fake(ChangeBatch([remove("a"), remove("never-filed")], "c2")))

    assert (report.removed, report.skipped) == (1, 1)
    record = wiki.origin("fake:Team A:a")
    assert record is not None and record.removed


def test_a_removed_item_that_returns_is_filed_again(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)
    script(llm, "Zed")
    sync(wiki, Fake(ChangeBatch([upsert("a")], "c1"), files={"a": b"Text a."}))
    sync(wiki, Fake(ChangeBatch([remove("a")], "c2")))

    report = sync(wiki, Fake(ChangeBatch([upsert("a")], "c3"), files={"a": b"Text a."}))

    assert report.created == 1


def test_an_orphan_raw_copy_with_the_incoming_version_does_not_make_sync_skip_the_item(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)
    orphan = upsert("a", "v1").origin
    wiki.store.save_raw("Text a.", title="a", resource=None, origin=orphan)
    source = Fake(ChangeBatch([upsert("a", "v1")], "c1"), files={"a": b"Text a."})

    report = sync(wiki, source)

    assert report == SyncReport(created=1)
    assert source.fetched == ["a"]


# -- LocalFolderSource ------------------------------------------------------------------


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "docs"
    (path / "sub").mkdir(parents=True)
    (path / "a.md").write_text("Alpha.", encoding="utf-8")
    (path / "sub" / "b.txt").write_text("Beta.", encoding="utf-8")
    return path


def touch(path: Path, text: str, mtime: int) -> None:
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_a_full_listing_upserts_every_supported_visible_file(folder: Path) -> None:
    (folder / ".hidden.md").write_text("x", encoding="utf-8")
    (folder / ".git").mkdir()
    (folder / ".git" / "c.md").write_text("x", encoding="utf-8")
    (folder / "image.png").write_bytes(b"\x89PNG")
    source = LocalFolderSource(folder)

    batch = source.changes(None)

    assert {(c.kind, c.origin.id, c.filename) for c in batch.changes} == {("upsert", "a.md", "a.md"), ("upsert", "sub/b.txt", "b.txt")}
    assert batch.more is False
    change = next(c for c in batch.changes if c.origin.id == "a.md")
    assert change.origin.source == "local" and change.origin.account == str(folder.resolve())
    assert change.origin.link == str(folder.resolve() / "a.md")
    assert change.origin.version == f"{(folder / 'a.md').stat().st_mtime_ns}-{len('Alpha.')}"
    assert source.fetch(change) == b"Alpha."


def test_local_source_diffs_against_the_cursor(folder: Path) -> None:
    source = LocalFolderSource(folder)
    cursor = source.changes(None).cursor
    assert source.changes(cursor).changes == []

    touch(folder / "a.md", "Alpha, edited.", 1_700_000_000)
    (folder / "sub" / "b.txt").unlink()
    batch = source.changes(cursor)

    assert sorted((c.kind, c.origin.id) for c in batch.changes) == [("remove", "sub/b.txt"), ("upsert", "a.md")]


def test_symlinked_files_and_directories_are_not_listed(folder: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("Secret.", encoding="utf-8")
    try:
        (folder / "link.md").symlink_to(folder / "a.md")
        (folder / "linked-dir").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlink support")

    ids = {c.origin.id for c in LocalFolderSource(folder).changes(None).changes}

    assert ids == {"a.md", "sub/b.txt"}


def test_local_source_needs_a_folder(tmp_path: Path) -> None:
    (tmp_path / "file.md").write_text("x", encoding="utf-8")

    with pytest.raises(InputError):
        LocalFolderSource(tmp_path / "file.md")
    with pytest.raises(InputError):
        LocalFolderSource(tmp_path / "missing")


def test_syncing_a_folder_twice_files_nothing_the_second_time(wiki: Wiki, llm: FakeLLM, folder: Path) -> None:
    script(llm, "Alpha")
    script(llm, "Beta")

    first = sync(wiki, LocalFolderSource(folder))
    calls = len(llm.calls)
    second = sync(wiki, LocalFolderSource(folder))

    assert first == SyncReport(created=2)
    assert second == SyncReport()
    assert len(llm.calls) == calls
    assert wiki.check() == []


def test_syncing_a_folder_follows_edits_and_deletions(wiki: Wiki, llm: FakeLLM, folder: Path) -> None:
    script(llm, "Alpha")
    script(llm, "Beta")
    script(llm, "Alpha two")
    sync(wiki, LocalFolderSource(folder))
    touch(folder / "a.md", "Alpha, edited.", 1_700_000_000)
    (folder / "sub" / "b.txt").unlink()

    report = sync(wiki, LocalFolderSource(folder))

    assert (report.created + report.merged, report.removed) == (1, 1)
    gone = wiki.origin(f"local:{folder.resolve()}:sub/b.txt")
    assert gone is not None and gone.removed


# -- the command ------------------------------------------------------------------------


@pytest.fixture
def made(monkeypatch: pytest.MonkeyPatch, bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    seed(Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm))

    def make_wiki(*, bundle: Path | None = None, mode: str | None = None) -> Wiki:
        return Wiki(WikiConfig(bundle=bundle, mode=mode or "classic"), llm=llm, classifier=classifier)

    monkeypatch.setattr(cli, "make_wiki", make_wiki)


@pytest.mark.usefixtures("made")
def test_cli_sync_prints_the_counts(bundle: Path, llm: FakeLLM, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script(llm, "Alpha")
    script(llm, "Beta")

    assert cli.main(["--bundle", str(bundle), "sync", str(folder)]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == "created 2, merged 0, unchanged 0, removed 0, skipped 0"


@pytest.mark.usefixtures("made")
def test_cli_sync_json_prints_the_report(bundle: Path, llm: FakeLLM, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    script(llm, "Alpha")
    script(llm, "Beta")

    assert cli.main(["--bundle", str(bundle), "sync", str(folder), "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {"created": 2, "merged": 0, "unchanged": 0, "removed": 0, "skipped": 0, "errors": []}


@pytest.mark.usefixtures("made")
def test_cli_sync_exits_1_and_names_the_failed_file(bundle: Path, llm: FakeLLM, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (folder / "empty.md").write_text("  ", encoding="utf-8")
    script(llm, "Alpha")
    script(llm, "Beta")

    assert cli.main(["--bundle", str(bundle), "sync", str(folder)]) == 1

    captured = capsys.readouterr()
    assert "created 2" in captured.out
    assert "empty.md" in captured.err


@pytest.mark.usefixtures("made")
def test_cli_sync_of_a_missing_folder_fails(bundle: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--bundle", str(bundle), "sync", str(tmp_path / "nope")]) != 0

    assert "not a folder" in capsys.readouterr().err


# -- HTTP -------------------------------------------------------------------------------


def test_http_ingest_with_an_origin_is_unchanged_the_second_time(client: TestClient, classic_wiki: Wiki, llm: FakeLLM) -> None:
    seed(classic_wiki)
    script(llm)
    origin = {"source": "onedrive", "account": "me", "id": "item-1", "version": "v1"}
    body: dict[str, Any] = {"text": "Booked on delivery.", "origin": origin}

    first = client.post("/ingest", json=body).json()
    calls = len(llm.calls)
    again = client.post("/ingest", json=body)

    assert first["action"] == "created"
    assert again.status_code == 200
    assert (again.json()["action"], again.json()["note"]) == ("unchanged", first["note"])
    assert len(llm.calls) == calls


def test_http_ingest_rejects_an_origin_without_an_id(client: TestClient) -> None:
    response = client.post("/ingest", json={"text": "x", "origin": {"source": "s"}})

    assert response.status_code == 422
