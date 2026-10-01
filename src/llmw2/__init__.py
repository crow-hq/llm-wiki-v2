# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An LLM wiki that writes Open Knowledge Format bundles.

A Librarian files sources into a folder tree of notes, a Researcher answers
questions from it; in CROW mode a typed classifier takes the decisions and the
LLM only writes text.
"""

from __future__ import annotations

__version__ = "0.3.0"

from llmw2.agents.data import Candidate, NoteDraft, Route, Source
from llmw2.agents.steps import CLASSIC, CROW, StepContext, Steps
from llmw2.bundle.origin import Origin
from llmw2.config import WikiConfig
from llmw2.errors import ConfigError, InputError, ModelError, StepError, WikiError
from llmw2.sources import Change, ChangeBatch, LocalFolderSource, SourceConnector, SyncReport, sync
from llmw2.wiki import Wiki

__all__ = [
    "CLASSIC",
    "CROW",
    "Candidate",
    "Change",
    "ChangeBatch",
    "ConfigError",
    "InputError",
    "LocalFolderSource",
    "ModelError",
    "NoteDraft",
    "Origin",
    "Route",
    "Source",
    "SourceConnector",
    "StepContext",
    "StepError",
    "Steps",
    "SyncReport",
    "Wiki",
    "WikiConfig",
    "WikiError",
    "__version__",
    "sync",
]
