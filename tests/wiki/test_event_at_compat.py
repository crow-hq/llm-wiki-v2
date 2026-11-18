# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A raw copy with `event_at` in its front matter (the time of the event a `remember(at=...)` was about) is read by the code of the
release before it without errors, and its `check` does not report the key. Written before the code that writes the key.

Needs the source of that release: `LLMW2_RELEASE_SRC` is the `src` folder of a checkout of it (read only; the test skips without it)."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from llmw2.bundle.origin import content_hash
from llmw2.bundle.store import WikiStore

RELEASE_SRC = os.environ.get("LLMW2_RELEASE_SRC", "")
EVENT_AT = "2026-03-01T10:15:30.123456+00:00"
TEXT = "The robot left the dock at ten."

READER = r"""
import json, sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import llmw2
from llmw2.bundle.check import check
from llmw2.bundle.origin import Origin
from llmw2.bundle.store import WikiStore

root = Path(sys.argv[2])
store = WikiStore(root, actor="old")
store.init()
tree = store.load()
notes = [n.rel for f in tree.walk() for n in f.notes] if hasattr(tree, "walk") else None
problems = [str(p) for p in check(root)]
raws = {p.name: p.read_text(encoding="utf-8") for p in (root / "raw").glob("*.md")}
adopted = store.adopt_raw(Origin("t", "", "k"), sys.argv[3])  # rewrites the front matter of the raw copy it adopts
after = {p.name: p.read_text(encoding="utf-8") for p in (root / "raw").glob("*.md")}
print(json.dumps({"version": llmw2.__version__, "notes": notes, "problems": problems, "adopted": adopted, "after": after, "before": raws}))
"""


def _wiki_with_event_at(root: Path) -> Path:
    store = WikiStore(root, actor="test")
    store.init()
    with store.lock():
        folder, _ = store.create_folder(store.load(), "robots", "About robots.")
        raw = store.save_raw(TEXT, title="Dock", resource=None)
        store.write_note(folder, title="Dock", summary="The robot left.", body="# Overview\n\nIt left.", tags=[], source={"resource": raw})
        store.append_log("Created", "Dock")
    path = root / raw.lstrip("/")
    doc = path.read_text(encoding="utf-8")
    assert "event_at" not in doc
    path.write_text(doc.replace("\nhash:", f"\nevent_at: '{EVENT_AT}'\nhash:", 1), encoding="utf-8")  # a top-level key of the front matter
    assert f"event_at: '{EVENT_AT}'" in path.read_text(encoding="utf-8")
    return path


@pytest.mark.skipif(not RELEASE_SRC or not Path(RELEASE_SRC, "llmw2").is_dir(), reason="LLMW2_RELEASE_SRC is not set")
def test_the_release_before_reads_a_raw_with_event_at_and_check_says_nothing_of_it(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    raw = _wiki_with_event_at(root)
    p = subprocess.run(
        [sys.executable, "-B", "-c", READER, RELEASE_SRC, str(root), TEXT], capture_output=True, text=True, timeout=120, check=False
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["version"] == "1.0.2" or re.match(r"\d+\.\d+\.\d+", out["version"])
    assert out["problems"] == [], out["problems"]
    assert not [x for x in out["problems"] if "event_at" in x]
    # the older code kept the key and the hash when it rewrote the copy (adopting it for an origin): the key is not lost
    assert out["adopted"] is True
    assert f"event_at: '{EVENT_AT}'" in out["after"][raw.name] or f"event_at: {EVENT_AT}" in out["after"][raw.name]
    assert content_hash(TEXT) in out["after"][raw.name]


def test_this_code_reads_and_checks_the_same_wiki(tmp_path: Path) -> None:
    from llmw2.bundle.check import check
    from tests.wiki.problems import errors

    root = tmp_path / "wiki"
    _wiki_with_event_at(root)
    assert errors(check(root)) == []
    assert not [p for p in check(root) if "event_at" in str(p)]
