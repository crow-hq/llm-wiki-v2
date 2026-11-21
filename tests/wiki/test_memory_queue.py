# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The MCP `remember` when the queue of entries to file is full: within about a second it answers NOT saved, as a result with the
keys a client of the release before reads, with the queue and when to try again, and writes nothing; `forget` is never held by it."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from mcp import Client

from llmw2 import Wiki
from llmw2 import filer as F
from llmw2.bundle import journal as J
from llmw2.bundle.filelock import FileLock
from llmw2.memory import Memory
from tests.wiki.test_memory_journal import FACT, KEY, SECRET, calls, one
from tests.wiki.test_remember import make
from tests.wiki.test_remember_queue import open_entries


@pytest.fixture
def full(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Wiki]:
    """A wiki whose queue holds one entry, with nobody filing."""
    monkeypatch.setattr(F, "QUEUE_LIMIT", 1)
    w, _ = make(bundle)
    w.cfg.mcp = True
    w._stop_filer()
    yield w
    w.close()


def test_a_full_queue_answers_not_saved_within_its_wait_with_the_keys_of_the_release_before(full: Wiki) -> None:
    memory = Memory(lambda: full)
    one(memory, "remember", {"text": FACT})

    t = time.monotonic()
    refused = one(memory, "remember", {"text": "topic: robots-2\nThe second robot stayed home."})
    took = time.monotonic() - t

    body = json.loads(json.dumps(refused.structured_content))
    assert refused.is_error and took <= 2.0  # an error of the tool: a client that judges by isError does not count it as saved
    assert "".join(getattr(c, "text", "") for c in refused.content).startswith("NOT saved")
    assert {k: body[k] for k in ("action", "note", "folder", "related")} == {"action": "not_saved", "note": "", "folder": "", "related": []}
    assert body["saved"] is False and body["message"].startswith("NOT saved") and "retry_after_s" in body
    assert (body["queue"], body["queue_limit"]) == (1, 1)
    assert len(open_entries(full.cfg.bundle)) == 1  # nothing written for it


def test_the_refusal_is_one_line_that_says_not_saved_and_what_to_tell_the_user(full: Wiki) -> None:
    memory = Memory(lambda: full)
    one(memory, "remember", {"text": FACT})

    refused = one(memory, "remember", {"text": "topic: robots-2\nThe second robot stayed home."})

    said = "".join(getattr(c, "text", "") for c in refused.content)
    assert said == refused.structured_content["message"] and "\n" not in said
    assert said.startswith("NOT saved") and "Tell the user now that it is not saved yet" in said
    assert "at most once more in this turn" in said and "try again" in said


def test_a_journal_busy_for_the_whole_wait_is_refused_with_its_true_reason(bundle: Path) -> None:
    """The queue has room, but other writers (or a seal of the journal) hold its admission for the whole wait: NOT saved, and the
    words say the memory was busy, never that it is still filing a queue, which a model reads as a full queue to report."""
    w, _ = make(bundle)
    w.cfg.mcp = True
    w._stop_filer()
    memory = Memory(lambda: w)
    held, release = threading.Event(), threading.Event()

    def other_writer() -> None:
        gate = FileLock(bundle / J.JOURNAL_DIR / "admit.lock")
        gate.acquire()
        held.set()
        release.wait(10)
        gate.release()

    th = threading.Thread(target=other_writer)
    try:
        one(memory, "remember", {"text": FACT})  # the journal is made; one entry waits
        th.start()
        assert held.wait(10)
        refused = one(memory, "remember", {"text": "topic: robots-2" + chr(10) + "The second robot stayed home."})
    finally:
        release.set()
        if th.is_alive():
            th.join(10)
        w.close()

    said = "".join(getattr(c, "text", "") for c in refused.content)
    assert refused.is_error and said.startswith("NOT saved") and "busy" in said and "still filing" not in said
    assert "Tell the user now that it is not saved yet" in said and "at most once more in this turn" in said
    assert refused.structured_content["queue"] == 1


def test_the_description_of_remember_says_to_reply_in_the_turn_after_a_refusal(full: Wiki) -> None:
    async def go() -> Any:
        async with Client(Memory(lambda: full).server) as client:
            return {t.name: t.description or "" for t in (await client.list_tools()).tools}

    described = asyncio.run(go())

    assert "NOT saved" in described["remember"] and "reply to the user in this turn" in described["remember"]
    assert "at most once more" in described["remember"] and "retry_after_s" in described["remember"]
    assert "nothing for you to start" in described["status"] and "drain" not in described["status"]


def test_status_over_mcp_names_no_call_a_client_cannot_make(full: Wiki) -> None:
    memory = Memory(lambda: full)
    one(memory, "remember", {"text": FACT})

    st = one(memory, "status", {}).structured_content

    assert st["filer"] == "none" and st["queue"] == 1
    assert not any("drain" in w for w in st["warnings"])
    assert any("filing resumes automatically" in w for w in st["warnings"])
    assert any("drain() starts one" in w for w in full.status()["warnings"])  # the library's own words stay, for its callers


def test_forget_takes_a_memory_out_while_the_queue_is_full(full: Wiki) -> None:
    memory = Memory(lambda: full)

    kept, refused, forgot, found = calls(
        memory,
        ("remember", {"text": f"topic: robots-1\n{SECRET}", "key": KEY}),
        ("remember", {"text": FACT}),
        ("forget", {"key": KEY, "confirm": True}),
        ("search", {"query": "purple falcon", "k": 8}),
    )

    assert kept.structured_content["saved"] is True and refused.is_error and refused.structured_content["action"] == "not_saved"
    assert not forgot.is_error and forgot.structured_content["entries"] == 1
    assert not found.structured_content["result"]
    again = one(memory, "remember", {"text": FACT})  # its place is free again
    assert again.structured_content["saved"] is True
