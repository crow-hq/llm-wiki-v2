# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Route.create: a step that already knows the whole folder path has it created level by level, with no name_folder call."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import CLASSIC, Candidate, Route, StepContext, StepError, Steps, Wiki, WikiConfig
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.tree import Folder, Note
from tests.wiki.fakes import FakeLLM


def read(path: Path) -> OKFDocument:
    return OKFDocument.parse(path.read_text(encoding="utf-8"))


def snapshot(bundle: Path) -> dict[str, bytes]:
    """Every file of the bundle, to prove a failed operation wrote nothing."""
    return {str(p.relative_to(bundle)): p.read_bytes() for p in sorted(bundle.rglob("*")) if p.is_file()}


def o_summarize(ctx: StepContext, source: Any) -> Candidate:
    return Candidate(source.title or "Memo", "A memo.", [], source.text)


def never_named(ctx: StepContext, parent: Folder, cand: Candidate) -> tuple[str, str]:
    raise AssertionError("name_folder must not run when the route gives the folders")


def o_match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
    return None


def o_consolidate(ctx: StepContext, note: Note, cand: Candidate) -> bool:
    return False


def o_relate(ctx: StepContext, note: Note, others: list[Note]) -> list[Note]:
    return []


def steps_for(route: Any, name_folder: Any = never_named) -> Steps:
    return Steps(
        name="create",
        summarize=o_summarize,
        route=route,
        name_folder=name_folder,
        match=o_match,
        consolidate=o_consolidate,
        relate=o_relate,
    )


def make(bundle: Path, llm: FakeLLM, steps: Steps, *, max_depth: int = 4, mode: str = "classic") -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode=mode, max_depth=max_depth), llm=llm, steps=steps)
    wiki.init()
    return wiki


def at(path: str, create: list[Any], *, new: bool = False, candidates: bool = False) -> Any:
    """A route step: starts from the folder `path` of the tree the core hands over."""

    def route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
        folder = root.find(path)
        assert folder is not None
        return Route(folder, new, [], create=create)

    return route


def folder_of(wiki: Wiki, path: str) -> Folder:
    folder = wiki.store.load().find(path)
    assert folder is not None, path
    return folder


# -- creation ----------------------------------------------------------------------------------------------------------------


def test_route_create_defaults_to_empty() -> None:
    assert Route(Folder(Path("/x"), ""), False, []).create == []


def test_several_levels_are_created_in_one_ingest_and_the_note_goes_in_the_last(bundle: Path, llm: FakeLLM) -> None:
    create = [("Science", "All science."), ("Chemistry", "Molecules."), ("Organic", "Carbon chemistry.")]
    wiki = make(bundle, llm, steps_for(at("/", create)))

    result = wiki.ingest("Esters.", title="Esters")

    assert result.created_folders == ["/science", "/science/chemistry", "/science/chemistry/organic"]
    assert (result.folder, result.note, result.action) == ("/science/chemistry/organic", "science/chemistry/organic/esters.md", "created")
    assert (bundle / result.note).is_file()
    descriptions = {"/science": "All science.", "/science/chemistry": "Molecules.", "/science/chemistry/organic": "Carbon chemistry."}
    assert {path: folder_of(wiki, path).description for path in descriptions} == descriptions
    assert "* [science](science/index.md) - All science." in read(bundle / "index.md").body.splitlines()
    assert "* [chemistry](chemistry/index.md) - Molecules." in read(bundle / "science" / "index.md").body.splitlines()
    assert wiki.check() == []
    assert llm.calls == []


def test_existing_levels_are_reused_untouched_and_not_listed_as_created(bundle: Path, llm: FakeLLM) -> None:
    wiki = make(bundle, llm, steps_for(at("/", [("Science", "Different words."), ("Chemistry", "Other words."), ("Organic", "Carbon.")])))
    science, _ = wiki.store.create_folder(wiki.store.load().find("/"), "science", "All science.")
    wiki.store.create_folder(science, "chemistry", "Molecules.")
    index_before = (bundle / "science" / "index.md").read_text(encoding="utf-8").split("Organic")[0]

    result = wiki.ingest("Esters.", title="Esters")

    assert result.created_folders == ["/science/chemistry/organic"]  # only the missing level
    assert result.folder == "/science/chemistry/organic"
    assert folder_of(wiki, "/science").description == "All science."
    assert folder_of(wiki, "/science/chemistry").description == "Molecules."
    assert folder_of(wiki, "/science/chemistry/organic").description == "Carbon."
    assert index_before == (bundle / "science" / "index.md").read_text(encoding="utf-8")  # no duplicate entry
    assert read(bundle / "index.md").body.count("science/index.md") == 1


def test_a_name_matching_an_existing_folder_by_slug_is_reused(bundle: Path, llm: FakeLLM) -> None:
    wiki = make(bundle, llm, steps_for(at("/", [("Life  Sciences!", "New words.")])))
    wiki.create_folder("/", "life-sciences", "Original.")

    result = wiki.ingest("Cells.", title="Cells")

    assert result.created_folders == []
    assert (result.folder, result.note) == ("/life-sciences", "life-sciences/cells.md")
    assert folder_of(wiki, "/life-sciences").description == "Original."


def test_all_levels_existing_files_the_note_in_the_deepest_and_creates_nothing(bundle: Path, llm: FakeLLM) -> None:
    wiki = make(bundle, llm, steps_for(at("/", [("a", "A."), ("b", "B.")])))
    wiki.create_folder("/", "a", "A.")
    wiki.create_folder("/a", "b", "B.")

    result = wiki.ingest("Text.", title="Memo")

    assert result.created_folders == []
    assert result.note == "a/b/memo.md"


def test_created_folders_lists_exactly_the_levels_created_in_order(bundle: Path, llm: FakeLLM) -> None:
    wiki = make(bundle, llm, steps_for(at("/", [("a", "A."), ("b", "B."), ("c", "C.")])))
    wiki.create_folder("/", "a", "A.")

    first = wiki.ingest("One.", title="One")
    second = wiki.ingest("Two.", title="Two")

    assert first.created_folders == ["/a/b", "/a/b/c"]
    assert second.created_folders == []
    assert second.folder == "/a/b/c"


def test_name_folder_is_never_called_when_create_is_given(bundle: Path, llm: FakeLLM) -> None:
    calls: list[Any] = []

    def spy(ctx: StepContext, parent: Folder, cand: Candidate) -> tuple[str, str]:
        calls.append(parent)
        return "x", "X."

    # never_named raises: the ingest succeeding is the proof; the spy variant checks it explicitly, also with new False
    make(bundle, llm, steps_for(at("/", [("one", "One.")]), spy)).ingest("Text.", title="Memo")

    assert calls == []


def test_create_starts_from_a_non_root_folder(bundle: Path, llm: FakeLLM) -> None:
    wiki = make(bundle, llm, steps_for(at("/finance", [("Reports", "Yearly reports."), ("Q1", "First quarter.")])))
    wiki.create_folder("/", "finance", "Money.")

    result = wiki.ingest("Numbers.", title="Numbers")

    assert result.created_folders == ["/finance/reports", "/finance/reports/q1"]
    assert result.note == "finance/reports/q1/numbers.md"
    assert folder_of(wiki, "/finance").description == "Money."
    assert wiki.check() == []


def test_create_up_to_max_depth_is_allowed(bundle: Path, llm: FakeLLM) -> None:
    wiki = make(bundle, llm, steps_for(at("/", [("a", "A."), ("b", "B.")])), max_depth=2)

    result = wiki.ingest("Text.", title="Memo")

    assert result.folder == "/a/b"


def test_create_from_a_non_root_folder_up_to_max_depth_is_allowed(bundle: Path, llm: FakeLLM) -> None:
    wiki = make(bundle, llm, steps_for(at("/top", [("a", "A."), ("b", "B."), ("c", "C.")])), max_depth=4)
    wiki.create_folder("/", "top", "Top.")  # depth 1 + 3 levels = 4

    assert wiki.ingest("Text.", title="Memo").folder == "/top/a/b/c"


def test_the_match_pool_is_the_candidates_and_the_route_folder_not_the_new_folders(bundle: Path, llm: FakeLLM) -> None:
    pools: list[list[str]] = []

    def match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
        pools.append([n.rel for n in pool])
        return None

    steps = replace(steps_for(at("/finance", [("fresh", "Fresh.")])), match=match)
    wiki = make(bundle, llm, steps)
    wiki.create_folder("/", "finance", "Money.")
    wiki.store.write_note(
        folder_of(wiki, "/finance"), title="Seed", summary="S.", body="Seed.", tags=[], source={"resource": "/raw/seed.md", "title": "Seed"}
    )

    wiki.ingest("Text.", title="Memo")

    assert pools == [["finance/seed.md"]]


# -- the new flag still works, and create does not break the classic/CROW flows -------------------------------------------


def test_new_without_create_still_asks_name_folder(bundle: Path, llm: FakeLLM) -> None:
    calls: list[str] = []

    def name_folder(ctx: StepContext, parent: Folder, cand: Candidate) -> tuple[str, str]:
        calls.append(parent.label)
        return "Fresh", "Fresh things."

    def route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
        return Route(root, True, [])

    result = make(bundle, llm, steps_for(route, name_folder)).ingest("Text.", title="Memo")

    assert len(calls) == 1
    assert (result.created_folders, result.note) == (["/fresh"], "fresh/memo.md")


def test_not_new_and_no_create_files_in_the_route_folder(bundle: Path, llm: FakeLLM) -> None:
    result = make(bundle, llm, steps_for(at("/", []))).ingest("Text.", title="Memo")

    assert (result.created_folders, result.folder, result.note) == ([], "/", "memo.md")


def test_the_classic_name_folder_is_still_called_for_a_new_folder(bundle: Path, llm: FakeLLM) -> None:
    def route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
        return Route(root, True, [])

    llm.add("librarian/name_folder", {"name": "acme", "description": "Acme things."})
    steps = replace(CLASSIC, summarize=o_summarize, route=route, match=o_match, relate=o_relate)

    result = make(bundle, llm, steps).ingest("Text.", title="Memo")

    assert llm.ops == ["librarian/name_folder"]
    assert (result.created_folders, result.note) == (["/acme"], "acme/memo.md")


# -- checks: StepError("route"), nothing written ---------------------------------------------------------------------------------

BAD = [
    pytest.param("/", [("a", "A.")], True, id="new-with-create"),
    pytest.param("/", "a/b", False, id="create-a-str"),
    pytest.param("/", (("a", "A."),), False, id="create-a-tuple"),
    pytest.param("/", [("a",)], False, id="item-one-element"),
    pytest.param("/", [("a", "b", "c")], False, id="item-three-elements"),
    pytest.param("/", [["a", "A."]], False, id="item-a-list"),
    pytest.param("/", ["a"], False, id="item-a-str"),
    pytest.param("/", [(1, "A.")], False, id="name-not-str"),
    pytest.param("/", [("a", None)], False, id="description-not-str"),
    pytest.param("/", [("a", "A."), ("b", 2)], False, id="second-item-bad"),
    pytest.param("/", [("!!!", "A.")], False, id="name-empty-after-slug"),
    pytest.param("/", [("   ", "A.")], False, id="name-blank"),
    pytest.param("/", [("ok", "A."), ("???", "B.")], False, id="second-name-empty-after-slug"),
    pytest.param("/", [(str(i), "x") for i in range(5)], False, id="deeper-than-max-depth-from-root"),
    pytest.param("/top", [("a", "A."), ("b", "B."), ("c", "C."), ("d", "D.")], False, id="deeper-than-max-depth-from-a-folder"),
]


@pytest.mark.parametrize(("start", "create", "new"), BAD)
def test_a_bad_create_is_a_route_step_error_and_the_wiki_is_left_intact(
    bundle: Path, llm: FakeLLM, start: str, create: Any, new: bool
) -> None:
    wiki = make(bundle, llm, steps_for(at(start, create, new=new)), max_depth=4)
    wiki.create_folder("/", "top", "Top.")
    before = snapshot(bundle)

    with pytest.raises(StepError) as raised:
        wiki.ingest("Some text.", title="Memo")

    assert raised.value.step == "route" and "route:" in str(raised.value)
    assert snapshot(bundle) == before  # no folder, note, raw copy, index change or log entry
    assert llm.calls == []


def test_depth_error_is_not_a_truncation(bundle: Path, llm: FakeLLM) -> None:
    wiki = make(bundle, llm, steps_for(at("/", [("a", "A."), ("b", "B."), ("c", "C.")])), max_depth=2)
    before = snapshot(bundle)

    with pytest.raises(StepError) as raised:
        wiki.ingest("Text.", title="Memo")

    assert raised.value.step == "route"
    assert snapshot(bundle) == before
    assert not (bundle / "a").exists()
