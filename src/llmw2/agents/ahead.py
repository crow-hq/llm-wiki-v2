# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Model answers asked ahead of a filing, for the exact requests the filing will make.

While the filer files an entry, the route of the next one (`Librarian.rehearse`: route, its LLM fallback, match) is worked out on
the tree that entry's section read, and every request it makes is kept here with its answer (`Answers`). Only the answers are kept:
what the rehearsal decides is dropped, and a tree that changes under it (the section writes, the next `load` of its session) can only
make its requests differ from the section's. When the next
entry's section asks the same request, byte for byte, it takes that answer instead of asking again; a request the section makes
differently (the section before changed a folder, or the notes the match reads) is asked as it would have been. So the section
decides on the very requests it would have made without this; each answer is taken once, so a request asked twice (a vote) gets two
answers, as from the model. A request still on its way when the section asks it is waited for, not asked twice.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import concurrent.futures
import threading
from collections import deque
from typing import Any

Key = tuple[Any, ...]


class Late(Exception):
    """The section has started: a request not asked yet is no longer asked ahead."""


class Answers:
    """The answers asked ahead for one entry, by request. `claim` (the section starts) stops what was not asked yet."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by: dict[Key, deque[concurrent.futures.Future[Any]]] = {}
        self.claimed = False
        self.asked = 0  # requests made ahead
        self.taken = 0  # ... and taken by the section

    def claim(self) -> None:
        with self._lock:
            self.claimed = True

    def ask(self, key: Key, call: Any) -> Any:
        """Ahead: make the request (`call()`) and keep its answer, or its error, under `key`."""
        done: concurrent.futures.Future[Any] = concurrent.futures.Future()
        with self._lock:
            if self.claimed:
                raise Late()
            self._by.setdefault(key, deque()).append(done)
            self.asked += 1
        try:
            out = call()
        except BaseException as e:
            done.set_exception(e)
            raise
        done.set_result(out)
        return out

    def take(self, key: Key, call: Any) -> Any:
        """In the section: the answer kept for this very request, waited for if it is on its way; else the request is made now. An
        answer that came as an error is not reused: the request is made again."""
        with self._lock:
            queue = self._by.get(key)
            done = queue.popleft() if queue else None
        if done is not None:
            try:
                out = done.result()
            except Exception:
                return call()
            with self._lock:
                self.taken += 1
            return out
        return call()


class AheadClassifier:
    """The classifier as a step sees it, its `choice` and `nouls` going through `answers` (ahead, or in the section)."""

    def __init__(self, inner: Any, answers: Answers, *, ahead: bool) -> None:
        self._inner = inner
        self._answers = answers
        self._ahead = ahead

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _through(self, key: Key, call: Any) -> Any:
        return self._answers.ask(key, call) if self._ahead else self._answers.take(key, call)

    def choice(self, state: str, instructions: str, options: dict[str, str], *, op: str = "") -> Any:
        key = ("choice", op, state, instructions, tuple(options.items()))
        return self._through(key, lambda: self._inner.choice(state, instructions, options, op=op))

    def nouls(self, state: str, questions: dict[str, str], *, op: str = "") -> dict[str, float]:
        key = ("nouls", op, state, tuple(questions.items()))
        return dict(self._through(key, lambda: self._inner.nouls(state, questions, op=op)))


class AheadLLM:
    """The writer as a step sees it, its JSON requests going through `answers` (the route's fallback, the one the route asks)."""

    def __init__(self, inner: Any, answers: Answers, *, ahead: bool) -> None:
        self._inner = inner
        self._answers = answers
        self._ahead = ahead

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def json(self, system: str, user: str, schema: Any, *, op: str = "", max_tokens: int | None = None) -> Any:
        key = ("json", op, system, user, f"{schema.__module__}.{schema.__qualname__}", max_tokens)

        def call() -> Any:
            return self._inner.json(system, user, schema, op=op, max_tokens=max_tokens)

        out = self._answers.ask(key, call) if self._ahead else self._answers.take(key, call)
        return out.model_copy(deep=True) if hasattr(out, "model_copy") else out
