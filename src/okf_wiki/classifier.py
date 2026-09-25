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

"""The CROW typed classifier (e.g. TypeSafe Jev), spoken over the System One API.

A request carries one *state* and several named *questions*; all are answered in
parallel with probabilities, and no text is generated. Two primitives are used:
*choice* (one option among up to 255, with a confidence) and *noul* (probability
that a yes/no statement holds). OpenRouter, TypeSafe and local Laya servers all
accept the same request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from okf_wiki.client import HttpModel, ModelError, reported_cost
from okf_wiki.config import ClassifierConfig
from okf_wiki.usage import UsageTracker

MAX_OPTIONS = 255


@dataclass
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float]
    confidence: float  # how concentrated the distribution is, not whether it is right


class Classifier(HttpModel):
    kind = "classifier"

    def __init__(self, cfg: ClassifierConfig, usage: UsageTracker, *, client: httpx.Client | None = None) -> None:
        super().__init__(cfg.base_url, cfg.model, usage, api_key=cfg.api_key, timeout=cfg.timeout, client=client)
        self.request_chars = cfg.request_chars

    def ask(self, state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
        """One request: every question is answered against the same state."""
        start = time.perf_counter()
        data = self._post("/systemone", {"model": self.model, "state": state, "questions": questions})
        usage = data.get("usage") or {}
        self.usage.record(
            "classifier",
            self.model,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            op=op,
            seconds=time.perf_counter() - start,
            cost=reported_cost(usage),
        )
        answers = data.get("answers")
        # A wrong base URL can answer 200 with an HTML page; check the shape, not the status.
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise ModelError(f"classifier answered without the expected answers (check base_url {self.base_url})")
        return answers

    def choice(self, state: str, instructions: str, options: dict[str, str], *, op: str = "") -> ChoiceAnswer:
        if not 2 <= len(options) <= MAX_OPTIONS:
            raise ModelError(f"a choice needs 2..{MAX_OPTIONS} options, got {len(options)}")
        answer = self.ask(state, {"q": {"type": "choice", "instructions": instructions, "criteria": options}}, op=op)["q"]
        try:
            probabilities = {str(k): float(v) for k, v in answer["probabilities"].items()}
            return ChoiceAnswer(str(answer["choice"]), probabilities, float(answer["confidence"]))
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise ModelError(f"malformed choice answer: {answer!r}") from e

    def nouls(self, state: str, questions: dict[str, str], *, op: str = "") -> dict[str, float]:
        """Probability that each statement holds, batched so every request fits the context."""
        result: dict[str, float] = {}
        for batch in self._batches(state, questions):
            answers = self.ask(state, {k: {"type": "noul", "instructions": q} for k, q in batch.items()}, op=op)
            for key, answer in answers.items():
                try:
                    result[key] = float(answer["noul"])
                except (KeyError, TypeError, ValueError) as e:
                    raise ModelError(f"malformed noul answer: {answer!r}") from e
        return result

    def _batches(self, state: str, questions: dict[str, str]) -> list[dict[str, str]]:
        budget = max(self.request_chars - len(state), 1)
        batches: list[dict[str, str]] = [{}]
        used = 0
        for key, question in questions.items():
            if batches[-1] and used + len(question) > budget:
                batches.append({})
                used = 0
            batches[-1][key] = question
            used += len(question)
        return [b for b in batches if b]
