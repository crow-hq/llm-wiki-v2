# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The CROW steps: the typed classifier takes the decisions (CROW §5.1 ingest, §5.2 retrieval).

Each falls back to the LLM (the classic step) where the paper prescribes an escape, or when the classifier fails.
They need `ctx.classifier`: without one they raise `StepError`.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from llmw2.agents import steps_classic as classic
from llmw2.agents.base import unique_objects
from llmw2.agents.data import Candidate, Route
from llmw2.agents.steps_classic import HERE, MODIFY, NEW, NEW_NOTE, NONE, FallbackRoute
from llmw2.bundle.tree import Folder, Note
from llmw2.errors import ModelError

if TYPE_CHECKING:
    from llmw2.agents.steps import StepContext

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


# -- ingest ---------------------------------------------------------------------------


def route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
    """Step 1: a Choice per level (subfolders, Here, New subfolder, None of these) with a beam."""
    ctx.need_classifier("route")
    try:
        finished, visited = _beam(ctx, root, ctx.state(cand))
    except ModelError as e:
        log.warning("classifier failed while routing, the LLM decides: %s", e)
        ctx.decide("route", "classifier", "error", fallback=True)
        return classic.route(ctx, root, cand)
    best = max(finished, key=lambda p: p.score, default=None)
    if best is None or best.score < ctx.cfg.crow.tau_path:
        return _route_fallback(ctx, root, visited, cand, best.score if best else None)
    candidates = unique_objects(f for p in finished for f in p.folders)
    ctx.decide("route", "classifier", f"{best.folders[-1].label} ({best.end})", best.score)
    return Route(best.folders[-1], best.end == NEW, candidates)


def _beam(ctx: StepContext, root: Folder, state: str) -> tuple[list[_Path], list[Folder]]:
    """Level by level; at most `beam` open paths survive each level (by path score)."""
    level, finished, visited = [_Path((root,))], [], [root]
    while level:
        expanded: list[_Path] = []
        for path in level:
            finished += _expand(ctx, path, state, expanded, visited)
        level = sorted(expanded, key=lambda p: p.score, reverse=True)[: ctx.cfg.crow.beam]
    return finished, visited


def _expand(ctx: StepContext, path: _Path, state: str, expanded: list[_Path], visited: list[Folder]) -> list[_Path]:
    """One Choice at the end of `path`; returns the paths it finishes, appends those that go deeper."""
    crow = ctx.cfg.crow
    folder = path.folders[-1]
    text = ctx.prompts.render_sections("classifier/route", folder=folder.label)
    options = {f.name: f.description or f.name for f in ctx.children(folder)}
    if not (folder.is_root and ctx.can_create(folder)):  # the root holds no notes, unless no folder can be made
        options[HERE] = text[HERE]
    if ctx.can_create(folder):
        options[NEW] = text[NEW]
    if not folder.is_root:
        options[NONE] = text[NONE]
    if len(options) == 1:  # one way only (a new folder in an empty wiki, or Here at max_depth 0): nothing to decide
        return [_Path(path.folders, path.probs, next(iter(options)))]
    answer = ctx.need_classifier("route").choice(state, text["instructions"], options, op="route")
    ranked = sorted(options, key=lambda o: answer.probabilities.get(o, 0.0), reverse=True)
    kept = ranked[:1] if answer.confidence >= crow.tau_route else ranked[: crow.beam]
    ctx.decide("route_step", "classifier", f"{folder.label}: {' | '.join(kept)}", answer.confidence, scores=answer.probabilities)
    finished = []
    for option in kept:
        p = answer.probabilities.get(option, 0.0)
        if option in (HERE, NEW):
            finished.append(_Path(path.folders, (*path.probs, p), option))
        elif option != NONE and (sub := folder.subfolder(option)) is not None:
            expanded.append(_Path((*path.folders, sub), (*path.probs, p)))
            visited.append(sub)
    return finished


def _route_fallback(ctx: StepContext, root: Folder, visited: list[Folder], cand: Candidate, score: float | None) -> Route:
    """§5.3: the LLM chooses among the folders the classifier explored, or opens a new one."""
    out = ctx.ask_json(
        "librarian/route_fallback",
        FallbackRoute,
        folders="\n".join(f"- {f.label}: {f.description or '(no description)'}" for f in visited),
        note=ctx.state(cand),
    )
    folder, unmade = _deepest(ctx, root, out.folder)
    # A path not made yet ("/finance/pricing" with no pricing) asks for a new subfolder of the deepest one that is.
    new = (out.action == "create" or unmade or folder.is_root) and ctx.can_create(folder)  # the root holds no notes
    ctx.decide("route", "llm", f"{folder.label} ({NEW if new else HERE})", score, fallback=True)
    return Route(folder, new, folder.ancestors())


def _deepest(ctx: StepContext, root: Folder, path: str) -> tuple[Folder, bool]:
    """The deepest folder of `path` that exists, explored or not, and whether `path` goes on below it."""
    folder = root
    for name in (n for n in path.strip().strip("/").split("/") if n):
        sub = next((f for f in ctx.children(folder) if f.name == name), None)
        if sub is None:
            return folder, True
        folder = sub
    return folder, False


def match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
    """Step 2: one batched Noul per note; the best one above tau_ing is the candidate."""
    classifier = ctx.need_classifier("match")
    if not pool:
        return None
    questions = {f"n{i}": ctx.prompts.render("classifier/note_relevance", title=n.title, summary=n.summary) for i, n in enumerate(pool)}
    try:
        probs = classifier.nouls(ctx.state(cand), questions, op="match")
    except ModelError as e:
        log.warning("classifier failed on note relevance, the LLM decides: %s", e)
        ctx.decide("match", "classifier", "error", fallback=True)
        return classic.match(ctx, pool, cand)
    p, best = max(((probs[f"n{i}"], n) for i, n in enumerate(pool)), key=lambda x: x[0])
    found = best if p > ctx.cfg.crow.tau_ing else None
    scores = {n.rel: probs[f"n{i}"] for i, n in enumerate(pool)}
    ctx.decide("match", "classifier", found.rel if found else "none", p, scores=scores)
    return found


def consolidate(ctx: StepContext, note: Note, cand: Candidate) -> bool:
    """Step 3: Choice between modifying the note and writing a new one; unsure → new note."""
    classifier = ctx.need_classifier("consolidate")
    room = max(classifier.request_chars // 2, 1)
    state = f"{ctx.state(cand)}\n\n--- Existing note ---\n{note.full_text()[:room]}"
    text = ctx.prompts.render_sections("classifier/consolidate")
    try:
        answer = classifier.choice(state, text["instructions"], {MODIFY: text[MODIFY], NEW_NOTE: text[NEW_NOTE]}, op="consolidate")
    except ModelError as e:
        log.warning("classifier failed on consolidation, the LLM decides: %s", e)
        ctx.decide("consolidate", "classifier", "error", fallback=True)
        return classic.consolidate(ctx, note, cand)
    merged = answer.choice == MODIFY and answer.confidence >= ctx.cfg.crow.tau_cons
    ctx.decide("consolidate", "classifier", "modify" if merged else "new", answer.confidence, scores=answer.probabilities)
    return merged


def relate(ctx: StepContext, note: Note, others: list[Note]) -> list[Note]:
    """Correlation (an extension of the paper): one Noul per candidate, above tau_link."""
    classifier = ctx.need_classifier("relate")
    questions = {f"n{i}": ctx.prompts.render("classifier/relate", title=n.title, summary=n.summary) for i, n in enumerate(others)}
    try:
        probs = classifier.nouls(note.compact(ctx.cfg.classifier.state_chars), questions, op="relate")
    except ModelError as e:
        log.warning("classifier failed on relatedness, the LLM decides: %s", e)
        ctx.decide("relate", "classifier", "error", fallback=True)
        return classic.relate(ctx, note, others)
    scored = sorted(((probs[f"n{i}"], n) for i, n in enumerate(others)), key=lambda x: x[0], reverse=True)
    related = [n for p, n in scored if p > ctx.cfg.crow.tau_link][: ctx.cfg.max_links]
    scores = {n.rel: p for p, n in scored}
    ctx.decide("relate", "classifier", ", ".join(n.rel for n in related) or "none", scores=scores)
    return related


# -- ask ------------------------------------------------------------------------------


def select(ctx: StepContext, root: Folder, question: str) -> list[Note]:
    """§5.2: a Noul per folder, a Noul per note, top-k; classic navigation when nothing clears its threshold."""
    ctx.need_classifier("select")
    try:
        notes = _retrieve(ctx, root, question)
    except ModelError as e:
        log.warning("classifier failed on retrieval, the LLM navigates: %s", e)
        notes = []
    if not notes:
        ctx.decide("select", "llm", "classic navigation", fallback=True)
        return classic.select(ctx, root, question)
    return notes


def _retrieve(ctx: StepContext, root: Folder, question: str) -> list[Note]:
    classifier = ctx.need_classifier("select")
    crow = ctx.cfg.crow
    explored, level = [root], [root]
    while level:  # step 1: at each level, explore up to b folders above tau_fold
        subs = [s for f in level for s in f.subfolders]
        if not subs:
            break
        questions = {
            f"f{i}": ctx.prompts.render("classifier/retrieval_folder", name=s.name, description=s.description or "no description")
            for i, s in enumerate(subs)
        }
        probs = classifier.nouls(question, questions, op="retrieve_folder")
        ranked = sorted(((probs[f"f{i}"], s) for i, s in enumerate(subs)), key=lambda x: x[0], reverse=True)
        level = [s for p, s in ranked if p > crow.tau_fold][: crow.retrieval_beam]
        explored += level
        scores = {s.label: p for p, s in ranked}
        ctx.decide("retrieve_folder", "classifier", ", ".join(s.label for s in level) or "none", scores=scores)
    candidates = [n for f in explored for n in f.notes]
    if not candidates:
        return []
    questions = {
        f"n{i}": ctx.prompts.render("classifier/retrieval_note", title=n.title, summary=n.summary) for i, n in enumerate(candidates)
    }
    probs = classifier.nouls(question, questions, op="retrieve_note")  # step 2: score the notes
    notes = sorted(((probs[f"n{i}"], n) for i, n in enumerate(candidates)), key=lambda x: x[0], reverse=True)
    top = [n for p, n in notes if p > crow.tau_ret][: crow.k]  # step 3 reads these k notes
    scores = {n.rel: p for p, n in notes}
    ctx.decide("retrieve_note", "classifier", ", ".join(n.rel for n in top) or "none", scores=scores)
    return top
