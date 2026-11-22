# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Researcher answers a question from the wiki: select notes, then answer with citations.

The flow is fixed; `select` and `answer` are steps of the `Steps` it is given, and their output is checked.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any, cast

from llmw2.agents import conflicts, note_checks, revisions
from llmw2.agents.base import Decision
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import CROW, StepContext, Steps, check_answer, check_select
from llmw2.bundle.document import OKFDocumentError
from llmw2.bundle.sheets import SHEET_CHARS, rows_for
from llmw2.bundle.store import WikiStore
from llmw2.bundle.tree import Folder, Note, matching
from llmw2.config import PROMPT_ROOM, WikiConfig
from llmw2.errors import InputError, ModelError
from llmw2.filer import PENDING
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageReport
from llmw2.search import Found, NoteSearch, Pending

log = logging.getLogger(__name__)

NO_ANSWER = "The wiki has no notes relevant to this question."
# the last line of an answer whose search was answered while the index was being built (`Answer.partial`)
PARTIAL_HINT = "(The search index is being built: the notes read may be incomplete.)"
_BRACKET = re.compile(r"\[([^\[\]]+)\]")
_NOTE_PATH = re.compile(r"[^\s\[\];,]+\.md|" + re.escape(PENDING) + r"[\w-]+")  # a note, or an entry not filed yet (`Wiki.search`)


@dataclass
class Answer:
    text: str
    citations: list[str]  # bundle paths cited in the text
    notes: list[str]  # bundle paths given to the answering LLM
    decisions: list[Decision]
    usage: UsageReport = field(default_factory=UsageReport)
    titles: dict[str, str] = field(default_factory=dict)  # each cited path -> the note's title when the answer was written
    # the search the notes were found by ran while the index was being built (`Partial.sentence`): the sources may be incomplete, and
    # the text ends with `PARTIAL_HINT`; None once the index is built
    partial: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "usage": self.usage.to_dict()}


def _cited(text: str) -> list[str]:
    """The note paths in the brackets of `text`: "[a/x.md]", "[a/x.md; b/y.md]" or "[a/x.md, b/y.md]".

    A bracket with any other word cites nothing.
    """
    found: list[str] = []
    for bracket in _BRACKET.findall(text):
        parts = [p.strip() for p in re.split(r"[;,]", bracket)]
        if all(_NOTE_PATH.fullmatch(p) for p in parts):
            found += parts
    return found


class Researcher:
    """One instance serves one question, so its trace of decisions is never shared."""

    def __init__(
        self,
        store: WikiStore,
        llm: LLM,
        prompts: Prompts,
        cfg: WikiConfig,
        steps: Steps,
        classifier: Classifier | None = None,
        search: NoteSearch | None = None,
    ) -> None:
        self.store = store
        self.cfg = cfg
        self.steps = steps
        self.search = search
        self.ctx = StepContext(llm, classifier, prompts, cfg, system_prompt="researcher/system")
        self._partial: str | None = None  # a search of this answer ran while the index was being built (`Found.partial`)

    def ask(self, question: str, where: dict[str, Any] | None = None, pending: Sequence[tuple[Pending, Note]] = ()) -> Answer:
        """`where` (field -> value) keeps the notes whose `fields` have all of them: a filter, not an access control. `pending`: entries
        not filed yet (already kept by `where`), each as the search takes it and as a note with no folder (`pending:<id>`): the search
        finds them as it does in `Wiki.search`, and the answer may read and cite them; a navigation of the folders does not see them."""
        ctx = self.ctx
        self._partial = None
        ctx.partial = False
        with ctx.llm.usage.span() as used:
            known = [n for _, n in pending]  # the notes not of the tree that `select` may return
            root: Folder | None
            if self.search is not None and not where and self.steps.select is CROW.select:
                # CROW's own select reads the notes the search finds, and the tree only to navigate when it finds none: the notes
                # found are read from their files, and the tree is loaded only if the step looks at it
                tree = _Tree(self.store)
                ctx.search = self._reader(pending, known)
                out = self.steps.select(ctx, cast(Folder, tree), question)  # it stands for the root, see `_Tree`
                root = tree.loaded
            else:
                root = self.store.load()
                if self.search is not None:
                    ctx.search = self._finder(root, where, pending)
                if where:
                    root = matching(root, where)
                out = self.steps.select(ctx, root, question)
            notes = check_select(out, root, known)
            notes, past = self._with_history(question, notes)
            asked = f"{question}\n\n{revisions.HISTORY_HINT}" if past else question
            if self._conflicting(question, notes):
                asked = f"{asked}\n\n{conflicts.CONFLICT_HINT}"
            text = NO_ANSWER
            if notes:
                given = self._with_rows(question, notes)
                text = check_answer(self.steps.answer(ctx, asked, given))
                # a quotation the notes read do not have is not given as theirs: its marks go, its words stay (no call)
                text, wrong = note_checks.unquoted(text, "\n\n".join(n.full_text() for n in given))
                if wrong:
                    ctx.decide("quote_unquoted", "code", f"{len(wrong)} quotations not in the notes read")
        read = {n.rel for n in notes}
        citations = [c for c in dict.fromkeys(_cited(text)) if c in read]
        titles = {n.rel: n.title for n in notes if n.rel in citations}
        if self._partial is not None:  # said in the answer itself, for whoever reads only its text (an MCP client's model)
            text = f"{text}\n\n{PARTIAL_HINT}"
        cited = {c: titles[c] for c in citations}
        return Answer(text, citations, [n.rel for n in notes], ctx.decisions, used, titles=cited, partial=self._partial)

    def _finder(
        self, root: Folder, where: dict[str, Any] | None, pending: Sequence[tuple[Pending, Note]] = ()
    ) -> Callable[[str, int], list[Note]]:
        """The search of the notes `where` keeps, and of the entries not filed yet, as `Wiki.search` runs it."""
        search = self.search
        assert search is not None
        by_rel = {n.rel: n for n in root.all_notes()} | {n.rel: n for _, n in pending}
        waiting = [hit for hit, _ in pending]

        def find(query: str, k: int) -> list[Note]:
            found = search.find(self.store, query, k, where, waiting)
            self._saw(found)
            return [by_rel[rel] for rel, _, _ in found if rel in by_rel]

        return find

    def _saw(self, found: Found) -> None:
        if found.partial is not None:
            self._partial = found.partial.sentence()
            self.ctx.partial = True

    def _reader(self, pending: Sequence[tuple[Pending, Note]], known: list[Note]) -> Callable[[str, int], list[Note]]:
        """`_finder` with no `where` and no tree: each note found is taken as a load would take it (`WikiStore.note_at`), once; one gone
        since the search, or no longer a note, is left out, as a load would leave it out. The notes taken are added to `known`."""
        search = self.search
        assert search is not None
        by_rel = {n.rel: n for _, n in pending}
        waiting = [hit for hit, _ in pending]

        def find(query: str, k: int) -> list[Note]:
            out = []
            found = search.find(self.store, query, k, None, waiting)
            self._saw(found)
            for rel, _, _ in found:
                if rel not in by_rel:
                    if (note := self.store.note_at(rel)) is None:
                        continue
                    by_rel[rel] = note
                    known.append(note)
                out.append(by_rel[rel])
            return out

        return find

    def _with_rows(self, question: str, notes: list[Note]) -> list[Note]:
        """The notes as the answer reads them: a spreadsheet sheet's note gets the rows of its sources the question needs.

        A sheet's note catalogues it; its data stays in the raw copy, as CSV (`sheets`). Copies of the notes are
        given, the files are untouched. The rows fill at most what the prompt has left after the notes;
        only the latest copy of each origin is read.
        """
        room = self.cfg.effective_read_chars - PROMPT_ROOM - sum(len(n.full_text()) for n in notes)  # what the prompt has left
        out = []
        for note in notes:
            blocks = []
            for rel in _latest_raws(note):
                if room < 1_000:
                    break
                try:
                    raw = self.store.read(rel)
                except (FileNotFoundError, InputError, OKFDocumentError):
                    continue
                if raw.frontmatter.get("removed"):
                    continue
                if block := rows_for(question, raw.body, min(SHEET_CHARS, room)):
                    blocks.append(block)
                    room -= len(block)
            out.append(replace(note, body=note.main_body() + "\n\n" + "\n\n".join(blocks)) if blocks else note)
        return out

    def _conflicting(self, question: str, notes: list[Note]) -> bool:
        """Whether the notes read disagree on the answer to the question (CROW, 2 notes at least)."""
        ctx = self.ctx
        if not self.cfg.answer_conflicts or ctx.classifier is None or len(notes) < 2:
            return False
        try:
            p = ctx.classifier.nouls(conflicts.conflict_state(question, notes), {"q": conflicts.CONFLICT}, op="ask/conflict")["q"]
        except ModelError as e:
            log.warning("classifier failed on the conflict question, no hint is added: %s", e)
            ctx.decide("conflict", "classifier", "error", fallback=True)
            return False
        ctx.decide("conflict", "classifier", "hint" if p > conflicts.CONFLICT_MIN else "none", p)
        return p > conflicts.CONFLICT_MIN

    def _with_history(self, question: str, notes: list[Note]) -> tuple[list[Note], bool]:
        """Each note that was revised in place is followed by its archived version, if the question asks about the past (True)."""
        ctx = self.ctx
        if self.cfg.revisions != "replace" or ctx.classifier is None or not any(n.frontmatter.get("previous_version") for n in notes):
            return notes, False
        try:
            p = ctx.classifier.nouls(question, {"q": revisions.HISTORY}, op="ask/historical")["q"]
        except ModelError as e:
            log.warning("classifier failed on the historical question, no archive is read: %s", e)
            ctx.decide("historical", "classifier", "error", fallback=True)
            return notes, False
        ctx.decide("historical", "classifier", "archive" if p > revisions.HISTORY_MIN else "current", p)
        if p <= revisions.HISTORY_MIN:
            return notes, False
        out: list[Note] = []
        for note in notes:
            out.append(note)
            previous = note.frontmatter.get("previous_version")
            if not previous:
                continue
            try:
                old = self.store.read(str(previous))
                if all(old.rel != n.rel for n in notes):
                    out.append(old)
            except (FileNotFoundError, InputError, OKFDocumentError):
                log.warning("the archived version of %s is missing: %s", note.rel, previous)
        return out, True


class _Tree:
    """The root folder for CROW's own `select`, loaded at the first attribute the step reads (`loaded`, None until then). Only that
    step is given it: it is not a `Folder` (no `isinstance`; the folders under it have the loaded root as parent)."""

    def __init__(self, store: WikiStore) -> None:
        self._store = store
        self.loaded: Folder | None = None

    def __getattr__(self, name: str) -> Any:
        if self.loaded is None:
            self.loaded = self._store.load()
        return getattr(self.loaded, name)


def _latest_raws(note: Note) -> list[str]:
    """The raw copies a note cites, newest first, only the latest of each origin (older versions of a sheet left out)."""
    seen: set[object] = set()
    out = []
    for source in reversed(note.frontmatter.get("sources") or []):
        if not isinstance(source, dict) or not str(source.get("resource", "")).startswith("/raw/"):
            continue
        origin = source.get("origin")
        key = (origin.get("source"), origin.get("account"), origin.get("id")) if isinstance(origin, dict) else source["resource"]
        if key not in seen:
            seen.add(key)
            out.append(str(source["resource"]))
    return out
