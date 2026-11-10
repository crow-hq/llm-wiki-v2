# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""CROW's beam: `beam` bounds the routing paths followed at ingestion."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents import steps_crow
from llmw2.agents.steps import CROW
from llmw2.config import CrowConfig
from tests.wiki.fakes import FakeClassifier, FakeLLM

FOLDERS = ("alpha", "beta", "gamma")


# These tests exercise the per-level routing, which handles "None of these": the flat Choice is left out.
LEVELS = replace(CROW, name="levels", route=steps_crow._route_by_levels)


@pytest.fixture
def wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    """/alpha, /beta, /gamma with one note each; beam 1 at ingestion."""
    crow = CrowConfig(beam=1)
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", crow=crow), llm=llm, classifier=classifier, steps=LEVELS)
    wiki.init()
    root = wiki.store.load()
    for name in FOLDERS:
        sub, _ = wiki.store.create_folder(root, name, f"{name.title()} things.")
        source = {"resource": "https://example.com", "title": name}
        wiki.store.write_note(sub, title=f"{name.title()} note", summary="About it.", body="Seeded.", tags=[], source=source)
    return wiki


def test_ingest_when_routing_is_uncertain_then_keeps_beam_paths(
    wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    llm.add("librarian/summarize", {"title": "Q3 revenue", "summary": "Revenue.", "tags": [], "body": "# Overview\n\nFacts."})
    classifier.add_choice("route", "alpha", {"alpha": 0.4, "beta": 0.35, "gamma": 0.25}, 0.3)  # uncertain: the beam splits
    classifier.add_choice("route", "Here", {"Here": 0.9}, 0.9)

    # ACT
    result = wiki.ingest("Revenue grew.")

    # ASSERT
    assert classifier.ops.count("route") == 2  # only alpha is followed: beam 1
    assert [d.choice for d in result.decisions if d.step == "route_step"] == ["/: alpha", "/alpha: Here"]
