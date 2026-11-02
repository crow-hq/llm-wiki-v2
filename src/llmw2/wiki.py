# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki`: the one object most users need.

    wiki = Wiki.from_env()                    # OKF_* variables, see .env.example
    wiki.init()
    wiki.ingest(text, title="…", resource="https://…")
    wiki.ingest_file(Path("minutes.pdf").read_bytes(), "minutes.pdf")
    wiki.ingest_file(data, "minutes.pdf", origin=Origin("onedrive", "me", item_id, version=etag))  # unchanged → no model call
    wiki.ingest_many([Item("a.md", text="…"), Item("b.pdf", fetch=read_b)], concurrency=4)   # prepared in parallel, filed in turn
    wiki.origin(key)                          # the latest raw copy cited by a note, and the notes, for one original
    wiki.mark_removed(key)                    # the original is gone at its source; the notes stay
    wiki.purge(key)                           # the original is to be forgotten: its raw copies, archive and notes go for good
    sync(wiki, LocalFolderSource(Path("~/OneDrive")))   # keep the wiki in step with a source
    wiki.ask("…").text
    wiki.create_folder("/", "finance", "Money in and out: budgets, invoices, pricing.")
    wiki.plan([(title, text), …])            # empty wiki: lay out its folders from the documents about to be filed
    wiki.move_note("finance/pricing.md", "/sourcing"); wiki.describe_folder("/finance", "Money in and out.")
    wiki.delete_note("finance/pricing.md"); wiki.delete_folder("/finance"); wiki.clear()
    wiki.audit_routes(0.7, apply=False)       # CROW: notes the classifier would file elsewhere today (no LLM call)
    wiki.taxonomy_health()                    # per folder and month, from log.md: new subfolders and fallbacks per note filed
    wiki.find_note(note_id)                   # a note by its stable `id`
    wiki.usage.to_dict()                      # {"llm": …, "classifier": …, "by_model": …}
"""

from __future__ import annotations

import collections
import concurrent.futures
import contextvars
import functools
import hashlib
import itertools
import logging
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePath
from typing import Any, ParamSpec, Self, TypeVar

from llmw2.agents import echo, grouping, lexical, steps_crow
from llmw2.agents.base import Decision
from llmw2.agents.data import Candidate, FolderPlan, Source, SplitPlan
from llmw2.agents.librarian import IngestResult, Librarian, Prepared
from llmw2.agents.prompts import Prompts
from llmw2.agents.researcher import Answer, Researcher
from llmw2.agents.steps import CLASSIC, CROW, StepContext, Steps, check_extract
from llmw2.audit import (
    AUDIT_MIN,
    HEALTH_MAX_SHARE,
    HEALTH_MIN_FILED,
    PLAN_MARK,
    PLAN_MORE_MARK,
    SPLIT_MARK,
    SPLIT_VERB,
    FolderHealth,
    RouteProposal,
    last_split_at,
    taxonomy_health,
)
from llmw2.batch import BatchReport, Item
from llmw2.bundle.check import Problem, check
from llmw2.bundle.files import is_spreadsheet, sheet_sources, title_of
from llmw2.bundle.origin import Origin, OriginRecord, content_hash
from llmw2.bundle.proposals import CONFIRMED, DISMISSED, OPEN, Proposal, notes_count, read_proposals, write_proposals
from llmw2.bundle.store import CACHE_FILE, Deleted, WikiStore, authored, current_author, raw_sources, read_utf8_or_latin1
from llmw2.bundle.tree import INDEX, LOG, Folder, Note, slugify
from llmw2.config import PROMPT_ROOM, WikiConfig
from llmw2.errors import ConfigError, InputError, NoteChanged, ReplyInvalid, SpendingCap
from llmw2.models.cache import SqliteCache
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageReport, UsageTracker

PLAN_DEPTH = 3  # levels `plan` may lay out (and never below max_depth)
PLAN_OPENING = 220  # characters of each document's opening `plan` reads
PLAN_REPLY_CHARS = 8_000  # about 40 folders, each a path and a one-line description
PLAN_BATCH = 50  # documents per plan: the batch size the plan was measured with
# what `librarian/plan_more` is told about new top-level areas: open (fewer than max_top_folders) or fixed (the limit reached)
PLAN_ROOTS_OPEN = (
    "a new top-level area only for a broad area none of the existing ones\n"
    "covers (never a single client, supplier, project or product: that is a subfolder of the broad area it belongs to), a new"
)
PLAN_ROOTS_FIXED = "The top-level areas are fixed: never propose a new one; put every new folder under the area it belongs to. A new"
COUNT_CHARS = 600  # characters of a document the flat count of a plan reads (`Wiki._count`)
SPLIT_AT = 12  # direct notes that make a folder worth splitting: the structure limit the measures used
SPLIT_REPLY_CHARS = 6_000  # about 3000 tokens: a few subfolders, each with a description and the numbers of its notes

log = logging.getLogger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")


def by_author(method: Callable[_P, _R]) -> Callable[_P, _R]:
    """A method that changes the wiki: the log lines it writes end with ` · by <author>`.

    The author is the call's `author=` keyword, else the `Wiki`'s own; the method declares `author` and this reads it. It
    is set in the context of the call, so threads sharing one `Wiki` (the server's) never mix their authors, and the
    lanes of a batch, which run in a copy of the context, write the batch's. Declared by the caller, not verified.
    """

    @functools.wraps(method)
    def run(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        wiki: Wiki = args[0]  # type: ignore[assignment]
        # the call's own author, else the one already in force (a method calling another), else the Wiki's
        with authored(kwargs.get("author") or current_author() or wiki.author):  # type: ignore[arg-type]
            return method(*args, **kwargs)

    return run


def _origin_of(fm: dict[str, Any]) -> Origin | None:
    """The origin a raw copy's frontmatter names; None for a copy with none, or a broken one (filed as one with none)."""
    try:
        return Origin.from_dict(fm["origin"]) if isinstance(fm.get("origin"), dict) else None
    except InputError:
        return None


def _named(item: Item) -> str:
    return item.origin.key if item.origin else item.filename


def _raw_item(fm: dict[str, Any], text: str) -> Item:
    """A raw copy as an item to file again: the text as it was ingested, with its title, origin and resource."""
    filename = f"{str(fm.get('title') or 'untitled').replace('/', ' ')}.md"  # the title is title_of(filename) again
    resource = fm.get("resource") if isinstance(fm.get("resource"), str) else None
    return Item(filename, text=text, origin=_origin_of(fm), resource=resource)


def batch_order(item: Item) -> tuple[bool, str, str]:
    """Oldest original first, those with no date last, then by name: the same batch is always filed the same way."""
    modified = item.origin.modified if item.origin else None
    return modified is None, modified or "", item.filename


@dataclass
class Purged:
    """What `Wiki.purge` removed (or, with `dry_run`, would remove), and what it filed again."""

    notes: list[str] = field(default_factory=list)  # bundle paths of the notes deleted
    sources: list[str] = field(default_factory=list)  # raw copies deleted ("/raw/…"): the origin's, and the old copies of the refiled
    archived: list[str] = field(default_factory=list)  # archived versions deleted ("/.archive/…")
    unlinked: list[str] = field(default_factory=list)  # remaining notes that lost a link to a note deleted
    refiled: list[str] = field(default_factory=list)  # the other documents of the notes deleted, filed again from their raw copies
    filed: list[str] = field(default_factory=list)  # the notes those made or merged into
    log_lines: int = 0  # lines of log.md now saying "[purged]"
    errors: list[tuple[str, str]] = field(default_factory=list)  # (document, message) of those that could not be filed again
    stopped: str | None = None  # why filing them again ended before the last (a model failing); None when it ran to the end

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Planned:
    """A window of `ingest_many` read and planned, waiting to be filed."""

    reads: list[Read | Exception] = field(default_factory=list)  # CROW: what the read phase found, per item
    single: int | None = None  # the one document of the window to file, when there is just one
    placed: dict[int, str] = field(default_factory=dict)  # the batch plan's groups: item -> the folder (label) its note goes to
    fixed: bool = False  # a rebuild: the folders are the ones there are (see `StepContext.fixed`), and nothing splits


@dataclass
class Read:
    """A source read and checked for "unchanged", waiting to be summarized: the first half of preparing a document."""

    librarian: Librarian
    usage: UsageReport  # what reading it cost (an extract that asks a model)
    source: Source | None = None  # None when `known`
    known: IngestResult | None = None  # the origin is already filed with this content or version
    spilled: Path | None = None  # the file holding the source's whole text, when `source.text` is only its opening (`_spill`)


class Wiki:
    def __init__(
        self,
        cfg: WikiConfig,
        *,
        llm: LLM | None = None,
        classifier: Classifier | None = None,
        steps: Steps | None = None,
        author: str | None = None,
    ) -> None:
        """Models are built from `cfg` unless given; given ones must share one UsageTracker.

        Without `steps`, `cfg.mode` picks CLASSIC or CROW (it also decides whether the classifier is built).

        `author` is who the changes are made for: every log line a changing method writes ends with ` · by <author>`.
        A method's own `author=` wins. Declared, not verified. None writes the log as it always was.
        """
        self.cfg = cfg
        self.author = author
        self.steps = steps or (CROW if cfg.mode == "crow" else CLASSIC)
        self.tracker = llm.usage if llm else classifier.usage if classifier else UsageTracker(cfg.usage_log)
        if classifier is not None and classifier.usage is not self.tracker:
            raise ValueError("the llm and the classifier must record into the same UsageTracker")
        self.store = WikiStore(cfg.bundle, actor=cfg.actor)
        self.prompts = Prompts(cfg.prompts_dir)
        self.llm = llm or LLM(cfg.llm, self.tracker)
        self.classifier = classifier
        self._preparing = threading.BoundedSemaphore(cfg.concurrency)
        if cfg.mode == "crow" and classifier is None:
            cache = SqliteCache(cfg.bundle / CACHE_FILE) if cfg.classifier.cache else None
            self.classifier = Classifier(cfg.classifier, self.tracker, cache=cache)

    def close(self) -> None:
        """Release the models' connections (a model passed in is closed only if it made its own client)."""
        self.llm.close()
        if self.classifier is not None:
            self.classifier.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @classmethod
    def from_env(cls, **overrides: Any) -> Wiki:
        return cls(WikiConfig.from_env(**overrides))

    def librarian(self, *, fixed: bool = False) -> Librarian:
        """A fresh Librarian for one ingest, running this wiki's steps; `fixed` for a rebuild (`StepContext.fixed`)."""
        return Librarian(self.store, self.llm, self.prompts, self.cfg, self.steps, self.classifier, fixed=fixed)

    def researcher(self) -> Researcher:
        """A fresh Researcher for one question, running this wiki's steps."""
        return Researcher(self.store, self.llm, self.prompts, self.cfg, self.steps, self.classifier)

    def init(self) -> None:
        self.store.init()

    @by_author
    def ingest(
        self,
        text: str,
        *,
        title: str | None = None,
        resource: str | None = None,
        origin: Origin | None = None,
        fields: dict[str, Any] | None = None,
        author: str | None = None,
    ) -> IngestResult:
        """File a text. With an `origin` already ingested with the same hash or version, nothing is done.

        That case returns action "unchanged" (no model call, no file written) with the note that cites the copy.
        `fields` is user data the steps may read (`ctx.source.fields`). Called from several threads, up to `cfg.concurrency`
        of them read and summarize at once and the rest wait; filing is one at a time, under the bundle's lock.

        In CROW mode, a document that makes a folder hold `SPLIT_AT` direct notes or more (and a new multiple of it
        since the last attempt) is followed by one attempt to split that folder (see `_split`).
        """
        return self._ingest(lambda ctx: Source(text, title, resource, origin, dict(fields or {})))

    @by_author
    def ingest_file(
        self,
        data: bytes,
        filename: str,
        *,
        resource: str | None = None,
        origin: Origin | None = None,
        fields: dict[str, Any] | None = None,
        author: str | None = None,
    ) -> IngestResult:
        """Ingest a file (see `files.READABLE` for the types the default `extract` step reads); its name is the title.

        A spreadsheet gives one note per sheet: the result is the first sheet's, the others are in its `parts`.
        """
        item = Item(filename, data=data, resource=resource, origin=origin, fields=dict(fields or {}))
        items = self._sheet_items(item)
        if items == [item]:
            return self._ingest(self._source_of(item))
        results = [self._ingest(self._source_of(one)) for one in items]
        self._after_sheets(item, items, results)
        first = results[0]
        first.parts = results[1:]
        return first

    @by_author
    def ingest_many(
        self,
        items: Iterable[Item],
        *,
        concurrency: int | None = None,
        on_result: Callable[[Item, IngestResult | Exception], None] | None = None,
        plan: bool = True,
        author: str | None = None,
    ) -> BatchReport:
        """File a batch: up to `concurrency` documents (default `cfg.concurrency`) are prepared at once, and filed in turn.

        Items go in order of their origin's `modified` (those without one last), then filename, and are taken in windows
        of `PLAN_BATCH` of them in that order. A window goes through these phases, the next one starting when it is filed:

        1. read: each document is fetched and extracted, and checked for "unchanged", on worker threads (in classic
           mode this is part of preparing it);
        2. plan (CROW only): when two or more documents of the window are to be filed, `plan` lays out the folders they
           need, from their titles and openings (an empty wiki gets its folders; any other, the new ones it needs). With
           `cfg.plan` set to "groups" on a wiki that had no folders when the batch began, `plan_groups` plans every window
           instead, with no model call. A plan that fails (a model error, a reply that is no plan) is logged
           as a warning and the window goes on with the folders there are;
        3. prepare: each is summarized, on worker threads;
        4. file: one document at a time in that order, under the lock, so the wiki is what `ingest_file` called in turn
           would make of it. At most `concurrency` documents are being prepared or wait to be filed.

        In CROW mode, with more than one window and no group plan, the phases go in two rounds: every window is read and
        planned first (1, 2), and only then are they prepared and filed (3, 4), on the folders of all the plans, so a
        document is routed among the folders the whole batch needs and not only among those the windows before it made.
        No note exists yet when a plan is made, so it is shown how full each folder would be: the folder BM25 ranks first
        for each document of the windows already planned (`_count`; no model is asked). The documents read stay in memory
        until their window is filed, but their text, which waits in files of a temporary folder (`_spill`). A batch that
        stops (a spending cap, a failing model) leaves the notes filed so far with their folders; the next run plans the
        documents it is given, on that tree. However a batch ends, it takes back the folders of its plans that are still
        empty, the deepest first (`_drop_empty`).
        With `plan` False no plan is made (`rebuild`): the documents go in the folders there are.

        A window with one document to file (the others "unchanged") makes no plan: it goes as `ingest_file` would, with
        the attempt to split its folder. A window of several never splits.

        An item that cannot be read (InputError), or whose model replies stay unusable after the retry (ReplyInvalid:
        invalid JSON, no content), is a result and the batch goes on; two ReplyInvalid in a row, in filing order, are the
        model's fault, not the documents', and stop the batch as below. Any other failure to prepare one (an endpoint
        that keeps failing, a step that returns what it should not) starts no new item: those in flight finish, the
        documents before the failed one are filed, and everything from it on is in `not_done` with `stopped` saying
        why. A failure while filing stops at once, the same way. `on_result` is called after each item is filed or fails.

        The batch is one session of the store (`WikiStore.session`): the tree is read whole once, then only the folders that
        changed. A note another program or Wiki wrote or edited meanwhile is seen by the next document; one the batch was
        to merge into is never overwritten: that document is a `NoteChanged` result (counted in the report's `conflicts`)
        and the next run files it again.

        A spreadsheet is read first (on worker threads) and spread into one item per sheet ("plan, Budget.csv"):
        those are the items the report and `on_result` give, each with its own result.
        """
        with self.store.session():  # one look at the tree, then only what changed (`WikiStore.session`)
            return self._ingest_many(items, concurrency, on_result, plan)

    def _ingest_many(
        self,
        items: Iterable[Item],
        concurrency: int | None,
        on_result: Callable[[Item, IngestResult | Exception], None] | None,
        plan: bool,
    ) -> BatchReport:
        workers = self.cfg.concurrency if concurrency is None else concurrency
        if not 1 <= workers <= 16:
            raise InputError("concurrency must be from 1 to 16")
        given = list(items)
        with concurrent.futures.ThreadPoolExecutor(workers, thread_name_prefix="llmw2-sheets") as readers:
            groups = list(zip(given, readers.map(self._sheet_items, given), strict=True))
        ordered = sorted((one for _, many in groups for one in many), key=batch_order)
        report = BatchReport()
        if not ordered:
            return report
        self._require_classifier()
        self.store.init()
        # with `plan` "groups", a batch on an empty wiki is planned by groups (`_file_window`)
        fresh = self.cfg.mode == "crow" and self.cfg.plan == "groups" and not self.store.load().subfolders
        seen: dict[str, Any] = {}  # what the batch's group plans have read, for the next window's (`plan_groups`)
        apart = self.cfg.mode == "crow" and not fresh and len(ordered) > PLAN_BATCH  # every plan before the first filing
        counts: dict[str, int] = {}  # apart: notes per folder (label) the windows planned would hold, for the next plan
        made: list[Folder] = []  # the folders the plans of this batch made
        waiting: collections.deque[tuple[int, list[Item], Planned]] = collections.deque()
        # apart: the text of the documents read goes to files here until they are filed, whatever ends the batch
        spill = Path(tempfile.mkdtemp(prefix="llmw2-batch-")) if apart else None
        try:
            for start in range(0, len(ordered), PLAN_BATCH):
                window = ordered[start : start + PLAN_BATCH]
                last = start + PLAN_BATCH >= len(ordered)
                try:
                    ready = self._plan_window(window, workers, fresh, seen, counts if apart and plan else None, plan, last, spill, made)
                except SpendingCap as e:  # nothing of this window is filed; the next run plans it again
                    report.not_done, report.stopped = ordered[start:], str(e)
                    break
                waiting.append((start, window, ready))
                while waiting and not apart:
                    at, one, planned = waiting.popleft()
                    if not self._file_window(one, planned, workers, report, on_result):
                        report.not_done += ordered[at + PLAN_BATCH :]
                        break
                if report.stopped is not None:
                    break
            while waiting and report.stopped is None:  # apart: all the windows are planned, now they are filed
                at, one, planned = waiting.popleft()
                if not self._file_window(one, planned, workers, report, on_result):
                    report.not_done += ordered[at + PLAN_BATCH :]
            waiting.clear()
        finally:
            if spill is not None:
                shutil.rmtree(spill, ignore_errors=True)
        self._drop_empty(made)
        if report.stopped is None:
            done = {id(i): r for i, r in report.results}
            for item, many in groups:
                if len(many) > 1 or many[0] is not item:
                    self._after_sheets(item, many, [done.get(id(one)) for one in many])
        return report

    def _plan_window(
        self,
        window: list[Item],
        workers: int,
        fresh: bool,
        seen: dict[str, Any],
        counts: dict[str, int] | None,
        plan: bool,
        last: bool,
        spill: Path | None = None,
        made: list[Folder] | None = None,
    ) -> Planned:
        """Phases 1 and 2 of one window of `ingest_many` (see there): read it and, in CROW mode, plan its folders.

        `fresh`: a batch with `cfg.plan` "groups" that began on a wiki with no folders. Only then are the documents grouped
        in code (`plan_groups`): a small writer plans a tree poorly, and a small classifier routes poorly among many
        folders. A wiki that has folders is extended by the writer's plan, as its folders were not made from groups and a
        group compared with them by words lands in the wrong one; with `plan` "writer" the writer always plans.

        `counts`, when given, is shown to the plan and then (unless this is the `last` window) gets this window's documents
        added (`_count`). `spill`, when given, is the folder where the text of the documents read goes once planned and counted
        (`_spill`): a batch planned apart would hold all of them in memory. `made`, when given, gets the folders the plan makes."""
        planned = Planned(fixed=not plan)
        if self.cfg.mode != "crow":
            return planned

        def read(i: int) -> Read | Exception:
            try:
                return self._read(self._source_of(window[i]))
            except Exception as e:  # the item's turn to be prepared raises it again, and decides what it means
                return e

        # a context of its own per item: usage spans are context variables
        with concurrent.futures.ThreadPoolExecutor(workers, thread_name_prefix="llmw2-read") as pool:
            started = [pool.submit(contextvars.copy_context().run, read, i) for i in range(len(window))]
            planned.reads = [f.result() for f in started]
        filing = [r for r in planned.reads if isinstance(r, Read) and r.known is None]
        if len(filing) == 1:
            planned.single = next(i for i, r in enumerate(planned.reads) if r is filing[0])
        elif filing and plan:
            documents = [(r.source.title, r.source.text) for r in filing if r.source is not None]
            try:
                if fresh:
                    at = [i for i, r in enumerate(planned.reads) if isinstance(r, Read) and r.known is None and r.source is not None]
                    planned.placed.update({at[j]: label for j, label in self.plan_groups(documents, seen, made).items()})
                else:
                    folders = self.plan(documents, counts)
                    if made is not None:
                        made.extend(folders)
            except SpendingCap:
                raise
            except Exception as e:
                log.warning("the plan of %d documents failed, they are filed in the folders there are: %s", len(documents), e)
        if counts is not None and not last:
            self._count([r.source for r in filing if r.source is not None], counts)
        if spill is not None:
            for r in filing:
                self._spill(r, spill)
        return planned

    def _drop_empty(self, made: list[Folder]) -> None:
        """A batch takes back, when it ends, the folders its plans made that are still empty, the deepest first: a stopped
        batch leaves the next run the tree it would have found had this one never run, and a finished one no folder the
        documents did not use. A folder with a note, or one made by anyone else, stays."""
        with self.store.lock():
            for folder in sorted(made, key=lambda f: f.rel.count("/"), reverse=True):
                self.store.remove_empty_folder(folder.rel, "empty, planned by a batch and not used")

    @staticmethod
    def _spill(read: Read, folder: Path) -> None:
        """Move the text of a source read to a file in `folder`, keeping in memory its opening (all the plan and the count
        read) and the file's path: `_summarize` reads it back. The text stays as it is, whatever characters it holds."""
        source = read.source
        if source is None:
            return
        fd, name = tempfile.mkstemp(dir=folder, suffix=".txt")
        with os.fdopen(fd, "wb") as out:
            out.write(source.text.encode("utf-8", errors="surrogatepass"))
        read.spilled = Path(name)
        read.source = replace(source, text=source.text[: max(PLAN_OPENING, COUNT_CHARS)])

    def _count(self, sources: list[Source], counts: dict[str, int]) -> None:
        """Add to `counts` the folder each source would go to: the folder (at any depth) whose name and description BM25
        ranks first against the document's title and opening, on the tree as it is. No model is asked: the counts only show
        the next plan how full the folders are."""
        folders = [f for f in self.store.load().walk() if not f.is_root]
        if not folders:
            return
        docs = {f.label: f"{f.label.replace('-', ' ')} {f.description}" for f in folders}
        for source in sources:
            label = lexical.rank(f"{source.title or ''} {source.text[:COUNT_CHARS]}", docs)[0]
            counts[label] = counts.get(label, 0) + 1

    def _file_window(
        self,
        window: list[Item],
        planned: Planned,
        workers: int,
        report: BatchReport,
        on_result: Callable[[Item, IngestResult | Exception], None] | None,
    ) -> bool:
        """Phases 3 and 4 of one window of `ingest_many` (see there), after `_plan_window`; False when it stopped the batch,
        `report` saying why."""
        halt = [len(window)]  # the first item that failed for a reason that is not its own: none after it starts
        halting = threading.Lock()
        reads, single, placed, fixed = planned.reads, planned.single, planned.placed, planned.fixed

        def prepare(i: int) -> Prepared | IngestResult | None:
            if i > halt[0]:  # items before the failed one still run: they are filed
                return None
            try:
                if not reads:
                    return self._prepare(self._source_of(window[i]))
                got = reads[i]
                if isinstance(got, Exception):
                    raise got
                return self._summarize(got)
            except (InputError, ReplyInvalid):  # the item's own: `settle` gives it as its result
                raise
            except Exception:
                with halting:
                    halt[0] = min(halt[0], i)
                raise

        futures: list[concurrent.futures.Future[Prepared | IngestResult | None]] = []

        def settle(i: int) -> IngestResult | InputError | ReplyInvalid:
            """Wait for item `i` to be prepared, then file it; its own InputError or ReplyInvalid is its result."""
            try:
                ready = futures[i].result()
            except (InputError, ReplyInvalid) as e:
                return e
            assert ready is not None  # only items after a failure are skipped, and the loop stops at that one
            if isinstance(ready, IngestResult):
                return ready
            self.tracker.check()  # a spending cap reached while this document was prepared: it is not filed, the next run redoes it
            try:
                return self._file(ready, placed.get(i), fixed)
            except NoteChanged as e:  # a note it needed changed meanwhile: nothing of it is written, the next run files it
                log.warning("%s is not filed, the next run files it: %s", window[i].filename, e)
                return e

        with concurrent.futures.ThreadPoolExecutor(workers, thread_name_prefix="llmw2-prepare") as pool:
            try:
                for i, item in enumerate(window):
                    while len(futures) < min(len(window), halt[0] + 1) and len(futures) - i < workers:
                        futures.append(pool.submit(contextvars.copy_context().run, prepare, len(futures)))
                    try:
                        result = settle(i)
                        if isinstance(result, ReplyInvalid) and report.results and isinstance(report.results[-1][1], ReplyInvalid):
                            raise result  # two documents in a row: the model, not them; stop before paying for the rest
                    except Exception as e:  # a failure that is not the item's: nothing from here on is filed
                        report.not_done, report.stopped = window[i:], f"{item.filename}: {e}"
                        return False
                    if i == single and isinstance(result, IngestResult) and not fixed:
                        self._split(result)
                    report.results.append((item, result))
                    if on_result is not None:
                        on_result(item, result)
            finally:
                halt[0] = -1  # whatever ends the loop, items still queued start nothing
        return True

    def _source_of(self, item: Item) -> Callable[[StepContext], Source]:
        """How to get an item's Source, given the context of the preparation: files go through the `extract` step."""
        link = item.resource or (item.origin.link if item.origin else None) or item.filename

        def source(ctx: StepContext) -> Source:
            if item.text is not None:
                text = item.text
            else:
                data = item.data
                if data is None:
                    assert item.fetch is not None  # an Item holds exactly one of text, data and fetch
                    data = item.fetch()
                text = check_extract(self.steps.extract(ctx, data, item.filename))
            name = PurePath(item.filename).name if item.text is None else None  # a sheet or a text is shown as before
            return Source(text, title_of(item.filename), link, item.origin, dict(item.fields), name)

        return source

    def _sheet_items(self, item: Item) -> list[Item]:
        """A spreadsheet as one item per sheet (its text the sheet as CSV, its origin id "<id>#<sheet>"); others as they are.

        The file is read here, on the caller's thread. A failure to fetch or read it is kept for the item's own turn,
        so that it ends where any other item's failure would.
        """
        if item.text is not None or not is_spreadsheet(item.filename):
            return [item]
        try:
            data = item.data if item.data is not None else item.fetch() if item.fetch is not None else b""
            found = sheet_sources(data, item.filename)
        except Exception as e:  # raised again when the item is prepared: an InputError is its result, others stop
            error = e

            def fail() -> bytes:
                raise error

            return [replace(item, data=None, fetch=fail)]
        stem = PurePath(item.filename).stem
        return [
            Item(
                f"{stem}, {name}.csv",  # the title is the name less its suffix: a sheet "v1.2" keeps its dot
                text=text,
                origin=replace(item.origin, id=f"{item.origin.id}#{name}") if item.origin else None,
                resource=item.resource or (item.origin.link if item.origin else None) or item.filename,
                fields=dict(item.fields),
            )
            for name, text in found
        ]

    def _after_sheets(self, item: Item, sheets: list[Item], results: Sequence[IngestResult | Exception | None]) -> None:
        """After the sheets of a spreadsheet are filed: link their notes, and mark the sheets no longer there removed."""
        notes = list(dict.fromkeys(r.note for r in results if isinstance(r, IngestResult) and r.note))
        with self.store.lock():
            for before, after in itertools.pairwise(notes):  # each sheet to the next: a chain, two links at most
                self.store.link(self.store.read(before), self.store.read(after))
            if item.origin is not None and all(isinstance(r, IngestResult) for r in results):
                present = {one.origin.key for one in sheets if one.origin is not None}
                for key in sorted(self.store.origin_keys(item.origin.key + "#") - present):
                    self.store.mark_origin_removed(key)

    def _ingest(self, source: Callable[[StepContext], Source]) -> IngestResult:
        with self._preparing:  # at most `cfg.concurrency` calls prepare at once; they wait here, not for the lock
            ready = self._prepare(source)
        if isinstance(ready, IngestResult):
            return ready
        result = self._file(ready)
        self._split(result)
        return result

    def _prepare(self, source: Callable[[StepContext], Source]) -> Prepared | IngestResult:
        """Everything an ingest does before it reads the tree: no lock, and safe to run on several threads at once.

        Reads the source (`_read`) and summarizes it (`_summarize`). Gives the result right away when the origin is
        already filed with this content or version.
        """
        return self._summarize(self._read(source))

    def _read(self, source: Callable[[StepContext], Source]) -> Read:
        """Extract the source and check "unchanged"."""
        librarian = self.librarian()
        with self.tracker.span() as used:
            src = source(librarian.ctx)
            if not src.text.strip():
                raise InputError("nothing to ingest: the source is empty")
            self._require_classifier()
            self.store.init()
            if src.origin is not None and (known := self._unchanged(src.origin, src.text) or self._adopt(src.origin, src.text)):
                return Read(librarian, used, known=known)
        return Read(librarian, used, src)

    def _summarize(self, read: Read) -> Prepared | IngestResult:
        """What follows `_read`: the result of an unchanged document, or the summarized source ready to be filed."""
        if read.known is not None:
            read.known.usage = read.usage
            return read.known
        assert read.source is not None
        source = read.source
        if read.spilled is not None:
            source = replace(source, text=read.spilled.read_bytes().decode("utf-8", errors="surrogatepass"))
        with self.tracker.span() as used:
            ready = read.librarian.prepare(source)
        used.merge(read.usage)  # extract and summarize both
        ready.usage = used
        return ready

    def _file(self, prepared: Prepared, folder: str | None = None, fixed: bool = False) -> IngestResult:
        """File what `_prepare` made, one writer at a time; the origin is checked again, as it may have been filed meanwhile.

        `folder` (a label) is where a group of the batch plan goes: the note is filed there without routing. `fixed`: a
        rebuild, which opens no folder."""
        with self.store.lock(), self.tracker.filing():  # the librarian reads the tree, then writes on what it read
            origin = prepared.source.origin
            if origin is not None and (known := self._unchanged(origin, prepared.source.text)) is not None:
                known.usage = prepared.usage
                return known
            try:
                return self.librarian(fixed=fixed).file(prepared, planned=folder)
            except BaseException:
                self.store.forget()  # what a batch kept of the tree may not be what is on disk now
                raise

    def origin(self, key: str) -> OriginRecord | None:
        """The latest raw copy cited by a note for an origin key (`Origin.key`), or None if no note cites one.

        A spreadsheet has none under its own key: its sheets are filed as "<key>#<sheet>". So a sync fetches it again
        whenever it is listed, and each sheet whose text is unchanged costs no model call.
        """
        return self.store.find_origin(key)

    @by_author
    def mark_removed(self, key: str, *, author: str | None = None) -> OriginRecord | None:
        """The original was deleted at its source: its raw copies are marked, its notes stay. None if unknown.

        A spreadsheet's sheets ("<key>#<sheet>") are marked with it.
        """
        with self.store.lock():
            record = self.store.mark_origin_removed(key)
            for sheet in sorted(self.store.origin_keys(key + "#")) if is_spreadsheet(key) else []:
                record = self.store.mark_origin_removed(sheet) or record
            return record

    def _adopt(self, origin: Origin, text: str) -> IngestResult | None:
        """The result of an origin no raw copy has, when a copy with this text was filed without one (a single ingest).

        The copy takes the origin, so the next sync recognises it: nothing is filed again.
        """
        with self.store.lock():
            if self.store.find_origin(origin.key) is None and self.store.adopt_raw(origin, text):
                return self._unchanged(origin, text)
        return None

    def _unchanged(self, origin: Origin, text: str) -> IngestResult | None:
        """The result to give back when this origin is already filed with the same content or version."""
        record = self.store.find_origin(origin.key)
        if record is None or record.removed:
            return None
        if record.hash != content_hash(text) and not (origin.version and origin.version == record.version):
            return None
        rel = record.notes[-1] if record.notes else ""
        title = self.store.read(rel).title if rel else ""
        folder = "/" + rel.rpartition("/")[0]
        return IngestResult("unchanged", rel, title, folder, [], [], [], UsageReport())

    def ask(self, question: str, where: dict[str, Any] | None = None) -> Answer:
        """Answer a question from the wiki. `where` (field -> value) restricts it to the notes whose `fields` hold all of
        them, compared as text. It filters what is read: the rest of the wiki (its pages, links and index) shows every note."""
        if not question.strip():
            raise InputError("empty question")
        self._require_classifier()
        return self.researcher().ask(question, where)

    def _require_classifier(self) -> None:
        """In CROW mode, a classifier without its key stops here, before any model call or file."""
        if self.cfg.mode == "crow" and self.classifier is not None:
            self.classifier.require_key()

    def tree(self) -> Folder:
        return self.store.load()

    @by_author
    def create_folder(self, parent: str, name: str, description: str, *, author: str | None = None) -> Folder:
        """Create a folder by hand under `parent` ("/" or "/finance"), named and described as the librarian would.

        Both are required: the description is the line the librarian routes by. A top-level folder made by hand counts in
        `cfg.max_top_folders`, and is never refused for it: with as many as the limit the wiki is closed, and no other is opened.
        InputError for a missing name or description, a name already taken, or a parent at max_depth (the librarian would
        never file below it); FileNotFoundError for no parent.
        """
        return self._create_folder(parent, name, description)

    def _create_folder(self, parent: str, name: str, description: str, *, limit: bool = False, over_limit: bool = False) -> Folder:
        """`create_folder`, with the check on the limit of top-level folders (`limit`) made under the same lock as the creation."""
        if not slugify(name, max_len=40, fallback=""):
            raise InputError("a folder needs a name, in letters or digits")
        if not description.strip():
            raise InputError("a folder needs a description: one line saying what belongs in it")
        self.store.init()
        with self.store.lock():
            at = self.store.load().find(parent)
            if at is None:
                raise FileNotFoundError(parent)
            if at.depth >= self.cfg.max_depth:
                raise InputError(f"no folder under {at.label}: the librarian files at most {self.cfg.max_depth} levels deep (max_depth)")
            if limit:
                self._room_for_area(over_limit)
            folder, created = self.store.create_folder(at, name, description)
            if not created:
                raise InputError(f"{folder.label} already exists")
            return folder

    @by_author
    def plan(
        self, documents: Iterable[tuple[str | None, str]], counts: dict[str, int] | None = None, *, author: str | None = None
    ) -> list[Folder]:
        """Lay out the folders the documents about to be filed need, as (title, text); the folders made.

        One LLM call reads each document's title and opening. In an empty wiki it proposes a few broad areas and the
        specific subjects several documents share; the folders are made empty, and filing then routes into them (each
        would otherwise be made, and described, from the one document that opened it). In a wiki that has folders it is
        shown them, with how many notes each holds, and proposes only the new ones: an existing folder is never
        renamed, moved or described again, and no note moves. Nothing is done for fewer than two documents. Openings
        are sampled evenly when they do not all fit in one call (`effective_read_chars`).

        A top-level folder the plan proposes when the wiki already has `cfg.max_top_folders` of them is left out, and with it its
        subfolders; the prompt then says the top-level areas are fixed. Top-level folders made by hand (`create_folder`) count in the
        limit: as many as the limit closes the wiki to new ones. `counts` (label -> number) adds to each folder's notes the
        ones about to be filed there: `ingest_many` plans all the windows of a batch before filing any, so the tree shows none of them.

        A reply that is no plan raises (ReplyInvalid), as does a model that fails; `ingest_many` calls this for every
        window of a batch and goes on without the folders, logging a warning. The log line of each folder made ends
        with ", by plan" or ", by plan_more".
        """
        lines = [f"- {(title or '').strip() or '(untitled)'}: {' '.join(text.split())[:PLAN_OPENING]}" for title, text in documents]
        self.store.init()
        if len(lines) < 2:
            return []
        room = self.cfg.effective_read_chars - PROMPT_ROOM
        while len(lines) > 2 and sum(len(line) + 1 for line in lines) > room:
            lines = lines[::2]
        depth = min(PLAN_DEPTH, self.cfg.max_depth)
        librarian = self.librarian()
        ctx = librarian.ctx
        existing = [f for f in self.store.load().walk() if not f.is_root]
        if existing:
            held = counts or {}
            tree = "\n".join(
                f"- {f.label}: {f.description or '(no description)'}; {len(f.notes) + held.get(f.label, 0)} notes" for f in existing
            )
            fixed = len(self.store.load().subfolders) >= self.cfg.max_top_folders
            plan = ctx.ask_json(
                "librarian/plan_more",
                FolderPlan,
                tree=tree,
                documents="\n".join(lines),
                roots=PLAN_ROOTS_FIXED if fixed else PLAN_ROOTS_OPEN,
                depth=depth,
                reply_chars=PLAN_REPLY_CHARS,
            )
        else:
            plan = ctx.ask_json(
                "librarian/plan",
                FolderPlan,
                documents="\n".join(lines),
                top=self.cfg.max_top_folders,
                depth=depth,
                reply_chars=PLAN_REPLY_CHARS,
            )
        made: list[Folder] = []
        mark = PLAN_MORE_MARK if existing else PLAN_MARK
        prompt, shown = ("librarian/plan_more" if existing else "librarian/plan"), "\n".join(lines)
        with self.store.lock():
            tree_now = self.store.load()
            if not existing and tree_now.subfolders:  # a document was filed meanwhile: its folders stand
                return []
            for planned in plan.folders:  # parents first; one under a folder not made, or too deep, is left out
                parts = [slugify(p, max_len=40, fallback="") for p in planned.path.strip().strip("/").split("/") if p.strip()]
                if not parts or "" in parts or len(parts) > depth or not planned.description.strip():
                    continue
                if librarian.local_writer:  # a small writer copies its prompt ("/area/subject", "new-top-level-area")
                    parts = [echo.own_name(p, shown) or p for p in parts[:-1]] + [echo.own_name(parts[-1], shown)]
                    if not parts[-1] or not echo.own_text(planned.description, shown, ctx.prompts, prompt):
                        log.warning("plan: folder %s is not named or described from the documents, left out", planned.path)
                        continue
                parent = tree_now.find("/".join(parts[:-1]))
                full = parent is not None and parent.is_root and len(parent.subfolders) >= self.cfg.max_top_folders
                if parent is None or (full and parent.subfolder(parts[-1]) is None):
                    continue  # its parent is not made, or it would be the top-level folder after the limit
                folder, created = self.store.create_folder(parent, parts[-1], planned.description, mark)
                if created:
                    made.append(folder)
        return made

    @by_author
    def plan_groups(
        self,
        documents: list[tuple[str | None, str]],
        seen: dict[str, Any] | None = None,
        made: list[Folder] | None = None,
        *,
        author: str | None = None,
    ) -> dict[int, str]:
        """The batch plan for `cfg.plan` "groups" (see `_file_window`): the documents that share a subject, grouped in code
        (`grouping.groups`), each group filed together; the folder (label) for each document of a group, by its index in
        `documents`. No model call.

        A group goes to the folder (already there before this plan) whose notes' sources it resembles
        (`grouping.closest`), else to a new top-level folder named from the group's own words (`grouping.name`) and
        described by the titles of its first three documents; the log line of the folder ends with ", by plan". A
        document in no group, or in a group whose documents share no word to name it by, is routed as usual.

        `seen`: what earlier plans of the same batch read (the raw copies, the words of each text), kept in it for the next
        one: every window compares its groups with all the folders' sources, which it would otherwise read and split again.
        `made`, when given, gets the folders it makes."""
        texts = [text for _, text in documents]
        found = grouping.groups(texts)
        if not found:
            return {}
        self.store.init()
        placed: dict[int, str] = {}
        with self.store.lock():
            root = self.store.load()
            # the folders' documents as ingested, not their notes: a group is compared with texts of the same kind
            memo = {} if seen is None else seen
            bodies: dict[str, str] = memo.setdefault("bodies", {})
            split: dict[str, list[str]] = memo.setdefault("words", {})

            def words(text: str) -> list[str]:
                if text not in split:
                    split[text] = lexical.words(text)
                return split[text]

            existing = {f.label: [self.store.source_text(n, bodies) for n in f.notes] for f in root.walk() if not f.is_root}
            # every group against the folders there before this plan (one made below is not joined by the next group)
            joins = grouping.closest_each([[texts[i] for i in group] for group in found], existing, words=words)
            for group, label in zip(found, joins, strict=True):
                if label is None:
                    name = grouping.name(texts, group)
                    if not name or len(self.store.load().subfolders) >= self.cfg.max_top_folders:
                        continue  # no word to name it by, or the wiki has its top-level folders: routed as usual
                    description = "; ".join((documents[i][0] or "").strip() or " ".join(texts[i].split()[:8]) for i in group[:3])
                    folder, created = self.store.create_folder(self.store.load(), name, description, PLAN_MARK)
                    if created and made is not None:
                        made.append(folder)
                    label = folder.label
                for i in group:
                    placed[i] = label
        return placed

    def _split(self, result: IngestResult) -> None:
        """CROW: after one document filed alone, one attempt to split its folder when it holds too many notes directly.

        The folder is not the root, is above the depth `plan` lays out (`PLAN_DEPTH`, and `max_depth`), holds `SPLIT_AT`
        notes or more, and a new multiple of `SPLIT_AT` of them since the last attempt (the "Split" lines of `log.md`,
        read only then). One LLM call lists the notes and the subfolders and proposes subfolders with the notes that go
        there; they are made and the notes moved, links and archived copies following. The attempt is logged with the
        multiple tried, also when it opens nothing or fails, so a new `Wiki` on the same bundle does not try again at
        that multiple, only at the next. A folder renamed or moved, or a deleted `log.md`, gets one more attempt. A
        failure never reaches the caller: it is logged as a warning and traced as the decision "split" of `result`.
        """
        if self.cfg.mode != "crow" or result.action == "unchanged":
            return
        folder = self.store.load().find(result.folder)
        if folder is None or folder.is_root or folder.depth >= min(PLAN_DEPTH, self.cfg.max_depth) or len(folder.notes) < SPLIT_AT:
            return
        path = self.store.root / LOG
        multiple = len(folder.notes) // SPLIT_AT
        if multiple <= last_split_at(read_utf8_or_latin1(path) if path.exists() else "", folder.rel) // SPLIT_AT:
            return
        opened: list[str] = []
        try:
            with self.tracker.filing():  # part of filing the document: the cap waits for it
                self._split_folder(folder, opened)
            result.decisions.append(Decision("split", "llm", f"{folder.label}: {len(opened)} opened"))
        except Exception as e:
            log.warning("splitting %s failed: %s", folder.label, e)
            result.decisions.append(Decision("split", "llm", f"{folder.label}: error", fallback=True))
        text = f"at {multiple * SPLIT_AT} notes, {len(opened)} opened" + (f": {', '.join(opened)}" if opened else "")
        with self.store.lock():
            self.store.append_log(SPLIT_VERB, text, f"{folder.rel}/index.md", folder.label)

    def _split_folder(self, folder: Folder, opened: list[str]) -> None:
        """Ask for the subfolders of `folder` and make them, moving the notes; the labels made are appended to `opened`."""
        notes = list(folder.notes)
        listing = "\n".join(f"{i}. {n.title}: {' '.join((n.summary or '').split())[:200]}" for i, n in enumerate(notes, start=1))
        subfolders = "\n".join(f"- {c.label}: {c.description}" for c in folder.subfolders) or "(none)"
        librarian = self.librarian()
        ctx = librarian.ctx
        plan = ctx.ask_json(
            "librarian/split",
            SplitPlan,
            notes=listing,
            folder=folder.label,
            description=folder.description or "(no description)",
            subfolders=subfolders,
            reply_chars=SPLIT_REPLY_CHARS,
        )
        taken: set[int] = set()
        with self.store.lock():
            here = self.store.load().find(folder.label)
            if here is None:
                return
            parent_words = set(re.split(r"[/-]", folder.label))
            for proposed in plan.folders:
                name = slugify(proposed.name, max_len=40, fallback="")
                numbers = sorted({i for i in proposed.notes if 1 <= i <= len(notes)} - taken)
                if not name or not proposed.description.strip() or len(numbers) < 2:
                    continue
                # a small writer's name made of the folder's own path ("benefits-benefits") or of the prompt is no subject
                if librarian.local_writer:
                    name = echo.own_name(proposed.name, listing, parent_words)
                    if not name or not echo.own_text(proposed.description, listing, ctx.prompts, "librarian/split"):
                        continue
                sub, created = self.store.create_folder(here, name, proposed.description, SPLIT_MARK)
                if not created:  # an existing subfolder is never moved into
                    continue
                opened.append(sub.label)
                taken.update(numbers)
                for i in numbers:
                    try:
                        self.store.move_note(notes[i - 1].rel, sub.rel)
                    except InputError:  # the note went meanwhile: the others go on
                        continue

    @by_author
    def move_note(self, path: str, folder: str, *, author: str | None = None) -> Note:
        """Move a note ("finance/pricing.md") into a folder ("/sourcing" or "sourcing"; "/" is the root).

        Every link to and from it follows, and so do its archived copies. InputError for no such note or folder, a
        path that is not a note, or a note already in that folder.
        """
        with self.store.lock():
            return self.store.move_note(path, folder)

    @by_author
    def describe_folder(self, path: str, description: str, *, author: str | None = None) -> Folder:
        """Change the one-line description of a folder ("/finance"), the line the librarian routes by."""
        with self.store.lock():
            return self.store.describe_folder(path, description)

    # -- the top-level areas: made, proposed, renamed and merged by users, never by the wiki ------------------

    @by_author
    def create_area(self, name: str, description: str, over_limit: bool = False, *, author: str | None = None) -> Folder:
        """Create a top-level folder by hand, counted in `cfg.max_top_folders`: InputError when the wiki has that many,
        unless `over_limit`. Otherwise as `create_folder` at "/"."""
        return self._create_folder("/", name, description, limit=True, over_limit=over_limit)

    def _room_for_area(self, over_limit: bool) -> None:
        roots = len(self.store.load().subfolders)
        if roots >= self.cfg.max_top_folders and not over_limit:
            raise InputError(
                f"the wiki already has {roots} top-level folders, the limit (max_top_folders): "
                "merge two of them, or confirm it over the limit"
            )

    @by_author
    def propose_area(self, label: str, description: str, notes: Sequence[str], *, author: str | None = None) -> Proposal:
        """Queue a top-level folder that `label` would name and `notes` would fill, for a later confirm.

        A note is given by its stable id, or by its path ("finance/pricing.md"), which is kept as its id.

        The wiki never proposes an area itself: this is how a user does. InputError for no name or description, a name
        a root already has, or no note, or a note the wiki does not have. Nothing is moved until `confirm_proposal`.
        """
        if not slugify(label, max_len=40, fallback=""):
            raise InputError("an area needs a name, in letters or digits")
        if not description.strip():
            raise InputError("an area needs a description: one line saying what belongs in it")
        if not notes:
            raise InputError("an area needs at least one note")
        with self.store.lock():
            tree = self.store.load()
            every = tree.all_notes()
            found = {n: next((x for x in every if x.id and n in (x.id, x.rel, "/" + x.rel)), None) for n in notes}
            if missing := [n for n, note in found.items() if note is None]:
                raise InputError(f"no note with id or path {', '.join(missing)}")
            ids = list(dict.fromkeys(note.id for note in found.values() if note and note.id))
            queue = read_proposals(self.cfg.bundle)
            slug = slugify(label, max_len=40)
            if tree.subfolder(slug) or any(p.status == OPEN and slugify(p.label, max_len=40) == slug for p in queue):
                raise InputError(f"/{slug} is taken: a top-level folder or an open proposal has that name")
            proposal = Proposal(label.strip(), " ".join(description.split()), ids)
            write_proposals(self.cfg.bundle, [*queue, proposal])
            self.store.append_log("Proposed area", f"{proposal.label} ({notes_count(len(ids))}), proposal {proposal.id}")
            return proposal

    def proposals(self, status: str | None = OPEN) -> list[Proposal]:
        """The queue of proposed areas, oldest first: the open ones, or those with this status ("confirmed", "dismissed"); None: all."""
        return [p for p in read_proposals(self.cfg.bundle) if status is None or p.status == status]

    @by_author
    def dismiss_proposal(self, proposal_id: str, *, author: str | None = None) -> Proposal:
        """Close an open proposal without doing anything. InputError for no open proposal with this id."""
        with self.store.lock():
            queue = read_proposals(self.cfg.bundle)
            proposal = self._open_proposal(queue, proposal_id)
            proposal.status = DISMISSED
            write_proposals(self.cfg.bundle, queue)
            self.store.append_log("Dismissed proposal", f"{proposal.label}, proposal {proposal.id}")
            return proposal

    @by_author
    def confirm_proposal(
        self, proposal_id: str, over_limit: bool = False, *, author: str | None = None
    ) -> tuple[Proposal, list[str]]:
        """Open the proposed area: create the root, counted in `cfg.max_top_folders`, and move into it the proposed notes
        that are still in the wiki; (the proposal, the paths they now have).

        Each moved note records the folder it came from (`moved_from`) and loses its `overflow` mark. Nothing else is
        touched, and nothing is rebuilt. InputError for no open proposal, a name taken meanwhile, none of the notes left,
        or the wiki already at the limit of top-level folders (unless `over_limit`); the proposal then stays open.
        """
        with self.store.lock():
            queue = read_proposals(self.cfg.bundle)
            proposal = self._open_proposal(queue, proposal_id)
            tree = self.store.load()
            if tree.subfolder(slugify(proposal.label, max_len=40)):
                raise InputError(f"/{slugify(proposal.label, max_len=40)} is taken: rename the proposal or the folder")
            self._room_for_area(over_limit)
            wanted = set(proposal.notes)
            present = [n for n in tree.all_notes() if n.id in wanted]
            if not present:
                raise InputError("none of the proposed notes is in the wiki any more: dismiss the proposal")
            folder, _ = self.store.create_folder(tree, proposal.label, proposal.description, log_it=False)
            moved = [n.rel for n in self.store.move_notes([(n.rel, folder.rel) for n in present], mark=True, root=tree)]
            proposal.status = CONFIRMED
            write_proposals(self.cfg.bundle, queue)
            shown = ", ".join(f"/{rel}" for rel in moved[:10]) + (f" and {len(moved) - 10} more" if len(moved) > 10 else "")
            text = f"{folder.description}: {notes_count(len(moved))} moved ({shown})"
            self.store.append_log("Confirmed area", text, f"{folder.rel}/{INDEX}", folder.label)
            return proposal, moved

    @staticmethod
    def _open_proposal(queue: list[Proposal], proposal_id: str) -> Proposal:
        found = next((p for p in queue if p.id == proposal_id and p.status == OPEN), None)
        if found is None:
            raise InputError(f"no open proposal {proposal_id}")
        return found

    @by_author
    def rename_root(self, path: str, new_name: str, *, author: str | None = None) -> Folder:
        """Rename a top-level folder ("/finance"): its notes go with it, links to and from them follow, the description stays.
        InputError for a folder that is not top-level, no name, or a name taken."""
        name = slugify(new_name, max_len=40, fallback="")
        if not name:
            raise InputError("a folder needs a name, in letters or digits")
        with self.store.lock():
            root = self._root_of(path)
            folder = self.store.move_folder(root.rel, name)
            self.store.append_log("Renamed folder", f"{root.label} to {folder.label}", f"{folder.rel}/{INDEX}", folder.label)
            return folder

    @by_author
    def merge_roots(self, source: str, target: str, *, author: str | None = None) -> Folder:
        """Move every note of the top-level folder `source` into the top-level folder `target` and delete `source`.

        Subfolders keep their structure (one `target` has too is merged), links follow, and each note records the folder it
        came from (`moved_from`). InputError for a folder that is not top-level, or the same twice.
        """
        with self.store.lock():
            a, b = self._root_of(source), self._root_of(target)
            if a.rel == b.rel:
                raise InputError(f"{a.label} cannot be merged into itself")
            count = self.store.merge_folders(a.rel, b.rel)
            self.store.append_log("Merged folders", f"{a.label} into {b.label}: {notes_count(count)} moved", f"{b.rel}/{INDEX}", b.label)
            return self.store.load().find(b.rel) or b

    def _root_of(self, path: str) -> Folder:
        """The top-level folder at `path`; InputError for any other."""
        rel = path.strip().strip("/")
        folder = self.store.load().find(rel) if rel else None
        if folder is None or "/" in rel:
            raise InputError(f"{path} is not a top-level folder of the wiki")
        return folder

    @by_author
    def delete_note(self, path: str, *, author: str | None = None) -> Deleted:
        """Delete a note ("finance/pricing.md"); links to it go from the other notes, and raw copies only it cites."""
        with self.store.lock():
            return self.store.delete_note(path)

    @by_author
    def delete_folder(self, path: str, *, author: str | None = None) -> Deleted:
        """Delete a folder ("/finance") with everything in it, as `delete_note` does for each note."""
        with self.store.lock():
            return self.store.delete_folder(path)

    @by_author
    def clear(self, *, author: str | None = None) -> Deleted:
        """Empty the wiki: every note, folder, raw copy and archived version, and the sync cursors. Cannot be undone."""
        with self.store.lock():
            return self.store.clear()

    @by_author
    def rebuild(
        self,
        items: Iterable[Item],
        *,
        concurrency: int | None = None,
        on_result: Callable[[Item, IngestResult | Exception], None] | None = None,
        author: str | None = None,
    ) -> BatchReport:
        """File `items` again on the folders the wiki has: every note, raw copy, archived version and sync cursor goes, the
        folders stay with their descriptions, and the documents are filed (`ingest_many` with no plan) among them.

        No `plan` call is made, so with the same documents, summaries and classifier answers the notes go where they did
        before, apart from the ones that had moved by `_split` into folders that stay. The folders are fixed: a score under
        `tau_path` files the note in the best path's folder (no routing fallback to the LLM), "New subfolder" counts as
        "Here", no folder is created and none is split. Cannot be undone. A rebuild that
        stops (the report says why) leaves the folders and the notes filed so far; run it again to start over.
        """
        with self.store.lock():
            self.store.clear(keep_folders=True)
        return self.ingest_many(items, concurrency=concurrency, on_result=on_result, plan=False)

    @by_author
    def purge(self, key: str, *, author: str | None = None, dry_run: bool = False) -> Purged | None:
        """Take a document out of the wiki for good, with everything written from it. None if no raw copy has this origin key.

        Deleted: its raw copies (a spreadsheet's sheets, "<key>#<sheet>", with it), their archived versions, and every note
        that cites one of them. A note that cites other documents too is deleted all the same, and those documents are
        filed again from their own raw copies, the way `rebuild` files them (`ingest_many` with no plan: on the folders there
        are, no folder made, none split), so no model is asked to take sentences out of a text: what was written from the
        document is not rewritten, it is gone. The links to the notes deleted go from the other notes, index.md is written
        again, and the lines of log.md that name a note or a raw copy deleted say "[purged]" in place of their text; a "Purged"
        line, with the author and without the document's title, is added. `check()` finds nothing wrong afterwards.
        The classifier's cache holds hashes and labels, never text: it stays.

        With `dry_run` nothing is changed: the report says what would go and what would be filed again. If filing the others
        again stops (a model failing) the report has `stopped` and `errors`: their old raw copies stay, no note cites them, and
        a `rebuild` files them. Cannot be undone.
        """
        with self.store.session():  # what the store reads in the first step it keeps for the last
            return self._purge(key, dry_run)

    def _purge(self, key: str, dry_run: bool) -> Purged | None:
        keys = [key, *sorted(self.store.origin_keys(key + "#"))] if is_spreadsheet(key) else [key]
        with self.store.lock():
            raws = [r for k in keys for r in self.store.origin_raws(k)]
            if not raws:
                return None
            root = self.store.load()
            notes = [n for n in root.all_notes() if set(raws).intersection(raw_sources(n.frontmatter))]
            others = self._cited_by(notes, set(raws))  # read before anything is deleted
            gone_rels = {n.rel for n in notes}
            elsewhere = {r for n in root.all_notes() if n.rel not in gone_rels for r in raw_sources(n.frontmatter)}  # filing adds none
            report = Purged(refiled=[_named(item) for item, _ in others])
            if dry_run:
                went = {n.rel for n in notes}
                report.notes, report.sources = sorted(went), raws
                went_archived = self.store.archived_of(went.__contains__, raws)
                report.archived = [f"/{p.relative_to(self.store.root).as_posix()}" for p in went_archived]
                return report
            kept = {r for _, olds in others for r in olds}
            deleted = self.store.purge_notes(root, notes, raws, keep=kept)
            report.notes, report.sources = deleted.notes, deleted.sources
            report.archived, report.unlinked = deleted.archived, deleted.unlinked
            # a link is written "[title](path)", and a raw copy's file name holds the title: all are names of the note
            report.log_lines = self.store.scrub_log(
                [*deleted.notes, *(f"[{n.title}](" for n in notes), *(PurePath(r).name for r in deleted.sources)]
            )
        filed: set[int] = set()
        if others:
            done = self.ingest_many([item for item, _ in others], plan=False)
            report.stopped = done.stopped
            report.errors = [(_named(item), str(r)) for item, r in done.results if isinstance(r, Exception)]
            report.errors += [(_named(item), "not filed: the run stopped") for item in done.not_done]
            report.filed = [r.note for _, r in done.results if isinstance(r, IngestResult) and r.note]
            filed = {id(item) for item, r in done.results if isinstance(r, IngestResult)}
        with self.store.lock():
            if filed:  # the old raw copies of what was filed again go, unless a note still cites them
                old = [r for item, olds in others if id(item) in filed for r in olds if r not in elsewhere]
                gone = Deleted()
                self.store.remove_raws(old, gone)
                report.sources += gone.sources
                report.log_lines += self.store.scrub_log(PurePath(r).name for r in gone.sources)
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]  # the key may name the document: only a reference to it
            self.store.append_log(
                "Purged",
                f"origin {digest}: {len(report.notes)} notes, {len(report.sources)} raw copies and {len(report.archived)} archived "
                f"versions deleted, {len(report.refiled)} other documents filed again",
            )
        return report

    def _cited_by(self, notes: list[Note], purged: set[str]) -> list[tuple[Item, list[str]]]:
        """The other documents the notes cite, as `rebuild` would file them (an origin from its latest raw copy, a copy with no
        origin once per text), each with the raw copies of it that the notes cite."""
        copies: dict[str, tuple[dict[str, Any], str]] = {}
        groups: dict[str, list[str]] = {}  # an origin's key, else the text's hash -> the raw copies of it cited
        for resource in dict.fromkeys(r for n in notes for r in raw_sources(n.frontmatter) if r not in purged):
            copies[resource] = self.store.read_raw(resource)
            fm, text = copies[resource]
            origin = _origin_of(fm)
            groups.setdefault(origin.key if origin else content_hash(text), []).append(resource)
        found: list[tuple[Item, list[str]]] = []
        keyed = [g for g, rs in groups.items() if _origin_of(copies[rs[0]][0]) is not None]
        latest_of = self.store.origins_raws(keyed)  # one look at the raw copies for all the origins
        for group, resources in groups.items():
            latest = resources[0]
            if group in latest_of:
                latest = latest_of[group][-1]
                copies.setdefault(latest, self.store.read_raw(latest))
            found.append((_raw_item(*copies[latest]), resources))
        return found

    def raw_items(self) -> list[Item]:
        """The wiki's own raw copies as items for `rebuild`, read now (a rebuild deletes them): the text as it was ingested.

        An origin is filed once, from its latest copy; a copy with no origin, once per text. The title is the copy's, and the
        origin and the resource are kept, so a later `sync` of the same folder finds each document already filed. A document
        marked deleted at its source is filed again as any other: the mark is not kept.
        """
        latest: dict[str, tuple[int, Item]] = {}
        seen: set[str] = set()
        items: list[Item] = []
        for fm, text in self.store.raw_copies():
            origin = _origin_of(fm)
            item = _raw_item(fm, text)
            if origin is None:
                if (digest := content_hash(text)) not in seen:
                    seen.add(digest)
                    items.append(item)
                continue
            seq: int = fm["seq"] if isinstance(fm.get("seq"), int) else 0
            if origin.key not in latest or seq >= latest[origin.key][0]:
                latest[origin.key] = (seq, item)
        return [*items, *(item for _, item in latest.values())]

    def find_note(self, note_id: str) -> Note | None:
        """The note of the tree with this stable `id` (archived copies are not searched), or None."""
        return next((n for n in self.store.load().all_notes() if n.id == note_id), None)

    @by_author
    def audit_routes(
        self, min_confidence: float = AUDIT_MIN, apply: bool = False, *, author: str | None = None
    ) -> list[RouteProposal]:
        """The notes the CROW classifier would file in another folder today, with the probability it gives that folder.

        Each note of the tree gets the one flat Choice `route` asks (every folder, and None of these), never the LLM and
        never the per-level routing: a note whose Choice ends in "None of these" gets no proposal, and no LLM call is
        made. A proposal needs a folder that is neither the note's own, nor below it (moving a note deeper into its own branch
        was most of the wrong proposals measured), nor above it (a note is never moved up, out of a subfolder: that would
        undo a plan or a split), and a top probability `>= min_confidence`; so it is a move to another branch.
        With `apply`, each proposal is carried out with `move_note`, one at a time under the lock; `applied` says which moved.
        ConfigError in classic mode or without a classifier.
        """
        if self.cfg.mode != "crow" or self.classifier is None:
            raise ConfigError("the routing audit needs CROW mode and its classifier")
        self._require_classifier()
        ctx = StepContext(self.llm, self.classifier, self.prompts, self.cfg, system_prompt="librarian/system")
        root = self.store.load()
        proposals: list[RouteProposal] = []
        for note in root.all_notes():
            ctx.decisions = []
            cand = Candidate(note.title, note.summary, note.tags, note.main_body())
            routed = steps_crow.audit_route(ctx, root, cand)
            current = note.folder.label if note.folder else "/"
            if routed is None or routed[0].label == current or routed[1] < min_confidence:
                continue
            if current != "/" and routed[0].label.startswith(current + "/"):  # deeper in its own branch: a tidy-up, not a misroute
                continue
            if current.startswith(routed[0].label.rstrip("/") + "/"):  # up in its own branch: never undo a plan or a split
                continue
            proposals.append(RouteProposal(note.rel, note.id, note.title, current, routed[0].label, routed[1]))
        if apply:
            for proposal in proposals:
                try:
                    with self.store.lock():
                        self.store.move_note(proposal.path, proposal.proposed)
                    proposal.applied = True
                except InputError:  # the note went meanwhile, or is already there: the others go on
                    continue
        return proposals

    def taxonomy_health(self, min_filed: int = HEALTH_MIN_FILED, max_share: float = HEALTH_MAX_SHARE) -> list[FolderHealth]:
        """Per folder and month of `log.md`: notes filed, new subfolders opened and fallbacks, flagged when they are too many.

        Reads the log alone, no model. A row is flagged when `filed >= min_filed` and
        `(new_subfolders + fallbacks) / filed > max_share`: a folder that keeps needing subfolders or the LLM's help.
        """
        path = self.store.root / LOG
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        return taxonomy_health(text, min_filed, max_share)

    def check(self) -> list[Problem]:
        return check(self.cfg.bundle)

    @property
    def usage(self) -> UsageReport:
        return self.tracker.snapshot()
