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

"""Text extraction from dropped files, and helpers other tests reuse to build PDFs."""

from __future__ import annotations

import pytest

from okf_wiki.files import extract_text, title_of


def make_pdf(*lines: str) -> bytes:
    """A minimal one-page PDF whose text layer holds `lines`."""
    text = " ".join(f"({line}) Tj 0 -20 Td" for line in lines)
    stream = f"BT /F1 12 Tf 72 720 Td {text} ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def test_text_and_markdown_are_decoded_as_utf8() -> None:
    assert extract_text("# Caffè\n\nPrezzi.".encode(), "note.md") == "# Caffè\n\nPrezzi."
    assert extract_text("plain".encode(), "a.TXT") == "plain"


def test_a_utf8_bom_is_dropped() -> None:
    assert extract_text("﻿hello".encode("utf-8"), "a.txt") == "hello"


def test_pdf_text_layer_is_extracted() -> None:
    text = extract_text(make_pdf("Refunds within 30 days.", "Digital goods 14 days."), "policy.pdf")
    assert "Refunds within 30 days." in text and "Digital goods 14 days." in text


def test_pdf_without_text_is_rejected() -> None:
    with pytest.raises(ValueError, match="no text layer"):
        extract_text(make_pdf(), "scan.pdf")


def test_broken_pdf_is_rejected() -> None:
    with pytest.raises(ValueError):
        extract_text(b"%PDF-1.4 not really", "broken.pdf")


def test_unsupported_types_are_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported file type"):
        extract_text(b"\x89PNG", "photo.png")


def test_title_of_turns_a_file_name_into_words() -> None:
    assert title_of("q3-board_minutes.pdf") == "q3 board minutes"
    assert title_of("README") == "README"
