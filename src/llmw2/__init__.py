# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An LLM wiki that writes Open Knowledge Format bundles.

A Librarian files sources into a folder tree of notes, a Researcher answers
questions from it; in CROW mode a typed classifier takes the decisions and the
LLM only writes text.
"""

from __future__ import annotations

__version__ = "0.2.0"

from llmw2.config import WikiConfig
from llmw2.errors import ConfigError, InputError, ModelError, WikiError
from llmw2.wiki import Wiki

__all__ = ["ConfigError", "InputError", "ModelError", "Wiki", "WikiConfig", "WikiError", "__version__"]
