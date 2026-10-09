# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`sync` with a scripted connector, `LocalFolderSource`, and the `sync` command and `origin` over HTTP."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from llmw2 import Change, ChangeBatch, InputError, LocalFolderSource, ModelError, Origin, SyncReport, Wiki, WikiConfig, cli, sync
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM, FakeClassifier, FakeLLM
from tests.wiki.test_ingest_many import Probe, make_wiki

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
    assert report.to_dict() == {"created": 2, "merged": 0, "unchanged": 0, "removed": 0, "skipped": 0, "errors": [], "stopped": None}


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

    def chat(messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
        if source.fetched == ["a", "b"]:
            raise ModelError("llm 401")
        return real_chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)

    monkeypatch.setattr(llm, "chat", chat)

    report = sync(wiki, source)  # a model failure no longer raises: the report says why it stopped

    assert report.stopped and "llm 401" in report.stopped
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


# -- sync, in parallel -------------------------------------------------------------


def topic_files(*ids: str) -> dict[str, bytes]:
    """One document per id, each on a topic of its own (red-<n>, n counted from 0 over the ids given)."""
    return {item: f"topic: red-{n}\nBody of {item}.".encode() for n, item in enumerate(ids)}


class Threaded(Fake):
    """A connector that notes which threads fetched, and can be asked what was already removed when a fetch began."""

    def __init__(self, *batches: ChangeBatch, files: dict[str, bytes] | None = None) -> None:
        super().__init__(*batches, files=files)
        self.threads: set[int] = set()
        self.on_fetch: list[Any] = []

    def fetch(self, change: Change) -> bytes:
        self.threads.add(threading.get_ident())
        for check in self.on_fetch:
            check()
        return super().fetch(change)


@pytest.fixture
def pllm() -> ContentLLM:
    return ContentLLM(UsageTracker())


@pytest.fixture
def par(bundle: Path, pllm: ContentLLM) -> Wiki:
    """A classic wiki on a model whose answers do not depend on the order of the calls, filing three at a time."""
    return make_wiki(bundle, concurrency=3, llm=pllm)


def test_sync_files_a_page_in_parallel_and_reports_created(par: Wiki) -> None:
    probe = Probe(pause=0.03)
    probe.want = 3
    par.steps = probe.steps
    ids = [f"d{n}" for n in range(9)]
    source = Fake(ChangeBatch([upsert(i) for i in ids], "c1"), files=topic_files(*ids))

    report = sync(par, source)

    assert report == SyncReport(created=9)
    assert probe.prep_max == 3 and probe.serial_max == 1
    assert saved_cursor(par) == "c1" and par.check() == []


def test_sync_takes_concurrency_from_its_argument_over_the_config(par: Wiki) -> None:
    probe = Probe(pause=0.03)
    par.steps = probe.steps
    ids = [f"d{n}" for n in range(8)]

    sync(par, Fake(ChangeBatch([upsert(i) for i in ids], "c1"), files=topic_files(*ids)), concurrency=2)

    assert probe.prep_max == 2


def test_sync_fetches_on_a_worker_thread(par: Wiki) -> None:
    source = Threaded(ChangeBatch([upsert("a"), upsert("b")], "c1"), files=topic_files("a", "b"))

    sync(par, source)

    assert source.threads and threading.get_ident() not in source.threads


def test_sync_applies_only_the_last_change_of_an_origin_in_a_page(par: Wiki) -> None:
    source = Fake(ChangeBatch([upsert("a", "v1"), upsert("b"), upsert("a", "v2")], "c1"), files=topic_files("a", "b"))

    report = sync(par, source)

    assert report == SyncReport(created=2)
    assert sorted(source.fetched) == ["a", "b"]  # a once
    record = par.origin("fake:Team A:a")
    assert record is not None and record.version == "v2"


def test_sync_when_an_item_is_added_then_removed_in_one_page_does_nothing_for_it(par: Wiki) -> None:
    source = Fake(ChangeBatch([upsert("a"), remove("a")], "c1"), files=topic_files("a"))

    report = sync(par, source)

    assert report == SyncReport(skipped=1)
    assert source.fetched == [] and par.origin("fake:Team A:a") is None


def test_sync_when_an_item_is_removed_then_returns_in_one_page_files_it_again(par: Wiki) -> None:
    sync(par, Fake(ChangeBatch([upsert("a", "v1")], "c1"), files=topic_files("a")))

    report = sync(par, Fake(ChangeBatch([remove("a"), upsert("a", "v2")], "c2"), files={"a": b"topic: red-0\nBody, edited."}))

    assert (report.removed, report.merged + report.created) == (0, 1)
    record = par.origin("fake:Team A:a")
    assert record is not None and not record.removed and record.version == "v2"


def test_sync_removes_before_it_fetches_anything_in_a_page(par: Wiki) -> None:
    files = topic_files("old", "a", "b")
    sync(par, Fake(ChangeBatch([upsert("old")], "c1"), files=files))
    removed_at_fetch: list[bool] = []
    source = Threaded(ChangeBatch([upsert("a"), upsert("b"), remove("old")], "c2"), files=files)

    def seen() -> None:
        record = par.origin("fake:Team A:old")
        removed_at_fetch.append(record is not None and record.removed)

    source.on_fetch.append(seen)

    report = sync(par, source)

    assert (report.removed, report.created) == (1, 2)
    assert removed_at_fetch == [True, True]


def test_sync_fetches_only_what_is_new_or_changed_in_a_mixed_page(par: Wiki) -> None:
    sync(par, Fake(ChangeBatch([upsert("a", "v1")], "c1"), files=topic_files("a")))
    source = Fake(ChangeBatch([upsert("a", "v1"), upsert("b", "v1"), upsert("c", "v1")], "c2"), files=topic_files("a", "b", "c"))

    report = sync(par, source)

    assert report == SyncReport(created=2, skipped=1)
    assert sorted(source.fetched) == ["b", "c"]


def test_sync_that_stops_on_a_model_error_returns_a_partial_report_and_keeps_the_cursor(par: Wiki, pllm: ContentLLM) -> None:
    ids = ["a", "b", "c", "d", "e", "f"]
    files = topic_files(*ids)
    pllm.fail_on["red-3\n"] = ModelError("llm 429 from http://llm.test: busy", status=429)  # "d"
    source = Fake(
        ChangeBatch([upsert(i) for i in ids[:2]], "c1", more=True),
        ChangeBatch([upsert(i) for i in ids[2:]], "c2"),
        files=files,
    )

    report = sync(par, source)

    assert report.stopped and "429" in report.stopped
    assert (report.created, report.errors) == (3, [])  # a, b and c: the page's documents before the failed one
    assert source.cursors == [None, "c1"]  # asked for no third page
    assert saved_cursor(par) == "c1"
    assert par.check() == []


def test_the_next_sync_after_a_stop_resumes_at_the_page_and_does_not_pay_again_for_what_was_filed(par: Wiki, pllm: ContentLLM) -> None:
    ids = ["a", "b", "c", "d", "e", "f"]
    files = topic_files(*ids)
    pllm.fail_on["red-3\n"] = ModelError("llm 429", status=429)
    pages = lambda: (  # noqa: E731
        ChangeBatch([upsert(i) for i in ids[:2]], "c1", more=True),
        ChangeBatch([upsert(i) for i in ids[2:]], "c2"),
    )
    sync(par, Fake(*pages(), files=files))
    pllm.fail_on.clear()
    summarized = len([1 for op, _ in pllm.calls if op == "librarian/summarize"])
    resume = Fake(pages()[1], files=files)

    report = sync(par, resume)

    assert resume.cursors == ["c1"]
    assert (report.created, report.skipped, report.stopped) == (3, 1, None)  # d, e, f are new; c, filed before, is the same version
    assert sorted(resume.fetched) == ["d", "e", "f"]
    assert len([1 for op, _ in pllm.calls if op == "librarian/summarize"]) - summarized == 3
    assert saved_cursor(par) == "c2"
    again = Fake(ChangeBatch([upsert(i) for i in ids], "c3"), files=files)
    assert sync(par, again) == SyncReport(skipped=6) and again.fetched == []
    assert par.check() == []


def test_sync_when_the_model_fails_on_the_first_document_files_nothing_and_saves_no_cursor(par: Wiki, pllm: ContentLLM) -> None:
    pllm.fail_on["red-0\n"] = ModelError("llm 503", status=503)
    source = Fake(ChangeBatch([upsert("a"), upsert("b")], "c1"), files=topic_files("a", "b"))

    report = sync(par, source)

    assert report.stopped and report.created == 0
    assert not (par.cfg.bundle / ".llmw2" / "sync").exists() or not list((par.cfg.bundle / ".llmw2" / "sync").glob("*.json"))


def test_a_finished_sync_has_no_stop_reason(par: Wiki) -> None:
    report = sync(par, Fake(ChangeBatch([upsert("a")], "c1"), files=topic_files("a")))

    assert report.stopped is None
    assert report.to_dict()["stopped"] is None


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

    expected = {"created": 2, "merged": 0, "unchanged": 0, "removed": 0, "skipped": 0, "errors": [], "stopped": None}
    assert json.loads(capsys.readouterr().out) == expected


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


@pytest.fixture
def topics(tmp_path: Path) -> Path:
    """A folder of six documents, each on a topic of its own."""
    path = tmp_path / "topics"
    path.mkdir()
    for n in range(6):
        (path / f"doc-{n}.md").write_text(f"topic: red-{n}\nBody {n}.", encoding="utf-8")
    return path


def use(monkeypatch: pytest.MonkeyPatch, where: Path, probe: Probe, llm: ContentLLM, concurrency: int = 3) -> None:
    """Make the command build its wiki on `llm` and the probe's steps, filing `concurrency` at a time unless the flag says otherwise."""

    def make(*, bundle: Path | None = None, mode: str | None = None) -> Wiki:
        return make_wiki(bundle or where, concurrency=concurrency, steps=probe.steps, llm=llm)

    monkeypatch.setattr(cli, "make_wiki", make)


def test_cli_sync_concurrency_sets_how_many_documents_are_prepared_at_once(
    bundle: Path, topics: Path, monkeypatch: pytest.MonkeyPatch, pllm: ContentLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    probe = Probe(pause=0.03)
    use(monkeypatch, bundle, probe, pllm, concurrency=4)

    assert cli.main(["--bundle", str(bundle), "sync", str(topics), "--concurrency", "2"]) == 0

    assert probe.prep_max == 2
    assert capsys.readouterr().out.strip() == "created 6, merged 0, unchanged 0, removed 0, skipped 0"


def test_cli_sync_without_concurrency_uses_the_config(
    bundle: Path, topics: Path, monkeypatch: pytest.MonkeyPatch, pllm: ContentLLM
) -> None:
    probe = Probe(pause=0.03)
    use(monkeypatch, bundle, probe, pllm, concurrency=3)

    assert cli.main(["--bundle", str(bundle), "sync", str(topics)]) == 0

    assert probe.prep_max == 3


def test_cli_sync_exits_non_zero_when_it_stops_and_says_why(
    bundle: Path, topics: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = ContentLLM(UsageTracker(), fail_on={"red-3\n": ModelError("llm 429 from http://llm.test: busy", status=429)})
    use(monkeypatch, bundle, Probe(pause=0.0), llm)

    code = cli.main(["--bundle", str(bundle), "sync", str(topics)])

    captured = capsys.readouterr()
    assert code != 0
    assert "429" in captured.err
    assert "created 3" in captured.out  # what was filed before it stopped is reported
    assert not list((bundle / ".llmw2" / "sync").glob("*.json"))  # the cursor did not move


def test_cli_sync_json_of_a_stopped_run_carries_the_reason(
    bundle: Path, topics: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    llm = ContentLLM(UsageTracker(), fail_on={"red-3\n": ModelError("llm 429", status=429)})
    use(monkeypatch, bundle, Probe(pause=0.0), llm)

    code = cli.main(["--bundle", str(bundle), "sync", str(topics), "--json"])

    assert code != 0
    report = json.loads(capsys.readouterr().out)
    assert report["created"] == 3 and "429" in report["stopped"]


def test_cli_sync_with_concurrency_below_one_fails_and_files_nothing(
    bundle: Path, topics: Path, monkeypatch: pytest.MonkeyPatch, pllm: ContentLLM
) -> None:
    use(monkeypatch, bundle, Probe(pause=0.0), pllm)

    assert cli.main(["--bundle", str(bundle), "sync", str(topics), "--concurrency", "0"]) != 0

    assert pllm.calls == []


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
