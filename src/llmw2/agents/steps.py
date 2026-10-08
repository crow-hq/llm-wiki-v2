# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The steps of the ingest and the ask flows, each a replaceable function with a fixed signature.

The flow itself (order of the steps, locking, writing) belongs to the core. Inside a step anything goes: one
model call, many, none, an external service. The core only checks that what a step returns fits what the next
one expects, and stops with `StepError` naming the step before writing anything. Provisional until parallel ingest.

    from dataclasses import replace
    steps = replace(CROW, name="patents-v2", summarize=my_summarize, route=my_route)
    wiki = Wiki(cfg, steps=steps)

Parameters of a step go in a closure or a `functools.partial`.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal, TypeVar

import yaml
from pydantic import BaseModel

from llmw2.agents import steps_classic as classic
from llmw2.agents import steps_crow as crow
from llmw2.agents.base import Decision
from llmw2.agents.data import Candidate, NoteDraft, Route, Source
from llmw2.agents.prompts import Prompts
from llmw2.agents.state import STATE_VERSION, source_state
from llmw2.bundle.tree import Folder, Note, slugify
from llmw2.config import WikiConfig
from llmw2.errors import StepError
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM, reply_tokens

__all__ = ["CLASSIC", "CROW", "StepContext", "StepError", "Steps", "decision_hash"]

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger(__name__)


# The Length bullet of `shared/note_rules`, with its "- " and its newline: `{{length_rule}}` stands at the start of a line,
# with no newline after it, so an empty value leaves no bullet and no blank line.
LENGTH_RULE = (
    "- Length: the body is at most about {note_chars} characters. When the subject needs more, first write more "
    "compactly (tables, lists, no repetition), then drop marginal details; never drop a fact that changed or one "
    "marked as previously.\n"
)


@dataclass
class StepContext:
    """What a step may use: the models, prompts and config of the operation, and its trace of decisions.

    There is no store access: a step reads the tree it is given. Model calls made through `llm` and `classifier`
    are counted in the usage of the operation like the core's own.
    """

    llm: LLM
    classifier: Classifier | None
    prompts: Prompts
    cfg: WikiConfig
    system_prompt: str = ""  # librarian/system for the ingest steps, researcher/system for the ask steps
    source: Source | None = None  # the source being ingested (None while asking)
    candidate: Candidate | None = None  # the note `summarize` wrote from it (None while asking and while summarizing)
    decisions: list[Decision] = field(default_factory=list)

    def ask_json(self, prompt: str, schema: type[T], *, reply_chars: int | None = None, **values: object) -> T:
        """Render `prompt` with `values` and ask for a JSON reply.

        `reply_chars` is about how long the reply should be (default: a note, `effective_note_chars`); it sets the
        output cap, and a reply cut by it is asked once more, shorter. It is not a prompt value: a prompt cannot
        have a variable of that name.
        """
        system = self.prompts.render(self.system_prompt)
        cap = reply_tokens(self.cfg.effective_note_chars if reply_chars is None else reply_chars)
        return self.llm.json(system, self.prompts.render(prompt, **self._values(values)), schema, op=prompt, max_tokens=cap)

    def ask_text(
        self, prompt: str, *, reply_chars: int | None = None, keep_cut: bool = False, cut: list[str] | None = None, **values: object
    ) -> str:
        """Like `ask_json`, for a plain-text reply.

        `keep_cut`: a reply cut by the cap is returned as written, not asked again; `cut` collects it. Not prompt values.
        """
        system = self.prompts.render(self.system_prompt)
        cap = reply_tokens(self.cfg.effective_note_chars if reply_chars is None else reply_chars)
        user = self.prompts.render(prompt, **self._values(values))
        return self.llm.complete(system, user, op=prompt, max_tokens=cap, keep_cut=keep_cut, **({} if cut is None else {"cut": cut}))

    def _values(self, values: dict[str, object]) -> dict[str, object]:
        """The values of a prompt, with the note length and the length rule that `shared/note_rules` asks for."""
        chars = values.get("note_chars", self.cfg.effective_note_chars)  # the rule follows a note length the caller passes
        return {"note_chars": chars, "length_rule": LENGTH_RULE.format(note_chars=chars), **values}

    def decide(
        self,
        step: str,
        decider: Literal["llm", "classifier", "rule"],
        choice: str,
        confidence: float | None = None,
        *,
        fallback: bool = False,
        scores: dict[str, float] | None = None,
    ) -> None:
        self.decisions.append(Decision(step, decider, choice, confidence, fallback, dict(scores or {})))
        extra = (f" {confidence:.2f}" if confidence is not None else "") + (" [fallback]" if fallback else "")
        log.info("decide %-15s %-10s %s%s", step, decider, choice, extra)

    def can_create(self, folder: Folder) -> bool:
        return folder.depth < self.cfg.max_depth

    def children(self, folder: Folder) -> list[Folder]:
        """Subfolders a step may enter: none below max_depth."""
        return folder.subfolders if folder.depth < self.cfg.max_depth else []

    def state(self, cand: Candidate) -> str:
        return cand.compact(self.cfg.classifier.state_chars)

    def route_state(self, cand: Candidate) -> str:
        """What routing reads: the candidate's own state, else one built from the source, else `state`."""
        if cand.state:
            return cand.state
        if self.source is not None:
            return source_state(self.source.title, self.source.text, self.cfg.classifier.state_chars)
        return self.state(cand)

    def need_classifier(self, step: str) -> Classifier:
        """The classifier, or a StepError for a CROW step run without one."""
        if self.classifier is None:
            raise StepError(step, "needs the classifier (mode crow)")
        return self.classifier


# -- the step signatures --------------------------------------------------------------

ExtractStep = Callable[[StepContext, bytes, str], str]
SummarizeStep = Callable[[StepContext, Source], Candidate]
RouteStep = Callable[[StepContext, Folder, Candidate], Route]
NameFolderStep = Callable[[StepContext, Folder, Candidate], tuple[str, str]]  # (name, description)
MatchStep = Callable[[StepContext, list[Note], Candidate], Note | None]
ConsolidateStep = Callable[[StepContext, Note, Candidate], bool]  # True = modify / merge
MergeStep = Callable[[StepContext, Note, Source], NoteDraft]
RelateStep = Callable[[StepContext, Note, list[Note]], list[Note]]
SelectStep = Callable[[StepContext, Folder, str], list[Note]]
AnswerStep = Callable[[StepContext, str, list[Note]], str]

STEP_NAMES = ("extract", "summarize", "route", "name_folder", "match", "consolidate", "merge", "relate", "select", "answer")


@dataclass(frozen=True)
class Steps:
    """A complete set of steps; every one defaults to the classic LLM implementation."""

    name: str = "custom"
    extract: ExtractStep = classic.extract
    summarize: SummarizeStep = classic.summarize
    route: RouteStep = classic.route
    name_folder: NameFolderStep = classic.name_folder
    match: MatchStep = classic.match
    consolidate: ConsolidateStep = classic.consolidate
    merge: MergeStep = classic.merge
    relate: RelateStep = classic.relate
    select: SelectStep = classic.select
    answer: AnswerStep = classic.answer

    @property
    def hash(self) -> str:
        """First 8 hex chars of a sha256 over the source of the ten functions, to tell sets apart in provenance.

        Best effort: a function whose source is unavailable counts by module and name. What a step calls (helpers,
        prompts, thresholds) and the values closed over or bound by `functools.partial` are NOT included.
        """
        parts = []
        for name in STEP_NAMES:
            fn = getattr(self, name)
            while isinstance(fn, functools.partial):  # hash the function it wraps, not the partial type
                fn = fn.func
            try:
                parts.append(inspect.getsource(fn))
            except (OSError, TypeError):
                parts.append(f"{getattr(fn, '__module__', '')}.{getattr(fn, '__qualname__', type(fn).__name__)}")
        return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:8]


CLASSIC = Steps(name="classic")
CROW = replace(
    CLASSIC, name="crow", route=crow.route, match=crow.match, consolidate=crow.consolidate, relate=crow.relate, select=crow.select
)


def decision_hash(steps: Steps, cfg: WikiConfig, prompts: Prompts) -> str:
    """First 8 hex chars of a sha256 over everything that decides where a note goes, to tell runs apart in provenance.

    The step code (`steps.hash`), the text of every prompt as `prompts` resolves it, the writing model, the classifier
    model (CROW only), temperature, seed, pinned providers, the CROW thresholds, `max_depth`, `max_links`, `mode`,
    `summarize`, the size of the state routing reads (`state_chars`, `STATE_VERSION`), how much one
    call reads and how long a note may be (`effective_read_chars`, `effective_note_chars`) and `SUMMARY_VERSION`. Keys, addresses,
    timeouts, attempts, other sizes, concurrency and logs do not change the decisions: not in it.
    """
    decisive = {
        "steps": steps.hash,
        "prompts": prompts.fingerprint(),
        "model": cfg.llm.model,
        "classifier": cfg.classifier.model if cfg.mode == "crow" else None,
        "temperature": cfg.llm.temperature,
        "seed": cfg.llm.seed,
        "pin_provider": cfg.llm.pin_provider,
        "crow": cfg.crow.model_dump(),
        "fallback_menu_chars": crow.FALLBACK_MENU_CHARS,  # the menu limit is a constant outside the hashed step functions
        "max_depth": cfg.max_depth,
        "max_links": cfg.max_links,
        "mode": cfg.mode,
        "summarize": cfg.summarize,
        "state_chars": cfg.classifier.state_chars,
        "state_version": STATE_VERSION,
        "read_chars": cfg.effective_read_chars,
        "note_chars": cfg.effective_note_chars,
        "summary_version": classic.SUMMARY_VERSION,
    }
    return hashlib.sha256(json.dumps(decisive, sort_keys=True).encode("utf-8")).hexdigest()[:8]


# -- output checks: StepError naming the step, before anything is written ---------------


def check_extract(text: object) -> str:
    if not isinstance(text, str):
        raise StepError("extract", f"must return a str, not {type(text).__name__}")
    return text


def _check_note_data(step: str, out: object, kind: type[Candidate] | type[NoteDraft]) -> None:
    if not isinstance(out, kind):
        raise StepError(step, f"must return a {kind.__name__}, not {type(out).__name__}")
    if not isinstance(out.title, str) or not out.title.strip():
        raise StepError(step, "the title is empty")
    if not isinstance(out.tags, list) or not all(isinstance(t, str) for t in out.tags):
        raise StepError(step, "tags must be a list of str")
    if not isinstance(out.fields, dict):
        raise StepError(step, "fields must be a dict")
    try:
        yaml.safe_dump(out.fields)
    except yaml.YAMLError as e:
        raise StepError(step, f"fields cannot be written to the note: {e}") from e


def check_summarize(cand: object) -> Candidate:
    _check_note_data("summarize", cand, Candidate)
    assert isinstance(cand, Candidate)
    if not isinstance(cand.state, str):
        raise StepError("summarize", "state must be a str")
    return cand


def check_merge(draft: object) -> NoteDraft:
    _check_note_data("merge", draft, NoteDraft)
    assert isinstance(draft, NoteDraft)
    return draft


def check_route(route: object, root: Folder, cfg: WikiConfig) -> Route:
    if not isinstance(route, Route):
        raise StepError("route", f"must return a Route, not {type(route).__name__}")
    known = {id(f) for f in root.walk()}
    if id(route.folder) not in known:
        raise StepError("route", "the folder is not in the wiki tree")
    if not isinstance(route.candidates, list) or any(id(f) not in known for f in route.candidates):
        raise StepError("route", "every candidate must be a folder of the wiki tree")
    create = route.create
    if not isinstance(create, list) or not all(
        isinstance(c, tuple) and len(c) == 2 and all(isinstance(x, str) for x in c) for c in create
    ):
        raise StepError("route", "create must be a list of (name, description) tuples of two str")
    if create and route.new:
        raise StepError("route", "new and create are alternatives: name the levels in create or let name_folder name one")
    if any(not slugify(name, max_len=40, fallback="") for name, _ in create):
        raise StepError("route", "a name in create is empty")
    if route.folder.depth + len(create) > cfg.max_depth:
        raise StepError("route", f"create goes beyond max_depth ({cfg.max_depth})")
    return route


def check_name_folder(out: object) -> tuple[str, str]:
    if not (isinstance(out, tuple) and len(out) == 2 and all(isinstance(x, str) for x in out)):
        raise StepError("name_folder", "must return a (name, description) tuple of two str")
    if not slugify(out[0], max_len=40, fallback=""):
        raise StepError("name_folder", "the name is empty")
    return out[0], out[1]


def check_match(match: object, pool: list[Note]) -> Note | None:
    if match is not None and not any(match is n for n in pool):
        raise StepError("match", "must return None or one of the notes it was given")
    return match  # type: ignore[return-value]


def check_consolidate(out: object) -> bool:
    if not isinstance(out, bool):
        raise StepError("consolidate", f"must return a bool, not {type(out).__name__}")
    return out


def check_relate(out: object, note: Note, others: list[Note]) -> list[Note]:
    if not isinstance(out, list):
        raise StepError("relate", f"must return a list of notes, not {type(out).__name__}")
    known = {id(n) for n in others}
    if any(n is note for n in out):
        raise StepError("relate", "a note cannot be related to itself")
    if any(id(n) not in known for n in out):
        raise StepError("relate", "must return only notes it was given")
    return out


def check_select(out: object, root: Folder) -> list[Note]:
    if not isinstance(out, list):
        raise StepError("select", f"must return a list of notes, not {type(out).__name__}")
    known = {id(n) for n in root.all_notes()}
    if any(id(n) not in known for n in out):
        raise StepError("select", "must return only notes of the wiki")
    return out


def check_answer(out: object) -> str:
    if not isinstance(out, str):
        raise StepError("answer", f"must return a str, not {type(out).__name__}")
    return out
