# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Text out of the files people drop in: plain text, Markdown and PDFs with a text layer."""

from __future__ import annotations

import io
from pathlib import PurePath

from llmw2.errors import InputError

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text", ".csv", ".json", ".html", ".htm", ".rst", ""}
SUFFIXES = TEXT_SUFFIXES | {".pdf"}


def extract_text(data: bytes, filename: str) -> str:
    """The text of a file; InputError when the type is unsupported or no text is found."""
    suffix = PurePath(filename).suffix.lower()
    if suffix == ".pdf":
        text = _pdf_text(data)
        if not text.strip():
            raise InputError(f"{filename}: the PDF has no text layer (scanned?); run OCR first")
        return text
    if suffix not in TEXT_SUFFIXES:
        raise InputError(f"{filename}: unsupported file type {suffix!r} (use .txt, .md or .pdf)")
    if b"\x00" in data[:8192]:  # git's test for binary: text never holds a NUL byte
        raise InputError(f"{filename}: a binary file, not text")
    # One kind of line end: the same file saved on Windows and elsewhere is the same text, with the same hash.
    return data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def title_of(filename: str) -> str:
    """A readable title from a file name: "q3-board_minutes.pdf" → "q3 board minutes"."""
    return " ".join(PurePath(filename).stem.replace("_", " ").replace("-", " ").split()) or filename


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    except (PdfReadError, ValueError, KeyError) as e:
        raise InputError(f"cannot read the PDF: {e}") from e
