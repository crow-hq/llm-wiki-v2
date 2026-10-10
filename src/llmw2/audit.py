# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Two checks on how a CROW wiki is filed, both apart from the ingest flow and never automatic.

- the routing audit: where would the classifier file each note of the tree today (`RouteProposal`, no LLM call);
- the taxonomy health: per folder and month, how often notes opened a new subfolder or were routed by the LLM
  fallback, read from `log.md` alone (`FolderHealth`, no model at all).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

AUDIT_MIN = 0.7  # least route confidence for the audit to propose moving a note
HEALTH_MIN_FILED = 5  # fewest notes filed in a folder and month for the health to flag it
HEALTH_MAX_SHARE = 0.3  # largest (new subfolders + fallbacks) / filed before the health flags it
FALLBACK_MARK = ", routed by fallback"  # what the librarian's log line ends with when the LLM fallback chose the folder

_DAY = re.compile(r"^## (\d{4}-\d{2})-\d{2}$")
_ENTRY = re.compile(r"^\* \*\*(Created folder|Created|Merged|Revised)\*\*: \[.*?\]\(([^)\s]+)\)(.*)$")


@dataclass
class RouteProposal:
    """A note the classifier would file in another folder than the one it is in."""

    path: str  # the note's bundle path when the audit read it
    id: str | None
    title: str
    current: str  # folder label, "/finance"
    proposed: str  # folder label
    confidence: float  # the route's confidence
    applied: bool = False  # the note was moved

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FolderHealth:
    """What was filed in a folder in a calendar month, by the log."""

    folder: str  # label, "/" or "/finance"
    month: str  # "2026-10"
    filed: int  # Created, Merged and Revised lines of notes in the folder
    new_subfolders: int  # "Created folder" lines under the folder
    fallbacks: int  # of the filed lines, those routed by the LLM fallback
    share: float  # (new_subfolders + fallbacks) / filed; 0.0 when nothing was filed
    flag: bool  # filed >= min_filed and share > max_share

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def taxonomy_health(log: str, min_filed: int = HEALTH_MIN_FILED, max_share: float = HEALTH_MAX_SHARE) -> list[FolderHealth]:
    """The health rows of a `log.md` text, newest month first, then by folder.

    A line counts for the folder of the note it links (a "Created folder" line, for the parent of the folder it links)
    and for the month of the `## YYYY-MM-DD` heading above it. Lines of other verbs, including the demo's lowercase
    "created", are ignored.
    """
    counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0])  # filed, new_subfolders, fallbacks
    month = ""
    for line in log.splitlines():
        if day := _DAY.match(line):
            month = day[1]
            continue
        if not month or not (m := _ENTRY.match(line)):
            continue
        verb, link, rest = m.groups()
        parent = link.rpartition("/")[0]  # the folder of a note; for "a/b/index.md" the folder a/b, whose parent is a
        if verb == "Created folder":
            parent = parent.rpartition("/")[0]
            counts[f"/{parent}", month][1] += 1
        else:
            row = counts[f"/{parent}", month]
            row[0] += 1
            row[2] += FALLBACK_MARK in rest
    rows = []
    for (folder, mon), (filed, new, fallbacks) in counts.items():
        share = (new + fallbacks) / filed if filed else 0.0
        rows.append(FolderHealth(folder, mon, filed, new, fallbacks, share, filed >= min_filed and share > max_share))
    return sorted(sorted(rows, key=lambda r: r.folder), key=lambda r: r.month, reverse=True)
