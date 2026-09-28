# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from pathlib import Path

import pytest

from okf_wiki.bundle.check import check
from okf_wiki.bundle.store import WikiStore
from okf_wiki.bundle.tree import Folder, Note

SOURCE = {"resource": "/raw/source.md", "title": "Source"}


@pytest.fixture
def store(bundle: Path) -> WikiStore:
    wiki = WikiStore(bundle, actor="okf_wiki/test")
    wiki.init()
    return wiki


def _problems(bundle: Path) -> list[str]:
    return [str(p) for p in check(bundle)]


def _put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _note(store: WikiStore, folder: Folder, title: str, body: str = "# Overview\n\nText.") -> Note:
    return store.write_note(folder, title=title, summary=f"About {title}.", body=body, tags=[], source=SOURCE)


def test_a_wiki_built_by_the_store_has_no_problems(store: WikiStore, bundle: Path) -> None:
    root = store.load()
    finance, _ = store.create_folder(root, "finance", "Money.")
    pricing, _ = store.create_folder(finance, "pricing", "Prices.")
    raw = store.save_raw("Original text.", title="Q3 Report", resource="https://example.com/q3")
    overview = _note(store, root, "Overview", body=f"# Overview\n\nFrom [the report]({raw}).")
    tiers = _note(store, pricing, "Tiers")
    store.link(overview, tiers)
    store.update_note(tiers, title="Tiers", summary="Updated.", body="New body.", tags=["x"], source={"resource": raw})
    store.append_log("Created", f"from {raw}", tiers.rel, tiers.title)
    assert _problems(bundle) == []


def test_missing_type_is_reported(store: WikiStore, bundle: Path) -> None:
    note = _note(store, store.load(), "Revenue")
    _put(note.path, "---\ntitle: Revenue\n---\n\nBody.\n")
    assert _problems(bundle) == ["revenue.md: Missing required frontmatter keys: type"]


def test_unterminated_frontmatter_is_reported(store: WikiStore, bundle: Path) -> None:
    note = _note(store, store.load(), "Revenue")
    _put(note.path, "---\ntype: Note\n\nBody.\n")
    assert _problems(bundle) == ["revenue.md: Unterminated YAML frontmatter block"]


def test_a_sub_index_with_frontmatter_is_reported(store: WikiStore, bundle: Path) -> None:
    store.create_folder(store.load(), "finance", "Money.")
    _put(bundle / "finance/index.md", '---\nokf_version: "0.2"\n---\n\n# Notes\n')
    assert _problems(bundle) == ["finance/index.md: only the root index.md may carry frontmatter"]


def test_an_index_listing_a_missing_file_is_reported(store: WikiStore, bundle: Path) -> None:
    _note(store, store.load(), "Revenue").path.unlink()
    assert _problems(bundle) == ["index.md: lists missing revenue.md"]


def test_a_note_missing_from_its_index_is_reported(store: WikiStore, bundle: Path) -> None:
    store.create_folder(store.load(), "finance", "Money.")
    _put(bundle / "finance/orphan.md", "---\ntype: Note\n---\n\nBody.\n")
    assert _problems(bundle) == ["finance/index.md: does not list orphan.md"]


def test_an_unlisted_subfolder_is_reported(store: WikiStore, bundle: Path) -> None:
    _put(bundle / "finance/index.md", "# Notes\n")
    assert _problems(bundle) == ["index.md: does not list finance"]


def test_a_subfolder_entry_without_description_is_reported(store: WikiStore, bundle: Path) -> None:
    store.create_folder(store.load(), "finance", "")
    assert _problems(bundle) == ["index.md: subfolder finance/index.md has no description"]


def test_a_folder_without_index_is_reported(store: WikiStore, bundle: Path) -> None:
    store.create_folder(store.load(), "finance", "Money.")
    (bundle / "finance/index.md").unlink()
    assert _problems(bundle) == ["index.md: lists missing finance/index.md", "finance: folder has no index.md"]


def test_a_broken_relative_link_in_a_note_is_reported(store: WikiStore, bundle: Path) -> None:
    root = store.load()
    finance, _ = store.create_folder(root, "finance", "Money.")
    _note(store, root, "Target")
    body = "See [ok](../target.md), [anchored](../target.md#top), [absolute](/target.md) and [gone](../gone.md)."
    _note(store, finance, "Revenue", body=body)
    assert _problems(bundle) == ["finance/revenue.md: broken link to ../gone.md"]


def test_links_with_a_scheme_are_ignored(store: WikiStore, bundle: Path) -> None:
    _note(store, store.load(), "Revenue", body="See [the docs](https://example.com/docs/page.md).")
    assert _problems(bundle) == []


def test_a_log_heading_that_is_not_an_iso_date_is_reported(store: WikiStore, bundle: Path) -> None:
    _put(bundle / "log.md", "# Wiki log\n\n## Yesterday\n\n* **Created**: x\n")
    assert _problems(bundle) == ["log.md: log heading is not ## YYYY-MM-DD: '## Yesterday'"]


def test_log_days_out_of_order_are_reported(store: WikiStore, bundle: Path) -> None:
    _put(bundle / "log.md", "# Wiki log\n\n## 2026-01-01\n\n* **Created**: x\n\n## 2026-02-01\n\n* **Created**: y\n")
    assert _problems(bundle) == ["log.md: log days are not newest first"]


def test_raw_is_not_required_in_the_root_index(store: WikiStore, bundle: Path) -> None:
    store.save_raw("Original text.", title="Q3 Report", resource=None)
    assert "raw" not in (bundle / "index.md").read_text()
    assert not (bundle / "raw/index.md").exists()
    assert _problems(bundle) == []


def test_an_index_with_malformed_frontmatter_is_reported_not_raised(store: WikiStore, bundle: Path) -> None:
    store.create_folder(store.load(), "finance", "Money.")
    _put(bundle / "finance/index.md", "---\ntitle: unterminated\n")
    assert any(p.startswith("finance/index.md: ") for p in _problems(bundle))


def test_links_inside_raw_copies_are_not_checked(store: WikiStore, bundle: Path) -> None:
    store.save_raw("Read the [intro](docs/intro.md) first.", title="README", resource="https://example.com/readme")
    assert _problems(bundle) == []


def test_an_index_entry_with_a_dot_slash_link_counts_as_listed(store: WikiStore, bundle: Path) -> None:
    _note(store, store.load(), "Revenue")
    _put(bundle / "index.md", '---\nokf_version: "0.2"\n---\n\n# Notes\n\n* [Revenue](./revenue.md) - About Revenue.\n')
    assert _problems(bundle) == []
