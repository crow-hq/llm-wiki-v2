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

import concurrent.futures
import contextvars
import itertools
import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import PurePath
from typing import Any, Self

from llmw2.agents import steps_crow
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
from llmw2.bundle.store import Deleted, WikiStore
from llmw2.bundle.tree import LOG, Folder, Note, slugify
from llmw2.config import PROMPT_ROOM, WikiConfig
from llmw2.errors import ConfigError, InputError, ReplyInvalid
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageReport, UsageTracker

PLAN_TOP = 10  # top-level folders `plan` may lay out, as shared/folder_rules wants few
PLAN_DEPTH = 3  # levels `plan` may lay out (and never below max_depth)
PLAN_OPENING = 220  # characters of each document's opening `plan` reads
PLAN_REPLY_CHARS = 8_000  # about 40 folders, each a path and a one-line description
PLAN_BATCH = 50  # documents per plan: the batch size the plan was measured with
SPLIT_AT = 12  # direct notes that make a folder worth splitting: the structure limit the measures used
SPLIT_REPLY_CHARS = 6_000  # about 3000 tokens: a few subfolders, each with a description and the numbers of its notes

log = logging.getLogger(__name__)


def batch_order(item: Item) -> tuple[bool, str, str]:
    """Oldest original first, those with no date last, then by name: the same batch is always filed the same way."""
    modified = item.origin.modified if item.origin else None
    return modified is None, modified or "", item.filename


@dataclass
class Read:
    """A source read and checked for "unchanged", waiting to be summarized: the first half of preparing a document."""

    librarian: Librarian
    usage: UsageReport  # what reading it cost (an extract that asks a model)
    source: Source | None = None  # None when `known`
    known: IngestResult | None = None  # the origin is already filed with this content or version


class Wiki:
    def __init__(
        self,
        cfg: WikiConfig,
        *,
        llm: LLM | None = None,
        classifier: Classifier | None = None,
        steps: Steps | None = None,
    ) -> None:
        """Models are built from `cfg` unless given; given ones must share one UsageTracker.

        Without `steps`, `cfg.mode` picks CLASSIC or CROW (it also decides whether the classifier is built).
        """
        self.cfg = cfg
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
            self.classifier = Classifier(cfg.classifier, self.tracker)

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

    def librarian(self) -> Librarian:
        """A fresh Librarian for one ingest, running this wiki's steps."""
        return Librarian(self.store, self.llm, self.prompts, self.cfg, self.steps, self.classifier)

    def researcher(self) -> Researcher:
        """A fresh Researcher for one question, running this wiki's steps."""
        return Researcher(self.store, self.llm, self.prompts, self.cfg, self.steps, self.classifier)

    def init(self) -> None:
        self.store.init()

    def ingest(
        self,
        text: str,
        *,
        title: str | None = None,
        resource: str | None = None,
        origin: Origin | None = None,
        fields: dict[str, Any] | None = None,
    ) -> IngestResult:
        """File a text. With an `origin` already ingested with the same hash or version, nothing is done.

        That case returns action "unchanged" (no model call, no file written) with the note that cites the copy.
        `fields` is user data the steps may read (`ctx.source.fields`). Called from several threads, up to `cfg.concurrency`
        of them read and summarize at once and the rest wait; filing is one at a time, under the bundle's lock.

        In CROW mode, a document that makes a folder hold `SPLIT_AT` direct notes or more (and a new multiple of it
        since the last attempt) is followed by one attempt to split that folder (see `_split`).
        """
        return self._ingest(lambda ctx: Source(text, title, resource, origin, dict(fields or {})))

    def ingest_file(
        self,
        data: bytes,
        filename: str,
        *,
        resource: str | None = None,
        origin: Origin | None = None,
        fields: dict[str, Any] | None = None,
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

    def ingest_many(
        self,
        items: Iterable[Item],
        *,
        concurrency: int | None = None,
        on_result: Callable[[Item, IngestResult | Exception], None] | None = None,
    ) -> BatchReport:
        """File a batch: up to `concurrency` documents (default `cfg.concurrency`) are prepared at once, and filed in turn.

        Items go in order of their origin's `modified` (those without one last), then filename, and are taken in windows
        of `PLAN_BATCH` of them in that order. A window goes through these phases, the next one starting when it is filed:

        1. read: each document is fetched and extracted, and checked for "unchanged", on worker threads (in classic
           mode this is part of preparing it);
        2. plan (CROW only): when two or more documents of the window are to be filed, `plan` lays out the folders they
           need, from their titles and openings (an empty wiki gets its folders; any other, the new ones it needs). A
           plan that fails (a model error, a reply that is no plan) is logged as a warning and the window goes on with
           the folders there are;
        3. prepare: each is summarized, on worker threads;
        4. file: one document at a time in that order, under the lock, so the wiki is what `ingest_file` called in turn
           would make of it. At most `concurrency` documents are being prepared or wait to be filed.

        A window with one document to file (the others "unchanged") makes no plan: it goes as `ingest_file` would, with
        the attempt to split its folder. A window of several never splits.

        An item that cannot be read (InputError), or whose model replies stay unusable after the retry (ReplyInvalid:
        invalid JSON, no content), is a result and the batch goes on; two ReplyInvalid in a row, in filing order, are the
        model's fault, not the documents', and stop the batch as below. Any other failure to prepare one (an endpoint
        that keeps failing, a step that returns what it should not) starts no new item: those in flight finish, the
        documents before the failed one are filed, and everything from it on is in `not_done` with `stopped` saying
        why. A failure while filing stops at once, the same way. `on_result` is called after each item is filed or fails.

        A spreadsheet is read first (on worker threads) and spread into one item per sheet ("plan, Budget.csv"):
        those are the items the report and `on_result` give, each with its own result.
        """
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
        for start in range(0, len(ordered), PLAN_BATCH):
            if not self._file_window(ordered[start : start + PLAN_BATCH], workers, report, on_result):
                report.not_done += ordered[start + PLAN_BATCH :]
                break
        if report.stopped is None:
            done = {id(i): r for i, r in report.results}
            for item, many in groups:
                if len(many) > 1 or many[0] is not item:
                    self._after_sheets(item, many, [done.get(id(one)) for one in many])
        return report

    def _file_window(
        self,
        window: list[Item],
        workers: int,
        report: BatchReport,
        on_result: Callable[[Item, IngestResult | Exception], None] | None,
    ) -> bool:
        """One window of `ingest_many` (see there for the phases); False when it stopped the batch, `report` saying why."""
        halt = [len(window)]  # the first item that failed for a reason that is not its own: none after it starts
        halting = threading.Lock()
        reads: list[Read | Exception] = []  # CROW: what the read phase found, per item

        def read(i: int) -> Read | Exception:
            try:
                return self._read(self._source_of(window[i]))
            except Exception as e:  # the item's turn to be prepared raises it again, and decides what it means
                return e

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
            return ready if isinstance(ready, IngestResult) else self._file(ready)

        single: int | None = None  # the one document of the window to file, when there is just one
        with concurrent.futures.ThreadPoolExecutor(workers, thread_name_prefix="llmw2-prepare") as pool:
            if self.cfg.mode == "crow":
                # a context of its own per item: usage spans are context variables
                started = [pool.submit(contextvars.copy_context().run, read, i) for i in range(len(window))]
                reads = [f.result() for f in started]
                filing = [r for r in reads if isinstance(r, Read) and r.known is None]
                if len(filing) == 1:
                    single = next(i for i, r in enumerate(reads) if r is filing[0])
                elif filing:
                    documents = [(r.source.title, r.source.text) for r in filing if r.source is not None]
                    try:
                        self.plan(documents)
                    except Exception as e:
                        log.warning("the plan of %d documents failed, they are filed in the folders there are: %s", len(documents), e)
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
                    if i == single and isinstance(result, IngestResult):
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
            return Source(text, title_of(item.filename), link, item.origin, dict(item.fields))

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
            if src.origin is not None and (known := self._unchanged(src.origin, src.text)) is not None:
                return Read(librarian, used, known=known)
        return Read(librarian, used, src)

    def _summarize(self, read: Read) -> Prepared | IngestResult:
        """What follows `_read`: the result of an unchanged document, or the summarized source ready to be filed."""
        if read.known is not None:
            read.known.usage = read.usage
            return read.known
        assert read.source is not None
        with self.tracker.span() as used:
            ready = read.librarian.prepare(read.source)
        used.merge(read.usage)  # extract and summarize both
        ready.usage = used
        return ready

    def _file(self, prepared: Prepared) -> IngestResult:
        """File what `_prepare` made, one writer at a time; the origin is checked again, as it may have been filed meanwhile."""
        with self.store.lock():  # the librarian reads the tree, then writes on what it read
            origin = prepared.source.origin
            if origin is not None and (known := self._unchanged(origin, prepared.source.text)) is not None:
                known.usage = prepared.usage
                return known
            return self.librarian().file(prepared)

    def origin(self, key: str) -> OriginRecord | None:
        """The latest raw copy cited by a note for an origin key (`Origin.key`), or None if no note cites one.

        A spreadsheet has none under its own key: its sheets are filed as "<key>#<sheet>". So a sync fetches it again
        whenever it is listed, and each sheet whose text is unchanged costs no model call.
        """
        return self.store.find_origin(key)

    def mark_removed(self, key: str) -> OriginRecord | None:
        """The original was deleted at its source: its raw copies are marked, its notes stay. None if unknown.

        A spreadsheet's sheets ("<key>#<sheet>") are marked with it.
        """
        with self.store.lock():
            record = self.store.mark_origin_removed(key)
            for sheet in sorted(self.store.origin_keys(key + "#")) if is_spreadsheet(key) else []:
                record = self.store.mark_origin_removed(sheet) or record
            return record

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

    def ask(self, question: str) -> Answer:
        if not question.strip():
            raise InputError("empty question")
        self._require_classifier()
        return self.researcher().ask(question)

    def _require_classifier(self) -> None:
        """In CROW mode, a classifier without its key stops here, before any model call or file."""
        if self.cfg.mode == "crow" and self.classifier is not None:
            self.classifier.require_key()

    def tree(self) -> Folder:
        return self.store.load()

    def create_folder(self, parent: str, name: str, description: str) -> Folder:
        """Create a folder by hand under `parent` ("/" or "/finance"), named and described as the librarian would.

        Both are required: the description is the line the librarian routes by. InputError for a missing one, a name
        already taken, or a parent at max_depth (the librarian would never file below it); FileNotFoundError for no parent.
        """
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
            folder, created = self.store.create_folder(at, name, description)
            if not created:
                raise InputError(f"{folder.label} already exists")
            return folder

    def plan(self, documents: Iterable[tuple[str | None, str]]) -> list[Folder]:
        """Lay out the folders the documents about to be filed need, as (title, text); the folders made.

        One LLM call reads each document's title and opening. In an empty wiki it proposes a few broad areas and the
        specific subjects several documents share; the folders are made empty, and filing then routes into them (each
        would otherwise be made, and described, from the one document that opened it). In a wiki that has folders it is
        shown them, with how many notes each holds, and proposes only the new ones: an existing folder is never
        renamed, moved or described again, and no note moves. Nothing is done for fewer than two documents. Openings
        are sampled evenly when they do not all fit in one call (`effective_read_chars`).

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
        ctx = self.librarian().ctx
        existing = [f for f in self.store.load().walk() if not f.is_root]
        if existing:
            tree = "\n".join(f"- {f.label}: {f.description or '(no description)'}; {len(f.notes)} notes" for f in existing)
            plan = ctx.ask_json(
                "librarian/plan_more", FolderPlan, tree=tree, documents="\n".join(lines), depth=depth, reply_chars=PLAN_REPLY_CHARS
            )
        else:
            plan = ctx.ask_json(
                "librarian/plan", FolderPlan, documents="\n".join(lines), top=PLAN_TOP, depth=depth, reply_chars=PLAN_REPLY_CHARS
            )
        made: list[Folder] = []
        mark = PLAN_MORE_MARK if existing else PLAN_MARK
        with self.store.lock():
            tree_now = self.store.load()
            if not existing and tree_now.subfolders:  # a document was filed meanwhile: its folders stand
                return []
            for planned in plan.folders:  # parents first; one under a folder not made, or too deep, is left out
                parts = [slugify(p, max_len=40, fallback="") for p in planned.path.strip().strip("/").split("/") if p.strip()]
                if not parts or "" in parts or len(parts) > depth or not planned.description.strip():
                    continue
                parent = tree_now.find("/".join(parts[:-1]))
                if parent is None:
                    continue
                folder, created = self.store.create_folder(parent, parts[-1], planned.description, mark)
                if created:
                    made.append(folder)
        return made

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
        if multiple <= last_split_at(path.read_text(encoding="utf-8") if path.exists() else "", folder.rel) // SPLIT_AT:
            return
        opened: list[str] = []
        try:
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
        plan = self.librarian().ctx.ask_json(
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
            for proposed in plan.folders:
                name = slugify(proposed.name, max_len=40, fallback="")
                numbers = sorted({i for i in proposed.notes if 1 <= i <= len(notes)} - taken)
                if not name or not proposed.description.strip() or len(numbers) < 2:
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

    def move_note(self, path: str, folder: str) -> Note:
        """Move a note ("finance/pricing.md") into a folder ("/sourcing" or "sourcing"; "/" is the root).

        Every link to and from it follows, and so do its archived copies. InputError for no such note or folder, a
        path that is not a note, or a note already in that folder.
        """
        with self.store.lock():
            return self.store.move_note(path, folder)

    def describe_folder(self, path: str, description: str) -> Folder:
        """Change the one-line description of a folder ("/finance"), the line the librarian routes by."""
        with self.store.lock():
            return self.store.describe_folder(path, description)

    def delete_note(self, path: str) -> Deleted:
        """Delete a note ("finance/pricing.md"); links to it go from the other notes, and raw copies only it cites."""
        with self.store.lock():
            return self.store.delete_note(path)

    def delete_folder(self, path: str) -> Deleted:
        """Delete a folder ("/finance") with everything in it, as `delete_note` does for each note."""
        with self.store.lock():
            return self.store.delete_folder(path)

    def clear(self) -> Deleted:
        """Empty the wiki: every note, folder, raw copy and archived version, and the sync cursors. Cannot be undone."""
        with self.store.lock():
            return self.store.clear()

    def find_note(self, note_id: str) -> Note | None:
        """The note of the tree with this stable `id` (archived copies are not searched), or None."""
        return next((n for n in self.store.load().all_notes() if n.id == note_id), None)

    def audit_routes(self, min_confidence: float = AUDIT_MIN, apply: bool = False) -> list[RouteProposal]:
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
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        return taxonomy_health(text, min_filed, max_share)

    def check(self) -> list[Problem]:
        return check(self.cfg.bundle)

    @property
    def usage(self) -> UsageReport:
        return self.tracker.snapshot()
