# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The state a routing decision reads, built from the source text alone.

The LLM-written summary differs on every run even at temperature 0, so a decision that reads it flips. The text of the
source does not change: this state is a pure function of it (title, native summary, outline, excerpts).
"""

from __future__ import annotations

import bisect
import itertools
import math
import re
from typing import NamedTuple

# Bump when the construction below changes: it is part of the decision hash.
STATE_VERSION = 2

_MARKDOWN = re.compile(r"^#{1,6}\s+(.+)")
_NUMBERED = re.compile(
    r"^(\d+(\.\d+)*\.?|[IVXLC]+\.|(Art\.?|Articolo|Article|Capitolo|Chapter|Section|Sezione)\s*\d+[.:]?)\s+\S", re.IGNORECASE
)
_NUMBERING = re.compile(r"^(\d+(\.\d+)*\.?|[ivxlc]+\.|(art\.?|articolo|article|capitolo|chapter|section|sezione)\s*\d+[.:]?)\s+")
_SUMMARY_TERMS = ("abstract", "executive summary", "summary", "sommario", "sintesi", "riassunto", "riepilogo")
_INTRO_TERMS = ("introduction", "introduzione", "premessa", "overview", "scope", "oggetto", "purpose", "scopo")
# (heading, lines, level): the heading is empty for the text before the first one; the level is None when unknown
_Section = tuple[str, list[str], int | None]


def _flat(text: str) -> str:
    return " ".join(text.split())


def _heading(line: str, before: str, after: str, markdown: bool) -> str | None:
    """The heading text if the (stripped) line is a heading, else None; `before` and `after` are its neighbours.

    In a text with Markdown headings only those count: the other rules are for flat text, such as a PDF's.
    """
    if not line:
        return None
    if m := _MARKDOWN.match(line):
        return m.group(1)
    if markdown:
        return None
    if len(line) <= 80 and not line.endswith((".", ";", ":", ",")):
        if _NUMBERED.match(line):
            return line
        if sum(c.isalpha() for c in line) >= 3 and line.upper() == line:
            return line
    isolated = not before and not after
    if isolated and len(line) <= 60 and not line.endswith((".", ";", ":", ",", "!", "?")) and (line[0].isupper() or line[0].isdigit()):
        return line
    return None


def _level(line: str) -> int | None:
    """How deep a heading line is: its `#` count, or how many numbers its numbering has; None for capitals and the like."""
    if _MARKDOWN.match(line):
        return len(line) - len(line.lstrip("#"))
    if m := re.match(r"^(\d+(\.\d+)*)\.?\s", line):
        return m.group(1).count(".") + 1
    return 1 if _NUMBERED.match(line) else None


def _sections(body: str) -> list[_Section]:
    lines = [line.strip() for line in body.splitlines()]
    sections: list[_Section] = [("", [], None)]
    markdown = any(_MARKDOWN.match(line) for line in lines)
    for i, line in enumerate(lines):
        h = _heading(line, lines[i - 1] if i else "", lines[i + 1] if i + 1 < len(lines) else "", markdown)
        if h is None:
            sections[-1][1].append(line)
        else:
            sections.append((h, [], _level(line)))
    return sections


def _span(sections: list[_Section], i: int) -> range:
    """Section `i` and its subsections: the sections after it whose known level is deeper than its own."""
    level, end = sections[i][2], i + 1
    while level is not None and end < len(sections) and (sub := sections[end][2]) is not None and sub > level:
        end += 1
    return range(i, end)


def _text(sections: list[_Section], span: range) -> str:
    """The text of the sections in `span`, with the headings of its subsections, whitespace-collapsed."""
    return _flat(" ".join(" ".join(([sections[j][0]] if j != span.start else []) + sections[j][1]) for j in span))


def _normalized(heading: str) -> str:
    h = _flat(heading.lower().lstrip("#").strip().rstrip(":"))
    return _flat(_NUMBERING.sub("", h).rstrip(":"))


def _native(sections: list[_Section]) -> int | None:
    """Index of the section that already summarizes the document: a summary, else an introduction."""
    for terms in (_SUMMARY_TERMS, _INTRO_TERMS):
        for i, (heading, _, _) in enumerate(sections):
            if heading and _text(sections, _span(sections, i)) and _normalized(heading).startswith(terms):
                return i
    return None


def _outline(headings: list[str], room: int) -> str:
    head, tail = "Sections:\n", "- …\n\n"
    out = head + "\n".join("- " + h for h in headings) + "\n\n"
    if len(out) <= room:
        return out
    kept = head
    for h in headings:
        if len(kept + "- " + h + "\n" + tail) > room:
            break
        kept += "- " + h + "\n"
    return kept + tail


def source_state(title: str | None, text: str, max_chars: int, *, native: bool = True) -> str:
    """The state of a source for routing: the text whole if it fits, else its native summary, outline and excerpts.

    Pure and deterministic, never longer than `max_chars`. A document with headings gets its own summary (or
    introduction), the list of its sections and the opening of each; one without gets passages at even offsets.
    With `native=False` the native summary has no block of its own: its section is one of the others.
    """
    header = f"Title: {title.strip()}\n\n" if title and title.strip() else ""
    body = text.strip()
    if len(header + body) <= max_chars:
        return header + body
    if max_chars <= len(header):
        return header[:max_chars]
    budget = max_chars - len(header)
    sections = _sections(body)
    headings = [h for h, _, _ in sections[1:]]
    if len(headings) < 2:
        return (header + _passages(_flat(body), budget - len("Excerpts:\n")))[:max_chars]
    own = _native(sections) if native else None
    summary, covered = "", range(0)
    if own is not None:
        covered = _span(sections, own)  # its subsections belong to the summary, not to the excerpts
        summary = "Summary:\n" + _text(sections, covered)[: int(0.4 * budget)] + "\n\n"
    outline = _outline(headings, int(0.25 * budget))
    rest = [(h, _flat(" ".join(lines))) for i, (h, lines, _) in enumerate(sections) if i not in covered]
    rest = [(h, b) for h, b in rest if b]
    out = header + summary + outline
    if rest:
        remaining = max_chars - len(out) - len("Excerpts:\n")
        share = max(remaining, 0) // len(rest)
        out += "Excerpts:\n"
        for h, b in rest:
            prefix = f"[{h or '…'}] "
            out += prefix + b[: max(0, share - len(prefix))] + "\n"
    return out[:max_chars]


def _passages(flat: str, remaining: int) -> str:
    """`k` passages at evenly spaced offsets of the flattened text, filling `remaining` characters."""
    remaining = max(remaining, 0)
    k = max(1, min(8, remaining // 500))
    width = max(remaining // k - 1, 0)
    span = max(len(flat) - width, 0)
    out = "Excerpts:\n"
    for i in range(k):
        start = round(i * span / (k - 1)) if k > 1 else 0
        out += flat[start : start + width] + "\n"
    return out


class Block(NamedTuple):
    """A run of consecutive lines of a source: the title of the section it starts in, and its text."""

    heading: str  # "" without titles; " (continued)" is added when the block starts in the middle of a section
    text: str


_SENTENCE_END = re.compile(r"""[.!?]["')\]]*\s+""")


def split_blocks(text: str, max_chars: int) -> list[Block]:
    """The text cut into `ceil(len / max_chars)` blocks of about equal size, none longer than `max_chars`.

    Each cut goes at the section boundary nearest to an even split, else the nearest paragraph, sentence or, failing
    those (within half a block of the split), the even split itself.
    Pure, and `"".join(b.text for b in blocks) == text`: nothing is stripped or lost.
    """
    lines = text.splitlines(keepends=True)
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line))
    headings = _heading_starts(lines, starts)
    n = math.ceil(len(text) / max_chars)
    if n <= 1:
        return [Block(_heading_at(headings, 0), text)] if text else []
    sections = {start for start, _ in headings}
    paragraphs = {start for i, start in enumerate(starts) if i and not lines[i - 1].strip() and lines[i].strip()}
    sentences = {m.end() for m in _SENTENCE_END.finditer(text)}
    reach = len(text) / n / 2  # a boundary farther than half a block from the even split would leave a block and a crumb
    cuts = [0]
    for k in range(1, n):
        ideal = round(k * len(text) / n)
        # the block must fit, and so must what is left for the blocks to come
        lo, hi = max(cuts[-1] + 1, len(text) - (n - k) * max_chars), min(cuts[-1] + max_chars, len(text) - 1)
        for kind in (sections, paragraphs, sentences):
            near = [c for c in kind if lo <= c <= hi and abs(c - ideal) <= reach]
            if near:
                cuts.append(min(near, key=lambda c: (abs(c - ideal), c)))
                break
        else:
            cuts.append(min(max(ideal, lo), hi))
    cuts.append(len(text))
    return [Block(_heading_at(headings, a), text[a:b]) for a, b in itertools.pairwise(cuts)]


def _heading_starts(lines: list[str], starts: list[int]) -> list[tuple[int, str]]:
    """(offset, title) of every heading line, in order."""
    stripped = [line.strip() for line in lines]
    markdown = any(_MARKDOWN.match(line) for line in stripped)
    found = []
    for i, line in enumerate(stripped):
        h = _heading(line, stripped[i - 1] if i else "", stripped[i + 1] if i + 1 < len(stripped) else "", markdown)
        if h is not None:
            found.append((starts[i], h))
    return found


def _heading_at(headings: list[tuple[int, str]], offset: int) -> str:
    """The title of the section the text at `offset` is in, with " (continued)" when it is not at the title itself."""
    i = bisect.bisect_right([start for start, _ in headings], offset) - 1
    if i < 0:
        return ""
    return headings[i][1] if headings[i][0] == offset else f"{headings[i][1]} (continued)"
