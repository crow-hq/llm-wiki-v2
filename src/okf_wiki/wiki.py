# Copyright 2026 Federico Cesarini, Marco Sassarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""`Wiki`: the one object most users need.

    wiki = Wiki.from_env()                    # OKF_* variables, see .env.example
    wiki.init()
    wiki.ingest(text, title="…", resource="https://…")
    wiki.ingest_file(Path("minutes.pdf").read_bytes(), "minutes.pdf")
    wiki.ask("…").text
    wiki.usage.to_dict()                      # {"llm": …, "classifier": …, "by_model": …}
"""

from __future__ import annotations

from typing import Any

from okf_wiki.check import Problem, check
from okf_wiki.classifier import Classifier
from okf_wiki.config import WikiConfig
from okf_wiki.files import extract_text, title_of
from okf_wiki.librarian import CrowLibrarian, IngestResult, Librarian, Source
from okf_wiki.llm import LLM
from okf_wiki.prompts import Prompts
from okf_wiki.researcher import Answer, CrowResearcher, Researcher
from okf_wiki.store import Folder, WikiStore
from okf_wiki.usage import UsageReport, UsageTracker


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

    def ingest(self, text: str, *, title: str | None = None, resource: str | None = None) -> IngestResult:
        if not text.strip():
            raise ValueError("nothing to ingest: the source is empty")
        self.store.init()
        return self.librarian().ingest(Source(text, title, resource))

    def ingest_file(self, data: bytes, filename: str, *, resource: str | None = None) -> IngestResult:
        """Ingest a .txt, .md or .pdf file; its name becomes the source title."""
        return self.ingest(extract_text(data, filename), title=title_of(filename), resource=resource or filename)

    def ask(self, question: str) -> Answer:
        if not question.strip():
            raise ValueError("empty question")
        return self.researcher().ask(question)

    def tree(self) -> Folder:
        return self.store.load()

    def check(self) -> list[Problem]:
        return check(self.cfg.bundle)

    @property
    def usage(self) -> UsageReport:
        return self.tracker.snapshot()
