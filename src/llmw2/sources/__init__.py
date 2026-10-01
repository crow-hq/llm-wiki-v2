# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sources that feed the wiki and keep it in step: the connector protocol, `sync`, and a local-folder connector."""

from __future__ import annotations

from llmw2.sources.base import Change, ChangeBatch, SourceConnector
from llmw2.sources.local import LocalFolderSource
from llmw2.sources.sync import SyncReport, sync

__all__ = ["Change", "ChangeBatch", "LocalFolderSource", "SourceConnector", "SyncReport", "sync"]
