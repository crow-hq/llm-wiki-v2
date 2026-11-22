# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What llmw2 raises on purpose: catch `WikiError` for all of it, or one branch.

The input and config errors are also `ValueError`s, and `ModelError` a `RuntimeError`,
so code written against the builtins keeps catching them.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)


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


class QueueFull(WikiError):
    """`remember` found the queue of entries not yet filed full, and no room came before the caller's `timeout` (or the stall limit):
    NOT saved, nothing was written, and the caller tries again later. It says where the queue is: `queue` (entries waiting to be filed),
    `queue_limit`, `oldest_s` (how long the oldest has waited, or None) and `retry_after_s` (about when to try again: the queue over the
    rate this process saw entries filed at, or, before it saw one, the rate the last filings in the journal took; None with neither).
    `reason`: "full" (the queue, in entries or bytes), "share" (the last places, kept for writers that keep few), "busy" (the queue had
    room, but other writers, or a seal of the journal, held its admission for the whole wait) or "stall" (nobody files)."""

    def __init__(
        self,
        message: str,
        *,
        queue: int | None = None,
        queue_limit: int | None = None,
        oldest_s: float | None = None,
        retry_after_s: float | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.queue = queue
        self.queue_limit = queue_limit
        self.oldest_s = oldest_s
        self.retry_after_s = retry_after_s
        self.reason = reason


class BundleFormatError(WikiError):
    """The wiki's folder says (`.llmw2/format`) it was written in a format newer than the one this version of llmw2 knows, or says it
    in words this version cannot read: the wiki is not opened, and nothing is changed. Install the version that wrote it, or a newer one."""


class DiskFull(WikiError, OSError):
    """The disk of the bundle has less room than the reserve that filing needs, or a write failed for lack of room: nothing was
    acknowledged. (An `OSError` too, so that code that handles a full disk handles this.)"""


class FilerError(WikiError):
    """The filer failed on an entry (the first error of an attempt), gave an entry up, or there is no filer to file what is queued."""


class FailedBacklog(QueueFull):
    """`remember` found that entries given up (X) fill the queue: they are not filed and nobody resolves them, so no room will come by
    waiting. Nothing was written. A `QueueFull` too, so that code that handles a full queue handles this."""
