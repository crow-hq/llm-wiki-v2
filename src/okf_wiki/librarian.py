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

"""The Librarian files a source into the wiki.

    summarize → route → find_match → should_merge → merge | (name_folder →) write → pick_related → link

`Librarian` takes every decision with the LLM. `CrowLibrarian` overrides only
the four decision hooks (route, find_match, should_merge, pick_related) with the
typed classifier, as in the CROW paper (§5.1), and falls back to the LLM where
the paper prescribes an escape. Everything that writes text or files is shared,
so both modes produce the same kind of wiki.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator

from okf_wiki.agent import Agent, Decision, list_folders, list_notes
from okf_wiki.classifier import Classifier
from okf_wiki.client import ModelError
from okf_wiki.config import WikiConfig
from okf_wiki.llm import LLM
from okf_wiki.prompts import Prompts
from okf_wiki.store import Folder, Note, WikiStore, slugify
from okf_wiki.usage import UsageReport

log = logging.getLogger(__name__)

# Routing options of the paper (§5.1 step 1) and consolidation options (step 3).
HERE, NEW, NONE = "Here", "New subfolder", "None of these"
MODIFY, NEW_NOTE = "Modify", "New note"


def _lower(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


Lower = BeforeValidator(_lower)


# -- data ---------------------------------------------------------------------


@dataclass
class Source:
    text: str
    title: str | None = None
    resource: str | None = None  # URL or path of the original, if any


@dataclass
class Candidate:
    """The note an ingest would write (CROW step 0)."""

    title: str
    summary: str
    tags: list[str]
    body: str

    def compact(self, max_chars: int) -> str:
        """Title, summary and opening passage: the state every decision reads."""
        return f"Title: {self.title}\nSummary: {self.summary}\n\n{self.body}".strip()[:max_chars]


@dataclass
class Route:
    folder: Folder  # where the note goes
    new: bool  # file it in a new subfolder of `folder`
    candidates: list[Folder]  # folders whose notes are checked for consolidation


@dataclass
class IngestResult:
    action: Literal["created", "merged"]
    note: str  # bundle path of the note written
    title: str
    folder: str  # "/" or "/finance/pricing"
    created_folders: list[str]
    related: list[str]
    decisions: list[Decision]
    usage: UsageReport = field(default_factory=UsageReport)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "usage": self.usage.to_dict()}


# -- LLM reply schemas ----------------------------------------------------------


class NoteDraft(BaseModel):
    title: str
    summary: str
    tags: list[str] = []
    body: str


class RouteStep(BaseModel):
    reasoning: str = ""
    action: Annotated[Literal["here", "descend", "new"], Lower]
    subfolder: str | None = None


class FallbackRoute(BaseModel):
    reasoning: str = ""
    action: Annotated[Literal["select", "create"], Lower]
    folder: str


class FolderName(BaseModel):
    name: str
    description: str


class Match(BaseModel):
    reasoning: str = ""
    match: str | None = None


class Consolidation(BaseModel):
    reasoning: str = ""
    decision: Annotated[Literal["modify", "new"], Lower]


class Related(BaseModel):
    reasoning: str = ""
    related: list[str] = []


# -- the classic librarian ---------------------------------------------------------


class Librarian(Agent):
    system_prompt = "librarian/system"
    mode = "classic"

    def ingest(self, source: Source) -> IngestResult:
        with self.llm.usage.span() as used:
            root = self.store.load()
            cand = self.summarize(source)
            raw = self.store.save_raw(source.text, title=source.title or cand.title, resource=source.resource)
            ref = {"resource": raw, "title": source.title or cand.title}
            route = self.route(root, cand)
            pool = _unique(n for f in [*route.candidates, route.folder] for n in f.notes)
            match = self.find_match(pool, cand)
            created: list[str] = []
            if match is not None and self.should_merge(match, cand):
                draft = self.merge(match, source)
                note = self.store.update_note(
                    match, title=draft.title, summary=draft.summary, body=draft.body, tags=_tags(draft.tags), source=ref
                )
                action: Literal["created", "merged"] = "merged"
            else:
                folder = route.folder
                if route.new and self.can_create(folder):
                    folder, is_new = self.store.create_folder(folder, *self.name_folder(folder, cand))
                    created += [folder.label] if is_new else []
                note = self.store.write_note(
                    folder, title=cand.title, summary=cand.summary, body=cand.body, tags=cand.tags, source=ref
                )
                action = "created"
            others = [n for n in _unique([*pool, *note.folder.notes]) if n is not note] if note.folder else pool
            related = self.pick_related(note, others)[: self.cfg.max_links] if others else []
            for other in related:
                self.store.link(note, other)
            verb = "Merged" if action == "merged" else "Created"
            self.store.append_log(verb, f"from {raw} ({self.mode})", note.rel, note.title)
        return IngestResult(
            action,
            note.rel,
            note.title,
            note.folder.label if note.folder else "/",
            created,
            [r.rel for r in related],
            self.decisions,
            used,
        )

    def can_create(self, folder: Folder) -> bool:
        return folder.depth < self.cfg.max_depth

    def children(self, folder: Folder) -> list[Folder]:
        """Subfolders the librarian may enter: none below max_depth."""
        return folder.subfolders if folder.depth < self.cfg.max_depth else []

    def state(self, cand: Candidate) -> str:
        return cand.compact(self.cfg.classifier.state_chars)

    # -- writing steps (LLM, shared by both modes) ---------------------------------

    def summarize(self, source: Source) -> Candidate:
        """CROW step 0: title, one-line summary, tags and body of the candidate note."""
        if not self.cfg.summarize:
            return _verbatim(source)
        draft = self.ask_json(
            "librarian/summarize",
            NoteDraft,
            source_title=source.title or "(none)",
            source_resource=source.resource or "(none)",
            source_text=source.text[: self.cfg.source_chars],
        )
        return Candidate(draft.title.strip(), draft.summary.strip(), _tags(draft.tags), draft.body.strip())

    def name_folder(self, parent: Folder, cand: Candidate) -> tuple[str, str]:
        out = self.ask_json(
            "librarian/name_folder",
            FolderName,
            parent=parent.label,
            parent_description=parent.description or "(root of the wiki)",
            siblings=list_folders(parent.subfolders),
            note=self.state(cand),
        )
        return out.name, out.description

    def merge(self, note: Note, source: Source) -> NoteDraft:
        """CROW step 3, modify branch: rewrite the note with the full incoming source."""
        return self.ask_json(
            "librarian/merge",
            NoteDraft,
            title=note.title,
            summary=note.summary,
            tags=", ".join(note.tags) or "(none)",
            body=note.main_body(),
            source_text=source.text[: self.cfg.source_chars],
        )

    # -- decision hooks (LLM here, classifier in CrowLibrarian) ----------------------

    def route(self, root: Folder, cand: Candidate) -> Route:
        """Descend one level at a time: stay here, enter a subfolder, or open a new one."""
        folder = root
        while True:
            if not self.can_create(folder):  # at max_depth: nothing to enter, nothing to create
                self.decide("route", "rule", folder.label)
                return Route(folder, False, folder.ancestors())
            step = self.ask_json(
                "librarian/route",
                RouteStep,
                folder=folder.label,
                description=folder.description or "(root of the wiki)",
                subfolders=list_folders(self.children(folder)),
                note=self.state(cand),
            )
            wanted = (step.subfolder or "").strip().strip("/").split("/")[-1]
            sub = next((f for f in self.children(folder) if f.name == wanted), None) if step.action == "descend" else None
            if sub is not None:
                self.decide("route", "llm", sub.label)
                folder = sub
                continue
            new = step.action == "new"
            self.decide("route", "llm", f"{folder.label} ({NEW if new else HERE})")
            return Route(folder, new, folder.ancestors())

    def find_match(self, pool: list[Note], cand: Candidate) -> Note | None:
        """CROW step 2: an existing note on the same subject, if any."""
        if not pool:
            return None
        out = self.ask_json("librarian/find_match", Match, note=self.state(cand), notes=list_notes(pool))
        match = next((n for n in pool if n.rel == (out.match or "").strip().strip("/")), None)
        self.decide("match", "llm", match.rel if match else "none")
        return match

    def should_merge(self, note: Note, cand: Candidate) -> bool:
        """CROW step 3: modify the existing note, or write a new one next to it."""
        out = self.ask_json("librarian/consolidate", Consolidation, note=self.state(cand), existing=note.full_text())
        self.decide("consolidate", "llm", out.decision)
        return out.decision == "modify"

    def pick_related(self, note: Note, others: list[Note]) -> list[Note]:
        """Correlation: notes worth a See-also link from this one."""
        out = self.ask_json(
            "librarian/relate",
            Related,
            note=note.compact(self.cfg.classifier.state_chars),
            notes=list_notes(others),
            max_links=self.cfg.max_links,
        )
        by_rel = {n.rel: n for n in others}
        related = [by_rel[r.strip().strip("/")] for r in dict.fromkeys(out.related) if r.strip().strip("/") in by_rel]
        self.decide("relate", "llm", ", ".join(n.rel for n in related) or "none")
        return related[: self.cfg.max_links]


# -- the CROW librarian ------------------------------------------------------------------


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
        candidates = _unique(f for p in finished for f in p.folders)
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


# -- helpers -------------------------------------------------------------------------------


def _unique(items: Any) -> list[Any]:
    """Keep the first occurrence of each object, in order."""
    seen: set[int] = set()
    out = []
    for item in items:
        if id(item) not in seen:
            seen.add(id(item))
            out.append(item)
    return out


def _tags(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(t for t in (slugify(t, max_len=30, fallback="") for t in tags) if t))[:6]


def _verbatim(source: Source) -> Candidate:
    """CROW step 0 disabled: the note stores the source; title and summary come from its metadata."""
    text = source.text.strip()
    lines = [line.strip().lstrip("#").strip() for line in text.splitlines() if line.strip()]
    title = (source.title or (lines[0] if lines else "") or "Untitled source")[:120]
    rest = lines[1:] if not source.title else lines  # the first line already became the title
    sentence = re.split(r"(?<=[.!?])\s", " ".join(" ".join(rest).split()), maxsplit=1)[0]
    return Candidate(title, (sentence or title)[:200], [], text)
