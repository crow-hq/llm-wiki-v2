# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The file lock holds across threads and processes, waits in the kernel, honours a timeout and dies with its holder."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import multiprocessing
import os
import threading
import time
from pathlib import Path

import pytest

from llmw2.bundle.filelock import FileLock


def _hold(path: str, ready: object, release: object, die: bool) -> None:
    lock = FileLock(path)
    assert lock.acquire(5)
    ready.set()  # type: ignore[attr-defined]
    if die:
        time.sleep(0.3)
        os._exit(0)  # the kernel frees the lock: no release call
    release.wait(30)  # type: ignore[attr-defined]
    lock.release()


def _count(path: str, counter: str, rounds: int) -> None:
    lock = FileLock(path)
    for _ in range(rounds):
        with lock:
            n = int(Path(counter).read_text() or "0")
            Path(counter).write_text(str(n + 1))


def test_try_acquire_and_release(tmp_path: Path) -> None:
    a, b = FileLock(tmp_path / "x.lock"), FileLock(tmp_path / "x.lock")
    assert a.try_acquire() and a.held
    assert not b.try_acquire()  # the same process, another object: excluded
    a.release()
    assert not a.held and b.try_acquire()
    b.release()
    a.release()  # a second release is harmless


def test_not_reentrant(tmp_path: Path) -> None:
    lock = FileLock(tmp_path / "x.lock")
    with lock, pytest.raises(RuntimeError):
        lock.acquire()
    assert lock.try_acquire()
    lock.release()


def test_timeout_is_measured_on_the_monotonic_clock(tmp_path: Path) -> None:
    holder, waiter = FileLock(tmp_path / "x.lock"), FileLock(tmp_path / "x.lock")
    with holder:
        start = time.monotonic()
        assert waiter.acquire(timeout=0.3) is False
        assert 0.25 <= time.monotonic() - start < 3
        with waiter.attempt(0) as got:
            assert got is False
    with waiter.attempt(1) as got:
        assert got is True and waiter.held
    assert not waiter.held


def test_a_blocked_thread_gets_the_lock_when_it_is_freed(tmp_path: Path) -> None:
    path = tmp_path / "x.lock"
    first, second = FileLock(path), FileLock(path)
    first.acquire()
    got: list[float] = []

    def wait() -> None:
        start = time.monotonic()
        with second:
            got.append(time.monotonic() - start)

    t = threading.Thread(target=wait)
    t.start()
    time.sleep(0.2)
    assert not got
    first.release()
    t.join(10)
    assert got and got[0] >= 0.15


def test_the_lock_creates_its_folder(tmp_path: Path) -> None:
    with FileLock(tmp_path / "a" / "b" / "x.lock"):
        assert (tmp_path / "a" / "b" / "x.lock").is_file()


def test_another_process_is_excluded_until_the_holder_releases(tmp_path: Path) -> None:
    path = str(tmp_path / "x.lock")
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    p = ctx.Process(target=_hold, args=(path, ready, release, False))
    p.start()
    try:
        assert ready.wait(60)
        mine = FileLock(path)
        assert not mine.try_acquire()
        assert mine.acquire(timeout=0.2) is False
        release.set()
        assert mine.acquire(timeout=30)
        mine.release()
    finally:
        release.set()
        p.join(30)
    assert p.exitcode == 0


def test_the_lock_is_freed_when_its_holder_dies(tmp_path: Path) -> None:
    path = str(tmp_path / "x.lock")
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    p = ctx.Process(target=_hold, args=(path, ready, release, True))
    p.start()
    assert ready.wait(60)
    mine = FileLock(path)
    assert mine.acquire(timeout=30)  # waits in the kernel until the holder is gone
    mine.release()
    p.join(30)


def test_processes_and_threads_do_not_lose_updates(tmp_path: Path) -> None:
    path, counter = str(tmp_path / "x.lock"), tmp_path / "count"
    counter.write_text("0")
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_count, args=(path, str(counter), 30)) for _ in range(3)]
    for p in procs:
        p.start()
    threads = [threading.Thread(target=_count, args=(path, str(counter), 30)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    for p in procs:
        p.join(120)
        assert p.exitcode == 0
    assert int(counter.read_text()) == 6 * 30


def test_a_lock_that_keeps_its_file_open_is_still_a_lock(tmp_path: Path) -> None:
    a, b = FileLock(tmp_path / "k.lock", keep_open=True), FileLock(tmp_path / "k.lock", keep_open=True)
    for _ in range(3):  # taken and given back many times with the one descriptor
        assert a.try_acquire() and a.held
        assert not b.try_acquire()  # another object of the process: excluded
        a.release()
        assert b.try_acquire()
        b.release()
    assert a._kept is not None
    a.close()
    b.close()
    assert a._kept is None and b._kept is None
    assert a.try_acquire()  # and it opens its file again when it is needed
    a.release()
    a.close()
