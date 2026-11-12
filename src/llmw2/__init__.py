# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An LLM wiki that writes Open Knowledge Format bundles.

A Librarian files sources into a folder tree of notes, a Researcher answers
questions from it; in CROW mode a typed classifier takes the decisions and the
LLM only writes text.
"""

from __future__ import annotations

__version__ = "1.0.1"

from llmw2.agents.base import Decision
from llmw2.agents.data import Candidate, NoteDraft, Route, Source
from llmw2.agents.librarian import IngestResult
from llmw2.agents.researcher import Answer
from llmw2.agents.steps import CLASSIC, CROW, StepContext, Steps
from llmw2.audit import AUDIT_MIN, FolderHealth, RouteProposal
from llmw2.batch import BatchReport, Item
from llmw2.bundle.check import Problem
from llmw2.bundle.origin import Origin, OriginRecord
from llmw2.bundle.proposals import Proposal
from llmw2.bundle.store import Deleted
from llmw2.bundle.tree import Folder, Note
from llmw2.config import WikiConfig
from llmw2.errors import (
    ConfigError,
    InputError,
    ModelError,
    NoteChanged,
    ReplyInvalid,
    SpendingCap,
    StepError,
    WikiError,
)
from llmw2.models.cache import AnswerCache
from llmw2.models.classifier import Classifier
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageReport, UsageTracker
from llmw2.search import Embedder, Hit
from llmw2.sources import Change, ChangeBatch, LocalFolderSource, SourceConnector, SyncReport, estimate, sync
from llmw2.wiki import Purged, Wiki

__all__ = [
    "AUDIT_MIN",
    "CLASSIC",
    "CROW",
    "LLM",
    "Answer",
    "AnswerCache",
    "BatchReport",
    "Candidate",
    "Change",
    "ChangeBatch",
    "Classifier",
    "ConfigError",
    "Decision",
    "Deleted",
    "Embedder",
    "Folder",
    "FolderHealth",
    "Hit",
    "IngestResult",
    "InputError",
    "Item",
    "LocalFolderSource",
    "ModelError",
    "Note",
    "NoteChanged",
    "NoteDraft",
    "Origin",
    "OriginRecord",
    "Problem",
    "Proposal",
    "Purged",
    "ReplyInvalid",
    "Route",
    "RouteProposal",
    "Source",
    "SourceConnector",
    "SpendingCap",
    "StepContext",
    "StepError",
    "Steps",
    "SyncReport",
    "UsageReport",
    "UsageTracker",
    "Wiki",
    "WikiConfig",
    "WikiError",
    "__version__",
    "estimate",
    "sync",
]
