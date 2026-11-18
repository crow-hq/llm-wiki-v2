# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A child process of `test_journal`: it appends entries to a journal and writes each one it was told is durable to its own file.

    python -m tests.wiki.journal_child <bundle> <worker> <count> <ack-file> [hint=1|0] [segment=BYTES] [text=BYTES]

Each entry's text is `w<worker>-<n>` padded; `count` 0 means until killed. An id is written to the ack file only after
`append_entry` returned, so a record in the file that the journal lacks would be a confirmed record lost.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import os
import sys

from llmw2.bundle.journal import Journal


def main(argv: list[str]) -> int:
    root, worker, count, ack = argv[0], int(argv[1]), int(argv[2]), argv[3]
    opts = dict(a.split("=", 1) for a in argv[4:])
    journal = Journal(
        root,
        shared_hint=opts.get("hint", "1") == "1",
        segment_size=int(opts.get("segment", 16 << 20)),
    )
    size = int(opts.get("text", 200))
    fd = os.open(ack, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0))
    n = 0
    while count == 0 or n < count:
        text = f"w{worker}-{n} ".ljust(size, "x")
        ref = journal.append_entry(text, title=f"w{worker}", id=f"{worker:04x}{n:012x}")
        os.write(fd, f"{ref.id}\n".encode("ascii"))
        n += 1
    journal.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
