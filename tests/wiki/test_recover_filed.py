# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The hole in `recover`: a process that dies after a section committed and before the `F` record is written. The commit carries the
id of the entry and what the section did, and whoever recovers writes the `F` record, durably, before the undo log is emptied."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import os
import subprocess
import sys
from pathlib import Path

import pytest

from llmw2.bundle import journal as J
from llmw2.bundle import txn
from llmw2.bundle.store import WikiStore
from tests.wiki import txn_child

ROOT = Path(__file__).resolve().parents[2]
RESULT = {"action": "created", "note": "a/note-7.md", "folder": "/a", "title": "Note 7", "related": [], "ms": 3}


def filed(root: Path) -> list[J.Record]:
    return [r for r in J.Journal(root, writable=False).read().records if r.type == J.T_FILED]


@pytest.fixture(params=["segment", "line"], autouse=True)
def log_format(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """The undo log as a preallocated segment (POSIX) and as lines (Windows): both owe the `F` record the same way."""
    monkeypatch.setattr(txn, "PREALLOCATED", request.param == "segment")
    return str(request.param)


@pytest.fixture
def base(tmp_path: Path) -> Path:
    root = tmp_path / "base"
    txn_child.setup(root)
    return root


def section(store: WikiStore, *, after_commit=None, result: dict | None = RESULT) -> None:
    with store.lock(id="e1", after_commit=after_commit):
        txn_child._new(store, 1)
        if result is not None:
            txn.current().set_result(result)  # type: ignore[union-attr]


def test_the_commit_record_carries_the_id_and_what_was_done(base: Path) -> None:
    store = WikiStore(base, actor="test")
    seen: list[dict] = []
    section(store, after_commit=lambda: seen.append(txn.read_records(base / txn.UNDO_FILE)[-1]))
    assert seen[0]["t"] == "commit" and seen[0]["id"] == "e1" and seen[0]["r"] == RESULT


def test_a_hook_that_fails_leaves_the_commit_and_the_next_recovery_writes_the_f_record(base: Path) -> None:
    store = WikiStore(base, actor="test")

    def hook() -> None:
        raise OSError("the journal could not be written")

    with pytest.raises(OSError):
        section(store, after_commit=hook)
    assert txn.pending(base) and filed(base) == []  # the section is done and is not rolled back; nobody has said so yet
    assert (base / "a" / "new.md").exists()
    done = WikiStore(base, actor="test").recover()
    assert done.committed and done.id == "e1" and done.result == RESULT
    got = filed(base)
    assert len(got) == 1 and got[0].meta["id"] == "e1" and got[0].meta["note"] == "a/note-7.md" and got[0].meta["action"] == "created"
    assert not txn.pending(base)


def test_the_log_is_emptied_only_after_the_f_record_is_durable(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = WikiStore(base, actor="test")
    with pytest.raises(OSError):
        section(store, after_commit=lambda: (_ for _ in ()).throw(OSError("x")))
    real = txn.file_committed

    def failing(root: Path, done: txn.Restored) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(txn, "file_committed", failing)
    with pytest.raises(OSError):
        WikiStore(base, actor="test").recover()
    assert txn.pending(base) and filed(base) == []  # still owed
    monkeypatch.setattr(txn, "file_committed", real)
    WikiStore(base, actor="test").recover()
    assert len(filed(base)) == 1 and not txn.pending(base)


def test_a_section_with_no_result_writes_no_f_record(base: Path) -> None:
    store = WikiStore(base, actor="test")
    with pytest.raises(OSError):
        section(store, after_commit=lambda: (_ for _ in ()).throw(OSError("x")), result=None)
    WikiStore(base, actor="test").recover()
    assert filed(base) == [] and not txn.pending(base)  # nothing is owed: a section with no result has no F record to write


def test_a_section_that_told_the_journal_owes_nothing_afterwards(base: Path) -> None:
    store = WikiStore(base, actor="test")
    told: list[str] = []
    section(store, after_commit=lambda: told.append("F"))
    assert told == ["F"] and not txn.pending(base)  # a log that still says `commit` is not owed: the section marked it
    WikiStore(base, actor="test").recover()
    assert filed(base) == []  # nobody wrote an F record again


def test_a_process_killed_between_the_commit_and_the_f_record(base: Path, log_format: str) -> None:
    code = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "from llmw2.bundle import txn\n"
        "from llmw2.bundle.store import WikiStore\n"
        "from tests.wiki import txn_child\n"
        "txn.PREALLOCATED = sys.argv[2] == 'segment'\n"
        "store = WikiStore(Path(sys.argv[1]), actor='test')\n"
        "with store.lock(id='e1', after_commit=lambda: os._exit(9)):\n"
        "    txn_child._new(store, 1)\n"
        f"    txn.current().set_result({RESULT!r})\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    p = subprocess.run([sys.executable, "-c", code, str(base), log_format], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert p.returncode == 9 and filed(base) == []
    store = WikiStore(base, actor="test")
    store.init()  # what any process does as it opens the wiki
    assert [r.meta["id"] for r in filed(base)] == ["e1"] and not txn.pending(base)
    assert (base / "a" / "new.md").exists()  # the note stays: the section was done
    store.recover()
    assert len(filed(base)) == 1  # nothing owed any more: not written twice


def test_the_result_in_the_commit_costs_no_flush(base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}
    names = [n for n in ("fsync", "fdatasync") if hasattr(os, n)]
    for name in names:
        real = getattr(os, name)

        def counted(*a: object, _real=real, **k: object) -> object:
            calls["n"] += 1
            return _real(*a, **k)

        monkeypatch.setattr(os, name, counted)
    store = WikiStore(base, actor="test")
    section(store, result=None)
    plain = calls["n"]
    calls["n"] = 0
    store2 = WikiStore(base, actor="test")
    with store2.lock(id="e2"):
        txn_child._new(store2, 1)
        txn.current().set_result(RESULT)  # type: ignore[union-attr]
    assert calls["n"] == plain


def test_finish_does_not_mark_the_f_record_written_when_no_hook_ran(base: Path, log_format: str) -> None:
    store = WikiStore(base, actor="test")
    section(store)  # an id and a result, no `after_commit`: nobody wrote the F record
    if log_format == "segment":
        assert txn.pending(base)  # still owed: the log was not marked
        WikiStore(base, actor="test").recover()
        assert len(filed(base)) == 1
