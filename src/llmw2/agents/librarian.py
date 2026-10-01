# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

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
import re
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator

from llmw2.agents.base import Agent, Decision, list_folders, list_notes
from llmw2.bundle.origin import Origin, content_hash
from llmw2.bundle.tree import Folder, Note, slugify
from llmw2.models.usage import UsageReport

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
    origin: Origin | None = None  # where the original lives at its source, if it has one


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
    action: Literal["created", "merged", "unchanged"]  # "unchanged": nothing was written
    note: str  # bundle path of the note written ("" when unchanged and no note cites the source)
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
            route = self.route(root, cand)
            pool = unique_objects(n for f in [*route.candidates, route.folder] for n in f.notes)
            match = self.find_match(pool, cand)
            created: list[str] = []
            if match is not None and self.should_merge(match, cand):
                draft = self.merge(match, source)
                raw, ref = self.save_source(source, cand)
                note = self.store.update_note(
                    match, title=draft.title, summary=draft.summary, body=draft.body, tags=clean_tags(draft.tags), source=ref
                )
                action: Literal["created", "merged"] = "merged"
            else:
                folder = route.folder
                if route.new and self.can_create(folder):
                    folder, is_new = self.store.create_folder(folder, *self.name_folder(folder, cand))
                    created += [folder.label] if is_new else []
                raw, ref = self.save_source(source, cand)
                note = self.store.write_note(
                    folder, title=cand.title, summary=cand.summary, body=cand.body, tags=cand.tags, source=ref
                )
                action = "created"
            others = [n for n in unique_objects([*pool, *note.folder.notes]) if n is not note] if note.folder else pool
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

    def save_source(self, source: Source, cand: Candidate) -> tuple[str, dict[str, Any]]:
        """Keep the raw copy and build the note's source entry; called right before the note is written.

        Saved this late so an interrupted ingest leaves no raw copy that no note cites.
        """
        title = source.title or cand.title
        raw = self.store.save_raw(source.text, title=title, resource=source.resource, origin=source.origin)
        ref: dict[str, Any] = {"resource": raw, "title": title, "hash": content_hash(source.text)}
        if source.origin is not None:
            ref["origin"] = source.origin.to_dict()
        return raw, ref

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
        return Candidate(draft.title.strip(), draft.summary.strip(), clean_tags(draft.tags), draft.body.strip())

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
            new = step.action == "new" or folder.is_root  # the root holds no notes (and here a folder can be made)
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


# -- helpers -------------------------------------------------------------------------------


def unique_objects(items: Any) -> list[Any]:
    """Keep the first occurrence of each object, in order."""
    seen: set[int] = set()
    out = []
    for item in items:
        if id(item) not in seen:
            seen.add(id(item))
            out.append(item)
    return out


def clean_tags(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(t for t in (slugify(t, max_len=30, fallback="") for t in tags) if t))[:6]


def _verbatim(source: Source) -> Candidate:
    """CROW step 0 disabled: the note stores the source; title and summary come from its metadata."""
    text = source.text.strip()
    lines = [line.strip().lstrip("#").strip() for line in text.splitlines() if line.strip()]
    title = (source.title or (lines[0] if lines else "") or "Untitled source")[:120]
    rest = lines[1:] if not source.title else lines  # the first line already became the title
    sentence = re.split(r"(?<=[.!?])\s", " ".join(" ".join(rest).split()), maxsplit=1)[0]
    return Candidate(title, (sentence or title)[:200], [], text)
