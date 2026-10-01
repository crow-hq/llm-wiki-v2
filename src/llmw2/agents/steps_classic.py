# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The classic steps: every decision of an ingest and of a question is taken by the LLM.

Public, so a custom step can reuse them: `replace(CLASSIC, summarize=lambda ctx, s: my_cleanup(classic.summarize(ctx, s)))`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator

from llmw2.agents.base import clean_tags, list_folders, list_notes
from llmw2.agents.data import Candidate, NoteDraft, Route, Source
from llmw2.bundle.files import extract_text
from llmw2.bundle.tree import Folder, Note

if TYPE_CHECKING:
    from llmw2.agents.steps import StepContext

# Routing options of the paper (§5.1 step 1) and consolidation options (step 3).
HERE, NEW, NONE = "Here", "New subfolder", "None of these"
MODIFY, NEW_NOTE = "Modify", "New note"


def _lower(value: Any) -> Any:
    return value.strip().lower() if isinstance(value, str) else value


Lower = BeforeValidator(_lower)


# -- LLM reply schemas ----------------------------------------------------------


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


class Navigation(BaseModel):
    reasoning: str = ""
    open: list[str] = []
    select: list[str] = []
    done: bool = False


# -- ingest ---------------------------------------------------------------------------


def extract(ctx: StepContext, data: bytes, filename: str) -> str:
    """The text of a .txt, .md or .pdf file."""
    return extract_text(data, filename)


def summarize(ctx: StepContext, source: Source) -> Candidate:
    """CROW step 0: title, one-line summary, tags and body of the candidate note."""
    if not ctx.cfg.summarize:
        return _verbatim(source)
    draft = ctx.ask_json(
        "librarian/summarize",
        NoteDraft,
        source_title=source.title or "(none)",
        source_resource=source.resource or "(none)",
        source_text=source.text[: ctx.cfg.source_chars],
    )
    return Candidate(draft.title.strip(), draft.summary.strip(), clean_tags(draft.tags), draft.body.strip())


def _verbatim(source: Source) -> Candidate:
    """CROW step 0 disabled: the note stores the source; title and summary come from its metadata."""
    text = source.text.strip()
    lines = [line.strip().lstrip("#").strip() for line in text.splitlines() if line.strip()]
    title = (source.title or (lines[0] if lines else "") or "Untitled source")[:120]
    rest = lines[1:] if not source.title else lines  # the first line already became the title
    sentence = re.split(r"(?<=[.!?])\s", " ".join(" ".join(rest).split()), maxsplit=1)[0]
    return Candidate(title, (sentence or title)[:200], [], text)


def route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
    """Descend one level at a time: stay here, enter a subfolder, or open a new one."""
    folder = root
    while True:
        if not ctx.can_create(folder):  # at max_depth: nothing to enter, nothing to create
            ctx.decide("route", "rule", folder.label)
            return Route(folder, False, folder.ancestors())
        step = ctx.ask_json(
            "librarian/route",
            RouteStep,
            folder=folder.label,
            description=folder.description or "(root of the wiki)",
            subfolders=list_folders(ctx.children(folder)),
            note=ctx.state(cand),
        )
        wanted = (step.subfolder or "").strip().strip("/").split("/")[-1]
        sub = next((f for f in ctx.children(folder) if f.name == wanted), None) if step.action == "descend" else None
        if sub is not None:
            ctx.decide("route", "llm", sub.label)
            folder = sub
            continue
        new = step.action == "new" or folder.is_root  # the root holds no notes (and here a folder can be made)
        ctx.decide("route", "llm", f"{folder.label} ({NEW if new else HERE})")
        return Route(folder, new, folder.ancestors())


def name_folder(ctx: StepContext, parent: Folder, cand: Candidate) -> tuple[str, str]:
    out = ctx.ask_json(
        "librarian/name_folder",
        FolderName,
        parent=parent.label,
        parent_description=parent.description or "(root of the wiki)",
        siblings=list_folders(parent.subfolders),
        note=ctx.state(cand),
    )
    return out.name, out.description


def match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
    """CROW step 2: an existing note on the same subject, if any."""
    if not pool:
        return None
    out = ctx.ask_json("librarian/find_match", Match, note=ctx.state(cand), notes=list_notes(pool))
    found = next((n for n in pool if n.rel == (out.match or "").strip().strip("/")), None)
    ctx.decide("match", "llm", found.rel if found else "none")
    return found


def consolidate(ctx: StepContext, note: Note, cand: Candidate) -> bool:
    """CROW step 3: modify the existing note, or write a new one next to it."""
    out = ctx.ask_json("librarian/consolidate", Consolidation, note=ctx.state(cand), existing=note.full_text())
    ctx.decide("consolidate", "llm", out.decision)
    return out.decision == "modify"


def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
    """CROW step 3, modify branch: rewrite the note with the full incoming source."""
    return ctx.ask_json(
        "librarian/merge",
        NoteDraft,
        title=note.title,
        summary=note.summary,
        tags=", ".join(note.tags) or "(none)",
        body=note.main_body(),
        source_text=source.text[: ctx.cfg.source_chars],
    )


def relate(ctx: StepContext, note: Note, others: list[Note]) -> list[Note]:
    """Correlation: notes worth a See-also link from this one."""
    out = ctx.ask_json(
        "librarian/relate",
        Related,
        note=note.compact(ctx.cfg.classifier.state_chars),
        notes=list_notes(others),
        max_links=ctx.cfg.max_links,
    )
    by_rel = {n.rel: n for n in others}
    related = [by_rel[r.strip().strip("/")] for r in dict.fromkeys(out.related) if r.strip().strip("/") in by_rel]
    ctx.decide("relate", "llm", ", ".join(n.rel for n in related) or "none")
    return related[: ctx.cfg.max_links]


# -- ask ------------------------------------------------------------------------------


def select(ctx: StepContext, root: Folder, question: str) -> list[Note]:
    """Classic navigation: open folders level by level, keep the notes that help, stop when enough."""
    k = ctx.cfg.max_notes
    frontier: list[Folder] = [root]
    visited: list[Folder] = []
    selected: list[Note] = []
    while frontier and len(visited) < ctx.cfg.max_steps and len(selected) < k:
        folder = frontier.pop(0)
        visited.append(folder)
        if not folder.subfolders and not folder.notes:
            continue
        step = ctx.ask_json(
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
        ctx.decide("navigate", "llm", f"{folder.label}: open {[s.name for s in opened]} select {len(picked)}")
        if step.done and selected:  # an empty-handed "done" only means this folder: queued ones stay unseen by the model
            break
    return selected[:k]


def answer(ctx: StepContext, question: str, notes: list[Note]) -> str:
    """The LLM reads the selected notes in full and answers."""
    return ctx.ask_text(
        "researcher/answer",
        question=question,
        notes="\n\n".join(f"### {n.rel}\n\n{n.full_text()}" for n in notes),
    )
