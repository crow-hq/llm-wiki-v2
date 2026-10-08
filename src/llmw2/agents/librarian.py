# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Librarian files a source into the wiki.

    summarize → route → match → consolidate → merge | (name_folder →) write → relate → link

The flow is fixed; each step is a function of the `Steps` it is given (classic, CROW or custom), and the
output of every step is checked before it is used.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import cached_property
from typing import Any, Literal

from llmw2.agents.base import Decision, clean_tags, unique_objects
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
from llmw2.bundle.store import WikiStore
from llmw2.config import WikiConfig
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageReport

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

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "usage": self.usage.to_dict()}


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
        self, store: WikiStore, llm: LLM, prompts: Prompts, cfg: WikiConfig, steps: Steps, classifier: Classifier | None = None
    ) -> None:
        self.store = store
        self.cfg = cfg
        self.steps = steps
        self.ctx = StepContext(llm, classifier, prompts, cfg, system_prompt="librarian/system")

    @cached_property
    def hash(self) -> str:
        """What the provenance says decided: the steps, prompts and decision parameters (see `decision_hash`)."""
        return decision_hash(self.steps, self.cfg, self.ctx.prompts)

    def ingest(self, source: Source) -> IngestResult:
        return self.file(self.prepare(source))

    def prepare(self, source: Source) -> Prepared:
        """The part of an ingest that does not read the tree: summarize. Needs no lock, and may run in parallel."""
        self.ctx.source = source
        with self.ctx.llm.usage.span() as used:
            cand = check_summarize(self.steps.summarize(self.ctx, source))
        return Prepared(source, cand, used, list(self.ctx.decisions))

    def file(self, prepared: Prepared) -> IngestResult:
        """The rest: from a fresh `load()` to the log. The caller holds the wiki's lock."""
        steps, ctx = self.steps, self.ctx
        source, cand = prepared.source, prepared.candidate
        ctx.source = source
        ctx.candidate = cand
        ctx.decisions = list(prepared.decisions)
        with ctx.llm.usage.span() as used:
            root = self.store.load()
            route = check_route(steps.route(ctx, root, cand), root, self.cfg)
            pool = unique_objects(n for f in [*route.candidates, route.folder] for n in f.notes)
            match = check_match(steps.match(ctx, pool, cand), pool)
            created: list[str] = []
            merging = match is not None and check_consolidate(steps.consolidate(ctx, match, cand))
            if match is not None and merging:
                draft = check_merge(steps.merge(ctx, match, source))
                raw, ref = self.save_source(source, cand)
                note = self.store.update_note(
                    match,
                    title=draft.title,
                    summary=draft.summary,
                    body=draft.body,
                    tags=clean_tags(draft.tags),
                    source=ref,
                    fields=draft.fields,
                )
                action: Literal["created", "merged"] = "merged"
            else:
                folder = route.folder
                if route.create:
                    for name, description in route.create:
                        folder, is_new = self.store.create_folder(folder, name, description)
                        created += [folder.label] if is_new else []
                elif route.new and ctx.can_create(folder):
                    name, description = check_name_folder(steps.name_folder(ctx, folder, cand))
                    folder, is_new = self.store.create_folder(folder, name, description)
                    created += [folder.label] if is_new else []
                raw, ref = self.save_source(source, cand)
                note = self.store.write_note(
                    folder,
                    title=cand.title,
                    summary=cand.summary,
                    body=cand.body,
                    tags=clean_tags(cand.tags),
                    source=ref,
                    fields=cand.fields,
                )
                action = "created"
            others = [n for n in unique_objects([*pool, *note.folder.notes]) if n is not note] if note.folder else pool
            related = check_relate(steps.relate(ctx, note, others), note, others) if others else []
            related = related[: self.cfg.max_links]
            for other in related:
                self.store.link(note, other)
            verb = "Merged" if action == "merged" else "Created"
            via = f", via {','.join(self.cfg.llm.pin_provider)}" if self.cfg.llm.pin_provider else ""
            self.store.append_log(verb, f"from {raw} ({steps.name} {self.hash}, {self.cfg.llm.model}{via})", note.rel, note.title)
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
        ref["steps"] = {"name": self.steps.name, "hash": self.hash}
        return raw, ref
