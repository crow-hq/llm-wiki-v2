# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The classic steps: every decision of an ingest and of a question is taken by the LLM.

Public, so a custom step can reuse them: `replace(CLASSIC, summarize=lambda ctx, s: my_cleanup(classic.summarize(ctx, s)))`.
"""

from __future__ import annotations

import logging
import math
import re
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator

from llmw2.agents.base import clean_tags, list_folders, list_notes
from llmw2.agents.data import Candidate, NoteDraft, Route, Source
from llmw2.agents.state import source_state, split_blocks
from llmw2.bundle.files import extract_text
from llmw2.bundle.tree import Folder, Note
from llmw2.config import PROMPT_ROOM, WikiConfig
from llmw2.errors import InputError, ModelError

if TYPE_CHECKING:
    from llmw2.agents.steps import StepContext

log = logging.getLogger(__name__)

# Bump when `split_blocks` or the map-reduce of `summarize` changes: it is part of the decision hash.
SUMMARY_VERSION = 7
MIN_PART = 500  # the least a part's notes may take, in characters, for a long source to be summarized at all
PART_REPLY_FACTOR = 1.3  # a part's reply may take this many times the characters kept of it (`cap`)
CONSOLIDATE_SOURCE_MAX = 2  # a source longer than this many times `effective_note_chars` is long: read as a state, merged from the text
DECIDE_CHARS = 2_000  # about how long a decision is: a short JSON with a reasoning line (it sets the output cap)
_SENTENCE_END = re.compile(r"\n|[.!?](?=\s)")
LOST_MAX = 0.1  # a merge may drop at most this share of the existing note's values before it is asked again
LOST_LISTED = 80  # most lost values named in that second request
MERGE_LOST_NOTE = (
    "\n\nThe previous version of the merged note dropped these values of the existing note; keep each of them, "
    'stating the newer value next to it ("previously …") where the incoming text replaces it:\n'
)
SOFT_CAP = 1.2 # a note body longer than this many times `effective_note_chars` is logged, not cut

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
        return _verbatim(source, ctx.cfg.classifier.state_chars)
    room = ctx.cfg.effective_read_chars - PROMPT_ROOM
    if len(source.text) <= room:
        draft = ctx.ask_json(
            "librarian/summarize",
            NoteDraft,
            source_title=source.title or "(none)",
            source_resource=source.resource or "(none)",
            source_text=source.text,
            length_rule="",  # one call reads the whole source: no length rule (the output cap only stops runaways)
            # A summary has no reason to be longer than its source; a cap at the note length cut long replies (2026-10-07).
            reply_chars=max(ctx.cfg.effective_note_chars, len(source.text)),
        )
    else:
        draft = _summarize_in_parts(ctx, source, room)
    state = source_state(source.title, source.text, ctx.cfg.classifier.state_chars)
    cand = Candidate(draft.title.strip(), draft.summary.strip(), clean_tags(draft.tags), draft.body.strip(), state=state)
    _warn_if_long("summarize", cand.title, cand.body, ctx.cfg)
    return cand


def _summarize_in_parts(ctx: StepContext, source: Source, room: int) -> NoteDraft:
    """A source longer than one call: notes on each block in turn (map), then one call writes the note from them (reduce).

    A failed part fails the step and the calls already made are lost.
    """
    cfg = ctx.cfg
    blocks = split_blocks(source.text, room)
    n = len(blocks)
    if room // n < MIN_PART:
        raise InputError(
            f"the source is too long to summarize with read_chars {cfg.effective_read_chars}: "
            f"it takes {n} parts and the notes of all of them must fit in one call; raise read_chars"
        )
    parts = []
    for k, block in enumerate(blocks, 1):
        heading = block.heading or "(no heading)"
        cap = min(cfg.effective_note_chars // 2, room // n, len(block.text) // 2)
        reply_chars = max(MIN_PART, math.ceil(PART_REPLY_FACTOR * cap))  # close to what is kept; `cut_notes` trims below
        try:
            args: dict[str, Any] = {
                "source_title": source.title or "(none)",
                "part": k,
                "parts": n,
                "heading": heading,
                "block_text": block.text,
                "cap": cap,
                "keep_cut": True,  # only the first `cap` characters are kept anyway: a cut reply holds them
            }
            cut: list[str] = []
            notes = ctx.ask_text("librarian/summarize_part", reply_chars=reply_chars, cut=cut, **args).strip()
            if cut and len(notes) < cap // 2 and reply_chars < cfg.effective_note_chars:
                # a reply cut that short (reasoning, or a runaway start) lost the notes: once more with the room of a note
                notes = ctx.ask_text("librarian/summarize_part", reply_chars=cfg.effective_note_chars, **args).strip()
        except ModelError as e:
            raise ModelError(f"librarian/summarize_part: part {k} of {n}: {e}", status=e.status) from e
        if len(notes) > cap:
            kept = cut_notes(notes, cap)
            log.warning("summarize: part %d of %d: notes cut from %d to %d characters", k, n, len(notes), len(kept))
            notes = kept
        parts.append(f"Part {k} of {n} — {heading}\n{notes}")
    return ctx.ask_json(
        "librarian/summarize_combine",
        NoteDraft,
        source_title=source.title or "(none)",
        source_resource=source.resource or "(none)",
        part_notes="\n\n".join(parts),
    )


def cut_notes(notes: str, cap: int) -> str:
    """The notes of a part cut to at most `cap` characters: at a sentence end, else at a space, else hard.

    A cut point is used only if it keeps at least half of `cap`.
    """
    if cap <= 0:
        return ""
    if len(notes) <= cap:
        return notes
    half = cap // 2
    window = notes[: cap + 1]  # the whitespace after a sentence's punctuation may sit at index cap
    ends = [m.start() + (m.group() != "\n") for m in _SENTENCE_END.finditer(window) if m.start() < cap]
    spaces = [m.start() for m in re.finditer(r"\s", window)]
    for at in (max(ends, default=0), max(spaces, default=0)):
        if at >= half and notes[:at].strip():
            return notes[:at].rstrip()
    return notes[:cap].rstrip() or notes.lstrip()[:cap].rstrip()  # a start of only whitespace: the first words instead


def _warn_if_long(op: str, title: str, body: str, cfg: WikiConfig) -> None:
    """The length of a note is asked for, not enforced: a body well over the wish is only logged."""
    if len(body) > SOFT_CAP * cfg.effective_note_chars:
        asked = cfg.effective_note_chars
        log.warning("%s: %r has a body of %d characters, over %.1f times the %d asked", op, title, len(body), SOFT_CAP, asked)


def _verbatim(source: Source, state_chars: int) -> Candidate:
    """CROW step 0 disabled: the note stores the source; title and summary come from its metadata."""
    text = source.text.strip()
    lines = [line.strip().lstrip("#").strip() for line in text.splitlines() if line.strip()]
    title = (source.title or (lines[0] if lines else "") or "Untitled source")[:120]
    rest = lines[1:] if not source.title else lines  # the first line already became the title
    sentence = re.split(r"(?<=[.!?])\s", " ".join(" ".join(rest).split()), maxsplit=1)[0]
    return Candidate(title, (sentence or title)[:200], [], text, state=source_state(source.title, source.text, state_chars))


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
            reply_chars=DECIDE_CHARS,
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
        reply_chars=DECIDE_CHARS,
    )
    return out.name, out.description


def match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
    """CROW step 2: an existing note on the same subject, if any."""
    if not pool:
        return None
    out = ctx.ask_json("librarian/find_match", Match, note=ctx.state(cand), notes=list_notes(pool), reply_chars=DECIDE_CHARS)
    found = next((n for n in pool if n.rel == (out.match or "").strip().strip("/")), None)
    ctx.decide("match", "llm", found.rel if found else "none")
    return found


def consolidate(ctx: StepContext, note: Note, cand: Candidate) -> bool:
    """CROW step 3: modify the existing note, or write a new one next to it."""
    out = ctx.ask_json("librarian/consolidate", Consolidation, note=ctx.state(cand), existing=note.full_text(), reply_chars=DECIDE_CHARS)
    ctx.decide("consolidate", "llm", out.decision)
    return out.decision == "modify"


def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
    """CROW step 3, modify branch: rewrite the existing note with the incoming one (the candidate), or else with the source.

    The incoming text is cut to what is left of the call after the existing note; a cut is logged.
    """
    existing = note.main_body()
    budget = max(0, ctx.cfg.effective_read_chars - PROMPT_ROOM - len(existing))
    cand = ctx.candidate
    if cand is not None:
        body, title, summary, tags = cand.body, cand.title, cand.summary or "(none)", ", ".join(cand.tags) or "(none)"
    else:
        body, title, summary, tags = source.text, source.title or "(none)", "(none)", "(none)"
    if len(body) > budget:
        log.warning("merge: the incoming text is cut from %d to %d characters to fit the call", len(body), budget)
    came = ", ".join(x for x in (source.title or title, source.resource) if x)
    reply_chars = len(existing) + ctx.cfg.effective_note_chars
    prompt, field = "librarian/merge", "incoming_body"
    values: dict[str, Any] = {
        "title": note.title,
        "summary": note.summary,
        "tags": ", ".join(note.tags) or "(none)",
        "body": existing,
        "incoming_title": title,
        "incoming_summary": summary,
        "incoming_tags": tags,
        "incoming_body": body[:budget],
        "incoming_from": f"written from {came}" if cand is not None else f"the source {came}",
        "source_text": "" if cand is not None else body[:budget],
        # The merge rewrites the whole existing note: its cap grows with it (a note of 36k characters failed at 3 times note_chars).
        "reply_chars": reply_chars,
    }
    if len(source.text) > CONSOLIDATE_SOURCE_MAX * ctx.cfg.effective_note_chars:
        # A long source: the candidate is a summary of it and misses values; read the text itself (no guard, no candidate call).
        prompt, field, values = "librarian/merge_source", "source_text", _source_values(note, existing, source, budget, reply_chars)
        draft = ctx.ask_json(prompt, NoteDraft, **values)
        if draft.body.strip() == existing.strip() and incoming_values(source.text[:budget]) - incoming_values(existing):
            log.warning("merge: the draft of %s is unchanged although the source has new values: asking again", note.rel)
            draft = ctx.ask_json(prompt, NoteDraft, **values)
    else:
        draft = ctx.ask_json(prompt, NoteDraft, **values)
        new = incoming_values(body[:budget]) - incoming_values(existing)
        kept = len(new & incoming_values(draft.body))
        if draft.body.strip() == existing.strip() or (new and kept * 2 < len(new)):
            log.warning(
                "merge: the draft of %s fails the guard (%d of %d new values kept, unchanged=%s): asking again from the source",
                note.rel,
                kept,
                len(new),
                draft.body.strip() == existing.strip(),
            )
            prompt, field, values = "librarian/merge_source", "source_text", _source_values(note, existing, source, budget, reply_chars)
            draft = ctx.ask_json(prompt, NoteDraft, **values)
    # The merge must keep the values of the existing note: where it drops too many, ask once more naming them.
    old = incoming_values(existing)
    lost = old - incoming_values(draft.body)
    if lost and len(lost) > LOST_MAX * len(old):
        log.warning("merge: the draft of %s drops %d of %d values of the existing note: asking again", note.rel, len(lost), len(old))
        listed = _lost_originals(existing, lost)
        values[field] = values[field] + MERGE_LOST_NOTE + "\n".join(f"- {v}" for v in listed)
        again = ctx.ask_json(prompt, NoteDraft, **values)
        if len(old - incoming_values(again.body)) < len(lost):
            draft = again
    _warn_if_long("merge", draft.title, draft.body, ctx.cfg)
    return draft


def _source_values(note: Note, existing: str, source: Source, budget: int, reply_chars: int) -> dict[str, Any]:
    return {
        "title": note.title,
        "summary": note.summary,
        "tags": ", ".join(note.tags) or "(none)",
        "body": existing,
        "source_text": source.text[:budget],
        "length_rule": "",
        "reply_chars": reply_chars,
    }


_CODE = re.compile(r"\b[A-Z]{2,4}-\d{3,5}\b")
_VALUE_NUMBER = re.compile(r"(?<![\w-])\d{1,3}(?:[ .,\u00a0\u202f]\d{3}(?!\d))+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
_NAME = re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b")
_DROP = re.compile(r"[\s.,\u00a0\u202f$\u20ac\u00a3\u00a5]")


def _value_tokens(text: str) -> list[str]:
    found = [*_CODE.findall(text), *_NAME.findall(text)]
    found += [n for n in _VALUE_NUMBER.findall(text) if sum(c.isdigit() for c in n) >= 2]
    return found


def incoming_values(text: str) -> set[str]:
    """Normalised tokens of a text that identify a value: codes, numbers of two digits or more, two-word names."""
    return {_DROP.sub("", v.lower()) for v in _value_tokens(text)}


def _lost_originals(text: str, lost: set[str]) -> list[str]:
    """The lost values as they appear in the text (original substrings, in order of appearance, at most LOST_LISTED)."""
    spans = sorted(
        (m.start(), m.group())
        for rx in (_CODE, _NAME, _VALUE_NUMBER)
        for m in rx.finditer(text)
        if _DROP.sub("", m.group().lower()) in lost and (rx is not _VALUE_NUMBER or sum(c.isdigit() for c in m.group()) >= 2)
    )
    seen: set[str] = set()
    out: list[str] = []
    for _, raw in spans:
        norm = _DROP.sub("", raw.lower())
        if norm not in seen:
            seen.add(norm)
            out.append(raw)
    return out[:LOST_LISTED]


def relate(ctx: StepContext, note: Note, others: list[Note]) -> list[Note]:
    """Correlation: notes worth a See-also link from this one."""
    out = ctx.ask_json(
        "librarian/relate",
        Related,
        note=note.compact(ctx.cfg.classifier.state_chars),
        notes=list_notes(others),
        max_links=ctx.cfg.max_links,
        reply_chars=DECIDE_CHARS,
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
            reply_chars=DECIDE_CHARS,
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
