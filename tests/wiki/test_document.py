# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import pytest

from llmw2.bundle.document import OKFDocument, OKFDocumentError

NOTE = (
    "---\n"
    "type: Note\n"
    "title: Refund policy\n"
    "tags: [refunds, policy]\n"
    "generated: {by: test, at: 2026-05-27T00:00:00Z}\n"
    "---\n"
    "\n"
    "# Refund policy\n"
    "\n"
    "Acme refunds orders within 30 days.\n"
)


def test_parse_splits_frontmatter_and_body() -> None:
    doc = OKFDocument.parse(NOTE)

    assert doc.frontmatter["type"] == "Note"
    assert doc.frontmatter["tags"] == ["refunds", "policy"]
    assert doc.body == "# Refund policy\n\nAcme refunds orders within 30 days."


def test_round_trip_is_byte_identical_and_keeps_timestamps_as_text() -> None:
    doc = OKFDocument.parse(NOTE)

    assert doc.frontmatter["generated"]["at"] == "2026-05-27T00:00:00Z"
    again = OKFDocument.parse(doc.serialize())
    assert again == doc
    assert again.serialize() == doc.serialize()


def test_serialize_keeps_key_order_and_unicode() -> None:
    text = OKFDocument({"type": "Note", "title": "Perché"}, "Corpo").serialize()

    assert text == "---\ntype: Note\ntitle: Perché\n---\n\nCorpo\n"


@pytest.mark.parametrize("text", ["# Hello\n\nNo frontmatter here.\n", ""])
def test_text_without_frontmatter_is_all_body(text: str) -> None:
    assert OKFDocument.parse(text) == OKFDocument({}, text)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("---\ntype: X\nstill in frontmatter\n", "Unterminated YAML frontmatter block"),
        ("---\ntype: [unclosed\n---\n", "Invalid YAML in frontmatter"),
        ("---\n- a\n- b\n---\n", "Frontmatter must be a YAML mapping"),
    ],
)
def test_malformed_frontmatter_raises(text: str, message: str) -> None:
    with pytest.raises(OKFDocumentError, match=message):
        OKFDocument.parse(text)


def test_empty_frontmatter_block_is_an_empty_mapping() -> None:
    assert OKFDocument.parse("---\n---\nBody\n") == OKFDocument({}, "Body")


def test_validate_requires_type_only() -> None:
    OKFDocument({"type": "Note"}).validate()
    with pytest.raises(OKFDocumentError, match="Missing required frontmatter keys: type"):
        OKFDocument({"title": "Untyped"}).validate()


def test_a_value_holding_a_delimiter_line_does_not_end_the_frontmatter() -> None:
    doc = OKFDocument({"type": "Note", "description": "Refund policy.\n---\nSecond line", "fields": {"a": "x\n---\ny"}}, "Body.\n")

    again = OKFDocument.parse(doc.serialize())

    assert again.frontmatter == doc.frontmatter
    assert again.body == "Body."


def test_a_byte_order_mark_before_the_frontmatter_is_ignored() -> None:
    doc = OKFDocument.parse("﻿" + NOTE)

    assert (doc.frontmatter["title"], doc.body) == ("Refund policy", "# Refund policy\n\nAcme refunds orders within 30 days.")


@pytest.mark.parametrize("separator", ["\x0c", "\u2028", "\x1e", "\x85"])
def test_characters_splitlines_breaks_on_stay_in_the_body(separator: str) -> None:
    body = f"Page one.{separator}Page two.\n"

    assert OKFDocument.parse(OKFDocument({"type": "Note"}, body).serialize()).body == body.rstrip("\n")
