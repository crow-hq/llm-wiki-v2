# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The release before this code opens a wiki that this code wrote, with every record this code adds on the disk: the journal of
`remember` with a tombstone left open (a copy let through named on it, its copy by an ordinary reuse), the undo log, a log line purged
in place, the search index (with its WAL) and the vectors kept with their rollback journal. Each is refused or harmless: 1.0.2 does
not know `.llmw2/journal/` nor the index file, and its check, search, ingest and purge work on such a wiki.

Needs the source of that release: `LLMW2_RELEASE_SRC` is the `src` folder of a checkout of it (read only; the test skips without it)."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2 import filer as F
from llmw2.bundle import journal as J
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM

RELEASE_SRC = os.environ.get("LLMW2_RELEASE_SRC", "")

OLD = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[3])
from pathlib import Path
import llmw2
from llmw2 import Wiki, WikiConfig
from llmw2.bundle.check import check
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM

root = Path(sys.argv[2])
before = sorted(str(p) for p in check(root))
w = Wiki(WikiConfig(bundle=root, mode="classic"), llm=ContentLLM(UsageTracker()))
hits = [h.path for h in w.search("heron")]
made = w.ingest("topic: birds-9\nThe crane dances in the marsh.", title="Crane", resource="https://x/crane").action
purged = w.purge("remember::k:2")
after = sorted(str(p) for p in check(root))
print(json.dumps({"version": llmw2.__version__, "before": before, "after": after, "hits": hits, "made": made,
                  "purged": purged is not None}))
"""


def embedder(texts: list[str]) -> list[list[float]]:
    return [[b / 255.0 for b in hashlib.sha256(t.encode("utf-8")).digest()[:8]] for t in texts]


def _wiki_of_this_code(root: Path) -> None:
    w = Wiki(WikiConfig(bundle=root, mode="classic"), llm=ContentLLM(UsageTracker()), embedder=embedder)
    w.init()
    try:
        w.remember("topic: birds-1\nThe heron waits by the river.", key="k:1")
        w.remember("topic: birds-2\nThe owl hunts at night.", key="k:2")
        w.remember("topic: birds-3\nThe swan sleeps on the lake.", key="k:3")
        w.drain(60)
        w.search("heron")
        w.purge("k:3")  # a log line purged in place, a tombstone completed
        w._stop_filer()
        hub = w._journal()
        tomb = hub.journal.append_entry("", state={"forget": {"kh": F.key_hash("k:none")}})  # a tombstone left open
        hub.journal.append(J.T_FILED, {"id": tomb.id, "forgot": ["0123456789abcdef"]})  # a copy let through, named on it
        hub.refresh()
        F.carry(hub, hub.entries[tomb.id])  # its copy by an ordinary reuse, with the segment of the original (`seg`)
        w.remember("topic: birds-4\nThe crow keeps a queued entry.", key="k:4")  # queued: no filer
    finally:
        w.close()
    assert any((root / ".llmw2" / "cache").glob("search-v*.sqlite")) and any((root / ".llmw2" / "journal").glob("*.wal"))


@pytest.mark.skipif(not RELEASE_SRC or not Path(RELEASE_SRC, "llmw2").is_dir(), reason="LLMW2_RELEASE_SRC is not set")
def test_the_release_before_works_on_a_wiki_this_code_wrote(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    _wiki_of_this_code(root)
    args = [sys.executable, "-B", "-c", OLD, RELEASE_SRC, str(root), str(Path(RELEASE_SRC).parent)]
    p = subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)
    assert p.returncode == 0, p.stderr[-2000:]
    got = json.loads(p.stdout.strip().splitlines()[-1])
    assert got["version"] != "" and got["hits"] and got["made"] == "created" and got["purged"]
    assert not [x for x in got["before"] + got["after"] if ".llmw2" in x or "search-v" in x or "journal" in x]
    back = Wiki(WikiConfig(bundle=root, mode="classic"), llm=ContentLLM(UsageTracker()))  # and this code reads its journal back
    try:
        hub = back._journal()
        hub.refresh()
        assert len(hub.forgets()) == 1  # the tombstone left open, with its copy: one
        data = b"".join(p.read_bytes() for p in (root / ".llmw2" / "journal").glob("*.wal")).replace(b" ", b"")
        assert b'"forgot":["0123456789abcdef"]' in data and b'"seg":' in data  # what 1.0.2 had in front of it
    finally:
        back.close()
