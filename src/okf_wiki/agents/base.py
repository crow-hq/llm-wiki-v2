# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What the Librarian and the Researcher share: store, LLM, prompts, and a trace of decisions."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from pydantic import BaseModel

from okf_wiki.agents.prompts import Prompts
from okf_wiki.bundle.store import WikiStore
from okf_wiki.bundle.tree import Folder, Note
from okf_wiki.config import WikiConfig
from okf_wiki.models.llm import LLM

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger(__name__)


@dataclass
class Decision:
    """One decision of an ingest or a question, kept to calibrate thresholds (CROW §5.3)."""

    step: str  # route, match, consolidate, relate, navigate, select, …
    decider: Literal["llm", "classifier", "rule"]
    choice: str
    confidence: float | None = None
    fallback: bool = False  # the classifier was not confident (or failed) and the LLM decided
    scores: dict[str, float] = field(default_factory=dict)  # classifier probability per option / candidate


class Agent:
    """Base class. One instance serves one operation, so its trace is never shared."""

    system_prompt: str = ""

    def __init__(self, store: WikiStore, llm: LLM, prompts: Prompts, cfg: WikiConfig) -> None:
        self.store = store
        self.llm = llm
        self.prompts = prompts
        self.cfg = cfg
        self.decisions: list[Decision] = []

    def ask_json(self, prompt: str, schema: type[T], **values: object) -> T:
        system = self.prompts.render(self.system_prompt)
        return self.llm.json(system, self.prompts.render(prompt, **values), schema, op=prompt)

    def ask_text(self, prompt: str, **values: object) -> str:
        system = self.prompts.render(self.system_prompt)
        return self.llm.complete(system, self.prompts.render(prompt, **values), op=prompt)

    def decide(
        self,
        step: str,
        decider: Literal["llm", "classifier", "rule"],
        choice: str,
        confidence: float | None = None,
        *,
        fallback: bool = False,
        scores: dict[str, float] | None = None,
    ) -> None:
        self.decisions.append(Decision(step, decider, choice, confidence, fallback, dict(scores or {})))
        extra = (f" {confidence:.2f}" if confidence is not None else "") + (" [fallback]" if fallback else "")
        log.info("decide %-15s %-10s %s%s", step, decider, choice, extra)


def list_folders(folders: list[Folder]) -> str:
    """`- name: description` lines, the way every prompt shows folders."""
    return "\n".join(f"- {f.name}: {f.description or '(no description)'}" for f in folders) or "(none)"


def list_notes(notes: list[Note]) -> str:
    """`- path: Title — summary` lines, the way every prompt shows notes."""
    return "\n".join(f"- {n.rel}: {n.title} — {n.summary}" for n in notes) or "(none)"
