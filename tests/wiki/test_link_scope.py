# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`link_scope` "root": the See-also links code adds stay inside a top-level folder, and a note that changes area loses the ones
that no longer fit."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents.linking import rare_scores
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import CLASSIC, decision_hash
from llmw2.bundle.tree import area_of, drop_see_also, within_area
from llmw2.models.usage import UsageTracker
from tests.wiki.areas import STEPS, file, linked, make
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_rare_value_links import find, make_root


def wiki_of(bundle: Path, scope: str) -> tuple[Wiki, str, str, str]:
    """Two notes in /red and one in /blue, each linked to those before it that its scope allows."""
    wiki = make(bundle, link_scope=scope)
    r1 = file(wiki, "/red", "Red one")
    r2 = file(wiki, "/red", "Red two")
    b1 = file(wiki, "/blue", "Blue one")
    return wiki, r1, r2, b1


# -- the candidates ---------------------------------------------------------------------------


def test_by_default_links_cross_areas(bundle: Path) -> None:
    wiki, r1, r2, b1 = wiki_of(bundle, "wiki")

    assert linked(wiki, b1) == [r1, r2]
    assert linked(wiki, r1) == [b1, r2]


def test_with_scope_root_links_stay_in_the_area(bundle: Path) -> None:
    wiki, r1, r2, b1 = wiki_of(bundle, "root")

    assert linked(wiki, b1) == []
    assert linked(wiki, r1) == [r2] and linked(wiki, r2) == [r1]


def test_the_scope_is_read_from_the_environment() -> None:
    cfg = WikiConfig.from_env({"OKF_LINK_SCOPE": "root"}, bundle=Path("w"))

    assert cfg.link_scope == "root" and WikiConfig(bundle=Path("w")).link_scope == "wiki"


def test_a_note_in_a_subfolder_is_in_the_area_of_its_top_level_folder(bundle: Path) -> None:
    wiki = make(bundle, link_scope="root")
    wiki.create_folder("/red", "deep", "Deeper red things.")
    r1 = file(wiki, "/red", "Red one")
    r2 = file(wiki, "/red/deep", "Red deep")
    b1 = file(wiki, "/blue", "Blue one")

    assert linked(wiki, r2) == [r1] and linked(wiki, b1) == []


def test_rare_value_candidates_are_chosen_among_the_notes_of_the_area() -> None:
    code = "ZK-4471"
    bodies = {"a/x.md": f"Code {code}.", "a/y.md": f"Code {code}.", "b/z.md": f"Code {code}."}
    root = make_root(bodies | {f"m/f{i}.md": "Nothing." for i in range(20)})
    note = find(root, "a/x.md")

    everywhere = {n.rel for n, _ in rare_scores(root, note, set())}
    near = {n.rel for n, _ in rare_scores(root, note, set(), lambda other: within_area(note, other))}

    assert everywhere == {"a/y.md", "b/z.md"} and near == {"a/y.md"}


def test_an_area_closed_to_automatic_filing_gives_and_gets_no_link(bundle: Path) -> None:
    wiki = make(bundle, link_scope="root")
    wiki.set_routable("/red", False)
    r1 = file(wiki, "/red", "Red one")
    r2 = file(wiki, "/red", "Red two")
    file(wiki, "/blue", "Blue one")

    assert linked(wiki, r1) == [] and linked(wiki, r2) == []


def test_within_area_compares_the_top_level_folder() -> None:
    root = make_root({"a/x.md": ".", "a/s/y.md": ".", "b/z.md": "."})
    x, y, z = find(root, "a/x.md"), find(root, "a/s/y.md"), find(root, "b/z.md")

    assert [area_of(n.rel) for n in (x, y, z)] == ["a", "a", "b"]
    assert within_area(x, y) and not within_area(x, z)


# -- a note that changes area -----------------------------------------------------------------


def test_moving_a_note_to_another_area_cuts_its_links_from_both_sides(bundle: Path) -> None:
    wiki, r1, r2, _ = wiki_of(bundle, "root")

    moved = wiki.move_note(r1, "/blue")

    assert linked(wiki, moved.rel) == [] and linked(wiki, r2) == []


def test_moving_a_note_inside_its_area_keeps_its_links(bundle: Path) -> None:
    wiki, r1, r2, _ = wiki_of(bundle, "root")
    wiki.create_folder("/red", "deep", "Deeper red things.")

    moved = wiki.move_note(r1, "/red/deep")

    assert linked(wiki, moved.rel) == [r2] and linked(wiki, r2) == [moved.rel]


def test_moving_a_note_keeps_its_links_by_default(bundle: Path) -> None:
    wiki, r1, r2, _ = wiki_of(bundle, "wiki")

    moved = wiki.move_note(r1, "/blue")

    assert r2 in linked(wiki, moved.rel) and moved.rel in linked(wiki, r2)


def test_a_one_way_link_to_a_moved_note_is_cut_too(bundle: Path) -> None:
    wiki, r1, r2, _ = wiki_of(bundle, "root")
    mine = wiki.store.read(r1)
    wiki.store._edit(mine, lambda n: setattr(n, "body", drop_see_also(n.body, n.rel, lambda target: True)) or True)  # no back link
    assert linked(wiki, r1) == [] and linked(wiki, r2) == [r1]

    moved = wiki.move_note(r1, "/blue")

    assert linked(wiki, r2) == [] and linked(wiki, moved.rel) == []


def test_moving_a_folder_to_another_area_cuts_the_links_that_no_longer_fit(bundle: Path) -> None:
    wiki = make(bundle, link_scope="root")
    wiki.create_folder("/red", "deep", "Deeper red things.")
    r1 = file(wiki, "/red", "Red one")
    file(wiki, "/red/deep", "Red deep")
    file(wiki, "/red/deep", "Red three")
    assert linked(wiki, r1) == ["red/deep/red-deep.md", "red/deep/red-three.md"]

    folder = wiki.store.move_folder("red/deep", "blue/deep")

    new = sorted(n.rel for n in folder.all_notes())
    assert new == ["blue/deep/red-deep.md", "blue/deep/red-three.md"]
    assert linked(wiki, r1) == []
    assert linked(wiki, new[0]) == [new[1]] and linked(wiki, new[1]) == [new[0]]


def test_confirming_an_area_cuts_the_links_of_the_notes_it_takes(bundle: Path) -> None:
    wiki, r1, r2, _ = wiki_of(bundle, "root")
    proposal = wiki.propose_area("green", "Green things.", [r1])

    _, moved = wiki.confirm_proposal(proposal.id)

    assert linked(wiki, moved[0]) == [] and linked(wiki, r2) == []


def test_merging_areas_cuts_what_does_not_fit_and_keeps_what_now_does(bundle: Path) -> None:
    wiki = make(bundle, folders=("red", "blue", "green"), link_scope="wiki")
    file(wiki, "/red", "Red one")
    file(wiki, "/blue", "Blue one")
    file(wiki, "/green", "Green one")  # linked to both
    narrow = Wiki(WikiConfig(bundle=bundle, mode="classic", link_scope="root"), llm=FakeLLM(UsageTracker()), steps=STEPS)

    narrow.merge_roots("/red", "/blue")

    assert sorted(n.rel for n in narrow.tree().all_notes()) == ["blue/blue-one.md", "blue/red-one.md", "green/green-one.md"]
    assert linked(narrow, "blue/red-one.md") == ["blue/blue-one.md"]
    assert linked(narrow, "green/green-one.md") == ["blue/blue-one.md"]  # a link between notes that did not move is left


def test_renaming_an_area_keeps_its_links(bundle: Path) -> None:
    wiki, _, _, _ = wiki_of(bundle, "root")

    wiki.rename_root("/red", "crimson")

    assert linked(wiki, "crimson/red-one.md") == ["crimson/red-two.md"]


@pytest.mark.parametrize("scope", ["wiki", "root"])
def test_the_scope_changes_the_decision_hash_only_when_set(scope: str, bundle: Path) -> None:
    base = decision_hash(CLASSIC, WikiConfig(bundle=bundle), Prompts(None))
    other = decision_hash(CLASSIC, WikiConfig(bundle=bundle, link_scope=scope), Prompts(None))  # type: ignore[arg-type]

    assert (base == other) is (scope == "wiki")
