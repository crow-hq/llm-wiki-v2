# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`log.md` is written in the order of arrival: a line is appended at the end, under the real date of the day."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from llmw2.audit import last_split_at
from llmw2.bundle import logfile, txn
from llmw2.bundle.check import check
from llmw2.bundle.store import WikiStore, authored

LEGACY = """# Wiki log

## 2026-03-02

* **Merged**: [B](b.md) - second b
* **Created**: [B](b.md) - first b

## 2026-03-01

* **Created**: [A](a.md) - the a
"""


class Clock:
    """The day the store believes it is (UTC), moved by the test."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, when: datetime) -> None:
        self.when = when
        monkeypatch.setattr("llmw2.bundle.store.utc_now", lambda: self.when)

    def set(self, when: datetime) -> None:
        self.when = when


@pytest.fixture
def store(tmp_path: Path) -> WikiStore:
    s = WikiStore(tmp_path / "w", actor="test")
    s.init()
    return s


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return Clock(monkeypatch, datetime(2026, 11, 14, 10, 0, tzinfo=UTC))


def text(store: WikiStore) -> str:
    return (store.root / "log.md").read_text(encoding="utf-8")


def headings(store: WikiStore) -> list[str]:
    return [line[3:] for line in text(store).splitlines() if line.startswith("## ")]


def entries(store: WikiStore) -> list[str]:
    return [line for line in text(store).splitlines() if line.startswith("* ")]


def problems(store: WikiStore) -> tuple[list[str], list[str]]:
    found = check(store.root)
    return [str(p) for p in found if p.severity == "error"], [str(p) for p in found if p.severity == "warning"]


# -- format -------------------------------------------------------------------------------------------------------


def test_a_new_log_says_its_order_and_gets_lines_at_the_end(store: WikiStore, clock: Clock) -> None:
    store.append_log("Created", "first", "a.md", "A")
    store.append_log("Created", "second", "b.md", "B")
    assert text(store) == (
        "# Wiki log (oldest first)\n\n## 2026-11-14\n\n* **Created**: [A](a.md) - first\n* **Created**: [B](b.md) - second\n"
    )


def test_a_new_day_adds_a_heading_after_a_blank_line(store: WikiStore, clock: Clock) -> None:
    store.append_log("Created", "one")
    clock.set(datetime(2026, 11, 15, 0, 0, 1, tzinfo=UTC))
    store.append_log("Created", "two")
    assert text(store).endswith("* **Created**: one\n\n## 2026-11-15\n\n* **Created**: two\n")


def test_the_author_ends_the_line(store: WikiStore, clock: Clock) -> None:
    with authored("ada"):
        store.append_log("Created", "x", "a.md", "A")
    assert entries(store) == ["* **Created**: [A](a.md) - x · by ada"]


def test_an_append_does_not_rewrite_the_file(store: WikiStore, clock: Clock) -> None:
    for i in range(20):
        store.append_log("Created", f"line {i}")
    path = store.root / "log.md"
    before, ino = path.read_bytes(), os.stat(path).st_ino
    store.append_log("Created", "one more")
    assert path.read_bytes().startswith(before) and os.stat(path).st_ino == ino


# -- the day of the heading is the real day --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ([timedelta(hours=1)], ["2026-11-14"]),  # an hour ahead, same day
        ([timedelta(hours=-1)], ["2026-11-14"]),  # an hour back, same day
        ([timedelta(days=-1)], ["2026-11-14", "2026-11-13"]),  # a day back: the real date, out of order
        ([timedelta(days=365), timedelta(days=-365)], ["2026-11-14", "2027-11-14", "2026-11-14"]),  # a year ahead and back
        ([timedelta(days=1), timedelta(days=1)], ["2026-11-14", "2026-11-15", "2026-11-16"]),
    ],
)
def test_the_heading_is_the_date_of_the_day_whatever_the_clock_did(
    store: WikiStore, clock: Clock, steps: list[timedelta], expected: list[str]
) -> None:
    store.append_log("Created", "start")
    when = clock.when
    for step in steps:
        when += step
        clock.set(when)
        store.append_log("Created", f"at {when.isoformat()}")
    assert headings(store) == expected
    assert len(entries(store)) == len(steps) + 1  # no line lost, in the order of arrival
    errors, _ = problems(store)
    assert errors == []


def test_a_clock_at_1970_writes_1970(store: WikiStore, clock: Clock) -> None:
    store.append_log("Created", "now")
    clock.set(datetime(1970, 1, 1, tzinfo=UTC))
    store.append_log("Created", "epoch")
    assert headings(store) == ["2026-11-14", "1970-01-01"]
    errors, warnings = problems(store)
    assert errors == [] and any("out of order" in w for w in warnings)


def test_two_headings_of_the_same_day_are_allowed(store: WikiStore, clock: Clock) -> None:
    store.append_log("Created", "a")
    clock.set(datetime(2026, 11, 13, tzinfo=UTC))
    store.append_log("Created", "b")
    clock.set(datetime(2026, 11, 14, tzinfo=UTC))
    store.append_log("Created", "c")
    assert headings(store) == ["2026-11-14", "2026-11-13", "2026-11-14"]
    errors, warnings = problems(store)
    assert errors == [] and warnings


def test_two_clones_joined_by_git_give_warnings_and_no_errors(store: WikiStore, clock: Clock) -> None:
    """Two copies of one wiki wrote on different days and a merge kept both runs of lines: the days repeat and cross."""
    store.append_log("Created", "shared")
    (store.root / "log.md").write_text(
        text(store)
        + "\n## 2026-11-16\n\n* **Created**: clone one\n"
        + "\n## 2026-11-15\n\n* **Created**: clone two\n"
        + "\n## 2026-11-16\n\n* **Created**: clone two, later\n",
        encoding="utf-8",
    )
    errors, warnings = problems(store)
    assert errors == [] and warnings


def test_a_heading_that_is_no_date_is_an_error(store: WikiStore, clock: Clock) -> None:
    store.append_log("Created", "a")
    (store.root / "log.md").write_text(text(store) + "\n## Yesterday\n\n* **Created**: b\n", encoding="utf-8")
    errors, _ = problems(store)
    assert errors == ["log.md: log heading is not ## YYYY-MM-DD: '## Yesterday'"]


def test_days_in_order_are_clean(store: WikiStore, clock: Clock) -> None:
    for day in (14, 15, 15, 17):
        clock.set(datetime(2026, 11, day, 9, tzinfo=UTC))
        store.append_log("Created", f"d{day}")
    assert problems(store) == ([], [])


# -- an old log is turned around once, exactly ------------------------------------------------------------------------


def test_a_newest_first_log_is_turned_around_exactly() -> None:
    assert logfile.newest_first_to_oldest_first(LEGACY) == (
        "# Wiki log (oldest first)\n\n## 2026-03-01\n\n* **Created**: [A](a.md) - the a\n\n"
        "## 2026-03-02\n\n* **Created**: [B](b.md) - first b\n* **Merged**: [B](b.md) - second b\n"
    )


def test_turning_a_log_around_twice_in_the_other_direction_gives_it_back() -> None:
    once = logfile.newest_first_to_oldest_first(LEGACY)
    legacy_again = once.replace("# Wiki log (oldest first)", "# Wiki log")
    assert logfile.newest_first_to_oldest_first(legacy_again).replace("# Wiki log (oldest first)", "# Wiki log") == LEGACY


def test_a_history_with_jumps_keeps_its_sequence_and_is_never_sorted() -> None:
    legacy = "# Wiki log\n\n## 2026-01-05\n\n* **Created**: c\n\n## 2027-02-01\n\n* **Created**: b\n\n## 2026-01-04\n\n* **Created**: a\n"
    moved = logfile.newest_first_to_oldest_first(legacy)
    assert [line for line in moved.splitlines() if line.startswith("## ")] == ["## 2026-01-04", "## 2027-02-01", "## 2026-01-05"]


def test_init_turns_a_log_of_1_0_2_around_once(tmp_path: Path, clock: Clock) -> None:
    root = tmp_path / "w"
    root.mkdir()
    (root / "log.md").write_text(LEGACY, encoding="utf-8")
    store = WikiStore(root, actor="test")
    store.init()
    assert text(store).startswith("# Wiki log (oldest first)\n")
    assert sorted(entries(store)) == sorted(line for line in LEGACY.splitlines() if line.startswith("* "))
    again = text(store)
    store.init()
    assert text(store) == again  # not turned back
    assert not txn.pending(root)


def test_append_turns_an_old_log_around_before_it_adds(tmp_path: Path, clock: Clock) -> None:
    root = tmp_path / "w"
    root.mkdir()
    (root / "log.md").write_text(LEGACY, encoding="utf-8")
    store = WikiStore(root, actor="test")  # init never called
    store.append_log("Created", "new")
    assert headings(store) == ["2026-03-01", "2026-03-02", "2026-11-14"]
    assert entries(store)[-1] == "* **Created**: new"


def test_an_empty_or_title_only_log_is_still_good(tmp_path: Path, clock: Clock) -> None:
    root = tmp_path / "w"
    root.mkdir()
    (root / "log.md").write_text("# Wiki log\n", encoding="utf-8")
    store = WikiStore(root, actor="test")
    store.init()
    store.append_log("Created", "x")
    assert text(store) == "# Wiki log (oldest first)\n\n## 2026-11-14\n\n* **Created**: x\n"


def test_a_log_saved_as_latin_1_is_not_lost_when_it_is_turned_around(tmp_path: Path, clock: Clock) -> None:
    root = tmp_path / "w"
    root.mkdir()
    (root / "log.md").write_bytes(LEGACY.replace("the a", "Café").encode("latin-1"))
    store = WikiStore(root, actor="test")
    store.init()
    assert "Café" in text(store)


# -- the tail ---------------------------------------------------------------------------------------------------------


def test_a_line_another_process_appended_is_seen_by_the_next_append(store: WikiStore, clock: Clock) -> None:
    store.append_log("Created", "mine")
    other = WikiStore(store.root, actor="other")
    clock.set(datetime(2026, 11, 15, 1, tzinfo=UTC))
    other.append_log("Created", "theirs")  # a new day heading, written by the other store
    store.append_log("Created", "mine again")  # the same day: no second heading
    assert headings(store) == ["2026-11-14", "2026-11-15"]
    assert entries(store)[-2:] == ["* **Created**: theirs", "* **Created**: mine again"]


def test_a_log_rewritten_in_place_is_read_again(store: WikiStore, clock: Clock) -> None:
    """`scrub_log` (or an editor) replaces the file: a store that remembered the old one reads the new one."""
    store.append_log("Created", "[x](x.md) - secret")
    assert store.scrub_log(["x.md"]) == 1
    clock.set(datetime(2026, 11, 15, 1, tzinfo=UTC))
    store.append_log("Created", "after")
    assert "secret" not in text(store) and headings(store) == ["2026-11-14", "2026-11-15"]


def test_a_rewrite_of_the_same_size_by_another_program_is_noticed(store: WikiStore, clock: Clock) -> None:
    store.append_log("Split", "at 12 notes, 0 opened", "a/index.md", "/a")
    assert store.last_split_at("a") == 12
    path = store.root / "log.md"
    changed = text(store).replace("at 12 notes", "at 24 notes")
    assert len(changed.encode()) == len(text(store).encode())
    tmp = path.with_name("log.tmp")
    tmp.write_text(changed, encoding="utf-8")
    os.replace(tmp, path)  # another inode, same size, same last bytes
    assert store.last_split_at("a") == 24


def test_the_last_split_of_a_folder_comes_from_the_tail_as_the_text_gives_it(store: WikiStore, clock: Clock) -> None:
    assert store.last_split_at("sales") == 0
    for at, folder in ((12, "sales"), (24, "sales"), (12, "ops/deep")):
        store.append_log("Split", f"at {at} notes, 1 opened: x", f"{folder}/index.md", f"/{folder}")
    for folder in ("sales", "ops/deep", "none"):
        assert store.last_split_at(folder) == last_split_at(text(store), folder)
    fresh = WikiStore(store.root, actor="other")  # a store that never saw the appends reads the file once
    assert fresh.last_split_at("sales") == 24


def test_the_heading_of_a_long_day_is_found_from_the_end(store: WikiStore, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logfile, "BLOCK", 256)
    for i in range(40):  # far more than one block of the day's lines
        store.append_log("Created", f"a line that is long enough to fill the block of bytes read from the end {i:05d}")
    fresh = WikiStore(store.root, actor="other")
    fresh.append_log("Created", "same day")
    assert headings(fresh) == ["2026-11-14"]


# -- reading ---------------------------------------------------------------------------------------------------------


def test_the_newest_lines_come_first(store: WikiStore, clock: Clock) -> None:
    for i in range(30):
        if i == 10:
            clock.set(datetime(2026, 11, 15, tzinfo=UTC))
        store.append_log("Created", f"n{i:02d}")
    assert [line.split("**: ")[-1] for line in logfile.newest_lines(store.root / "log.md", 5)] == ["n29", "n28", "n27", "n26", "n25"]
    assert len(logfile.newest_lines(store.root / "log.md", 100)) == 30


def test_newest_lines_reads_blocks_from_the_end(store: WikiStore, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logfile, "BLOCK", 200)
    for i in range(120):
        store.append_log("Created", f"entry {i:05d} " + "x" * 30)
    got = logfile.newest_lines(store.root / "log.md", 100)  # many blocks
    assert [line.split("**: ")[-1][:11] for line in got[:3]] == ["entry 00119", "entry 00118", "entry 00117"]
    assert len(got) == 100 and got[-1].split("**: ")[-1].startswith("entry 00020")
    assert len(logfile.newest_lines(store.root / "log.md", 1000)) == 120


def test_a_crash_inside_a_section_takes_back_the_lines_it_appended(tmp_path: Path, clock: Clock) -> None:
    store = WikiStore(tmp_path / "w", actor="test")
    store.init()
    store.append_log("Created", "kept")
    before = text(store)
    with pytest.raises(RuntimeError), store.lock():
        clock.set(datetime(2026, 11, 20, tzinfo=UTC))
        store.append_log("Created", "lost")
        raise RuntimeError("boom")
    assert text(store) == before
    clock.set(datetime(2026, 11, 14, 12, tzinfo=UTC))
    store.append_log("Created", "next")  # the tail forgot the heading of the lost line
    assert headings(store) == ["2026-11-14"] and entries(store)[-1] == "* **Created**: next"


def test_wiki_log_gives_the_newest_lines_first(tmp_path: Path, clock: Clock) -> None:
    from llmw2 import Wiki, WikiConfig

    wiki = Wiki(WikiConfig(bundle=tmp_path / "w", mode="classic"))
    wiki.init()
    for i in range(7):
        wiki.store.append_log("Created", f"n{i}")
    assert [line.split("**: ")[-1] for line in wiki.log(3)] == ["n6", "n5", "n4"]
    assert len(wiki.log()) == 7 and wiki.log(0) == []


def test_wiki_log_reads_a_log_not_yet_turned_around_as_it_is(tmp_path: Path, clock: Clock) -> None:
    from llmw2 import Wiki, WikiConfig

    root = tmp_path / "w"
    root.mkdir()
    (root / "log.md").write_text(LEGACY, encoding="utf-8")
    wiki = Wiki(WikiConfig(bundle=root, mode="classic"))
    assert [line.split("**: ")[0] for line in wiki.log(2)] == ["* **Merged", "* **Created"]
    assert text(WikiStore(root, actor="x")) == LEGACY  # reading did not change the file
