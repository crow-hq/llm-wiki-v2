# Copyright 2026 Federico Cesarini, Marco Sassarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Scripted stand-ins for the two model clients.

They subclass the real clients and replace only the network call, so JSON
parsing, retries on invalid JSON, batching and usage accounting all run for real.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from okf_wiki.classifier import Classifier
from okf_wiki.config import ClassifierConfig, LLMConfig
from okf_wiki.llm import LLM
from okf_wiki.usage import UsageTracker

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
        super().__init__(ClassifierConfig(model="fake/jev", api_key=""), usage)
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
