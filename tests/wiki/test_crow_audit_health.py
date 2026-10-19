# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: the routing audit asks the flat Choice, and the taxonomy health ignores what plan and split opened."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from llmw2 import ModelError, Wiki, WikiConfig
from llmw2.bundle.tree import LOG, Note
from tests.wiki.fakes import FakeClassifier, FakeLLM

HERE, NEW, NONE = "Here", "New subfolder", "None of these"
SUMMARIZE, NAME_FOLDER = "librarian/summarize", "librarian/name_folder"


# -- helpers ----------------------------------------------------------------------


def add_folder(wiki: Wiki, parent: str, name: str, description: str) -> None:
    wiki.store.create_folder(wiki.store.load().find(parent), name, description)


def add_note(wiki: Wiki, folder: str, title: str) -> Note:
    return wiki.store.write_note(
        wiki.store.load().find(folder),
        title=title,
        summary=f"About {title}.",
        body="# Overview\n\nSeeded.",
        tags=[],
        source={"resource": "/raw/seed.md", "title": "Seed"},
    )


@pytest.fixture
def tree(crow_wiki: Wiki) -> Wiki:
    """/alpha, /alpha/sub, /beta and /gamma, one note in /alpha."""
    add_folder(crow_wiki, "/", "alpha", "Alpha things.")
    add_folder(crow_wiki, "/alpha", "sub", "Sub things.")
    add_folder(crow_wiki, "/", "beta", "Beta things.")
    add_folder(crow_wiki, "/", "gamma", "Gamma things.")
    add_note(crow_wiki, "/alpha", "Misfiled")
    return crow_wiki


def flat(classifier: FakeClassifier, choice: str, p: float) -> None:
    """The flat Choice of one note: `choice` with probability p, the rest on None of these."""
    classifier.add_choice("route", choice, {choice: p, NONE: 1 - p} if choice != NONE else {NONE: p, "/beta": 1 - p}, 0.9)


# -- the audit -------------------------------------------------------------------------------


def test_audit_proposes_a_move_when_the_flat_choice_picks_another_branch(tree: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    flat(classifier, "/beta", 0.9)

    # ACT
    proposals = tree.audit_routes()

    # ASSERT
    assert len(proposals) == 1
    p = proposals[0]
    assert (p.path, p.title, p.current, p.proposed, p.applied) == ("alpha/misfiled.md", "Misfiled", "/alpha", "/beta", False)
    assert p.confidence == pytest.approx(0.9)
    assert classifier.ops == ["route"]  # one flat Choice per note, no per-level routing
    assert list(classifier.calls[0][2]["q"]["criteria"]) == ["/alpha", "/alpha/sub", "/beta", "/gamma", NONE]
    assert llm.calls == []


def test_audit_proposes_nothing_for_none_of_these_and_does_not_route_by_levels(
    tree: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    flat(classifier, NONE, 0.95)

    # ACT / ASSERT
    assert tree.audit_routes(min_confidence=0.0) == []
    assert classifier.ops == ["route"] and llm.calls == []


@pytest.mark.parametrize(("p", "minimum", "proposed"), [(0.65, None, False), (0.7, None, True), (0.65, 0.5, True), (0.9, 0.95, False)])
def test_audit_compares_the_top_probability_with_min_confidence(
    tree: Wiki, classifier: FakeClassifier, p: float, minimum: float | None, proposed: bool
) -> None:
    # ARRANGE
    flat(classifier, "/gamma", p)

    # ACT
    proposals = tree.audit_routes() if minimum is None else tree.audit_routes(min_confidence=minimum)

    # ASSERT
    assert [x.proposed for x in proposals] == (["/gamma"] if proposed else [])


def test_audit_never_proposes_the_notes_own_folder(tree: Wiki, classifier: FakeClassifier) -> None:
    flat(classifier, "/alpha", 0.95)

    assert tree.audit_routes() == []


def test_audit_never_proposes_a_descendant_of_the_notes_folder(tree: Wiki, classifier: FakeClassifier) -> None:
    flat(classifier, "/alpha/sub", 0.95)

    assert tree.audit_routes() == []


def test_audit_never_proposes_an_ancestor_of_the_notes_folder(crow_wiki: Wiki, classifier: FakeClassifier) -> None:
    # ARRANGE: the note sits in a subfolder, as after a plan or a split
    add_folder(crow_wiki, "/", "alpha", "Alpha things.")
    add_folder(crow_wiki, "/alpha", "sub", "Sub things.")
    add_note(crow_wiki, "/alpha/sub", "Deep note")
    flat(classifier, "/alpha", 0.95)

    # ACT / ASSERT
    assert crow_wiki.audit_routes() == []
    assert classifier.ops == ["route"]


def test_audit_proposes_nothing_when_the_classifier_fails_and_makes_no_llm_call(
    tree: Wiki, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ARRANGE
    def ask(*args: object, **kwargs: object) -> dict[str, dict[str, Any]]:
        raise ModelError("classifier unreachable")

    monkeypatch.setattr(classifier, "ask", ask)

    # ACT / ASSERT
    assert tree.audit_routes(min_confidence=0.0) == []
    assert llm.calls == []


def test_audit_without_apply_changes_nothing(tree: Wiki, classifier: FakeClassifier) -> None:
    # ARRANGE
    flat(classifier, "/beta", 0.9)
    log = (tree.cfg.bundle / LOG).read_text(encoding="utf-8")

    # ACT
    tree.audit_routes(apply=False)

    # ASSERT
    assert (tree.cfg.bundle / "alpha" / "misfiled.md").is_file()
    assert (tree.cfg.bundle / LOG).read_text(encoding="utf-8") == log


def test_audit_with_apply_moves_the_note(tree: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    flat(classifier, "/beta", 0.9)

    # ACT
    proposals = tree.audit_routes(apply=True)

    # ASSERT
    assert [(p.path, p.proposed, p.applied) for p in proposals] == [("alpha/misfiled.md", "/beta", True)]
    assert (tree.cfg.bundle / "beta" / "misfiled.md").is_file()
    assert not (tree.cfg.bundle / "alpha" / "misfiled.md").exists()
    assert tree.check() == []
    assert "Moved" in (tree.cfg.bundle / LOG).read_text(encoding="utf-8")
    assert llm.calls == []


# -- the taxonomy health ----------------------------------------------------------------------------


def created(rel: str, suffix: str = "") -> str:
    return f"* **Created**: [{rel}]({rel}) - from /raw/x.md (steps abc, fake/llm){suffix}"


def new_folder(rel: str, suffix: str = "") -> str:
    return f"* **Created folder**: [/{rel}]({rel}/index.md) - A folder.{suffix}"


def write_log(wiki: Wiki, days: dict[str, list[str]]) -> None:
    parts = ["# Wiki log", ""]
    for day, lines in days.items():
        parts += [f"## {day}", "", *lines, ""]
    (wiki.cfg.bundle / LOG).write_text("\n".join(parts), encoding="utf-8")


def health_rows(wiki: Wiki, **kw: Any) -> dict[tuple[str, str], Any]:
    return {(r.folder.strip("/"), r.month): r for r in wiki.taxonomy_health(**kw)}


FILED = [created(f"finance/{c}.md") for c in "abcde"]


def test_health_counts_a_plain_created_folder_but_not_the_ones_made_by_plan_or_split(classic_wiki: Wiki) -> None:
    # ARRANGE
    write_log(
        classic_wiki,
        {
            "2026-09-28": [
                *FILED,
                new_folder("finance/routed"),  # opened by routing while filing: counts
                new_folder("finance/planned", ", by plan"),
                new_folder("finance/more", ", by plan_more"),
                new_folder("finance/split", ", by split"),
            ]
        },
    )

    # ACT
    row = health_rows(classic_wiki)[("finance", "2026-09")]

    # ASSERT
    assert (row.filed, row.new_subfolders, row.fallbacks) == (5, 1, 0)
    assert row.share == pytest.approx(0.2)
    assert row.flag is False


def test_health_does_not_flag_a_parent_whose_subfolders_were_all_made_by_plan_or_split(classic_wiki: Wiki) -> None:
    # ARRANGE
    write_log(
        classic_wiki,
        {
            "2026-09-28": [
                *FILED,
                new_folder("finance/planned", ", by plan"),
                new_folder("finance/more", ", by plan_more"),
                new_folder("finance/split", ", by split"),
            ]
        },
    )

    # ACT
    row = health_rows(classic_wiki)[("finance", "2026-09")]

    # ASSERT
    assert (row.new_subfolders, row.share, row.flag) == (0, 0.0, False)


def test_health_counts_split_lines_as_nothing(classic_wiki: Wiki) -> None:
    # ARRANGE
    split = "* **Split**: [/finance](finance/index.md) - at 12 notes, 2 opened: /finance/a, /finance/b"
    write_log(classic_wiki, {"2026-09-28": [*FILED, split, new_folder("finance/a", ", by split"), new_folder("finance/b", ", by split")]})

    # ACT
    rows = health_rows(classic_wiki)

    # ASSERT
    row = rows[("finance", "2026-09")]
    assert (row.filed, row.new_subfolders, row.fallbacks) == (5, 0, 0)
    assert [k for k in rows if k[0] != "finance"] == []


def test_health_of_a_log_with_only_a_split_line_has_no_filed_folder(classic_wiki: Wiki) -> None:
    # ARRANGE
    write_log(classic_wiki, {"2026-09-28": ["* **Split**: [/finance](finance/index.md) - at 12 notes, 0 opened"]})

    # ACT / ASSERT
    assert all(r.filed == 0 and r.new_subfolders == 0 and r.fallbacks == 0 for r in classic_wiki.taxonomy_health())


def test_health_still_counts_a_folder_opened_by_routing_during_filing(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", strict_new_folders=False), llm=llm, classifier=classifier)
    wiki.init()
    add_folder(wiki, "/", "sales", "Sales documents.")
    for n in range(5):
        llm.add(SUMMARIZE, {"title": f"Deal {n}", "summary": "S.", "tags": [], "body": "Facts."})
        classifier.add_choice("route", "/sales", {"/sales": 0.9, NONE: 0.1}, 0.9)
        wiki.ingest(f"Deal {n}.")
    llm.add(SUMMARIZE, {"title": "Overseas", "summary": "S.", "tags": [], "body": "Facts."})
    llm.add(NAME_FOLDER, {"name": "emea", "description": "EMEA deals."})
    classifier.add_choice("route", NONE, {NONE: 0.9, "/sales": 0.1}, 0.9)  # nothing covers it: the per-level routing opens a folder
    classifier.add_choice("route", "sales", {"sales": 0.9, NEW: 0.1}, 0.9)
    classifier.add_choice("route", NEW, {NEW: 0.9, HERE: 0.05, NONE: 0.05}, 0.9)

    # ACT
    result = wiki.ingest("Overseas deal.")
    rows = [r for r in wiki.taxonomy_health() if r.folder.strip("/") == "sales"]

    # ASSERT
    assert result.created_folders == ["/sales/emea"]
    assert [(r.filed, r.new_subfolders) for r in rows] == [(5, 1)]
