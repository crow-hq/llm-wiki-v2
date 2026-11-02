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
    file_name: str | None = None  # the file it was read from, when `title` is only made from that name

    @property
    def label(self) -> str:
        """The line that shows the source to the writer: by its file name when `title` is made from it."""
        return f"File name: {self.file_name}" if self.file_name else f"Source title: {self.title or '(none)'}"


@dataclass
class Candidate:
    """The note an ingest would write (CROW step 0)."""

    title: str
    summary: str
    tags: list[str]
    body: str
    fields: dict[str, Any] = field(default_factory=dict)  # written to the note's frontmatter under `fields:`
    state: str = ""  # what the routing decisions read; empty: built from the source text (see `agents.state`)

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
    # set when the limit on top-level folders sent the note to the likeliest one: the probabilities that made it, written to
    # the note's frontmatter as `overflow` (the likeliest existing root took it instead of a new one); None for a note filed as routed
    overflow: dict[str, float] | None = None


class PlannedFolder(BaseModel):
    path: str  # "/area" or "/area/subject"
    description: str


class FolderPlan(BaseModel):
    folders: list[PlannedFolder]  # required: a reply without it is no plan, never an empty one


class SplitFolder(BaseModel):
    name: str
    description: str
    notes: list[int] = []  # the numbers of the notes it takes, as the prompt listed them (from 1)


class SplitPlan(BaseModel):
    folders: list[SplitFolder]  # required, as in FolderPlan


class GrownArea(BaseModel):
    name: str
    description: str


class NoteDraft(BaseModel):
    title: str
    summary: str
    tags: list[str] = []
    body: str
    fields: dict[str, Any] = {}
