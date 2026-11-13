# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What `Wiki.ingest_many` takes and gives back: the documents of a batch and what became of each."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from llmw2.agents.librarian import IngestResult
from llmw2.bundle.origin import Origin
from llmw2.errors import InputError, NoteChanged


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


def deviations(results: Iterable[IngestResult]) -> list[dict[str, Any]]:
    """The notes the limit on top-level folders deviated, among `results` (and the sheets they hold): for each, the note's
    path, the root it was filed in and the probabilities that chose it (None when the words chose, not the classifier)."""
    found: list[dict[str, Any]] = []
    for result in results:
        if result.overflow is not None:
            root = "/" + result.folder.strip("/").split("/")[0]
            odds: dict[str, Any] = {"p_none": result.overflow.get("p_none"), "p_root": result.overflow.get("p_root")}
            if "declared" in result.overflow:  # sent to the folder `overflow_folder` names
                odds["declared"] = True
            found.append({"note": result.note, "root": root, **odds})
        found += deviations(result.parts)
    return found


def deviation_line(d: dict[str, Any]) -> str:
    """One deviation as a line of text: `path -> /root (p_none 0.4, p_root 0.3)`."""
    odds = f"p_none {d['p_none']}, p_root {d['p_root']}" if d.get("p_root") is not None else "chosen by words"
    if d.get("declared"):
        odds = "the declared overflow folder"
    return f"{d['note']} -> {d['root']} ({odds})"


@dataclass
class BatchReport:
    results: list[tuple[Item, IngestResult | Exception]] = field(default_factory=list)  # in filing order; an InputError is a result
    not_done: list[Item] = field(default_factory=list)  # never filed: the batch stopped before them
    stopped: str | None = None  # why it stopped, None when it ran to the end

    @property
    def conflicts(self) -> int:
        """The documents not filed because a note they needed changed on disk meanwhile (`NoteChanged`): the next run files them."""
        return sum(isinstance(r, NoteChanged) for _, r in self.results)

    @property
    def deviations(self) -> list[dict[str, Any]]:
        """The notes the limit on top-level folders sent to the likeliest root (see `deviations`)."""
        return deviations(r for _, r in self.results if isinstance(r, IngestResult))

    def to_dict(self) -> dict[str, Any]:
        out = {
            "results": [
                {"filename": item.filename, **({"error": str(r)} if isinstance(r, Exception) else {"result": r.to_dict()})}
                for item, r in self.results
            ],
            "not_done": [item.filename for item in self.not_done],
            "stopped": self.stopped,
        }
        if self.conflicts:
            out["conflicts"] = self.conflicts
        if found := self.deviations:  # only when there are any
            out |= {"deviated": len(found), "deviations": found}
        return out
