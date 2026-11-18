# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki.purge` and the journal of `remember`: a tombstone forgets the entries of the key, filed or not, the segments that held them and
the undo log are zeroed, and nothing of the text is left in a file of the wiki. The tombstone holds the hash of the key, never the key;
it is completed (`O`) by whoever holds the lease, and a purge that was cut is taken up again from the hash."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from llmw2 import FilerError
from llmw2 import filer as F
from llmw2.bundle import journal as J
from llmw2.bundle import txn
from llmw2.bundle.filelock import FileLock
from llmw2.bundle.store import raw_sources
from tests.wiki.test_remember import make, notes, raws

ROOT = Path(__file__).resolve().parents[2]
SECRET = "the purple falcon lands at midnight by the old mill"
TEXT = f"topic: birds-1\n{SECRET}."
KEY = "mail:falcon-secret-7"
TITLE = "Falcon secret report"


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "RETRY_FIRST", 0.05)
    monkeypatch.setattr(F, "IDLE_POLL", 0.01)


def disk(bundle: Path) -> bytes:
    """Every byte of every file under the wiki's folder, the hidden ones included (a lock file that Windows holds is empty)."""
    found = []
    for p in sorted(bundle.rglob("*")):
        if p.is_file():
            try:
                found.append(p.read_bytes())
            except PermissionError:
                assert p.suffix == ".lock" and p.stat().st_size <= 1, p
    return b"".join(found)


def leftovers(bundle: Path, *texts: str) -> list[str]:
    data = disk(bundle)
    return [t for t in texts if t.encode("utf-8") in data or t.encode("utf-16-le") in data]


def tombstones(bundle: Path) -> list[J.Record]:
    return [r for r in J.Journal(bundle, writable=False).read().records if r.type == J.T_ENTRY and "forget" in (r.meta.get("state") or {})]


def pending(w: Any) -> list[F.Entry]:
    hub = w._journal()
    hub.refresh()
    return hub.forgets()


def wait_for(cond: Any, seconds: float = 30.0) -> None:
    deadline = time.monotonic() + seconds
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


# -- the entries of the key ----------------------------------------------------------------------------------------------------


def test_a_queued_entry_is_forgotten_and_nothing_of_it_is_left_in_the_wiki(bundle: Path) -> None:
    w, _ = make(bundle)
    try:
        w._stop_filer()  # nobody files: the entry stays queued
        r = w.remember(TEXT, key=KEY, title=TITLE)
        assert SECRET.encode() in disk(bundle)
        report = w.purge(KEY)
        assert report is not None and report.entries == 1 and report.pending == [] and report.notes == []
        assert r.state == "forgotten" and r.note is None
        with pytest.raises(FilerError, match="forgotten"):
            r.wait(1)
        assert w.status()["queue"] == 0 and w.status()["warnings"] == []
        assert leftovers(bundle, SECRET, TITLE, KEY) == []
    finally:
        w.close()


def test_a_filed_entry_goes_with_its_note_and_raw_copy_and_the_records_of_its_filing(bundle: Path) -> None:
    w, _ = make(bundle)
    try:
        r = w.remember(TEXT, key=KEY, title=TITLE)
        w.drain(60)
        assert r.state == "filed" and len(notes(bundle)) == 1
        report = w.purge(KEY)
        assert report is not None and report.entries == 1 and report.pending == [] and len(report.notes) == 1
        assert notes(bundle) == [] and raws(bundle) == [] and r.state == "forgotten"
        assert leftovers(bundle, SECRET, TITLE, KEY) == []
        assert [t.meta["state"]["forget"]["kh"] for t in tombstones(bundle)] == [F.key_hash(KEY)]  # the hash, never the key
    finally:
        w.close()


def test_a_memory_kept_without_a_key_is_purged_by_the_key_of_its_receipt(bundle: Path) -> None:
    w, _ = make(bundle)
    try:
        r = w.remember(TEXT)
        w.drain(60)
        assert r.key.startswith(F.TEXT_KEY) and len(raws(bundle)) == 1
        report = w.purge(r.key)
        assert report is not None and report.pending == [] and len(report.notes) == 1 and len(report.sources) == 1
        assert notes(bundle) == [] and raws(bundle) == [] and leftovers(bundle, SECRET) == []
    finally:
        w.close()


def test_the_same_text_remembered_after_the_purge_is_a_new_memory(bundle: Path) -> None:
    w, _ = make(bundle)
    try:
        first = w.remember(TEXT, key=KEY)
        w.drain(60)
        w.purge(KEY)
        again = w.remember(TEXT, key=KEY)
        assert again.id != first.id
        w.drain(60)
        assert again.state == "filed" and first.state == "forgotten" and len(notes(bundle)) == 1
    finally:
        w.close()


def test_a_purge_with_nothing_of_the_key_writes_nothing(bundle: Path) -> None:
    w, _ = make(bundle)
    try:
        assert w.purge("mail:nobody") is None
        assert not list((bundle / J.JOURNAL_DIR).glob("*.wal"))
    finally:
        w.close()


def test_a_dry_run_counts_the_entries_and_writes_nothing(bundle: Path) -> None:
    w, _ = make(bundle)
    try:
        w._stop_filer()
        w.remember(TEXT, key=KEY)
        w._journal().journal.prepare()  # the spare segment, made in a thread, is there before the bytes are taken
        before = disk(bundle)
        report = w.purge(KEY, dry_run=True)
        assert report is not None and report.entries == 1 and report.pending == []
        assert disk(bundle) == before and tombstones(bundle) == []
    finally:
        w.close()


def test_an_entry_given_up_is_cleared_by_the_purge_and_frees_its_place(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w, _ = make(bundle)
    try:
        w._stop_filer()
        r = w.remember(TEXT, key=KEY)
        hub = w._journal()
        for _ in range(F.ATTEMPTS):
            hub.journal.append(J.T_ATTEMPT, {"id": r.id, "error": "bad"})
        hub.journal.append(J.T_ABANDON, {"id": r.id, "error": "bad"})
        hub.refresh()
        assert hub.failed_count() == 1 and hub.backlog_bytes()[1] > 0
        w.purge(KEY)
        assert hub.failed_count() == 0 and hub.backlog_bytes() == (0, 0) and r.state == "forgotten"
        assert leftovers(bundle, SECRET) == []
    finally:
        w.close()


# -- how the hub reads a tombstone ---------------------------------------------------------------------------------------------


def small_hub(root: Path) -> F.Hub:
    hub = F.Hub(root)
    hub.journal.close()
    hub.journal = J.Journal(root, shared_hint=False, segment_size=1 << 20)
    hub.journal.prepare()
    return hub


def entry(j: J.Journal, ident: str, text: str, key: str, **kw: Any) -> J.Ref:
    return j.append_entry(text, key=key, hash="h-" + text[:20], id=ident, **kw)


def test_the_tombstone_wins_in_the_same_segment_and_for_a_reader_that_starts_after_it(tmp_path: Path) -> None:
    hub = small_hub(tmp_path)
    j = hub.journal
    entry(j, "a1", "alpha", "k1")
    entry(j, "b1", "beta", "k2")
    j.append_entry("", id="t1", state={"forget": {"kh": F.key_hash("k1")}})
    entry(j, "a1", "alpha", "k1", state={"attempts": 1, "error": "x"})  # a copy carried after the tombstone: stays forgotten
    j.append(J.T_FILED, {"id": "a1", "note": "x.md"})
    entry(j, "a2", "alpha again", "k1")  # remembered again after the purge: a new memory
    for h in (hub, small_hub(tmp_path)):  # the hub that saw it all, and a new one that reads from the start
        h.refresh()
        assert h.entries["a1"].forgotten and h.entries["a1"].filed is None and h.entries["a1"].text == ""
        assert [e.id for e in h.open_entries()] == ["b1", "a2"] and [t.id for t in h.forgets()] == ["t1"]


def test_a_carried_tombstone_does_not_forget_what_was_remembered_after_the_seal(tmp_path: Path) -> None:
    hub = small_hub(tmp_path)
    j = hub.journal
    entry(j, "a1", "alpha", "k1")
    upto = j.seal()
    entry(j, "a2", "alpha after the seal", "k1")
    j.append_entry("", id="t1", state={"forget": {"kh": F.key_hash("k1"), "upto": upto}})
    hub.refresh()
    assert hub.entries["a1"].forgotten and not hub.entries["a2"].forgotten


def test_after_o_the_tombstone_is_done_and_the_next_reuse_takes_it_away(tmp_path: Path) -> None:
    hub = small_hub(tmp_path)
    j = hub.journal
    entry(j, "a1", "alpha", "k1")
    entry(j, "b1", "beta", "k2")
    j.append_entry("", id="t1", state={"forget": {"kh": F.key_hash("k1")}})
    hub.refresh()
    assert F.close_forget(hub, hub.entries["t1"])
    assert hub.entries["t1"].done and [e.id for e in hub.open_entries()] == ["b1"]
    records = J.Journal(tmp_path, writable=False, shared_hint=False).read().records
    assert b"alpha" not in b"".join(r.text for r in records) and [r.kind for r in records].count("O") == 1
    j.append_entry("", id="t2", state={"forget": {"kh": F.key_hash("k2")}})
    hub.refresh()
    assert F.close_forget(hub, hub.entries["t2"])
    left = [r.meta.get("id") for r in J.Journal(tmp_path, writable=False, shared_hint=False).read().records if r.type == J.T_ENTRY]
    assert left == ["t2"]  # t1 was done: not carried; b1 forgotten by t2
    assert not any(b"alpha" in p.read_bytes() or b"beta" in p.read_bytes() for p in (tmp_path / J.JOURNAL_DIR).glob("*.wal"))


class Cut(Exception):
    pass


def test_a_purge_cut_in_the_middle_of_the_reuse_is_taken_up_to_the_same_segment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = small_hub(tmp_path)
    j = hub.journal
    entry(j, "a1", "alpha", "k1")
    j.seal()
    entry(j, "b1", "beta open", "k2")
    j.seal()
    entry(j, "a2", "alpha two", "k1")
    j.append_entry("", id="t1", state={"forget": {"kh": F.key_hash("k1")}})
    hub.refresh()
    real, calls = J.Journal.recycle, {"n": 0}

    def cut(self: J.Journal, number: int) -> bool:
        calls["n"] += 1
        if calls["n"] == 2:
            raise Cut("the power went in the middle of the reuse")
        return real(self, number)

    monkeypatch.setattr(J.Journal, "recycle", cut)
    with pytest.raises(Cut):
        F.close_forget(hub, hub.entries["t1"])
    monkeypatch.setattr(J.Journal, "recycle", real)
    upto = hub.entries["t1"].forget["upto"]  # type: ignore[index]
    again = small_hub(tmp_path)  # a new process: the tombstone's copy says up to where
    again.refresh()
    tomb = again.entries["t1"]
    assert tomb.forget == {"kh": F.key_hash("k1"), "upto": upto} and not tomb.done
    rotations = again.journal.rotations
    assert F.close_forget(again, tomb)
    assert again.journal.rotations == rotations  # nothing sealed anew
    assert [e.id for e in again.open_entries()] == ["b1"]
    data = b"".join(p.read_bytes() for p in (tmp_path / J.JOURNAL_DIR).glob("*.wal"))
    assert b"alpha" not in data and b"beta open" in data


def test_the_guard_of_the_compaction_holds_the_purge_back_and_status_says_so(tmp_path: Path) -> None:
    hub = small_hub(tmp_path)
    j = hub.journal
    entry(j, "big", "x" * 600_000, "k2")  # open, more than half a segment of 1 MiB: it cannot be carried
    j.append_entry("", id="t1", state={"forget": {"kh": F.key_hash("k1")}})
    hub.refresh()
    assert not F.close_forget(hub, hub.entries["t1"])
    assert hub.compact_stopped is not None and [t.id for t in hub.forgets()] == ["t1"]
    warnings = F.status(hub, tmp_path)["warnings"]
    assert any("purges are not complete" in x for x in warnings) and any("compaction is stopped" in x for x in warnings)


# -- the filing, the records of others, the undo log ---------------------------------------------------------------------------


def test_an_entry_purged_while_it_is_being_filed_writes_nothing(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "FORGET_WAIT", 0.5)
    w, llm = make(bundle)
    done: list[Any] = []
    real = llm.chat

    def chat(messages: list[dict[str, str]], **kw: Any) -> str:
        if not done:  # the first model call of the filing: a purge comes in from another thread meanwhile, and ends
            t = threading.Thread(target=lambda: done.append(w.purge(KEY)))
            t.start()
            t.join(30)
        return real(messages, **kw)

    monkeypatch.setattr(llm, "chat", chat)
    try:
        r = w.remember(TEXT, key=KEY)
        wait_for(lambda: bool(done) and not pending(w))
        assert done[0].pending == ["journal"]  # the filer of this process held the lease, busy with the entry
        assert r.state == "forgotten" and notes(bundle) == [] and raws(bundle) == []
        assert leftovers(bundle, SECRET, KEY) == []
    finally:
        w.close()


def test_the_record_of_a_filing_names_its_links_by_id(bundle: Path) -> None:
    w, _ = make(bundle)
    try:
        w.remember("topic: birds-1\nThe heron waits.", key="k:1")
        w.remember("topic: birds-2\nThe owl hunts at night.", key="k:2")
        r = w.remember("topic: birds-3\nThe swan sleeps.", key="k:3")
        w.drain(60)
        related = r.wait(10)["related"]
        ids = {n.id for n in w.store.load().all_notes()}
        assert related and set(related) <= ids
    finally:
        w.close()


@pytest.mark.skipif(sys.platform == "win32", reason="the undo log is a file of lines on Windows, cut to nothing after each section")
def test_the_undo_log_holds_no_path_of_the_purged_note_afterwards(bundle: Path) -> None:
    w, _ = make(bundle)
    try:
        w.remember(TEXT, key=KEY, title=TITLE)
        w.drain(60)
        name = notes(bundle)[0].stem.encode()
        assert name in (bundle / txn.UNDO_FILE).read_bytes()
        w.purge(KEY)
        assert name not in (bundle / txn.UNDO_FILE).read_bytes()
    finally:
        w.close()


def test_wipe_zeroes_every_byte_the_undo_log_used(tmp_path: Path) -> None:
    undo = tmp_path / txn.UNDO_FILE
    undo.parent.mkdir(parents=True)
    log = txn._SegmentLog(undo)
    log.open()
    for i in range(50):
        log.put({"t": "new", "p": f"notes/secret-path-{i}.md"})
    log.close()
    txn.wipe(tmp_path)
    data = undo.read_bytes()
    assert b"secret-path" not in data and J.parse_header(data) is not None and not any(data[J.HEADER_SIZE :])
    assert not txn.pending(tmp_path)


def test_a_merged_note_always_cites_the_raw_copy_of_what_was_merged_into_it(bundle: Path) -> None:
    """What lets a purge find the notes a document was merged into: consolidate adds the raw copy to the note's sources."""
    w, _ = make(bundle)
    try:
        w.remember("topic: birds-1\nThe heron waits.", key="k:1")
        w.remember("topic: birds-1\nThe heron flies at dawn.", key="k:2")
        w.drain(60)
        [note] = w.store.load().all_notes()
        cited = set(raw_sources(note.frontmatter))
        assert cited == {f"/raw/{p.name}" for p in raws(bundle)} and len(cited) == 2
        report = w.purge("k:2")
        assert report is not None and report.refiled == ["remember::k:1"] and "dawn" not in disk(bundle).decode("utf-8", "replace")
    finally:
        w.close()


# -- a purge cut, and the lease of another process -----------------------------------------------------------------------------


def test_a_tombstone_left_without_its_purge_is_finished_by_the_filer_from_the_hash(bundle: Path) -> None:
    w, _ = make(bundle)
    try:
        w.remember(TEXT, key=KEY, title=TITLE)
        w.drain(60)
        w._stop_filer()
        w._journal().journal.append_entry("", state={"forget": {"kh": F.key_hash(KEY)}})  # the purge died right after its tombstone
    finally:
        w.close()
    w2, _ = make(bundle)
    try:
        w2._start_filer()
        wait_for(lambda: not pending(w2))
        assert notes(bundle) == [] and raws(bundle) == [] and leftovers(bundle, SECRET, TITLE, KEY) == []
    finally:
        w2.close()


HOLDER = """
import sys, time
from pathlib import Path
from llmw2.bundle.filelock import FileLock
lock = FileLock(Path(sys.argv[1]) / ".llmw2/journal/filer.lock")
assert lock.try_acquire()
print("held", flush=True)
sys.stdin.read()
"""

FILER = """
import sys
from llmw2 import Wiki, WikiConfig
from llmw2 import filer as F
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM
F.IDLE_POLL = 0.01
w = Wiki(WikiConfig(bundle=sys.argv[1], mode="classic"), llm=ContentLLM(UsageTracker()))
w._start_filer()
print("filing", flush=True)
sys.stdin.read()
w.close()
"""


def lease_free(bundle: Path) -> bool:
    lock = FileLock(bundle / J.JOURNAL_DIR / "filer.lock")
    got = lock.try_acquire()
    if got:
        lock.release()
    return got


def child(code: str, bundle: Path) -> subprocess.Popen[str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    args = [sys.executable, "-c", code, str(bundle)]
    p = subprocess.Popen(args, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert p.stdout is not None and p.stdout.readline().strip() in ("held", "filing")
    return p


def test_a_purge_while_another_process_holds_the_lease_and_does_not_answer_is_pending_then_completed(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(F, "FORGET_WAIT", 0.5)
    w, _ = make(bundle)
    w.remember(TEXT, key=KEY, title=TITLE)
    w.drain(60)
    w._stop_filer()
    p = child(HOLDER, bundle)
    try:
        report = w.purge(KEY)
        assert report is not None and report.pending == ["journal"] and notes(bundle) == []
        assert any("purges are not complete" in x for x in w.status()["warnings"])
    finally:
        p.communicate("", timeout=30)
        w.close()
    w2, _ = make(bundle)  # the lease is free again: the next filer finishes it
    try:
        w2._start_filer()
        wait_for(lambda: not pending(w2))
        assert leftovers(bundle, SECRET, TITLE, KEY) == []
    finally:
        w2.close()


def test_a_purge_while_the_filer_of_another_process_holds_the_lease_is_completed_by_that_filer(bundle: Path) -> None:
    w, _ = make(bundle)
    w.remember(TEXT, key=KEY, title=TITLE)
    w.drain(60)
    w._stop_filer()
    wait_for(lambda: lease_free(bundle))
    p = child(FILER, bundle)
    try:
        wait_for(lambda: not lease_free(bundle))
        report = w.purge(KEY)
        assert report is not None and report.pending == []
        assert leftovers(bundle, SECRET, TITLE, KEY) == []
    finally:
        p.communicate("", timeout=60)
        w.close()


def test_a_purge_of_the_same_key_takes_up_a_tombstone_left_open(bundle: Path) -> None:
    w, _ = make(bundle)
    try:
        w.remember(TEXT, key=KEY, title=TITLE)
        w.drain(60)
        w._stop_filer()
        w._journal().journal.append_entry("", state={"forget": {"kh": F.key_hash(KEY)}})  # a purge cut right after its tombstone
        report = w.purge(KEY)
        assert report is not None and report.pending == [] and len(report.notes) == 1 and len(tombstones(bundle)) == 1
        assert not pending(w) and leftovers(bundle, SECRET, TITLE, KEY) == []
    finally:
        w.close()


def test_a_reuse_cut_while_it_zeroed_a_segment_is_finished_by_the_purge_taken_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = small_hub(tmp_path)
    j = hub.journal
    entry(j, "a1", "alpha secret bytes", "k1")
    j.append_entry("", id="t1", state={"forget": {"kh": F.key_hash("k1")}})
    hub.refresh()

    def cut(path: Path) -> None:
        raise Cut("the power went while the segment was zeroed")

    real = J._zero_used
    monkeypatch.setattr(J, "_zero_used", cut)
    with pytest.raises(Cut):
        F.close_forget(hub, hub.entries["t1"])
    monkeypatch.setattr(J, "_zero_used", real)
    again = small_hub(tmp_path)
    again.refresh()
    assert F.close_forget(again, again.entries["t1"])
    assert not any(b"alpha secret" in p.read_bytes() for p in (tmp_path / J.JOURNAL_DIR).glob("*.wal"))


def test_a_purge_of_another_key_completes_a_tombstone_left_open_when_nobody_files(bundle: Path) -> None:
    w, _ = make(bundle)
    try:
        w.remember(TEXT, key=KEY, title=TITLE)
        w.remember("topic: birds-2\nThe owl hunts at night.", key="k:other")
        w.drain(60)
        w._stop_filer()  # a wiki with no filer: the command line, `ingest` only
        w._journal().journal.append_entry("", state={"forget": {"kh": F.key_hash(KEY)}})  # the purge of KEY was cut after its tombstone
        report = w.purge("k:other")
        assert report is not None and report.pending == [] and not pending(w)
        assert leftovers(bundle, SECRET, TITLE, KEY) == [] and notes(bundle) == []
    finally:
        w.close()


def test_one_wait_in_all_with_a_reader_on_the_index_and_the_lease_held_elsewhere(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    from llmw2 import searchindex as si

    monkeypatch.setattr(F, "FORGET_WAIT", 3.0)
    w, _ = make(bundle)
    w.remember(TEXT, key=KEY, title=TITLE)
    w.drain(60)
    w.search("falcon")
    w._stop_filer()
    p = child(HOLDER, bundle)
    reader = sqlite3.connect(bundle / si.INDEX_FILE, isolation_level=None)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM postings").fetchone()
        t0 = time.monotonic()
        report = w.purge(KEY)
        took = time.monotonic() - t0
        assert report is not None and report.pending == ["journal", "index"]
        assert took < 2 * F.FORGET_WAIT  # the index and `O` share one wait, they are not waited for one after the other
    finally:
        reader.execute("COMMIT")
        reader.close()
        p.communicate("", timeout=30)
        w.close()


def test_a_filer_that_finds_only_the_index_left_does_not_scan_or_wipe_again(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    from llmw2 import searchindex as si
    from llmw2 import wiki as W

    monkeypatch.setattr(F, "FORGET_WAIT", 0.3)
    monkeypatch.setattr(F, "RETRY_FIRST", 0.2)
    calls = {"wipe": 0, "keys": 0}
    real_wipe, real_keys = txn.wipe, W.Wiki._purge_keys
    monkeypatch.setattr(txn, "wipe", lambda root: (calls.__setitem__("wipe", calls["wipe"] + 1), real_wipe(root))[1])
    monkeypatch.setattr(W.Wiki, "_purge_keys", lambda self: (calls.__setitem__("keys", calls["keys"] + 1), real_keys(self))[1])
    w, _ = make(bundle)
    try:
        w.remember(TEXT, key=KEY, title=TITLE)
        w.drain(60)
        w.search("falcon")
        reader = sqlite3.connect(bundle / si.INDEX_FILE, isolation_level=None)
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM postings").fetchone()
        w._journal().journal.append_entry("", state={"forget": {"kh": F.key_hash(KEY)}})  # taken up by the filer, the reader holds on
        time.sleep(3)
        assert calls["keys"] == 1 and calls["wipe"] == 1 and pending(w)
        tries = w._journal().filer.forget_tries  # type: ignore[union-attr]
        assert 1 <= tries <= 5  # the delay doubles: a few looks in 3 s, not one every 0.2 s
        reader.execute("COMMIT")
        reader.close()
        wait_for(lambda: not pending(w), 60)
        assert calls["keys"] == 1 and calls["wipe"] == 1
    finally:
        w.close()


def test_the_vector_of_the_note_purged_goes_at_the_purge_without_a_search_after(bundle: Path) -> None:
    import hashlib
    import sqlite3
    from contextlib import closing

    from llmw2 import Wiki, WikiConfig
    from llmw2.models.usage import UsageTracker
    from llmw2.search import VECTORS_FILE
    from tests.wiki.fakes import ContentLLM

    def embedder(texts: list[str]) -> list[list[float]]:
        return [[b / 255.0 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]

    w = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=ContentLLM(UsageTracker()), embedder=embedder)
    w.init()
    try:
        w.remember(TEXT, key=KEY, title=TITLE)
        w.remember("topic: birds-2\nThe owl hunts at night.", key="k:other")
        w.drain(60)
        w.search("falcon")
        path = bundle / VECTORS_FILE
        with closing(sqlite3.connect(path)) as db:
            before = {k for (k,) in db.execute("SELECT key FROM vectors")}
        report = w.purge(KEY)
        assert report is not None and report.pending == []
        with closing(sqlite3.connect(path)) as db:
            after = {k for (k,) in db.execute("SELECT key FROM vectors")}
        assert len(before) == 2 and len(after) == 1 and after < before
        gone = (before - after).pop()
        journal = path.with_name(path.name + "-journal")
        data = path.read_bytes() + (journal.read_bytes() if journal.exists() else b"")
        assert gone.encode() not in data  # the key and its page are zeroed, the journal cut
    finally:
        w.close()


def test_the_wait_for_o_starts_once_the_files_are_done(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from llmw2 import wiki as W

    monkeypatch.setattr(F, "FORGET_WAIT", 1.0)
    real_purge, real_close = W.Wiki._purge, W.Wiki._close_forget
    left: list[float] = []

    def slow(self: Any, *a: Any, **k: Any) -> Any:  # files and merged notes filed again that take longer than the whole wait
        got = real_purge(self, *a, **k)
        time.sleep(1.5)
        return got

    def close(self: Any, tomb_id: str, complete: bool, deadline: float) -> bool:
        left.append(deadline - time.monotonic())
        return real_close(self, tomb_id, complete, deadline)

    monkeypatch.setattr(W.Wiki, "_purge", slow)
    monkeypatch.setattr(W.Wiki, "_close_forget", close)
    w, _ = make(bundle)
    try:
        w.remember(TEXT, key=KEY)
        w.drain(60)
        w._stop_filer()
        report = w.purge(KEY)
        assert report is not None and report.pending == [] and left and left[0] > 0.5  # the wait was not used up by the files
    finally:
        w.close()


def test_an_ordinary_purge_says_its_files_are_done_and_nobody_scans_for_the_key(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from llmw2 import wiki as W

    scans = {"n": 0}
    real = W.Wiki._purge_keys

    def counted(self: Any) -> Any:
        scans["n"] += 1
        return real(self)

    monkeypatch.setattr(W.Wiki, "_purge_keys", counted)
    w, _ = make(bundle)
    try:
        w.remember(TEXT, key=KEY, title=TITLE)
        w.drain(60)
        report = w.purge(KEY)  # the filer of this process holds the lease: it writes `O`
        assert report is not None and report.pending == [] and scans["n"] == 0
        fresh = F.Hub(bundle)  # what another process reads: the mark is in the journal, carried with the tombstone
        fresh.refresh()
        assert next(t.forget for t in fresh.entries.values() if t.forget is not None).get("files") is True
        fresh.journal.close()
        w._stop_filer()
        w._journal().journal.append_entry("", state={"forget": {"kh": F.key_hash("k:gone")}})  # cut before its files: no mark
        w._finish_others("")  # what the next purge does with the tombstones of other keys
        assert scans["n"] == 1 and not pending(w)  # only the purge that was cut is scanned for
    finally:
        w.close()


WRITER_OF_TOMBSTONE = """
import sys
from llmw2.bundle.journal import Journal
from llmw2 import filer as F
j = Journal(sys.argv[1], segment_size=1 << 20, shared_hint=sys.argv[3] == "hint")
print("ready", flush=True)
sys.stdin.readline()
j.append_entry("", state={"forget": {"kh": F.key_hash(sys.argv[2])}})
j.close()
print("written", flush=True)
"""


def test_a_tombstone_written_by_another_process_in_the_middle_of_a_reuse_stops_the_copy_of_what_it_forgot(tmp_path: Path) -> None:
    """One read of the journal for the whole lot, and again only when somebody else wrote: a tombstone that another process writes
    between two copies is seen before the next one."""
    hub = F.Hub(tmp_path)
    hub.journal.close()
    hub.journal = J.Journal(tmp_path, segment_size=1 << 20)  # the shared hint, as every process has it
    hub.journal.prepare()
    j = hub.journal
    for i in range(6):
        entry(j, f"e{i}", f"text number {i}", f"k{i}")
    j.seal()
    entry(j, "z0", "after", "kz")
    hub.refresh()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    p = subprocess.Popen([sys.executable, "-c", WRITER_OF_TOMBSTONE, str(tmp_path), "k4", "hint"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True, env=env)
    assert p.stdout is not None and p.stdout.readline().strip() == "ready"
    real, copies, reads = F.carry, [], {"n": 0}
    real_refresh = hub.refresh

    def counted() -> None:
        reads["n"] += 1
        real_refresh()

    def watched(h: F.Hub, e: F.Entry, **k: Any) -> J.Ref:
        copies.append(e.id)
        ref = real(h, e, **k)
        if e.id == "e2":  # the other process writes its tombstone now, between two copies
            assert p.stdin is not None and p.stdout is not None
            p.stdin.write("go\n")
            p.stdin.flush()
            assert p.stdout.readline().strip() == "written"
        return ref

    hub.refresh = counted  # type: ignore[method-assign]
    F.carry = watched
    try:
        assert F.reuse(hub, 1)
    finally:
        F.carry = real
        p.communicate(timeout=30)
    assert copies == ["e0", "e1", "e2", "e3", "e5"] and hub.entries["e4"].forgotten  # e4 forgotten meanwhile: not copied
    assert reads["n"] <= 4  # the lot's read, one after the other process wrote, the last: not one a copy
    data = b"".join(x.read_bytes() for x in (tmp_path / J.JOURNAL_DIR).glob("*.wal"))
    assert b"text number 4" not in data and b"text number 3" in data  # never copied forward, and the segment zeroed


def test_a_tombstone_that_a_writer_with_no_hint_writes_in_the_middle_of_a_reuse_is_completed_by_its_own_close(tmp_path: Path) -> None:
    """The hint is an accelerator: a writer that does not share it is not seen before the next copy, which lands after its tombstone.
    The read that this copy's place calls for finds it: the copy is named on the tombstone before the segment is zeroed, the rest of
    the lot is not copied blind, and the tombstone's own close leaves none of its bytes."""
    hub = F.Hub(tmp_path)
    hub.journal.close()
    hub.journal = J.Journal(tmp_path, segment_size=1 << 20)
    hub.journal.prepare()
    j = hub.journal
    for i in range(6):
        entry(j, f"e{i}", f"text number {i}", f"k{i}")
    j.seal()
    entry(j, "z0", "after", "kz")
    hub.refresh()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    p = subprocess.Popen([sys.executable, "-c", WRITER_OF_TOMBSTONE, str(tmp_path), "k3", "none"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True, env=env)
    assert p.stdout is not None and p.stdout.readline().strip() == "ready"
    real, copies = F.carry, []

    def watched(h: F.Hub, e: F.Entry, **k: Any) -> J.Ref:
        copies.append(e.id)
        ref = real(h, e, **k)
        if e.id == "e2":
            assert p.stdin is not None and p.stdout is not None
            p.stdin.write("go\n")
            p.stdin.flush()
            assert p.stdout.readline().strip() == "written"
        return ref

    F.carry = watched
    try:
        assert F.reuse(hub, 1)
    finally:
        F.carry = real
        p.communicate(timeout=30)
    hub.refresh()
    assert copies == ["e0", "e1", "e2", "e3", "e4", "e5"]  # e3 not seen: copied after the tombstone
    assert hub.entries["e3"].forgotten
    data = b"".join(x.read_bytes() for x in (tmp_path / J.JOURNAL_DIR).glob("*.wal"))
    assert b'"forgot":["e3"]' in data.replace(b" ", b"")  # named on the tombstone before the original was zeroed
    (tomb,) = hub.forgets()
    assert F.close_forget(hub, tomb)
    hub.refresh()
    assert {e.id for e in hub.entries.values() if not e.forgotten and e.forget is None} == {"e0", "e1", "e2", "e4", "e5", "z0"}
    data = b"".join(x.read_bytes() for x in (tmp_path / J.JOURNAL_DIR).glob("*.wal"))
    assert b"text number 3" not in data and b"text number 4" in data


def test_a_hub_that_comes_after_the_reuse_does_not_take_a_copy_let_through_for_an_open_entry(tmp_path: Path) -> None:
    """The copy let through after a tombstone, read by a hub that never saw the original (the segment was zeroed, the process that made
    the copy is gone): it is forgotten all the same, and the close of the tombstone made by that hub leaves none of its bytes."""
    hub = F.Hub(tmp_path)
    hub.journal.close()
    hub.journal = J.Journal(tmp_path, segment_size=1 << 20)
    hub.journal.prepare()
    j = hub.journal
    for i in range(6):
        entry(j, f"e{i}", f"text number {i}", f"k{i}")
    j.seal()
    entry(j, "z0", "after", "kz")
    hub.refresh()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    p = subprocess.Popen([sys.executable, "-c", WRITER_OF_TOMBSTONE, str(tmp_path), "k4", "none"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True, env=env)
    assert p.stdout is not None and p.stdout.readline().strip() == "ready"
    real, copies = F.carry, []

    def watched(h: F.Hub, e: F.Entry, **k: Any) -> J.Ref:
        if e.id == "e4":  # the other process writes its tombstone inside the copy, after the look and before the append
            assert p.stdin is not None and p.stdout is not None
            p.stdin.write("go\n")
            p.stdin.flush()
            assert p.stdout.readline().strip() == "written"
        copies.append(e.id)
        return real(h, e, **k)

    F.carry = watched
    try:
        assert F.reuse(hub, 1)
    finally:
        F.carry = real
        p.communicate(timeout=30)
    assert "e4" in copies
    hub.journal.close()  # the process that made the copy is gone
    fresh = F.Hub(tmp_path)
    fresh.journal.close()
    fresh.journal = J.Journal(tmp_path, segment_size=1 << 20)
    fresh.journal.prepare()
    fresh.refresh()
    assert "e4" not in fresh.entries or fresh.entries["e4"].forgotten
    (tomb,) = fresh.forgets()
    assert F.close_forget(fresh, tomb)
    fresh.refresh()
    assert not [e for e in fresh.entries.values() if e.key == "k4" and not e.forgotten]
    data = b"".join(x.read_bytes() for x in (tmp_path / J.JOURNAL_DIR).glob("*.wal"))
    assert b"text number 4" not in data and b"text number 3" in data
    fresh.journal.close()


def test_a_tombstone_carried_by_an_ordinary_reuse_does_not_forget_a_later_entry_of_its_key_for_a_new_reader(tmp_path: Path) -> None:
    """A tombstone whose purge did not complete is carried forward by an ordinary reuse, to the tail, after an entry of the same key
    remembered after the purge. A process that reads the journal from the start once the original is zeroed must keep that entry, as
    the process that read the original does."""
    hub = F.Hub(tmp_path)
    hub.journal.close()
    hub.journal = J.Journal(tmp_path, segment_size=1 << 20)
    hub.journal.prepare()
    j = hub.journal
    entry(j, "e0", "the old text", "k")
    j.append_entry("", state={"forget": {"kh": F.key_hash("k")}})  # the purge's tombstone, not completed (no `upto`)
    hub.refresh()
    assert hub.entries["e0"].forgotten
    number = hub.entries["e0"].seg
    j.seal()
    entry(j, "m0", "a new memory after the purge", "k")
    hub.refresh()
    assert not hub.entries["m0"].forgotten
    assert F.reuse(hub, number)  # the ordinary compaction: the tombstone is copied after m0, the original zeroed
    hub.refresh()
    assert not hub.entries["m0"].forgotten
    hub.journal.close()
    fresh = F.Hub(tmp_path)
    fresh.journal.close()
    fresh.journal = J.Journal(tmp_path, segment_size=1 << 20)
    fresh.journal.prepare()
    fresh.refresh()
    assert "e0" not in fresh.entries
    assert not fresh.entries["m0"].forgotten  # the same answer as the process that read the original
    (tomb,) = fresh.forgets()
    assert F.close_forget(fresh, tomb)
    fresh.refresh()
    assert not fresh.entries["m0"].forgotten and fresh.entries["m0"].text == "a new memory after the purge"
    data = b"".join(x.read_bytes() for x in (tmp_path / J.JOURNAL_DIR).glob("*.wal"))
    assert b"the old text" not in data and b"a new memory after the purge" in data
    fresh.journal.close()


def test_a_copy_let_through_is_named_by_the_next_process_when_the_one_that_made_it_died_before_naming_it(tmp_path: Path) -> None:
    """The process that let a copy through dies after its copies are durable and before the record that names them: the next one
    reads the journal (the copy of an entry already forgotten says it), names it before it zeroes the original, and a process that
    comes after reads it forgotten. Nothing of its text is left once the tombstone is complete."""

    def new_hub() -> F.Hub:
        h = F.Hub(tmp_path)
        h.journal.close()
        h.journal = J.Journal(tmp_path, segment_size=1 << 20)
        h.journal.prepare()
        return h

    hub = new_hub()
    j = hub.journal
    for i in range(6):
        entry(j, f"e{i}", f"text number {i}", f"k{i}")
    j.seal()
    entry(j, "z0", "after", "kz")
    hub.refresh()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    p = subprocess.Popen([sys.executable, "-c", WRITER_OF_TOMBSTONE, str(tmp_path), "k4", "none"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True, env=env)
    assert p.stdout is not None and p.stdout.readline().strip() == "ready"
    real_carry, real_append = F.carry, J.Journal.append

    def watched(h: F.Hub, e: F.Entry, **k: Any) -> J.Ref:
        if e.id == "e4":  # the tombstone lands inside the copy of e4: e4 is let through
            assert p.stdin is not None and p.stdout is not None
            p.stdin.write("go\n")
            p.stdin.flush()
            assert p.stdout.readline().strip() == "written"
        return real_carry(h, e, **k)

    def died(self: J.Journal, rtype: int, meta: dict[str, Any], *a: Any, **k: Any) -> J.Ref:
        if rtype == J.T_FILED and "forgot" in meta:
            raise Cut("the process died before the record that names the copy")
        return real_append(self, rtype, meta, *a, **k)

    F.carry, J.Journal.append = watched, died  # type: ignore[method-assign]
    try:
        with pytest.raises(Cut):
            F.reuse(hub, 1)
    finally:
        F.carry, J.Journal.append = real_carry, real_append  # type: ignore[method-assign]
        p.communicate(timeout=30)
    hub.journal.close()
    nxt = new_hub()  # the next process: it reads the original, the tombstone and the copy
    nxt.refresh()
    assert nxt.entries["e4"].forgotten and "e4" in nxt.let_through
    assert F.reuse(nxt, 1)  # nothing to copy any more, and the copy is named before the original goes
    assert not nxt.let_through
    nxt.journal.close()
    fresh = new_hub()  # a process that never read the original
    fresh.refresh()
    assert fresh.entries["e4"].forgotten
    (tomb,) = fresh.forgets()
    assert F.close_forget(fresh, tomb)
    fresh.refresh()
    data = b"".join(x.read_bytes() for x in (tmp_path / J.JOURNAL_DIR).glob("*.wal"))
    assert b"text number 4" not in data and b"text number 5" in data
    fresh.journal.close()
