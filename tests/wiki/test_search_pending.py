# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki.search` finds what `remember` keeps before it is filed, in every process: an entry of the journal is a hit `pending:<id>` until
the index has its note, or until a purge forgets it; nothing of it goes in the index, and `ask` does not see it."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from llmw2 import InputError, Wiki
from llmw2 import filer as F
from llmw2 import searchindex as SI
from tests.wiki.test_remember import make
from tests.wiki.test_search_index import _age

ROOT = Path(__file__).resolve().parents[2]
NARWHAL = "topic: sea-1\nThe narwhal of the north keeps code NW-4471 in its tusk."


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "RETRY_FIRST", 0.05)
    monkeypatch.setattr(F, "IDLE_POLL", 0.01)


def seeded(bundle: Path) -> Wiki:
    """A wiki of notes filed (an index built whole) and a filer that does not run: what is remembered stays queued."""
    w, _ = make(bundle)
    for i in range(4):
        w.store.write_note(
            w.store.load(), title=f"Whale {i}", summary=f"s{i}", body=f"whale of the sea number {i}", tags=[], source={"resource": f"/r{i}"}
        )
    _age(bundle)
    assert w.search("whale")  # builds the index
    w._stop_filer()
    return w


def paths(w: Wiki, query: str, k: int = 8, where: dict[str, Any] | None = None) -> list[str]:
    return [h.path for h in w.search(query, k, where)]


REMEMBERS = r"""
import sys
from pathlib import Path
from llmw2 import Wiki, WikiConfig
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM
w = Wiki(WikiConfig(bundle=Path(sys.argv[1]), mode="classic"), llm=ContentLLM(UsageTracker()))
w._stop_filer()  # nobody files
print(w.remember(sys.argv[2], key="narwhal", title="Narwhal", fields={"area": "sea"}).id, flush=True)
w.close()
"""


def test_an_entry_another_process_queued_is_found_before_it_is_filed(bundle: Path) -> None:
    w = seeded(bundle)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    p = subprocess.run([sys.executable, "-c", REMEMBERS, str(bundle), NARWHAL], capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr[-2000:]
    ident = p.stdout.strip()

    (hit,) = w.search("narwhal tusk", 8)
    assert hit.path == f"pending:{ident}" and hit.title == "Narwhal" and hit.summary == NARWHAL[: F.PENDING_SUMMARY]
    assert w.read_note(hit.path).body == NARWHAL
    assert paths(w, "narwhal", where={"area": "sea"}) == [hit.path] and paths(w, "narwhal", where={"area": "land"}) == []
    assert b"narwhal" not in _index_bytes(bundle)  # nothing of it in the index
    w.close()


def _index_bytes(bundle: Path) -> bytes:
    cache = bundle / ".llmw2" / "cache"
    return b"".join(p.read_bytes().lower() for p in cache.glob("search-*") if p.is_file())


def test_a_filed_entry_leaves_for_its_note_once(bundle: Path) -> None:
    w = seeded(bundle)
    r = w.remember(NARWHAL, key="narwhal")
    assert paths(w, "narwhal") == [f"pending:{r.id}"]
    w._filer_stopped = False
    w.drain(60)
    note = r.note
    assert note and paths(w, "narwhal") == [note]
    assert w._journal().shown == {}
    w.close()


def test_a_search_that_asks_the_index_about_the_queue_looks_at_its_file_once(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = seeded(bundle)
    r = w.remember(NARWHAL, key="narwhal")
    assert paths(w, "narwhal") == [f"pending:{r.id}"]
    w._filer_stopped = False
    w.drain(60)
    index = w._search.index
    real = index._ident
    seen: list[int] = []
    monkeypatch.setattr(index, "_ident", lambda: seen.append(1) or real())
    assert paths(w, "narwhal") == [r.note]  # the queue asks the index whether it has the entry's note, then the search runs
    assert len(seen) == 1
    w.close()

def test_a_purge_forgets_the_entry_and_drops_the_copy_the_search_kept(bundle: Path) -> None:
    w = seeded(bundle)
    r = w.remember(NARWHAL, key="narwhal")
    assert paths(w, "narwhal") == [f"pending:{r.id}"]
    assert w.purge("narwhal") is not None
    assert w._journal().shown == {}  # the purge's tombstone dropped the copy at once, before any search
    assert paths(w, "narwhal") == []
    with pytest.raises(FileNotFoundError):
        w.read_note(f"pending:{r.id}")
    w.close()


def test_an_entry_filed_while_the_index_lacks_its_note_stays_until_the_index_has_it(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = seeded(bundle)
    r = w.remember(NARWHAL, key="narwhal")
    assert paths(w, "narwhal") == [f"pending:{r.id}"]

    def failing(self: SI.SearchIndex, *a: Any, **k: Any) -> None:
        raise OSError("the index could not be written")

    monkeypatch.setattr(SI.SearchIndex, "update", failing)  # the hook fails: the note is filed, the index is not told
    monkeypatch.setattr(SI, "LISTING_LOOK", 3600.0)  # and no search lists the wiki meanwhile
    w._filer_stopped = False
    w.drain(60)
    note = r.note
    assert note and not w._journal().entries[r.id].text  # the entry is done: the journal's copy of the text is gone
    assert paths(w, "narwhal") == [f"pending:{r.id}"]  # the search's copy stays, the note is not in the index
    monkeypatch.setattr(SI, "LISTING_LOOK", 0.0)
    assert note in paths(w, "narwhal")  # the listing puts it in the index (the entry may be a hit this once)
    assert paths(w, "narwhal") == [note] and w._journal().shown == {}
    w.close()


def test_a_pending_path_is_read_but_not_moved_nor_deleted(bundle: Path) -> None:
    w = seeded(bundle)
    r = w.remember(NARWHAL, key="narwhal", title="Narwhal")
    path = f"pending:{r.id}"
    assert w.read_note(path).title == "Narwhal"
    with pytest.raises(InputError, match="forget it with its key"):
        w.delete_note(path)
    with pytest.raises(InputError, match="forget it with its key"):
        w.move_note(path, "/")
    with pytest.raises(FileNotFoundError):
        w.read_note("pending:no-such-entry")
    w.close()


def test_a_search_with_no_journal_makes_no_file(bundle: Path) -> None:
    w, _ = make(bundle)
    w.store.write_note(w.store.load(), title="Whale", summary="s", body="whale", tags=[], source={"resource": "/r"})
    assert paths(w, "whale")
    assert not list((bundle / ".llmw2" / "journal").glob("*.wal")) and w._hub is None
    w.close()


def test_ask_does_not_see_the_entries(bundle: Path) -> None:
    w = seeded(bundle)
    w.remember(NARWHAL, key="narwhal")
    assert paths(w, "narwhal")
    finder = w.researcher()._finder(w.store.load(), None)
    assert w.researcher().search is not None and finder("narwhal", 8) == []
    w.close()


def test_the_notes_keep_their_scores_and_order_and_a_note_comes_first_at_the_same_score(bundle: Path) -> None:
    w = seeded(bundle)
    before = paths(w, "whale sea number 2")
    r = w.remember("s2\n\nwhale of the sea number 2", key="twin", title="Whale 2")  # the words of whale-2.md, counted the same
    after = paths(w, "whale sea number 2")
    assert [p for p in after if not p.startswith("pending:")] == before[: len(after) - 1]
    assert after.index("whale-2.md") + 1 == after.index(f"pending:{r.id}")  # the same score: the note first
    w.close()


def test_the_journal_is_read_again_only_when_its_tail_moved(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared tail hint says when another process appended: a search reads the journal then, and not at every search."""
    w = seeded(bundle)
    w.remember("topic: sea-2\nA first memory of the seal.", key="seal")  # this process holds the hint now
    preparer = w._journal().journal._preparer
    if preparer is not None:
        preparer.join(60)  # the segments made ahead of time, in a thread of their own: done before anything is counted
    assert paths(w, "seal")
    monkeypatch.setattr(SI, "LISTING_LOOK", 3600.0)
    reads: list[int] = []
    real = F.Hub.refresh
    searching = threading.current_thread()

    def counted(self: F.Hub) -> None:
        if threading.current_thread() is searching:  # only the reads of the searches: another thread of the process is not counted
            reads.append(1)
        real(self)

    monkeypatch.setattr(F.Hub, "refresh", counted)
    for _ in range(3):
        assert len(paths(w, "seal")) == 1
    assert reads == []  # nothing new: the journal is not read
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    p = subprocess.run([sys.executable, "-c", REMEMBERS, str(bundle), NARWHAL], capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr[-2000:]
    assert paths(w, "narwhal") == [f"pending:{p.stdout.strip()}"] and reads  # the other process's entry, at the next search
    w.close()


def test_entries_at_the_same_score_come_in_the_order_they_were_acknowledged_whatever_a_process_read_first(bundle: Path) -> None:
    """A2: the order of a search is the same in every process. Two entries with the same words: the older first, then by id."""
    w = seeded(bundle)
    first = w.remember("topic: sea-3\nThe orca sings at dawn.", key="orca-1")
    second = w.remember("topic: sea-3\nThe orca sings at dawn.", key="orca-2")
    order = [f"pending:{first.id}", f"pending:{second.id}"]
    assert paths(w, "orca dawn") == order
    shown = w._journal().shown
    shown[first.id] = shown.pop(first.id)  # a process that read the second one first (a copy carried forward, another process)
    assert list(shown) == [second.id, first.id]
    assert paths(w, "orca dawn") == order
    w.close()


def test_a_pending_path_read_after_its_entry_was_filed_gives_the_note_it_went_in(bundle: Path) -> None:
    """A client cites or finds `pending:<id>`, the filer files it, then the client reads it: the note, from the journal's `F` record."""
    w = seeded(bundle)
    r = w.remember(NARWHAL, key="narwhal")
    path = f"pending:{r.id}"
    assert paths(w, "narwhal") == [path]
    w._filer_stopped = False
    w.drain(60)
    assert paths(w, "narwhal") == [r.note]
    note = w.read_note(path)
    assert note.rel == r.note and "NW-4471" in note.body
    w.close()


def test_a_pending_path_of_an_entry_forgotten_or_unknown_is_not_found_and_gives_no_text(bundle: Path) -> None:
    w = seeded(bundle)
    r = w.remember(NARWHAL, key="narwhal")
    w._filer_stopped = False
    w.drain(60)
    assert w.read_note(f"pending:{r.id}").rel == r.note
    assert w.purge("narwhal") is not None
    again = w.remember(NARWHAL, key="narwhal-again")  # the same text kept again: its note may take the path the purged one had
    w.drain(60)
    assert again.note
    for path in (f"pending:{r.id}", "pending:0123456789abcdef"):
        with pytest.raises(FileNotFoundError) as e:
            w.read_note(path)
        assert "NW-4471" not in str(e.value) and "narwhal" not in str(e.value)
    assert w.read_note(f"pending:{again.id}").rel == again.note
    w.close()
