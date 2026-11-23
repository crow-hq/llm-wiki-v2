# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki.remember`: the entry is kept the moment it is asked, a filer thread files it, and the receipt, `status`, `drain` and `close`
say where it is. The crashes (kill after the commit, kill after the acknowledgement) run in a process of their own."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import contextvars
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from llmw2 import FilerError, InputError, QueueFull, Wiki, WikiConfig
from llmw2 import filer as F
from llmw2.bundle import journal as J
from llmw2.bundle import txn
from llmw2.bundle.origin import content_hash, format_at
from llmw2.errors import DiskFull, ModelError, ReplyInvalid
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM

ROOT = Path(__file__).resolve().parents[2]
TEXT = "topic: robots-1\nThe robot left the dock at ten."


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "RETRY_FIRST", 0.05)
    monkeypatch.setattr(F, "IDLE_POLL", 0.01)


def make(bundle: Path, llm: ContentLLM | None = None, **kw: Any) -> tuple[Wiki, ContentLLM]:
    tracker = UsageTracker()
    llm = llm or ContentLLM(tracker)
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm, **kw)
    wiki.init()
    return wiki, llm


@pytest.fixture
def wiki(bundle: Path) -> Iterator[Wiki]:
    w, _ = make(bundle)
    yield w
    w.close()


def entries(bundle: Path) -> list[J.Record]:
    return [r for r in J.Journal(bundle, writable=False).read().records if r.type == J.T_ENTRY]


def kinds(bundle: Path) -> str:
    return "".join(r.kind for r in J.Journal(bundle, writable=False).read().records)


def raws(bundle: Path) -> list[Path]:
    return sorted((bundle / "raw").glob("*.md"))


def notes(bundle: Path) -> list[Path]:
    return sorted(p for p in bundle.rglob("*.md") if p.name not in ("index.md", "log.md") and "raw" not in p.relative_to(bundle).parts)


# -- the basics ------------------------------------------------------------------------------------------------------------


def test_a_wiki_made_starts_no_thread_and_opens_no_journal(bundle: Path) -> None:
    before = {t.name for t in threading.enumerate()}
    w, _ = make(bundle)
    assert {t.name for t in threading.enumerate()} == before
    assert not (bundle / ".llmw2" / "journal").exists() or not list((bundle / ".llmw2" / "journal").glob("*.wal"))
    w.close()


def test_remember_returns_a_receipt_and_a_filer_files_the_entry(wiki: Wiki, bundle: Path) -> None:
    r = wiki.remember(TEXT, title="Dock")
    assert r.state in ("queued", "filing", "filed") and not hasattr(r, "duplicate")
    done = r.wait(30)
    assert done["action"] == "created" and done["note"] and (bundle / done["note"]).exists()
    assert r.state == "filed" and r.note == done["note"]
    wiki.drain(30)
    assert kinds(bundle) == "EFD"
    assert r.to_dict()["state"] == "filed"
    assert any(t.name == "llmw2-filer" for t in threading.enumerate())


def test_the_key_is_the_one_given_or_the_hash_of_the_text(wiki: Wiki, bundle: Path) -> None:
    a = wiki.remember(TEXT)
    b = wiki.remember("topic: robots-2\nA second one.", key="mail:42")
    wiki.drain(30)
    assert a.key == "text:" + content_hash(TEXT).split(":", 1)[1] and b.key == "mail:42"
    recs = {r.meta["id"]: r for r in entries(bundle)}
    assert recs[a.id].meta["key"] == a.key and recs[a.id].meta["origin"] is None
    assert recs[b.id].meta["key"] == "mail:42" and recs[b.id].meta["origin"]["id"] == "mail:42"
    assert recs[a.id].meta["hash"] == content_hash(TEXT)


def test_a_retry_in_the_process_gives_the_same_receipt_and_writes_nothing(wiki: Wiki, bundle: Path) -> None:
    a = wiki.remember(TEXT)
    again = wiki.remember(TEXT)
    wiki.drain(30)
    after = wiki.remember(TEXT)  # filed meanwhile: still the same entry
    assert again.id == a.id == after.id and len(entries(bundle)) == 1 and len(notes(bundle)) == 1


def test_the_same_key_with_another_text_is_an_update_never_a_silent_drop(wiki: Wiki, bundle: Path) -> None:
    a = wiki.remember("topic: robots-1\nFirst version.", key="doc:1")
    b = wiki.remember("topic: robots-1\nSecond version.", key="doc:1")
    assert a.id != b.id
    assert a.wait(30)["action"] == "created"
    assert b.wait(30)["action"] in ("merged", "created")
    wiki.drain(30)
    assert len(raws(bundle)) == 2 and len(entries(bundle)) == 2


def test_a_duplicate_is_known_before_any_model_is_called(bundle: Path) -> None:
    w, llm = make(bundle)
    try:
        w.remember(TEXT, key="k").wait(30)
        used = len(llm.calls)
        assert used > 0
        w.remember(TEXT, key="k").wait(30)  # the same receipt: no entry, no call
        w.drain(30)
        assert len(llm.calls) == used
    finally:
        w.close()
    w2, llm2 = make(bundle)  # another wiki, as another process would be: the journal is read, the entry is found
    try:
        w2.remember(TEXT, key="k").wait(30)
        w2.drain(30)
        assert llm2.calls == [] and len(notes(bundle)) == 1
    finally:
        w2.close()


def test_a_text_filed_before_is_found_at_the_filing_before_any_model_call(bundle: Path) -> None:
    w, llm = make(bundle)
    try:
        w.ingest(TEXT)  # no origin: a raw copy with this text, cited by a note
        used = len(llm.calls)
        r = w.remember(TEXT)  # the key is the hash of the text; the entry is new, the filing finds the copy
        done = r.wait(30)
        assert done["action"] == "unchanged" and done["note"] and len(llm.calls) == used and len(notes(bundle)) == 1
    finally:
        w.close()


def test_the_same_text_at_another_time_is_unchanged_and_the_new_time_is_recorded(bundle: Path) -> None:
    w, llm = make(bundle)
    first = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
    second = first + timedelta(minutes=5)
    try:
        a = w.remember(TEXT, at=first)
        a.wait(30)
        used = len(llm.calls)
        b = w.remember(TEXT, at=second)
        done = b.wait(30)
        assert a.id != b.id and done["action"] == "unchanged" and done["event_at"] == format_at(second) and done["note"] == a.note
        w.drain(30)
        assert len(llm.calls) == used and len(raws(bundle)) == 1 and len(notes(bundle)) == 1
        raw = raws(bundle)[0].read_text(encoding="utf-8")
        assert f"event_at: '{format_at(first)}'" in raw and format_at(second) not in raw  # the first one stays in the raw copy
        lines = w.log(5)
        assert any(format_at(second) in line and "Seen again" in line for line in lines)
    finally:
        w.close()


# -- the time of the event ---------------------------------------------------------------------------------------------------


def test_100_times_a_millisecond_apart_sort_as_strings_in_the_order_of_time() -> None:
    zones = [UTC, timezone(timedelta(hours=2)), timezone(timedelta(hours=-5, minutes=-30))]
    start = datetime(2026, 5, 1, 23, 59, 59, 950_000, tzinfo=UTC)
    moments = [(start + timedelta(milliseconds=i)).astimezone(zones[i % 3]) for i in range(100)]
    strings = [format_at(m) for m in moments]
    assert strings == sorted(strings) and len(set(strings)) == 100
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00", s) for s in strings)


def test_the_hundred_times_keep_their_order_through_remember(wiki: Wiki, bundle: Path) -> None:
    start = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    receipts = [wiki.remember(f"topic: ros-{i % 3}\nevent {i}", at=start + timedelta(milliseconds=i)) for i in range(100)]
    got = [r.event_at for r in receipts]
    assert got == sorted(got)
    wiki.drain(120)
    recs = {r.meta["id"]: r.meta for r in entries(bundle)}
    assert [recs[r.id]["event_at"] for r in receipts] == got and all(recs[r.id]["at"].endswith("+00:00") for r in receipts)


def test_times_near_1970_are_accepted(wiki: Wiki, bundle: Path) -> None:
    for moment in (
        datetime(1970, 1, 1, 0, 0, 0, 1000, tzinfo=UTC),
        datetime(1970, 1, 1, 0, 0, 3, tzinfo=timezone(timedelta(hours=1))),  # before the epoch in UTC
        datetime(1969, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
    ):
        r = wiki.remember(f"topic: sim-1\nsim time {moment.isoformat()}", at=moment)
        assert r.event_at == format_at(moment) and r.event_at.endswith("+00:00")
    wiki.drain(60)
    assert "event_at: '1970-01-01T00:00:00.001000+00:00'" in "".join(p.read_text(encoding="utf-8") for p in raws(bundle))


def test_a_time_with_no_zone_is_refused_and_nothing_is_written(wiki: Wiki, bundle: Path) -> None:
    with pytest.raises(InputError):
        wiki.remember(TEXT, at=datetime(2026, 3, 1, 10, 0, 0))
    assert entries(bundle) == [] and not list((bundle / ".llmw2" / "journal").glob("*.wal"))


def test_a_remember_with_no_time_leaves_a_raw_copy_as_ingest_does(bundle: Path, tmp_path: Path) -> None:
    w, _ = make(bundle)
    w2, _ = make(tmp_path / "other")
    try:
        w.remember(TEXT, title="Dock").wait(30)
        w.drain(30)
        w2.ingest(TEXT, title="Dock")
        a, b = raws(bundle)[0].read_bytes(), raws(tmp_path / "other")[0].read_bytes()

        def plain(data: bytes) -> bytes:
            return re.sub(rb"(?m)^(  at: ).*$", rb"\1T", data)

        assert plain(a) == plain(b) and b"event_at" not in a
    finally:
        w.close()
        w2.close()


def test_the_entry_has_the_time_of_the_ack_and_the_time_of_the_event(wiki: Wiki, bundle: Path) -> None:
    moment = datetime(2026, 3, 1, 10, 15, 30, 123456, tzinfo=timezone(timedelta(hours=1)))
    a = wiki.remember(TEXT, at=moment)
    b = wiki.remember("topic: robots-2\nNo time here.")
    wiki.drain(30)
    recs = {r.meta["id"]: r.meta for r in entries(bundle)}
    assert recs[a.id]["event_at"] == "2026-03-01T09:15:30.123456+00:00" and "event_at" not in recs[b.id]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00", recs[b.id]["at"])
    both = "".join(p.read_text(encoding="utf-8") for p in raws(bundle))
    assert "event_at: '2026-03-01T09:15:30.123456+00:00'" in both


# -- drain, close, errors ----------------------------------------------------------------------------------------------------


class Gated(ContentLLM):
    """A model that waits for the test before it answers."""

    def __init__(self, usage: UsageTracker) -> None:
        super().__init__(usage)
        self.gate = threading.Event()

    def chat(self, messages: list[dict[str, str]], **kw: Any) -> str:
        self.gate.wait(60)
        return super().chat(messages, **kw)


def test_drain_that_times_out_raises_and_loses_nothing(bundle: Path) -> None:
    llm = Gated(UsageTracker())
    w, _ = make(bundle, llm)
    try:
        r = w.remember(TEXT)
        with pytest.raises(TimeoutError):
            w.drain(0.2)
        with pytest.raises(TimeoutError):
            r.wait(0.05)
        assert len(entries(bundle)) == 1 and r.state in ("queued", "filing")
        llm.gate.set()
        w.drain(30)
        assert r.state == "filed" and len(notes(bundle)) == 1 and kinds(bundle) == "EFD"
    finally:
        llm.gate.set()
        w.close()


def test_drain_with_no_filer_raises_instead_of_hanging(bundle: Path) -> None:
    w, _ = make(bundle)
    try:
        w._stop_filer()  # what the server does to the wiki it replaces
        r = w.remember(TEXT)  # written, and not filed: this wiki does not start a filer again
        with pytest.raises(FilerError):
            w.drain(5)
        with pytest.raises(FilerError):
            r.wait(5)
        assert len(entries(bundle)) == 1
    finally:
        w.close()
    w2, _ = make(bundle)  # another wiki files what was left
    try:
        w2.drain(30)
        assert len(notes(bundle)) == 1
    finally:
        w2.close()


def test_a_closed_wiki_takes_no_more_entries_and_close_can_be_repeated(bundle: Path) -> None:
    w, _ = make(bundle)
    w.remember(TEXT).wait(30)
    w.close()
    w.close()
    with pytest.raises(Exception, match="closed"):
        w.remember("topic: x-1\nmore")


def test_a_failure_that_may_pass_is_raised_once_by_drain_and_the_entry_stays_first(bundle: Path) -> None:
    class Flaky(Gated):
        failures = 1

        def chat(self, messages: list[dict[str, str]], **kw: Any) -> str:
            self.gate.wait(60)  # the failure comes while drain is waiting
            if self.failures:
                self.failures -= 1
                raise ModelError("the endpoint is down")
            return super().chat(messages, **kw)

    llm = Flaky(UsageTracker())
    w, _ = make(bundle, llm)
    try:
        w.remember(TEXT)
        w.remember("topic: robots-2\nAfter it.")
        threading.Timer(0.3, llm.gate.set).start()
        with pytest.raises(FilerError, match="endpoint is down"):
            w.drain(30)
        w.drain(30)  # the next one goes through
        assert len(notes(bundle)) >= 1 and kinds(bundle).count("D") == 2
    finally:
        w.close()


def test_an_entry_that_cannot_be_filed_is_given_up_after_three_attempts(bundle: Path) -> None:
    llm = ContentLLM(UsageTracker(), fail_on={"poison": ReplyInvalid("no usable JSON")})
    w, _ = make(bundle, llm)
    try:
        bad = w.remember("topic: robots-1\npoison")
        good = w.remember("topic: robots-2\nfine")
        with pytest.raises(FilerError):
            bad.wait(30)
        good.wait(30)
        w.drain(30)  # what is given up is closed: it does not hold the drain
        assert bad.state == "failed" and kinds(bundle).count("A") == 3 and "X" in kinds(bundle)
        assert w.status()["failed"] == 1
        assert next(r.text for r in entries(bundle)) == b"topic: robots-1\npoison"  # the text is kept
    finally:
        w.close()


def test_a_queue_that_is_full_refuses_and_writes_nothing(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "QUEUE_LIMIT", 2)
    llm = Gated(UsageTracker())
    w, _ = make(bundle, llm)
    try:
        w.remember("topic: a-1\none")
        w.remember("topic: a-2\ntwo")
        with pytest.raises(QueueFull):
            w.remember("topic: a-3\nthree", timeout=0.2)
        assert len(entries(bundle)) == 2
        llm.gate.set()
        w.drain(30)
        w.remember("topic: a-3\nthree", timeout=5).wait(30)
    finally:
        llm.gate.set()
        w.close()


def test_a_disk_with_no_room_is_refused_before_anything_is_written(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w, _ = make(bundle)
    try:
        monkeypatch.setattr(F.shutil, "disk_usage", lambda p: type("U", (), {"free": 1024})())
        with pytest.raises(DiskFull):
            w.remember(TEXT)
        assert entries(bundle) == []
    finally:
        w.close()


# -- status -----------------------------------------------------------------------------------------------------------------


def test_status_has_a_fixed_set_of_keys_and_is_json(wiki: Wiki) -> None:
    st = wiki.status()
    assert set(st) == set(F.ENTRY_KEYS) and st["queue"] == 0 and st["queue_limit"] == F.QUEUE_LIMIT and st["failed"] == 0
    wiki.remember(TEXT).wait(30)
    wiki.drain(30)
    st = wiki.status()
    assert set(st) == set(F.ENTRY_KEYS) and st["queue"] == 0 and st["journal_bytes"] > 0 and st["disk_free"] > 0
    assert isinstance(st["warnings"], list) and json.loads(json.dumps(st)) == st


def test_status_is_cheap_it_does_not_check_the_wiki_lock_the_journal_or_read_it_all(wiki: Wiki, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki.remember(TEXT).wait(30)
    wiki.drain(30)
    wiki.status()  # warm

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("status must not do this")

    taken: list[str] = []
    real = J.Journal._flock

    def spy(self: J.Journal, name: str) -> Any:
        taken.append(name)
        return real(self, name)

    monkeypatch.setattr("llmw2.bundle.check.check", boom)
    monkeypatch.setattr("llmw2.wiki.check", boom)
    monkeypatch.setattr(J.Journal, "check", boom)
    monkeypatch.setattr(J.Journal, "read", boom)
    monkeypatch.setattr(J.Journal, "_flock", spy)
    monkeypatch.setattr(Wiki, "check", boom)
    wiki.status()
    wiki.status()
    assert "append.lock" not in taken


def test_status_says_a_filer_that_is_not_running_is_a_warning_when_something_waits(bundle: Path) -> None:
    w, _ = make(bundle)
    try:
        w._stop_filer()
        w.remember(TEXT)
        st = w.status()
        assert st["queue"] == 1 and st["filer"] in ("none", "other") and any("filer" in x for x in st["warnings"])
    finally:
        w.close()


# -- crashes, in a process of their own ---------------------------------------------------------------------------------------

CHILD = r"""
import os, sys, json
from pathlib import Path
from llmw2 import Wiki, WikiConfig
from llmw2.bundle import journal as J
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM

bundle, mode, text, key = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4] or None
wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=ContentLLM(UsageTracker()))
wiki.init()
if mode == "die-before-f":  # the section committed and the process dies before it tells the journal
    real = J.Journal.append
    def append(self, rtype, meta, text=b"", **kw):
        if rtype == J.T_FILED:
            os._exit(9)
        return real(self, rtype, meta, text, **kw)
    J.Journal.append = append
r = wiki.remember(text, key=key)
print(json.dumps({"id": r.id}), flush=True)
if mode == "die-after-ack":  # the caller never saw the answer
    os._exit(9)
wiki.drain(60)
"""


def run_child(bundle: Path, mode: str, text: str, key: str = "") -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    args = [sys.executable, "-c", CHILD, str(bundle), mode, text, key]
    return subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)


@pytest.mark.parametrize("key", ["", "mail:7"])
def test_kill_between_the_commit_and_the_f_record_no_duplicate_and_the_receipt_says_filed(bundle: Path, key: str) -> None:
    w, _ = make(bundle)
    w.close()
    p = run_child(bundle, "die-before-f", TEXT, key)
    assert p.returncode == 9, p.stderr
    assert len(notes(bundle)) == 1 and "F" not in kinds(bundle)  # the section committed: the note is there; nobody said so
    w2, llm2 = make(bundle)  # a new process; opening the wiki recovers
    try:
        assert "F" in kinds(bundle)
        r = w2.remember(TEXT, key=key or None)  # the retry of a caller who never heard back
        assert r.state == "filed" and r.wait(30)["note"] == str(notes(bundle)[0].relative_to(bundle)).replace(os.sep, "/")
        w2.drain(60)
        assert len(notes(bundle)) == 1 and len(raws(bundle)) == 1 and llm2.calls == [] and len(entries(bundle)) == 1
        assert kinds(bundle).count("F") == 1 and kinds(bundle).count("D") == 1
    finally:
        w2.close()


@pytest.mark.parametrize("key", ["", "mail:7"])
def test_kill_after_the_ack_a_new_process_retries_and_one_note_is_made(bundle: Path, key: str) -> None:
    w, _ = make(bundle)
    w.close()
    p = run_child(bundle, "die-after-ack", TEXT, key)
    assert p.returncode == 9, p.stderr
    w2, _ = make(bundle)
    try:
        r = w2.remember(TEXT, key=key or None)
        r.wait(60)
        w2.drain(60)
        assert len(notes(bundle)) == 1 and len(raws(bundle)) == 1
        assert not txn.pending(bundle)
    finally:
        w2.close()


def test_the_filer_starts_with_an_empty_context_and_never_inherits_undo_suspended(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from llmw2.bundle import store as S

    seen: list[object] = []
    real = Wiki._work_on

    def spy(self: Wiki, entry: F.Entry, filer: F.Filer) -> None:
        seen.append(S._undo_off.get())
        real(self, entry, filer)

    monkeypatch.setattr(Wiki, "_work_on", spy)
    real_start = F.Filer.start

    def start(self: F.Filer) -> None:  # a thread that inherits the context of its starter (Python 3.14, a copied context)
        ctx, real_run = contextvars.copy_context(), self.run
        self.run = lambda: ctx.run(real_run)  # type: ignore[method-assign]
        real_start(self)

    monkeypatch.setattr(F.Filer, "start", start)
    w, _ = make(bundle)
    try:
        with w.store.undo_suspended():  # the thread is started in here
            r = w.remember(TEXT)
            r.wait(30)
        assert seen and all(not v for v in seen)
    finally:
        w.close()


def test_the_filer_uses_the_models_of_the_wiki_that_asked_last(bundle: Path) -> None:
    a, llm_a = make(bundle)  # never closed, as a script or a test may leave it
    b, llm_b = make(bundle)
    try:
        a.remember(TEXT).wait(30)
        used_by_a = len(llm_a.calls)
        assert used_by_a > 0 and llm_b.calls == []
        r = b.remember("topic: robots-9" + chr(10) + "A text asked of the second wiki.")
        r.wait(30)
        assert llm_b.calls and len(llm_a.calls) == used_by_a  # the filer files what b asked with b's models
    finally:
        b.close()
        a.close()


def test_the_filer_that_reads_the_entry_before_the_call_returns_files_it_with_the_models_of_the_wiki_that_asked(
    bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The idle filer of `a` reads the journal while `b` writes its entry, and files it before `b.remember` returns (as when the
    caller's thread is paused right after the write): with `b`'s models all the same."""
    a, llm_a = make(bundle)
    b, llm_b = make(bundle)
    real = J.Journal.append_entry
    asked = "topic: robots-9" + chr(10) + "A text asked of the second wiki."

    def append_entry(self: J.Journal, text: str, **kw: Any) -> Any:
        ref = real(self, text, **kw)
        if text == asked:  # the caller pauses after the write until the filer has filed the entry
            hub = b._journal()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                hub.refresh()
                if (e := hub.entries.get(ref.id)) is None or e.filed is not None:
                    break
                with hub.cond:
                    hub.cond.wait(0.01)
        return ref

    monkeypatch.setattr(J.Journal, "append_entry", append_entry)
    try:
        a.remember(TEXT).wait(30)
        used_by_a = len(llm_a.calls)
        b.remember(asked).wait(30)
        assert llm_b.calls and len(llm_a.calls) == used_by_a
    finally:
        b.close()
        a.close()
