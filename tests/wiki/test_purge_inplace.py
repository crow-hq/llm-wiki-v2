# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The purge of the lines of `log.md` writes them where they stand: same length, no rewrite of the file, undo of the lines only."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from llmw2.bundle import txn
from llmw2.bundle.check import check
from llmw2.bundle.store import WikiStore, authored
from tests.wiki import purge_child, txn_child
from tests.wiki.problems import errors
from tests.wiki.test_txn import ROOT, STRIDE, child, digest

SECRET = "a secret in it"
STAYS = {"scrub": 4, "scrubdel": 5, "scrubappend": 4, "scrubrewrite": 4, "scrubshort": 5}  # lines that keep the text, once committed


@pytest.fixture
def linked(tmp_path: Path) -> Path:
    root = tmp_path / "linked"
    txn_child.setup_linked(root)
    return root


def run_here(root: Path, scenario: str) -> None:
    store = WikiStore(root, actor="test")
    with store.lock():
        {**txn_child.SCENARIOS, **txn_child.SCRUBS}[scenario](store)


def every_byte(root: Path) -> bytes:
    """All the bytes of the log and of every file of `.llmw2` (the journal, its trash, the caches): where a purge must leave no name."""
    files = [root / "log.md", *(p for p in sorted((root / ".llmw2").rglob("*")) if p.is_file())]
    return b"\n".join(p.read_bytes() for p in files)


def test_the_lines_are_overwritten_in_place_by_lines_of_the_same_length(linked: Path) -> None:
    before = (linked / "log.md").read_bytes().split(b"\n")

    run_here(linked, "scrub")

    after = (linked / "log.md").read_bytes().split(b"\n")
    assert len(after) == len(before)
    changed = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    assert len(changed) == 2
    for i, (a, b) in enumerate(zip(before, after, strict=True)):
        assert len(a) == len(b), i
        if i in changed:
            assert b.startswith(b"* **Linked**: [purged]") and b"note-" not in b and SECRET.encode() not in b
        else:
            assert a == b


def test_an_author_stays_after_the_marker_and_the_line_still_parses(linked: Path) -> None:
    store = WikiStore(linked, actor="test")
    with authored("ada"), store.lock():
        store.append_log("Authored", "Facts of the note", f"{txn_child.GROUP}/note-1.md", "Note 1")
    size = (linked / "log.md").stat().st_size

    with store.lock():
        store.scrub_log([f"{txn_child.GROUP}/note-1.md"])

    lines = (linked / "log.md").read_text(encoding="utf-8").splitlines()
    mine = next(x for x in lines if x.startswith("* **Authored**"))
    assert mine.startswith("* **Authored**: [purged]") and mine.endswith(" · by ada") and "note-1" not in mine
    assert (linked / "log.md").stat().st_size == size and not errors(check(linked))
    assert [x for x in store.recent_log(50) if "Authored" in x] == [mine]


def test_the_second_purge_of_the_same_lines_writes_nothing(linked: Path) -> None:
    run_here(linked, "scrub")
    once = digest(linked)
    run_here(linked, "scrub")
    assert digest(linked) == once


def test_what_was_purged_is_in_no_byte_of_the_bundle_not_even_in_the_journal(linked: Path) -> None:
    assert SECRET.encode() in every_byte(linked)

    run_here(linked, "scrub")

    data = every_byte(linked)
    assert b"note-3.md" not in data and b"Note 3](" not in data and b"note-5.md" not in data
    assert data.count(SECRET.encode()) == 4  # the four lines of the notes that stay
    assert not list((linked / txn.TRASH_DIR).iterdir())


def test_the_undo_log_holds_the_bytes_of_the_lines_and_not_the_log(linked: Path) -> None:
    store = WikiStore(linked, actor="test")
    size = (linked / "log.md").stat().st_size
    with pytest.raises(RuntimeError), store.lock():
        store.scrub_log([f"{txn_child.GROUP}/note-3.md"])
        kept = [p.stat().st_size for p in (linked / txn.TRASH_DIR).iterdir()]
        assert len(kept) == 1 and kept[0] < 2 * 200  # the line twice (was, is), not 2 KB of log
        raise RuntimeError("the section fails")
    assert b"note-3.md" in (linked / "log.md").read_bytes() and (linked / "log.md").stat().st_size == size
    assert not list((linked / txn.TRASH_DIR).iterdir()) and not errors(check(linked))


@pytest.mark.parametrize("scenario", ["scrubdel", "scrubappend", "scrubrewrite", "scrubshort"])
def test_a_section_that_scrubs_and_then_fails_is_undone_whatever_it_did_after(linked: Path, scenario: str) -> None:
    before = digest(linked)
    store = WikiStore(linked, actor="test")
    with pytest.raises(RuntimeError), store.lock():
        {**txn_child.SCENARIOS, **txn_child.SCRUBS}[scenario](store)
        raise RuntimeError("the section fails")
    assert digest(linked) == before and not errors(check(linked))


def test_a_line_too_short_for_the_marker_makes_the_whole_file_be_written(linked: Path) -> None:
    run_here(linked, "scrubshort")
    text = (linked / "log.md").read_text(encoding="utf-8")
    assert "n.md" not in text and "* **Tiny**: [purged]" in text and "note-1.md" not in text


def test_the_whole_file_written_is_counted_and_said(linked: Path) -> None:
    store = WikiStore(linked, actor="test")
    with store.lock():
        store.append_log("Tiny", "n.md")
        assert store.scrub_log([f"{txn_child.GROUP}/note-1.md"]) == 1 and store.log_rewrites == 0  # in place
        assert store.scrub_log(["n.md"]) == 1 and store.log_rewrites == 1  # a line too short: the file is written whole


def test_a_log_saved_as_latin_1_is_overwritten_in_its_own_bytes(linked: Path) -> None:
    store = WikiStore(linked, actor="test")
    with store.lock():
        store.append_log("Linked", "Café", f"{txn_child.GROUP}/note-3.md", "Note 3")
    path = linked / "log.md"
    path.write_bytes(path.read_bytes().decode("utf-8").encode("latin-1"))
    size = path.stat().st_size

    run_here(linked, "scrub")

    assert path.stat().st_size == size and b"note-3.md" not in path.read_bytes() and "Café".encode("latin-1") not in path.read_bytes()


def test_what_somebody_wrote_over_a_line_after_the_crash_is_not_overwritten(linked: Path) -> None:
    section = txn.Txn(linked, id=None)
    with txn.active(section):
        WikiStore(linked, actor="test").scrub_log([f"{txn_child.GROUP}/note-3.md"])
    path = linked / "log.md"
    data = bytearray(path.read_bytes())
    at = data.index(b"[purged]")
    data[at : at + 8] = b"SOMEBODY"  # an editor wrote the line again
    path.write_bytes(bytes(data))
    done = txn.recover(linked)
    assert done.conflicts and b"SOMEBODY" in path.read_bytes()


@pytest.mark.parametrize("fmt", ["line", "segment"])
@pytest.mark.parametrize("scenario", sorted(txn_child.SCRUBS))
def test_a_crash_before_any_system_call_of_a_purge_of_log_lines_leaves_it_as_before_or_as_after(
    linked: Path, tmp_path: Path, scenario: str, fmt: str
) -> None:
    before = digest(linked)
    after = tmp_path / "after"
    shutil.copytree(linked, after)
    done = child(after, scenario, 0, fmt)
    assert done.returncode == 0, done.stderr
    total = int(done.stdout.strip().splitlines()[-1])
    after_digest = digest(after, normalize=True)
    assert total > 10 and before != digest(after)

    def one(n: int) -> tuple[int, str | None]:
        work = tmp_path / f"crash-{n}"
        shutil.copytree(linked, work)
        assert child(work, scenario, n, fmt).returncode == 3, f"no crash at call {n}"
        WikiStore(work, actor="test").init()  # the next process to open the bundle
        if txn.pending(work):
            return n, "still pending"
        # a purge cut while it reused a segment of the journal leaves it marked: the next one that files or purges finishes it
        problems = [str(p) for p in errors(check(work)) if "was being reused" not in str(p)] + [str(p) for p in work.rglob("*.tmp")]
        if problems:
            return n, f"problems {problems}"
        trash = list((work / txn.TRASH_DIR).iterdir()) if (work / txn.TRASH_DIR).is_dir() else []
        if trash:
            return n, f"trash left {trash}"
        if digest(work) == before:
            if every_byte(work).count(SECRET.encode()) != 6:
                return n, "the text of a note that stays is gone"
            retry = child(work, scenario, 0, fmt)
            if retry.returncode != 0 or digest(work, normalize=True) != after_digest:
                return n, f"retry differs: {retry.stderr[-300:]}"
            return n, None
        if digest(work, normalize=True) != after_digest:
            return n, "neither as before nor as after"
        if every_byte(work).count(SECRET.encode()) != STAYS[scenario]:
            return n, "committed, and the text of a purged line is still in the log or in the journal"
        return n, None

    with ThreadPoolExecutor(6) as pool:
        failures = [(n, why) for n, why in pool.map(one, range(1, total + 1, STRIDE)) if why]
    assert failures == []


def test_the_journal_of_a_section_that_patched_and_committed_is_only_emptied(linked: Path) -> None:
    section = txn.Txn(linked, id=None)
    with txn.active(section):
        WikiStore(linked, actor="test").scrub_log([f"{txn_child.GROUP}/note-3.md"])
    section.commit()  # the process dies here: the log says commit and the trash still holds the lines
    assert any((linked / txn.TRASH_DIR).iterdir())
    after = digest(linked)
    done = txn.recover(linked)
    assert done.committed and digest(linked) == after
    assert not list((linked / txn.TRASH_DIR).iterdir()) and b"note-3.md" not in every_byte(linked)


# -- Wiki.purge as a whole: the B11 criterion (the text is gone; a power cut leaves nothing half done) ----------------------------


def run_purge_child(root: Path, crash_at: int, fmt: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tests.wiki.purge_child", str(root), str(crash_at)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")]),
            "LLMW2_TXN_FORMAT": fmt,
        },
    )


def outside_the_journal(root: Path) -> bytes:
    """Every byte of the bundle but the journal's segments (`.wal`), which hold the text of a memory, and the undo log, which holds the
    paths of the notes a section deleted, until the step that forgets an entry zeroes them: that step is not part of the purge of the
    log's lines."""
    return b"\n".join(p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and p.suffix != ".wal" and p.name != "undo")


def all_bytes(root: Path) -> bytes:
    """Every byte of the bundle, the journal and the undo log included (a lock file Windows holds is empty)."""
    return b"\n".join(p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and p.suffix != ".lock")


@pytest.fixture(scope="module")
def two_memories(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("two") / "wiki"
    purge_child.setup(root)
    return root


@pytest.mark.parametrize("fmt", ["line", "segment"])
def test_a_crash_before_any_system_call_of_wiki_purge_leaves_it_as_before_or_as_after_and_never_the_text(
    two_memories: Path, tmp_path: Path, fmt: str
) -> None:
    before = digest(two_memories)
    after = tmp_path / "after"
    shutil.copytree(two_memories, after)
    done = run_purge_child(after, 0, fmt)
    assert done.returncode == 0, done.stderr
    total = int(done.stdout.strip().splitlines()[-1])
    assert total > 20 and digest(after) != before
    secret = purge_child.SECRET.encode()
    assert secret not in outside_the_journal(after) and purge_child.OTHER.encode() in outside_the_journal(after)
    after_digest = digest(after, normalize=True)

    def one(n: int) -> tuple[int, str | None]:
        work = tmp_path / f"crash-{n}"
        shutil.copytree(two_memories, work)
        rc = run_purge_child(work, n, fmt).returncode
        if rc == 0:  # the count of calls moves a little (the journal makes its spare segment in a thread): it ended before the n-th
            return n, None if secret not in all_bytes(work) else "ended, and the text is still there"
        if rc != 3:
            return n, "no crash"
        wiki = purge_child.make(work)  # the next process to open the bundle
        wiki.close()
        if txn.pending(work):
            return n, "still pending"
        # a purge cut while it reused a segment of the journal leaves it marked: the next one that files or purges finishes it
        problems = [str(p) for p in errors(check(work)) if "was being reused" not in str(p)] + [str(p) for p in work.rglob("*.tmp")]
        if problems:
            return n, f"problems {problems}"
        if txn.trash_left(work):
            return n, "trash left"
        now = outside_the_journal(work)
        if digest(work) == before:
            if secret not in now:
                return n, "before, and the text of the memory is gone"
        elif digest(work, normalize=True) != after_digest:
            # the first section of the purge committed (the memory is gone, whole), the second (the "Purged" line) did not
            if secret in now or purge_child.OTHER.encode() not in now or b"about-red-1" in now:
                return n, "neither as before nor as after, nor the memory gone whole"
        elif secret in now:
            return n, "as after, and the text is still there"
        wiki = purge_child.make(work)
        again = wiki.purge("remember::a")  # a retry finishes the job (or finds nothing to do)
        wiki.close()
        if secret in all_bytes(work) or errors(check(work)) or (again is not None and again.sources and again.notes == []):
            return n, "the retry did not leave it purged"
        return n, None

    with ThreadPoolExecutor(6) as pool:
        failures = [(n, why) for n, why in pool.map(one, range(1, total + 1, STRIDE)) if why]
    assert failures == []
