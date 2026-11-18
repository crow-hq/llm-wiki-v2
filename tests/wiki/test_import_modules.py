# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What `import llmw2` loads of the package: a fixed list, so that a module that comes in with the package (and its import time, paid by
every program and every command) is a choice seen in review. The search index is loaded with the first `Wiki`, not with the package."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

LOADED = {
    "llmw2", "llmw2.agents", "llmw2.agents.base", "llmw2.agents.conflicts", "llmw2.agents.data", "llmw2.agents.echo",
    "llmw2.agents.grouping", "llmw2.agents.lexical", "llmw2.agents.librarian", "llmw2.agents.linking", "llmw2.agents.note_checks",
    "llmw2.agents.prompts", "llmw2.agents.researcher", "llmw2.agents.revisions", "llmw2.agents.state", "llmw2.agents.steps",
    "llmw2.agents.steps_classic", "llmw2.agents.steps_crow", "llmw2.audit", "llmw2.batch", "llmw2.bundle", "llmw2.bundle.check",
    "llmw2.bundle.document", "llmw2.bundle.filelock", "llmw2.bundle.files", "llmw2.bundle.formats", "llmw2.bundle.journal",
    "llmw2.bundle.logfile", "llmw2.bundle.origin", "llmw2.bundle.origin_index", "llmw2.bundle.proposals", "llmw2.bundle.sheets",
    "llmw2.bundle.store", "llmw2.bundle.tree", "llmw2.bundle.txn", "llmw2.config", "llmw2.errors", "llmw2.filer", "llmw2.models",
    "llmw2.models.cache", "llmw2.models.classifier", "llmw2.models.client", "llmw2.models.embedder", "llmw2.models.llm",
    "llmw2.models.usage", "llmw2.search", "llmw2.sources", "llmw2.sources.base", "llmw2.sources.local", "llmw2.sources.sync",
    "llmw2.wiki",
}


def test_import_llmw2_loads_these_modules_of_the_package_and_no_other() -> None:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), os.environ.get("PYTHONPATH", "")])}
    code = "import json, sys, llmw2; print(json.dumps(sorted(m for m in sys.modules if m == 'llmw2' or m.startswith('llmw2.'))))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True).stdout
    loaded = set(json.loads(out.strip().splitlines()[-1]))
    assert "llmw2.searchindex" not in loaded
    assert loaded == LOADED, {"new": sorted(loaded - LOADED), "gone": sorted(LOADED - loaded)}
