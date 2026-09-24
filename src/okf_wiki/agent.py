# Copyright 2026 Federico Cesarini
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

"""What the Librarian and the Researcher share: store, LLM, prompts, and a trace of decisions."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, TypeVar

from pydantic import BaseModel

from okf_wiki.config import WikiConfig
from okf_wiki.llm import LLM
from okf_wiki.prompts import Prompts
from okf_wiki.store import Folder, Note, WikiStore

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger("okf_wiki")


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
