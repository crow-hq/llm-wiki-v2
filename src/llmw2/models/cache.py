# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The classifier's answer cache: what a request was answered, kept under a hash of the request.

Nothing is stored but the hash and the answers: no state, no question text. A backend is any object with
`get(key)` and `put(key, answers)`, so a shared store (Redis, say) can stand in for the SQLite file of the bundle.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol

Answers = dict[str, dict[str, Any]]


class AnswerCache(Protocol):
    def get(self, key: str) -> Answers | None:
        """The answers stored under `key`, None when there are none."""
        ...

    def put(self, key: str, answers: Answers) -> None:
        """Store `answers` under `key`."""
        ...


def cache_key(base_url: str, model: str, state: str, questions: dict[str, dict[str, Any]]) -> str:
    """The sha256 of everything a classifier's answer depends on."""
    payload = json.dumps([base_url, model, state, questions], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class SqliteCache:
    """The default backend: one SQLite file in WAL mode. Each call opens its own connection, so threads and processes
    share it safely and no handle stays open to block the file's removal (Windows)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._ready = False
        self._setup_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        if not self._ready:
            with self._setup_lock:  # the first requests of several threads must not all switch the journal mode at once
                if not self._ready:
                    self._setup()
                    self._ready = True
        return sqlite3.connect(self.path, timeout=30)

    def _setup(self) -> None:
        """Make the file and its table, in WAL mode (kept by the file). Another process may be doing the same: try again."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(100):
            try:
                with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
                    db.execute("PRAGMA journal_mode=WAL")
                    db.execute("CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID")
                return
            except sqlite3.OperationalError:  # "database is locked": a switch of the journal mode does not wait
                if attempt == 99:
                    raise
                time.sleep(0.05)

    def get(self, key: str) -> Answers | None:
        if not self._ready and not self.path.exists():
            return None
        with closing(self._connect()) as db:
            row = db.execute("SELECT value FROM answers WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, answers: Answers) -> None:
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO answers VALUES (?, ?)", (key, json.dumps(answers)))
