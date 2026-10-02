# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`sync`: apply a connector's changes to a wiki, resuming from the cursor left by the last run."""

from __future__ import annotations

import functools
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llmw2.batch import Item
from llmw2.bundle.store import write_atomic
from llmw2.bundle.tree import slugify
from llmw2.sources.base import Change, SourceConnector

if TYPE_CHECKING:
    from llmw2.wiki import Wiki


@dataclass
class SyncReport:
    created: int = 0
    merged: int = 0
    unchanged: int = 0  # fetched, but its content was already filed
    removed: int = 0
    skipped: int = 0  # not fetched (same version), or a removal of something never filed
    errors: list[tuple[str, str]] = field(default_factory=list)  # (origin key, message) of items that could not be filed
    stopped: str | None = None  # why the sync ended before the last page (a model failing); None when it ran to the end

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sync(wiki: Wiki, connector: SourceConnector, *, concurrency: int | None = None) -> SyncReport:
    """Bring the wiki up to date with `connector`, one page of changes at a time.

    An item that cannot be read (unsupported type, scanned PDF) goes into `errors` and the sync goes on. A page is
    filed with `Wiki.ingest_many`, so `concurrency` (default `cfg.concurrency`) documents are prepared at once.
    A model failure stops the sync: the report comes back with `stopped` set, and the cursor stays at the last page
    that was finished, so the next run resumes there and does not pay again for what was filed. A missing key
    raises ConfigError.
    """
    wiki.init()
    state = _state_path(wiki, connector)
    cursor = _load_cursor(state)
    report = SyncReport()
    while True:
        batch = connector.changes(cursor)
        _apply(wiki, connector, batch.changes, report, concurrency)
        if report.stopped:
            return report
        cursor = batch.cursor
        state.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(state, json.dumps({"cursor": cursor}))
        if not batch.more:
            return report


def _apply(wiki: Wiki, connector: SourceConnector, changes: list[Change], report: SyncReport, concurrency: int | None) -> None:
    """One page: the last change of each origin, removals first, then what is new or changed as one batch."""
    last = {change.origin.key: change for change in changes}
    items = []
    for key, change in last.items():
        if change.kind == "remove":
            if wiki.mark_removed(key) is None:
                report.skipped += 1
            else:
                report.removed += 1
            continue
        known = wiki.origin(key)
        if known is not None and not known.removed and change.origin.version and known.version == change.origin.version:
            report.skipped += 1
            continue
        items.append(Item(change.filename, fetch=functools.partial(connector.fetch, change), origin=change.origin))
    done = wiki.ingest_many(items, concurrency=concurrency)
    for item, result in done.results:
        if isinstance(result, Exception):
            report.errors.append((item.origin.key if item.origin else item.filename, str(result)))
        else:
            setattr(report, result.action, getattr(report, result.action) + 1)
    report.stopped = done.stopped


def _state_path(wiki: Wiki, connector: SourceConnector) -> Path:
    """Where the cursor lives: a hidden folder in the bundle, one file per connector and account.

    The name is readable, and a hash of the pair keeps two accounts with a long common prefix apart.
    """
    digest = hashlib.sha256(f"{connector.name}\0{connector.account}".encode()).hexdigest()[:12]
    readable = (slugify(connector.name, fallback=""), slugify(connector.account, max_len=30, fallback=""), digest)
    return wiki.cfg.bundle / ".llmw2" / "sync" / f"{'-'.join(p for p in readable if p)}.json"


def _load_cursor(path: Path) -> str | None:
    try:
        return str(json.loads(path.read_text(encoding="utf-8"))["cursor"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
