# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Text out of the files people drop in: plain text, Markdown, PDFs with a text layer, Office and OpenDocument."""

from __future__ import annotations

import io
from pathlib import PurePath

from llmw2.bundle import formats
from llmw2.bundle.sheets import sheet_text
from llmw2.errors import InputError

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text", ".csv", ".json", ".html", ".htm", ".rst", ""}
DOCUMENT_SUFFIXES = {".docx", ".pptx", ".odt"}
SHEET_SUFFIXES = {".xlsx", ".ods"}  # one source per sheet: see `sheet_sources`
SUFFIXES = TEXT_SUFFIXES | DOCUMENT_SUFFIXES | SHEET_SUFFIXES | {".pdf"}
READABLE = "text, Markdown, PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx) or OpenDocument (.odt, .ods)"


def extract_text(data: bytes, filename: str) -> str:
    """The text of a file; InputError when the type is unsupported or no text is found.

    A spreadsheet gives all its sheets, one after the other; `sheet_sources` gives them one by one.
    """
    suffix = PurePath(filename).suffix.lower()
    if suffix == ".pdf":
        text = _pdf_text(data)
        if not text.strip():
            raise InputError(f"{filename}: the PDF has no text layer (scanned?); run OCR first")
        return text
    if suffix in DOCUMENT_SUFFIXES:
        return formats.markdown(data)
    if suffix in SHEET_SUFFIXES:
        return "\n".join(text for _, text in sheet_sources(data, filename))
    if suffix not in TEXT_SUFFIXES:
        raise InputError(f"{filename}: unsupported file type {suffix!r} (readable: {READABLE})")
    if b"\x00" in data[:8192]:  # git's test for binary: text never holds a NUL byte
        raise InputError(f"{filename}: a binary file, not text")
    # One kind of line end: the same file saved on Windows and elsewhere is the same text, with the same hash.
    return data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def sheet_sources(data: bytes, filename: str) -> list[tuple[str, str]]:
    """(sheet name, source text) of each sheet of a spreadsheet that holds anything; InputError when there is none."""
    found = [(name, sheet_text(PurePath(filename).name, name, rows)) for name, rows in formats.sheets(data)]
    if not found:
        raise InputError(f"{filename}: the spreadsheet is empty")
    return found


def is_spreadsheet(filename: str) -> bool:
    return PurePath(filename).suffix.lower() in SHEET_SUFFIXES


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
