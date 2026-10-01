# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The data the steps pass to one another: a source, the candidate note, a route, a rewritten note."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from llmw2.bundle.origin import Origin
from llmw2.bundle.tree import Folder


@dataclass
class Source:
    text: str
    title: str | None = None
    resource: str | None = None  # URL or path of the original, if any
    origin: Origin | None = None  # where the original lives at its source, if it has one
    fields: dict[str, Any] = field(default_factory=dict)  # user data, uninterpreted by the core


@dataclass
class Candidate:
    """The note an ingest would write (CROW step 0)."""

    title: str
    summary: str
    tags: list[str]
    body: str
    fields: dict[str, Any] = field(default_factory=dict)  # written to the note's frontmatter under `fields:`

    def compact(self, max_chars: int) -> str:
        """Title, summary and opening passage: the state every decision reads."""
        return f"Title: {self.title}\nSummary: {self.summary}\n\n{self.body}".strip()[:max_chars]


@dataclass
class Route:
    folder: Folder  # where the note goes
    new: bool  # file it in a new subfolder of `folder`
    candidates: list[Folder]  # folders whose notes are checked for consolidation
    # (name, description) per level to open below `folder`, reusing a subfolder of the same name; the note goes in the last one
    create: list[tuple[str, str]] = field(default_factory=list)


class NoteDraft(BaseModel):
    title: str
    summary: str
    tags: list[str] = []
    body: str
    fields: dict[str, Any] = {}
