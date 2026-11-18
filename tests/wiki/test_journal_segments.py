# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Journal corrections before `remember` writes into it: a segment with a bad header is kept aside, never deleted; `recycle` zeroes
outside the append lock; the incremental read."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import threading
from pathlib import Path

import pytest

from llmw2.bundle import journal as J
from llmw2.bundle.check import check
from llmw2.bundle.journal import Journal
from tests.wiki.test_journal import SMALL, grep, new, seg_path

# -- a segment with no valid header --------------------------------------------------------------------------------------------


def _spare_with_header(tmp_path: Path, payload: bytes | None) -> tuple[Journal, Path]:
    """A journal whose spare segment (number 2) has a header that is not valid: the next `prepare` has to make another one."""
    j = new(tmp_path)
    j.append_entry("the first entry")
    j.prepare()
    spare = seg_path(tmp_path, 2)
    data = bytearray(spare.read_bytes())
    data[20] ^= 0xFF  # the header CRC no longer holds
    if payload is not None:
        data[4096 : 4096 + len(payload)] = payload
    spare.write_bytes(bytes(data))
    assert J.parse_header(bytes(data[:64])) is None
    return j, spare


def test_a_segment_with_a_bad_header_and_bytes_in_it_is_kept_aside_and_check_says_so(tmp_path: Path) -> None:
    payload = b"AN-ENTRY-THAT-WAS-ACKED-9917"
    j, spare = _spare_with_header(tmp_path, payload)
    j.prepare()
    kept = [p for p in (tmp_path / ".llmw2" / "journal").iterdir() if p.suffix not in (".wal", ".lock") and p.name.startswith(spare.stem)]
    assert len(kept) == 1 and payload in kept[0].read_bytes()  # not deleted: the name is no longer a segment's
    assert not spare.exists() or J.parse_header(spare.read_bytes()[:64]) is not None  # the name is free for a good segment
    assert any("quarantine" in p.text for p in new(tmp_path, writable=False).check())
    assert any("quarantine" in p.message for p in check(tmp_path))
    j.append_entry("and it goes on")
    assert [e.text for e in j.read().entries()] == [b"the first entry", b"and it goes on"]


def test_a_segment_of_zeros_that_never_got_its_header_is_not_worth_keeping(tmp_path: Path) -> None:
    j, spare = _spare_with_header(tmp_path, None)
    spare.write_bytes(bytes(SMALL))  # a creation that was cut before the header: nothing in it
    j.prepare()
    leftovers = [p for p in (tmp_path / ".llmw2" / "journal").iterdir() if p.suffix not in (".wal", ".lock") and p.stem.isdigit()]
    assert leftovers == [] and j.check() == []


# -- recycle does not stop the acks ----------------------------------------------------------------------------------------------


def _filled(tmp_path: Path) -> tuple[Journal, J.Ref]:
    j = new(tmp_path)
    secret = j.append_entry("DEAD-COPY-TEXT " + "x" * 2000)
    for i in range(40):
        j.append_entry(f"filler {i} " + "f" * 3000)
    assert secret.seg == 1
    return j, secret


def test_recycle_zeroes_while_other_writers_can_ack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    j, _ = _filled(tmp_path)
    other = new(tmp_path)
    acked: list[bool] = []
    real = J._pwrite

    def pwrite(fd: int, data: bytes, off: int) -> None:
        if off >= J.HEADER_SIZE and not any(data) and not acked:  # the first write of zeros over the old bytes
            t = threading.Thread(target=lambda: (other.append_entry("acked meanwhile"), acked.append(True)), daemon=True)
            t.start()
            t.join(10)
        real(fd, data, off)

    monkeypatch.setattr(J, "_pwrite", pwrite)
    assert j.recycle(1)
    assert acked == [True]  # the append lock was free while the bytes were zeroed
    monkeypatch.undo()
    assert grep(tmp_path, b"DEAD-COPY-TEXT") == 0 and j.check() == []


def test_a_writer_still_attached_to_the_segment_does_not_write_into_it_while_it_is_recycled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer that has not seen the seal of the old segment is attached to it. Once the zeros have taken the seal away its tail
    looks free: its ack must go to the segment in use, not into bytes that are about to get a new header."""
    w = new(tmp_path)
    w.append_entry("w0")  # attached to segment 1 and behind what comes next
    j = new(tmp_path)
    for i in range(40):
        j.append_entry(f"filler {i} " + "f" * 3000)  # fills segment 1, seals it, goes on in the next
    assert j.rotations >= 1
    real, acked = J._zero_used, []

    def zero(path: Path) -> None:
        real(path)
        acked.append(w.append_entry("ACKED-DURING-RECYCLE"))  # the writer acks after the zeros and before the header is final

    monkeypatch.setattr(J, "_zero_used", zero)
    assert j.recycle(1)
    monkeypatch.undo()
    assert acked and acked[0].seg != 1
    got = [e.text.decode() for e in new(tmp_path).read().entries()]
    assert "ACKED-DURING-RECYCLE" in got and got[0] == got[0] and len(got) == len(set(got))
    assert j.check() == []


# -- the incremental read ----------------------------------------------------------------------------------------------------


def test_read_from_gives_what_came_after_a_position(tmp_path: Path) -> None:
    j = new(tmp_path)
    for i in range(3):
        j.append_entry(f"e{i}")
    first, pos = j.read_from()
    assert [r.text for r in first] == [b"e0", b"e1", b"e2"] and pos[0] == 1
    assert j.read_from(pos) == ([], pos)
    j.append_entry("e3")
    other = new(tmp_path)  # another process writes too
    other.append_entry("e4")
    more, pos2 = j.read_from(pos)
    assert [r.text for r in more] == [b"e3", b"e4"] and pos2[1] > pos[1]
    assert j.read_from(pos2) == ([], pos2)


def test_read_from_follows_the_journal_across_segments_and_a_segment_used_again(tmp_path: Path) -> None:
    j = new(tmp_path)
    j.append_entry("a")
    _, pos = j.read_from()
    for i in range(60):
        j.append_entry(f"e{i} " + "x" * 3000)
    assert j.rotations >= 1
    got, pos = j.read_from(pos)
    assert [r.text.decode()[:3] for r in got if r.type == J.T_ENTRY][:2] == ["e0 ", "e1 "] and len(got) >= 60
    whole = [(r.seg, r.off) for r in j.read().records]
    assert [(r.seg, r.off) for r in j.read_from()[0]] == whole
    assert j.recycle(1)  # the segment the first position was in is gone
    j.append_entry("after")
    tail, _ = j.read_from(pos)
    assert [r.text for r in tail] == [b"after"]
    assert [r.text for r in j.read_from((1, J.HEADER_SIZE))[0] if r.type == J.T_ENTRY][-1] == b"after"


def test_read_from_reads_only_what_is_new(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    j = new(tmp_path)
    for i in range(200):
        j.append_entry(f"e{i} " + "x" * 200)
    _, pos = j.read_from()
    j.append_entry("one more")
    read = []
    real = J._pread
    monkeypatch.setattr(J, "_pread", lambda fd, n, off: read.append(n) or real(fd, n, off))
    got, pos = j.read_from(pos)
    assert [r.text for r in got] == [b"one more"] and sum(read) < 16 << 10  # a few bytes, not the 40 KB behind
    monkeypatch.setattr(Journal, "read", lambda self: pytest.fail("a full read"))
    assert j.read_from(pos)[0] == []
