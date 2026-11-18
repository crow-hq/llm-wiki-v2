# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The order of the search does not depend on the hash seed of the process: a query's words are added up in the order the query
gives them (a set's order changes from one process to the next, and so would the last bits of the scores), and notes with the same
score keep the order of the tree (the index) or of the keys (the index in memory)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from llmw2 import Wiki, WikiConfig
from llmw2.agents import lexical
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeLLM

ROOT = Path(__file__).resolve().parents[2]
WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo", "lima", "mike",
         "november", "oscar", "papa", "quebec", "romeo"]
QUERIES = [" ".join(WORDS[i : i + 9]) for i in range(0, 9)] + ["golf alpha mike echo kilo charlie papa india", "romeo"]
QUERIES += ["xray yankee zulu", "zulu xray yankee", "yankee zulu xray"]
# three notes of the same length that hold three words of the same frequency, counted 1, 2 and 6 in turn: the same score in exact
# arithmetic, and floating sums whose last bits depend on the order the words are added in
TIES = {name: " ".join(" ".join([w] * c) for w, c in zip(("xray", "yankee", "zulu"), counts, strict=True))
        for name, counts in (("tie-one", (1, 2, 6)), ("tie-two", (2, 6, 1)), ("tie-six", (6, 1, 2)))}

CHILD = r"""
import json, sys
from pathlib import Path
from llmw2 import Wiki, WikiConfig
from llmw2.agents import lexical
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_search_ties import QUERIES, docs
from collections import Counter
bm = lexical.Bm25({k: Counter(lexical.words(t)) for k, t in docs().items()})
wiki = Wiki(WikiConfig(bundle=Path(sys.argv[1]), mode="classic"), llm=FakeLLM(UsageTracker()))
out = {
    "scores": {q: {k: repr(s) for k, s in bm.scores(q).items()} for q in QUERIES},
    "search": {q: [h.path for h in wiki.search(q, k=12)] for q in QUERIES},
}
wiki.close()
print(json.dumps(out))
"""


def docs() -> dict[str, str]:
    """Notes that hold many of the query's words, in many counts: their scores are sums of many terms."""
    out = {}
    for i in range(40):
        picked = [WORDS[(i * 7 + j * 5) % len(WORDS)] for j in range(3 + i % 9)]
        out[f"note-{i:02d}"] = " ".join(picked + picked[: 1 + i % 4]) + f" filler{i}"
    return out | TIES


def build(root: Path) -> None:
    wiki = Wiki(WikiConfig(bundle=root, mode="classic"), llm=FakeLLM(UsageTracker()))
    wiki.init()
    for key, text in docs().items():
        wiki.store.write_note(wiki.store.load(), title=key, summary=key, body=text, tags=[], source={"resource": f"/raw/{key}.md"})
    wiki.close()


def run(root: Path, seed: str) -> dict[str, dict[str, object]]:
    path = os.pathsep.join([str(ROOT / "src"), str(ROOT), os.environ.get("PYTHONPATH", "")])
    env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": path}
    out = subprocess.run([sys.executable, "-c", CHILD, str(root)], capture_output=True, text=True, env=env, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def test_scores_and_order_are_the_same_whatever_the_hash_seed(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    build(root)
    first = run(root, "0")
    assert any(first["search"].values())
    for seed in ("1", "2", "3", "12345"):
        assert run(root, seed) == first, seed


def test_equal_scores_keep_the_order_of_the_tree_and_of_the_keys(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    wiki = Wiki(WikiConfig(bundle=root, mode="classic"), llm=FakeLLM(UsageTracker()))
    wiki.init()
    for title in ("Charlie", "Alpha", "Bravo"):  # written out of order, the same text: the same score
        wiki.store.write_note(wiki.store.load(), title=title, summary="s", body="the same zebra text", tags=[], source={})
    assert [h.path for h in wiki.search("zebra text", k=3)] == ["alpha.md", "bravo.md", "charlie.md"]
    wiki.close()
    bm = lexical.Bm25({k: Counter(lexical.words("same zebra text")) for k in ("z", "x", "y")})
    assert bm.rank("zebra text") == ["z", "x", "y"]
