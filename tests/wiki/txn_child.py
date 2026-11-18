# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A child process of `test_txn`: one write section of the store, killed (`os._exit`) before the N-th durable system call.

    python -m tests.wiki.txn_child <bundle> <scenario> <crash-at>     crash-at 0: no crash, print the number of calls made
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import os
import sys
from collections.abc import Callable
from pathlib import Path

from llmw2.bundle import txn
from llmw2.bundle.store import WikiStore, write_atomic
from llmw2.bundle.tree import Folder

CALLS = ("replace", "fsync", "fdatasync", "write", "pwrite", "link", "ftruncate", "unlink", "rmdir", "mkdir", "rename")
GROUP = "a"  # the folder the scenarios work in; it holds notes "Note 1" ... "Note 6"


def setup(root: Path) -> None:
    """The bundle before a scenario: a folder with six notes, each with a raw copy and a log line."""
    store = WikiStore(root, actor="test")
    store.init()
    with store.lock():
        folder, _ = store.create_folder(store.load(), GROUP, "A folder.")
        for n in range(1, 7):
            raw = store.save_raw(f"Text of note {n}.", title=f"Note {n}", resource=None)
            store.write_note(
                folder, title=f"Note {n}", summary=f"About {n}.", body="# Overview\n\nFacts.", tags=[], source={"resource": raw}
            )
            store.append_log("Created", f"Note {n}")


def setup_linked(root: Path) -> None:
    """`setup`, and a log line for each note that names it by its path and its title, with an author (what a filing writes)."""
    setup(root)
    store = WikiStore(root, actor="test")
    with store.lock():
        for n in range(1, 7):
            store.append_log("Linked", "Facts of the note, with a secret in it", f"{GROUP}/note-{n}.md", f"Note {n}")


def _folder(store: WikiStore) -> Folder:
    found = store.load().find(GROUP)
    assert found is not None
    return found


def _new(store: WikiStore, links: int, declare: bool = False) -> None:
    folder = _folder(store)
    raw_slug = note_slug = None
    if declare:  # the way a filing does it: one undo record for the two files it makes and the two it appends to
        raw_slug, note_slug = store.declare_filing(folder, raw_title="New", note_title="New")
    raw = store.save_raw("A new text.", title="New", resource=None, slug=raw_slug)
    note = store.write_note(
        folder, title="New", summary="About new.", body="# Overview\n\nFacts.", tags=[], source={"resource": raw}, slug=note_slug
    )
    for other in folder.notes[:links]:
        if other is not note:
            store.link(note, other)
    store.append_log("Created", "New", note.rel, note.title)


def _merge(store: WikiStore) -> None:
    folder = _folder(store)
    note = folder.notes[0]
    raw = store.save_raw("More text.", title="More", resource=None)
    store.update_note(
        note, title="Note 1 revised", summary="About one, revised.", body="# Overview\n\nMore facts.", tags=["x"], source={"resource": raw}
    )
    store.append_log("Merged", "Note 1", note.rel, note.title)


def _split(store: WikiStore) -> None:
    folder = _folder(store)
    sub, _ = store.create_folder(folder, "sub", "A subject.", " (split)")
    for note in list(folder.notes)[:2]:
        store.move_note(note.rel, sub.rel)
    store.append_log("Split", "2 opened", f"{folder.rel}/index.md", folder.label)


def _tidy(store: WikiStore) -> None:
    folder = _folder(store)
    sub, _ = store.create_folder(folder, "lone", "One note.")
    note = folder.notes[0]
    store.move_note(note.rel, sub.rel)
    store.move_note(f"{sub.rel}/{note.path.name}", folder.rel)
    store.remove_empty_folder(sub.rel, "emptied")


def _delete(store: WikiStore) -> None:
    store.delete_note(f"{GROUP}/note-3.md")


def _append(store: WikiStore) -> None:
    txn.append(store.root / "log.md", "* **Appended**: one\n")
    txn.append(store.root / "log.md", "* **Appended**: two\n")
    txn.append(store.root / "fresh.txt", "new\n")


def _scrub(store: WikiStore) -> None:
    store.scrub_log([f"{GROUP}/note-3.md", f"{GROUP}/note-5.md"])


def _scrub_and_delete(store: WikiStore) -> None:
    store.delete_note(f"{GROUP}/note-3.md")
    store.scrub_log([f"{GROUP}/note-3.md", "[Note 3]("])


def _scrub_then_append(store: WikiStore) -> None:
    store.scrub_log([f"{GROUP}/note-2.md", f"{GROUP}/note-4.md"])
    store.append_log("Purged", "two lines")


def _scrub_then_rewrite(store: WikiStore) -> None:
    store.scrub_log([f"{GROUP}/note-2.md", f"{GROUP}/note-6.md"])
    path = store.root / "log.md"
    write_atomic(path, path.read_text(encoding="utf-8") + "* **Rewritten**: whole\n")


def _scrub_short(store: WikiStore) -> None:
    store.append_log("Tiny", "n.md")  # a line too short to hold the marker: the file is written whole
    store.scrub_log(["n.md", f"{GROUP}/note-1.md"])


# the scenarios of a log with linked lines (`setup_linked`): the purge of lines of log.md
SCRUBS: dict[str, Callable[[WikiStore], None]] = {
    "scrub": _scrub,
    "scrubdel": _scrub_and_delete,
    "scrubappend": _scrub_then_append,
    "scrubrewrite": _scrub_then_rewrite,
    "scrubshort": _scrub_short,
}

SCENARIOS: dict[str, Callable[[WikiStore], None]] = {
    "new0": lambda s: _new(s, 0),
    "new2": lambda s: _new(s, 2),
    "new5": lambda s: _new(s, 5),
    "newd0": lambda s: _new(s, 0, True),
    "newd2": lambda s: _new(s, 2, True),
    "merge": _merge,
    "split": _split,
    "tidy": _tidy,
    "delete": _delete,
    "append": _append,
}


def main(argv: list[str]) -> int:
    root, scenario, crash_at = Path(argv[1]), argv[2], int(argv[3])
    count = 0
    if os.environ.get("LLMW2_TXN_FORMAT"):  # "segment": the preallocated log (the default on POSIX); "line": the other one
        txn.PREALLOCATED = os.environ["LLMW2_TXN_FORMAT"] == "segment"

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
    store = WikiStore(root, actor="test")
    with store.lock(id="the-id"):
        {**SCENARIOS, **SCRUBS}[scenario](store)
    print(count)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
