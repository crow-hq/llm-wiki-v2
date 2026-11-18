# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Places where an acknowledged entry could be lost or hidden while `check()` stayed silent: the look for the tail after a torn record, a
segment that was half reused, a reuse of a segment with a fault, a segment with a bad header that nobody had set aside, and a reader that
stops at a record that is not valid."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

from pathlib import Path

import pytest

from llmw2.bundle import journal as J
from llmw2.bundle.check import check
from llmw2.bundle.journal import Journal
from tests.wiki.test_journal import SMALL, grep, new, seg_path, texts


class Cut(Exception):
    pass


# -- the look for the tail ----------------------------------------------------------------------------------------------------


def test_the_look_for_the_tail_does_not_read_the_segment_from_its_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After a crash left a torn record at the tail every ack stepped over it by reading from byte 0: now from the tail."""
    j = new(tmp_path, shared_hint=False)
    for i in range(30):
        j.append_entry(f"text {i} " + "x" * 1000)
    other = new(tmp_path, shared_hint=False)
    refs = [other.append_entry(f"later {i}") for i in range(3)]
    j.close()
    path = seg_path(tmp_path, 1)
    data = bytearray(path.read_bytes())
    data[refs[1].off + J.HEAD_SIZE + 6] ^= 0xFF  # torn
    path.write_bytes(bytes(data))
    w = new(tmp_path, shared_hint=False)
    w.append_entry("attach")
    reads: list[tuple[int, int]] = []
    real = J._pread

    def spy(fd: int, n: int, off: int) -> bytes:
        reads.append((off, n))
        return real(fd, n, off)

    monkeypatch.setattr(J, "_pread", spy)
    w.append_entry("another")
    assert all(n <= J.HEADER_SIZE for off, n in reads if off == 0)


# -- a segment that was half reused ---------------------------------------------------------------------------------------


def half_recycled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Journal:
    j = new(tmp_path)
    j.append_entry("FORGET-ME-TEXT " + "x" * 2000, key="the-key")
    for i in range(40):
        j.append_entry(f"filler {i} " + "f" * 3000)
    assert j.segments()[-1][0] >= 2

    def crash(path: Path) -> None:
        raise Cut("crash before the zeroing")

    monkeypatch.setattr(J, "_zero_used", crash)
    with pytest.raises(Cut):
        j.recycle(1)
    monkeypatch.undo()
    assert grep(tmp_path, b"FORGET-ME-TEXT") == 1  # the old text is on the disk
    return j


def test_check_reports_a_segment_that_was_half_reused_and_prepare_finishes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    half_recycled(tmp_path, monkeypatch)
    reader = Journal(tmp_path, writable=False, shared_hint=False)
    assert any("reuse" in p.text and p.severity == "error" for p in reader.check())
    assert any("reuse" in p.message for p in check(tmp_path))
    j = Journal(tmp_path, segment_size=SMALL)
    j.prepare()
    assert grep(tmp_path, b"FORGET-ME-TEXT") == 0 and j.check() == []
    assert 1 in j.free_segments() or any(n == 1 for n, _p, _h in j.segments())




def test_nothing_is_written_into_a_segment_that_is_being_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    half_recycled(tmp_path, monkeypatch)
    w = Journal(tmp_path, segment_size=SMALL)
    ref = w.append_entry("new entry")
    assert ref.seg != 1  # the segment whose old bytes wait to be zeroed is not a place to write
    assert "new entry" in texts(w)


# -- no reuse of a segment with a fault -----------------------------------------------------------------------------------


def test_a_segment_with_a_fault_is_not_reused(tmp_path: Path) -> None:
    j = new(tmp_path)
    refs = [j.append_entry(f"acked {i} " + "z" * 3000) for i in range(30)]  # rotates: segment 1 is full
    j.close()
    path = seg_path(tmp_path, 1)
    data = bytearray(path.read_bytes())
    data[refs[2].off + J.HEAD_SIZE + 6] ^= 0xFF  # a record flushed and not valid: the acknowledged ones after it are not read
    path.write_bytes(bytes(data))
    j = new(tmp_path)
    assert any("not valid but was flushed" in p.text for p in j.check())
    assert j.recycle(1) is False
    assert grep(tmp_path, b"acked 29") == 1  # the records after the fault are still there to be looked at
    assert any("not valid but was flushed" in p.text for p in j.check())


def test_a_segment_whose_torn_tail_is_followed_by_nothing_is_still_reused(tmp_path: Path) -> None:
    j = new(tmp_path)
    for i in range(30):
        j.append_entry(f"text {i} " + "z" * 3000)
    assert j.recycle(1) is True


# -- a segment with a bad header that nobody set aside --------------------------------------------------------------------


def test_check_reports_a_segment_with_a_bad_header_before_any_prepare_set_it_aside(tmp_path: Path) -> None:
    j = new(tmp_path)
    for i in range(3):
        j.append_entry(f"ACKED-{i}")
    j.close()
    p = seg_path(tmp_path, 1)
    data = bytearray(p.read_bytes())
    data[20] ^= 0xFF
    p.write_bytes(bytes(data))
    reader = Journal(tmp_path, writable=False, shared_hint=False)
    assert any("header" in x.text and x.severity == "error" for x in reader.check())
    assert any("header" in x.message for x in check(tmp_path))
    assert p.exists()  # reported, not touched by a reader


# -- the reading of the filer ------------------------------------------------------------------------------------------------


def test_read_from_says_so_when_it_stops_at_a_record_that_is_not_valid(tmp_path: Path) -> None:
    j = new(tmp_path)
    refs = [j.append_entry(f"text {i}") for i in range(5)]
    j.close()
    path = seg_path(tmp_path, 1)
    data = bytearray(path.read_bytes())
    data[refs[1].off + J.HEAD_SIZE + 6] ^= 0xFF
    path.write_bytes(bytes(data))
    r = new(tmp_path, writable=False, shared_hint=False)
    records, _pos = r.read_from(None)
    assert [x.text.decode() for x in records] == ["text 0"]
    assert r.stuck == (1, refs[1].off)  # not a silent stop: the filer and `status` can say it


def test_read_from_reads_another_journal_from_its_start_when_the_position_is_not_in_it(tmp_path: Path) -> None:
    """A bundle deleted and made again at the same path: a position (segment, offset) of the old one is not read as in the new."""
    old = new(tmp_path / "old")
    for i in range(5):
        old.append_entry(f"old {i}")
    old.close()
    other = new(tmp_path / "other")
    other.append_entry("the new one")
    other.close()
    r = new(tmp_path / "old", writable=False, shared_hint=False)
    records, pos = r.read_from(None)
    assert len(records) == 5 and not r.restarted
    seg_path(tmp_path / "old", 1).write_bytes(seg_path(tmp_path / "other", 1).read_bytes())  # the same name, another journal
    records, pos2 = r.read_from(pos)
    assert [x.text for x in records] == [b"the new one"] and r.restarted and pos2[0] == 1


def test_read_from_does_not_go_on_from_the_middle_of_a_journal_that_was_rolled_back_and_written_again(tmp_path: Path) -> None:
    """A power cut takes what was only in the page cache; a reader that survives it (a test, a supervisor) has read records that are gone,
    and new ones are written where they were: its position is then inside one of them."""
    j = new(tmp_path, shared_hint=False)
    refs = [j.append_entry(f"first {i} " + "a" * 200) for i in range(3)]
    r = new(tmp_path, writable=False, shared_hint=False)
    records, pos = r.read_from(None)
    assert len(records) == 3
    path = seg_path(tmp_path, 1)
    data = bytearray(path.read_bytes())
    data[refs[2].off : refs[2].end + 64] = bytes(refs[2].end + 64 - refs[2].off)  # the third record is gone from the disk
    path.write_bytes(bytes(data))
    w = new(tmp_path, shared_hint=False)
    w.append_entry("written where the third was, and longer " + "b" * 400)
    records, pos2 = r.read_from(pos)
    assert r.restarted and [x.text.decode()[:6] for x in records] == ["first ", "first ", "writte"] and r.stuck is None
    assert r.read_from(pos2)[0] == []


AT = "2026-01-01T00:00:00.000000+00:00"


def test_read_from_notices_a_rollback_that_was_written_again_with_records_of_the_same_size(tmp_path: Path) -> None:
    """The seq and the length of the last record read are the same as before; its CRC is not."""
    w = new(tmp_path, shared_hint=False)
    a = w.append_entry("aaaa", key="ka", hash="h", at=AT, id="a" * 16)
    b = w.append_entry("bbbb", key="kb", hash="h", at=AT, id="b" * 16)
    reader = new(tmp_path, shared_hint=False, writable=False)
    recs, pos = reader.read_from(None)
    assert [r.meta["id"][0] for r in recs] == ["a", "b"]
    path = seg_path(tmp_path, 1)
    data = bytearray(path.read_bytes())
    data[a.off : b.end] = bytes(b.end - a.off)  # a and b were only in the page cache
    path.write_bytes(bytes(data))
    w2 = new(tmp_path, shared_hint=False)
    w2.append_entry("cccc", key="kc", hash="h", at=AT, id="c" * 16)
    w2.append_entry("dddd", key="kd", hash="h", at=AT, id="d" * 16)
    recs, _ = reader.read_from(pos)
    assert reader.restarted and [r.meta["id"][0] for r in recs] == ["c", "d"]
