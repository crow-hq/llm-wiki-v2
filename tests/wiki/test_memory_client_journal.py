# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The MCP `remember` with the client's models (client and hybrid) on the journal: it answers once the entry is on the disk, with no
model call and the receipt of the wiki's own models, plus the server's filing `job`; the memory is found at once as `pending:<id>`;
the client files the queue later through `reply`, once per entry whatever the process; the job is there after a restart and with a
full queue; a client that answers badly or stops answering costs a retry, never the memory; `forget`, `delete_note` and `status` work
on what waits."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import asyncio
import functools
import json
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from mcp import Client

import llmw2.agents.librarian as librarian
import llmw2.memory as memory_module
from llmw2 import Wiki, WikiConfig
from llmw2 import filer as F
from llmw2.bundle import journal as J
from llmw2.errors import StepError
from llmw2.filer import PENDING
from llmw2.memory import Memory
from llmw2.models.relay import Relay
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_memory import INITIALIZE, NOTE, client, client_wiki, played, text
from tests.wiki.test_memory_journal import ROOT
from tests.wiki.test_remember import make, notes
from tests.wiki.test_remember_queue import open_entries

FIRST = "The supplier quillfeather pays at 45 days."
SECOND = "The courier larkspur delivers on Tuesdays."
RECEIPT = {"action", "note", "folder", "related", "saved", "id", "key", "state"}  # what `remember` gives with the wiki's models
ENV = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])}


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(F, "RETRY_FIRST", 0.05)
    monkeypatch.setattr(F, "IDLE_POLL", 0.01)


@pytest.fixture
def wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Iterator[Wiki]:
    """A classic wiki whose memory works with the client's models: the fakes are never asked."""
    w = client_wiki(bundle, llm, classifier)
    yield w
    w.close()


@pytest.fixture
def memory(wiki: Wiki) -> Iterator[Memory]:
    m = Memory(lambda: wiki)
    yield m
    m.close()


def respond(request: dict[str, Any]) -> str:
    """The tests' client, with any write but the folder's name answered by a note (a merge, a summary of another text)."""
    if request["kind"] == "decide" or request["step"] == "librarian/name_folder":
        return client(request)
    return json.dumps({"title": "Terms", "summary": "Terms of the suppliers.", "tags": [], "body": "Paid at 45 days."})


def bad(request: dict[str, Any]) -> str:
    """A client whose writes are not what was asked."""
    return client(request) if request["kind"] == "decide" else "not json"


def untitled(request: dict[str, Any]) -> str:
    """A client whose writes are JSON with an empty title: read, but not what their step needs."""
    if request["kind"] == "decide" or request["step"] == "librarian/name_folder":
        return client(request)
    return json.dumps({"title": "", "summary": "Terms.", "tags": [], "body": "Paid at 45 days."})


def session(memory: Memory, *steps: tuple[str, dict[str, Any]]) -> list[Any]:
    """The tools called one after the other by one client."""

    async def go() -> list[Any]:
        async with Client(memory.server) as s:
            return [await s.call_tool(name, arguments) for name, arguments in steps]

    return asyncio.run(go())


def file(memory: Memory, answer: Callable[[dict[str, Any]], str] = respond) -> tuple[Any, list[dict[str, Any]]]:
    """The filing job played to its end: its result (or error), and the requests it asked."""

    async def go() -> tuple[Any, list[dict[str, Any]]]:
        async with Client(memory.server) as s:
            return await played(s, await s.call_tool("reply", {"job": memory.job, "replies": {}}), answer)

    return asyncio.run(go())


def done_ids(bundle: Path) -> Counter[str]:
    """How many `D` records the journal holds for each entry."""
    return Counter(str(r.meta.get("id")) for r in J.Journal(bundle, writable=False).read().records if r.type == J.T_DONE)


# -- remember answers at once ----------------------------------------------------------------------------------------------


def test_remember_answers_at_once_with_the_receipt_of_the_wikis_models_and_no_model_call(
    memory: Memory, bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ACT
    start = time.monotonic()
    (kept,) = session(memory, ("remember", {"text": FIRST, "key": "terms-1"}))
    took = time.monotonic() - start

    # ASSERT: on disk, the same receipt as with the wiki's models, and the job that files it, not started
    body = kept.structured_content
    assert not kept.is_error and took < 1.0
    assert {k: body[k] for k in RECEIPT} == {
        "action": "queued", "note": "", "folder": "", "related": [], "saved": True, "id": body["id"], "key": "terms-1", "state": "queued",
    }
    assert (body["job"], body["status"], "requests" in body) == (memory.job, "to file", False)
    assert memory._run is None and (llm.ops, classifier.ops) == ([], [])  # nothing asked of the client yet
    assert len(open_entries(bundle)) == 1 and notes(bundle) == []
    assert memory.wiki().status()["filer"] == "none"  # no filer waits on a client that may never reply


def test_the_receipt_says_to_file_at_a_natural_pause_not_now(memory: Memory) -> None:
    (kept,) = session(memory, ("remember", {"text": FIRST}))

    how = kept.structured_content["how"]
    assert how.startswith("Saved") and "Do not file it now" in how and "natural pause" in how and "before you end that turn" in how
    assert "until it is done" not in how and "haiku" not in how  # the job's own `how` comes with its requests


def test_a_memory_kept_is_found_at_once_as_pending_and_status_names_the_job(memory: Memory) -> None:
    # ACT
    kept, found, st = session(memory, ("remember", {"text": FIRST}), ("search", {"query": "quillfeather"}), ("status", {}))
    ident = kept.structured_content["id"]
    (read,) = session(memory, ("read_note", {"path": PENDING + ident}))

    # ASSERT
    assert [h["path"] for h in found.structured_content["result"]] == [PENDING + ident]
    assert FIRST in text(read)
    body = st.structured_content
    assert (body["queue"], body["job"]) == (1, memory.job)
    assert any("natural pause" in w for w in body["warnings"])  # no warning says it files by itself


# -- the client files later ------------------------------------------------------------------------------------------------


def test_the_client_files_the_queue_later_through_its_replies(memory: Memory, bundle: Path) -> None:
    # ARRANGE
    session(memory, ("remember", {"text": FIRST}))

    # ACT
    done, asked = file(memory)
    (st, again) = session(memory, ("status", {}), ("reply", {"job": memory.job, "replies": {}}))

    # ASSERT
    assert asked and (done.structured_content["status"], done.structured_content["queue"], done.structured_content["failed"]) == (
        "done", 0, 0,
    )
    assert len(notes(bundle)) == 1 and open_entries(bundle) == []
    assert (st.structured_content["queue"], "job" in st.structured_content) == (0, False)
    assert again.structured_content["status"] == "done"  # nothing to file: done at once, and the result never expires
    (later,) = session(memory, ("remember", {"text": SECOND}))
    assert later.structured_content["job"] == memory.job  # one job for the life of the server


def test_memories_kept_while_the_job_files_are_filed_by_it(memory: Memory, bundle: Path) -> None:
    async def go() -> tuple[Any, Any, Any]:
        async with Client(memory.server) as s:
            first = await s.call_tool("remember", {"text": FIRST})
            started = await s.call_tool("reply", {"job": memory.job, "replies": {}})
            second = await s.call_tool("remember", {"text": SECOND})
            return first, second, (await played(s, started, respond))[0]

    # ACT
    first, second, done = asyncio.run(go())

    # ASSERT
    assert first.structured_content["job"] == second.structured_content["job"] == memory.job
    assert done.structured_content["queue"] == 0 and open_entries(bundle) == []


def test_two_replies_at_once_start_one_run(memory: Memory, bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # ARRANGE: the wikis the runs file with, counted
    made: list[int] = []

    class Counted(memory_module.ClientFiling):  # type: ignore[name-defined,misc]
        def __init__(self, cfg: WikiConfig, **kw: Any) -> None:
            super().__init__(cfg, **kw)
            made.append(1)

    monkeypatch.setattr(memory_module, "ClientFiling", Counted)
    session(memory, ("remember", {"text": FIRST}))

    async def go() -> list[Any]:
        async with Client(memory.server) as s:
            both = await asyncio.gather(*(s.call_tool("reply", {"job": memory.job, "replies": {}}) for _ in range(2)))
            return [await played(s, r, respond) for r in both]

    # ACT
    asyncio.run(go())

    # ASSERT
    assert made == [1] and open_entries(bundle) == [] and done_ids(bundle).most_common(1)[0][1] == 1


def test_hybrid_answers_at_once_too_and_hands_the_client_only_the_writing(
    bundle: Path, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARRANGE: a classifier ready in the settings (a key), so the wiki's own decides
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", mcp=True, mcp_models="hybrid", classifier={"api_key": "k"}), llm=llm)
    wiki.init()
    deciders: list[str] = []

    class Spy(memory_module.ClientFiling):  # type: ignore[name-defined,misc]
        def __init__(self, cfg: WikiConfig, **kw: Any) -> None:
            super().__init__(cfg, **kw)
            deciders.append(type(self.classifier).__name__)

    monkeypatch.setattr(memory_module, "ClientFiling", Spy)
    memory = Memory(lambda: wiki)
    try:
        # ACT
        start = time.monotonic()
        (kept,) = session(memory, ("remember", {"text": FIRST}))
        took = time.monotonic() - start
        done, asked = file(memory)
    finally:
        memory.close()
        wiki.close()

    # ASSERT
    assert took < 1.0 and set(kept.structured_content) >= RECEIPT | {"job"} and kept.structured_content["state"] == "queued"
    assert deciders == ["Classifier"]  # the filing decides with the settings' classifier
    assert asked and {r["kind"] for r in asked} == {"write"}
    assert done.structured_content["queue"] == 0 and llm.ops == []


def test_the_first_recall_after_filing_on_an_index_never_built_answers_from_the_note(memory: Memory, wiki: Wiki) -> None:
    # ARRANGE: a wiki whose search index was never built: the first search builds it, and the memory may show as pending too
    session(memory, ("remember", {"text": "Revenue is booked on delivery."}))
    file(memory, client)

    # ACT
    async def go() -> Any:
        async with Client(memory.server) as s:
            return (await played(s, await s.call_tool("recall", {"question": "When is revenue booked?"}), client))[0]

    found = asyncio.run(go())

    # ASSERT
    assert found.structured_content["notes"] == [NOTE] and NOTE in found.structured_content["answer"]


# -- nothing is lost ------------------------------------------------------------------------------------------------------


def test_after_a_restart_status_gives_the_job_and_it_files_what_the_server_before_kept(wiki: Wiki, bundle: Path) -> None:
    # ARRANGE: a server kept a memory and ended before its client filed it
    before = Memory(lambda: wiki)
    session(before, ("remember", {"text": FIRST}))
    before.close()
    after = Memory(lambda: wiki)
    try:
        # ACT
        (st,) = session(after, ("status", {}))
        done, _ = file(after)
    finally:
        after.close()

    # ASSERT
    assert (st.structured_content["queue"], st.structured_content["job"]) == (1, after.job)
    assert done.structured_content["queue"] == 0 and open_entries(bundle) == [] and len(notes(bundle)) == 1


def test_a_full_queue_refuses_with_the_job_that_empties_it(memory: Memory, bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # ARRANGE
    monkeypatch.setattr(F, "QUEUE_LIMIT", 1)
    session(memory, ("remember", {"text": FIRST}))

    # ACT
    start = time.monotonic()
    (refused,) = session(memory, ("remember", {"text": SECOND}))
    took = time.monotonic() - start
    full = len(open_entries(bundle))
    done, _ = file(memory)
    (kept,) = session(memory, ("remember", {"text": SECOND}))

    # ASSERT: refused as with the wiki's models, with the job and what to do
    body = refused.structured_content
    assert refused.is_error and took <= 2.0 and text(refused).startswith("NOT saved") and "call reply with `job`" in text(refused)
    assert {k: body[k] for k in ("action", "note", "folder", "related", "saved")} == {
        "action": "not_saved", "note": "", "folder": "", "related": [], "saved": False,
    }
    assert (body["queue"], body["queue_limit"], body["job"]) == (1, 1, memory.job) and full == 1  # nothing written for it
    assert done.structured_content["queue"] == 0 and kept.structured_content["saved"] is True


def test_replies_that_are_not_what_was_asked_cost_a_retry_never_the_memory(memory: Memory, wiki: Wiki, bundle: Path) -> None:
    # ACT: three runs whose writes are not JSON, then one that answers well
    session(memory, ("remember", {"text": FIRST}))
    failed = [file(memory, bad)[0] for _ in range(3)]
    after = wiki.status()
    done, _ = file(memory)

    # ASSERT: each run ended with the error, the memory queued and not set aside, then filed
    assert all(r.is_error and "file them again" in text(r) for r in failed)
    assert (after["queue"], after["failed"]) == (1, 0)
    assert done.structured_content["queue"] == 0 and len(notes(bundle)) == 1


def test_writes_without_what_their_step_needs_cost_a_retry_never_the_memory(memory: Memory, wiki: Wiki, bundle: Path) -> None:
    # ACT: three runs whose writes have an empty title, then one that answers well
    session(memory, ("remember", {"text": FIRST}))
    failed = [file(memory, untitled)[0] for _ in range(3)]
    after = wiki.status()
    done, _ = file(memory)

    # ASSERT
    assert all(r.is_error and "the title is empty" in text(r) for r in failed)
    assert (after["queue"], after["failed"]) == (1, 0)
    assert done.structured_content["queue"] == 0 and len(notes(bundle)) == 1 and memory._invalid == Counter()


def test_the_invalid_replies_of_a_memory_no_longer_queued_are_not_kept(memory: Memory, bundle: Path) -> None:
    # ARRANGE: a memory whose writes were not what was asked, then forgotten
    (kept,) = session(memory, ("remember", {"text": FIRST}))
    file(memory, bad)
    counted = dict(memory._invalid)

    async def forget() -> None:
        async with Client(memory.server) as s:
            await played(s, await s.call_tool("forget", {"id": kept.structured_content["id"], "confirm": True}), respond)

    asyncio.run(forget())

    # ACT: another memory, filed by the next run
    session(memory, ("remember", {"text": SECOND}))
    done, _ = file(memory)

    # ASSERT: the count of the forgotten one went with it
    assert counted == {kept.structured_content["id"]: 1}
    assert done.structured_content["queue"] == 0 and len(notes(bundle)) == 1 and memory._invalid == Counter()


def test_a_step_error_of_the_wikis_side_in_hybrid_counts_as_the_memorys_attempt(
    bundle: Path, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARRANGE: hybrid with the wiki's classifier, and a routing that fails on the wiki's side
    def broken(*_: Any) -> Any:
        raise StepError("route", "no top-level folder is open to automatic filing")

    monkeypatch.setattr(librarian, "check_route", broken)
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", mcp=True, mcp_models="hybrid", classifier={"api_key": "k"}), llm=llm)
    wiki.init()
    memory = Memory(lambda: wiki)
    try:
        # ACT
        session(memory, ("remember", {"text": FIRST}))
        failed = [file(memory)[0] for _ in range(3)]
        after = wiki.status()
    finally:
        memory.close()
        wiki.close()

    # ASSERT: not the client's failure, so three of them set the memory aside, as with any model
    assert all(r.is_error for r in failed) and (after["queue"], after["failed"]) == (0, 1)


def test_a_late_reply_to_a_run_that_stopped_is_unknown_to_the_next_one(memory: Memory, bundle: Path) -> None:
    # ARRANGE: three memories; a run asks the client, then a forget of the first stops it, and a new run asks again
    third = "The warehouse marigold closes at noon on Fridays."
    stale = json.dumps({"title": "Quillfeather", "summary": "Quillfeather pays at 45 days.", "tags": [], "body": "QUILLFEATHER-STALE"})

    async def go() -> tuple[list[str], list[str], Any, Any]:
        async with Client(memory.server) as s:
            ids = [(await s.call_tool("remember", {"text": t})).structured_content["id"] for t in (FIRST, SECOND, third)]
            before = [r["id"] for r in (await s.call_tool("reply", {"job": memory.job, "replies": {}})).structured_content["requests"]]
            await played(s, await s.call_tool("forget", {"id": ids[0], "confirm": True}), respond)
            now = [r["id"] for r in (await s.call_tool("reply", {"job": memory.job, "replies": {}})).structured_content["requests"]]
            # ACT: the client's replies to the stopped run come late, written for the memory forgotten
            late = await s.call_tool("reply", {"job": memory.job, "replies": dict.fromkeys(before, stale)})
            return before, now, late, (await played(s, await s.call_tool("reply", {"job": memory.job, "replies": {}}), respond))[0]

    before, now, late, done = asyncio.run(go())

    # ASSERT: none of them is taken, and no note holds the text written for the memory forgotten
    assert before and now and not set(before) & set(now)
    assert sorted(late.structured_content["unknown"]) == sorted(before)
    assert done.structured_content["queue"] == 0 and open_entries(bundle) == []
    assert all("QUILLFEATHER-STALE" not in n.read_text(encoding="utf-8") for n in notes(bundle))


def test_an_error_nobody_read_is_not_said_once_a_forget_emptied_the_queue(
    memory: Memory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARRANGE: a run that ended with an error (no reply within 0.3 s) that no reply read
    monkeypatch.setattr(memory_module, "Relay", functools.partial(Relay, patience=0.3))
    (kept,) = session(memory, ("remember", {"text": FIRST}))
    session(memory, ("reply", {"job": memory.job, "replies": {}}))
    time.sleep(1.0)
    monkeypatch.setattr(memory_module, "Relay", Relay)

    async def go() -> Any:
        async with Client(memory.server) as s:
            return (await played(s, await s.call_tool("forget", {"id": kept.structured_content["id"], "confirm": True}), respond))[0]

    # ACT
    gone = asyncio.run(go())
    (after,) = session(memory, ("reply", {"job": memory.job, "replies": {}}))

    # ASSERT
    assert not gone.is_error and not after.is_error and after.structured_content["status"] == "done"


def test_a_client_that_stops_replying_ends_the_run_and_the_next_files_the_memory(
    memory: Memory, wiki: Wiki, bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARRANGE: a client that waits 0.5 s at most
    monkeypatch.setattr(memory_module, "Relay", functools.partial(Relay, patience=0.5))
    session(memory, ("remember", {"text": FIRST}))

    # ACT: the requests handed out, never answered
    (started,) = session(memory, ("reply", {"job": memory.job, "replies": {}}))
    time.sleep(1.0)
    (ended,) = session(memory, ("reply", {"job": memory.job, "replies": {}}))
    after = wiki.status()
    monkeypatch.setattr(memory_module, "Relay", Relay)
    done, _ = file(memory)

    # ASSERT
    assert started.structured_content["requests"] and ended.is_error and "did not reply" in text(ended)
    assert (after["queue"], after["failed"]) == (1, 0)
    assert done.structured_content["queue"] == 0 and len(notes(bundle)) == 1


@pytest.mark.parametrize("how", ["forget", "delete_note"])
@pytest.mark.parametrize("started", [False, True])
def test_a_memory_still_waiting_is_forgotten_by_its_id_at_once(memory: Memory, bundle: Path, how: str, started: bool) -> None:
    # ARRANGE: kept; with `started` the filing asked the client and waits for its reply
    (kept,) = session(memory, ("remember", {"text": FIRST}))
    if started:
        (asked,) = session(memory, ("reply", {"job": memory.job, "replies": {}}))
        assert asked.structured_content["requests"]
    ident = kept.structured_content["id"]
    arguments = {"id": ident, "confirm": True} if how == "forget" else {"path": PENDING + ident, "confirm": True}

    # ACT: the run of the job, if any, stops first: the purge does not wait for the client
    start = time.monotonic()

    async def go() -> list[Any]:
        async with Client(memory.server) as s:
            gone = (await played(s, await s.call_tool(how, arguments), respond))[0]
            return [gone, await s.call_tool("search", {"query": "quillfeather"}), await s.call_tool("status", {})]

    gone, found, st = asyncio.run(go())
    took = time.monotonic() - start
    after, _ = file(memory)

    # ASSERT
    assert not gone.is_error and took < 10.0 < F.FORGET_WAIT
    assert found.structured_content["result"] == [] and st.structured_content["queue"] == 0
    assert open_entries(bundle) == [] and notes(bundle) == []
    assert after.structured_content["status"] == "done"  # the run stopped for the purge leaves no error to read


def test_a_server_whose_client_stopped_replying_ends_at_once_and_leaves_the_memory_queued(memory: Memory, bundle: Path) -> None:
    # ARRANGE: the filing started, and its first request is never answered
    session(memory, ("remember", {"text": FIRST}))
    (started,) = session(memory, ("reply", {"job": memory.job, "replies": {}}))
    assert started.structured_content["requests"]

    # ACT
    start = time.monotonic()
    memory.close()
    took = time.monotonic() - start

    # ASSERT
    assert took < 5.0
    assert len(open_entries(bundle)) == 1 and notes(bundle) == []


# -- other processes ----------------------------------------------------------------------------------------------------

SERVER = """
import sys
from pathlib import Path
import llmw2.memory as M
from llmw2 import Wiki, WikiConfig
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeClassifier, FakeLLM

M.WAIT = 1.0  # a reply waits 1 s for requests, so that a test plays two servers in turn
tracker = UsageTracker()
cfg = WikiConfig(bundle=Path(sys.argv[1]), mode="classic", mcp=True, mcp_models="client")
wiki = Wiki(cfg, llm=FakeLLM(tracker), classifier=FakeClassifier(tracker))
wiki.init()
memory = M.Memory(lambda: wiki, local_files=True)
try:
    memory.server.run("stdio")
finally:
    memory.close()
"""

FILER = """
import sys, time
from pathlib import Path
from tests.wiki.test_remember import make

wiki, _ = make(Path(sys.argv[1]))  # the wiki's own models
wiki._start_filer()  # it takes the journal's lease and files what comes, whoever wrote it
time.sleep(1.0)
print("ready", flush=True)
sys.stdin.readline()
wiki.close()
"""


class Stdio:
    """A process of its own that speaks MCP over stdio, driven line by line."""

    def __init__(self, script: str, bundle: Path) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-c", script, str(bundle)], cwd=ROOT, env=ENV, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.ids = 1

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def answer(self, ident: int) -> dict[str, Any]:
        assert self.proc.stdout is not None
        while line := self.proc.stdout.readline():
            message = json.loads(line)
            if message.get("id") == ident:
                return message
        raise AssertionError("the server ended")

    def start(self) -> Stdio:
        self.send(INITIALIZE)
        self.answer(1)
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.ids += 1
        self.send({"jsonrpc": "2.0", "id": self.ids, "method": "tools/call", "params": {"name": tool, "arguments": arguments}})
        result = self.answer(self.ids)["result"]
        assert not result.get("isError"), result
        return dict(result["structuredContent"])

    def end(self) -> None:
        self.proc.kill()
        self.proc.wait(30)


def test_two_client_servers_on_one_wiki_file_each_memory_once(bundle: Path) -> None:
    # ARRANGE: two servers, each with a memory, each with its filing started
    servers = [Stdio(SERVER, bundle).start() for _ in range(2)]
    try:
        jobs = [s.call("remember", {"text": t})["job"] for s, t in zip(servers, (FIRST, SECOND), strict=True)]
        bodies = [s.call("reply", {"job": j, "replies": {}}) for s, j in zip(servers, jobs, strict=True)]

        # ACT: both played in turn until both are done
        deadline = time.monotonic() + 120
        while any(b["status"] != "done" for b in bodies) and time.monotonic() < deadline:
            for i, (s, j) in enumerate(zip(servers, jobs, strict=True)):
                if bodies[i]["status"] != "done":
                    replies = {r["id"]: respond(r) for r in bodies[i].get("requests", [])}
                    bodies[i] = s.call("reply", {"job": j, "replies": replies})
    finally:
        for s in servers:
            s.end()

    # ASSERT: one `D` per entry, nothing left
    assert [b["status"] for b in bodies] == ["done", "done"]
    assert open_entries(bundle) == [] and len(done_ids(bundle)) == 2 and set(done_ids(bundle).values()) == {1}


def test_a_memory_filed_by_a_process_with_the_wikis_own_models_ends_the_job_with_no_request(bundle: Path) -> None:
    # ARRANGE: another process holds the journal's lease and files with the wiki's models
    make(bundle)[0].close()
    filer = subprocess.Popen([sys.executable, "-c", FILER, str(bundle)], cwd=ROOT, env=ENV, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert filer.stdout is not None and filer.stdin is not None
    try:
        assert filer.stdout.readline().strip() == b"ready"
        t = UsageTracker()  # the fakes are never asked
        wiki = client_wiki(bundle, FakeLLM(t), FakeClassifier(t))
        memory = Memory(lambda: wiki)
        try:
            # ACT
            session(memory, ("remember", {"text": FIRST}))
            done, asked = file(memory)
        finally:
            memory.close()
            wiki.close()
    finally:
        filer.stdin.write(b"\n")
        filer.stdin.flush()
        filer.wait(30)

    # ASSERT
    assert done.structured_content["status"] == "done" and asked == []
    assert open_entries(bundle) == [] and len(notes(bundle)) == 1
