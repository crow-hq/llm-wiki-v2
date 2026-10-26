# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The relay: a piece of work whose model calls the MCP client answers, handed out as requests and answered back."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from llmw2.config import WikiConfig
from llmw2.errors import ModelError
from llmw2.models.relay import Relay, RelayClassifier, RelayLLM, relayed
from llmw2.models.typed import TYPED_DECISIONS
from llmw2.models.usage import UsageTracker

TIMEOUT = 5.0  # seconds a test waits for the work's thread before calling it a bug


def test_a_call_waits_for_the_clients_reply_and_the_work_goes_on_with_it() -> None:
    # ARRANGE
    relay = Relay()
    relay.run(lambda: relay.call("write", "librarian/summarize", "You write notes.", "Summarize this.") + "!")

    # ACT
    (request,) = relay.next(TIMEOUT)
    relay.answer({request.id: "A note"})
    left = relay.next(TIMEOUT)

    # ASSERT
    assert (request.kind, request.step) == ("write", "librarian/summarize")
    assert (request.system, request.prompt) == ("You write notes.", "Summarize this.")
    assert (left, relay.done, relay.result) == ([], True, "A note!")


def test_the_calls_made_together_are_handed_out_together(monkeypatch: pytest.MonkeyPatch) -> None:
    # ARRANGE: three parts of a long document, summarized at once (the gathering waits longer here, for a slow machine)
    monkeypatch.setattr("llmw2.models.relay.GATHER", 0.5)
    relay = Relay()
    replies: list[str] = []

    def work() -> list[str]:
        threads = [threading.Thread(target=lambda i=i: replies.append(relay.call("write", f"part {i}", "", ""))) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return sorted(replies)

    relay.run(work)

    # ACT
    requests = relay.next(TIMEOUT)
    relay.answer({r.id: r.step for r in requests})
    relay.next(TIMEOUT)

    # ASSERT
    assert sorted(r.step for r in requests) == ["part 0", "part 1", "part 2"]
    assert relay.result == ["part 0", "part 1", "part 2"]


def test_a_request_handed_out_and_not_answered_is_awaited_not_waited_for() -> None:
    # ARRANGE
    relay = Relay()
    relay.run(lambda: relay.call("decide", "route", "", ""))
    (request,) = relay.next(TIMEOUT)

    # ACT
    start = time.perf_counter()
    again = relay.next(TIMEOUT)

    # ASSERT
    assert (again, relay.awaited()) == ([], [request]) and time.perf_counter() - start < 1
    relay.answer({request.id: "{}"})


def test_reply_ids_that_no_call_waits_for_are_given_back() -> None:
    relay = Relay()
    relay.run(lambda: relay.call("write", "s", "", ""))
    (request,) = relay.next(TIMEOUT)

    assert relay.answer({"r99": "lost", request.id: "ok"}) == ["r99"]


def test_a_call_without_a_reply_fails_as_a_model_that_does_not_answer() -> None:
    relay = Relay(patience=0.2)
    relay.run(lambda: relay.call("write", "librarian/summarize", "", ""))

    relay.next(TIMEOUT)
    unanswered(relay)

    assert relay.done and isinstance(relay.error, ModelError) and "did not reply to the librarian/summarize request" in str(relay.error)


def test_once_a_call_goes_without_a_reply_the_next_ones_fail_at_once() -> None:
    # ARRANGE: a decision nobody answers, then the LLM's call CROW falls back to
    relay = Relay(patience=1.0)

    def work() -> float:
        with pytest.raises(ModelError):
            relay.call("decide", "route", "", "")
        start = time.perf_counter()
        with pytest.raises(ModelError, match="stopped replying: the librarian/route request was not sent"):
            relay.call("write", "librarian/route", "", "")
        return time.perf_counter() - start

    relay.run(work)

    # ACT
    relay.next(TIMEOUT)
    unanswered(relay)

    # ASSERT
    assert relay.error is None and relay.result < 0.5


def test_when_a_call_goes_without_a_reply_the_ones_still_waiting_fail_with_it() -> None:
    # ARRANGE: two decisions asked 0.6 s apart, neither answered
    relay = Relay(patience=1.0)
    ended: dict[str, tuple[float, str]] = {}  # when each call failed, and why

    def ask(step: str) -> None:
        try:
            relay.call("decide", step, "", "")
        except ModelError as e:
            ended[step] = (time.perf_counter(), str(e))

    def work() -> None:
        first = threading.Thread(target=ask, args=("first",))
        first.start()
        time.sleep(0.6)
        ask("second")  # alone, it would wait until 1.6 s
        first.join()

    start = time.perf_counter()
    relay.run(work)

    # ACT
    relay.next(TIMEOUT)
    unanswered(relay)

    # ASSERT: the second ends with the first, not a patience later
    assert "did not reply to the first request" in ended["first"][1]
    assert "stopped replying" in ended["second"][1] and ended["second"][0] - start < 1.4


def test_a_work_ended_by_any_exception_ends_its_job() -> None:
    relay = Relay()

    def work() -> None:
        raise SystemExit("stopped")

    relay.run(work)
    relay.next(TIMEOUT)

    assert relay.done and isinstance(relay.error, SystemExit)


def test_the_work_runs_crow_with_the_clients_models_named_in_its_provenance(tmp_path: Path) -> None:
    cfg = relayed(WikiConfig(bundle=tmp_path, mode="classic"))

    assert (cfg.mode, cfg.llm.model, cfg.classifier.model, cfg.effective_read_chars) == ("crow", "mcp-client", "mcp-client", 40_000)


def test_the_writer_relays_the_system_messages_and_the_user_turn() -> None:
    # ARRANGE
    relay = Relay()
    llm = RelayLLM(relay, UsageTracker())
    relay.run(lambda: llm.json("You write notes.", "Summarize this.", dict_schema(), op="librarian/summarize"))

    # ACT
    (request,) = relay.next(TIMEOUT)
    relay.answer({request.id: '```json\n{"title": "A"}\n```'})
    relay.next(TIMEOUT)

    # ASSERT
    assert (request.kind, request.step, request.system) == ("write", "librarian/summarize", "You write notes.")
    assert request.prompt.startswith("Summarize this.") and relay.result.title == "A"


def test_the_classifier_reads_the_clients_decision_as_system_one_answers() -> None:
    # ARRANGE
    relay = Relay()
    classifier = RelayClassifier(relay, UsageTracker())
    relay.run(lambda: classifier.choice("Title: Refund", "Which folder?", {"billing": "payments", "sales": "pricing"}, op="route"))

    # ACT
    (request,) = relay.next(TIMEOUT)
    relay.answer({request.id: '{"answers": {"q": {"billing": 0.8, "sales": 0.2}}}'})
    relay.next(TIMEOUT)

    # ASSERT
    assert (request.kind, request.step, request.system) == ("decide", "route", TYPED_DECISIONS)
    assert (relay.result.choice, relay.result.probabilities) == ("billing", {"billing": 0.8, "sales": 0.2})


def test_an_unusable_decision_is_a_model_error_so_crow_falls_back_to_the_llm() -> None:
    relay = Relay()
    classifier = RelayClassifier(relay, UsageTracker())
    relay.run(lambda: classifier.nouls("Title: Refund", {"n0": "About payments."}, op="match"))

    (request,) = relay.next(TIMEOUT)
    relay.answer({request.id: "Payments, surely."})
    relay.next(TIMEOUT)

    assert isinstance(relay.error, ModelError) and "without the expected answers" in str(relay.error)


def unanswered(relay: Relay) -> None:
    """Wait for the end of a work whose calls nobody answers: each gives up after its patience."""
    deadline = time.monotonic() + TIMEOUT
    while not relay.done and time.monotonic() < deadline:
        time.sleep(0.05)


def dict_schema() -> type:
    from pydantic import BaseModel

    class Draft(BaseModel):
        title: str

    return Draft


@pytest.fixture(autouse=True)
def no_stray_threads() -> Iterator[None]:
    """Every relay's work ends within its test: none is left waiting for a reply."""
    before = {t for t in threading.enumerate() if t.name == "llmw2-relay"}
    yield
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline and {t for t in threading.enumerate() if t.name == "llmw2-relay"} - before:
        time.sleep(0.01)
    assert not {t for t in threading.enumerate() if t.name == "llmw2-relay"} - before, "a relay's work outlived its test"
