# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A child process of `test_purge_inplace`: `Wiki.purge` of one memory, killed (`os._exit`) before the N-th durable system call.

    python -m tests.wiki.purge_child <bundle> <crash-at>     crash-at 0: no crash, print the number of calls made
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import os
import sys
from pathlib import Path

from llmw2 import Wiki, WikiConfig
from llmw2.bundle import txn
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM

CALLS = ("replace", "fsync", "fdatasync", "write", "pwrite", "link", "ftruncate", "unlink", "rmdir", "mkdir", "rename")
SECRET = "the purple falcon lands at midnight"
OTHER = "the green heron sleeps at noon"


def make(bundle: Path) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", concurrency=1), llm=ContentLLM(UsageTracker()))
    wiki.init()
    return wiki


def setup(bundle: Path) -> None:
    """Two memories filed, each with its raw copy, its note and its log line, and the text of both in the journal."""
    wiki = make(bundle)
    wiki.remember(f"topic: red-1\n{SECRET}", key="a")
    wiki.remember(f"topic: blue-1\n{OTHER}", key="b")
    wiki.drain()
    wiki.close()


def main(argv: list[str]) -> int:
    bundle, crash_at = Path(argv[1]), int(argv[2])
    if os.environ.get("LLMW2_TXN_FORMAT"):
        txn.PREALLOCATED = os.environ["LLMW2_TXN_FORMAT"] == "segment"
    wiki = make(bundle)
    count = 0

    def wrap(name: str) -> None:
        real = getattr(os, name)

        def counted(*args: object, **kwargs: object) -> object:
            nonlocal count
            count += 1
            if crash_at and count == crash_at:
                os._exit(3)
            return real(*args, **kwargs)

        setattr(os, name, counted)

    for name in CALLS:
        if hasattr(os, name):
            wrap(name)
    assert wiki.purge("remember::a", author="ada") is not None
    print(count, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
