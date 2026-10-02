# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Scripted stand-ins for the two model clients.

They subclass the real clients and replace only the network call, so JSON
parsing, retries on invalid JSON, batching and usage accounting all run for real.
"""

from __future__ import annotations

import json
import re
import time
import zlib
from collections.abc import Callable
from typing import Any

from llmw2.config import ClassifierConfig, LLMConfig
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageTracker

LLM_TOKENS = (100, 10)
CLASSIFIER_TOKENS = (50, 0)


class FakeLLM(LLM):
    """Replies are queued per op (the prompt name, e.g. "librarian/route"); dicts are sent as JSON."""

    def __init__(self, usage: UsageTracker, replies: dict[str, list[Any]] | None = None) -> None:
        super().__init__(LLMConfig(model="fake/llm"), usage)
        self.replies: dict[str, list[Any]] = {op: list(r) for op, r in (replies or {}).items()}
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    def add(self, op: str, *replies: Any) -> FakeLLM:
        self.replies.setdefault(op, []).extend(replies)
        return self

    def chat(self, messages: list[dict[str, str]], *, op: str = "") -> str:
        self.calls.append((op, [dict(m) for m in messages]))
        self.usage.record("llm", self.model, *LLM_TOKENS, op=op)
        if not self.replies.get(op):
            raise AssertionError(f"FakeLLM: no scripted reply left for {op!r}")
        reply = self.replies[op].pop(0)
        return reply if isinstance(reply, str) else json.dumps(reply)

    @property
    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]


Noul = dict[str, float] | Callable[[str], float]


class FakeClassifier(Classifier):
    """Scripted typed answers.

    `choices[op]` is a queue of (choice, probabilities, confidence).
    `nouls[op]` maps a substring of the question (e.g. a note title) to a probability,
    first match wins, default 0.0; or is a function of the question text.
    """

    def __init__(
        self,
        usage: UsageTracker,
        *,
        choices: dict[str, list[tuple[str, dict[str, float], float]]] | None = None,
        nouls: dict[str, Noul] | None = None,
    ) -> None:
        super().__init__(ClassifierConfig(model="fake/jev", api_key="fake-key"), usage)
        self.choices = {op: list(c) for op, c in (choices or {}).items()}
        self.noul_scores: dict[str, Noul] = dict(nouls or {})
        self.calls: list[tuple[str, str, dict[str, dict[str, Any]]]] = []

    def add_choice(self, op: str, choice: str, probabilities: dict[str, float], confidence: float) -> FakeClassifier:
        self.choices.setdefault(op, []).append((choice, probabilities, confidence))
        return self

    def ask(self, state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
        self.calls.append((op, state, questions))
        self.usage.record("classifier", self.model, *CLASSIFIER_TOKENS, op=op)
        answers: dict[str, dict[str, Any]] = {}
        for key, question in questions.items():
            if question["type"] == "choice":
                if not self.choices.get(op):
                    raise AssertionError(f"FakeClassifier: no scripted choice left for {op!r}")
                choice, probabilities, confidence = self.choices[op].pop(0)
                unknown = {choice, *probabilities} - set(question["criteria"])
                if unknown:
                    raise AssertionError(f"FakeClassifier: {unknown} not among options {list(question['criteria'])}")
                answers[key] = {"type": "choice", "choice": choice, "probabilities": probabilities, "confidence": confidence}
            else:
                answers[key] = {"type": "noul", "noul": self._noul(op, question["instructions"])}
        return answers

    def _noul(self, op: str, text: str) -> float:
        score = self.noul_scores.get(op, {})
        if callable(score):
            return score(text)
        return next((p for needle, p in score.items() if needle in text), 0.0)

    @property
    def ops(self) -> list[str]:
        return [op for op, _, _ in self.calls]


class ContentLLM(LLM):
    """A model whose every answer depends only on the prompt it is given, never on the order of the calls.

    That makes it safe under parallel ingest: the same prompt gets the same reply from any thread. Sources are
    written "topic: <group>-<n>" then free lines. Notes are titled "About <topic>"; a note goes in a folder named
    after the group (the part of the topic before the first "-"); a second source of one topic matches that note
    and is merged into it (its text appended); every note is related to the others it is shown.
    A source containing one of `fail_on`'s keys makes its summarize call fail with that error.
    """

    def __init__(self, usage: UsageTracker, *, fail_on: dict[str, Exception] | None = None, jitter: float = 0.0) -> None:
        super().__init__(LLMConfig(model="fake/llm"), usage)
        self.calls: list[tuple[str, list[dict[str, str]]]] = []
        self.fail_on = fail_on or {}
        self.jitter = jitter  # up to this many seconds of delay per call, picked from the prompt: shuffles who finishes first

    @property
    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]

    def chat(self, messages: list[dict[str, str]], *, op: str = "") -> str:
        user = messages[1]["content"]
        self.calls.append((op, [dict(m) for m in messages]))
        if self.jitter:
            time.sleep(self.jitter * (zlib.crc32(user.encode()) % 7) / 6)
        self.usage.record("llm", self.model, *LLM_TOKENS, op=op)
        if op == "librarian/summarize":
            for key, error in self.fail_on.items():
                if key in user:
                    raise error
        handler = getattr(self, "_" + op.rpartition("/")[2], None)
        if handler is None:
            raise AssertionError(f"ContentLLM: no answer for {op!r}")
        return json.dumps(handler(user))

    # -- the answers ---------------------------------------------------------------

    @staticmethod
    def _between(text: str, start: str, end: str) -> str:
        """What lies between the LAST `start` and the `end` after it."""
        head = text.rindex(start) + len(start)
        return text[head : text.index(end, head)]

    def _summarize(self, user: str) -> dict[str, Any]:
        text = self._between(user, "<source>\n", "\n</source>")
        topic = re.match(r"topic: (\S+)", text)
        name = topic.group(1) if topic else "misc-0"
        return {"title": f"About {name}", "summary": f"All on {name}.", "tags": [name.split("-")[0]], "body": text}

    def _route(self, user: str) -> dict[str, Any]:
        group = self._between(user, "Title: About ", "\n").split("-")[0]
        folders = self._between(user, "Its subfolders (name: description):\n", "\n\n<note>").splitlines()
        if any(line.startswith(f"- {group}:") for line in folders):
            return {"action": "descend", "subfolder": group}
        return {"action": "new" if "(root of the wiki)" in user else "here"}

    def _name_folder(self, user: str) -> dict[str, Any]:
        group = self._between(user, "Title: About ", "\n").split("-")[0]
        return {"name": group, "description": f"Notes on {group}."}

    def _find_match(self, user: str) -> dict[str, Any]:
        title = self._between(user, "Title: ", "\n")
        notes = self._between(user, "<notes>\n", "\n</notes>").splitlines()
        found = next((line[2:].split(":", 1)[0] for line in notes if f": {title} — " in line), None)
        return {"match": found}

    def _consolidate(self, user: str) -> dict[str, Any]:
        return {"decision": "modify"}

    def _merge(self, user: str) -> dict[str, Any]:
        title = self._between(user, "Existing note\nTitle: ", "\n")
        summary = self._between(user, "\nSummary: ", "\n")
        body = self._between(user, "<existing>\n", "\n</existing>")
        return {"title": title, "summary": summary, "tags": [], "body": body + "\n\n" + self._between(user, "<source>\n", "\n</source>")}

    def _relate(self, user: str) -> dict[str, Any]:
        notes = self._between(user, "<notes>\n", "\n</notes>").splitlines()
        return {"related": [line[2:].split(":", 1)[0] for line in notes if line.startswith("- ")]}
