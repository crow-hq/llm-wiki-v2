# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A spreadsheet sheet as a source: one note per sheet, its data kept as CSV for the researcher.

The text of a sheet source is a line naming it, a "Caption:" line when a title sits above the sheet's header, and
a CSV block. That text is the raw copy (so its hash says
whether the sheet changed); the librarian writes the note from `brief`, the first rows only, because the note
catalogues the sheet rather than holding its data; the researcher puts back the rows a question needs (`rows_for`).
"""

from __future__ import annotations

import csv
import io
import re

BRIEF_ROWS = 20  # data rows the librarian reads to write a sheet's note
SHEET_CHARS = 20_000  # characters of CSV the researcher gives for one sheet (most sheets fit whole)

_HEAD = re.compile(r'\ASheet "(?P<name>.*)" of (?P<file>.*): (?P<rows>\d+) rows?, (?P<cols>\d+) columns?\.\n')
_BLOCK = re.compile(r"^(?P<fence>`{3,})csv\n(?P<csv>.*?)^(?P=fence)$", re.S | re.M)
_WORD = re.compile(r"[^\W_]+(?:[.,]\d+)*")
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)*")


def sheet_text(filename: str, name: str, rows: list[list[str]]) -> str:
    """The source text of one sheet: "Sheet "Budget" of plan.xlsx: 40 rows, 6 columns.", its caption, the rows as CSV."""
    caption, rows = _caption(rows)
    body = _csv(rows)
    fence = _fence(body)
    width = max((len(r) for r in rows), default=0)
    line = f"Caption: {caption}\n" if caption else ""
    return f'Sheet "{name}" of {filename}: {len(rows)} rows, {width} columns.\n{line}\n{fence}csv\n{body}{fence}\n'


def parse(text: str) -> tuple[str, list[list[str]]] | None:
    """(sheet name, rows) of a sheet source's text; None for any other text."""
    head, block = _HEAD.match(text), _BLOCK.search(text)
    if head is None or block is None:
        return None
    return head.group("name"), list(csv.reader(io.StringIO(block.group("csv"))))


def brief(text: str) -> str:
    """What the librarian reads of a sheet source: its first line, caption and first BRIEF_ROWS rows; other texts unchanged."""
    sheet = parse(text)
    if sheet is None:
        return text
    _, rows = sheet
    if len(rows) <= BRIEF_ROWS + 1:
        return text
    head = text.split("\n\n", 1)[0]  # the first line and the caption line
    shown = _csv(rows[: BRIEF_ROWS + 1])
    fence = _fence(shown)
    more = len(rows) - BRIEF_ROWS - 1
    return f"{head}\n\nThe first {BRIEF_ROWS} rows, under the header:\n\n{fence}csv\n{shown}{fence}\n\n[… {more} more rows]\n"


def rows_for(question: str, text: str, budget: int = SHEET_CHARS) -> str:
    """The rows of a sheet source a question needs, as a block for the researcher; "" for any other text.

    The whole sheet when it fits in `budget`. Otherwise the header, then the rows sharing the most words or
    numbers with the question (in sheet order), a line saying how many are shown, and per numeric column its
    count, sum, minimum and maximum, so that totals are not guessed from part of the rows.
    """
    sheet = parse(text)
    if sheet is None:
        return ""
    name, rows = sheet
    if not rows:
        return ""
    whole = _csv(rows)
    if len(whole) <= budget:
        fence = _fence(whole)
        return f'Rows of the sheet "{name}" (all {len(rows) - 1}):\n\n{fence}csv\n{whole}{fence}'
    header, body = rows[0], rows[1:]
    stats = _stats(header, body)
    wanted = _terms(question)
    ranked = sorted(range(len(body)), key=lambda i: -len(_terms(" ".join(body[i])) & wanted))
    room = budget - len(_csv([header])) - len(stats) - 200
    keep: list[int] = []
    for i in ranked:
        size = len(_csv([body[i]]))
        if size > room:
            break
        room -= size
        keep.append(i)
    shown = _csv([header, *(body[i] for i in sorted(keep))])
    return (
        f'Rows of the sheet "{name}": {len(keep)} of {len(body)}, those closest to the question.\n\n'
        f"{_fence(shown)}csv\n{shown}{_fence(shown)}\n\n{stats}"
    ).rstrip()


def _caption(rows: list[list[str]]) -> tuple[str, list[list[str]]]:
    """(caption, rows): up to 5 leading rows with one filled cell, above a row with three or more within the next
    three, are a caption ("Table 4 — Sales by region"), not data; the columns then empty in every row are dropped."""
    k = 0
    while k < min(5, len(rows) - 1) and sum(1 for c in rows[k] if c.strip()) == 1:
        k += 1
    if not k or not any(sum(1 for c in r if c.strip()) >= 3 for r in rows[k : k + 3]):
        return "", rows
    caption = " — ".join(" ".join(next(c for c in r if c.strip()).split()) for r in rows[:k])  # one line, whatever the cells hold
    rest = rows[k:]
    used = [i for i in range(max(map(len, rest))) if any(i < len(r) and r[i] for r in rest)]
    return caption, [[r[i] if i < len(r) else "" for i in used] for r in rest]


def _csv(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def _value(cell: str) -> float | None:
    """A cell as a number: "1.234,56", "1,234.56", "12%", "€ 3" and "1234" all count; None for text."""
    s = re.sub(r"[\s€$£%]", "", cell)
    if not re.fullmatch(r"-?[\d.,]+", s) or not any(ch.isdigit() for ch in s):
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if re.fullmatch(r"-?\d+,\d{1,2}", s) else s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _terms(text: str) -> set[str]:
    """Words cut to 5 letters (so "vendite" meets "vendita") and numbers as numbers."""
    out = set()
    for w in _WORD.findall(text.lower()):
        n = _value(w) if _NUMBER.fullmatch(w) else None
        if n is not None:
            out.add(repr(n))
        elif len(w) >= 3:
            out.add(w[:5])
    return out


def _stats(header: list[str], body: list[list[str]]) -> str:
    lines = []
    for c, name in enumerate(header):
        values = [_value(r[c]) for r in body if c < len(r) and r[c]]
        numbers = [v for v in values if v is not None]
        if numbers and len(numbers) >= 0.8 * len(values):
            label = name or f"column {c + 1}"
            lines.append(f"- {label}: {len(numbers)} values, sum {_fmt(sum(numbers))}, min {_fmt(min(numbers))}, max {_fmt(max(numbers))}")
    return ("Numeric columns over all rows:\n" + "\n".join(lines)) if lines else ""


def _fmt(x: float) -> str:
    return f"{x:,.10g}"


def _fence(body: str) -> str:
    """A code fence longer than any run of backticks in `body`, so that no cell can close it."""
    return "`" * max(3, 1 + max((len(m) for m in re.findall(r"`+", body)), default=0))
