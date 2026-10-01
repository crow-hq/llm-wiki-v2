# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What a source must offer to be synced: a list of changes since a cursor, and the bytes of an item."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from llmw2.bundle.origin import Origin


@dataclass(frozen=True)
class Change:
    kind: Literal["upsert", "remove"]
    origin: Origin  # with `version` set when the source knows it, so unchanged items are not fetched
    filename: str  # decides the file type and the title: "minutes.pdf", "notes.md"


@dataclass(frozen=True)
class ChangeBatch:
    changes: list[Change] = field(default_factory=list)
    cursor: str = ""  # opaque to the caller: handed back to `changes` next time
    more: bool = False  # True: ask again at once with `cursor`


class SourceConnector(Protocol):
    """A place documents come from. `name` and `account` identify it in origin keys and in the cursor file."""

    name: str
    account: str

    def changes(self, cursor: str | None) -> ChangeBatch:
        """What changed since `cursor`; None asks for a full listing."""
        ...

    def fetch(self, change: Change) -> bytes:
        """The content of an upserted item."""
        ...
