# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The classic steps: every decision of an ingest and of a question is taken by the LLM.

Public, so a custom step can reuse them: `replace(CLASSIC, summarize=lambda ctx, s: my_cleanup(classic.summarize(ctx, s)))`.
"""

from __future__ import annotations

import difflib
import logging
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator

from llmw2.agents.base import clean_tags, list_folders, list_notes
from llmw2.agents.data import Candidate, NoteDraft, Route, Source
from llmw2.agents.state import source_state, split_blocks
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.files import extract_text
from llmw2.bundle.tree import Folder, Note
from llmw2.config import PROMPT_ROOM
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
MERGE_UNCHANGED_NOTE = (
    "\n\nThe previous reply returned the note unchanged, but the source has these values the note lacks; "
    'update the note with each of them, stating the value it replaces ("previously …") where it replaces one:\n'
)
REVISION_MIN = 0.5  # a source sharing this much of its 8-word shingles with a raw copy the note cites revises that copy
MIN_CHUNK = 8_000  # the least of the text an edits merge reads per call, however full the note
EDITS_CHARS = 16_000  # about how long the reply of an edits merge is: a list of edits, not a note
LOCATE_BY_CLASSIFIER = True  # the typed classifier, not only code, picks the note unit a change updates
LOCATE_TOP = 15  # a change is located among this many units of the note, the best by a cheap prefilter
LOCATE_MARGIN = 0.1  # the classifier's pick must lead the next option by this much of probability
CONTEXT_CHARS = 600  # the paragraph a change is in goes with it, cut to this
SOFT_CAP = 1.2 # a note body longer than the length asked for, times this, is logged, not cut

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
    _warn_if_long("summarize", cand.title, cand.body, ctx.cfg.target_note_chars(len(source.text)))
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
    parts, written = [], []
    for k, block in enumerate(blocks, 1):
        heading = block.heading or "(no heading)"
        cap = min(room // n, len(block.text) // 2)  # proportional: the notes of all parts still fit in the combine call
        if cfg.note_length == "fixed":
            cap = min(cap, cfg.effective_note_chars // 2)
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
        written.append(notes)
        parts.append(f"Part {k} of {n} — {heading}\n{notes}")
    target = cfg.target_note_chars(len(source.text))
    part_notes = "\n\n".join(parts)
    draft = ctx.ask_json(
        "librarian/summarize_combine",
        NoteDraft,
        source_title=source.title or "(none)",
        source_resource=source.resource or "(none)",
        part_notes=part_notes,
        note_chars=target,
        **({} if cfg.note_length == "fixed" else {"reply_chars": math.ceil(1.2 * target)}),  # fixed: the default cap of a note
    )
    wanted = incoming_values("\n\n".join(written))  # the notes only: a heading such as "Green Coffee" is not a value
    missing = wanted - incoming_values(draft.body)
    if missing:  # only logged: nothing is asked again
        log.warning("summarize: the note of %r lacks %d of %d values of its part notes", draft.title, len(missing), len(wanted))
    return draft


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


def _warn_if_long(op: str, title: str, body: str, asked: int) -> None:
    """The length of a note is asked for, not enforced: a body well over the wish is only logged."""
    if len(body) > SOFT_CAP * asked:
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
    long_source = len(source.text) > CONSOLIDATE_SOURCE_MAX * ctx.cfg.effective_note_chars
    if long_source and ctx.cfg.merge_mode in ("edits", "located"):
        copy = _revised_copy(ctx, note, source) if ctx.cfg.merge_mode == "located" else None
        if copy is not None:
            draft = merge_located(ctx, note, source, copy[1])
            _warn_if_long("merge", draft.title, draft.body, ctx.cfg.target_note_chars(len(source.text), len(existing)))
            return draft
        revised = None if ctx.cfg.merge_mode == "located" else _revision_diff(ctx, note, source)  # located: no cited copy is revised
        share = ctx.cfg.target_note_chars(len(source.text))
        if revised is not None or len(existing) >= share:
            draft = merge_edits(ctx, note, source, revised=revised)
            _warn_if_long("merge", draft.title, draft.body, ctx.cfg.target_note_chars(len(source.text), len(existing)))
            return draft
        log.info("merge: %s takes the rewrite: the source revises no cited copy and the note (%d characters) is under its share (%d)",
                 note.rel, len(existing), share)  # fmt: skip
    cut = len(source.text) if long_source else len(body)  # a long source is merged from its text, not from the candidate
    if cut > budget:
        log.warning("merge: the incoming text is cut from %d to %d characters to fit the call", cut, budget)
    came = ", ".join(x for x in (source.title or title, source.resource) if x)
    reply_chars = len(existing) + ctx.cfg.target_note_chars(len(source.text))
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
        "note_chars": ctx.cfg.target_note_chars(len(source.text), len(existing)),
        "incoming_from": f"written from {came}" if cand is not None else f"the source {came}",
        "source_text": "" if cand is not None else body[:budget],
        # The merge rewrites the whole existing note: its cap grows with it (a note of 36k characters failed at 3 times note_chars).
        "reply_chars": reply_chars,
    }
    if long_source:
        # A long source: the candidate is a summary of it and misses values; read the text itself (no guard, no candidate call).
        prompt, field, values = "librarian/merge_source", "source_text", _source_values(note, existing, source, budget, reply_chars)
        draft = ctx.ask_json(prompt, NoteDraft, **values)
        missing = incoming_values(source.text[:budget]) - incoming_values(existing)
        if draft.body.strip() == existing.strip() and missing:
            log.warning("merge: the draft of %s is unchanged although the source has new values: asking again", note.rel)
            listed = _lost_originals(source.text[:budget], missing)
            values["source_text"] += MERGE_UNCHANGED_NOTE + "\n".join(f"- {v}" for v in listed)
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
    _warn_if_long("merge", draft.title, draft.body, ctx.cfg.target_note_chars(len(source.text), len(existing)))
    return draft


class Edit(BaseModel):
    find: str
    replace: str


class Addition(BaseModel):
    section: str
    text: str


class Edits(BaseModel):
    edits: list[Edit] = []
    additions: list[Addition] = []


_UNSET: Any = object()


def merge_edits(ctx: StepContext, note: Note, source: Source, revised: Any = _UNSET) -> NoteDraft:
    """Merge a long source as edits the model proposes and code applies; title, summary and tags stay the note's.

    The model reads the changes of the source against the raw copy it revises, else the source itself, in chunks that
    fit next to the note. An edit is applied only if its `find` occurs once and its new values are in the text read;
    the values it removes are kept in place ("previously ...").
    """
    existing = note.main_body()
    body = existing
    if revised is _UNSET:
        revised = _revision_diff(ctx, note, source)
    text = source.text if revised is None else revised
    kind = "a new source" if revised is None else "the changes between the previous version of this source and the new one"
    budget = max(MIN_CHUNK, ctx.cfg.effective_read_chars - PROMPT_ROOM - len(existing))
    chunks = [b.text for b in split_blocks(text, budget)]
    applied, rejected = 0, {"not found": 0, "ambiguous": 0, "invented": 0}

    def ask(chunk: str, changes: str) -> None:
        nonlocal body, applied
        out = ctx.ask_json(
            "librarian/merge_edits", Edits, title=note.title, summary=note.summary, body=body, changes=changes, kind=kind,
            reply_chars=EDITS_CHARS,
        )  # fmt: skip
        known = incoming_values(chunk)
        for edit in out.edits:
            body, why = _apply_edit(body, edit, known)
            if why:
                rejected[why] += 1
                log.debug("merge: %s rejected for %s: %r", why, note.rel, edit.find[:120])
            else:
                applied += 1
        for add in out.additions:
            body, why = _apply_addition(body, add, known)
            if why:
                rejected[why] += 1
                log.debug("merge: %s rejected for %s: %r", why, note.rel, add.text[:120])
            else:
                applied += 1

    for chunk in chunks:
        ask(chunk, chunk)
    missing = incoming_values(text) - incoming_values(existing)
    if chunks and body == existing and missing:
        log.warning("merge: the edits for %s changed nothing although the source has new values: asking again", note.rel)
        listed = _lost_originals(text, missing)
        ask(chunks[0], chunks[0] + MERGE_UNCHANGED_NOTE + "\n".join(f"- {v}" for v in listed))
    refused = sum(rejected.values())
    (log.warning if refused else log.info)(
        "merge: %s by edits: %d applied, %d rejected (not found %d, ambiguous %d, invented %d)",
        note.rel, applied, refused, rejected["not found"], rejected["ambiguous"], rejected["invented"],
    )  # fmt: skip
    return NoteDraft(title=note.title, summary=note.summary, tags=list(note.tags), body=body)


class UnitText(BaseModel):
    id: str
    text: str


class Located(BaseModel):
    units: list[UnitText] = []
    additions: list[Addition] = []


@dataclass
class Change:
    before: str  # the old sentences ("" for a new paragraph)
    after: str  # the new ones
    context: str  # the new paragraph they are in ("" if it is `after`)


_LIST_ITEM = re.compile(r"\s*([-*+] |\d+[.)] )")
_HEADING = re.compile(r"#+\s")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


def _sentences(paragraphs: list[str]) -> list[str]:
    """A sentence ends at a newline, or at `.`, `!`, `?` followed by space; a table row is one sentence."""
    out: list[str] = []
    for par in paragraphs:
        for line in par.split("\n"):
            out += [s.strip() for s in ([line] if _row_cells(line) is not None else _SENTENCE_BREAK.split(line)) if s.strip()]
    return out


def _joined(sentences: list[str]) -> str:
    return ("\n" if any(_row_cells(s) is not None for s in sentences) else " ").join(sentences)


def _sentence_changes(old: str, new: str) -> list[Change]:
    """What the new text changes against the old: each new paragraph, and the sentences a changed paragraph replaces."""
    before, after = _paragraphs(old), _paragraphs(new)
    changes: list[Change] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag == "insert":
            changes += [Change("", par, "") for par in after[j1:j2]]
        elif tag == "replace":
            old_s, new_s = _sentences(before[i1:i2]), _sentences(after[j1:j2])
            for t, a1, a2, b1, b2 in difflib.SequenceMatcher(None, old_s, new_s, autojunk=False).get_opcodes():
                if t == "equal" or b1 == b2:  # a deleted sentence gives nothing
                    continue
                text = _joined(new_s[b1:b2])
                par = next((p.strip() for p in after[j1:j2] if new_s[b1] in p), "")
                changes.append(Change(_joined(old_s[a1:a2]), text, "" if par == text else par[:CONTEXT_CHARS]))
    return changes


def _units(body: str) -> list[tuple[int, int]]:
    """Spans of the note's units: a block between blank lines, else each heading, table row or list item of it on its own."""
    spans: list[tuple[int, int]] = []
    at = 0
    for cut, resume in [*((m.start(), m.end()) for m in re.finditer(r"\n\s*\n", body)), (len(body), len(body))]:
        block = body[at:cut]
        lead = re.match(r"(?:[ \t]*\n)*", block)
        start, end = at + (lead.end() if lead else 0), at + len(block.rstrip())
        at = resume
        if end <= start:
            continue
        lines: list[tuple[int, int, str]] = []
        pos = start
        for line in body[start:end].split("\n"):
            lines.append((pos, pos + len(line), line))
            pos += len(line) + 1
        each = all(_row_cells(x) is not None or _LIST_ITEM.match(x) for _, _, x in lines[1:])
        group: tuple[int, int] | None = None
        for a, b, line in lines:
            if each or _HEADING.match(line):
                if group:
                    spans.append(group)
                    group = None
                spans.append((a, b))
            else:
                group = (group[0] if group else a, b)
        if group:
            spans.append(group)
    return spans


def _shingles3(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", text.lower())
    return {tuple(words[i : i + 3]) for i in range(len(words) - 2)}


def _locate(ctx: StepContext, change: Change, texts: list[str], failed: list[bool]) -> tuple[int, str]:
    """The index of the unit a change updates and who found it ("code" or "classifier"), or (-1, "") if it updates none.

    `failed` is set (once per merge) when the classifier raises: the rest is located by code.
    """
    values, grams = incoming_values(change.before), _shingles3(change.before)
    held = [incoming_values(t) for t in texts]
    heading = [bool(_HEADING.match(t)) for t in texts]
    removed = values - incoming_values(change.after)
    holders = [i for i, h in enumerate(held) if removed and not heading[i] and removed <= h]
    if len(holders) == 1:
        return holders[0], "code"
    scored = sorted((-(10 * len(values & held[i]) + len(grams & _shingles3(t))), i) for i, t in enumerate(texts) if not heading[i])
    top = [i for score, i in scored if score < 0][:LOCATE_TOP]
    if not top:
        return -1, ""
    if ctx.classifier is not None and LOCATE_BY_CLASSIFIER and not failed[0]:
        options = {f"u{k}": texts[i][:600] for k, i in enumerate(top, 1)}
        options["new"] = "None of these: the change adds a fact the note does not state"
        state = f"A change to a document.\nBEFORE:\n{change.before[:3000]}\nAFTER:\n{change.after[:3000]}"
        try:
            ask = "Which paragraph of the note states what this change updates?"
            answer = ctx.classifier.choice(state, ask, options, op="merge/locate")
        except Exception as e:
            failed[0] = True
            log.warning("merge: the classifier failed locating a change, code locates the rest: %s", e)
        else:
            probs = answer.probabilities
            lead = probs.get(answer.choice, 0.0) - max((p for k, p in probs.items() if k != answer.choice), default=0.0)
            if answer.choice in options and answer.choice != "new" and lead >= LOCATE_MARGIN - 1e-9:
                return top[int(answer.choice[1:]) - 1], "classifier"
            return -1, ""
    return (top[0], "code") if removed & held[top[0]] else (-1, "")


def merge_located(ctx: StepContext, note: Note, source: Source, old_text: str) -> NoteDraft:
    """Merge a revision of a raw copy the note cites: code splits it into changes and finds the note unit each updates.

    The model rewrites only the units that have changes (by id) and writes the facts the note lacks as additions.
    A rewritten unit is applied only if it holds no value that its old text and its changes lack; the values it
    replaces are kept in place ("previously ..."). Title, summary and tags stay the note's.
    """
    body = note.main_body()
    spans = _units(body)
    texts = [body[a:b] for a, b in spans]
    by_unit: dict[int, list[Change]] = {}
    facts: list[Change] = []
    found = {"code": 0, "classifier": 0}
    dropped, failed = 0, [False]
    present = incoming_values(body)
    changes = _sentence_changes(old_text, source.text)
    for change in changes:
        at, how = _locate(ctx, change, texts, failed) if change.before else (-1, "")
        if at >= 0:
            by_unit.setdefault(at, []).append(change)
            found[how] += 1
        elif incoming_values(change.after) - present:
            facts.append(change)
        else:
            dropped += 1
    if not by_unit and not facts:
        log.info(
            "merge: %s located: %d changes (0 by code, 0 by classifier, 0 additions, %d dropped), 0 units rewritten, "
            "0 additions applied, 0 rejected (invented 0, empty 0, unknown 0)",
            note.rel, len(changes), dropped,
        )  # fmt: skip
        return NoteDraft(title=note.title, summary=note.summary, tags=list(note.tags), body=body)

    def where(c: Change) -> str:
        return f"\n  (in: {c.context})" if c.context else ""

    items: list[tuple[str, int, Change | None]] = []  # (rendered, unit index or -1, fact)
    for i in sorted(by_unit):
        lines = "\n".join(f"- BEFORE: {c.before}\n  AFTER: {c.after}{where(c)}" for c in by_unit[i])
        items.append((f"[u{i + 1}]\n{texts[i]}\nChanges:\n{lines}", i, None))
    items += [(f"- {c.after}{where(c)}", -1, c) for c in facts]
    outline = "\n".join(line for line in body.split("\n") if _HEADING.match(line)) or "(no headings)"
    room = max(MIN_CHUNK, ctx.cfg.effective_read_chars - PROMPT_ROOM - len(outline))
    batches: list[list[tuple[str, int, Change | None]]] = [[]]
    used = 0
    for item in items:
        if batches[-1] and used + len(item[0]) > room:
            batches.append([])
            used = 0
        batches[-1].append(item)
        used += len(item[0])

    done: dict[int, str] = {}
    adds: list[tuple[Addition, set[str]]] = []
    rejected = {"invented": 0, "empty": 0, "unknown": 0}

    def reject(why: str, text: str) -> None:
        rejected[why] += 1
        log.debug("merge: %s rejected for %s: %r", why, note.rel, text[:120])

    for batch in batches:
        sent = {i for _, i, _ in batch if i >= 0}
        added = [c for _, _, c in batch if c is not None]
        reply = int(1.3 * sum(len(texts[i]) for i in sent)) + min(sum(len(c.after) for c in added), 8_000) + 1_000
        out = ctx.ask_json(
            "librarian/merge_located", Located, title=note.title, summary=note.summary, outline=outline,
            units="\n\n".join(r for r, i, _ in batch if i >= 0) or "(none)",
            facts="\n".join(r for r, i, _ in batch if i < 0) or "(none)", reply_chars=reply,
        )  # fmt: skip
        for unit in out.units:
            i = int(unit.id[1:]) - 1 if re.fullmatch(r"u\d+", unit.id) else -1
            if i not in sent or i in done:
                reject("unknown", unit.text)
                continue
            new = unit.text.strip()
            if not new:
                reject("empty", unit.text)
                continue
            known = incoming_values("\n".join(f"{c.before}\n{c.after}\n{c.context}" for c in by_unit[i]))
            if incoming_values(new) - incoming_values(texts[i]) - known:
                reject("invented", new)
                continue
            lost = incoming_values(texts[i]) - incoming_values(new)
            done[i] = _note_removed(texts[i], new, _lost_originals(texts[i], lost)) if lost else new
        known = incoming_values("\n".join(f"{c.after}\n{c.context}" for c in added))
        adds += [(add, known) for add in out.additions]
    for i in sorted(done, reverse=True):
        body = body[: spans[i][0]] + done[i].strip("\n") + body[spans[i][1] :]
    applied = 0
    for add, known in adds:
        body, why = _apply_addition(body, add, known)
        if why:
            reject(why, add.text)
        else:
            applied += 1
    refused = sum(rejected.values())
    (log.warning if refused else log.info)(
        "merge: %s located: %d changes (%d by code, %d by classifier, %d additions, %d dropped), %d units rewritten, "
        "%d additions applied, %d rejected (invented %d, empty %d, unknown %d)",
        note.rel, len(changes), found["code"], found["classifier"], len(facts), dropped, sum(done[i] != texts[i] for i in done),
        applied, refused, rejected["invented"], rejected["empty"], rejected["unknown"],
    )  # fmt: skip
    return NoteDraft(title=note.title, summary=note.summary, tags=list(note.tags), body=body)


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = text.lower().split()
    return {tuple(words[i : i + 8]) for i in range(len(words) - 7)}


def _paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def _revised_copy(ctx: StepContext, note: Note, source: Source) -> tuple[str, str, float] | None:
    """The cited raw copy the source most resembles (resource, body, share of the source's shingles in it), or None if it revises none."""
    new = _shingles(source.text)
    cited = note.frontmatter.get("sources")
    best, best_share, best_text = "", 0.0, ""
    for ref in cited if isinstance(cited, list) else []:
        resource = str(ref.get("resource") or "") if isinstance(ref, dict) else ""
        path = ctx.cfg.bundle / resource.lstrip("/")
        if not resource.startswith("/raw/") or not path.is_file():
            continue
        old = OKFDocument.parse(path.read_text(encoding="utf-8")).body
        share = len(new & _shingles(old)) / len(new) if new else 0.0
        if share > best_share:
            best, best_share, best_text = resource, share, old
    return None if best_share < REVISION_MIN else (best, best_text, best_share)


def _revision_diff(ctx: StepContext, note: Note, source: Source) -> str | None:
    """The paragraphs the source changes against the cited raw copy it most resembles, or None if it revises none."""
    copy = _revised_copy(ctx, note, source)
    if copy is None:
        return None
    best, best_text, best_share = copy
    before, after = _paragraphs(best_text), _paragraphs(source.text)
    blocks: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag == "replace":
            blocks.append("BEFORE:\n" + "\n\n".join(before[i1:i2]) + "\nAFTER:\n" + "\n\n".join(after[j1:j2]))
        elif tag == "insert":
            blocks.append("ADDED:\n" + "\n\n".join(after[j1:j2]))
    diff = "\n\n---\n\n".join(blocks)
    log.info("merge: the source is a revision of %s (share %.2f): the model reads %d characters of changes", best, best_share, len(diff))
    return diff


def _apply_edit(body: str, edit: Edit, known: set[str]) -> tuple[str, str]:
    """The body with the edit applied, and why it was rejected ("" if it was applied)."""
    count = body.count(edit.find) if edit.find else 0
    if count != 1:
        return body, "not found" if count == 0 else "ambiguous"
    old, new = incoming_values(edit.find), incoming_values(edit.replace)
    if new - old - known:
        return body, "invented"
    replace = edit.replace
    removed = _lost_originals(edit.find, old - new)
    if removed:
        if "\n" in edit.find or "\n" in replace or _row_cells(replace) is not None:
            replace = _note_removed(edit.find, replace, removed)
        elif "previously" not in replace:
            replace += f" (previously {', '.join(removed)})"
    return body.replace(edit.find, replace, 1), ""


def _row_cells(line: str) -> list[str] | None:
    """The cells of a markdown table row (a line that starts and ends with a pipe), else None."""
    s = line.strip()
    return s[1:-1].split("|") if len(s) > 1 and s.startswith("|") and s.endswith("|") else None


def _line_key(line: str) -> str:
    cells = _row_cells(line)
    return (cells[0] if cells else line).strip().lower()


def _note_removed(find: str, replace: str, removed: list[str]) -> str:
    """The replace with "(previously ...)" put on the line (and, in a table row, the cell) that took the place of each removed value."""
    before, after = find.split("\n"), replace.split("\n")
    pair: dict[int, int] = {}  # find line -> replace line
    keys = ([_line_key(x) for x in before], [_line_key(x) for x in after])
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, *keys, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            pair.update({i1 + k: j1 + k for k in range(i2 - i1)})
    last = max((j for j, x in enumerate(after) if x.strip()), default=-1)
    notes: dict[tuple[int, int | None], list[str]] = {}  # (replace line, cell or None) -> values
    for raw in removed:
        norm = next(iter(incoming_values(raw)), "")
        i = next((i for i, x in enumerate(before) if norm in incoming_values(x)), -1)
        j, cell = pair.get(i, last), None
        if j < 0:
            continue
        target, source = _row_cells(after[j]), _row_cells(before[i]) if i in pair else None
        if target is not None:
            cell = len(target) - 1
            if source is not None and len(source) == len(target):
                cell = next((c for c, x in enumerate(source) if norm in incoming_values(x)), cell)
        notes.setdefault((j, cell), []).append(raw)
    for (j, cell), values in notes.items():
        text = f"(previously {', '.join(values)})"
        if cell is None:
            if "previously" not in after[j]:
                base = after[j].rstrip()
                after[j] = f"{base} {text}" + after[j][len(base) :]
            continue
        parts = after[j].split("|")  # a row's cells are parts[1:-1]
        base = parts[cell + 1].rstrip()
        if "previously" not in base:
            parts[cell + 1] = f"{base} {text}" + parts[cell + 1][len(base) :]
        after[j] = "|".join(parts)
    return "\n".join(after)


def _apply_addition(body: str, add: Addition, known: set[str]) -> tuple[str, str]:
    """The body with the text added at the end of the section named, or in a new one; and why it was rejected."""
    if incoming_values(add.text) - known:
        return body, "invented"
    wanted, text = add.section.strip().lstrip("#").strip().lower(), add.text.strip()  # "Pricing" names "# Pricing" too
    lines = body.split("\n")
    start, level, end = -1, 0, len(lines)
    for i, line in enumerate(lines):
        m = re.match(r"(#+)\s", line)
        if m and start < 0 and line[len(m.group(1)) :].strip().lower() == wanted:
            start, level = i, len(m.group(1))
        elif m and start >= 0 and len(m.group(1)) <= level:
            end = i
            break
    if start < 0:
        heading = add.section.strip()
        return body + "\n\n" + (heading if heading.startswith("#") else "# " + heading) + "\n" + text, ""
    head, tail = lines[:end], lines[end:]
    while len(head) > start + 1 and not head[-1].strip():
        head.pop()
    return "\n".join([*head, "", text, *([""] if tail else []), *tail]), ""


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
