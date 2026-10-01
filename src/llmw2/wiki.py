# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki`: the one object most users need.

    wiki = Wiki.from_env()                    # OKF_* variables, see .env.example
    wiki.init()
    wiki.ingest(text, title="…", resource="https://…")
    wiki.ingest_file(Path("minutes.pdf").read_bytes(), "minutes.pdf")
    wiki.ingest_file(data, "minutes.pdf", origin=Origin("onedrive", "me", item_id, version=etag))  # unchanged → no model call
    wiki.origin(key)                          # the latest raw copy and the notes for one original
    wiki.mark_removed(key)                    # the original is gone at its source; the notes stay
    sync(wiki, LocalFolderSource(Path("~/OneDrive")))   # keep the wiki in step with a source
    wiki.ask("…").text
    wiki.delete_note("finance/pricing.md"); wiki.delete_folder("/finance"); wiki.clear()
    wiki.usage.to_dict()                      # {"llm": …, "classifier": …, "by_model": …}
"""

from __future__ import annotations

from typing import Any, Self

from llmw2.agents.librarian import IngestResult, Librarian, Source
from llmw2.agents.librarian_crow import CrowLibrarian
from llmw2.agents.prompts import Prompts
from llmw2.agents.researcher import Answer, CrowResearcher, Researcher
from llmw2.bundle.check import Problem, check
from llmw2.bundle.files import extract_text, title_of
from llmw2.bundle.origin import Origin, OriginRecord, content_hash
from llmw2.bundle.store import Deleted, WikiStore
from llmw2.bundle.tree import Folder
from llmw2.config import WikiConfig
from llmw2.errors import InputError
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageReport, UsageTracker


class Wiki:
    def __init__(
        self,
        cfg: WikiConfig,
        *,
        llm: LLM | None = None,
        classifier: Classifier | None = None,
    ) -> None:
        """Models are built from `cfg` unless given; given ones must share one UsageTracker."""
        self.cfg = cfg
        self.tracker = llm.usage if llm else classifier.usage if classifier else UsageTracker(cfg.usage_log)
        if classifier is not None and classifier.usage is not self.tracker:
            raise ValueError("the llm and the classifier must record into the same UsageTracker")
        self.store = WikiStore(cfg.bundle, actor=cfg.actor)
        self.prompts = Prompts(cfg.prompts_dir)
        self.llm = llm or LLM(cfg.llm, self.tracker)
        self.classifier = classifier
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
        """A fresh Librarian for one ingest (CROW or classic, per `mode`)."""
        if self.cfg.mode == "crow" and self.classifier is not None:
            return CrowLibrarian(self.store, self.llm, self.prompts, self.cfg, self.classifier)
        return Librarian(self.store, self.llm, self.prompts, self.cfg)

    def researcher(self) -> Researcher:
        """A fresh Researcher for one question (CROW or classic, per `mode`)."""
        if self.cfg.mode == "crow" and self.classifier is not None:
            return CrowResearcher(self.store, self.llm, self.prompts, self.cfg, self.classifier)
        return Researcher(self.store, self.llm, self.prompts, self.cfg)

    def init(self) -> None:
        self.store.init()

    def ingest(
        self, text: str, *, title: str | None = None, resource: str | None = None, origin: Origin | None = None
    ) -> IngestResult:
        """File a text. With an `origin` already ingested with the same hash or version, nothing is done.

        That case returns action "unchanged" (no model call, no file written) with the note that cites the copy.
        """
        if not text.strip():
            raise InputError("nothing to ingest: the source is empty")
        self._require_classifier()
        self.store.init()
        with self.store.lock():  # the librarian reads the tree, then writes on what it read
            if origin is not None and (known := self._unchanged(origin, text)) is not None:
                return known
            return self.librarian().ingest(Source(text, title, resource, origin))

    def ingest_file(
        self, data: bytes, filename: str, *, resource: str | None = None, origin: Origin | None = None
    ) -> IngestResult:
        """Ingest a .txt, .md or .pdf file; its name becomes the source title."""
        link = resource or (origin.link if origin else None) or filename
        return self.ingest(extract_text(data, filename), title=title_of(filename), resource=link, origin=origin)

    def origin(self, key: str) -> OriginRecord | None:
        """The latest raw copy ingested for an origin key (`Origin.key`), or None if there is none."""
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
