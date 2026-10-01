# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Researcher answers a question from the wiki: select notes, then answer with citations.

The flow is fixed; `select` and `answer` are steps of the `Steps` it is given, and their output is checked.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from llmw2.agents.base import Decision
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import StepContext, Steps, check_answer, check_select
from llmw2.bundle.store import WikiStore
from llmw2.config import WikiConfig
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageReport

NO_ANSWER = "The wiki has no notes relevant to this question."
_CITATION = re.compile(r"\[([^\[\]\s]+\.md)\]")


@dataclass
class Answer:
    text: str
    citations: list[str]  # bundle paths cited in the text
    notes: list[str]  # bundle paths given to the answering LLM
    decisions: list[Decision]
    usage: UsageReport = field(default_factory=UsageReport)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "usage": self.usage.to_dict()}


class Researcher:
    """One instance serves one question, so its trace of decisions is never shared."""

    def __init__(
        self, store: WikiStore, llm: LLM, prompts: Prompts, cfg: WikiConfig, steps: Steps, classifier: Classifier | None = None
    ) -> None:
        self.store = store
        self.cfg = cfg
        self.steps = steps
        self.ctx = StepContext(llm, classifier, prompts, cfg, system_prompt="researcher/system")

    def ask(self, question: str) -> Answer:
        ctx = self.ctx
        with ctx.llm.usage.span() as used:
            root = self.store.load()
            notes = check_select(self.steps.select(ctx, root, question), root)
            text = check_answer(self.steps.answer(ctx, question, notes)) if notes else NO_ANSWER
        read = {n.rel for n in notes}
        citations = [c for c in dict.fromkeys(_CITATION.findall(text)) if c in read]
        return Answer(text, citations, [n.rel for n in notes], ctx.decisions, used)
