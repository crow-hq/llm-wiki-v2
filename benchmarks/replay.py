# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A replay cache of model answers for the benchmarks: the same request is asked of the model once, ever.

Every cacheable call that is not in the cache goes to the model and its answer is kept (the first one stays); a call
that is in it is answered from the file and costs nothing. The models are wrapped in place, the core is untouched.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
from collections.abc import Callable
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM

KINDS = ("llm", "classifier")


def digest(payload: dict[str, Any]) -> str:
    """The sha256 of the canonical JSON of `payload`."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class Replay:
    """The answers kept in `path`, for the kinds of model in `kinds` and the operations matching `ops` (None: all)."""

    def __init__(self, path: Path, kinds: set[str], ops: list[str] | None = None) -> None:
        self.path, self.kinds, self.ops = path, kinds, ops
        self.lock = threading.Lock()
        self.data: dict[str, dict[str, Any]] = {k: {} for k in KINDS}
        self.counts = self.empty()
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.data = {k: dict(saved.get(k) or {}) for k in KINDS}

    @staticmethod
    def empty() -> dict[str, dict[str, int]]:
        return {k: {"served": 0, "called": 0} for k in KINDS}

    def save(self) -> None:
        """Write the file (whole, through a temporary one, so an interrupted save leaves the old one)."""
        with self.lock:
            text = json.dumps(self.data, ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)

    def take(self) -> dict[str, dict[str, int]]:
        """The answers served from the cache and the calls made to the model since the last `take`, per kind."""
        with self.lock:
            counts, self.counts = self.counts, self.empty()
        return counts

    def _wants(self, kind: str, op: str) -> bool:
        return kind in self.kinds and (self.ops is None or any(fnmatchcase(op, pattern) for pattern in self.ops))

    def _through(self, kind: str, key: str, ask: Callable[[], Any]) -> Any:
        with self.lock:
            if key in self.data[kind]:
                self.counts[kind]["served"] += 1
                return copy.deepcopy(self.data[kind][key])
        answer = ask()  # an error is not kept
        with self.lock:
            self.counts[kind]["called"] += 1
            self.data[kind].setdefault(key, copy.deepcopy(answer))
        return answer

    def wrap(self, llm: LLM | None, classifier: Classifier | None) -> None:
        """Route the models' calls through the cache."""
        if llm is not None:
            chat = llm.chat

            # The key leaves max_tokens out: an uncut answer does not depend on it, and a cut raises (it is not cached), so older
            # caches stay valid. A cached reply longer than a cap set later is replayed as a success: a replay cannot show a cut.
            def cached_chat(messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
                if not self._wants("llm", op):
                    return chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)
                key = digest(
                    {"model": llm.model, "temperature": llm.temperature, "seed": llm.seed, "json_mode": json_mode, "messages": messages}
                )
                return str(self._through("llm", key, lambda: chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)))

            llm.chat = cached_chat  # type: ignore[method-assign]
        if classifier is not None:
            ask = classifier.ask

            def cached_ask(state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
                if not self._wants("classifier", op):
                    return ask(state, questions, op=op)
                key = digest({"model": classifier.model, "state": state, "questions": questions})
                return self._through("classifier", key, lambda: ask(state, questions, op=op))  # type: ignore[no-any-return]

            classifier.ask = cached_ask  # type: ignore[method-assign]
