# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`sync`: apply a connector's changes to a wiki, resuming from the cursor left by the last run."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import functools
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llmw2.batch import BatchReport, Item
from llmw2.bundle.store import SYNC_DIR, authored, current_author, write_atomic
from llmw2.bundle.tree import slugify
from llmw2.errors import InputError, NoteChanged, ReplyInvalid
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
    # the notes the limit on top-level folders sent to the likeliest root: note, root, p_none, p_root (see `batch.deviations`)
    deviations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        if self.deviations:  # only when there are any: a report without deviations reads as it always did
            out["deviated"] = len(self.deviations)
        else:
            del out["deviations"]
        return out

    def add(self, done: BatchReport) -> None:
        """Count a filed batch: what became of each document, the deviations, and why it stopped."""
        retry = 0  # documents whose model replies were unusable or that met a changed note: likely to pass on another run
        for item, result in done.results:
            if isinstance(result, Exception):
                self.errors.append((item.origin.key if item.origin else item.filename, str(result)))
                retry += isinstance(result, ReplyInvalid | NoteChanged)
            else:
                setattr(self, result.action, getattr(self, result.action) + 1)
        self.deviations += done.deviations
        self.stopped = done.stopped
        if retry and self.stopped is None:  # the page is done, but its cursor is not saved: the next run retries them
            self.stopped = f"unusable replies or notes changed meanwhile for {retry} document(s), listed in errors: run again to retry them"


def documents(connector: SourceConnector) -> list[Item]:
    """Every document the connector lists now, as items for `Wiki.rebuild`: the same origins `sync` files them under."""
    return [
        Item(c.filename, fetch=functools.partial(connector.fetch, c), origin=c.origin, fields=dict(c.fields))
        for c in _latest(connector, None).values()
        if c.kind == "upsert"
    ]


def _latest(connector: SourceConnector, cursor: str | None) -> dict[str, Change]:
    """The last change of each origin from `cursor` to the end, pages included; nothing is fetched and no cursor is saved."""
    last: dict[str, Change] = {}
    while True:
        batch = connector.changes(cursor)
        for change in batch.changes:
            last[change.origin.key] = change
        cursor = batch.cursor
        if not batch.more:
            return last


def sync(
    wiki: Wiki,
    connector: SourceConnector,
    *,
    concurrency: int | None = None,
    max_cost: float | None = None,
    purge_removed: bool = False,
    author: str | None = None,
) -> SyncReport:
    """Bring the wiki up to date with `connector`, one page of changes at a time.

    An item that cannot be read (unsupported type, scanned PDF) goes into `errors` and the sync goes on. A page is
    filed with `Wiki.ingest_many`, so `concurrency` (default `cfg.concurrency`) documents are prepared at once.
    A model failure stops the sync: the report comes back with `stopped` set, and the cursor stays at the last page
    that was finished, so the next run resumes there and does not pay again for what was filed. A document whose
    model replies stay unusable (ReplyInvalid), or that needs a note which changed on disk meanwhile (NoteChanged, counted
    as `conflicts` by the batch), is in `errors` and the rest of its page is filed; the sync then stops
    at the end of that page, so the next run retries only that document. A document that always fails thus keeps a
    connector with several pages from going past its page. A missing key raises ConfigError.

    `max_cost` (USD, above 0) caps what this sync spends, in the cost the provider reports: before each model request the
    spending is checked, and once it reaches the cap the sync stops like on any model failure (`stopped` says "spending
    cap", the cursor stays at the last page finished), so a later run goes on without paying again for what was filed.
    The cap is passed by at most the calls already on their way. A provider that reports no cost (Ollama) never reaches it.
    The documents filed and the cost of each sync are kept (`estimate` uses them).

    A document removed at its source is marked as such and its notes stay (`Wiki.mark_removed`); with `purge_removed` it is
    purged instead (`Wiki.purge`): its raw copies, archived versions and notes are deleted, and the other documents those
    notes held are filed again. A purge that cannot file them again is in `errors`, and stops the sync like a failing model.

    `author` (default: the wiki's) is who the sync is made for: the log lines it writes end with ` · by <author>`.
    """
    if max_cost is not None and not max_cost > 0:
        raise InputError(f"max_cost must be above 0, got {max_cost}")
    with authored(author or current_author() or wiki.author):
        return _sync(wiki, connector, concurrency, max_cost, purge_removed)


def _sync(wiki: Wiki, connector: SourceConnector, concurrency: int | None, max_cost: float | None, purge_removed: bool) -> SyncReport:
    wiki.init()
    state = _state_path(wiki, connector)
    cursor = _load_cursor(state)
    report = SyncReport()
    with wiki.tracker.span() as used:  # the sync's own calls and its lanes, not other work on the same tracker
        try:
            with wiki.tracker.limit(max_cost), wiki.store.session():  # one session of the store: the tree is read whole once
                while True:
                    batch = connector.changes(cursor)
                    _apply(wiki, connector, batch.changes, report, concurrency, purge_removed)
                    if report.stopped:
                        if max_cost is not None and not report.created + report.merged and "spending cap" in report.stopped:
                            # a page is read and planned whole before its first document is filed: a cap under that cost files
                            # nothing, and each run would pay it again
                            page, cost = len(batch.changes), used.llm.cost_usd + used.classifier.cost_usd
                            report.stopped += (
                                f"; nothing was filed: the cap is under what reading and planning a page of {page} changes costs"
                                f" (${cost:.4f} so far)"
                            )
                        return report
                    cursor = batch.cursor
                    state.parent.mkdir(parents=True, exist_ok=True)
                    write_atomic(state, json.dumps({"cursor": cursor}))
                    if not batch.more:
                        return report
        finally:
            _remember(wiki, report.created + report.merged, used.llm.cost_usd + used.classifier.cost_usd)


def estimate(wiki: Wiki, connector: SourceConnector) -> dict[str, Any]:
    """What a `sync` would file now, and about what it would cost, without fetching or filing anything.

    `documents` is how many the connector lists from the saved cursor that the wiki does not already hold at that version.
    `est_cost` is that many times the average cost per document of the syncs kept in the wiki's history (USD, the cost the
    providers reported), or None when there is none yet (`basis` is then "no history"): set `max_cost` on the first run.
    """
    n = sum(
        1
        for change in _latest(connector, _load_cursor(_state_path(wiki, connector))).values()
        if change.kind == "upsert" and not _same_version(wiki, change)
    )
    past = [e for e in _history(wiki) if e["documents"] > 0]
    if not past:
        return {"documents": n, "est_cost": None, "basis": "no history"}
    filed = sum(e["documents"] for e in past)
    mean = sum(e["cost_usd"] for e in past) / filed
    return {"documents": n, "est_cost": n * mean, "basis": f"{len(past)} past sync(s): {filed} documents, ${mean:.4f} each"}


def _same_version(wiki: Wiki, change: Change) -> bool:
    """Whether the wiki already holds this change's version of its origin."""
    known = wiki.origin(change.origin.key)
    return known is not None and not known.removed and bool(change.origin.version) and known.version == change.origin.version


def _history_path(wiki: Wiki) -> Path:
    """One file for the wiki, next to the cursors: a line per sync, `{"documents": n, "cost_usd": c}`."""
    return wiki.cfg.bundle / SYNC_DIR / "history.jsonl"


def _history(wiki: Wiki) -> list[dict[str, Any]]:
    try:
        lines = _history_path(wiki).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
            out.append({"documents": int(entry["documents"]), "cost_usd": float(entry["cost_usd"])})
        except (ValueError, KeyError, TypeError):
            continue
    return out


def _remember(wiki: Wiki, documents_filed: int, cost: float) -> None:
    """Add a sync to the history: the documents it filed and what it spent. A sync that did neither adds nothing."""
    if documents_filed or cost:
        path = _history_path(wiki)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"documents": documents_filed, "cost_usd": round(cost, 6)}) + "\n")


def _apply(
    wiki: Wiki, connector: SourceConnector, changes: list[Change], report: SyncReport, concurrency: int | None, purge_removed: bool
) -> None:
    """One page: the last change of each origin, removals first, then what is new or changed as one batch."""
    last = {change.origin.key: change for change in changes}
    items = []
    for key, change in last.items():
        if change.kind == "remove":
            if purge_removed:
                purged = wiki.purge(key)
                known = purged is not None
                if purged is not None:
                    report.errors += purged.errors
                    report.stopped = report.stopped or purged.stopped
            else:
                known = wiki.mark_removed(key) is not None
            if known:
                report.removed += 1
            else:
                report.skipped += 1
            continue
        if _same_version(wiki, change):
            report.skipped += 1
            continue
        items.append(
            Item(change.filename, fetch=functools.partial(connector.fetch, change), origin=change.origin, fields=dict(change.fields))
        )
    if report.stopped is None:  # a purge that stopped (a model failing) would be followed by a batch that fails the same way
        report.add(wiki.ingest_many(items, concurrency=concurrency))


def _state_path(wiki: Wiki, connector: SourceConnector) -> Path:
    """Where the cursor lives: a hidden folder in the bundle, one file per connector and account.

    The name is readable, and a hash of the pair keeps two accounts with a long common prefix apart.
    """
    digest = hashlib.sha256(f"{connector.name}\0{connector.account}".encode()).hexdigest()[:12]
    readable = (slugify(connector.name, fallback=""), slugify(connector.account, max_len=30, fallback=""), digest)
    return wiki.cfg.bundle / SYNC_DIR / f"{'-'.join(p for p in readable if p)}.json"


def _load_cursor(path: Path) -> str | None:
    try:
        return str(json.loads(path.read_text(encoding="utf-8"))["cursor"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
