# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What the steps share: the trace of decisions and the helpers every prompt and flow uses."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

from llmw2.bundle.tree import Folder, Note, slugify

T = TypeVar("T")


@dataclass
class Decision:
    """One decision of an ingest or a question, kept to calibrate thresholds (CROW §5.3)."""

    step: str  # route, match, consolidate, relate, navigate, select, …
    decider: Literal["llm", "classifier", "rule", "code"]  # "code": a check the code makes alone (revisions)
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


def fields_conflict(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool:
    """Whether two `fields` have a key in common with different values: such a note and document are never merged."""
    if not isinstance(a, Mapping) or not isinstance(b, Mapping):
        return False
    return any(a[k] != b[k] for k in a.keys() & b.keys())


OMITTED_MAX = 20  # headings a note records as omitted
_HEADING = re.compile(r"^ {0,3}#{1,3}[ \t]+(?P<text>.*?)[ \t#]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _words(text: str) -> set[str]:
    return set(re.findall(r"[^\W\d_]{4,}", text.lower()))


def source_headings(text: str) -> list[str]:
    """The Markdown headings (# to ###) of a text, in order, code blocks left out."""
    found: list[str] = []
    fence = ""
    for line in text.splitlines():
        if m := _FENCE.match(line):
            fence = "" if fence and m[1][0] == fence[0] else fence or m[1]
        elif not fence and (h := _HEADING.match(line)) and h["text"].strip():
            found.append(h["text"].strip())
    return found


def omitted_headings(source: str, body: str, earlier: Iterable[str] = ()) -> list[str]:
    """The headings of `source` that the note `body` leaves out: none of their words of four letters or more is in the body.

    A heading with no such word is not judged. `earlier`, the headings an earlier source left out, stay when the body still
    leaves them out. In order, once each, at most `OMITTED_MAX`. No model is asked.
    """
    have = _words(body)
    wanted = [*earlier, *source_headings(source)]
    kept = [h for h in dict.fromkeys(wanted) if (w := _words(h)) and not (w & have)]
    return kept[:OMITTED_MAX]
