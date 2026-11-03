# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A batch that stops takes back the empty folders its plans made: a resumed wiki is the one a run never stopped would make."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from llmw2 import Item, Origin, Wiki, sync
from tests.wiki.test_crow_plan import PlanLLM, add_folder, folders_json, labels, log_lines, make_crow
from tests.wiki.test_ingest_many import TIMESTAMP
from tests.wiki.test_sources import Fake, topic_files
from tests.wiki.test_spending_cap import pages

IDS = [f"d{n}" for n in range(6)]
PLAN = folders_json(("/red", "Red things."), ("/red/deep", "Deeper red things."), ("/blue", "Blue things."), ("/green", "Green things."))


def crow(bundle: Path) -> tuple[Wiki, PlanLLM]:
    wiki, llm = make_crow(bundle)
    llm.cost = 1.0
    llm.plan_reply = PLAN
    return wiki, llm


def run(wiki: Wiki, max_cost: float | None) -> None:
    sync(wiki, Fake(*pages(IDS, 6), files=topic_files(*IDS)), max_cost=max_cost)


def bundle_files(wiki: Wiki) -> dict[str, str]:
    """Every note and index of the bundle, but the log and the raw copies, with their dates and ids left out."""
    root = wiki.cfg.bundle
    skip = {"log.md"}
    return {
        p.relative_to(root).as_posix(): TIMESTAMP.sub("", re.sub(r"^id: '?\w+'?$", "", p.read_text(encoding="utf-8"), flags=re.M))
        for p in sorted(root.rglob("*.md"))
        if p.name not in skip and not p.relative_to(root).parts[0].startswith((".", "raw"))
    }


def empty(wiki: Wiki) -> list[str]:
    return [f.label for f in wiki.store.load().walk() if not f.is_root and not f.notes and not f.subfolders]


@pytest.mark.parametrize("stops", [1, 3])
def test_syncs_stopped_before_the_first_document_leave_the_wiki_a_run_never_stopped_would_make(tmp_path: Path, stops: int) -> None:
    whole, _ = crow(tmp_path / "whole")
    run(whole, None)
    resumed, llm = crow(tmp_path / "resumed")

    for _ in range(stops):
        run(resumed, 1.0)  # the plan costs the cap: nothing is filed
        assert empty(resumed) == [] and labels(resumed) == [] and not list(resumed.store.load().all_notes())
    run(resumed, None)

    assert bundle_files(resumed) == bundle_files(whole)
    assert labels(resumed) == labels(whole) and resumed.check() == []
    removed = log_lines(resumed, "Removed folder")
    # the folders no document went to (3 of 4) are taken back by the run that finishes too
    assert len(removed) == 4 * stops + 3 and len(log_lines(resumed, "Created folder")) == 4 * (stops + 1)
    assert len(log_lines(whole, "Created folder")) == 4 and len(log_lines(whole, "Removed folder")) == 3
    assert [op for op in llm.ops if op == "librarian/plan"] == ["librarian/plan"] * (stops + 1)


def test_a_batch_stopped_while_filing_keeps_the_folders_with_notes_and_removes_the_empty_ones_of_its_plans(bundle: Path) -> None:
    wiki, _ = crow(bundle)
    run(wiki, 5.0)

    filed = list(wiki.store.load().all_notes())
    assert 0 < len(filed) < len(IDS)
    assert "/red" in labels(wiki) and empty(wiki) == []
    assert "/blue" not in labels(wiki) and "/red/deep" not in labels(wiki)
    assert wiki.check() == []


def test_folders_that_were_there_before_the_batch_stay_empty_or_not(bundle: Path) -> None:
    wiki, llm = crow(bundle)
    add_folder(wiki, "/", "green", "Green things.")  # by hand, before the batch
    llm.plan_more_reply = folders_json(("/green", "Green things."), ("/blue", "Blue things."), ("/green/deep", "Deep green."))

    run(wiki, 1.0)

    assert labels(wiki) == ["/green"]  # the folders of the plan_more go, the one that was there stays
    assert len(log_lines(wiki, "Removed folder")) == 2


def test_a_batch_with_plan_groups_that_stops_keeps_only_folders_with_notes(bundle: Path) -> None:
    wiki, llm = make_crow(bundle, plan="groups")
    llm.cost = 1.0
    texts = {
        "roast-1": "Aurora roast profile: the Aurora espresso blend is roasted to 212 degrees, first crack at nine minutes.",
        "roast-2": "Aurora roast adjustments after cupping: the Aurora espresso blend roasted darker, first crack later.",
        "pay-1": "Payroll calendar: salaries are paid on the 27th, payroll closes on the 20th, payslips by email.",
        "pay-2": "Payroll changes: the payroll closing day moves to the 18th, salaries paid on the 27th, payslips unchanged.",
    }
    items = [Item(f"{t}.md", data=f"topic: {t}\n{text}".encode(), origin=Origin("test", "acct", t)) for t, text in texts.items()]

    with wiki.tracker.limit(1.5):
        report = wiki.ingest_many(items)

    assert report.stopped and "spending cap" in report.stopped
    assert 0 < len(list(wiki.store.load().all_notes())) < 4
    assert len(labels(wiki)) == 1 and empty(wiki) == []  # the group planned for the documents not filed is taken back
    assert len(log_lines(wiki, "Created folder")) == 2 and len(log_lines(wiki, "Removed folder")) == 1
