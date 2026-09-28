# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

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

from llmw2.config import NAMES, ClassifierConfig
from llmw2.errors import ConfigError, ModelError
from llmw2.models.client import HttpModel, reported_cost
from llmw2.models.usage import UsageTracker

MAX_OPTIONS = 255


@dataclass
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float]
    confidence: float  # how concentrated the distribution is, not whether it is right


class Classifier(HttpModel):
    kind = "classifier"

    def __init__(self, cfg: ClassifierConfig, usage: UsageTracker, *, client: httpx.Client | None = None) -> None:
        super().__init__(cfg.base_url, cfg.model, usage, api_key=cfg.api_key, needs_key=not cfg.ready, timeout=cfg.timeout, client=client)
        self.request_chars = cfg.request_chars
        self.attempts = cfg.attempts
        self.provider = NAMES.get(cfg.provider, cfg.provider)

    def require_key(self) -> None:
        """ConfigError, not ModelError, when the key is missing: every caller falls back to the LLM on a ModelError,
        and a wiki without its classifier key would then run in classic mode without a word."""
        if self.needs_key and not self.api_key:
            raise ConfigError(
                f"no API key for the CROW classifier on {self.provider}: "
                "paste it in Settings → Advanced → Classifier key, or choose classic mode there"
            )

    def ask(self, state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
        """One request: every question is answered against the same state."""
        self.require_key()
        start = time.perf_counter()
        try:
            data = self._post("/systemone", {"model": self.model, "state": state, "questions": questions})
        except ModelError as e:
            if e.status in (401, 403):  # a refused key is the settings' fault, not a passing failure: say so
                raise ConfigError(
                    f"the CROW classifier on {self.provider} refused its key ({e.status}): check Settings → Advanced → Classifier key"
                ) from e
            raise
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


def probe(classifier: Classifier) -> float:
    """One tiny request, to tell a working classifier from a wrong key or address (ModelError)."""
    return classifier.nouls("The sky is blue today.", {"q": "The text is about the weather."}, op="settings/probe")["q"]
