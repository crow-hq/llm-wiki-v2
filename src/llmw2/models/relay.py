# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Models whose calls the MCP client answers, so that its own plan does the memory's model work: no key, no other process.

When the memory's models are the client's (`mcp_models = "client"`), a remember or a recall runs the wiki's pipeline as
usual (CROW: the routing, the thresholds, the files), in a thread of its own. A model call there does not go out: it
waits in the work's `Relay`, and the MCP tool hands it to the client as a request. There are two kinds:
- `decide`: the System One questions with the typed-decision prompt (`models.typed`), for a light model, such as a
  Haiku subagent in Claude Code;
- `write`: the pipeline's own prompt, for the client's model.

Each reply lets the pipeline go on to its next call, until the work is done.
"""

from __future__ import annotations

import functools
import itertools
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from llmw2.config import ClassifierConfig, LLMConfig, WikiConfig
from llmw2.errors import ModelError
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM
from llmw2.models.typed import TYPED_DECISIONS, answers, decision_prompt
from llmw2.models.usage import UsageTracker

# Seconds a call waits for the client's reply before it fails, as a model that does not answer. While a remember files,
# it holds the wiki for writing: a client that went away keeps it this long at most (see `Relay.call`).
PATIENCE = 300.0
GATHER = 0.1  # seconds with no new call before the calls made together (the parts of a long document) go out together
GATHER_MAX = 1.0  # seconds a gathering lasts at most
CLIENT = "your MCP client"  # who answers, in the errors
# The client's models, as the work's config names them: in log.md and the decision hash, so a note filed by the client
# tells its provenance. A request holds about 12k tokens: a tool result that clients take whole, even counting it twice,
# as the SDK sends it (text and structured content; Claude Code takes 25k tokens from a tool by default).
LLM_CONFIG = LLMConfig(provider="custom", base_url="mcp://client", model="mcp-client", read_chars=40_000)
CLASSIFIER_CONFIG = ClassifierConfig(provider="custom", base_url="mcp://client", model="mcp-client", request_chars=40_000)

Kind = Literal["decide", "write"]


@dataclass(frozen=True)
class Request:
    """One model call waiting for the client's reply."""

    id: str
    kind: Kind
    step: str  # the pipeline's step: librarian/summarize, route, relate, researcher/answer, …
    system: str
    prompt: str


class Relay:
    """The model calls of one piece of work, handed to the client and answered by it.

    `run` starts the work in a thread. Its model calls wait in `call` until `answer` brings their replies, and `next`
    hands the tool those not handed out yet. A call left without a reply for `patience` seconds fails with ModelError,
    as a model that does not answer would, and so does every call after it, at once: the client is gone.
    """

    def __init__(self, patience: float = PATIENCE) -> None:
        self.patience = patience
        self.ended: float | None = None  # time.monotonic() when the work ended
        self.result: Any = None
        self.error: BaseException | None = None
        self._changed = threading.Condition()
        self._asked: dict[str, Request] = {}  # calls waiting for a reply
        self._handed: set[str] = set()  # those given to the client
        self._replies: dict[str, str] = {}
        self._ids = itertools.count(1)
        self._gone = False  # a call went without a reply

    @property
    def done(self) -> bool:
        return self.ended is not None

    def run(self, work: Callable[[], Any]) -> None:
        """Start `work` in a thread; what it returns, or raises, ends the relay."""

        def go() -> None:
            result, error = None, None
            try:
                result = work()
            except BaseException as e:  # whatever ends the work is the tool's to report: the job must end with it
                error = e
            with self._changed:
                self.result, self.error, self.ended = result, error, time.monotonic()
                self._changed.notify_all()

        threading.Thread(target=go, name="llmw2-relay", daemon=True).start()

    def call(self, kind: Kind, step: str, system: str, prompt: str) -> str:
        """From the pipeline: the client's reply to one model call."""
        with self._changed:
            if self._gone:  # CROW falls back to the LLM when a decision fails: that call must not wait as long again
                raise ModelError(f"{CLIENT} stopped replying: the {step} request was not sent")
            request = Request(f"r{next(self._ids)}", kind, step, system, prompt)
            self._asked[request.id] = request
            self._changed.notify_all()
            self._changed.wait_for(lambda: request.id in self._replies or self._gone, timeout=self.patience)
            del self._asked[request.id]
            if request.id in self._replies:
                return self._replies.pop(request.id)
            if self._gone:  # another call went without a reply meanwhile
                raise ModelError(f"{CLIENT} stopped replying: the {step} request went unanswered")
            self._gone = True
            self._changed.notify_all()  # the calls still waiting fail now too
            raise ModelError(f"{CLIENT} did not reply to the {step} request within {self.patience:.0f} s")

    def next(self, timeout: float) -> list[Request]:
        """From the tool: the calls not handed out yet, as soon as there are some.

        It returns at once, with nothing, when the work is done or a request already handed out waits for its reply.
        """
        with self._changed:
            self._changed.wait_for(lambda: self.done or bool(self._fresh() or self._awaited()), timeout=timeout)
            deadline = time.monotonic() + GATHER_MAX
            while self._fresh() and not self.done and time.monotonic() < deadline:  # the calls made together go out together
                asked = len(self._asked)
                self._changed.wait(GATHER)
                if len(self._asked) == asked:
                    break
            fresh = self._fresh()
            self._handed.update(r.id for r in fresh)
            return fresh

    def awaited(self) -> list[Request]:
        """The requests handed out that still wait for their reply."""
        with self._changed:
            return self._awaited()

    def answer(self, replies: Mapping[str, str]) -> list[str]:
        """From the tool: the client's replies, by request id. The ids that no call waits for are returned."""
        with self._changed:
            unknown = [rid for rid in replies if rid not in self._asked]
            self._replies.update((rid, str(text)) for rid, text in replies.items() if rid in self._asked)
            self._changed.notify_all()
            return unknown

    def _fresh(self) -> list[Request]:
        return [r for rid, r in self._asked.items() if rid not in self._handed]

    def _awaited(self) -> list[Request]:
        return [r for rid, r in self._asked.items() if rid in self._handed and rid not in self._replies]


def relayed(cfg: WikiConfig) -> WikiConfig:
    """`cfg` with the client's models, and always CROW: the client's light model decides, its own writes.

    The CROW thresholds stay the wiki's (Settings): its routing policy, whichever model decides. No preset is
    calibrated on a chat model's stated probabilities; the bench is the way to tune them.
    """
    return cfg.model_copy(update={"mode": "crow", "llm": LLM_CONFIG.model_copy(), "classifier": CLASSIFIER_CONFIG.model_copy()})


@functools.cache
def idle_http() -> httpx.Client:
    """The one HTTP client of the relayed models: their base classes want one, and the client's replies need none."""
    return httpx.Client()


class RelayLLM(LLM):
    """The writer whose calls the client answers with its own model."""

    def __init__(self, relay: Relay, usage: UsageTracker) -> None:
        super().__init__(LLM_CONFIG, usage, client=idle_http())
        self.relay = relay

    def chat(
        self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool | dict[str, Any] = False, max_tokens: int | None = None
    ) -> str:
        """The client's reply to `messages`. What the reply must be (one JSON object, about a length) is in the prompt."""
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        prompt = "\n\n".join(m["content"] for m in messages if m["role"] != "system")
        return self.relay.call("write", op or "write", system, prompt)


class RelayClassifier(Classifier):
    """The typed classifier whose decisions the client answers, with a light model of its own."""

    def __init__(self, relay: Relay, usage: UsageTracker) -> None:
        super().__init__(CLASSIFIER_CONFIG, usage, client=idle_http())
        self.relay = relay

    def ask(self, state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
        """The client's answers to the questions, read as System One's; an unusable reply falls to the LLM (ModelError)."""
        reply = self.relay.call("decide", op or "decide", TYPED_DECISIONS, decision_prompt(state, questions))
        try:
            return answers(reply, questions)
        except ValueError as e:
            raise ModelError(f"{CLIENT} replied to a decision without the expected answers: {e}") from e
