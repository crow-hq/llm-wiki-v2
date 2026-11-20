# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The journal: the format of its segments, a torn tail against a fault, group commit across processes, rotation and reuse,
the version, and forgetting an entry in place."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import os
import random
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from llmw2.bundle import journal as J
from llmw2.bundle.journal import Journal, JournalError, JournalVersionError

ROOT = Path(__file__).resolve().parents[2]
SMALL = 64 << 10  # a segment of 64 KiB: rotation and reuse in a test


def new(root: Path, **kw: object) -> Journal:
    kw.setdefault("segment_size", SMALL)
    return Journal(root, **kw)  # type: ignore[arg-type]


def texts(journal: Journal) -> list[str]:
    return [e.text.decode("utf-8") for e in journal.read().entries()]


def seg_path(root: Path, number: int) -> Path:
    for n, p, _h in Journal(root).segments():
        if n == number:
            return p
    raise AssertionError(number)


def children(root: Path, workers: int, count: int, tmp: Path, *extra: str, kill_after: float | None = None) -> list[Path]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    acks = [tmp / f"ack-{w}.txt" for w in range(workers)]
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "tests.wiki.journal_child", str(root), str(w), str(count), str(acks[w]), *extra], env=env, cwd=ROOT
        )
        for w in range(workers)
    ]
    if kill_after is not None:
        deadline = time.monotonic() + 180
        while len(acked(acks)) < workers * 6 and time.monotonic() < deadline:
            time.sleep(0.1)  # they are all at work
        time.sleep(kill_after)
        for p in procs:
            p.kill()
    for p in procs:
        p.wait(timeout=300)
        if kill_after is None:
            assert p.returncode == 0
    return acks


def acked(acks: list[Path]) -> list[str]:
    out = []
    for a in acks:
        if a.exists():
            lines = a.read_bytes().split(b"\n")
            out += [x.decode().strip() for x in lines[:-1] if len(x.strip()) == 16]  # the last line may be torn by the kill
    return out


# -- the format -----------------------------------------------------------------------------------------------------------


def test_append_and_read(tmp_path: Path) -> None:
    j = new(tmp_path)
    a = j.append_entry("first text", title="One", key="k1", hash="h1", origin={"key": "o"}, fields={"a": 1}, author="me")
    b = j.append_entry("second é text")
    assert len(a.id) == 16 and a.seq == 1 and b.seq == 2 and b.off == a.end
    entries = j.read().entries()
    assert [e.text.decode() for e in entries] == ["first text", "second é text"]
    first = entries[0].meta
    got = (first["title"], first["key"], first["hash"], first["origin"], first["fields"], first["author"])
    assert got == ("One", "k1", "h1", {"key": "o"}, {"a": 1}, "me")
    assert j.check() == []
    j.close()
    # a new process-like object reads the same
    assert texts(new(tmp_path)) == ["first text", "second é text"]
    assert (tmp_path / ".llmw2" / "journal" / ".gitignore").exists()


def test_segment_file_is_zeros_written_with_header_and_size(tmp_path: Path) -> None:
    j = new(tmp_path)
    j.append_entry("x")
    (number, path, header), *_ = j.segments()
    assert number == 1 and path.stat().st_size == SMALL
    data = path.read_bytes()
    assert data[:8] == J.MAGIC and header.version == J.VERSION and header.salt != 0
    assert len(header.node) == 16
    assert J.parse_header(data[:64]) == header
    # the header has its own CRC: a flipped byte is not a header
    broken = bytearray(data[:64])
    broken[20] ^= 0xFF
    assert J.parse_header(bytes(broken)) is None
    assert path.read_bytes().count(b"\0") > SMALL - 400  # the rest is zeros that exist on disk


def test_crc_covers_salt_seq_and_content(tmp_path: Path) -> None:
    salt = 0x1122334455667788
    body = J.encode_body({"id": "a" * 16}, b"text")
    rec = bytes(J.HEADER_SIZE) + J.pack_record(salt, 1, 0, J.T_ENTRY, 0, body)
    at = J.HEADER_SIZE
    assert J._parse_at(rec, at, salt) is not None
    assert J._parse_at(rec, at, salt + 1) is None  # another generation
    for pos in (0, 5, 9, 17, 25, J.HEAD_SIZE + 4, len(rec) - at - 9):  # length, crc, seq, synced, type, body
        broken = bytearray(rec)
        broken[at + pos] ^= 0x01
        assert J._parse_at(bytes(broken), at, salt) is None, pos


def test_reading_stops_at_the_first_record_that_is_not_valid(tmp_path: Path) -> None:
    j = new(tmp_path)
    refs = [j.append_entry(f"text {i}") for i in range(5)]
    j.close()
    path = seg_path(tmp_path, 1)
    data = bytearray(path.read_bytes())
    data[refs[2].off + J.HEAD_SIZE + 10] ^= 0xFF  # a record in the middle
    path.write_bytes(bytes(data))
    got = new(tmp_path).read()
    assert [e.text.decode() for e in got.entries()] == ["text 0", "text 1"]  # never skipped: not text 3 and 4


def test_a_record_of_an_older_generation_is_never_read_as_new(tmp_path: Path) -> None:
    j = new(tmp_path, segment_size=SMALL)
    old = [j.append_entry(f"old {i} " + "o" * 3000) for i in range(30)]  # fills the first segment and goes on to the second
    assert j.rotations >= 1
    j.close()
    j = new(tmp_path)
    assert j.recycle(1)
    # the third segment is the free one used again; a record or two of the new generation, over the old bytes
    for i in range(30):
        j.append_entry(f"fresh {i} " + "f" * 3000)
    got = texts(j)
    assert not any(t.startswith("old 0 ") for t in got[30:])
    assert [t for t in got if t.startswith("old")] == [f"old {i} " + "o" * 3000 for i in range(30) if f"old {i} " + "o" * 3000 in got]
    assert len(got) == len(set(got))
    assert j.check() == [] and old[0].seg == 1


def test_a_reused_segment_stays_valid_against_old_bytes_after_the_tail(tmp_path: Path) -> None:
    j = new(tmp_path)
    for i in range(10):
        j.append_entry(f"old {i}")
    j.close()
    path = seg_path(tmp_path, 1)
    # make the segment "free" and use it again by hand: the new generation writes less than the old one did
    j = new(tmp_path)
    for _ in range(30):
        j.append_entry("x" * 3000)  # rotate away from segment 1
    assert j.recycle(1)
    for i in range(40):
        j.append_entry(f"new {i} " + "n" * 3000)
    seg1 = next(s for s in j.read().segments if s.header.number > 2 and s.path == path)
    assert all(r.text.startswith(b"new") for r in seg1.records if r.type == J.T_ENTRY)
    assert j.check() == []


def test_the_salt_is_read_again_from_the_header(tmp_path: Path) -> None:
    a = new(tmp_path)
    a.append_entry("one")
    a.prepare()  # the spare is made by a thread after the first append: here it is there before the test goes on
    path = seg_path(tmp_path, 1)
    # somebody else used the segment again: the header has a new salt and another number
    header = J.parse_header(path.read_bytes()[:64])
    assert header is not None
    other = J.Header(header.version, 7, header.salt ^ 0xFFFF, header.node)
    with open(path, "r+b") as f:
        f.write(other.pack() + bytes(J.HEAD_SIZE))
    a.append_entry("two")  # `a` holds the old salt in memory and must not write under it
    assert [s[0] for s in a.segments()] == [2, 7]
    assert [e.text for e in a.read().entries()] == [b"two"]
    assert next(s for s in a.read().segments if s.number == 7).records == []  # nothing was written under the old salt
    a.close()


# -- a torn tail and a fault ------------------------------------------------------------------------------------------------


def test_a_torn_tail_is_written_over_and_is_no_fault(tmp_path: Path) -> None:
    j = new(tmp_path)
    refs = [j.append_entry(f"text {i}") for i in range(3)]
    refs += [j.append_entry(f"unflushed {i}", sync=False) for i in range(3)]
    j.close()
    path = seg_path(tmp_path, 1)
    data = bytearray(path.read_bytes())
    data[refs[4].off + J.HEAD_SIZE + 6] ^= 0xFF  # a crash tore the fifth record; the sixth is whole
    path.write_bytes(bytes(data))
    j = new(tmp_path)
    assert j.check() == []  # the flush limit of the sixth record is before the torn one: the ordinary crash
    assert len(j.read().entries()) == 4
    ref = j.append_entry("after the crash, and a little more text")
    assert ref.off == refs[4].off  # written over the torn record, after the last valid one
    assert texts(j) == ["text 0", "text 1", "text 2", "unflushed 0", "after the crash, and a little more text"]
    assert j.check() == []


def test_a_record_not_valid_before_a_durable_limit_is_a_fault(tmp_path: Path) -> None:
    j = new(tmp_path)
    refs = [j.append_entry(f"text {i}") for i in range(5)]  # each is flushed before the next: the next declares it durable
    j.close()
    path = seg_path(tmp_path, 1)
    data = bytearray(path.read_bytes())
    data[refs[1].off + J.HEAD_SIZE + 6] ^= 0xFF
    path.write_bytes(bytes(data))
    j = new(tmp_path)
    problems = j.check()
    assert len(problems) == 1 and problems[0].severity == "error" and "not valid but was flushed" in problems[0].text
    assert texts(j) == ["text 0"]
    # nothing is appended over the fault: a new segment is opened
    ref = j.append_entry("after the fault")
    assert ref.seg == 2 and j.check() == problems
    assert texts(new(tmp_path)) == ["text 0", "after the fault"]


def test_a_crash_with_group_commit_is_not_a_fault(tmp_path: Path) -> None:
    """Records written and not yet covered by a flush look like a fault to a reader that does not know the flush limits."""
    j = new(tmp_path)
    for i in range(3):
        j.append_entry(f"flushed {i}")
    unflushed = [j.append_entry(f"pending {i}", sync=False) for i in range(6)]
    j.close()
    path = seg_path(tmp_path, 1)
    data = bytearray(path.read_bytes())
    for k in (1, 3):  # the pages of two of them did not reach the disk: zeros, as a crash leaves them
        start = unflushed[k].off
        data[start : start + unflushed[k].end - unflushed[k].off] = bytes(unflushed[k].end - unflushed[k].off)
    path.write_bytes(bytes(data))
    j = new(tmp_path)
    assert j.check() == []
    assert texts(j) == ["flushed 0", "flushed 1", "flushed 2", "pending 0"]


# -- forgetting an entry -------------------------------------------------------------------------------------------------


def grep(root: Path, needle: bytes) -> int:
    return sum(p.read_bytes().count(needle) for p in (root / ".llmw2" / "journal").glob("*.wal"))










def test_a_segment_used_again_holds_nothing_of_what_it_held(tmp_path: Path) -> None:
    j = new(tmp_path)
    secret = j.append_entry("DEAD-COPY-TEXT " + "x" * 2000, title="DEAD-COPY-TITLE")
    for i in range(40):
        j.append_entry(f"filler {i} " + "f" * 3000)
    assert secret.seg == 1 and grep(tmp_path, b"DEAD-COPY-TEXT") == 1
    assert j.recycle(1)  # all filed: the segment is used again, and what it held is zeroed first
    assert grep(tmp_path, b"DEAD-COPY-TEXT") == 0 and grep(tmp_path, b"DEAD-COPY-TITLE") == 0
    for _ in range(40):  # used again: a copy that went into it is forgotten like any other
        j.append_entry("SECOND-LIFE " + "x" * 2000)
    assert grep(tmp_path, b"SECOND-LIFE") > 0
    assert j.check() == []




# -- rotation and reuse --------------------------------------------------------------------------------------------------------


def test_rotation_and_order(tmp_path: Path) -> None:
    j = new(tmp_path)
    want = []
    for i in range(80):
        text = f"entry {i:03d} " + "z" * 2000
        want.append(text)
        j.append_entry(text)
    assert j.rotations >= 2 and len(j.segments()) >= 3
    assert texts(j) == want
    assert [r.seg for r in j.read().records] == sorted(r.seg for r in j.read().records)
    assert j.check() == []


def test_the_next_segment_is_made_ahead_and_a_rotation_makes_no_file(tmp_path: Path) -> None:
    j = new(tmp_path)
    j.append_entry("first")
    j.prepare()  # what the thread does off the ack
    names = sorted(p.name for p in (tmp_path / ".llmw2" / "journal").glob("*.wal"))
    assert len(names) == 2
    while j.rotations == 0:
        j.append_entry("f" * 3000)
        if j.rotations == 0:
            assert sorted(p.name for p in (tmp_path / ".llmw2" / "journal").glob("*.wal")) == names
    # the rotation itself created no file (a thread may be making the one after)
    j._preparer and j._preparer.join()
    assert j.read().segments[1].records  # the spare took the records


def test_the_first_segment_can_be_made_in_a_thread(tmp_path: Path) -> None:
    j = new(tmp_path)
    t = j.prepare_async()
    j.append_entry("waits only if the thread has not finished")
    t.join(30)
    assert len(j.segments()) >= 1 and texts(j) == ["waits only if the thread has not finished"]
    j.close()


def test_recycle_refuses_the_segment_in_use_and_the_spare_and_gives_free_ones_back(tmp_path: Path) -> None:
    j = new(tmp_path)
    for i in range(60):
        j.append_entry(f"e{i} " + "x" * 3000)
    numbers = [n for n, _p, _h in j.segments()]
    used = j.read().segments[-1].number
    assert not j.recycle(used) and not j.recycle(used + 5) and not j.recycle(99)
    assert j.recycle(numbers[0]) and j.free_segments()
    top = 0
    for i in range(300):  # the filer frees what is behind the writers: no new file is made after the first few
        j.append_entry(f"again {i} " + "y" * 3000)
        for n, _p, _h in j.segments():
            if n < j.read().segments[-1].number - 1:
                j.recycle(n)
        top = max(top, len(list((tmp_path / ".llmw2" / "journal").glob("*.wal"))))
    if j._preparer:
        j._preparer.join()
    assert top <= 6 and j.rotations > 10
    assert j.check() == []
    assert [t for t in texts(j) if t.startswith("again")][-1].startswith("again 299")


def test_a_record_larger_than_a_segment(tmp_path: Path) -> None:
    j = new(tmp_path)
    j.append_entry("small")
    big = "B" * (3 * SMALL)
    j.append_entry(big)
    j.append_entry("small again")
    assert texts(j) == ["small", big, "small again"] and j.check() == []


def test_the_ack_during_a_rotation_has_a_bound(tmp_path: Path) -> None:
    j = new(tmp_path)
    j.prepare()
    worst = 0.0
    for i in range(60):
        t = time.perf_counter()
        j.append_entry(f"r{i} " + "x" * 3000)
        worst = max(worst, time.perf_counter() - t)
    assert j.rotations >= 2
    assert worst < 5.0  # a generous bound for a loaded test machine: the measured maximum is in the report of the step


# -- versions ----------------------------------------------------------------------------------------------------------------


def test_a_newer_version_is_not_written(tmp_path: Path) -> None:
    j = new(tmp_path)
    j.append_entry("written by the old code")
    j.close()
    path = seg_path(tmp_path, 1)
    header = J.parse_header(path.read_bytes()[:64])
    assert header is not None
    newer = J.Header(J.VERSION + 1, header.number, header.salt, header.node)
    with open(path, "r+b") as f:
        f.write(newer.pack())
    again = new(tmp_path)
    with pytest.raises(JournalVersionError, match="newer"):
        again.append_entry("no")
    with pytest.raises(JournalVersionError):
        again.read()
    problems = again.check()
    assert len(problems) == 1 and problems[0].severity == "error" and "newer" in problems[0].text


def test_reserved_record_types_are_not_written(tmp_path: Path) -> None:
    j = new(tmp_path)
    for kind in J.T_RESERVED:
        with pytest.raises(JournalError):
            j.append(kind, {"id": "x"})
    j.append(J.T_FILED, {"id": "a" * 16, "note": "n.md"})
    assert [r.kind for r in j.read().records] == ["F"]


def test_the_journal_is_invisible_to_the_bundle(tmp_path: Path) -> None:
    from llmw2.bundle.check import check
    from llmw2.bundle.store import WikiStore

    store = WikiStore(tmp_path, actor="test")
    store.init()
    before = check(tmp_path)
    j = Journal(tmp_path)
    j.append_entry("a text the bundle never sees")
    assert check(tmp_path) == before
    assert not store.load().all_notes()
    j.close()


# -- processes ----------------------------------------------------------------------------------------------------------------


def verify_children(root: Path, acks: list[Path], workers: int, count: int | None) -> None:
    j = new(root)
    view = j.read()
    entries = view.entries()
    ids = [e.meta["id"] for e in entries]
    assert len(ids) == len(set(ids))
    assert view.problems == [] and [p for p in j.check() if p.severity == "error"] == []  # (warnings: segments wait, no filer here)
    last: dict[int, int] = {}
    for e in entries:  # each process's records are in the order it wrote them
        worker, n = int(e.meta["id"][:4], 16), int(e.meta["id"][4:], 16)
        assert n == last.get(worker, -1) + 1, (worker, n, last.get(worker))
        last[worker] = n
    if count is not None:
        assert len(ids) == workers * count
    assert set(acked(acks)) <= set(ids)  # no record that was confirmed is missing


@pytest.mark.parametrize("workers", [8, 30])
@pytest.mark.parametrize("hint", ["1", "0"])
def test_processes_keep_order_and_integrity(tmp_path: Path, workers: int, hint: str) -> None:
    count = 12 if workers == 30 else 25
    acks = children(tmp_path, workers, count, tmp_path, f"hint={hint}", f"segment={SMALL}", "text=900")
    verify_children(tmp_path, acks, workers, count)
    assert len(list((tmp_path / ".llmw2" / "journal").glob("*.wal"))) >= 2  # they rotated, too


@pytest.mark.parametrize("workers", [8, 30])
def test_killing_processes_in_the_middle_of_a_group_loses_nothing_confirmed(tmp_path: Path, workers: int) -> None:
    acks = children(tmp_path, workers, 0, tmp_path, "text=400", kill_after=0.4 if workers == 30 else 0.3)
    assert len(acked(acks)) > workers  # they did work
    verify_children(tmp_path, acks, workers, None)  # and no fault that is not one
    j = new(tmp_path)
    ref = j.append_entry("the journal goes on after the kill")
    assert ref.id and j.check() == []


def test_a_hint_with_another_salt_is_ignored(tmp_path: Path) -> None:
    j = new(tmp_path)
    if j._hint is None or not j._hint.available:
        pytest.skip("no shared memory here")
    j.append_entry("first")
    syncs = j.syncs
    j._hint.put(0xDEADBEEF, 1 << 30)  # a claim about some other segment
    j.append_entry("second")
    assert j.syncs == syncs + 1  # it flushed for itself
    j.close()


def test_without_shared_memory_each_process_flushes_for_itself(tmp_path: Path) -> None:
    j = new(tmp_path, shared_hint=False)
    a = [j.append_entry(f"e{i}") for i in range(5)]
    assert j.syncs == 5 and len(a) == 5 and j.check() == []


def test_random_cut_of_a_tail_never_reads_a_wrong_record(tmp_path: Path) -> None:
    rng = random.Random(3)
    for trial in range(15):
        root = tmp_path / f"t{trial}"
        j = new(root)
        refs = [j.append_entry("e" * rng.randrange(1, 400)) for _ in range(12)]
        j.close()
        path = seg_path(root, 1)
        data = bytearray(path.read_bytes())
        cut = rng.randrange(refs[3].off, refs[-1].end)
        data[cut : refs[-1].end + 40] = bytes(refs[-1].end + 40 - cut)  # a crash: the tail never reached the disk
        path.write_bytes(bytes(data))
        got = new(root)
        ids = [e.meta["id"] for e in got.read().entries()]
        assert ids == [r.id for r in refs][: len(ids)]
        assert got.check() == []
        got.append_entry("after")
        assert new(root).check() == []


def test_struct_sizes() -> None:
    assert J.HEAD_SIZE == 32 and struct.calcsize("<I") == 4 and J.HEADER_SIZE == 64


def test_a_fault_is_reported_by_the_check_of_the_bundle(tmp_path: Path) -> None:
    from llmw2.bundle.check import check
    from llmw2.bundle.store import WikiStore

    WikiStore(tmp_path, actor="test").init()
    j = new(tmp_path)
    refs = [j.append_entry(f"text {i}") for i in range(4)]
    j.close()
    path = seg_path(tmp_path, 1)
    data = bytearray(path.read_bytes())
    data[refs[1].off + J.HEAD_SIZE + 6] ^= 0xFF
    path.write_bytes(bytes(data))
    found = [p for p in check(tmp_path) if p.path == ".llmw2/journal"]
    assert len(found) == 1 and found[0].severity == "error" and "not valid but was flushed" in found[0].message
    path.write_bytes(bytes(data[: refs[1].off]) + bytes(len(data) - refs[1].off))  # nothing after the damage: a torn tail, no fault
    assert [p for p in check(tmp_path) if p.path == ".llmw2/journal"] == []


def test_a_hint_that_cannot_be_made_leaves_a_journal_that_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No /dev/shm (Android), no mapping (another Windows session): each process flushes for itself and nothing else changes."""
    def refuse(*a: object, **k: object) -> object:
        raise OSError("no shared memory here")

    monkeypatch.setattr(J.mmap, "mmap", refuse)
    j = new(tmp_path)
    assert j._hint is not None and not j._hint.available
    for i in range(6):
        j.append_entry(f"e{i}")
    assert j.syncs == 6 and texts(j) == [f"e{i}" for i in range(6)] and j.check() == []
    acks = children(tmp_path / "other", 8, 10, tmp_path, "hint=0")
    verify_children(tmp_path / "other", acks, 8, 10)


# -- the tail hint: where the others left the tail, an accelerator and never the truth --------------------------------------------
#
# A process that shares the hint jumps to the tail the last writer left, so it does not read, with a CRC, the records the others
# wrote since its turn. These tests are about what the jump must never do: write at a place that is not the tail, take a record
# for valid that is not, or lose a record that was confirmed. The reference is the journal of a process with no hint, on a copy.


def hint_journal(root: Path) -> Journal:
    """A journal with the shared hint, kept open: on Windows the mapping lives as long as one process holds it."""
    j = new(root)
    if j._hint is None or not j._hint.available:
        pytest.skip("no shared memory here")
    return j


def set_tail(j: Journal, salt: int, off: int, seq: int, prev: int = 0) -> None:
    """Write the hint by hand, with a valid check word, bypassing `put_tail`: a hint that is old, wrong or from another generation."""
    assert j._hint is not None and j._hint.mm is not None
    fields = (salt, off, seq, prev)[: J._TAIL.size // 8 - 1]
    chk = J._HINT_MASK
    for f in fields:
        chk ^= f
    j._hint.mm[24 : 24 + J._TAIL.size] = struct.pack(f"<{len(fields) + 1}Q", *fields, chk)


def tail_of(j: Journal, salt: int) -> tuple[int, int]:
    assert j._hint is not None
    got = j._hint.get_tail(salt)
    assert got is not None
    return got[0], got[1]


def salts(root: Path) -> set[int]:
    return {h.salt for _n, _p, h in Journal(root, shared_hint=False).segments()}


def state(j: Journal) -> tuple[list[tuple[int, int, str]], list[str]]:
    view = j.read()
    return [(r.seg, r.off, r.meta.get("id", "")) for r in view.records], [p.text for p in j.check()]


def same_as_without_hint(root: Path, tmp: Path, act) -> Journal:  # type: ignore[no-untyped-def]
    """`act(journal)` on the bundle with the hint and on a copy of it without: same offsets, same entries, same problems."""
    import shutil

    other = tmp / "twin"
    shutil.copytree(root, other)
    mine = new(root)
    plain = new(other, shared_hint=False)
    a, b = act(mine), act(plain)
    assert (a.seg, a.off, a.seq) == (b.seg, b.off, b.seq), "the hint put the record where the journal without it did not"
    sa, sb = state(mine), state(plain)
    assert [x[1:] for x in sa[0]] == [x[1:] for x in sb[0]] and sa[1] == sb[1]
    assert texts(mine) == texts(plain)
    return mine


def test_tail_hint_is_kept_by_the_writers_and_moves_forward(tmp_path: Path) -> None:
    j = hint_journal(tmp_path)
    ref = j.append_entry("one")
    assert tail_of(j, ref.salt) == (ref.end, ref.seq + 1)
    new(tmp_path).append_entry("two")
    assert tail_of(j, ref.salt)[1] == 3  # the other writer's record moved it
    j.append_entry("three")
    assert texts(j) == ["one", "two", "three"] and j.check() == []


def test_an_old_tail_hint_is_not_a_tail(tmp_path: Path) -> None:
    """The hint says the tail is further back than it is (a writer that died before it wrote the hint, or a hint that lags)."""
    j = hint_journal(tmp_path)
    refs = [j.append_entry(f"e{i}") for i in range(8)]
    set_tail(j, refs[0].salt, refs[2].end, refs[2].seq + 1, prev=refs[2].off)
    ref = new(tmp_path).append_entry("after the old hint")
    assert (ref.off, ref.seq) == (refs[7].end, 9)
    assert texts(j) == [f"e{i}" for i in range(8)] + ["after the old hint"] and j.check() == []
    assert tail_of(j, ref.salt) == (ref.end, ref.seq + 1)  # and the hint is true again


def test_an_attached_writer_behind_the_hint_jumps_to_the_tail_and_loses_nothing(tmp_path: Path) -> None:
    a = hint_journal(tmp_path)
    a.append_entry("a0")
    b = new(tmp_path)
    for i in range(20):
        b.append_entry(f"b{i}")
    ref = a.append_entry("a1")  # a is 20 records behind: it jumps to the tail the hint gives
    assert ref.seq == 22 and a.check() == []
    assert texts(a) == ["a0"] + [f"b{i}" for i in range(20)] + ["a1"]


def test_a_tail_hint_does_not_hide_a_torn_record_it_jumps_over(tmp_path: Path) -> None:
    """a is attached and behind; a record the others wrote is damaged after that (not a crash: a rewrite from outside). The hint takes a
    to the tail, but the damage is still there for the check to find, and nothing of the records after it is written over."""
    a = hint_journal(tmp_path)
    a.append_entry("a0")
    b = new(tmp_path)
    refs = [b.append_entry(f"b{i}") for i in range(6)]
    path = seg_path(tmp_path, 1)
    data = bytearray(path.read_bytes())
    data[refs[2].off + J.HEAD_SIZE + 6] ^= 0xFF
    path.write_bytes(bytes(data))
    ref = a.append_entry("a1")
    assert ref.off == refs[-1].end  # what follows the damage is not written over
    c = new(tmp_path)
    problems = c.check()
    assert len(problems) == 1 and "not valid but was flushed" in problems[0].text
    assert texts(c) == ["a0", "b0", "b1"]
    assert path.read_bytes()[refs[3].off : refs[-1].end] == bytes(data)[refs[3].off : refs[-1].end]


def test_a_tail_hint_beyond_what_the_file_holds_is_not_followed(tmp_path: Path) -> None:
    """The records the hint counts are gone (the file was put back, zeroed) while a is attached: a record written at the hint would
    leave a hole, and the reader would stop in front of it."""
    a = hint_journal(tmp_path)
    a.append_entry("a0")
    b = new(tmp_path)
    refs = [b.append_entry(f"b{i}") for i in range(5)]
    path = seg_path(tmp_path, 1)
    data = bytearray(path.read_bytes())
    data[refs[1].off : refs[4].end] = bytes(refs[4].end - refs[1].off)
    path.write_bytes(bytes(data))
    ref = a.append_entry("a1")
    assert ref.off == refs[1].off and ref.seq == refs[1].seq  # in front of the zeros, where the journal ends
    assert texts(new(tmp_path)) == ["a0", "b0", "a1"] and a.check() == []


@pytest.mark.parametrize("cut", ["half", "head-only", "hint-not-written"])
def test_a_tail_hint_after_a_crash_in_the_middle_of_an_append(tmp_path: Path, cut: str) -> None:
    """b died inside `pwrite` (the record is partial and the hint was never written) or just after it (whole, hint behind)."""
    a = hint_journal(tmp_path)
    b = new(tmp_path)
    for i in range(4):
        b.append_entry(f"text {i}")
    mid = a.append_entry("a0")  # a is attached at the tail
    last = b.append_entry("the last one")
    if cut != "hint-not-written":
        path = seg_path(tmp_path, 1)
        data = bytearray(path.read_bytes())
        keep = (last.end - last.off) // 2 if cut == "half" else J.HEAD_SIZE
        data[last.off + keep : last.end] = bytes(last.end - last.off - keep)  # a torn record
        path.write_bytes(bytes(data))
    set_tail(a, mid.salt, mid.end, mid.seq + 1, prev=mid.off)  # the hint is where it was before b's record
    ref = a.append_entry("next")
    if cut == "hint-not-written":
        assert ref.off == last.end and texts(a)[-2:] == ["the last one", "next"]  # whole: it is read, not written over
    else:
        assert ref.off == last.off and texts(a)[-2:] == ["a0", "next"]  # torn: written over, as with no hint
    assert texts(new(tmp_path))[:5] == ["text 0", "text 1", "text 2", "text 3", "a0"] and a.check() == []


def test_a_tail_hint_with_a_seq_that_does_not_match_is_not_used(tmp_path: Path) -> None:
    """A hint with a good check word and the right offset, and a number that is not the record's: a record written with it would be
    one the reader stops at, and a confirmed record would be lost."""
    j = hint_journal(tmp_path)
    a = new(tmp_path)
    refs = [j.append_entry(f"e{i}") for i in range(6)]
    a.append_entry("a is attached here")  # a, at record 7
    refs += [j.append_entry(f"later {i}") for i in range(4)]
    set_tail(j, refs[0].salt, refs[-1].end, 99, prev=refs[-1].off)  # the right place, a wrong number
    ref = a.append_entry("a writes")
    assert ref.seq == 12 and ref.off == refs[-1].end
    assert texts(a)[-1] == "a writes" and a.check() == []
    assert tail_of(a, refs[0].salt) == (ref.end, 13)
    for i in range(3):
        j.append_entry(f"more {i}")
    last = j.append_entry("the last one")
    set_tail(j, last.salt, last.end + 4096, last.seq + 1, prev=last.off)  # an offset that is not the end of the last record
    ref = a.append_entry("a writes again")
    assert (ref.off, ref.seq) == (last.end, last.seq + 1)
    assert texts(a)[-2:] == ["the last one", "a writes again"] and a.check() == []


def test_a_tail_hint_of_another_segment_is_ignored(tmp_path: Path) -> None:
    j = hint_journal(tmp_path)
    first = j.append_entry("x" * 3000)
    for i in range(40):
        j.append_entry(f"fill {i} " + "f" * 3000)
    assert j.rotations >= 1
    ref = j.append_entry("in the new segment")
    set_tail(j, first.salt, first.end + 5000, 9, prev=first.off)  # what segment 1 said, long ago
    a = new(tmp_path)
    ref2 = a.append_entry("a writes")
    assert (ref2.seg, ref2.seq) == (ref.seg, ref.seq + 1) and a.check() == []
    assert texts(a)[-2:] == ["in the new segment", "a writes"]


def test_a_recycled_segment_is_not_written_by_the_hint_it_had(tmp_path: Path) -> None:
    """Segment 1 is used again under a new salt; the hint of its old generation, set again and again, never decides where it is written."""
    j = hint_journal(tmp_path)
    old = j.append_entry("OLD-LIFE " + "x" * 3000)
    for i in range(40):
        j.append_entry(f"fill {i} " + "f" * 3000)
    assert j.recycle(1)
    assert old.salt not in salts(tmp_path)
    for i in range(80):  # the journal goes round to segment 1 again, under its new salt
        set_tail(j, old.salt, 5000, 50, prev=4000)  # the old generation's claim
        j.append_entry(f"again {i} " + "y" * 3000)
        for n, _p, _h in j.segments():
            if n < j.read().segments[-1].number - 1:
                j.recycle(n)
    assert j.rotations > 3 and j.check() == []
    ids = [e.meta["id"] for e in j.read().entries()]
    assert len(ids) == len(set(ids))
    again = [t for t in texts(j) if t.startswith("again")]
    assert again[-1].startswith("again 79") and "OLD-LIFE" not in "".join(texts(j))
    assert [int(t.split()[1]) for t in again] == sorted(int(t.split()[1]) for t in again)


def test_shared_bytes_another_user_made_under_the_name_are_not_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIX: the bytes the processes share are a file of `/dev/shm` whose name can be guessed. A file of that name that another user
    made is not mapped (None: no sharing, nothing lost); one of this user is. The POSIX branch runs here on any system, `/dev/shm`
    put in a folder of the test."""
    import types

    real_open = os.open

    def opened(path: str, flags: int, mode: int = 0o777) -> int:
        if path.startswith("/dev/shm/"):
            path = str(tmp_path / path.removeprefix("/dev/shm/"))
        return real_open(path, flags | getattr(os, "O_BINARY", 0), mode)

    owner = [0]

    class PosixOs(types.SimpleNamespace):
        def __getattr__(self, name: str) -> object:
            return getattr(os, name)

    monkeypatch.setattr(J, "sys", types.SimpleNamespace(platform="linux"))
    monkeypatch.setattr(J, "os", PosixOs(open=opened, geteuid=lambda: owner[0]))
    (tmp_path / "llmw2-test").write_bytes(b"")
    owner[0] = os.stat(tmp_path / "llmw2-test").st_uid
    mine = J.shared_bytes("llmw2-test", 64)
    assert mine is not None and len(mine) == 64
    mine.close()
    owner[0] += 1  # the file is another user's
    assert J.shared_bytes("llmw2-test", 64) is None
