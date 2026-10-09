# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Provenance: `Origin`, the hash and origin kept in raw copies and notes, dedupe on ingest, `mark_removed`."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from llmw2 import InputError, ModelError, Origin, Wiki
from llmw2.bundle import store as store_module
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.origin import content_hash
from llmw2.bundle.store import WikiStore
from tests.wiki.fakes import FakeLLM

ORIGIN = Origin("onedrive", "me", "item-1", link="https://od.test/1", version="v1", modified="2026-09-01T10:00:00+00:00")
KEY = "onedrive:me:item-1"


def read(path: Path) -> OKFDocument:
    return OKFDocument.parse(path.read_text(encoding="utf-8"))


def raws(bundle: Path) -> list[Path]:
    return sorted((bundle / "raw").glob("*.md"))


def script(llm: FakeLLM, title: str = "Revenue recognition") -> None:
    """One classic ingest into a wiki that already has a /finance folder with a note."""
    llm.add("librarian/summarize", {"title": title, "summary": f"About {title}.", "body": "Body."})
    llm.add("librarian/route", {"action": "descend", "subfolder": "finance"}, {"action": "here"})
    llm.add("librarian/find_match", {"match": None}).add("librarian/relate", {"related": []})


class _Frozen(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> _Frozen:
        return cls(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every write in the same second, as a fast sync does."""
    monkeypatch.setattr(store_module, "datetime", _Frozen)



@pytest.fixture
def wiki(classic_wiki: Wiki) -> Wiki:
    store = classic_wiki.store
    folder, _ = store.create_folder(store.load().find("/"), "finance", "Money in and out.")
    store.write_note(folder, title="Seed", summary="Seeded.", body="Seed.", tags=[], source={"resource": "/raw/seed.md", "title": "Seed"})
    return classic_wiki


# -- Origin ----------------------------------------------------------------------


def test_origin_key_joins_source_account_and_id() -> None:
    assert ORIGIN.key == KEY
    assert Origin("local", "", "a/b.md").key == "local::a/b.md"


def test_origin_to_dict_drops_none_values() -> None:
    assert Origin("local", "", "a.md").to_dict() == {"source": "local", "account": "", "id": "a.md"}
    assert ORIGIN.to_dict() == {
        "source": "onedrive",
        "account": "me",
        "id": "item-1",
        "link": "https://od.test/1",
        "version": "v1",
        "modified": "2026-09-01T10:00:00+00:00",
    }


def test_origin_from_dict_round_trips() -> None:
    assert Origin.from_dict(ORIGIN.to_dict()) == ORIGIN
    bare = Origin("local", "", "a.md")
    assert Origin.from_dict(bare.to_dict()) == bare


@pytest.mark.parametrize("missing", ["source", "account", "id"])
def test_origin_from_dict_requires_source_account_and_id(missing: str) -> None:
    data = ORIGIN.to_dict()
    del data[missing]

    with pytest.raises(InputError, match=missing):
        Origin.from_dict(data)


@pytest.mark.parametrize("bad", [None, 3, ["x"]])
def test_origin_from_dict_rejects_non_string_required_values(bad: Any) -> None:
    with pytest.raises(InputError):
        Origin.from_dict({**ORIGIN.to_dict(), "id": bad})


def test_origin_from_dict_tolerates_missing_and_unknown_optional_keys() -> None:
    origin = Origin.from_dict({"source": "s", "account": "", "id": "i", "extra": "ignored"})

    assert (origin.link, origin.version, origin.modified) == (None, None, None)


# -- provenance in the raw copy and the note ------------------------------------------


def test_raw_copy_and_note_carry_hash_and_origin(wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    script(llm)

    result = wiki.ingest("Booked on delivery.", origin=ORIGIN)

    [raw] = raws(bundle)
    fm = read(raw).frontmatter
    assert fm["hash"] == content_hash("Booked on delivery.")
    assert fm["hash"].startswith("sha256:") and len(fm["hash"]) == len("sha256:") + 64
    assert fm["origin"] == ORIGIN.to_dict()
    [source] = read(bundle / result.note).frontmatter["sources"]
    assert (source["hash"], source["origin"]) == (fm["hash"], ORIGIN.to_dict())


def test_ingest_without_origin_adds_only_the_hash_and_never_dedupes(wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    script(llm)
    script(llm, "Second")

    first = wiki.ingest("Same text.")
    second = wiki.ingest("Same text.")

    assert (first.action, second.action) == ("created", "created")
    assert len(raws(bundle)) == 2
    fm = read(raws(bundle)[0]).frontmatter
    assert "origin" not in fm and fm["hash"] == content_hash("Same text.")
    assert "origin" not in read(bundle / first.note).frontmatter["sources"][0]


def test_bundle_with_old_style_notes_and_raw_still_checks_clean(wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    (bundle / "raw").mkdir(exist_ok=True)
    (bundle / "raw" / "old.md").write_text("---\ntype: Source\ntitle: Old\n---\nold text", encoding="utf-8")
    script(llm)
    wiki.ingest("New text.", origin=ORIGIN)

    assert wiki.check() == []


def test_ingest_file_takes_the_link_as_resource(wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    script(llm)

    wiki.ingest_file(b"Booked on delivery.", "memo.md", origin=ORIGIN)

    assert read(raws(bundle)[0]).frontmatter["resource"] == "https://od.test/1"


# -- dedupe ---------------------------------------------------------------------------


def files_and_log(bundle: Path) -> tuple[list[str], str]:
    names = sorted(str(p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file())
    return names, (bundle / "log.md").read_text(encoding="utf-8")


def test_same_origin_and_same_text_is_unchanged_with_no_model_call_file_or_log_line(
    wiki: Wiki, llm: FakeLLM, bundle: Path
) -> None:
    script(llm)
    first = wiki.ingest("Booked on delivery.", origin=ORIGIN)
    before, calls = files_and_log(bundle), len(llm.calls)

    again = wiki.ingest("Booked on delivery.", origin=Origin("onedrive", "me", "item-1"))

    assert again.action == "unchanged"
    assert (again.note, again.title) == (first.note, first.title)
    assert len(llm.calls) == calls
    assert files_and_log(bundle) == before


def test_same_origin_and_same_version_with_different_text_is_unchanged(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)
    wiki.ingest("Booked on delivery.", origin=ORIGIN)
    calls = len(llm.calls)

    again = wiki.ingest("Edited text, same version.", origin=ORIGIN)

    assert again.action == "unchanged"
    assert len(llm.calls) == calls


def test_an_empty_version_does_not_count_as_a_match(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)
    script(llm, "Second")
    wiki.ingest("One.", origin=Origin("s", "", "i", version=""))

    assert wiki.ingest("Two.", origin=Origin("s", "", "i", version="")).action == "created"


def test_new_text_and_new_version_ingests_again_as_a_second_raw_copy_of_the_key(
    wiki: Wiki, llm: FakeLLM, bundle: Path
) -> None:
    script(llm)
    script(llm, "Revenue recognition v2")
    wiki.ingest("Booked on delivery.", origin=ORIGIN)
    newer = Origin("onedrive", "me", "item-1", version="v2")

    result = wiki.ingest("Booked monthly.", origin=newer)

    assert result.action != "unchanged"
    copies = [read(p).frontmatter for p in raws(bundle)]
    assert sorted(c["origin"]["version"] for c in copies) == ["v1", "v2"]
    record = wiki.origin(KEY)
    assert record is not None and record.version == "v2" and result.note in record.notes
    assert wiki.check() == []


def test_after_mark_removed_the_same_content_ingests_again(wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    script(llm)
    script(llm, "Zed")
    wiki.ingest("Booked on delivery.", origin=ORIGIN)
    wiki.mark_removed(KEY)

    again = wiki.ingest("Booked on delivery.", origin=ORIGIN)

    assert again.action != "unchanged"
    assert len(raws(bundle)) == 2


@pytest.mark.usefixtures("frozen_clock")
def test_the_record_after_a_re_ingest_is_the_new_copy_not_removed(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm, "Revenue recognition")
    script(llm, "Back again")
    wiki.ingest("Booked on delivery.", origin=ORIGIN)
    wiki.mark_removed(KEY)
    wiki.ingest("Booked on delivery.", origin=ORIGIN)

    record = wiki.origin(KEY)

    assert record is not None and not record.removed


@pytest.mark.usefixtures("frozen_clock")
def test_the_record_follows_the_newest_version_saved_in_the_same_second(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm, "Revenue recognition")
    script(llm, "Back again")
    wiki.ingest("Booked on delivery.", origin=ORIGIN)
    wiki.ingest("Booked monthly.", origin=Origin("onedrive", "me", "item-1", version="v2"))

    record = wiki.origin(KEY)

    assert record is not None and record.version == "v2"


# -- wiki.origin and mark_removed ---------------------------------------------------------


def test_unknown_key_gives_none(wiki: Wiki) -> None:
    assert wiki.origin("nope:x:y") is None
    assert wiki.mark_removed("nope:x:y") is None


def test_origin_record_describes_the_latest_copy(wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    script(llm)
    result = wiki.ingest("Booked on delivery.", origin=ORIGIN)

    record = wiki.origin(KEY)

    assert record is not None
    assert record.raw == read(bundle / result.note).frontmatter["sources"][0]["resource"]
    assert (record.hash, record.version, record.removed) == (content_hash("Booked on delivery."), "v1", False)
    assert record.notes == [result.note]


def test_mark_removed_marks_every_copy_and_keeps_raw_text_and_notes(wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    script(llm)
    script(llm, "Second")
    first = wiki.ingest("Booked on delivery.", origin=ORIGIN)
    second = wiki.ingest("Booked monthly.", origin=Origin("onedrive", "me", "item-1", version="v2"))
    bodies = [p.read_bytes().split(b"\n---\n", 1)[1] for p in raws(bundle)]

    record = wiki.mark_removed(KEY)

    assert record is not None and record.removed
    for path in raws(bundle):
        assert read(path).frontmatter["removed"]
    assert [p.read_bytes().split(b"\n---\n", 1)[1] for p in raws(bundle)] == bodies
    assert all((bundle / n).is_file() for n in (first.note, second.note))
    assert wiki.check() == []


def test_mark_removed_logs_once_and_is_idempotent(wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    script(llm)
    wiki.ingest("Booked on delivery.", origin=ORIGIN)

    wiki.mark_removed(KEY)
    after_first = files_and_log(bundle)
    stamps = [read(p).frontmatter["removed"] for p in raws(bundle)]
    again = wiki.mark_removed(KEY)

    assert again is not None and again.removed
    assert after_first[1].count("Source removed") == 1
    assert files_and_log(bundle) == after_first
    assert [read(p).frontmatter["removed"] for p in raws(bundle)] == stamps


def test_raw_copy_without_origin_has_no_seq_and_with_origin_counts_up(wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    script(llm)
    script(llm, "Second")
    script(llm, "Third")
    wiki.ingest("Plain.")
    wiki.ingest("One.", origin=ORIGIN)
    wiki.ingest("Two.", origin=Origin("onedrive", "me", "item-1", version="v2"))

    seqs = {read(p).body.strip(): read(p).frontmatter.get("seq") for p in raws(bundle)}

    assert seqs == {"Plain.": None, "One.": 1, "Two.": 2}


@pytest.mark.usefixtures("frozen_clock")
def test_latest_copy_survives_lost_mtimes_and_a_deleted_older_copy(wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    for title in ("Zed", "Alpha", "Mid"):
        script(llm, title)
    wiki.ingest("One.", origin=ORIGIN)
    wiki.ingest("Two.", origin=Origin("onedrive", "me", "item-1", version="v2"))
    for path in raws(bundle):
        os.utime(path, (0, 0))
    oldest = next(p for p in raws(bundle) if read(p).frontmatter["seq"] == 1)
    oldest.unlink()

    wiki.ingest("Three.", origin=Origin("onedrive", "me", "item-1", version="v3"))

    record = wiki.origin(KEY)
    assert record is not None and record.version == "v3"
    assert read(bundle / record.raw.lstrip("/")).frontmatter["seq"] == 3


# -- a raw copy is only kept once a note cites it ---------------------------------------------


def fail_first_write(wiki: Wiki, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first note write raises, as an interruption after the raw copy would; the second goes through."""
    real = WikiStore.write_note
    attempts: list[int] = []

    def write_note(self: WikiStore, *args: Any, **kwargs: Any) -> Any:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("interrupted")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(wiki.store, "write_note", write_note.__get__(wiki.store))


def test_an_interrupted_ingest_is_filed_as_created_when_it_runs_again_and_the_note_cites_a_raw_copy(
    wiki: Wiki, llm: FakeLLM, bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARRANGE
    script(llm)
    script(llm)
    fail_first_write(wiki, monkeypatch)
    with pytest.raises(RuntimeError):
        wiki.ingest("Booked on delivery.", origin=ORIGIN)

    # ACT
    again = wiki.ingest("Booked on delivery.", origin=ORIGIN)

    # ASSERT
    assert again.action == "created"
    [source] = read(bundle / again.note).frontmatter["sources"]
    assert "/raw/" in source["resource"]
    copy = read(bundle / source["resource"].lstrip("/"))
    assert copy.frontmatter["hash"] == content_hash("Booked on delivery.")
    record = wiki.origin(KEY)
    assert record is not None and record.raw == source["resource"] and record.notes == [again.note]


def test_a_model_error_during_the_merge_leaves_no_new_raw_copy(
    wiki: Wiki, llm: FakeLLM, bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script(llm)
    llm.replies["librarian/find_match"] = [{"match": "finance/seed.md"}]
    llm.add("librarian/consolidate", {"decision": "modify"})
    real_chat = llm.chat

    def chat(messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
        if op == "librarian/merge":
            raise ModelError("llm 500")
        return real_chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)

    monkeypatch.setattr(llm, "chat", chat)
    before = raws(bundle)

    with pytest.raises(ModelError):
        wiki.ingest("Booked on delivery.", origin=ORIGIN)

    assert raws(bundle) == before
    assert wiki.origin(KEY) is None


def test_an_orphan_raw_copy_is_not_an_origin_and_its_document_is_ingested_as_created(wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    script(llm)
    wiki.store.save_raw("Booked on delivery.", title="Memo", resource=None, origin=ORIGIN)

    record = wiki.origin(KEY)
    result = wiki.ingest("Booked on delivery.", origin=ORIGIN)

    assert record is None
    assert result.action == "created"
    assert wiki.origin(KEY) is not None


def test_the_origin_ignores_a_later_orphan_copy_and_its_document_is_not_unchanged(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)
    script(llm, "Second")
    first = wiki.ingest("Booked on delivery.", origin=ORIGIN)
    cited = wiki.origin(KEY)
    wiki.store.save_raw("Booked monthly.", title="Memo", resource=None, origin=Origin("onedrive", "me", "item-1", version="v2"))

    record = wiki.origin(KEY)
    result = wiki.ingest("Booked monthly.", origin=Origin("onedrive", "me", "item-1", version="v2"))

    assert cited is not None and record is not None
    assert (record.raw, record.hash, record.version) == (cited.raw, content_hash("Booked on delivery."), "v1")
    assert record.notes == [first.note]
    assert result.action != "unchanged"


def test_only_the_latest_cited_copy_counts_so_an_older_version_is_filed_again(wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)
    script(llm, "Second")
    script(llm, "Third")
    wiki.ingest("Booked on delivery.", origin=ORIGIN)
    wiki.ingest("Booked monthly.", origin=Origin("onedrive", "me", "item-1", version="v2"))

    result = wiki.ingest("Booked on delivery.", origin=ORIGIN)

    assert result.action != "unchanged"


def test_mark_removed_of_a_key_with_only_orphan_copies_gives_none(wiki: Wiki) -> None:
    wiki.store.save_raw("Booked on delivery.", title="Memo", resource=None, origin=ORIGIN)

    assert wiki.mark_removed(KEY) is None
