# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Folder names and descriptions a writer copied from its prompt instead of writing them from the documents, in code.

A small writer copies what its prompt shows: the reply format ("/area/subject", "1 to 3 lowercase words joined by
hyphens"), the head of a listing ("title: opening"), the examples of the folder rules ("engineering", "suppliers"), the
codes of the file names ("a13a14a20"). A name is the writer's own when one of its words is a content word of the
documents it was shown: three letters or more, not a word that only says how to name or lay out a folder.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from llmw2.agents.prompts import Prompts
from llmw2.bundle.tree import slugify

# the words the prompts use to say how to name or lay out a folder: in a name they are copied, never content
FORMAT_WORDS = frozenset(
    {
        "area", "areas", "subject", "subjects", "opening", "title", "name", "path", "description", "folder", "folders",
        "subfolder", "subfolders", "new", "top", "level", "broad", "specific", "lowercase", "words", "word", "joined",
        "hyphens", "hyphenated", "letters", "digits", "plural", "noun"
    }
)
_LETTERS = re.compile(r"[a-z]{3,}")
# the prompts whose reply a note's title and summary are: a small writer copies their reply format into the note
SUMMARIZE_PROMPTS = ("librarian/summarize", "librarian/summarize_part", "librarian/summarize_combine", "shared/note_rules")


def ascii_lower(text: str) -> str:
    """The text lowercased, in ASCII: what folder names are made of."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _in(word: str, shown: str) -> bool:
    """`word` opens a word of `shown`, but for its last letters (plurals and words cut short match)."""
    return re.search(rf"\b{word[: max(4, len(word) - 3)]}", shown) is not None


def own_name(name: str, shown: str, taken: Iterable[str] = ()) -> str:
    """`name` as a folder name, without the counts that open it ("1-3-"), or "" when it is not the writer's own: none of
    its words is a content word of `shown` (the documents the writer was shown). Words in `taken` (the words of the
    parent's path, for a subfolder) do not count."""
    text, skip = ascii_lower(shown), FORMAT_WORDS | set(taken)
    if not _LETTERS.search(text):  # documents in a script folder names cannot spell: nothing to judge by
        return name
    tokens = slugify(name, max_len=40, fallback="").split("-")
    while tokens and tokens[0].isdigit():
        tokens.pop(0)
    for token in tokens:
        if any(w not in skip and _in(w, text) for w in _LETTERS.findall(token)):
            return "-".join(tokens)
    return ""


def _template(prompts: Prompts, prompt: str) -> str:
    """The prompt as the writer reads it, its values left empty."""
    return prompts.render(prompt, **dict.fromkeys(prompts.placeholders(prompt), ""))


def own_text(text: str, shown: str, prompts: Prompts, prompt: str) -> bool:
    """A description is the writer's own: one of its words, of four letters or more, is in `shown` and not in the
    prompt it was asked with (`prompt`, its values left empty)."""
    known, said = set(_LETTERS.findall(ascii_lower(_template(prompts, prompt)))), ascii_lower(shown)
    if not _LETTERS.search(said):  # documents in a script folder names cannot spell: nothing to judge by
        return True
    return any(len(w) >= 4 and w not in known and _in(w, said) for w in _LETTERS.findall(ascii_lower(text)))


def copied(text: str, prompts: Prompts, names: Iterable[str]) -> bool:
    """A text the writer copied from its prompts: it has words of three letters or more, and every one is a word of the
    prompts `names` ("<a specific title written from the content>" of summarize, "Take notes from a source")."""
    words = _LETTERS.findall(ascii_lower(text or ""))
    known = set().union(*(_LETTERS.findall(ascii_lower(_template(prompts, name))) for name in names))
    return bool(words) and all(w in known for w in words)
