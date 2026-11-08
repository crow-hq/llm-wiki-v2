# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`overflow_folder`: where a note goes when routing cannot open a new top-level folder, instead of the likeliest existing one."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from llmw2 import Item, Wiki, WikiConfig
from llmw2.batch import deviation_line, deviations
from llmw2.bundle.tree import LOG
from tests.wiki.test_crow_plan import filed_all
from tests.wiki.test_own_roots import make, own

RED, GREEN = "topic: red-1\nRed things happen.", "topic: green-1\nGreen things happen."


def capped(bundle: Path, **cfg: str) -> Wiki:
    """A CROW wiki whose two top-level folders are the limit: /red, and /inbox, closed to automatic filing."""
    wiki, _ = make(bundle, max_top_folders=2, **cfg)
    own(wiki, "red")
    wiki.create_folder("/", "inbox", "What the wiki could not place.", routable=False)
    return wiki


def test_a_note_that_cannot_open_its_folder_goes_to_the_declared_one_and_is_marked(bundle: Path) -> None:
    wiki = capped(bundle, overflow_folder="/inbox")

    result = wiki.ingest(GREEN, title="Green")

    assert result.folder == "/inbox" and result.overflow == {"declared": 1.0}
    assert wiki.store.read(result.note).frontmatter["overflow"]["declared"] == 1.0
    line = next(x for x in (bundle / LOG).read_text(encoding="utf-8").splitlines() if "Deviated note" in x)
    assert "filed in /inbox (the declared overflow folder)" in line and "limit of 2" in line


def test_a_note_that_routing_settles_does_not_go_there(bundle: Path) -> None:
    wiki = capped(bundle, overflow_folder="/inbox")

    result = wiki.ingest(RED, title="Red")

    assert result.folder == "/red" and result.overflow is None


def test_the_trace_names_the_folder_and_no_classifier_is_asked_for_it(bundle: Path) -> None:
    wiki = capped(bundle, overflow_folder="inbox")  # with or without the slash

    result = wiki.ingest(GREEN, title="Green")

    assert result.folder == "/inbox"
    assert ("route", "code", "/inbox (Here)") in [(d.step, d.decider, d.choice) for d in result.decisions]


def test_without_the_setting_the_likeliest_open_root_takes_the_note_as_before(bundle: Path) -> None:
    wiki = capped(bundle)

    result = wiki.ingest(GREEN, title="Green")

    assert result.folder == "/red" and set(result.overflow or {}) <= {"p_none", "p_root"}


def test_a_folder_that_does_not_exist_warns_and_the_note_goes_where_it_would_have(
    bundle: Path, caplog: pytest.LogCaptureFixture
) -> None:
    wiki = capped(bundle, overflow_folder="/missing")

    with caplog.at_level(logging.WARNING):
        result = wiki.ingest(GREEN, title="Green")

    assert result.folder == "/red" and "overflow_folder /missing is not a folder of the wiki" in caplog.text


def test_with_room_for_a_new_folder_the_setting_changes_nothing(bundle: Path) -> None:
    wiki, _ = make(bundle, max_top_folders=3, overflow_folder="/inbox")
    wiki.create_folder("/", "inbox", "What the wiki could not place.", routable=False)

    result = wiki.ingest(GREEN, title="Green")

    assert result.folder == "/green" and result.overflow is None


def test_a_folder_below_a_root_can_be_the_destination(bundle: Path) -> None:
    wiki, _ = make(bundle, max_top_folders=1, overflow_folder="/red/inbox")
    own(wiki, "red")
    wiki.create_folder("/red", "inbox", "Unplaced.")

    result = wiki.ingest(GREEN, title="Green")

    assert result.folder == "/red/inbox" and result.overflow == {"declared": 1.0}


def test_the_batch_report_says_the_destination_was_declared(bundle: Path) -> None:
    wiki = capped(bundle, overflow_folder="/inbox")

    report = wiki.ingest_many([Item("green.md", data=GREEN.encode())])

    assert filed_all(report, 1)
    [one] = report.deviations
    assert one["declared"] is True and one["root"] == "/inbox"
    assert deviation_line(one) == f"{one['note']} -> /inbox (the declared overflow folder)"
    assert deviations([report.results[0][1]]) == [one]  # type: ignore[list-item]


def test_a_rebuild_sends_the_documents_no_folder_can_take_to_it_too(bundle: Path) -> None:
    wiki = capped(bundle, overflow_folder="/inbox")
    items = [Item("red.md", data=RED.encode()), Item("green.md", data=GREEN.encode())]
    wiki.ingest_many(items)

    report = wiki.rebuild(items)

    assert filed_all(report, 2) and [d["root"] for d in report.deviations] == ["/inbox"]


def test_the_setting_is_read_from_the_environment() -> None:
    cfg = WikiConfig.from_env({"OKF_OVERFLOW_FOLDER": "/inbox"}, bundle=Path("w"))

    assert cfg.overflow_folder == "/inbox" and WikiConfig(bundle=Path("w")).overflow_folder is None
