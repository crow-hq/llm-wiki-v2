# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`LocalFolderSource`: a folder of documents (for example an rclone-synced OneDrive) as a connector."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path, PurePath

from llmw2.bundle.files import SUFFIXES
from llmw2.bundle.origin import Origin
from llmw2.errors import InputError
from llmw2.sources.base import Change, ChangeBatch


class LocalFolderSource:
    """Every readable file under a folder, hidden files and folders and symbolic links excepted.

    An item's id is its path relative to the folder and its version is "<mtime_ns>-<size>". The cursor is the
    JSON listing of the last run ({path: version}), so a change is a file that is new, differs or is gone.
    """

    name = "local"

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder).expanduser().resolve()
        if not self.folder.is_dir():
            raise InputError(f"not a folder: {folder}")
        self.account = str(self.folder)

    def changes(self, cursor: str | None) -> ChangeBatch:
        seen: dict[str, str] = json.loads(cursor) if cursor else {}
        now = self._listing()
        changes = [
            Change("upsert", self._origin(rel, version), PurePath(rel).name)
            for rel, version in now.items()
            if seen.get(rel) != version
        ]
        changes += [
            Change("remove", Origin(self.name, self.account, rel), PurePath(rel).name) for rel in seen if rel not in now
        ]
        return ChangeBatch(changes, json.dumps(now, sort_keys=True))

    def fetch(self, change: Change) -> bytes:
        return (self.folder / change.origin.id).read_bytes()

    def _listing(self) -> dict[str, str]:
        """{relative POSIX path: version} of the files worth reading."""
        found: dict[str, str] = {}
        for path in sorted(self.folder.rglob("*")):
            rel = path.relative_to(self.folder)
            if (
                any(part.startswith(".") for part in rel.parts)
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

    def _origin(self, rel: str, version: str) -> Origin:
        path = self.folder / rel
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
        return Origin(self.name, self.account, rel, link=str(path), version=version, modified=modified)
