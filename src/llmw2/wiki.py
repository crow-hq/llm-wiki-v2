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
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from pathlib import PurePath
from typing import Any, Self

from llmw2.agents import steps_crow
from llmw2.agents.data import Candidate, Source
from llmw2.agents.librarian import IngestResult, Librarian, Prepared
from llmw2.agents.prompts import Prompts
from llmw2.agents.researcher import Answer, Researcher
from llmw2.agents.steps import CLASSIC, CROW, StepContext, Steps, check_extract
from llmw2.audit import AUDIT_MIN, HEALTH_MAX_SHARE, HEALTH_MIN_FILED, FolderHealth, RouteProposal, taxonomy_health
from llmw2.batch import BatchReport, Item
from llmw2.bundle.check import Problem, check
from llmw2.bundle.files import is_spreadsheet, sheet_sources, title_of
from llmw2.bundle.origin import Origin, OriginRecord, content_hash
from llmw2.bundle.store import Deleted, WikiStore
from llmw2.bundle.tree import LOG, Folder, Note, slugify
from llmw2.config import WikiConfig
from llmw2.errors import ConfigError, InputError, ReplyInvalid
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageReport, UsageTracker


def batch_order(item: Item) -> tuple[bool, str, str]:
    """Oldest original first, those with no date last, then by name: the same batch is always filed the same way."""
    modified = item.origin.modified if item.origin else None
    return modified is None, modified or "", item.filename


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

        Items go in order of their origin's `modified` (those without one last), then filename. Preparing one means
        fetching and extracting it, the "unchanged" check and `summarize`, on a worker thread; filing, which reads and
        writes the tree, is one document at a time in that order, under the lock, so the wiki is what `ingest_file`
        called in turn would make of it. At most `concurrency` documents are being prepared or wait to be filed.

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
        halt = [len(ordered)]  # the first item that failed for a reason that is not its own: none after it starts
        halting = threading.Lock()

        def prepare(i: int) -> Prepared | IngestResult | None:
            if i > halt[0]:  # items before the failed one still run: they are filed
                return None
            try:
                return self._prepare(self._source_of(ordered[i]))
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

        with concurrent.futures.ThreadPoolExecutor(workers, thread_name_prefix="llmw2-prepare") as pool:
            try:
                for i, item in enumerate(ordered):
                    while len(futures) < min(len(ordered), halt[0] + 1) and len(futures) - i < workers:
                        # a context of its own per item: usage spans are context variables
                        futures.append(pool.submit(contextvars.copy_context().run, prepare, len(futures)))
                    try:
                        result = settle(i)
                        if isinstance(result, ReplyInvalid) and report.results and isinstance(report.results[-1][1], ReplyInvalid):
                            raise result  # two documents in a row: the model, not them; stop before paying for the rest
                    except Exception as e:  # a failure that is not the item's: nothing from here on is filed
                        report.not_done, report.stopped = ordered[i:], f"{item.filename}: {e}"
                        break
                    report.results.append((item, result))
                    if on_result is not None:
                        on_result(item, result)
            finally:
                halt[0] = -1  # whatever ends the loop, items still queued start nothing
        if report.stopped is None:
            done = {id(i): r for i, r in report.results}
            for item, many in groups:
                if len(many) > 1 or many[0] is not item:
                    self._after_sheets(item, many, [done.get(id(one)) for one in many])
        return report

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
        return self._file(ready)

    def _prepare(self, source: Callable[[StepContext], Source]) -> Prepared | IngestResult:
        """Everything an ingest does before it reads the tree: no lock, and safe to run on several threads at once.

        Extracts the source, checks "unchanged" and summarizes. Gives the result right away when the origin is already
        filed with this content or version.
        """
        librarian = self.librarian()
        with self.tracker.span() as used:
            src = source(librarian.ctx)
            if not src.text.strip():
                raise InputError("nothing to ingest: the source is empty")
            self._require_classifier()
            self.store.init()
            if src.origin is not None and (known := self._unchanged(src.origin, src.text)) is not None:
                ready: Prepared | IngestResult = known
            else:
                ready = librarian.prepare(src)
        ready.usage = used  # extract and summarize both
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
        """Empty the wiki: every note, folder and raw copy. Cannot be undone."""
        with self.store.lock():
            return self.store.clear()

    def find_note(self, note_id: str) -> Note | None:
        """The note of the tree with this stable `id` (archived copies are not searched), or None."""
        return next((n for n in self.store.load().all_notes() if n.id == note_id), None)

    def audit_routes(self, min_confidence: float = AUDIT_MIN, apply: bool = False) -> list[RouteProposal]:
        """The notes the CROW classifier would file in another folder today, with the confidence of the route.

        Each note of the tree is routed as `route` routes a candidate (the same per-level choices and beam), but never by
        the LLM: a note whose route would fall back, or end in "New subfolder" or "None of these", gets no proposal, and
        no LLM call is made. A proposal needs a folder other than the note's own, not below it (moving a note deeper
        into its own branch was most of the wrong proposals measured), and `confidence >= min_confidence`.
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
