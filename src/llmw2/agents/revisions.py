# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A revision of the one document a note cites replaces the note, and the old body goes to the archive (option `revisions = "replace"`).

Code decides that the source and the cited copy are the same document, and writes the new body: the summary of the
source, what changed ("previously ..."), and the sentences of the old body that still hold. With a classifier (CROW)
two typed questions, one of identity and one per kept sentence, guard it; classic has none of them.
"""

from __future__ import annotations

import difflib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Literal

from llmw2.agents.data import NoteDraft, Source
from llmw2.agents.steps_classic import _paragraphs, _sentence_changes, _sentences, _shingles, _units, incoming_values
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.store import ARCHIVE_DIR, body_hash
from llmw2.bundle.tree import Note
from llmw2.errors import ModelError

if TYPE_CHECKING:
    from llmw2.agents.steps import StepContext

__all__ = [
    "ARCHIVE_DIR",
    "CHANGES_MAX_LINES",
    "HISTORY",
    "HISTORY_HINT",
    "HISTORY_MIN",
    "IDENTITY",
    "KEPT_MAX_CHARS",
    "NEW_IN_OLD",
    "OLD_IN_NEW",
    "SAME_DOC_MIN",
    "STILL",
    "STILL_MIN",
    "body_hash",
    "changes_section",
    "containment",
    "kept_sentences",
    "normalize_name",
    "removed_strings",
    "revise",
    "same_document",
    "value_changes",
]

log = logging.getLogger(__name__)

NEW_IN_OLD = 0.6  # the share of the source's 8-word shingles in the cited copy, at least
OLD_IN_NEW = 0.9  # the share of the copy's shingles in the source, at least
SAME_DOC_MIN = 0.7  # the classifier's confidence that the source is the same document, at least
STILL_MIN = 0.3  # the classifier's confidence that a sentence of the old body still holds, at least
HISTORY_MIN = 0.5  # the probability that a question asks about an earlier version, above which the archive is read
CHANGES_MAX_LINES = 60
KEPT_MAX_CHARS = 8_000
IDENTITY_SHOWN = 40  # most value changes the identity question reads
OPENING_CHARS = 1_200
SENTENCE_MAX = 400
KEPT_WORKERS = 8

IDENTITY = (
    "The changes from A to B replace what the document is about: its subject, case, item, period, or the entities it "
    "evaluates or compares; not only figures, codes, dates or wording about the same subject."
)
STILL = (
    "The passage of the new version states the same facts as the sentence, and no value in the sentence (number, code, "
    "date, name) differs from what the passage says."
)
HISTORY_HINT = (  # added to a question the classifier reads as historical, after the archived versions join the notes
    "(This question asks about an earlier version: answer with the value that version gave, from the archived note or "
    'from a value marked "previously", not with the current one; say what replaced it if the notes tell.)'
)
HISTORY = "The question asks what a value or fact was in an earlier version of a document, before a revision or change."

_EXTENSION = re.compile(r"\.(?:md|pdf|docx|txt)$")
_ORDER_PREFIX = re.compile(r"^\d+[-_ ]")
_MARKER = re.compile(
    r"[\s\-\u2013\u2014_]*(?<![a-z0-9])"
    r"(?:rev\.?\s*\d+|revision\s*\d+|revisione\s*\d+|version\s*\d+|versione\s*\d+|v\d+(?:\.\d+)*|draft(?:\s*\d+)?|bozza(?:\s*\d+)?)"
    r"(?![a-z0-9])"
)
_LIST_MARK = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_NON_ALNUM = re.compile(r"[\W_]+")
_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
    "|gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"
)
_DATE = re.compile(rf"\b\d{{1,2}} (?:{_MONTHS}) \d{{4}}\b|\b(?:{_MONTHS}) \d{{1,2}}, \d{{4}}\b", re.IGNORECASE)


def normalize_name(text: str) -> str:
    """A title or file name without extension, order prefix and revision marker, so two versions of it are equal."""
    text = _EXTENSION.sub("", text.strip().lower())
    text = _ORDER_PREFIX.sub("", text)
    text = _MARKER.sub(" ", text)
    return _NON_ALNUM.sub(" ", text).strip()


def containment(old: str, new: str) -> tuple[float, float]:
    """(share of the new text's 8-word shingles in the old, share of the old's in the new); 0.0 if one has none."""
    a, b = _shingles(old), _shingles(new)
    if not a or not b:
        return 0.0, 0.0
    common = len(a & b)
    return common / len(b), common / len(a)


def _file_name(path: str) -> str:
    return normalize_name(re.split(r"[/\\]", path.strip())[-1])


def _same_name(ref: dict[str, object], source: Source) -> str:
    """Why a cited source is not the source's document ("" if it is)."""
    title, new_title = normalize_name(str(ref.get("title") or "")), normalize_name(source.title or "")
    if not title or not new_title:
        return "a title is missing"
    if title != new_title:
        return "the titles differ"
    origin = ref.get("origin")
    old_file = str(origin.get("id") or "") if isinstance(origin, dict) else ""
    new_file = source.origin.id if source.origin is not None and source.origin.id else source.resource or ""
    if old_file and new_file and _file_name(old_file) != _file_name(new_file):
        return "the file names differ"
    return ""


def same_document(ctx: StepContext, note: Note, source: Source) -> tuple[str, str] | None:
    """The cited raw copy the source revises (resource, body of the copy), or None: then the source is merged."""
    if ctx.cfg.revisions != "replace":
        return None

    def no(reason: str, decider: Literal["code", "classifier"] = "code") -> None:
        ctx.decide("revise", decider, f"merge: {reason}")
        log.info("revise: %s is merged, not replaced: %s", note.rel, reason)

    generated = note.frontmatter.get("generated")
    saved = generated.get("body_hash") if isinstance(generated, dict) else None
    if not saved or saved != body_hash(note.main_body()):
        no("the body was not written by code alone")
        return None
    cited = note.frontmatter.get("sources")
    refs = [r for r in cited if isinstance(r, dict)] if isinstance(cited, list) else []
    paths = [ctx.cfg.bundle / str(r.get("resource") or "").lstrip("/") for r in refs]
    if not refs or any(not str(r.get("resource") or "").startswith("/raw/") or not p.is_file() for r, p in zip(refs, paths, strict=True)):
        no("a source of the note is not a raw copy in the bundle")
        return None
    for ref in refs:
        if why := _same_name(ref, source):
            no(f"a source of the note is another document: {why}")
            return None
    old = OKFDocument.parse(paths[-1].read_text(encoding="utf-8")).body
    new_in_old, old_in_new = containment(old, source.text)
    if new_in_old < NEW_IN_OLD or old_in_new < OLD_IN_NEW:
        no(f"the source does not contain the latest copy enough ({new_in_old:.2f}, {old_in_new:.2f})")
        return None
    resource = str(refs[-1]["resource"])
    if ctx.classifier is None:
        ctx.decide("revise", "code", "replace")
        return resource, old
    changes = value_changes(old, source.text)[:IDENTITY_SHOWN]
    lines = []
    for before, after, sentence in changes:
        lead = " ".join(sentence[: max(sentence.find(after), 0)].split()[-6:])
        lines.append(f"- \u2026{lead} [{before} \u2192 {after}]")
    title = str(refs[-1].get("title") or "")
    state = (
        f"Document A, title: {title}\nOpening of A:\n{old[:OPENING_CHARS]}\n\n"
        f"Document B, title: {source.title}\nOpening of B:\n{source.text[:OPENING_CHARS]}\n\n"
        f"What B changes against A ({len(lines)} shown, old \u2192 new):\n" + "\n".join(lines)
    )
    try:
        p = ctx.classifier.nouls(state, {"q": IDENTITY}, op="revise/identity")["q"]
    except ModelError as e:
        no(f"the classifier failed: {e}", "classifier")
        return None
    if 1 - p < SAME_DOC_MIN:
        no(f"the classifier says the subject changed ({1 - p:.2f})", "classifier")
        return None
    ctx.decide("revise", "classifier", "replace", 1 - p)
    return resource, old


def _is_value(words: list[str]) -> bool:
    return any(c.isdigit() for w in words for c in w) or any(w[:1].isupper() for w in words)


def value_changes(old: str, new: str) -> list[tuple[str, str, str]]:
    """(old words, new words, the new sentence) for each short word-level replacement of a figure or a name."""
    found: list[tuple[str, str, str]] = []
    for change in _sentence_changes(old, new):
        if not change.before:
            continue
        before, after = change.before.split(), change.after.split()
        sentences = [" ".join(s.split()) for s in _sentences([change.after])]
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
            if tag != "replace" or i2 - i1 > 6 or j2 - j1 > 6 or not _is_value([*before[i1:i2], *after[j1:j2]]):
                continue
            was, now = " ".join(before[i1:i2]).strip(",.:;"), " ".join(after[j1:j2]).strip(",.:;")
            sentence = next((s for s in sentences if now in s), " ".join(change.after.split()))
            found.append((was, now, sentence[:SENTENCE_MAX]))
    return found


def changes_section(old: str, new: str) -> str:
    """The values the new text changes, one line each, with the old value ("previously"); empty if it changes none."""
    lines: list[str] = []
    for was, now, sentence in value_changes(old, new):
        line = f"- {sentence.replace(now, f'**{now}**', 1)} (previously: {was})"
        if line not in lines:
            lines.append(line)
    if not lines:
        return ""
    return "## Changes from the previous version\n\n" + "\n".join(lines[:CHANGES_MAX_LINES])


def removed_strings(old: str, new: str) -> set[str]:
    """The old values that the new text no longer holds."""
    return {was for was, _, _ in value_changes(old, new) if was not in new}


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _passage(sentence: str, source_text: str) -> str:
    """The paragraph of the source that shares most with the sentence (values weigh ten times a word)."""
    values, words = incoming_values(sentence), _words(sentence)
    return max(
        _paragraphs(source_text),
        key=lambda p: 10 * len(values & incoming_values(p)) + len(words & _words(p)),
        default="",
    )


def kept_sentences(ctx: StepContext, old_body: str, source_text: str, new_body: str, removed: set[str]) -> list[str]:
    """The sentences of the old body that the revision leaves true: they hold a value the new body lacks, and the source keeps it."""
    in_source, in_new = incoming_values(source_text), incoming_values(new_body)
    lowered = source_text.lower()
    candidates: list[str] = []
    for a, b in _units(old_body):
        for sentence in _sentences([old_body[a:b]]):
            values = incoming_values(sentence)
            if not values or not values <= in_source or values <= in_new:
                continue
            if any(r in sentence for r in removed):
                continue
            if any(m.group(0).lower() not in lowered for m in _DATE.finditer(sentence)):
                continue
            candidates.append(sentence)
    classifier = ctx.classifier
    if classifier is not None and candidates:

        def still(sentence: str) -> bool:
            state = f"Sentence (previous version):\n{sentence}\n\nPassage of the new version:\n{_passage(sentence, source_text)}"
            try:
                return classifier.nouls(state, {"q": STILL}, op="revise/still_holds")["q"] >= STILL_MIN
            except ModelError:
                return False

        with ThreadPoolExecutor(max_workers=KEPT_WORKERS) as pool:
            candidates = [s for s, keep in zip(candidates, pool.map(still, candidates), strict=True) if keep]
    kept: list[str] = []
    used = 0
    for sentence in candidates:
        if used + len(sentence) > KEPT_MAX_CHARS:
            break
        kept.append(sentence)
        used += len(sentence)
    return kept


def revise(ctx: StepContext, note: Note, source: Source) -> NoteDraft | None:
    """The note rewritten from the source (summary, changes, sentences kept), or None: then the source is merged."""
    cand = ctx.candidate
    if cand is None:
        return None
    found = same_document(ctx, note, source)
    if found is None:
        return None
    _, old = found
    body = cand.body.rstrip()
    if changes := changes_section(old, source.text):
        body += "\n\n" + changes
    kept = kept_sentences(ctx, note.main_body(), source.text, body, removed_strings(old, source.text))
    if kept:
        body += "\n\n## From the previous version\n\n" + "\n".join("- " + _LIST_MARK.sub("", s) for s in kept)
    return NoteDraft(title=note.title, summary=cand.summary, tags=list(cand.tags), body=body, fields=dict(cand.fields))
