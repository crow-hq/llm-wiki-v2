# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What a purge leaves of a note in the search index and the vectors: nothing. Deleted rows are zeroed (`secure_delete`), the WAL is
cut to nothing after the purge (or the purge says it could not, when a reader holds it), the files put aside go, and the rollback
journal of the vectors is cut."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import hashlib
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2 import filer as F
from llmw2 import searchindex as si
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM

WORD = "zorblaxquintet"


def embedder(texts: list[str]) -> list[list[float]]:
    return [[b / 255.0 for b in hashlib.sha256(t.encode("utf-8")).digest()[:8]] for t in texts]


def make(bundle: Path) -> Wiki:
    w = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=ContentLLM(UsageTracker()), embedder=embedder)
    w.init()
    return w


def cache_bytes(bundle: Path) -> bytes:
    return b"".join(p.read_bytes() for p in sorted((bundle / ".llmw2" / "cache").glob("*")) if p.is_file() and p.name != ".gitignore")


def test_after_a_purge_no_file_of_the_cache_holds_a_word_or_the_title_of_the_note(bundle: Path) -> None:
    w = make(bundle)
    try:
        for i in range(6):
            w.ingest(f"topic: plain-{i}\nRivers and hills, text {i}.", title=f"Plain {i}", resource=f"https://x/{i}")
        w.ingest(f"topic: odd-1\nThe {WORD} sleeps under the stone.", title="Odd stone note", resource="https://x/odd")
        assert w.search(WORD) and WORD.encode() in cache_bytes(bundle)
        text_key = F.TEXT_KEY + hashlib.sha256(f"topic: odd-1\nThe {WORD} sleeps under the stone.".encode()).hexdigest()
        report = w.purge(text_key)
        assert report is not None and report.pending == [] and len(report.notes) == 1
        data = cache_bytes(bundle)
        assert WORD.encode() not in data and b"Odd stone note" not in data
        assert w.search("rivers") and all("odd" not in h.path for h in w.search(WORD))
        wal = si.INDEX_FILE + "-wal"
        assert not (bundle / wal).exists() or (bundle / wal).stat().st_size == 0
    finally:
        w.close()


def test_a_reader_that_holds_the_wal_makes_the_purge_say_the_index_is_pending(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "FORGET_WAIT", 0.5)
    w = make(bundle)
    try:
        text = f"topic: odd-1\nThe {WORD} sleeps under the stone."
        w.ingest("topic: plain-1\nRivers and hills.", title="Plain", resource="https://x/1")
        w.ingest(text, title="Odd stone note", resource="https://x/odd")
        w.search(WORD)
        reader = sqlite3.connect(bundle / si.INDEX_FILE, isolation_level=None)  # another process's long read, as SQLite sees it
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM postings").fetchone()
        t0 = time.monotonic()
        report = w.purge(F.TEXT_KEY + hashlib.sha256(text.encode()).hexdigest())
        took = time.monotonic() - t0
        assert report is not None and report.pending == ["index"] and took < F.FORGET_WAIT + 5
        reader.execute("COMMIT")
        reader.close()
        assert w._search.forget(w.store, 1.0)  # the next checkpoint empties it
        assert WORD.encode() not in cache_bytes(bundle)
    finally:
        w.close()


def test_files_put_aside_as_bad_go_at_the_purge(bundle: Path) -> None:
    w = make(bundle)
    try:
        w.ingest("topic: plain-1\nRivers and hills.", title="Plain", resource="https://x/1")
        w.search("rivers")
        bad = bundle / (si.INDEX_FILE + ".bad")
        bad.write_bytes(WORD.encode() * 10)
        assert w._search.forget(w.store, 1.0) and not bad.exists()
    finally:
        w.close()


def test_a_file_made_without_secure_delete_is_made_again(bundle: Path) -> None:
    w = make(bundle)
    try:
        w.ingest("topic: plain-1\nRivers and hills.", title="Plain", resource="https://x/1")
        w.search("rivers")
        w._search.index.close()
        with closing(sqlite3.connect(bundle / si.INDEX_FILE)) as db, db:
            db.execute("DELETE FROM meta WHERE k = 'secure'")
        w2 = make(bundle)
        try:
            assert w2.search("rivers")
            with closing(sqlite3.connect(bundle / si.INDEX_FILE)) as db:
                assert dict(db.execute("SELECT k, v FROM meta")).get("secure") == 1
        finally:
            w2.close()
    finally:
        w.close()


def test_the_rollback_journal_of_the_vectors_is_kept_between_writes_and_cut_at_the_purge(bundle: Path) -> None:
    w = make(bundle)
    try:
        w.ingest("topic: plain-1\nRivers and hills.", title="Plain", resource="https://x/1")
        w.ingest("topic: plain-2\nHills and rivers again.", title="Plain two", resource="https://x/2")
        w.search("rivers")
        w.ingest("topic: plain-3\nA third text on hills.", title="Plain three", resource="https://x/3")
        w.search("hills")
        journal = w._search.vectors.path.with_name(w._search.vectors.path.name + "-journal")  # type: ignore[union-attr]
        assert journal.exists() and journal.stat().st_size > 0  # PERSIST: the file stays between writes
        w._search.vectors.truncate_journal()  # type: ignore[union-attr]
        assert journal.stat().st_size == 0
        with closing(sqlite3.connect(w._search.vectors.path)) as db:  # type: ignore[union-attr]
            assert db.execute("SELECT count(*) FROM vectors WHERE key = ''").fetchone()[0] == 0  # the row written to cut it is gone
    finally:
        w.close()
