"""Keep a wiki in step with a source: a minimal `SourceConnector` over an in-memory "drive".

A connector offers `name`, `account`, `changes(cursor)` and `fetch(change)`; `sync` does the rest and
remembers the cursor. Here the first sync files three documents, the second sees one edited and one
deleted at the source. The steps are model-free, so this needs NO API key.

    python examples/sync_connector.py [bundle-folder]

Without a folder it uses a temporary one.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from llmw2 import Candidate, Change, ChangeBatch, NoteDraft, Origin, Route, Source, StepContext, Steps, SyncReport, Wiki, WikiConfig, sync
from llmw2.bundle.tree import Folder, Note


class DictDrive:
    """A source whose documents live in a dict: {item id: (version, text)}."""

    name = "drive"
    account = "demo"

    def __init__(self, items: dict[str, tuple[str, str]]) -> None:
        self.items = items

    def changes(self, cursor: str | None) -> ChangeBatch:
        seen: dict[str, str] = json.loads(cursor) if cursor else {}
        now = {item: version for item, (version, _) in self.items.items()}
        changes = [Change("upsert", Origin(self.name, self.account, i, version=v), f"{i}.md") for i, v in now.items() if seen.get(i) != v]
        changes += [Change("remove", Origin(self.name, self.account, i), f"{i}.md") for i in seen if i not in now]
        return ChangeBatch(changes, json.dumps(now))

    def fetch(self, change: Change) -> bytes:
        return self.items[change.origin.id][1].encode("utf-8")


def summarize(ctx: StepContext, source: Source) -> Candidate:
    return Candidate(source.title or "Untitled", source.text.splitlines()[0], [], source.text)


def route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
    # With `create`, the match pool is route.candidates + route.folder: list the folder whose notes may match.
    inbox = root.subfolder("inbox")
    return Route(root, False, [inbox] if inbox else [], create=[("inbox", "Everything synced from the drive.")])


def match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
    """The same title is the same document."""
    return next((n for n in pool if n.title == cand.title), None)


def consolidate(ctx: StepContext, note: Note, cand: Candidate) -> bool:
    return True


def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
    """The new version replaces the old text."""
    return NoteDraft(title=note.title, summary=source.text.splitlines()[0], body=source.text)


def relate(ctx: StepContext, note: Note, others: list[Note]) -> list[Note]:
    return []


STEPS = Steps(name="drive-sync", summarize=summarize, route=route, match=match, consolidate=consolidate, merge=merge, relate=relate)


def main(bundle: Path | None = None) -> tuple[Wiki, SyncReport, SyncReport]:
    bundle = bundle or Path(tempfile.mkdtemp(prefix="llmw2-sync-"))
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), steps=STEPS)
    drive = DictDrive(
        {
            "holidays": ("v1", "Holidays\nThe office closes on 24 December."),
            "parking": ("v1", "Parking\nVisitors park in the yard."),
            "wifi": ("v1", "Wifi\nThe guest network is called Guests."),
        }
    )
    first = sync(wiki, drive)
    print("first sync: ", first.to_dict())
    drive.items["holidays"] = ("v2", "Holidays\nThe office closes from 24 December to 2 January.")
    del drive.items["parking"]
    second = sync(wiki, drive)
    print("second sync:", second.to_dict())
    print("model calls:", wiki.usage.llm.calls)
    return wiki, first, second


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
