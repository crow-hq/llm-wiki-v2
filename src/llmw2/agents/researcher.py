# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Researcher answers a question from the wiki: select notes, then answer with citations.

`Researcher.select` navigates the folder tree with the LLM. `CrowResearcher.select`
routes with the classifier (§5.2): a Noul per folder, a Noul per note, top-k, and
falls back to LLM navigation when nothing clears its threshold.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel

from llmw2.agents.base import Agent, Decision, list_folders, list_notes
from llmw2.agents.prompts import Prompts
from llmw2.bundle.store import WikiStore
from llmw2.bundle.tree import Folder, Note
from llmw2.config import WikiConfig
from llmw2.errors import ModelError
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


class Navigation(BaseModel):
    reasoning: str = ""
    open: list[str] = []
    select: list[str] = []
    done: bool = False


class Researcher(Agent):
    system_prompt = "researcher/system"

    def ask(self, question: str) -> Answer:
        with self.llm.usage.span() as used:
            notes = self.select(self.store.load(), question)
            text = self.answer(question, notes) if notes else NO_ANSWER
        read = {n.rel for n in notes}
        citations = [c for c in dict.fromkeys(_CITATION.findall(text)) if c in read]
        return Answer(text, citations, [n.rel for n in notes], self.decisions, used)

    def answer(self, question: str, notes: list[Note]) -> str:
        """Shared by both modes: the LLM reads the selected notes in full and answers."""
        return self.ask_text(
            "researcher/answer",
            question=question,
            notes="\n\n".join(f"### {n.rel}\n\n{n.full_text()}" for n in notes),
        )

    def select(self, root: Folder, question: str) -> list[Note]:
        """Classic navigation: open folders level by level, keep the notes that help, stop when enough."""
        k = self.cfg.max_notes
        frontier: list[Folder] = [root]
        visited: list[Folder] = []
        selected: list[Note] = []
        while frontier and len(visited) < self.cfg.max_steps and len(selected) < k:
            folder = frontier.pop(0)
            visited.append(folder)
            if not folder.subfolders and not folder.notes:
                continue
            step = self.ask_json(
                "researcher/navigate",
                Navigation,
                question=question,
                visited="\n".join(f"- {f.label}" for f in visited[:-1]) or "(none yet)",
                folder=folder.label,
                description=folder.description or "(root of the wiki)",
                subfolders=list_folders(folder.subfolders),
                notes=list_notes(folder.notes),
                selected=list_notes(selected),
                k=k,
            )
            by_rel = {n.rel: n for n in folder.notes}
            picked = list(dict.fromkeys(by_rel[r] for r in (x.strip().strip("/") for x in step.select) if r in by_rel))
            selected += [n for n in picked if n not in selected]
            names = (name.strip().strip("/").split("/")[-1] for name in step.open)
            opened = list(dict.fromkeys(s for name in names if (s := folder.subfolder(name))))
            frontier += [s for s in opened if s not in visited and s not in frontier]
            self.decide("navigate", "llm", f"{folder.label}: open {[s.name for s in opened]} select {len(picked)}")
            if step.done and selected:  # an empty-handed "done" only means this folder: queued ones stay unseen by the model
                break
        return selected[:k]


class CrowResearcher(Researcher):
    def __init__(self, store: WikiStore, llm: LLM, prompts: Prompts, cfg: WikiConfig, classifier: Classifier) -> None:
        super().__init__(store, llm, prompts, cfg)
        self.classifier = classifier
        self.crow = cfg.crow

    def select(self, root: Folder, question: str) -> list[Note]:
        try:
            notes = self._route(root, question)
        except ModelError as e:
            log.warning("classifier failed on retrieval, the LLM navigates: %s", e)
            notes = []
        if not notes:
            self.decide("select", "llm", "classic navigation", fallback=True)
            return super().select(root, question)
        return notes

    def _route(self, root: Folder, question: str) -> list[Note]:
        explored, level = [root], [root]
        while level:  # step 1: at each level, explore up to b folders above tau_fold
            subs = [s for f in level for s in f.subfolders]
            if not subs:
                break
            questions = {
                f"f{i}": self.prompts.render("classifier/retrieval_folder", name=s.name, description=s.description or "no description")
                for i, s in enumerate(subs)
            }
            probs = self.classifier.nouls(question, questions, op="retrieve_folder")
            ranked = sorted(((probs[f"f{i}"], s) for i, s in enumerate(subs)), key=lambda x: x[0], reverse=True)
            level = [s for p, s in ranked if p > self.crow.tau_fold][: self.crow.retrieval_beam]
            explored += level
            scores = {s.label: p for p, s in ranked}
            self.decide("retrieve_folder", "classifier", ", ".join(s.label for s in level) or "none", scores=scores)
        candidates = [n for f in explored for n in f.notes]
        if not candidates:
            return []
        questions = {
            f"n{i}": self.prompts.render("classifier/retrieval_note", title=n.title, summary=n.summary)
            for i, n in enumerate(candidates)
        }
        probs = self.classifier.nouls(question, questions, op="retrieve_note")  # step 2: score the notes
        notes = sorted(((probs[f"n{i}"], n) for i, n in enumerate(candidates)), key=lambda x: x[0], reverse=True)
        top = [n for p, n in notes if p > self.crow.tau_ret][: self.crow.k]  # step 3 reads these k notes
        scores = {n.rel: p for p, n in notes}
        self.decide("retrieve_note", "classifier", ", ".join(n.rel for n in top) or "none", scores=scores)
        return top
