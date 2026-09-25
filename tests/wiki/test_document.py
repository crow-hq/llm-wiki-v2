# Copyright 2026 Federico Cesarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import pytest

from okf_wiki.document import OKFDocument, OKFDocumentError

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
