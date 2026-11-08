# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Librarian files a source into the wiki.

    summarize → route → match → consolidate → merge | (name_folder →) write → relate → link

The flow is fixed; each step is a function of the `Steps` it is given (classic, CROW or custom), and the
output of every step is checked before it is used.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from functools import cached_property
from typing import Any, Literal

from llmw2.agents import echo, linking, revisions, steps_classic, steps_crow
from llmw2.agents.base import Decision, clean_tags, fields_conflict, omitted_headings, unique_objects
from llmw2.agents.data import Candidate, NoteDraft, Route, Source
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import (
    StepContext,
    Steps,
    check_consolidate,
    check_match,
    check_merge,
    check_name_folder,
    check_relate,
    check_route,
    check_summarize,
    decision_hash,
)
from llmw2.bundle.origin import content_hash
from llmw2.bundle.sheets import brief
from llmw2.bundle.store import WikiStore
from llmw2.bundle.tree import Folder, Note, slugify, within_area
from llmw2.config import WikiConfig
from llmw2.errors import ModelError, ReplyInvalid, StepError
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageReport

log = logging.getLogger(__name__)


def _first_heading(text: str) -> str:
    """The first non-empty line of a text, without its Markdown heading marks."""
    return next((line.strip().lstrip("#").strip() for line in text.splitlines() if line.strip().lstrip("#").strip()), "")


_FALLBACK_PROMPTS = (*echo.SUMMARIZE_PROMPTS, "librarian/name_folder", "shared/folder_rules")


def _name_words(text: str | None) -> list[str]:
    """The first four words of a text for a folder named after its note, a leading count ("8", "26.") left out."""
    words = (text or "").split()
    while words and not slugify(words[0].strip(".,;:()"), fallback="").replace("-", "").isalpha():
        words.pop(0)
    return words[:4] if slugify(" ".join(words[:4]), max_len=40, fallback="") else []


def _first_line(text: str) -> str:
    """The first non-empty line of a text that is not a Markdown heading (a body opens with "# Overview")."""
    return next((line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")), "")


def _unusable_title(title: str, source: Source) -> bool:
    """A title that is only the source's file name as small writers copy it, numbering included ("26 new hire
    checklist" for 26-new-hire-checklist.md, "26-new-hire-checklist-md"; not "Contract" for contract.md), or a
    placeholder with no letter or digit ("...")."""
    name = (source.title or "").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if not any(c.isalnum() for c in title):
        return True
    slug = slugify(title, fallback="")
    if slug.endswith("-md") and not any(c.isspace() for c in title.strip()):  # a file name: "26-new-hire-checklist-md"
        return True
    numbered = any(c.isdigit() for c in slug.split("-", 1)[0])
    return numbered and bool(stem) and slug in {slugify(name, fallback=""), slugify(stem, fallback="")}


__all__ = ["Candidate", "IngestResult", "Librarian", "NoteDraft", "Prepared", "Route", "Source"]


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
    parts: list[IngestResult] = field(default_factory=list)  # the other sheets of a spreadsheet, filed with this one
    # set when the limit on top-level folders sent this new note to the likeliest root: p_none and p_root (absent when BM25 chose)
    overflow: dict[str, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "usage": self.usage.to_dict(), "parts": [p.to_dict() for p in self.parts]}


@dataclass
class Prepared:
    """A source summarized and waiting to be filed: what `Librarian.prepare` hands to `Librarian.file`."""

    source: Source
    candidate: Candidate
    usage: UsageReport  # the model calls made so far for this source
    decisions: list[Decision] = field(default_factory=list)


class Librarian:
    """One instance serves one ingest, so its trace of decisions is never shared."""

    def __init__(
        self,
        store: WikiStore,
        llm: LLM,
        prompts: Prompts,
        cfg: WikiConfig,
        steps: Steps,
        classifier: Classifier | None = None,
        *,
        fixed: bool = False,
        redact: Callable[[str], str] | None = None,
    ) -> None:
        self.store = store
        self.redact = redact
        self.cfg = cfg
        self.steps = steps
        self.ctx = StepContext(llm, classifier, prompts, cfg, system_prompt="librarian/system", fixed=fixed)

    @property
    def local_writer(self) -> bool:
        """A writer on Ollama: a small model whose unusable reply (invalid, cut, or refused by a check) is worked around (the
        source text, a section, a name from the note) instead of stopping the ingest. A hosted writer's failure stops it,
        so a later run retries it, and so does an endpoint that fails (down, wrong model, timeout) on any provider."""
        return self.cfg.llm.provider == "ollama"

    @cached_property
    def hash(self) -> str:
        """What the provenance says decided: the steps, prompts and decision parameters (see `decision_hash`)."""
        return decision_hash(self.steps, self.cfg, self.ctx.prompts)

    def shown(self, text: str) -> str:
        """A text the wiki repeats outside the note's body (title, summary, a folder's name or description), through `redact`."""
        return self.redact(text) if self.redact is not None else text

    def _open_name(self, parent: Folder, name: str) -> str:
        """`name`, or the first "name 2", "name 3"... that is not the name of a folder of `parent` closed to automatic filing
        (a new folder with that name would be the closed one)."""
        pick, n = name, 2
        while (clash := parent.subfolder(slugify(pick, max_len=40, fallback="topics"))) is not None and not clash.routable:
            pick, n = f"{name} {n}", n + 1
        return pick

    def ingest(self, source: Source) -> IngestResult:
        return self.file(self.prepare(source))

    def prepare(self, source: Source) -> Prepared:
        """The part of an ingest that does not read the tree: summarize. Needs no lock, and may run in parallel."""
        read = _read(source)
        self.ctx.source = read
        with self.ctx.llm.usage.span() as used:
            try:
                cand = check_summarize(self.steps.summarize(self.ctx, read))
            except (ReplyInvalid, StepError) as e:  # a local writer that cannot summarize it: the note keeps the source text
                if not self.local_writer:
                    raise
                log.warning("summarize failed (%s): the note keeps the source text", e)
                self.ctx.decide("summarize", "code", "verbatim")
                cand = steps_classic._verbatim(read, self.cfg.classifier.state_chars)
        if self.local_writer and self.cfg.summarize:  # a small writer copies the file name or the reply format
            if _unusable_title(cand.title, read):
                heading = _first_heading(read.text)[:120]
                if heading and not _unusable_title(heading, read):
                    log.warning("summarize: the title %r is the file name or empty: %r from the text instead", cand.title, heading)
                    cand = replace(cand, title=heading)
            if not any(c.isalnum() for c in cand.summary or ""):  # a placeholder summary ("..."): the opening of the body
                cand = replace(cand, summary=_first_line(cand.body or read.text)[:200])
            cand = self._own_title_and_summary(cand, read)
        return Prepared(source, cand, used, list(self.ctx.decisions))

    def _own_title_and_summary(self, cand: Candidate, read: Source) -> Candidate:
        """A title or summary made only of words of the summarize prompts ("<a specific title written from the content>",
        "Take notes from a source") gives way to the source's own first heading and first line, which routing and folder
        names read instead."""
        prompts = self.ctx.prompts

        def mine(text: str) -> bool:
            return bool(text) and not echo.copied(text, prompts, echo.SUMMARIZE_PROMPTS)

        if echo.copied(cand.title, prompts, echo.SUMMARIZE_PROMPTS):
            lines = (_first_heading(read.text)[:120], _first_line(read.text)[:120], _first_line(cand.body)[:120])
            title = next((t for t in lines if mine(t) and not _unusable_title(t, read)), "")
            if title:
                log.warning("summarize: the title %r is copied from the prompt: %r from the text instead", cand.title, title)
                cand = replace(cand, title=title)
        if echo.copied(cand.summary, prompts, echo.SUMMARIZE_PROMPTS):
            summary = next((t for t in (_first_line(cand.body)[:200], _first_line(read.text)[:200]) if mine(t)), "")
            if summary:
                log.warning("summarize: the summary %r is copied from the prompt: %r instead", cand.summary[:80], summary[:80])
                cand = replace(cand, summary=summary)
        return cand

    def file(self, prepared: Prepared, planned: str | None = None) -> IngestResult:
        """The rest: from a fresh `load()` to the log. The caller holds the wiki's lock.

        `planned`, a folder label, is where the batch plan put the document's group: it is filed there and not routed."""
        steps, ctx = self.steps, self.ctx
        source, cand = prepared.source, prepared.candidate
        read = _read(source)  # what the steps read; the raw copy keeps `source` whole
        ctx.source = read
        ctx.candidate = cand
        ctx.decisions = list(prepared.decisions)
        with ctx.llm.usage.span() as used:
            root = self.store.load()
            placed = root.find(planned.strip("/")) if planned else None
            if placed is not None:
                ctx.decide("route", "code", placed.label)
                route = Route(placed, False, [placed])
            else:
                route = check_route(steps.route(ctx, root, cand), root, self.cfg)
                if self.cfg.mode == "crow":  # at most `cfg.max_top_folders` top-level folders, whoever opened one
                    route = steps_crow.keep_folders(ctx, root, cand, route)  # a rebuild opens none at all
                    route = steps_crow.cap_roots(ctx, root, cand, route)
            pool = unique_objects(n for f in [*route.candidates, route.folder] for n in f.notes)
            pool = [n for n in pool if n.folder is None or n.folder.is_routable]  # nothing is merged into a closed folder
            theirs = {**read.fields, **cand.fields}  # the document's fields: its own, and what the summary made of them
            mergeable = [n for n in pool if not fields_conflict(n.frontmatter.get("fields"), theirs)]
            if len(mergeable) < len(pool):  # a note whose fields differ from the document's on a shared key is not merged into
                ctx.decide("match", "code", f"{len(pool) - len(mergeable)} excluded by fields")
            match = check_match(steps.match(ctx, mergeable, cand), mergeable)
            created: list[str] = []
            merging = match is not None and check_consolidate(steps.consolidate(ctx, match, cand))
            revised, archived = False, ""
            deviated: dict[str, float] | None = None
            if match is not None and merging:
                draft = revisions.revise(ctx, match, read) if self.cfg.revisions == "replace" else None
                if draft is not None:
                    self.store.check_unchanged(match)  # the revision was made from the note as read: not over a hand edit
                    archived = self.store.archive_note(match)
                    revised = True
                else:
                    try:
                        draft = check_merge(steps.merge(ctx, match, read))
                    except (ReplyInvalid, StepError) as e:  # a local writer that cannot merge: the note becomes a section
                        if not self.local_writer:
                            raise
                        log.warning("merge into %s failed (%s): the incoming note is added as a section", match.rel, e)
                        ctx.decide("merge", "code", "appended")
                        draft = steps_classic._appended(match, match.main_body(), cand, read)
                self.store.check_unchanged(match)  # the draft merges what was read: a note edited since is left as it is
                raw, ref = self.save_source(source, cand)
                note = self.store.update_note(
                    match,
                    title=self.shown(draft.title).strip() or "Untitled",
                    summary=self.shown(draft.summary),
                    body=draft.body,
                    tags=clean_tags(draft.tags),
                    source=ref,
                    fields={**read.fields, **draft.fields},
                    previous_version=archived or None,
                    omitted=omitted_headings(read.text, draft.body, [] if revised else _earlier(match)),
                )
                action: Literal["created", "merged"] = "merged"
            else:
                folder = route.folder
                if route.create:
                    for name, description in route.create:
                        folder, is_new = self.store.create_folder(folder, name, description)
                        created += [folder.label] if is_new else []
                elif route.new and ctx.can_create(folder):
                    failed: Exception | None = None
                    try:
                        name, description = check_name_folder(steps.name_folder(ctx, folder, cand))
                    except (ReplyInvalid, StepError) as e:  # a local writer that cannot name it: named after the note
                        if not self.local_writer:
                            raise
                        name, description, failed = "", "", e
                    # a small writer's name copied from the prompt ("suppliers" for a coffee roaster) or from the
                    # parent's path is no name of the note: named after the note too
                    shown = ctx.state(cand)
                    own = echo.own_name(name, shown, folder.label.replace("/", "-").split("-")) if self.local_writer else name
                    if not own:
                        stem = (read.title or "").rsplit("/", 1)[-1].rsplit(".", 1)[0]
                        texts = (cand.title, cand.summary, stem)
                        # the first four words, leading counts left out, and never a cut made only of prompt words
                        cuts = [_name_words(t) for t in texts]
                        words = next((c for c in cuts if c and not echo.copied(" ".join(c), ctx.prompts, _FALLBACK_PROMPTS)), [])
                        own = " ".join(words) or "notes"
                        log.warning("name_folder %s: the folder is named after the note, %r", failed or f"gave {name!r}", own)
                        ctx.decide("name_folder", "code", own)
                    if failed or (self.local_writer and not echo.own_text(description, shown, ctx.prompts, "librarian/name_folder")):
                        description = cand.summary
                    folder, is_new = self.store.create_folder(folder, self._open_name(folder, self.shown(own)), self.shown(description))
                    created += [folder.label] if is_new else []
                raw, ref = self.save_source(source, cand)
                note = self.store.write_note(
                    folder,
                    title=self.shown(cand.title).strip() or "Untitled",
                    summary=self.shown(cand.summary),
                    body=cand.body,
                    tags=clean_tags(cand.tags),
                    source=ref,
                    fields=theirs,
                    overflow=route.overflow,
                    omitted=omitted_headings(read.text, cand.body),
                )
                deviated = route.overflow
                action = "created"
            others = [n for n in unique_objects([*pool, *note.folder.notes]) if n is not note] if note.folder else pool

            def in_area(other: Note) -> bool:
                return within_area(note, other)

            near = in_area if self.cfg.link_scope == "root" else None  # links stay in the note's area
            if ctx.classifier is not None and self.cfg.relate_rare_values:
                found = linking.rare_scores(root, note, {n.rel for n in others} | {note.rel}, near)
                others = [*others, *(n for n, _ in found)]
                if found:
                    ctx.decide("relate_candidates", "code", f"{len(found)} by rare values", scores={n.rel: round(s, 3) for n, s in found})
            if near is not None and (apart := [n for n in others if not near(n)]):
                others = [n for n in others if near(n)]
                ctx.decide("relate_candidates", "code", f"{len(apart)} left out, in another area")
            try:
                related = check_relate(steps.relate(ctx, note, others), note, others) if others else []
            except ModelError as e:  # the note is written: a failure here costs its links, not the ingest
                log.warning("relate failed for %s, no See-also links added: %s", note.rel, e)
                ctx.decide("relate", "llm", "error")
                related = []
            related = related[: self.cfg.max_links]
            for other in related:
                self.store.link(note, other)
            verb = ("Revised" if revised else "Merged") if action == "merged" else "Created"
            via = f", via {','.join(self.cfg.llm.pin_provider)}" if self.cfg.llm.pin_provider else ""
            kept = f", previous version archived at {archived}" if revised else ""
            # CROW: the classifier did not settle the route and the LLM chose the folder (read by `Wiki.taxonomy_health`)
            llm_decides = self.cfg.mode == "crow"  # in classic the LLM routes every note: that is no fallback
            fallback = any(d.step == "route" and (d.fallback or (llm_decides and d.decider == "llm")) for d in ctx.decisions)
            routed = ", routed by fallback" if fallback else ""
            text = f"from {raw} ({steps.name} {self.hash}, {self.cfg.llm.model}{via}){kept}{routed}"
            self.store.append_log(verb, text, note.rel, note.title)
            if deviated is not None:
                self.store.append_log("Deviated note", _overflow_text(note.rel, deviated, self.cfg.max_top_folders), note.rel, note.title)
        total = prepared.usage.copy()
        total.merge(used)
        return IngestResult(
            action,
            note.rel,
            note.title,
            note.folder.label if note.folder else "/",
            created,
            [r.rel for r in related],
            ctx.decisions,
            total,
            overflow=deviated,
        )

    def save_source(self, source: Source, cand: Candidate) -> tuple[str, dict[str, Any]]:
        """Keep the raw copy and build the note's source entry; called right before the note is written.

        Saved this late so an interrupted ingest leaves no raw copy that no note cites.
        """
        title = source.title or self.shown(cand.title)  # the document's own title; the note's, when it has none, as it is written
        raw = self.store.save_raw(source.text, title=title, resource=source.resource, origin=source.origin)
        ref: dict[str, Any] = {"resource": raw, "title": title, "hash": content_hash(source.text)}
        if source.origin is not None:
            ref["origin"] = source.origin.to_dict()
        ref["steps"] = {"name": self.steps.name, "hash": self.hash}
        return raw, ref


def _earlier(note: Note) -> list[str]:
    """The headings an earlier source of the note left out (its `omitted`)."""
    held = note.frontmatter.get("omitted")
    return [str(h) for h in held] if isinstance(held, list) else []


def _overflow_text(rel: str, overflow: dict[str, float], limit: int) -> str:
    """The log line of a note the limit on top-level folders deviated: the root it went to and the probabilities that chose it."""
    if "declared" in overflow:
        where = rel.rpartition("/")[0]
        return f"filed in /{where} (the declared overflow folder): no new top-level folder could be opened (limit of {limit})"
    odds = f"p_none {overflow['p_none']}, p_root {overflow['p_root']}" if "p_root" in overflow else "chosen by words, no probabilities"
    return f"filed in /{rel.split('/', 1)[0]} ({odds}): no new top-level folder could be opened (limit of {limit})"


def _read(source: Source) -> Source:
    """The source as the steps read it: a spreadsheet sheet by its first rows, the note catalogues it (`sheets.brief`)."""
    text = brief(source.text)
    return source if text is source.text else replace(source, text=text)
