# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The CROW Librarian: the four decision hooks of `Librarian` taken by the typed classifier (CROW §5.1).

Each falls back to the LLM where the paper prescribes an escape, or when the classifier fails.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from okf_wiki.agents.librarian import (
    HERE,
    MODIFY,
    NEW,
    NEW_NOTE,
    NONE,
    Candidate,
    FallbackRoute,
    Librarian,
    Route,
    unique_objects,
)
from okf_wiki.agents.prompts import Prompts
from okf_wiki.bundle.store import WikiStore
from okf_wiki.bundle.tree import Folder, Note
from okf_wiki.config import WikiConfig
from okf_wiki.errors import ModelError
from okf_wiki.models.classifier import Classifier
from okf_wiki.models.llm import LLM

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Path:
    folders: tuple[Folder, ...]
    probs: tuple[float, ...] = ()
    end: str = ""  # HERE or NEW once finished

    @property
    def score(self) -> float:
        """Geometric mean of the edge probabilities (1.0 when nothing had to be decided)."""
        if not self.probs:
            return 1.0
        return math.exp(sum(math.log(max(p, 1e-9)) for p in self.probs) / len(self.probs))


class CrowLibrarian(Librarian):
    mode = "crow"

    def __init__(self, store: WikiStore, llm: LLM, prompts: Prompts, cfg: WikiConfig, classifier: Classifier) -> None:
        super().__init__(store, llm, prompts, cfg)
        self.classifier = classifier
        self.crow = cfg.crow

    def route(self, root: Folder, cand: Candidate) -> Route:
        """Step 1: a Choice per level (subfolders, Here, New subfolder, None of these) with a beam."""
        try:
            finished, visited = self._beam(root, self.state(cand))
        except ModelError as e:
            log.warning("classifier failed while routing, the LLM decides: %s", e)
            self.decide("route", "classifier", "error", fallback=True)
            return super().route(root, cand)
        best = max(finished, key=lambda p: p.score, default=None)
        if best is None or best.score < self.crow.tau_path:
            return self._route_fallback(visited, cand, best.score if best else None)
        candidates = unique_objects(f for p in finished for f in p.folders)
        self.decide("route", "classifier", f"{best.folders[-1].label} ({best.end})", best.score)
        return Route(best.folders[-1], best.end == NEW, candidates)

    def _beam(self, root: Folder, state: str) -> tuple[list[_Path], list[Folder]]:
        """Level by level; at most `beam` open paths survive each level (by path score)."""
        level, finished, visited = [_Path((root,))], [], [root]
        while level:
            expanded: list[_Path] = []
            for path in level:
                finished += self._expand(path, state, expanded, visited)
            level = sorted(expanded, key=lambda p: p.score, reverse=True)[: self.crow.beam]
        return finished, visited

    def _expand(self, path: _Path, state: str, expanded: list[_Path], visited: list[Folder]) -> list[_Path]:
        """One Choice at the end of `path`; returns the paths it finishes, appends those that go deeper."""
        folder = path.folders[-1]
        text = self.prompts.render_sections("classifier/route", folder=folder.label)
        options = {f.name: f.description or f.name for f in self.children(folder)}
        options[HERE] = text[HERE]
        if self.can_create(folder):
            options[NEW] = text[NEW]
        if not folder.is_root:
            options[NONE] = text[NONE]
        if len(options) == 1:  # only "Here" is possible: nothing to decide
            return [_Path(path.folders, path.probs, HERE)]
        answer = self.classifier.choice(state, text["instructions"], options, op="route")
        ranked = sorted(options, key=lambda o: answer.probabilities.get(o, 0.0), reverse=True)
        kept = ranked[:1] if answer.confidence >= self.crow.tau_route else ranked[: self.crow.beam]
        self.decide(
            "route_step", "classifier", f"{folder.label}: {' | '.join(kept)}", answer.confidence, scores=answer.probabilities
        )
        finished = []
        for option in kept:
            p = answer.probabilities.get(option, 0.0)
            if option in (HERE, NEW):
                finished.append(_Path(path.folders, (*path.probs, p), option))
            elif option != NONE and (sub := folder.subfolder(option)) is not None:
                expanded.append(_Path((*path.folders, sub), (*path.probs, p)))
                visited.append(sub)
        return finished

    def _route_fallback(self, visited: list[Folder], cand: Candidate, score: float | None) -> Route:
        """§5.3: the LLM chooses among the folders the classifier explored, or opens a new one."""
        out = self.ask_json(
            "librarian/route_fallback",
            FallbackRoute,
            folders="\n".join(f"- {f.label}: {f.description or '(no description)'}" for f in visited),
            note=self.state(cand),
        )
        wanted = out.folder.strip().rstrip("/") or "/"
        folder = next((f for f in visited if f.label == wanted or f.rel == wanted.strip("/")), visited[0])
        new = out.action == "create" and self.can_create(folder)
        self.decide("route", "llm", f"{folder.label} ({NEW if new else HERE})", score, fallback=True)
        return Route(folder, new, folder.ancestors())

    def find_match(self, pool: list[Note], cand: Candidate) -> Note | None:
        """Step 2: one batched Noul per note; the best one above tau_ing is the candidate."""
        if not pool:
            return None
        questions = {
            f"n{i}": self.prompts.render("classifier/note_relevance", title=n.title, summary=n.summary)
            for i, n in enumerate(pool)
        }
        try:
            probs = self.classifier.nouls(self.state(cand), questions, op="match")
        except ModelError as e:
            log.warning("classifier failed on note relevance, the LLM decides: %s", e)
            self.decide("match", "classifier", "error", fallback=True)
            return super().find_match(pool, cand)
        p, best = max(((probs[f"n{i}"], n) for i, n in enumerate(pool)), key=lambda x: x[0])
        match = best if p > self.crow.tau_ing else None
        scores = {n.rel: probs[f"n{i}"] for i, n in enumerate(pool)}
        self.decide("match", "classifier", match.rel if match else "none", p, scores=scores)
        return match

    def should_merge(self, note: Note, cand: Candidate) -> bool:
        """Step 3: Choice between modifying the note and writing a new one; unsure → new note."""
        room = max(self.classifier.request_chars // 2, 1)
        state = f"{self.state(cand)}\n\n--- Existing note ---\n{note.full_text()[:room]}"
        text = self.prompts.render_sections("classifier/consolidate")
        try:
            answer = self.classifier.choice(
                state, text["instructions"], {MODIFY: text[MODIFY], NEW_NOTE: text[NEW_NOTE]}, op="consolidate"
            )
        except ModelError as e:
            log.warning("classifier failed on consolidation, the LLM decides: %s", e)
            self.decide("consolidate", "classifier", "error", fallback=True)
            return super().should_merge(note, cand)
        merge = answer.choice == MODIFY and answer.confidence >= self.crow.tau_cons
        self.decide("consolidate", "classifier", "modify" if merge else "new", answer.confidence, scores=answer.probabilities)
        return merge

    def pick_related(self, note: Note, others: list[Note]) -> list[Note]:
        """Correlation (an extension of the paper): one Noul per candidate, above tau_link."""
        questions = {
            f"n{i}": self.prompts.render("classifier/relate", title=n.title, summary=n.summary)
            for i, n in enumerate(others)
        }
        try:
            probs = self.classifier.nouls(note.compact(self.cfg.classifier.state_chars), questions, op="relate")
        except ModelError as e:
            log.warning("classifier failed on relatedness, the LLM decides: %s", e)
            self.decide("relate", "classifier", "error", fallback=True)
            return super().pick_related(note, others)
        scored = sorted(((probs[f"n{i}"], n) for i, n in enumerate(others)), key=lambda x: x[0], reverse=True)
        related = [n for p, n in scored if p > self.crow.tau_link][: self.cfg.max_links]
        scores = {n.rel: p for p, n in scored}
        self.decide("relate", "classifier", ", ".join(n.rel for n in related) or "none", scores=scores)
        return related
