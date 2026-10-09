# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Revisions replaced in place: the pure helpers and the store's part (archive, body hash)."""

from __future__ import annotations

from pathlib import Path

from llmw2.agents import revisions
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.store import WikiStore

OLD = "The price is 120 euro for the Alpha plan. Delivery takes ten days. Contact Mario Rossi for help."
NEW = "The price is 135 euro for the Alpha plan. Delivery takes ten days. Contact Mario Rossi for help."


def test_normalize_name() -> None:
    assert revisions.normalize_name("Wholesale programme review 2026 — rev. 2") == "wholesale programme review 2026"
    assert revisions.normalize_name("13-wholesale-programme-review-2026-rev2") == "wholesale programme review 2026"
    assert revisions.normalize_name("lotto_005") == "lotto 005"
    assert revisions.normalize_name("relazione-2025.pdf") != revisions.normalize_name("relazione-2026.pdf")


def test_changes_and_removed() -> None:
    assert revisions.value_changes(OLD, NEW) == [("120", "135", "The price is 135 euro for the Alpha plan.")]
    assert "(previously: 120)" in revisions.changes_section(OLD, NEW)
    assert revisions.removed_strings(OLD, NEW) == {"120"}
    assert revisions.changes_section(OLD, OLD) == ""


def test_archive_note_and_body_hash(tmp_path: Path) -> None:
    store = WikiStore(tmp_path, actor="test")
    store.init()
    note = store.write_note(store.load(), title="T", summary="S", body="Body.", tags=[], source={"resource": "/raw/a.md"})
    assert note.frontmatter["generated"]["body_hash"] == revisions.body_hash(note.main_body())
    archived = store.archive_note(note)
    assert archived.startswith("/.archive/t--")
    doc = OKFDocument.parse((tmp_path / archived.lstrip("/")).read_text(encoding="utf-8"))
    assert doc.frontmatter["status"] == "archived" and doc.frontmatter["archived_from"] == "/t.md"
    assert [n.rel for n in store.load().all_notes()] == ["t.md"]
    store.update_note(note, title="T", summary="S", body="New.", tags=[], source={"resource": "/raw/b.md"}, previous_version=archived)
    assert note.frontmatter["previous_version"] == archived
    assert note.frontmatter["generated"]["body_hash"] == revisions.body_hash("New.")
