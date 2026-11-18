# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`log.md` on disk: one line per change, in the order the changes came (oldest first), appended at the end.

    # Wiki log (oldest first)

    ## 2026-11-14

    * **Created**: [Title](folder/note.md) - what it says
    * **Moved**: ...

    ## 2026-11-15

    * ...

An entry is one line added at the end of the file (and a `## YYYY-MM-DD` heading before it when the day, in UTC, is not the
day of the last heading): the cost of a change to the log does not grow with the log. The heading is the real date of the
entry, whatever the clock did before: a clock that jumped back leaves headings out of order, which `check` reports as a
warning and no code corrects. Two headings of the same day are allowed.

A line that a purge took the text out of (`WikiStore.scrub_log`) is overwritten where it stands by a line of the same number of bytes:
`* **Created**: [purged]` and as many blanks as it takes to fill the line, then ` · by <author>` if the line had one. So the file keeps
its size and the position of every other line, and every reader that takes the log line by line reads a line that says "purged".

A log written by 1.0.5 or earlier holds the newest day first and, in a day, the newest line first. `newest_first_to_oldest_first`
turns it around once, exactly (the days' blocks in the opposite order, the lines of each block in the opposite order; no
sorting by date, so a history with jumps keeps its sequence).

`LogTail` is what a store remembers of the file between two appends, so that an append reads nothing: its size, its last bytes,
the file's identity, the last heading and, once asked, the last "Split" attempt of each folder. A file that is not as the tail
says (another process appended, `scrub_log` or an editor rewrote it) is read again, once.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import os
import re
from dataclasses import dataclass
from pathlib import Path

TITLE = "# Wiki log (oldest first)"
LEGACY_TITLE = "# Wiki log"
SPLIT_VERB = "Split"
HEAD_BYTES = 64  # the start of a log that says which order it is in
TAIL_BYTES = 64  # the last bytes of the log a `LogTail` compares
BLOCK = 65536  # what is read from the end of a log, a block at a time, to find its last heading

_SPLIT_LINE = re.compile(r"^\* \*\*" + SPLIT_VERB + r"\*\*: \[[^\]]*\]\((?P<folder>.*)/index\.md\) - at (?P<at>\d+) notes")


def is_current(head: bytes) -> bool:
    """Whether the first bytes of a log are the title of the oldest-first order."""
    return head.startswith(TITLE.encode("utf-8")) and head[len(TITLE) : len(TITLE) + 1] in (b"\n", b"\r", b"")


def newest_first_to_oldest_first(text: str) -> str:
    """A log of 1.0.5 and earlier (newest day first, newest line first in a day) in the order of arrival."""
    lines = text.splitlines()
    first = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
    preamble = [line for line in lines[:first] if line.strip() and not line.startswith("# ")]
    blocks: list[tuple[str, list[str]]] = []
    for line in lines[first:]:
        if line.startswith("## "):
            blocks.append((line, []))
        else:
            blocks[-1][1].append(line)
    out = [TITLE, "", *([*preamble, ""] if preamble else [])]
    for heading, body in reversed(blocks):
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        out += [heading, "", *reversed(body), ""]
    return "\n".join(out).rstrip() + "\n"


def split_marks(text: str) -> dict[str, int]:
    """The most notes each folder held when a split was last tried on it, by the "Split" lines of a log (folder path -> notes)."""
    marks: dict[str, int] = {}
    for line in text.splitlines():
        if m := _SPLIT_LINE.match(line):
            marks[m["folder"]] = max(marks.get(m["folder"], 0), int(m["at"]))
    return marks


def last_heading(path: Path, size: int) -> str:
    """The last `## ` heading of the log ("" if none): read from the end, a block at a time, as far back as it takes."""
    with open(path, "rb") as f:
        end, tail = size, b""
        while end > 0:
            start = max(0, end - BLOCK)
            f.seek(start)
            tail = f.read(end - start) + tail
            end = start
            index = tail.rfind(b"\n## ")
            if index >= 0:
                heading = tail[index + 1 :].split(b"\n", 1)[0]
                return heading.decode("utf-8", errors="replace").rstrip("\r")
            if tail.startswith(b"## "):
                return tail.split(b"\n", 1)[0].decode("utf-8", errors="replace").rstrip("\r")
    return ""


def newest_lines(path: Path, limit: int) -> list[str]:
    """The last `limit` entries of an oldest-first log, newest first (reads from the end, a block at a time)."""
    found: list[str] = []
    with open(path, "rb") as f:
        end = f.seek(0, os.SEEK_END)
        carry = b""
        while end > 0 and len(found) < limit:
            start = max(0, end - BLOCK)
            f.seek(start)
            chunk = f.read(end - start) + carry
            end = start
            parts = chunk.split(b"\n")
            carry = parts[0] if start > 0 else b""
            for raw in reversed(parts[1:] if start > 0 else parts):
                if raw.startswith(b"* "):
                    found.append(raw.decode("utf-8", errors="replace").rstrip("\r"))
                    if len(found) >= limit:
                        break
    return found[:limit]


@dataclass
class LogTail:
    """What a store knows of `log.md` after it last read or wrote it (see the module's docstring)."""

    size: int = -1
    last: bytes = b""
    ident: tuple[int, int] = (0, 0)  # st_dev, st_ino: a rewrite by replacement is another file
    heading: str = ""
    splits: dict[str, int] | None = None

    def sync(self, path: Path) -> bool:
        """Make the tail the file's: True if it was, as it was remembered; False if the file was read again."""
        try:
            st = os.stat(path)
        except OSError:
            self.size, self.last, self.ident, self.heading, self.splits = -1, b"", (0, 0), "", None
            return False
        ident = (st.st_dev, st.st_ino)
        if st.st_size == self.size and ident == self.ident and self._last_bytes(path, st.st_size) == self.last:
            return True
        self.size, self.ident = st.st_size, ident
        self.last = self._last_bytes(path, st.st_size)
        self.heading = last_heading(path, st.st_size)
        self.splits = None
        return False

    @staticmethod
    def _last_bytes(path: Path, size: int) -> bytes:
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            return f.read(TAIL_BYTES)

    def appended(self, path: Path, added: bytes, heading: str | None, entry: str) -> None:
        """The store appended `added` (ending with `entry`, under `heading` if given) to the file this tail describes."""
        if self.size < 0:
            return
        self.size += len(added)
        self.last = (self.last + added)[-TAIL_BYTES:]
        try:
            st = os.stat(path)
            self.ident = (st.st_dev, st.st_ino)
            if st.st_size != self.size:  # someone else wrote too: read again at the next append
                self.size = -1
                return
        except OSError:
            self.size = -1
            return
        if heading is not None:
            self.heading = heading
        if self.splits is not None and (m := _SPLIT_LINE.match(entry)):
            self.splits[m["folder"]] = max(self.splits.get(m["folder"], 0), int(m["at"]))
