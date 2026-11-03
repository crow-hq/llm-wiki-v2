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


class NoteChanged(InputError):
    """A note changed on disk after the store read it, so the store did not overwrite it. A document that needed that note
    is not filed (nothing is written for it) and the next run files it again."""


class ConfigError(WikiError, ValueError):
    """The configuration itself is broken: an unreadable settings file, a malformed environment variable."""


class ModelError(WikiError, RuntimeError):
    """A model endpoint failed or answered something unusable."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status  # the provider's HTTP status, when it answered with an error


class SpendingCap(WikiError):
    """The spending cap of a run was reached (`sync(max_cost=...)`): no model is called any more, and the run stops.

    It is not a `ModelError` on purpose: the steps that tolerate a failing model (a fallback, a note without links) must
    not take the cap for one, or a document would be filed with a worse decision and never redone.
    """


class ReplyInvalid(ModelError):
    """The model answered, but nothing usable for this request even after a retry (invalid JSON, no content).

    It belongs to the document asked about, not to the endpoint: a batch files the other documents (see
    `Wiki.ingest_many`), while any other ModelError means the endpoint itself is failing.
    """


class ReplyCut(ReplyInvalid):
    """The model's reply hit the length limit (finish_reason length), so it is unfinished; `text` is what it wrote."""

    def __init__(self, message: str, *, text: str = "", status: int | None = None) -> None:
        super().__init__(message, status=status)
        self.text = text


class StepError(WikiError):
    """A step returned something the next one cannot take (or lacks what it needs); nothing was written."""

    def __init__(self, step: str, message: str) -> None:
        super().__init__(f"{step}: {message}")
        self.step = step
