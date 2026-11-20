# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The filer files the entries of a lot in one session of the store: the tree is listed once a lot, then only what changed; the session
ends when the queue is empty and before a purge is completed; the split of a folder looks at the folder's own listing before the tree."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from llmw2 import IngestResult, Wiki, WikiConfig
from llmw2 import filer as F
from llmw2.bundle import store as S
from llmw2.models.usage import UsageTracker
from llmw2.wiki import SPLIT_AT
from tests.wiki.fakes import ContentLLM, FakeClassifier
from tests.wiki.test_remember import make

GROUPS = ("alpha", "bravo", "charlie", "delta")


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "RETRY_FIRST", 0.05)
    monkeypatch.setattr(F, "IDLE_POLL", 0.01)


def aged(root: Path) -> None:
    """Every file and folder older than RACY_NS: what a session trusts by its time."""
    old = time.time_ns() - 3 * S.RACY_NS
    for p in [*root.rglob("*"), root]:
        os.utime(p, ns=(old, old))


def listings(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """How many times each folder of the wiki is listed."""
    seen: dict[str, int] = {}
    real = os.scandir

    def counted(path: Any = ".") -> Any:
        seen[Path(path).name] = seen.get(Path(path).name, 0) + 1
        return real(path)

    monkeypatch.setattr(S.os, "scandir", counted)
    return seen


def test_a_lot_lists_the_folders_it_does_not_touch_once(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w, _ = make(bundle)
    for g in GROUPS:  # four folders, one note each, filed before
        w.remember(f"topic: {g}-0\nThe first {g} memory.", key=f"{g}-0")
    w.drain(60)
    w._stop_filer()
    aged(bundle)
    for i in range(1, 11):  # ten entries, all for the folder alpha
        w.remember(f"topic: alpha-{i}\nAnother alpha memory number {i}.", key=f"alpha-{i}")
    seen = listings(monkeypatch)
    w._filer_stopped = False
    w.drain(60)
    assert all(seen.get(g, 0) <= 1 for g in GROUPS[1:]), str(sorted(seen.items()))  # untouched folders: listed once a lot
    w.close()


def test_the_session_ends_when_the_queue_is_empty(bundle: Path) -> None:
    w, _ = make(bundle)
    w.remember("topic: alpha-1\nA memory.", key="a1")
    w.drain(60)
    deadline = time.monotonic() + 10
    while w.store._depth and time.monotonic() < deadline:
        time.sleep(0.01)
    assert w.store._depth == 0 and w._journal().filer is not None and w._journal().filer.is_alive()
    w.close()


def test_a_tombstone_between_two_entries_of_a_lot_forgets_the_second_and_completes_out_of_the_session(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, _ = make(bundle)
    w._stop_filer()
    first = w.remember("topic: alpha-1\nThe first memory of the lot.", key="first")
    w.remember("topic: alpha-2\nThe purple secret of the lot.", key="secret")
    third = w.remember("topic: alpha-3\nThe third memory of the lot.", key="third")
    real_work, real_finish = Wiki._work_on, Wiki._finish_forget
    depth_at_finish: list[int] = []

    def work(self: Wiki, entry: Any, filer: Any) -> None:
        real_work(self, entry, filer)
        if entry.id == first.id:  # another process purges the key of the next entry, while the lot goes on
            filer.hub.journal.append_entry("", state={"forget": {"kh": F.key_hash("secret")}})

    def finish(self: Wiki, tomb: Any) -> bool:
        depth_at_finish.append(self.store._depth)
        return real_finish(self, tomb)

    monkeypatch.setattr(Wiki, "_work_on", work)
    monkeypatch.setattr(Wiki, "_finish_forget", finish)
    w._filer_stopped = False
    w.drain(60)
    deadline = time.monotonic() + 30
    while w._journal().forgets() and time.monotonic() < deadline:
        w._journal().refresh()
        time.sleep(0.02)
    assert first.note and third.note and w._journal().forgets() == []
    assert not any("purple secret" in p.read_text(encoding="utf-8") for p in bundle.rglob("*.md"))
    assert depth_at_finish and set(depth_at_finish) == {0}  # the purge is completed out of the lot's session
    w.close()


def test_a_split_looks_at_the_folder_listing_before_the_tree(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ut = UsageTracker()
    w = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=ContentLLM(ut), classifier=FakeClassifier(ut))
    w.init()
    folder, _ = w.store.create_folder(w.store.load(), "sales", "Sales.")
    for n in range(SPLIT_AT - 1):
        w.store.write_note(folder, title=f"Note {n}", summary="s", body="b", tags=[], source={"resource": "/raw/x.md"})
    loads: list[int] = []
    real = S.WikiStore.load

    def counted(self: S.WikiStore) -> Any:
        loads.append(1)
        return real(self)

    monkeypatch.setattr(S.WikiStore, "load", counted)
    created = IngestResult("created", "sales/note-0.md", "Note 0", "/sales", [], [], [])
    w._split(created)
    assert loads == []  # under the threshold: the folder's listing alone
    w.store.write_note(real(w.store).find("sales"), title="One more", summary="s", body="b", tags=[], source={"resource": "/raw/x.md"})
    loads.clear()
    w._split(created)
    assert loads  # at the threshold: the tree, and the attempt as before (a "Split" line in the log)
    assert "Split" in (bundle / "log.md").read_text(encoding="utf-8")
    w.close()


def test_the_session_ends_while_a_failed_entry_waits_to_be_tried_again(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The session holds for the whole process: while the filer waits (up to RETRY_LAST) an `ask` must see the files as they are."""
    w, _ = make(bundle)
    w._stop_filer()
    w.remember("topic: alpha-1\nA memory that fails once.", key="a1")
    real = Wiki._work_on
    failed: list[int] = []

    def flaky(self: Wiki, entry: Any, filer: Any) -> None:
        if not failed:
            failed.append(self.store._depth)
            raise RuntimeError("a model that is down, for now")
        real(self, entry, filer)

    monkeypatch.setattr(Wiki, "_work_on", flaky)
    monkeypatch.setattr(F, "RETRY_FIRST", 2.0)
    w._filer_stopped = False
    w._start_filer()
    deadline = time.monotonic() + 10
    while not w._journal().errors and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)  # the filer is in its wait now
    assert failed == [1] and w.store._depth == 0  # filed in a session, which ended for the wait
    w.drain(60)
    w.close()
