# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What `Wiki.ingest_many` takes and gives back: the documents of a batch and what became of each."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from llmw2.agents.librarian import IngestResult
from llmw2.bundle.origin import Origin
from llmw2.errors import InputError


@dataclass(frozen=True)
class Item:
    """One document of a batch. Its content is in exactly one of `text`, `data` or `fetch`.

    `data` and `fetch` (a function returning the bytes, called on a worker thread) go through the `extract` step
    like `Wiki.ingest_file`, with `filename` deciding the type; `text` is filed as it is. The title is the file name's.
    """

    filename: str
    text: str | None = None
    data: bytes | None = None
    fetch: Callable[[], bytes] | None = None
    origin: Origin | None = None
    resource: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if sum(x is not None for x in (self.text, self.data, self.fetch)) != 1:
            raise InputError(f"{self.filename}: give exactly one of text, data or fetch")


@dataclass
class BatchReport:
    results: list[tuple[Item, IngestResult | Exception]] = field(default_factory=list)  # in filing order; an InputError is a result
    not_done: list[Item] = field(default_factory=list)  # never filed: the batch stopped before them
    stopped: str | None = None  # why it stopped, None when it ran to the end

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [
                {"filename": item.filename, **({"error": str(r)} if isinstance(r, Exception) else {"result": r.to_dict()})}
                for item, r in self.results
            ],
            "not_done": [item.filename for item in self.not_done],
            "stopped": self.stopped,
        }
