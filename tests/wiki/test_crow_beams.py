# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""CROW's two beams: `beam` bounds the routing paths at ingestion, `retrieval_beam` the folders explored per level."""

from __future__ import annotations

from pathlib import Path

import pytest

from okf_wiki import Wiki, WikiConfig
from okf_wiki.config import CrowConfig
from tests.wiki.fakes import FakeClassifier, FakeLLM

FOLDERS = ("alpha", "beta", "gamma")


@pytest.fixture
def wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    """/alpha, /beta, /gamma with one note each; beam 1 at ingestion, 3 at retrieval."""
    crow = CrowConfig(beam=1, retrieval_beam=3, tau_fold=0.1, tau_ret=0.1)
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", crow=crow), llm=llm, classifier=classifier)
    wiki.init()
    root = wiki.store.load()
    for name in FOLDERS:
        sub, _ = wiki.store.create_folder(root, name, f"{name.title()} things.")
        source = {"resource": "https://example.com", "title": name}
        wiki.store.write_note(sub, title=f"{name.title()} note", summary="About it.", body="Seeded.", tags=[], source=source)
    return wiki


def test_ask_when_retrieval_beam_exceeds_beam_then_explores_retrieval_beam_folders(
    wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    classifier.noul_scores = {
        "retrieve_folder": {f'folder "{name}"': p for name, p in zip(FOLDERS, (0.9, 0.8, 0.7), strict=True)},
        "retrieve_note": {'note titled "Gamma note"': 0.9},
    }
    llm.add("researcher/answer", "Answer.")

    # ACT
    answer = wiki.ask("What about gamma?")

    # ASSERT
    assert answer.notes == ["gamma/gamma-note.md"]  # the third folder was explored although beam is 1


def test_ingest_when_retrieval_beam_exceeds_beam_then_routing_keeps_beam_paths(
    wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    llm.add("librarian/summarize", {"title": "Q3 revenue", "summary": "Revenue.", "tags": [], "body": "# Overview\n\nFacts."})
    classifier.add_choice("route", "alpha", {"alpha": 0.4, "beta": 0.35, "gamma": 0.25}, 0.3)  # uncertain: the beam splits
    classifier.add_choice("route", "Here", {"Here": 0.9}, 0.9)

    # ACT
    result = wiki.ingest("Revenue grew.")

    # ASSERT
    assert classifier.ops.count("route") == 2  # only alpha is followed: beam 1, not retrieval_beam 3
    assert [d.choice for d in result.decisions if d.step == "route_step"] == ["/: alpha", "/alpha: Here"]
