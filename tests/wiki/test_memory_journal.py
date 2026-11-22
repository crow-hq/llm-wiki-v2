# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The MCP memory on the journal of `remember`: with the wiki's models `remember` answers once the entry is on the disk, with the
keys a client of the release before reads; `status` says what waits; `forget` takes a memory out by its key or its id, with no byte
of it left; the server gives what it queued a short drain at its end, and an entry acknowledged survives a server killed."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from fastapi.testclient import TestClient
from mcp import Client

from llmw2 import Wiki
from llmw2 import filer as F
from llmw2.bundle import journal as J
from llmw2.bundle.filelock import FileLock
from llmw2.bundle.origin import Origin
from llmw2.bundle.store import WikiStore
from llmw2.memory import CLOSE_DRAIN, Memory
from llmw2.server import create_app
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_memory import INITIALIZE, client, client_wiki, text, through
from tests.wiki.test_purge_journal import complete, leftovers, tombstones
from tests.wiki.test_remember import make, notes

ROOT = Path(__file__).resolve().parents[2]
WORD = "quillfeather"
FACT = f"topic: robots-1\nThe robot named {WORD} left the dock at ten."
SECRET = "the purple falcon lands at midnight by the old mill"
KEY = "secret-7"


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "RETRY_FIRST", 0.05)
    monkeypatch.setattr(F, "IDLE_POLL", 0.01)


@pytest.fixture
def wiki(bundle: Path) -> Iterator[Wiki]:
    w, _ = make(bundle)  # classic, with a model that answers from the prompt alone: the wiki's own models
    w.cfg.mcp = True
    yield w
    w.close()


def calls(memory: Memory, *tools: tuple[str, dict[str, Any]]) -> list[Any]:
    """The tools called one after the other by one client of the SDK, connected in-process."""

    async def go() -> list[Any]:
        async with Client(memory.server) as session:
            return [await session.call_tool(name, arguments) for name, arguments in tools]

    return asyncio.run(go())


def one(memory: Memory, tool: str, arguments: dict[str, Any]) -> Any:
    return calls(memory, (tool, arguments))[0]


class Held:
    """The wiki's lock held by another thread, as a filing of another process holds it, until `release` (at most 20 s)."""

    def __init__(self, wiki: Wiki) -> None:
        self.held, self.free = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self.hold, args=(wiki,))
        self.thread.start()
        assert self.held.wait(10)

    def hold(self, wiki: Wiki) -> None:
        with wiki.store.lock():
            self.held.set()
            self.free.wait(20)

    def release(self) -> None:
        self.free.set()
        self.thread.join()


# -- remember ----------------------------------------------------------------------------------------------------------


def test_remember_answers_while_the_wiki_is_held_for_writing_and_search_and_read_note_find_it(wiki: Wiki) -> None:
    # ARRANGE: a filing elsewhere holds the wiki, which `ingest` would wait for
    memory = Memory(lambda: wiki)
    held = Held(wiki)
    try:
        # ACT
        start = time.monotonic()
        kept, found, waiting = calls(memory, ("remember", {"text": FACT, "title": "Dock"}), ("search", {"query": WORD}), ("status", {}))
        took = time.monotonic() - start
    finally:
        held.release()
    wiki.drain(30)
    read, done = calls(memory, ("read_note", {"path": f"pending:{kept.structured_content['id']}"}), ("status", {}))

    # ASSERT
    body = kept.structured_content
    assert not kept.is_error and took < 10  # the lock was held for 20 s
    assert body["state"] in ("queued", "filing") and body["key"].startswith("text:") and body["id"]
    assert [h["path"] for h in found.structured_content["result"]] == [f"pending:{body['id']}"]
    assert set(waiting.structured_content) == set(F.ENTRY_KEYS) and waiting.structured_content["queue"] == 1
    assert not read.is_error and WORD in text(read) and text(read).startswith("Title: ")  # the note it went in
    assert done.structured_content["queue"] == 0 and done.structured_content["failed"] == 0
    assert len(notes(wiki.cfg.bundle)) == 1


def test_a_client_of_the_release_before_reads_the_keys_it_knew_with_nothing_filed_yet(wiki: Wiki) -> None:
    # ARRANGE
    memory = Memory(lambda: wiki)

    async def go() -> tuple[Any, Any]:
        async with Client(memory.server) as session:
            tools = {t.name: t for t in (await session.list_tools()).tools}
            return tools["remember"], await session.call_tool("remember", {"text": FACT, "title": "Dock"})

    # ACT
    tool, kept = asyncio.run(go())

    # ASSERT: the same required arguments; the keys of 1.0.5 there, with the values of "nothing filed yet", and the receipt added
    body = json.loads(json.dumps(kept.structured_content))
    assert tool.input_schema["required"] == ["text"]
    assert {k: body[k] for k in ("action", "note", "folder", "related")} == {"action": "queued", "note": "", "folder": "", "related": []}
    assert [type(body[k]) for k in ("action", "note", "folder", "related")] == [str, str, str, list]  # the types of 1.0.5: "" is no note
    assert set(body) == {"action", "note", "folder", "related", "saved", "id", "key", "state"} and body["saved"] is True


def test_the_same_text_given_again_once_filed_is_unchanged_with_its_note_as_in_the_release_before(wiki: Wiki) -> None:
    # ARRANGE
    memory = Memory(lambda: wiki)
    first = one(memory, "remember", {"text": FACT})
    wiki.drain(30)

    # ACT
    again = one(memory, "remember", {"text": FACT}).structured_content

    # ASSERT
    (note,) = notes(wiki.cfg.bundle)
    rel = note.relative_to(wiki.cfg.bundle).as_posix()
    assert again["id"] == first.structured_content["id"] and again["state"] == "filed"
    folder = "/" + rel.rpartition("/")[0]
    assert {k: again[k] for k in ("action", "note", "folder")} == {"action": "unchanged", "note": rel, "folder": folder}


def test_the_same_text_again_gives_the_first_receipt_and_a_key_names_the_memory(wiki: Wiki) -> None:
    memory = Memory(lambda: wiki)

    first, again, keyed = calls(
        memory, ("remember", {"text": FACT}), ("remember", {"text": FACT}), ("remember", {"text": FACT + " Again.", "key": KEY})
    )

    assert again.structured_content["id"] == first.structured_content["id"]
    assert keyed.structured_content["key"] == KEY and keyed.structured_content["id"] != first.structured_content["id"]


def test_a_text_the_journal_refuses_tells_the_client_why(wiki: Wiki) -> None:
    result = one(Memory(lambda: wiki), "remember", {"text": "  "})

    assert result.is_error and "nothing to remember" in text(result)


# -- forget ------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("filed", [False, True])
@pytest.mark.parametrize("by", ["key", "id", "pending"])
def test_forget_takes_a_memory_out_by_its_key_or_its_id_and_no_byte_of_it_is_left(wiki: Wiki, by: str, filed: bool) -> None:
    # ARRANGE: the secret queued (no filer) or filed, and another memory that stays
    memory = Memory(lambda: wiki)
    if not filed:
        wiki._stop_filer()  # the entry is written and nobody files it: it is still queued when it is forgotten
    kept = one(memory, "remember", {"text": f"topic: birds-1\n{SECRET}.", "title": "Falcon", "key": KEY}).structured_content
    one(memory, "remember", {"text": FACT})
    if filed:
        wiki.drain(30)
    named = {"key": {"key": KEY}, "id": {"id": kept["id"]}, "pending": {"id": f"pending:{kept['id']}"}}[by]

    # ACT
    gone, found = calls(memory, ("forget", {**named, "confirm": True}), ("search", {"query": "purple falcon"}))

    # ASSERT
    report = gone.structured_content
    assert not gone.is_error and report["entries"] == 1 and report["pending"] == ["journal"]  # the journal is the filer's
    assert len(report["notes"]) == (1 if filed else 0)
    assert found.structured_content["result"] == []
    assert complete(wiki) and leftovers(wiki.cfg.bundle, SECRET) == []  # B11: in no file of the wiki's folder


def gone_from_disk(wiki: Wiki, text: str, within: float = 15.0) -> bool:
    """The text is in no file of the wiki's folder once the filer that holds the journal's lease completed what was left to it: its
    tombstones done (read from the journal), then one look at every file (looking again and again while the wiki is open meets files
    that SQLite holds locked on Windows)."""
    hub = wiki._journal()
    end = time.monotonic() + within
    while True:
        hub.refresh()
        if not hub.forgets():
            break
        if time.monotonic() > end:
            return False
        time.sleep(0.05)
    return leftovers(wiki.cfg.bundle, text) == []


def remembered_secret(wiki: Wiki, memory: Memory) -> str:
    """The secret remembered and filed through the journal, with another memory that stays; the path of the secret's note."""
    one(memory, "remember", {"text": f"topic: birds-1\n{SECRET}.", "title": "Falcon"})
    one(memory, "remember", {"text": FACT})
    wiki.drain(30)
    return str(one(memory, "search", {"query": "purple falcon"}).structured_content["result"][0]["path"])


def test_delete_note_of_a_memory_leaves_no_byte_of_it_in_the_journal(wiki: Wiki) -> None:
    """A client of the release before forgets with `delete_note` (the note and its raw copy go, as there). The entry of the memory is in
    the journal now: it goes too, so that no file of the wiki's folder keeps the text, as in the release before."""
    memory = Memory(lambda: wiki)
    path = remembered_secret(wiki, memory)

    deleted = one(memory, "delete_note", {"path": path, "confirm": True}).structured_content

    assert deleted["notes"] == [path] and deleted["pending"] in ([], ["journal"])  # the filer of this process may hold the lease
    assert gone_from_disk(wiki, SECRET)  # B11, as with forget: in no file of the wiki's folder
    assert one(memory, "search", {"query": WORD}).structured_content["result"]  # the other memory stays


def test_delete_note_of_a_memory_still_queued_forgets_it_as_forget_does(wiki: Wiki) -> None:
    """A client of the release before searches and deletes the first hit; with the journal that hit may be `pending:<id>`, a memory not
    filed yet: it is forgotten as `forget` by its id, and the result says so, with the keys a delete returns and those of `forget`."""
    memory = Memory(lambda: wiki)
    wiki._stop_filer()  # the memory stays queued
    one(memory, "remember", {"text": f"topic: birds-1\n{SECRET}.", "title": "Falcon"})
    hit = one(memory, "search", {"query": "purple falcon"}).structured_content["result"][0]["path"]

    deleted = one(memory, "delete_note", {"path": hit, "confirm": True})

    body = deleted.structured_content
    assert hit.startswith("pending:") and not deleted.is_error
    assert body["forgot"] == hit and body["entries"] == 1 and body["pending"] == ["journal"]
    assert {"notes", "folders", "sources", "archived", "unlinked"} <= set(body) and body["notes"] == []
    assert one(memory, "search", {"query": "purple falcon"}).structured_content["result"] == []
    assert complete(wiki) and leftovers(wiki.cfg.bundle, SECRET) == []


def test_delete_note_finds_the_memory_by_its_raw_copy_after_the_note_moved(wiki: Wiki) -> None:
    memory = Memory(lambda: wiki)
    path = remembered_secret(wiki, memory)
    one(memory, "create_area", {"name": "aviary", "description": "Birds kept."})
    moved = one(memory, "move_note", {"path": path, "folder": "/aviary"}).structured_content["note"]

    one(memory, "delete_note", {"path": moved, "confirm": True})

    assert moved != path and gone_from_disk(wiki, SECRET)


def test_delete_note_does_not_wait_for_a_lease_another_process_holds(wiki: Wiki) -> None:
    """The journal's lease is another process's (its filer is on an entry): `delete_note` returns at once and says the journal is
    pending; that filer completes it at its next look."""
    memory = Memory(lambda: wiki)
    path = remembered_secret(wiki, memory)
    wiki._stop_filer()
    held, release = threading.Event(), threading.Event()

    def other_process() -> None:
        lease = FileLock(wiki.cfg.bundle / J.JOURNAL_DIR / "filer.lock")
        lease.acquire()
        held.set()
        release.wait(30)
        lease.release()

    th = threading.Thread(target=other_process)
    th.start()
    assert held.wait(10)
    t = time.monotonic()
    deleted = wiki.delete_note(path)
    took = time.monotonic() - t
    try:
        assert took < 2.0 and deleted.pending == ["journal"] and leftovers(wiki.cfg.bundle, SECRET)  # still on the journal's disk
    finally:
        release.set()
        th.join(30)
    wiki._filer_stopped = False
    wiki._start_filer()  # the filer that gets the lease completes the tombstone
    assert gone_from_disk(wiki, SECRET)


def test_a_run_of_deletes_shares_one_seal_and_leaves_no_byte(wiki: Wiki, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deletes of memories one after the other while the filer of this process holds the lease: each returns at once ("journal"), and
    the filer completes their tombstones together, with one seal (and one reuse of what is before it) for the run, not one each."""
    memory = Memory(lambda: wiki)
    birds = ["falcon", "heron", "osprey", "kestrel"]
    for bird in birds:
        one(memory, "remember", {"text": f"topic: birds-{bird}\nThe {bird} {SECRET}.", "title": bird.title()})
    wiki.drain(30)
    paths = [str(one(memory, "search", {"query": bird}).structured_content["result"][0]["path"]) for bird in birds]
    seals: list[int] = []
    real = J.Journal.seal

    def seal(self: J.Journal) -> int:
        seals.append(1)
        return real(self)

    monkeypatch.setattr(J.Journal, "seal", seal)

    for path in paths:
        assert wiki.delete_note(path).notes == [path]

    assert gone_from_disk(wiki, SECRET)  # B11 for each of them
    assert 1 <= len(seals) < len(paths)


def test_the_filer_completes_a_deletes_tombstone_with_the_journal_alone(wiki: Wiki, monkeypatch: pytest.MonkeyPatch) -> None:
    """The tombstone of a delete is of the journal alone: its section committed before it was written, so the filer that completes it
    zeroes no undo log and asks nothing of the index (the work, and the wait for the wiki's lock and the index, that the deletes of other
    processes would queue behind); what it leaves is in no file all the same."""
    memory = Memory(lambda: wiki)
    path = remembered_secret(wiki, memory)
    asked: list[str] = []
    real_wipe, real_forget = Wiki._wipe_undo, type(wiki._search).forget

    def wipe(self: Wiki) -> bool:
        asked.append("undo")
        return real_wipe(self)

    def forget(self: Any, *a: Any, **k: Any) -> Any:
        asked.append("index")
        return real_forget(self, *a, **k)

    monkeypatch.setattr(Wiki, "_wipe_undo", wipe)
    monkeypatch.setattr(type(wiki._search), "forget", forget)

    deleted = wiki.delete_note(path)

    assert deleted.pending == ["journal"]  # the filer of this process holds the lease
    assert gone_from_disk(wiki, SECRET)
    assert asked == []


class Died(BaseException):
    """The process dies: raised once the section that deleted has committed, before anything after it runs."""


@pytest.mark.parametrize("how", ["delete_note", "delete_folder", "clear"])
def test_a_process_that_dies_once_its_delete_committed_leaves_the_journal_to_the_next(
    wiki: Wiki, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    """A delete of a memory whose process dies right after its section commits: the tombstone that asks for the journal is durable
    already (written inside the section, as a purge writes its own), so the next process seals and reuses the journal, and no byte
    of the text is left; written after the commit, it would be lost with the process and the text would stay in the journal."""
    memory = Memory(lambda: wiki)
    path = remembered_secret(wiki, memory)
    wiki._stop_filer()
    real = WikiStore.lock

    @contextlib.contextmanager
    def lock(self: WikiStore, *a: Any, **k: Any) -> Iterator[None]:
        with real(self, *a, **k):
            yield
        raise Died  # the section committed; the process goes before the next line

    monkeypatch.setattr(WikiStore, "lock", lock)
    with pytest.raises(Died):
        if how == "delete_note":
            wiki.delete_note(path)
        elif how == "delete_folder":
            wiki.delete_folder("/" + path.rpartition("/")[0])
        else:
            wiki.clear()
    monkeypatch.setattr(WikiStore, "lock", real)
    assert not (wiki.cfg.bundle / path).exists() and leftovers(wiki.cfg.bundle, SECRET)  # deleted; the journal still holds it
    wiki.close()

    after, _ = make(wiki.cfg.bundle)  # the next process, whose filer takes the lease
    try:
        after._start_filer()
        assert gone_from_disk(after, SECRET)
    finally:
        after.close()


def test_delete_folder_and_clear_take_the_journals_copies_out_too(wiki: Wiki) -> None:
    memory = Memory(lambda: wiki)
    path = remembered_secret(wiki, memory)

    wiki.delete_folder("/" + path.rpartition("/")[0])
    assert gone_from_disk(wiki, SECRET)

    one(memory, "remember", {"text": f"topic: birds-2\n{SECRET} again."})
    wiki.drain(30)
    wiki.clear()
    assert gone_from_disk(wiki, SECRET)


def test_delete_note_of_a_note_the_journal_did_not_file_leaves_the_journal_alone(wiki: Wiki) -> None:
    """Only a raw copy that `remember` may have filed (an origin of `remember`, or none) costs the journal a seal and a reuse: deleting a
    note filed from a document with an origin writes nothing in it."""
    memory = Memory(lambda: wiki)
    one(memory, "remember", {"text": FACT})
    wiki.drain(30)
    other = wiki.ingest("topic: boats-1\nThe ferry leaves at noon.", title="Ferry", origin=Origin("local", "", "ferry.txt"))

    deleted = one(memory, "delete_note", {"path": other.note, "confirm": True}).structured_content

    assert deleted["notes"] == [other.note] and deleted["pending"] == []
    assert tombstones(wiki.cfg.bundle) == []


@pytest.mark.parametrize(
    ("arguments", "said"),
    [
        ({"key": KEY}, "confirm=true"),
        ({"key": KEY, "confirm": False}, "confirm=true"),
        ({"confirm": True}, "one of the two"),
        ({"key": KEY, "id": "abc", "confirm": True}, "one of the two"),
        ({"id": "nope", "confirm": True}, "forget it by its key"),
        ({"key": "nothing-here", "confirm": True}, "nothing is kept under the key nothing-here"),
    ],
)
def test_forget_refuses_what_it_cannot_do_and_changes_nothing(wiki: Wiki, arguments: dict[str, Any], said: str) -> None:
    # ARRANGE
    memory = Memory(lambda: wiki)
    one(memory, "remember", {"text": FACT, "key": KEY})
    wiki.drain(30)

    # ACT
    result = one(memory, "forget", arguments)

    # ASSERT
    assert result.is_error and said in text(result)
    assert len(notes(wiki.cfg.bundle)) == 1


def test_with_the_clients_models_remember_files_now_under_its_key_and_forget_takes_it_out(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    wiki = client_wiki(bundle, llm, classifier)
    memory = Memory(lambda: wiki)

    # ACT
    kept, _ = through(memory, "remember", {"text": "Revenue is booked on delivery.", "key": "rev-1"}, client)
    gone, _ = through(memory, "forget", {"key": "rev-1", "confirm": True}, client)

    # ASSERT
    assert kept.structured_content["status"] == "done" and kept.structured_content["action"] == "created"
    assert kept.structured_content["key"] == "rev-1"
    assert gone.structured_content["status"] == "done" and gone.structured_content["notes"] == [kept.structured_content["note"]]
    assert notes(bundle) == [] and leftovers(bundle, "Revenue is booked on delivery") == []


def test_with_the_clients_models_a_memory_given_no_key_is_forgotten_by_the_key_it_returns(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    memory = Memory(lambda: client_wiki(bundle, llm, classifier))
    kept, _ = through(memory, "remember", {"text": "Revenue is booked on delivery."}, client)

    # ACT
    gone, _ = through(memory, "forget", {"key": kept.structured_content["key"], "confirm": True}, client)

    # ASSERT
    assert kept.structured_content["key"].startswith("text:")
    assert gone.structured_content["status"] == "done" and gone.structured_content["notes"] == [kept.structured_content["note"]]
    assert notes(bundle) == [] and leftovers(bundle, "Revenue is booked on delivery") == []


# -- the server's end --------------------------------------------------------------------------------------------------


def test_close_files_what_the_server_queued(wiki: Wiki, monkeypatch: pytest.MonkeyPatch) -> None:
    # ARRANGE: no filer starts while the client is there
    memory = Memory(lambda: wiki)
    start = F.Hub.start_filer
    monkeypatch.setattr(F.Hub, "start_filer", lambda self, w: None)
    one(memory, "remember", {"text": FACT})
    assert wiki.status()["queue"] == 1
    monkeypatch.setattr(F.Hub, "start_filer", start)

    # ACT
    memory.close()

    # ASSERT
    assert wiki.status()["queue"] == 0 and len(notes(wiki.cfg.bundle)) == 1


def test_close_waits_no_longer_than_its_bound_and_what_is_left_stays_queued(wiki: Wiki) -> None:
    # ARRANGE
    memory = Memory(lambda: wiki)
    held = Held(wiki)
    try:
        one(memory, "remember", {"text": FACT})

        # ACT
        start = time.monotonic()
        memory.close()
        took = time.monotonic() - start
        left = wiki.status()["queue"]
    finally:
        held.release()

    # ASSERT
    assert took < CLOSE_DRAIN + 2.0 and left == 1
    wiki.drain(30)
    assert len(notes(wiki.cfg.bundle)) == 1  # nothing lost: filed once the wiki is free


def test_a_server_that_queued_nothing_closes_at_once_and_makes_no_journal(wiki: Wiki) -> None:
    memory = Memory(lambda: wiki)
    one(memory, "search", {"query": WORD})

    memory.close()

    assert not list((wiki.cfg.bundle / ".llmw2" / "journal").glob("*.wal"))


def test_the_http_server_closes_the_memory_at_its_end(wiki: Wiki, monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[Memory] = []
    monkeypatch.setattr(Memory, "close", lambda self, timeout=CLOSE_DRAIN: closed.append(self))

    with TestClient(create_app(wiki)):
        assert closed == []

    assert len(closed) == 1


# -- a server killed after the acknowledgement (A4) ---------------------------------------------------------------------

CHILD = """
import sys
from pathlib import Path
from llmw2 import Wiki, WikiConfig
from llmw2 import filer as F
from llmw2.memory import Memory
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM

F.Hub.start_filer = lambda self, wiki: None  # the server is killed before it files: the entry is in the journal alone
wiki = Wiki(WikiConfig(bundle=Path(sys.argv[1]), mode="classic", mcp=True), llm=ContentLLM(UsageTracker()))
wiki.init()
Memory(lambda: wiki, local_files=True).server.run("stdio")
"""


def test_an_entry_acknowledged_by_a_server_killed_before_it_files_is_filed_once_by_the_next_process(bundle: Path) -> None:
    # ARRANGE: the memory over stdio, in a process of its own
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}
    server = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(bundle)], cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert server.stdin is not None and server.stdout is not None

    def send(message: dict[str, Any]) -> None:
        server.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        server.stdin.flush()

    def answer(ident: int) -> dict[str, Any]:
        while line := server.stdout.readline():
            message = json.loads(line)
            if message.get("id") == ident:
                return message
        raise AssertionError(server.stderr.read().decode("utf-8", "replace") if server.stderr else "the server ended")

    try:
        send(INITIALIZE)
        answer(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "remember", "arguments": {"text": FACT}}})
        acked = answer(2)["result"]["structuredContent"]

        # ACT: killed after the acknowledgement, before any filing
        server.kill()
    finally:
        server.kill()
        server.wait(30)
    after, _ = make(bundle)
    try:
        after.drain(60)

        # ASSERT
        assert acked["state"] == "queued"
        filed = notes(bundle)
        assert len(filed) == 1 and WORD in filed[0].read_text(encoding="utf-8")
    finally:
        after.close()
