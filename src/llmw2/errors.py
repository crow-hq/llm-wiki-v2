# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What llmw2 raises on purpose: catch `WikiError` for all of it, or one branch.

The input and config errors are also `ValueError`s, and `ModelError` a `RuntimeError`,
so code written against the builtins keeps catching them.
"""

from __future__ import annotations


class WikiError(Exception):
    """Base of every error llmw2 raises on purpose."""


class InputError(WikiError, ValueError):
    """The caller's input cannot be used: an empty source or question, a file type we cannot read, invalid settings."""


class ConfigError(WikiError, ValueError):
    """The configuration itself is broken: an unreadable settings file, a malformed environment variable."""


class ModelError(WikiError, RuntimeError):
    """A model endpoint failed or answered something unusable."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status  # the provider's HTTP status, when it answered with an error
