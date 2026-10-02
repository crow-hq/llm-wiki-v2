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
    wiki.delete_note("finance/pricing.md"); wiki.delete_folder("/finance"); wiki.clear()
    wiki.usage.to_dict()                      # {"llm": …, "classifier": …, "by_model": …}
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import threading
from collections.abc import Callable, Iterable
from typing import Any, Self

from llmw2.agents.data import Source
from llmw2.agents.librarian import IngestResult, Librarian, Prepared
from llmw2.agents.prompts import Prompts
from llmw2.agents.researcher import Answer, Researcher
from llmw2.agents.steps import CLASSIC, CROW, StepContext, Steps, check_extract
from llmw2.batch import BatchReport, Item
from llmw2.bundle.check import Problem, check
from llmw2.bundle.files import title_of
from llmw2.bundle.origin import Origin, OriginRecord, content_hash
from llmw2.bundle.store import Deleted, WikiStore
from llmw2.bundle.tree import Folder, slugify
from llmw2.config import WikiConfig
from llmw2.errors import InputError
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
        """Ingest a file (.txt, .md or .pdf with the default `extract` step); its name becomes the source title."""
        item = Item(filename, data=data, resource=resource, origin=origin, fields=dict(fields or {}))
        return self._ingest(self._source_of(item))

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

        An item that cannot be read (InputError) is a result and the batch goes on. Any other failure to prepare one
        (a model that keeps failing, a step that returns what it should not) starts no new item: those in flight finish,
        the documents before the failed one are filed, and everything from it on is in `not_done` with `stopped` saying
        why. A failure while filing stops at once, the same way. `on_result` is called after each item is filed or fails.
        """
        workers = self.cfg.concurrency if concurrency is None else concurrency
        if not 1 <= workers <= 16:
            raise InputError("concurrency must be from 1 to 16")
        ordered = sorted(items, key=batch_order)
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
            except InputError:
                raise
            except Exception:
                with halting:
                    halt[0] = min(halt[0], i)
                raise

        futures: list[concurrent.futures.Future[Prepared | IngestResult | None]] = []

        def settle(i: int) -> IngestResult | InputError:
            """Wait for item `i` to be prepared, then file it; its own InputError is its result."""
            try:
                ready = futures[i].result()
            except InputError as e:
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
                    except Exception as e:  # a failure that is not the item's: nothing from here on is filed
                        report.not_done, report.stopped = ordered[i:], f"{item.filename}: {e}"
                        break
                    report.results.append((item, result))
                    if on_result is not None:
                        on_result(item, result)
            finally:
                halt[0] = -1  # whatever ends the loop, items still queued start nothing
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
        """The latest raw copy cited by a note for an origin key (`Origin.key`), or None if no note cites one."""
        return self.store.find_origin(key)

    def mark_removed(self, key: str) -> OriginRecord | None:
        """The original was deleted at its source: its raw copies are marked, its notes stay. None if unknown."""
        with self.store.lock():
            return self.store.mark_origin_removed(key)

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

    def check(self) -> list[Problem]:
        return check(self.cfg.bundle)

    @property
    def usage(self) -> UsageReport:
        return self.tracker.snapshot()
