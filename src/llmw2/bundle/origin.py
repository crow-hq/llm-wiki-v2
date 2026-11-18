# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Where a document comes from: the link between a raw copy, its notes and the original.

An `Origin` names one item at one source (a file in a folder, a OneDrive item, a mail).
Two ingests with the same `key` are two versions of the same original.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from llmw2.errors import InputError

REQUIRED = ("source", "account", "id")


def content_hash(text: str) -> str:
    """The hash recorded for a raw copy: "sha256:" and the hex digest of its UTF-8 text."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def format_at(moment: datetime) -> str:
    """The time of an event (`event_at` of a raw copy, of an entry of the journal), the one way it is written: UTC, `+00:00`, always
    with microseconds, so that the order of the strings is the order of the times. A time with no zone is refused (`InputError`):
    what it means depends on who reads it."""
    if not isinstance(moment, datetime):
        raise InputError("`at` is a datetime with a time zone")
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise InputError("`at` needs a time zone (a datetime that says its offset from UTC)")
    try:
        return moment.astimezone(UTC).isoformat(timespec="microseconds")
    except (OverflowError, ValueError) as e:
        raise InputError(f"`at` is out of range: {e}") from e


@dataclass(frozen=True)
class Origin:
    source: str  # connector name: "local", "onedrive", "outlook"
    account: str  # account or space the item belongs to ("" if the source has none)
    id: str  # stable id at the source: an item id, a message id, a relative path
    link: str | None = None  # URL or path that opens the original
    version: str | None = None  # the source's own version (eTag, mtime): equal means unchanged
    modified: str | None = None  # ISO timestamp of the original

    @property
    def key(self) -> str:
        return f"{self.source}:{self.account}:{self.id}"

    def to_dict(self) -> dict[str, str]:
        """The set fields as plain strings, ready for YAML or JSON."""
        fields = {
            "source": self.source,
            "account": self.account,
            "id": self.id,
            "link": self.link,
            "version": self.version,
            "modified": self.modified,
        }
        return {k: v for k, v in fields.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Origin:
        """Rebuild from `to_dict`; InputError when source, account or id is missing."""
        if missing := [k for k in REQUIRED if not isinstance(data.get(k), str)]:
            raise InputError(f"origin is missing: {', '.join(missing)}")
        optional = {k: str(data[k]) for k in ("link", "version", "modified") if data.get(k) is not None}
        return cls(data["source"], data["account"], data["id"], **optional)


@dataclass
class OriginRecord:
    """What the wiki holds for one origin key: its latest raw copy cited by a note and the notes that cite any copy."""

    raw: str  # bundle path of the latest raw copy cited by a note ("/raw/…")
    hash: str | None  # of that copy; None for copies saved before hashes were kept
    version: str | None
    removed: bool  # that copy is marked as deleted at the source
    notes: list[str] = field(default_factory=list)  # bundle paths; those citing the latest copy come last
