# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: `check` does not report a broken relative link written inside a fenced code block or inline code."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2.bundle.check import check
from llmw2.bundle.store import WikiStore
from llmw2.bundle.tree import Folder, Note

SOURCE = {"resource": "/raw/source.md", "title": "Source"}
BROKEN = "[gone](../gone.md)"


@pytest.fixture
def store(bundle: Path) -> WikiStore:
    wiki = WikiStore(bundle, actor="llmw2/test")
    wiki.init()
    return wiki


def _problems(bundle: Path) -> list[str]:
    return [str(p) for p in check(bundle)]


def _note(store: WikiStore, folder: Folder, title: str, body: str) -> Note:
    return store.write_note(folder, title=title, summary=f"About {title}.", body=body, tags=[], source=SOURCE)


def _revenue(store: WikiStore, body: str) -> None:
    """finance/revenue.md with `body` written as is, whatever the store did to it."""
    finance, _ = store.create_folder(store.load(), "finance", "Money.")
    note = _note(store, finance, "Revenue", "# Overview\n\nText.")
    note.path.write_text(f"---\ntype: Note\ntitle: Revenue\n---\n\n{body}\n", encoding="utf-8")


def test_a_broken_link_in_plain_text_is_still_reported(store: WikiStore, bundle: Path) -> None:
    _revenue(store, f"See {BROKEN}.")

    assert _problems(bundle) == ["finance/revenue.md: broken link to ../gone.md"]


@pytest.mark.parametrize("fence", ["```", "~~~", "```python"])
def test_a_broken_link_in_a_fenced_code_block_is_not_reported(store: WikiStore, bundle: Path, fence: str) -> None:
    closing = fence[:3]
    _revenue(store, f"Example:\n\n{fence}\n{BROKEN}\n{closing}\n\nDone.")

    assert _problems(bundle) == []


def test_a_broken_link_in_inline_code_is_not_reported(store: WikiStore, bundle: Path) -> None:
    _revenue(store, f"Write it as `{BROKEN}` in a note.")

    assert _problems(bundle) == []


def test_the_same_link_in_plain_text_next_to_code_is_reported_once(store: WikiStore, bundle: Path) -> None:
    body = f"Plain {BROKEN}.\n\n```\n{BROKEN}\n```\n\nInline `{BROKEN}` and again plain [other](../other.md)."
    _revenue(store, body)

    assert _problems(bundle) == [
        "finance/revenue.md: broken link to ../gone.md",
        "finance/revenue.md: broken link to ../other.md",
    ]


def test_a_broken_link_after_a_closed_code_block_is_reported(store: WikiStore, bundle: Path) -> None:
    _revenue(store, f"```\ncode\n```\n\nThen {BROKEN}.")

    assert _problems(bundle) == ["finance/revenue.md: broken link to ../gone.md"]


def test_a_broken_link_after_a_closed_inline_code_span_on_the_same_line_is_reported(store: WikiStore, bundle: Path) -> None:
    _revenue(store, f"Use `code` then {BROKEN}.")

    assert _problems(bundle) == ["finance/revenue.md: broken link to ../gone.md"]


def test_a_good_link_in_code_and_a_good_link_in_text_raise_nothing(store: WikiStore, bundle: Path) -> None:
    _note(store, store.load(), "Target", "# Overview\n\nText.")
    _revenue(store, "See [ok](../target.md) and `[ok](../target.md)`.")

    assert _problems(bundle) == []
