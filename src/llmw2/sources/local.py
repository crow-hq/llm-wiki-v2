# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`LocalFolderSource`: a folder of documents (for example an rclone-synced OneDrive) as a connector."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePath, PurePosixPath
from typing import Any

from llmw2.bundle.files import SUFFIXES
from llmw2.bundle.origin import Origin
from llmw2.errors import InputError
from llmw2.sources.base import Change, ChangeBatch

PAGE = 200  # changes handed over at once: the sync saves its cursor after each page, so a stop redoes at most one


class LocalFolderSource:
    """Every readable file under a folder, hidden files and folders and symbolic links excepted.

    An item's id is its path relative to the folder and its version is "<mtime_ns>-<size>". The cursor is the
    JSON listing of what has been handed over ({path: version}), so a change is a file that is new, differs or is gone.
    `fields`, if given, is called with each file's path relative to the folder and gives its document's `fields`
    (asked only for the files handed over). It is not part of the cursor: a file's fields are sent again only when the file changes.
    Changes come PAGE at a time, and a page's cursor is the listing with that page applied: asked again from it, the
    source gives the next page, and a sync stopped in the middle resumes from the last page it finished.
    """

    name = "local"

    def __init__(self, folder: Path, fields: Callable[[PurePosixPath], dict[str, Any]] | None = None) -> None:
        self.fields = fields
        self.folder = Path(folder).expanduser().resolve()
        if not self.folder.is_dir():
            raise InputError(f"not a folder: {folder}")
        self.account = str(self.folder)

    def changes(self, cursor: str | None) -> ChangeBatch:
        seen: dict[str, str] = json.loads(cursor) if cursor else {}
        now = self._listing()
        changes = [("upsert", rel, version) for rel, version in now.items() if seen.get(rel) != version]
        changes += [("remove", rel, "") for rel in seen if rel not in now]
        page: list[Change] = []
        for kind, rel, version in changes[:PAGE]:
            if kind == "upsert":
                page.append(Change("upsert", self._origin(rel, version), PurePath(rel).name, self._fields(rel)))
                seen[rel] = now[rel]
            else:
                page.append(Change("remove", Origin(self.name, self.account, rel), PurePath(rel).name))
                del seen[rel]
        return ChangeBatch(page, json.dumps(seen, sort_keys=True), more=len(changes) > PAGE)

    def fetch(self, change: Change) -> bytes:
        return (self.folder / change.origin.id).read_bytes()

    def _listing(self) -> dict[str, str]:
        """{relative POSIX path: version} of the files worth reading."""
        found: dict[str, str] = {}
        for path in sorted(self.folder.rglob("*")):
            rel = path.relative_to(self.folder)
            if (
                any(part.startswith(".") for part in rel.parts)
                or path.name.startswith("~$")  # Office's lock file of a document open in Word or Excel
                or path.suffix.lower() not in SUFFIXES
                or any((self.folder / parent).is_symlink() for parent in [rel, *rel.parents][:-1])
            ):  # hidden, unreadable type, or a link (or inside one): it could lead outside the folder
                continue
            try:
                stat = path.stat()
            except OSError:  # gone since the walk began
                continue
            if path.is_file():
                found[rel.as_posix()] = f"{stat.st_mtime_ns}-{stat.st_size}"
        return found

    def _fields(self, rel: str) -> dict[str, Any]:
        return dict(self.fields(PurePosixPath(rel))) if self.fields else {}

    def _origin(self, rel: str, version: str) -> Origin:
        path = self.folder / rel
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
        return Origin(self.name, self.account, rel, link=str(path), version=version, modified=modified)
