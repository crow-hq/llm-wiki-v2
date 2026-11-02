# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`rebuild` on the folders a wiki has: no routing fallback to the LLM, no folder made, no split; an ingest as before."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import CROW, Item, Route, Wiki
from llmw2.agents import steps_crow
from llmw2.agents.base import Decision
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import Steps
from llmw2.bundle.tree import Folder
from llmw2.wiki import PLAN_ROOTS_OPEN
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_librarian_crow import add_folder, add_note, draft, make_wiki, uncertain_alpha

HERE, NEW, NONE = "Here", "New subfolder", "None of these"
SUMMARIZE, NAME_FOLDER = "librarian/summarize", "librarian/name_folder"
ROUTE_FALLBACK, SPLIT = "librarian/route_fallback", "librarian/split"
ITEM = Item("revenue.md", data=b"Revenue grew.")


def rebuilt(wiki: Wiki) -> Any:
    report = wiki.rebuild([ITEM])
    assert report.stopped is None and len(report.results) == 1
    return report.results[0][1]


def route_of(result: Any) -> Decision:
    return next(d for d in reversed(result.decisions) if d.step == "route")


@pytest.fixture
def alpha(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    wiki = make_wiki(bundle, llm, classifier)
    add_folder(wiki, "/", "alpha", "Alpha things.")
    return wiki


# -- a score under tau_path ---------------------------------------------------------------


def test_rebuild_with_a_score_under_tau_path_files_in_the_best_paths_folder_without_the_llm(
    alpha: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    uncertain_alpha(classifier)

    # ACT
    result = rebuilt(alpha)

    # ASSERT
    assert llm.ops == [SUMMARIZE]
    assert (result.folder, result.created_folders) == ("/alpha", [])
    assert (route_of(result).decider, route_of(result).choice, route_of(result).fallback) == ("classifier", "/alpha (Here)", True)


def test_an_ingest_with_a_score_under_tau_path_still_asks_the_llm(alpha: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    llm.add(SUMMARIZE, draft("Q3 revenue")).add(ROUTE_FALLBACK, {"action": "select", "folder": "/alpha"})
    uncertain_alpha(classifier)

    # ACT
    result = alpha.ingest("Revenue grew.")

    # ASSERT
    assert llm.ops == [SUMMARIZE, ROUTE_FALLBACK]
    assert route_of(result).decider == "llm"


def test_rebuild_with_no_finished_path_files_in_the_likeliest_top_level_folder(
    alpha: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE: None of these below /alpha: no path finishes; the flat Choice of the likeliest top-level folder follows
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    classifier.add_choice("route", "alpha", {"alpha": 0.9, NEW: 0.1}, 0.9)
    classifier.add_choice("route", NONE, {NONE: 0.9, HERE: 0.1}, 0.9)
    classifier.add_choice("route", "/alpha", {"/alpha": 0.7, NONE: 0.3}, 0.7)

    # ACT
    result = rebuilt(alpha)

    # ASSERT
    assert llm.ops == [SUMMARIZE]
    assert (result.folder, result.created_folders) == ("/alpha", [])


# -- no folder is made ---------------------------------------------------------------------


def test_rebuild_reads_a_new_subfolder_as_here(alpha: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    classifier.add_choice("route", "alpha", {"alpha": 0.9, NEW: 0.1}, 0.9)
    classifier.add_choice("route", NEW, {NEW: 0.9, HERE: 0.1}, 0.9)

    # ACT
    result = rebuilt(alpha)

    # ASSERT
    assert llm.ops == [SUMMARIZE]  # no name_folder
    assert (result.folder, result.created_folders) == ("/alpha", [])
    assert route_of(result).choice == f"/alpha ({HERE})"


def test_an_ingest_with_a_new_subfolder_still_makes_it(alpha: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    llm.add(SUMMARIZE, draft("Q3 revenue")).add(NAME_FOLDER, {"name": "revenue", "description": "Revenue reports."})
    classifier.add_choice("route", "alpha", {"alpha": 0.9, NEW: 0.1}, 0.9)
    classifier.add_choice("route", NEW, {NEW: 0.9, HERE: 0.1}, 0.9)

    # ACT
    result = alpha.ingest("Revenue grew.")

    # ASSERT
    assert (result.folder, result.created_folders) == ("/alpha/revenue", ["/alpha/revenue"])


@pytest.mark.parametrize("opens", ["new", "create"])
def test_rebuild_makes_no_folder_whichever_step_asks_for_one(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier, opens: str
) -> None:
    # ARRANGE: a custom route step that opens a folder under /alpha, by name_folder or by create
    def route(ctx: Any, root: Folder, cand: Any) -> Route:
        folder = root.find("/alpha")
        assert folder is not None
        return Route(folder, True, [folder]) if opens == "new" else Route(folder, False, [folder], [("revenue", "Revenue reports.")])

    steps: Steps = replace(CROW, name="opens", route=route)
    wiki = make_wiki(bundle, llm, classifier)
    wiki.steps = steps
    add_folder(wiki, "/", "alpha", "Alpha things.")
    llm.add(SUMMARIZE, draft("Q3 revenue"))

    # ACT
    result = rebuilt(wiki)

    # ASSERT
    assert llm.ops == [SUMMARIZE]
    assert (result.folder, result.created_folders) == ("/alpha", [])
    assert [f.label for f in wiki.store.load().walk()] == ["/", "/alpha"]


def test_rebuild_with_a_route_at_the_root_files_in_the_likeliest_top_level_folder(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE: a step that opens a top-level folder
    wiki = make_wiki(bundle, llm, classifier)
    wiki.steps = replace(CROW, name="opens", route=lambda ctx, root, cand: Route(root, True, [root]))
    add_folder(wiki, "/", "alpha", "Alpha things.")
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    classifier.add_choice("route", "/alpha", {"/alpha": 0.8, NONE: 0.2}, 0.8)

    # ACT
    result = rebuilt(wiki)

    # ASSERT
    assert (result.folder, result.created_folders, llm.ops) == ("/alpha", [], [SUMMARIZE])


def test_keep_folders_leaves_an_ingest_and_a_tree_without_folders_alone(alpha: Wiki) -> None:
    # ARRANGE
    ctx = alpha.librarian().ctx
    root = alpha.store.load()
    opening = Route(root, True, [root])

    # ACT, ASSERT: not a rebuild
    assert steps_crow.keep_folders(ctx, root, _cand(), opening) is opening

    # ACT, ASSERT: a rebuild of a tree with no folder has nowhere else to file
    empty = make_wiki(alpha.cfg.bundle.parent / "empty", alpha.llm, alpha.classifier)  # type: ignore[arg-type]
    fixed = empty.librarian(fixed=True).ctx
    assert steps_crow.keep_folders(fixed, empty.store.load(), _cand(), opening) is opening


def _cand() -> Any:
    from llmw2.agents.data import Candidate

    return Candidate("A note", "", [], "text")


# -- no split ---------------------------------------------------------------------------------


def _full(wiki: Wiki, classifier: FakeClassifier) -> None:
    add_folder(wiki, "/", "sales", "Sales documents.")
    for n in range(11):
        add_note(wiki, "/sales", f"Note {n:02d}")
    classifier.add_choice("route", "/sales", {"/sales": 0.9, NONE: 0.1}, 0.9)


@pytest.mark.parametrize(("plan", "splits"), [(True, 1), (False, 0)])
def test_a_single_document_splits_its_folder_unless_the_batch_is_a_rebuild(
    crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier, plan: bool, splits: int
) -> None:
    # ARRANGE: the twelfth note of /sales
    _full(crow_wiki, classifier)
    llm.add(SUMMARIZE, draft("Twelfth"))
    llm.add(SPLIT, {"folders": []})

    # ACT
    report = crow_wiki.ingest_many([ITEM], plan=plan)

    # ASSERT
    assert report.stopped is None and llm.ops.count(SPLIT) == splits


# -- the prompts of the plan --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "values", "rule"),
    [
        (
            "librarian/plan",
            {"documents": "- x: y", "top": 10, "depth": 3},
            "A single client, supplier, project or product is never a top-level folder, however many documents are about it: "
            "it is a subfolder of the broad area it belongs to.",
        ),
        (
            "librarian/plan_more",
            {"tree": "- /a: b; 1 notes", "documents": "- x: y", "roots": PLAN_ROOTS_OPEN, "depth": 3},
            "never a single client, supplier, project or product: that is a subfolder of the broad area it belongs to",
        ),
    ],
)
def test_the_plan_prompts_keep_a_single_client_or_project_out_of_the_roots(name: str, values: dict[str, Any], rule: str) -> None:
    assert rule in " ".join(Prompts().render(name, **values).split())
