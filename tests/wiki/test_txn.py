# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Write sections are transactions: an undo log restores the bundle after a crash, or an error, in any of them."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import hashlib
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from llmw2 import Wiki
from llmw2.bundle import txn
from llmw2.bundle.check import check
from llmw2.bundle.store import WikiStore, write_atomic
from llmw2.errors import InputError
from tests.wiki import txn_child
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.problems import errors

ROOT = Path(__file__).resolve().parents[2]
STRIDE = int(os.environ.get("LLMW2_TXN_STRIDE", "1"))  # 1: a crash before every system call of every scenario


def digest(root: Path, *, normalize: bool = False) -> str:
    """Every file of the bundle (the journal folder aside) by name and content; `normalize`: without times, stamps and ids."""
    h = hashlib.sha256()
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and ".llmw2" not in p.relative_to(root).parts):
        data = path.read_bytes()
        name = path.relative_to(root).as_posix()
        if normalize:
            text = data.decode("utf-8")
            text = re.sub(r"\d{4}-\d\d-\d\d(T[\d:]+(\+00:00)?)?", "T", text)
            text = re.sub(r"(?m)^id: '?[0-9a-f]{12}'?$", "id: X", text)
            text = re.sub(r"\d{8}-\d{6}", "TS", text)
            data = text.encode("utf-8")
            name = re.sub(r"\d{8}-\d{6}", "TS", name)
        files.append((name, data))
    if normalize:  # raw copies are named by the second they were made in: the order of "TS-new" and "TS-note-5" depends on the clock
        files.sort()
    for name, data in files:
        h.update(name.encode() + b"\0" + data + b"\0")
    return h.hexdigest()


def child(root: Path, scenario: str, crash_at: int, fmt: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tests.wiki.txn_child", str(root), scenario, str(crash_at)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")]),
            "LLMW2_TXN_FORMAT": fmt,
        },
    )


@pytest.fixture
def base(tmp_path: Path) -> Path:
    root = tmp_path / "base"
    txn_child.setup(root)
    return root


def clean_run(base: Path, scenario: str, where: Path, fmt: str = "") -> tuple[Path, int]:
    shutil.copytree(base, where)
    done = child(where, scenario, 0, fmt)
    assert done.returncode == 0, done.stderr
    return where, int(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("fmt", ["line", "segment"])
@pytest.mark.parametrize("scenario", sorted(txn_child.SCENARIOS))
def test_a_crash_before_any_system_call_leaves_the_bundle_as_before_or_as_after(
    base: Path, tmp_path: Path, scenario: str, fmt: str
) -> None:
    """The process dies before the N-th durable call of a section, for every N: once the next one opens the bundle it is the
    bundle as it was (and a retry gives the clean run's result), or, after the commit, the section's own result."""
    before = digest(base)
    after, total = clean_run(base, scenario, tmp_path / "after", fmt)
    after_digest = digest(after, normalize=True)
    assert total > 10 and before != digest(after)
    points = list(range(1, total + 1, STRIDE))

    def one(n: int) -> tuple[int, str | None]:
        work = tmp_path / f"crash-{n}"
        shutil.copytree(base, work)
        assert child(work, scenario, n, fmt).returncode == 3, f"no crash at call {n}"
        store = WikiStore(work, actor="test")
        store.init()  # the next process to open the bundle
        assert not txn.pending(work)
        problems = [str(p) for p in errors(check(work))]
        leftovers = [str(p) for p in work.rglob("*.tmp")]
        if problems or leftovers:
            return n, f"problems {problems} {leftovers}"
        if digest(work) == before:
            retry = child(work, scenario, 0, fmt)  # the same section again
            if retry.returncode != 0 or digest(work, normalize=True) != after_digest:
                return n, f"retry differs: {retry.stderr[-300:]}"
            return n, None
        if digest(work, normalize=True) != after_digest:
            return n, "neither as before nor as after"
        return n, None

    with ThreadPoolExecutor(6) as pool:
        failures = [(n, why) for n, why in pool.map(one, points) if why]
    assert failures == []


def test_a_section_that_raises_is_rolled_back(base: Path) -> None:
    before = digest(base)
    store = WikiStore(base, actor="test")
    with pytest.raises(RuntimeError), store.lock():
        txn_child._new(store, 2)
        raise RuntimeError("boom")
    assert digest(base) == before and not txn.pending(base)
    with store.lock():  # and the store is usable
        txn_child._new(store, 2)
    assert digest(base) != before


def test_the_in_memory_tree_is_read_again_after_a_rollback(base: Path) -> None:
    store = WikiStore(base, actor="test")
    with store.session():
        store.load()
        with pytest.raises(RuntimeError), store.lock():
            txn_child._new(store, 0)
            raise RuntimeError("boom")
        folder = store.load().find("a")
        assert folder is not None and len(folder.notes) == 6


def test_commit_comes_before_the_hook_and_the_hook_before_the_log_is_emptied(base: Path) -> None:
    store = WikiStore(base, actor="test")
    seen: list[list[str]] = []

    def hook() -> None:
        records = txn.read_records(base / txn.UNDO_FILE)
        seen.append([r["t"] for r in records[-2:]] + [str(records[-1].get("id"))])

    with store.lock(id="entry-1", after_commit=hook):
        txn_child._new(store, 1)
    assert seen and seen[0][-2:] == ["commit", "entry-1"]  # the entry's id is in the commit record the hook sees
    assert not txn.pending(base)


def test_a_log_with_a_commit_is_only_emptied(base: Path) -> None:
    """A crash after the commit and before the log is emptied: the section stays done."""
    after, _ = clean_run(base, "new2", base.parent / "done")
    kept = base.parent / "kept"
    shutil.copytree(after, kept)
    # put back a log that ends with its commit record, as if the process died before emptying it
    store = WikiStore(kept, actor="test")
    section = txn.Txn(kept, id="x")
    with txn.active(section):
        write_atomic(kept / "extra.md", "x\n")
    section.commit()
    os.close(section._fd)  # type: ignore[arg-type]
    assert txn.pending(kept)
    store.init()
    assert (kept / "extra.md").exists() and not txn.pending(kept)


def test_what_somebody_wrote_after_the_crash_is_not_overwritten(base: Path) -> None:
    store = WikiStore(base, actor="test")
    section = txn.Txn(base, id=None)
    note = base / "a/note-1.md"
    with txn.active(section):
        write_atomic(note, "changed by the interrupted filing\n")
        txn.append(base / "log.md", "* **Created**: lost\n")
    # the process died here: no commit. Then another program (an old version, git, a person) edited the note and the log
    note.write_text("edited by hand\n", encoding="utf-8")
    log_before = (base / "log.md").read_bytes()
    with (base / "log.md").open("ab") as f:
        f.write(b"* **Created**: written by someone else\n")
    done = txn.recover(base)
    assert done.rolled_back and len(done.conflicts) == 2
    assert note.read_text(encoding="utf-8") == "edited by hand\n"
    assert (base / "log.md").read_bytes().startswith(log_before)  # nothing of theirs lost
    assert (base / "log.md").read_bytes().endswith(b"someone else\n")
    reported = [p for p in check(base) if "interrupted filing" in str(p)]
    assert len(reported) == 2 and {"a/note-1.md", "log.md"} == {str(p).split(":")[0] for p in reported}
    note.write_text("fixed\n", encoding="utf-8")  # settled by whoever fixes it
    assert [str(p).split(":")[0] for p in check(base) if "interrupted filing" in str(p)] == ["log.md"]
    store.init()


def test_a_file_a_power_cut_left_empty_is_put_back(base: Path) -> None:
    section = txn.Txn(base, id=None)
    note = base / "a/note-2.md"
    was = note.read_bytes()
    with txn.active(section):
        write_atomic(note, "the new text\n")
        write_atomic(base / "a/brand-new.md", "x\n")
    note.write_bytes(b"")  # the data never reached the disk: the file survived empty
    (base / "a/brand-new.md").write_bytes(b"\0\0\0")
    done = txn.recover(base)
    assert done.conflicts == [] and note.read_bytes() == was and not (base / "a/brand-new.md").exists()


def test_a_torn_last_record_is_ignored(base: Path) -> None:
    section = txn.Txn(base, id=None)
    note = base / "a/note-2.md"
    was = note.read_bytes()
    with txn.active(section):
        write_atomic(note, "the new text\n")
    with (base / txn.UNDO_FILE).open("ab") as f:
        f.write(b'12345678 {"v":1,"t":"copy","p":"a/note-')  # cut in the middle
    assert txn.recover(base).rolled_back and note.read_bytes() == was


def test_recovery_is_repeatable(base: Path) -> None:
    before = digest(base)
    section = txn.Txn(base, id=None)
    with txn.active(section):
        txn_child._new(WikiStore(base, actor="test"), 2)
    saved = (base / txn.UNDO_FILE).read_bytes()
    txn.recover(base)
    assert digest(base) == before
    (base / txn.UNDO_FILE).write_bytes(saved)  # the process died during the recovery: it runs again
    txn.recover(base)
    assert digest(base) == before


def test_a_folder_cannot_be_moved_inside_a_transaction(base: Path) -> None:
    store = WikiStore(base, actor="test")
    root = store.load()
    store.create_folder(root, "b", "Another.")
    with pytest.raises(txn.TxnUnsupported), store.lock():
        store.move_folder("b", "c")
    with store.lock(undo=False):
        store.move_folder("b", "c")
    assert (base / "c").is_dir()


def test_writes_outside_the_bundle_and_outside_a_section_are_plain(base: Path, tmp_path: Path) -> None:
    store = WikiStore(base, actor="test")
    other = tmp_path / "elsewhere.txt"
    with store.lock():
        write_atomic(other, "kept\n")
    assert other.read_text() == "kept\n"
    write_atomic(base / "plain.md", "x\n")  # no section: no undo log
    assert not txn.pending(base)


def test_the_journal_folder_ignores_itself_in_git(base: Path) -> None:
    ignore = base / ".llmw2/journal/.gitignore"
    assert ignore.is_file()
    text = ignore.read_text()
    assert "*" in text.splitlines() and "!.gitignore" in text.splitlines()


def test_an_error_of_the_store_inside_a_section_undoes_the_notes_it_wrote(base: Path) -> None:
    store = WikiStore(base, actor="test")
    before = digest(base)
    with pytest.raises(InputError), store.lock():
        txn_child._new(store, 3)
        store.move_note("a/nothing.md", "a")
    assert digest(base) == before


def test_directories_made_in_a_section_go_with_it(base: Path) -> None:
    store = WikiStore(base, actor="test")
    with pytest.raises(RuntimeError), store.lock():
        store.create_folder(store.load(), "deep", "Deep.")
        txn.mkdir(base / "deep" / "er" / "still", parents=True, exist_ok=True)
        raise RuntimeError("boom")
    assert not (base / "deep").exists()


def test_a_split_that_fails_half_way_is_undone_and_says_zero_opened(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    """The moves of a split and its "Split" line are one section: an error in it leaves no subfolder and no moved note."""
    from tests.wiki import test_crow_split as split

    wiki = crow_wiki
    split.add_folder(wiki, "/", "sales", "Sales documents.")
    split.seed(wiki, 11)
    llm.add(split.SPLIT, split.groups(("renewals", [1, 2, 3]), ("partners", [4, 5])))
    real, calls = wiki.store.move_note, []

    def failing(rel: str, folder: str, **kwargs: object) -> object:
        calls.append(rel)
        if len(calls) == 4:
            raise OSError("the disk went")
        return real(rel, folder, **kwargs)  # type: ignore[arg-type]

    wiki.store.move_note = failing  # type: ignore[method-assign]
    result = split.file_note(wiki, llm, classifier, 12)
    wiki.store.move_note = real  # type: ignore[method-assign]

    tree = wiki.store.load()
    assert len(calls) == 4 and tree.find("/sales").subfolders == []  # type: ignore[union-attr]
    assert split.direct(wiki) == [split.title(n) for n in range(1, 13)]
    decision = next(d for d in result.decisions if d.step == "split")
    assert (decision.choice, decision.fallback) == ("/sales: error", True)
    assert split.log_lines(wiki, "Split") == ["* **Split**: [/sales](sales/index.md) - at 12 notes, 0 opened"]
    assert split.log_lines(wiki, "Created folder") == ["* **Created folder**: [/sales](sales/index.md) - Sales documents."]
    assert errors(wiki.check()) == [] and not txn.pending(wiki.cfg.bundle)


def test_a_context_copied_during_a_section_does_not_write_into_it_afterwards(base: Path) -> None:
    """A worker thread that kept a copy of the context of a section that has ended is not in a transaction: its writes are plain,
    and leave no undo log behind to roll back unrelated work."""
    import contextvars

    store = WikiStore(base, actor="test")
    copies: list[contextvars.Context] = []
    with store.lock():
        copies.append(contextvars.copy_context())
    assert not txn.pending(base)
    copies[0].run(write_atomic, base / "late.md", "written later\n")
    assert (base / "late.md").exists() and not txn.pending(base) and txn.current() is None
