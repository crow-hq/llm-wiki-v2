# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The queue of proposed areas, kept in the wiki at `.llmw2/proposals.json`.

A proposal is a top-level folder a user suggests, with the notes it would hold. The wiki never makes one by itself:
proposals come from users (the page, the CLI, an MCP client), and confirming one is what opens the folder.
"""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from llmw2.bundle.store import PROPOSALS_FILE, now_iso, write_atomic

log = logging.getLogger(__name__)

OPEN, CONFIRMED, DISMISSED = "open", "confirmed", "dismissed"


@dataclass
class Proposal:
    label: str  # the root's name, as the user wrote it
    description: str  # one line: what belongs in it
    notes: list[str]  # stable note ids
    origin: str = "user"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created: str = field(default_factory=now_iso)
    status: str = OPEN

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def notes_count(n: int) -> str:
    """How many notes an area takes or moves, as a log line says it: 1 note, 2 notes."""
    return f"{n} note" if n == 1 else f"{n} notes"


def read_proposals(bundle: Path) -> list[Proposal]:
    """Every proposal of the wiki at `bundle`, oldest first; none when the file is missing or unreadable."""
    path = bundle / PROPOSALS_FILE
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))["proposals"]
        return [Proposal(**row) for row in rows]
    except FileNotFoundError:
        return []
    except (OSError, ValueError, KeyError, TypeError) as e:  # a damaged file is no queue: the wiki goes on without it
        log.warning("ignoring %s: %s", path, e)
        return []


def write_proposals(bundle: Path, proposals: list[Proposal]) -> None:
    path = bundle / PROPOSALS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps({"proposals": [p.to_dict() for p in proposals]}, indent=2) + "\n")
