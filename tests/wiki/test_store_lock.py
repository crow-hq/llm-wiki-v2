# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The bundle's write lock holds across processes, also where there is no flock (Windows)."""

from __future__ import annotations

import multiprocessing
import threading
from pathlib import Path

from llmw2.bundle.store import WikiStore

ROUNDS = 40


def _append(root: str, tag: str, rounds: int) -> None:
    store = WikiStore(Path(root), actor=tag)
    for i in range(rounds):
        with store.lock():  # as the Wiki methods do around a write
            store.append_log("Test", f"{tag}-{i}")


def test_two_processes_lose_no_log_line(tmp_path: Path) -> None:
    store = WikiStore(tmp_path / "w", actor="test")
    store.init()
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_append, args=(str(store.root), tag, ROUNDS)) for tag in ("a", "b")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
        assert p.exitcode == 0
    lines = (store.root / "log.md").read_text(encoding="utf-8").splitlines()
    for tag in ("a", "b"):
        for i in range(ROUNDS):
            assert f"* **Test**: {tag}-{i}" in lines, f"lost {tag}-{i}"


def test_lock_is_released_and_taken_again(tmp_path: Path) -> None:
    store = WikiStore(tmp_path / "w", actor="test")
    store.init()
    try:
        with store.lock():
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    done = threading.Event()

    def other() -> None:
        with WikiStore(store.root, actor="t2").lock():
            done.set()

    t = threading.Thread(target=other)
    t.start()
    t.join(10)
    assert done.is_set()
    with store.lock():
        pass
    assert not [p for p in store.root.glob("*.md") if "lock" in p.name]
