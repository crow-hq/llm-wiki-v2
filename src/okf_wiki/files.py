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

"""Text out of the files people drop in: plain text, Markdown and PDFs with a text layer."""

from __future__ import annotations

import io
from pathlib import PurePath

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text", ".csv", ".json", ".html", ".htm", ".rst", ""}
SUFFIXES = TEXT_SUFFIXES | {".pdf"}


def extract_text(data: bytes, filename: str) -> str:
    """The text of a file; ValueError when the type is unsupported or no text is found."""
    suffix = PurePath(filename).suffix.lower()
    if suffix == ".pdf":
        text = _pdf_text(data)
        if not text.strip():
            raise ValueError(f"{filename}: the PDF has no text layer (scanned?); run OCR first")
        return text
    if suffix not in TEXT_SUFFIXES:
        raise ValueError(f"{filename}: unsupported file type {suffix!r} (use .txt, .md or .pdf)")
    return data.decode("utf-8-sig", errors="replace")


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
        raise ValueError(f"cannot read the PDF: {e}") from e
