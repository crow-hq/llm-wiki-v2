# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What the steps share: the trace of decisions and the helpers every prompt and flow uses."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from llmw2.bundle.tree import Folder, Note, slugify

T = TypeVar("T")


@dataclass
class Decision:
    """One decision of an ingest or a question, kept to calibrate thresholds (CROW §5.3)."""

    step: str  # route, match, consolidate, relate, navigate, select, …
    decider: Literal["llm", "classifier", "rule"]
    choice: str
    confidence: float | None = None
    fallback: bool = False  # the classifier was not confident (or failed) and the LLM decided
    scores: dict[str, float] = field(default_factory=dict)  # classifier probability per option / candidate


def list_folders(folders: list[Folder]) -> str:
    """`- name: description` lines, the way every prompt shows folders."""
    return "\n".join(f"- {f.name}: {f.description or '(no description)'}" for f in folders) or "(none)"


def list_notes(notes: list[Note]) -> str:
    """`- path: Title — summary` lines, the way every prompt shows notes."""
    return "\n".join(f"- {n.rel}: {n.title} — {n.summary}" for n in notes) or "(none)"


def unique_objects(items: Iterable[T]) -> list[T]:
    """Keep the first occurrence of each object, in order."""
    seen: set[int] = set()
    out = []
    for item in items:
        if id(item) not in seen:
            seen.add(id(item))
            out.append(item)
    return out


def clean_tags(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(t for t in (slugify(t, max_len=30, fallback="") for t in tags) if t))[:6]
