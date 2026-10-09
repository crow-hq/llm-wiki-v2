# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Researcher answers a question from the wiki: select notes, then answer with citations.

The flow is fixed; `select` and `answer` are steps of the `Steps` it is given, and their output is checked.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from llmw2.agents import revisions
from llmw2.agents.base import Decision
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import StepContext, Steps, check_answer, check_select
from llmw2.bundle.document import OKFDocumentError
from llmw2.bundle.store import WikiStore
from llmw2.bundle.tree import Note
from llmw2.config import WikiConfig
from llmw2.errors import InputError, ModelError
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageReport

log = logging.getLogger(__name__)

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
            notes, past = self._with_history(question, notes)
            asked = f"{question}\n\n{revisions.HISTORY_HINT}" if past else question
            text = check_answer(self.steps.answer(ctx, asked, notes)) if notes else NO_ANSWER
        read = {n.rel for n in notes}
        citations = [c for c in dict.fromkeys(_CITATION.findall(text)) if c in read]
        return Answer(text, citations, [n.rel for n in notes], ctx.decisions, used)

    def _with_history(self, question: str, notes: list[Note]) -> tuple[list[Note], bool]:
        """Each note that was revised in place is followed by its archived version, if the question asks about the past (True)."""
        ctx = self.ctx
        if self.cfg.revisions != "replace" or ctx.classifier is None or not any(n.frontmatter.get("previous_version") for n in notes):
            return notes, False
        try:
            p = ctx.classifier.nouls(question, {"q": revisions.HISTORY}, op="ask/historical")["q"]
        except ModelError as e:
            log.warning("classifier failed on the historical question, no archive is read: %s", e)
            ctx.decide("historical", "classifier", "error", fallback=True)
            return notes, False
        ctx.decide("historical", "classifier", "archive" if p > revisions.HISTORY_MIN else "current", p)
        if p <= revisions.HISTORY_MIN:
            return notes, False
        out: list[Note] = []
        for note in notes:
            out.append(note)
            previous = note.frontmatter.get("previous_version")
            if not previous:
                continue
            try:
                old = self.store.read(str(previous))
                if all(old.rel != n.rel for n in notes):
                    out.append(old)
            except (FileNotFoundError, InputError, OKFDocumentError):
                log.warning("the archived version of %s is missing: %s", note.rel, previous)
        return out, True
