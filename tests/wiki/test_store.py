# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

import pytest

from llmw2.bundle import store as store_module
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.store import WikiStore
from llmw2.bundle.tree import Folder, Note, graph, join_see_also, parse_index, slugify, split_see_also

ACTOR = "llmw2/test"
NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=UTC)
SOURCE = {"resource": "/raw/source.md", "title": "Source"}


class _Clock(datetime):
    """Stands in for `datetime` inside llmw2.bundle.store; `_Clock.at` is "now"."""

    at = NOW

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
        return cls.at.astimezone(tz)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> type[_Clock]:
    monkeypatch.setattr(store_module, "datetime", _Clock)
    monkeypatch.setattr(_Clock, "at", NOW)
    return _Clock


@pytest.fixture
def store(bundle: Path) -> WikiStore:
    wiki = WikiStore(bundle, actor=ACTOR)
    wiki.init()
    return wiki


def _read(path: Path) -> OKFDocument:
    return OKFDocument.parse(path.read_text(encoding="utf-8"))


def _put(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _note(store: WikiStore, folder: Folder, title: str, summary: str = "A summary.", **kw: Any) -> Note:
    fields: dict[str, Any] = {"body": "# Overview\n\nText.", "tags": [], "source": SOURCE} | kw
    return store.write_note(folder, title=title, summary=summary, **fields)


# -- slugify ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("Café Déjà Vu", "cafe-deja-vu"),
        ("Hello, World! (v2)", "hello-world-v2"),
        ("  Revenue -- Recognition  ", "revenue-recognition"),
    ],
)
def test_slugify_strips_accents_and_punctuation(text: str, slug: str) -> None:
    assert slugify(text) == slug


@pytest.mark.parametrize(
    ("text", "max_len", "slug"),
    [("alpha beta gamma", 12, "alpha-beta"), ("alpha beta gamma", 10, "alpha-beta"), ("a" * 70, 60, "a" * 60)],
    ids=["mid-word", "word-ends-at-limit", "one-long-word"],
)
def test_slugify_when_text_exceeds_max_len_then_cuts_at_a_word_boundary(text: str, max_len: int, slug: str) -> None:
    assert slugify(text, max_len=max_len) == slug


@pytest.mark.parametrize("text", ["", "!!!", "日本語"])
def test_slugify_falls_back_when_nothing_is_left(text: str) -> None:
    assert slugify(text) == "note"
    assert slugify(text, fallback="topics") == "topics"


@pytest.mark.parametrize(("text", "slug"), [("Index", "index-note"), ("log", "log-note"), ("LOG!", "log-note")])
def test_slugify_never_returns_a_reserved_name(text: str, slug: str) -> None:
    assert slugify(text) == slug


# -- init / load --------------------------------------------------------------


def test_init_writes_root_index_log_and_raw(store: WikiStore, bundle: Path) -> None:
    index = _read(bundle / "index.md")
    assert index.frontmatter == {"okf_version": "0.2"}
    assert index.body.strip() == "# Notes"
    assert (bundle / "log.md").read_text() == "# Wiki log\n"
    assert (bundle / "raw").is_dir()


def test_init_is_idempotent(store: WikiStore, bundle: Path) -> None:
    _put(bundle / "index.md", "# Notes\n\n* [Kept](kept.md) - kept\n")
    _put(bundle / "log.md", "# Wiki log\n\n## 2026-01-01\n\n* **Kept**: kept\n")
    store.init()
    assert (bundle / "index.md").read_text() == "# Notes\n\n* [Kept](kept.md) - kept\n"
    assert (bundle / "log.md").read_text() == "# Wiki log\n\n## 2026-01-01\n\n* **Kept**: kept\n"


def test_load_raises_when_the_bundle_is_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        WikiStore(tmp_path / "nowhere", actor=ACTOR).load()


def test_load_reads_folder_descriptions_and_note_frontmatter(store: WikiStore, bundle: Path) -> None:
    _put(bundle / "index.md", "# Subdirectories\n\n* [finance](finance/index.md) - Money matters.\n")
    _put(bundle / "finance/index.md", "# Notes\n\n* [Revenue](revenue.md) - Stale index text.\n")
    _put(
        bundle / "finance/revenue.md",
        "---\ntype: Note\ntitle: Revenue\ndescription: When revenue is booked.\ntags: [gaap]\n---\n\nBody.\n",
    )
    finance = store.load().subfolder("finance")
    assert finance is not None
    assert finance.description == "Money matters."
    [note] = finance.notes
    assert (note.rel, note.title, note.summary, note.tags) == ("finance/revenue.md", "Revenue", "When revenue is booked.", ["gaap"])
    assert note.folder is finance
    assert note.body == "Body."


def test_load_skips_reserved_files_raw_dotfiles_and_non_markdown(store: WikiStore, bundle: Path) -> None:
    note = "---\ntype: Note\n---\n\nBody.\n"
    for rel in ["raw/copy.md", ".hidden.md", ".obsidian/workspace.md", "viz.html", "kept.md"]:
        _put(bundle / rel, note)
    root = store.load()
    assert [n.rel for n in root.notes] == ["kept.md"]
    assert root.subfolders == []


def test_load_skips_unparseable_notes(store: WikiStore, bundle: Path, caplog: pytest.LogCaptureFixture) -> None:
    _put(bundle / "broken.md", "---\ntype: Note\n\nno closing delimiter\n")
    _put(bundle / "good.md", "---\ntype: Note\n---\n\nBody.\n")
    with caplog.at_level(logging.WARNING, logger="llmw2.bundle.store"):
        root = store.load()
    assert [n.rel for n in root.notes] == ["good.md"]
    assert "broken.md" in caplog.text


def test_load_tolerates_an_index_with_malformed_frontmatter(store: WikiStore, bundle: Path) -> None:
    _put(bundle / "finance/index.md", "---\ntitle: unterminated\n")
    assert store.load().subfolder("finance") is not None


def test_store_loads_a_bundle_written_by_another_okf_producer(bundle: Path) -> None:
    # Shaped like Google's OKF sample bundles: other section headings, other types, folded YAML.
    _put(bundle / "index.md", "# Subdirectories\n\n* [datasets](datasets/index.md) - GA4 sample dataset.\n"
         "* [references](references/index.md) - Audience metrics.\n")
    _put(bundle / "datasets/index.md", "# Dataset\n\n")
    _put(bundle / "references/index.md", "# Subdirectories\n\n* [metrics](metrics/index.md) - Metric queries.\n")
    _put(bundle / "references/metrics/index.md", "# Reference\n\n* [Purchasers](purchasers.md) - Users who bought.\n")
    _put(bundle / "references/metrics/purchasers.md", "---\ntype: Reference\ntitle: Purchasers\ndescription: Users who\n"
         "  completed a purchase.\ngenerated:\n  by: reference_agent/gemini\n  at: '2026-07-10T21:16:35+00:00'\n---\n\n# Query\n")

    root = WikiStore(bundle, actor=ACTOR).load()

    assert [f.name for f in root.subfolders] == ["datasets", "references"]
    assert root.subfolder("datasets").description == "GA4 sample dataset."
    metrics = root.find("references/metrics")
    assert metrics is not None and metrics.description == "Metric queries."
    [note] = metrics.notes
    assert (note.frontmatter["type"], note.title, note.summary) == ("Reference", "Purchasers", "Users who completed a purchase.")


# -- folders ------------------------------------------------------------------


def test_create_folder_writes_its_index_the_parent_entry_and_a_log_line(store: WikiStore, bundle: Path, clock: type[_Clock]) -> None:
    folder, created = store.create_folder(store.load(), "Finance & Ops", "Money  matters\n here.")
    assert (created, folder.rel, folder.description) == (True, "finance-ops", "Money matters here.")
    assert (bundle / "finance-ops/index.md").read_text() == "# Notes\n\n"
    assert "* [finance-ops](finance-ops/index.md) - Money matters here.\n" in (bundle / "index.md").read_text()
    assert "* **Created folder**: [/finance-ops](finance-ops/index.md) - Money matters here." in (bundle / "log.md").read_text()


def test_create_folder_nests_under_a_subfolder(store: WikiStore, bundle: Path) -> None:
    finance, _ = store.create_folder(store.load(), "finance", "Money.")
    pricing, _ = store.create_folder(finance, "pricing", "Prices.")
    assert (pricing.rel, pricing.parent) == ("finance/pricing", finance)
    assert parse_index((bundle / "finance/index.md").read_text()) == [("pricing", "pricing/index.md", "Prices.")]
    assert store.load().find("finance/pricing").description == "Prices."


def test_create_folder_reuses_an_existing_sibling(store: WikiStore, bundle: Path) -> None:
    root = store.load()
    first, _ = store.create_folder(root, "Finance", "Money.")
    again, created = store.create_folder(root, "finance!", "Other words.")
    assert (again, created) == (first, False)
    assert root.subfolders == [first]
    assert (bundle / "log.md").read_text().count("Created folder") == 1


def test_create_folder_never_creates_raw_at_the_root(store: WikiStore) -> None:
    root = store.load()
    folder, created = store.create_folder(root, "raw", "Raw materials.")
    assert (folder.rel, created) == ("raw-topics", True)
    nested, _ = store.create_folder(folder, "raw", "Allowed below the root.")
    assert nested.rel == "raw-topics/raw"


# -- notes --------------------------------------------------------------------


def test_write_note_writes_an_okf_note(store: WikiStore, clock: type[_Clock]) -> None:
    folder, _ = store.create_folder(store.load(), "finance", "Money.")
    note = _note(store, folder, " Revenue recognition ", " When revenue is booked. ", tags=["gaap"])
    assert note.rel == "finance/revenue-recognition.md"
    doc = _read(note.path)
    assert doc.frontmatter == {
        "type": "Note",
        "title": "Revenue recognition",
        "description": "When revenue is booked.",
        "tags": ["gaap"],
        "generated": {"by": ACTOR, "at": "2026-09-24T12:00:00+00:00"},
        "sources": [{"id": "s1", **SOURCE}],
    }
    assert datetime.fromisoformat(doc.frontmatter["generated"]["at"]).utcoffset() == timedelta(0)
    assert doc.body == "# Overview\n\nText."


def test_write_note_lists_the_note_in_its_folder_index(store: WikiStore, bundle: Path) -> None:
    folder, _ = store.create_folder(store.load(), "finance", "Money.")
    note = _note(store, folder, "Revenue recognition", "When revenue is booked.")
    assert folder.notes == [note]
    assert "* [Revenue recognition](revenue-recognition.md) - When revenue is booked.\n" in (bundle / "finance/index.md").read_text()


def test_write_note_suffixes_duplicate_slugs(store: WikiStore) -> None:
    root = store.load()
    notes = [_note(store, root, "Revenue") for _ in range(3)]
    assert [n.rel for n in notes] == ["revenue.md", "revenue-2.md", "revenue-3.md"]


def _existing_note(store: WikiStore) -> Note:
    """finance/revenue.md, linked to finance/costs.md, with a `verified` key the store never writes; loaded from disk."""
    folder, _ = store.create_folder(store.load(), "finance", "Money.")
    revenue = _note(store, folder, "Revenue", "Old summary.", tags=["money", "gaap"])
    store.link(revenue, _note(store, folder, "Costs", "What we spend."))
    doc = _read(revenue.path)
    doc.frontmatter["verified"] = {"by": "human:fc", "at": "2026-01-01T00:00:00+00:00"}
    revenue.path.write_text(doc.serialize(), encoding="utf-8")
    return store.load().find("finance").note("revenue")


def _update(store: WikiStore, note: Note) -> OKFDocument:
    store.update_note(
        note,
        title="Revenue recognition",
        summary="New summary.",
        body="# Overview\n\nNew body.",
        tags=["gaap", "ifrs"],
        source={"resource": "/raw/second.md", "title": "Second"},
    )
    return _read(note.path)


def test_update_note_keeps_unknown_keys_and_the_see_also_section(store: WikiStore) -> None:
    doc = _update(store, _existing_note(store))
    assert doc.frontmatter["verified"] == {"by": "human:fc", "at": "2026-01-01T00:00:00+00:00"}
    assert doc.body == "# Overview\n\nNew body.\n\n# See also\n\n* [Costs](costs.md) - What we spend."


def test_update_note_appends_sources_and_merges_tags(store: WikiStore) -> None:
    doc = _update(store, _existing_note(store))
    assert doc.frontmatter["sources"] == [{"id": "s1", **SOURCE}, {"id": "s2", "resource": "/raw/second.md", "title": "Second"}]
    assert doc.frontmatter["tags"] == ["money", "gaap", "ifrs"]
    assert (doc.frontmatter["title"], doc.frontmatter["description"]) == ("Revenue recognition", "New summary.")


def test_update_note_refreshes_generated_at(store: WikiStore, clock: type[_Clock]) -> None:
    note = _existing_note(store)
    clock.at = NOW + timedelta(days=1)
    doc = _update(store, note)
    assert doc.frontmatter["generated"] == {"by": ACTOR, "at": "2026-09-25T12:00:00+00:00"}


def test_update_note_rewrites_the_folder_index(store: WikiStore, bundle: Path) -> None:
    _update(store, _existing_note(store))
    assert parse_index((bundle / "finance/index.md").read_text()) == [
        ("Costs", "costs.md", "What we spend."),
        ("Revenue recognition", "revenue.md", "New summary."),
    ]


def test_update_note_when_the_write_fails_midway_then_the_old_note_is_intact(
    store: WikiStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARRANGE
    note = _existing_note(store)
    before = note.path.read_text(encoding="utf-8")
    write_text = Path.write_text

    def torn(path: Path, text: str, *args: Any, **kwargs: Any) -> int:
        write_text(path, text[:10], *args, **kwargs)
        raise OSError("disk full")

    # ACT
    with monkeypatch.context() as patched, pytest.raises(OSError, match="disk full"):
        patched.setattr(Path, "write_text", torn)
        _update(store, note)

    # ASSERT
    assert note.path.read_text(encoding="utf-8") == before
    assert list(note.path.parent.glob(".*.tmp")) == []  # no half-written temp file left behind


def test_update_note_when_two_writers_save_at_once_then_both_succeed_and_one_complete_note_wins(
    store: WikiStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARRANGE
    _existing_note(store)
    both_written = threading.Barrier(2)
    write_text = Path.write_text

    def in_step(path: Path, text: str, *args: Any, **kwargs: Any) -> int:
        written = write_text(path, text, *args, **kwargs)
        both_written.wait(timeout=5)  # both temp files exist before either is moved into place
        return written

    monkeypatch.setattr(Path, "write_text", in_step)
    notes = [store.load().find("finance").note("revenue") for _ in range(2)]

    def save(i: int) -> None:
        store.update_note(notes[i], title="Revenue", summary="S.", body=f"Body {i}.", tags=[], source={"resource": "/raw/x.md"})

    # ACT
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(save, range(2)))

    # ASSERT
    body = _read(notes[0].path).body
    assert body.startswith(("Body 0.", "Body 1.")) and "# See also" in body
    assert list(notes[0].path.parent.glob(".*.tmp")) == []


def test_lock_when_a_writer_holds_it_then_a_second_writer_waits_for_its_release(store: WikiStore) -> None:
    # ARRANGE
    first_in, release, second_in = threading.Event(), threading.Event(), threading.Event()

    def first() -> None:
        with store.lock():
            first_in.set()
            release.wait(timeout=5)

    def second() -> None:
        first_in.wait(timeout=5)
        with store.lock():
            second_in.set()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()

    # ACT
    waited = not second_in.wait(timeout=0.2)
    release.set()
    for thread in threads:
        thread.join(timeout=5)

    # ASSERT
    assert waited and second_in.is_set()


def test_update_note_never_repeats_a_source_id(store: WikiStore, bundle: Path) -> None:
    _put(bundle / "revenue.md", "---\ntype: Note\ntitle: Revenue\nsources:\n- id: s2\n  resource: /raw/kept.md\n---\n\nBody.\n")
    ids = [s["id"] for s in _update(store, store.load().note("revenue")).frontmatter["sources"]]
    assert len(ids) == len(set(ids)), ids


# -- links ----------------------------------------------------------------------


def test_link_adds_see_also_both_ways_across_folders(store: WikiStore) -> None:
    alpha, _ = store.create_folder(store.load(), "alpha", "A.")
    beta, _ = store.create_folder(store.load(), "beta", "B.")
    gamma, _ = store.create_folder(beta, "gamma", "G.")
    a, b = _note(store, alpha, "A", "About A."), _note(store, gamma, "B", "About B.")
    assert store.link(a, b) is True
    assert _read(a.path).body.endswith("\n# See also\n\n* [B](../beta/gamma/b.md) - About B.")
    assert _read(b.path).body.endswith("\n# See also\n\n* [A](../../alpha/a.md) - About A.")


def test_link_is_idempotent(store: WikiStore) -> None:
    root = store.load()
    a, b = _note(store, root, "A"), _note(store, root, "B")
    assert store.link(a, b) is True
    before = a.path.read_text(), b.path.read_text()
    assert store.link(a, b) is False
    assert store.link(b, a) is False
    assert store.link(a, a) is False
    assert (a.path.read_text(), b.path.read_text()) == before
    assert split_see_also(a.body)[1] == ["* [B](b.md) - A summary."]


def test_split_and_join_see_also_round_trip() -> None:
    body = "# Overview\n\nText.\n\n# See also\n\n* [A](a.md) - About A.\n* [B](../b.md)\n"
    main, entries = split_see_also(body)
    assert main == "# Overview\n\nText."
    assert entries == ["* [A](a.md) - About A.", "* [B](../b.md)"]
    assert join_see_also(main, entries) == body


def test_split_see_also_without_a_section() -> None:
    assert split_see_also("# Overview\n\nText.\n") == ("# Overview\n\nText.", [])
    assert join_see_also("# Overview\n\nText.", []) == "# Overview\n\nText.\n"


# -- raw, log, index --------------------------------------------------------------


def test_save_raw_writes_a_source_document(store: WikiStore, bundle: Path, clock: type[_Clock]) -> None:
    ref = store.save_raw("Original text.", title="Q3 Report", resource="https://example.com/q3")
    assert ref == "/raw/20260924-120000-q3-report.md"
    doc = _read(bundle / ref.lstrip("/"))
    assert doc.frontmatter == {
        "type": "Source",
        "title": "Q3 Report",
        "resource": "https://example.com/q3",
        "hash": "sha256:debbcd723a6ecc3c43f6b6140e113c1e7d546fb6be0dc9989f7ece129b53872b",
        "generated": {"by": ACTOR, "at": "2026-09-24T12:00:00+00:00"},
    }
    assert doc.body == "Original text."


def test_save_raw_never_overwrites_a_copy(store: WikiStore, bundle: Path, clock: type[_Clock]) -> None:
    first = store.save_raw("one", title="Notes", resource=None)
    second = store.save_raw("two", title="Notes", resource=None)
    assert second == first.replace(".md", "-2.md")
    assert "resource" not in _read(bundle / first.lstrip("/")).frontmatter
    assert _read(bundle / first.lstrip("/")).body == "one"


def test_append_log_starts_today_on_top(store: WikiStore, bundle: Path, clock: type[_Clock]) -> None:
    store.append_log("Created", "from /raw/x.md", "finance/revenue.md", "Revenue")
    assert (bundle / "log.md").read_text() == (
        "# Wiki log\n\n## 2026-09-24\n\n* **Created**: [Revenue](finance/revenue.md) - from /raw/x.md\n"
    )


def test_append_log_puts_newer_entries_first_within_the_day(store: WikiStore, bundle: Path, clock: type[_Clock]) -> None:
    store.append_log("Created", "first")
    store.append_log("Merged", "second", "a.md")
    assert (bundle / "log.md").read_text() == (
        "# Wiki log\n\n## 2026-09-24\n\n* **Merged**: [a.md](a.md) - second\n* **Created**: first\n"
    )


def test_append_log_keeps_older_days_below(store: WikiStore, bundle: Path, clock: type[_Clock]) -> None:
    _put(bundle / "log.md", "# Wiki log\n\n## 2026-09-20\n\n* **Created**: older\n")
    store.append_log("Created", "newer")
    assert (bundle / "log.md").read_text() == (
        "# Wiki log\n\n## 2026-09-24\n\n* **Created**: newer\n\n## 2026-09-20\n\n* **Created**: older\n"
    )


def test_write_index_omits_notes_in_a_folder_with_only_subfolders(store: WikiStore, bundle: Path) -> None:
    finance, _ = store.create_folder(store.load(), "finance", "Money.")
    store.create_folder(finance, "pricing", "Prices.")
    assert (bundle / "finance/index.md").read_text() == "# Subdirectories\n\n* [pricing](pricing/index.md) - Prices.\n"
    assert "# Notes" not in (bundle / "index.md").read_text()


def test_write_index_sanitizes_brackets_in_titles(store: WikiStore, bundle: Path) -> None:
    _note(store, store.load(), "[Draft] Plan", "Next steps.")
    assert parse_index((bundle / "index.md").read_text()) == [("(Draft) Plan", "draft-plan.md", "Next steps.")]


# -- Folder helpers -----------------------------------------------------------------


def _tree() -> tuple[Folder, Folder, Folder, Folder, Note]:
    root = Folder(Path("/w"), "")
    a = Folder(Path("/w/a"), "a", "A.", parent=root)
    b = Folder(Path("/w/a/b"), "a/b", "B.", parent=a)
    c = Folder(Path("/w/c"), "c", "C.", parent=root)
    note = Note(Path("/w/a/b/n.md"), "a/b/n.md", {"title": "N", "description": "About N."}, "", b)
    root.subfolders, a.subfolders, b.notes = [a, c], [b], [note]
    return root, a, b, c, note


def test_folder_walk_and_lookups() -> None:
    root, a, b, c, note = _tree()
    assert list(root.walk()) == [root, a, b, c]
    assert root.all_notes() == [note]
    assert (a.subfolder("b"), a.subfolder("x")) == (b, None)
    assert (b.note("n"), b.note("x")) == (note, None)


def test_folder_find_by_bundle_path() -> None:
    root, _, b, c, _ = _tree()
    assert root.find("") is root
    assert root.find("a/b") is b
    assert root.find("/a/b/") is b
    assert root.find("c") is c
    assert root.find("x") is None


def test_folder_ancestors_depth_label_and_name() -> None:
    root, a, b, _, _ = _tree()
    assert b.ancestors() == [root, a, b]
    assert root.ancestors() == [root]
    assert [f.depth for f in (root, a, b)] == [0, 1, 2]
    assert [f.label for f in (root, a, b)] == ["/", "/a", "/a/b"]
    assert [f.name for f in (root, a, b)] == ["/", "a", "b"]
    assert (root.is_root, a.is_root) == (True, False)


def test_read_returns_a_note_by_bundle_path(tmp_path: Path) -> None:
    store = WikiStore(tmp_path, actor="t/1")
    store.init()
    root = store.load()
    folder, _ = store.create_folder(root, "finance", "Money.")
    store.write_note(folder, title="Revenue", summary="How revenue is booked.", body="Body.", tags=[], source={"resource": "/raw/x.md"})

    note = store.read("/finance/revenue.md")

    assert (note.rel, note.title, note.summary) == ("finance/revenue.md", "Revenue", "How revenue is booked.")


@pytest.mark.parametrize("rel", ["../outside.md", "index.md", "log.md", "finance", "notes.txt"])
def test_read_refuses_paths_that_are_not_wiki_documents(tmp_path: Path, rel: str) -> None:
    store = WikiStore(tmp_path / "wiki", actor="t/1")
    store.init()
    (tmp_path / "outside.md").write_text("---\ntype: Secret\n---\n", encoding="utf-8")
    with pytest.raises(ValueError):
        store.read(rel)


def test_graph_has_folders_notes_containment_and_see_also_links(tmp_path: Path) -> None:
    store = WikiStore(tmp_path, actor="t/1")
    store.init()
    root = store.load()
    finance, _ = store.create_folder(root, "finance", "Money.")
    pricing, _ = store.create_folder(finance, "pricing", "Prices.")
    source = {"resource": "/raw/x.md"}
    a = store.write_note(finance, title="Revenue", summary="How revenue is booked.", body="B.", tags=[], source=source)
    b = store.write_note(pricing, title="Discounts", summary="Discount bands.", body="B.", tags=[], source=source)
    c = store.write_note(root, title="Overview", summary="The wiki.", body="B.", tags=[], source=source)
    store.link(a, b)

    g = graph(store.load())

    kinds = {n["id"]: (n["kind"], n.get("area")) for n in g["nodes"]}
    assert kinds == {"/": ("folder", ""), "/finance": ("folder", "finance"), "/finance/pricing": ("folder", "finance"),
                     a.rel: ("note", "finance"), b.rel: ("note", "finance"), c.rel: ("note", "")}
    folders = {n["id"]: n["notes"] for n in g["nodes"] if n["kind"] == "folder"}
    assert folders == {"/": 3, "/finance": 2, "/finance/pricing": 1}
    edges = {(e["source"], e["target"], e["kind"]) for e in g["links"]}
    assert ("/", "/finance", "contains") in edges and ("/finance/pricing", b.rel, "contains") in edges
    assert [e for e in g["links"] if e["kind"] == "see_also"] == [{"source": a.rel, "target": b.rel, "kind": "see_also"}]


# -- the lock holds between threads, with or without flock -------------------------------------------------------


@pytest.mark.parametrize("flock", [True, False], ids=["flock", "no-flock"])
def test_lock_when_two_stores_on_one_folder_lock_from_two_threads_then_they_take_turns(
    bundle: Path, monkeypatch: pytest.MonkeyPatch, flock: bool
) -> None:
    if not flock:  # as on Windows: importing fcntl fails, and only the lock within the process is left
        monkeypatch.setitem(sys.modules, "fcntl", None)
    first, second = WikiStore(bundle, actor=ACTOR), WikiStore(bundle / ".", actor=ACTOR)  # two paths, one folder
    first.init()
    inside, waited = threading.Event(), threading.Event()

    def hold() -> None:
        with first.lock():
            inside.set()
            assert not waited.wait(0.2), "the second store got in while the first held the lock"

    with ThreadPoolExecutor(1) as pool:
        held = pool.submit(hold)
        assert inside.wait(5)
        with second.lock():
            waited.set()
        held.result(5)
