# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A folder closed to automatic filing (`routable` false) is no option of routing, plans, consolidation or the audit; people and
code can still file there."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from llmw2 import Wiki
from llmw2.agents import steps_crow
from llmw2.agents.data import Candidate
from llmw2.agents.steps import StepContext
from llmw2.bundle.tree import CLOSED, Note
from llmw2.errors import InputError, StepError
from tests.wiki import areas
from tests.wiki.test_crow_plan import DOCS
from tests.wiki.test_own_roots import make, own
from tests.wiki.test_plan_groups import PAY, ROAST

RED, GREEN = "topic: red-1\nRed things happen.", "topic: green-1\nGreen things happen."


def routed_options(wiki: Wiki) -> list[str]:
    """The folders the flat routing Choice was offered (its first question)."""
    classifier = wiki.classifier
    assert classifier is not None
    call = next(c for c in classifier.calls if c[0] == "route")  # type: ignore[attr-defined]
    return [o for q in call[2].values() for o in q["criteria"]]


# -- the attribute ----------------------------------------------------------------------------


def test_a_folder_is_open_by_default_and_closes_and_opens_again(bundle: Path) -> None:
    wiki, _ = make(bundle)
    own(wiki, "red")
    assert wiki.tree().find("/red").routable is True  # type: ignore[union-attr]

    wiki.set_routable("/red", False)
    assert wiki.tree().find("/red").routable is False  # type: ignore[union-attr]
    assert (bundle / "red" / CLOSED).is_file()

    wiki.set_routable("/red", True)
    assert wiki.tree().find("/red").routable is True  # type: ignore[union-attr]
    assert not (bundle / "red" / CLOSED).exists()


def test_a_folder_can_be_created_closed_and_the_log_says_so(bundle: Path) -> None:
    wiki, _ = make(bundle)

    folder = wiki.create_folder("/", "private", "Kept apart.", routable=False)

    assert folder.routable is False and wiki.tree().find("/private").routable is False  # type: ignore[union-attr]
    assert "closed to automatic filing" in (bundle / "log.md").read_text(encoding="utf-8")
    assert wiki.check() == []  # the layout is still OKF's


def test_the_attribute_is_kept_by_a_new_wiki_in_a_session_and_with_the_folder_moved(bundle: Path) -> None:
    wiki, _ = make(bundle)
    wiki.create_folder("/", "private", "Kept apart.", routable=False)

    wiki.rename_root("/private", "vault")
    with wiki.store.session():
        assert wiki.store.load().find("/vault").routable is False  # type: ignore[union-attr]
    wiki.set_routable("/vault", True)
    with wiki.store.session():
        assert wiki.store.load().find("/vault").routable is True  # type: ignore[union-attr]


def test_a_folder_below_a_closed_one_is_closed_to_filing_too(bundle: Path) -> None:
    wiki, _ = make(bundle)
    wiki.create_folder("/", "private", "Kept apart.", routable=False)
    wiki.create_folder("/private", "deep", "Deeper.")

    tree = wiki.tree()

    assert tree.find("/private/deep").routable is True  # type: ignore[union-attr]
    assert tree.find("/private/deep").is_routable is False  # type: ignore[union-attr]


def test_the_root_cannot_be_closed_and_a_missing_folder_is_refused(bundle: Path) -> None:
    wiki, _ = make(bundle)

    with pytest.raises(InputError):
        wiki.set_routable("/", False)
    with pytest.raises(InputError):
        wiki.set_routable("/nowhere", False)


def test_merging_a_closed_folder_does_not_stop_on_its_marker(bundle: Path) -> None:
    wiki, _ = make(bundle)
    own(wiki, "red")
    wiki.create_folder("/", "private", "Kept apart.", routable=False)

    wiki.merge_roots("/private", "/red")

    assert wiki.tree().find("/private") is None


# -- routing ----------------------------------------------------------------------------------


def test_the_routing_choice_does_not_offer_a_closed_folder_and_the_note_goes_elsewhere(bundle: Path) -> None:
    wiki, _ = make(bundle, max_top_folders=3)
    own(wiki, "red", "blue")
    wiki.set_routable("/red", False)

    result = wiki.ingest(RED, title="Red")

    assert "/red" not in routed_options(wiki) and "/blue" in routed_options(wiki)
    assert result.folder == "/red-2"  # a new folder named "red" would have been the closed one


def test_a_subfolder_of_a_closed_folder_is_not_offered_either(bundle: Path) -> None:
    wiki, _ = make(bundle, max_top_folders=3)
    own(wiki, "blue")
    wiki.create_folder("/", "red", "Red things.", routable=False)
    wiki.create_folder("/red", "deep", "Deeper red things.")

    wiki.ingest(RED, title="Red")

    assert not any(o.startswith("/red") for o in routed_options(wiki))


def test_an_open_wiki_routes_as_before(bundle: Path) -> None:
    wiki, _ = make(bundle, max_top_folders=3)
    own(wiki, "red", "blue")

    result = wiki.ingest(RED, title="Red")

    assert result.folder == "/red" and "/red" in routed_options(wiki)


def test_with_every_top_level_folder_closed_and_no_room_the_ingest_stops_and_writes_nothing(bundle: Path) -> None:
    wiki, _ = make(bundle, max_top_folders=1)
    wiki.create_folder("/", "red", "Red things.", routable=False)

    with pytest.raises(StepError, match="overflow_folder"):
        wiki.ingest(GREEN, title="Green")

    assert wiki.tree().all_notes() == []


def test_the_likeliest_root_is_never_a_closed_one(bundle: Path) -> None:
    wiki, _ = make(bundle, max_top_folders=2)
    own(wiki, "red", "blue")
    wiki.set_routable("/blue", False)
    root = wiki.tree()
    librarian = wiki.librarian()
    librarian.ctx.source = None

    cand = steps_crow.Candidate("About green-1", "All on green-1.", [], "topic: green-1")
    folder, _ = steps_crow._likeliest_root(librarian.ctx, root, cand)

    assert folder.name == "red"


# -- planning, consolidation, audit -----------------------------------------------------------


def test_a_plan_is_not_shown_a_closed_folder_and_makes_nothing_under_it(bundle: Path) -> None:
    wiki, llm = make(bundle, max_top_folders=3)
    own(wiki, "red")
    wiki.create_folder("/", "private", "Kept apart.", routable=False)
    llm.plan_more_reply = {"folders": [{"path": "/private/sub", "description": "Under the closed one."}]}

    made = wiki.plan(DOCS)

    prompt = next(c[1][1]["content"] for c in llm.calls if c[0] == "librarian/plan_more")
    assert "/red:" in prompt and "/private" not in prompt
    assert made == [] and wiki.tree().find("/private/sub") is None


def test_plan_groups_never_joins_a_group_to_a_closed_folder(bundle: Path) -> None:
    wiki = areas.make(bundle, folders=("private",))
    areas.file(wiki, "/private", "Roast", ROAST[0])
    documents = [(None, ROAST[1]), (None, ROAST[0]), (None, PAY[0]), (None, PAY[1])]

    wiki.set_routable("/private", False)
    closed = wiki.plan_groups(documents)
    wiki.set_routable("/private", True)
    open_ = wiki.plan_groups(documents)

    assert "/private" not in closed.values()
    assert open_[0] == "/private"  # the same group joins it once it is open


def test_a_note_in_a_closed_folder_is_not_a_candidate_to_merge_into(bundle: Path) -> None:
    wiki = areas.make(bundle)
    areas.file(wiki, "/red", "Red one")
    wiki.set_routable("/red", False)
    seen: list[list[str]] = []

    def match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
        seen.append([n.rel for n in pool])
        return None

    wiki.steps = replace(areas.STEPS, match=match)  # the route offers every folder of the tree as a candidate
    areas.file(wiki, "/blue", "Blue one")

    assert seen == [[]]


def test_the_audit_leaves_the_notes_of_a_closed_folder_where_they_are(bundle: Path) -> None:
    wiki, _ = make(bundle, max_top_folders=3)
    own(wiki, "red", "blue")
    moved = wiki.move_note(wiki.ingest(RED, title="Red").note, "/blue").rel
    assert [p.path for p in wiki.audit_routes()] == [moved]  # the classifier would put it back in /red

    wiki.set_routable("/blue", False)

    assert wiki.audit_routes() == []


def test_a_note_can_still_be_moved_into_a_closed_folder(bundle: Path) -> None:
    wiki, _ = make(bundle, max_top_folders=3)
    own(wiki, "red", "blue")
    wiki.set_routable("/blue", False)
    note = wiki.ingest(RED, title="Red").note

    moved = wiki.move_note(note, "/blue")

    assert moved.rel.startswith("blue/")
