# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The rules of benchmarks/stability.py added for the long2 corpus: merge_with_doc by note, alt_folders, extra label keys."""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest

from tests.wiki.test_stability_metrics import load

CREATED = ["created"]


@pytest.fixture(scope="module")
def stability() -> ModuleType:
    return load()


# -- stability.py: merge_with_doc by note, alt_folders, extra label keys ------------------------


def lab(kind: str, folders: list[str], **more: Any) -> dict[str, Any]:
    return {"kind": kind, "folders": folders, "merge_into": None, "group": None, **more}


def run_of(existing: list[str] | None = None, **docs: tuple[str, list[str], str | None]) -> dict[str, Any]:
    """A run record: each document is (folder, outcome, note it ended in)."""
    return {
        "docs": {d: {"folder": folder, "outcome": outcome, "note": note} for d, (folder, outcome, note) in docs.items()},
        "existing_folders": existing or ["/", "/customers", "/operations"],
    }


def test_merge_accuracy_when_the_update_merged_into_the_note_where_the_other_document_ended_then_it_is_correct(
    stability: ModuleType,
) -> None:
    labels = {"first": lab("existing", ["/customers"]), "u": lab("update", ["/customers"], merge_with_doc="first")}
    run = run_of(
        first=("/customers", ["merged", "customers/a.md"], "customers/a.md"),
        u=("/customers", ["merged", "customers/a.md"], "customers/a.md"),
    )

    assert stability.accuracy(run, labels)["merge_accuracy"] == 1.0


def test_merge_accuracy_when_the_update_merged_into_another_note_than_the_other_documents_then_it_is_not_correct(
    stability: ModuleType,
) -> None:
    labels = {"first": lab("existing", ["/customers"]), "u": lab("update", ["/customers"], merge_with_doc="first")}
    run = run_of(
        first=("/customers", ["merged", "customers/a.md"], "customers/a.md"),
        u=("/customers", ["merged", "customers/b.md"], "customers/b.md"),
    )

    assert stability.accuracy(run, labels)["merge_accuracy"] == 0.0


def test_merge_accuracy_when_the_update_merged_with_the_named_document_then_it_is_still_correct(stability: ModuleType) -> None:
    labels = {"first": lab("existing", ["/customers"]), "u": lab("update", ["/customers"], merge_with_doc="first")}
    run = run_of(first=("/customers", CREATED, "customers/a.md"), u=("/customers", ["merged-with", "first"], "customers/a.md"))

    assert stability.accuracy(run, labels)["merge_accuracy"] == 1.0


def test_false_merge_rate_when_a_document_merged_into_the_note_of_its_merge_with_doc_then_it_is_not_false(stability: ModuleType) -> None:
    labels = {"first": lab("new", ["/x"]), "u": lab("new", ["/x"], merge_with_doc="first")}
    run = run_of(first=("/x", CREATED, "x/a.md"), u=("/x", ["merged", "x/a.md"], "x/a.md"))

    assert stability.accuracy(run, labels)["false_merge_rate"] == 0.0


def test_false_merge_rate_when_a_document_merged_into_a_note_of_another_document_then_it_is_false(stability: ModuleType) -> None:
    labels = {"first": lab("new", ["/x"]), "u": lab("new", ["/x"], merge_with_doc="first")}
    run = run_of(first=("/x", CREATED, "x/a.md"), u=("/x", ["merged", "x/other.md"], "x/other.md"))

    # one of the two documents (u) is a false merge
    assert stability.accuracy(run, labels)["false_merge_rate"] == pytest.approx(1 / 2)


def test_new_alt_rate_is_the_share_of_new_documents_in_one_of_their_alt_folders(stability: ModuleType) -> None:
    labels = {k: lab("new", ["/lumen"], group="g", alt_folders=["/operations"]) for k in "abc"}
    run = run_of(a=("/operations", CREATED, None), b=("/coffee", CREATED, None), c=("/tea", CREATED, None))

    assert stability.accuracy(run, labels)["new_alt_rate"] == pytest.approx(1 / 3)


def test_new_alt_rate_when_the_new_documents_have_no_alt_folders_then_zero(stability: ModuleType) -> None:
    labels = {k: lab("new", ["/lumen"]) for k in "ab"}
    run = run_of(a=("/coffee", CREATED, None), b=("/tea", CREATED, None))

    assert stability.accuracy(run, labels)["new_alt_rate"] == 0.0


def test_new_alt_rate_leaves_out_the_documents_that_are_not_new(stability: ModuleType) -> None:
    labels = {"a": lab("new", ["/lumen"], alt_folders=["/operations"]), "b": lab("existing", ["/customers"], alt_folders=["/operations"])}
    run = run_of(a=("/operations", CREATED, None), b=("/operations", CREATED, None))

    assert stability.accuracy(run, labels)["new_alt_rate"] == 1.0


def test_alt_folders_when_present_then_new_accuracy_and_group_accuracy_do_not_change(stability: ModuleType) -> None:
    plain = {k: lab("new", ["/lumen"], group="g") for k in "abc"}
    alt = {k: lab("new", ["/lumen"], group="g", alt_folders=["/operations"]) for k in "abc"}
    run = run_of(a=("/operations", CREATED, None), b=("/coffee", CREATED, None), c=("/tea", CREATED, None))

    without, with_alt = stability.accuracy(run, plain), stability.accuracy(run, alt)

    assert with_alt["new_accuracy"] == without["new_accuracy"] == pytest.approx(2 / 3)  # the existing folder does not count
    assert with_alt["group_accuracy"] == without["group_accuracy"]


def test_accuracy_when_the_labels_have_secondary_sections_and_facts_then_no_metric_changes(stability: ModuleType) -> None:
    base = {
        "a": lab("existing", ["/customers"]),
        "u": lab("update", ["/operations"], merge_into=["ops/x.md"]),
        "n": lab("new", ["/lumen"], group="g", alt_folders=["/operations"]),
    }
    extra = {
        k: {
            **v,
            "secondary": ["/customers", "/operations"],
            "sections": [{"heading": "H", "folder": "/customers", "chars": 100}],
            "facts": [{"fact": "LMN-4471", "kind": "headline", "section": "H", "offset": 1, "position": "begin", "beyond_100k": False}],
        }
        for k, v in base.items()
    }
    run = run_of(a=("/operations", CREATED, None), u=("/customers", ["merged", "ops/x.md"], "ops/x.md"), n=("/coffee", CREATED, None))

    assert stability.accuracy(run, extra) == stability.accuracy(run, base)
