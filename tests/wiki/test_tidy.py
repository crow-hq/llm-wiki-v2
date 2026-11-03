# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The tidy-up at the end of a finished CROW batch: a planned folder with one note gives it to the folder above, a planned
top-level folder that only wraps one planned subfolder takes its contents; nothing else is touched."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents.librarian import IngestResult
from llmw2.batch import BatchReport
from llmw2.bundle.tree import Folder
from llmw2.models.usage import UsageTracker
from llmw2.wiki import _follow
from tests.wiki.fakes import ContentLLM, FakeClassifier
from tests.wiki.test_crow_plan import add_folder, add_note, folders_json, item, labels, log_lines, make_crow, note_rels


def planned(wiki: Wiki, *rels: str) -> list[Folder]:
    root = wiki.store.load()
    found = [root.find(r) for r in rels]
    assert all(f is not None for f in found)
    return [f for f in found if f is not None]


def tidy(wiki: Wiki, *rels: str) -> None:
    wiki._tidy(planned(wiki, *rels))


@pytest.fixture
def crow(bundle: Path) -> Wiki:
    return make_crow(bundle)[0]


def test_a_planned_folder_with_one_note_gives_the_note_to_the_folder_above_and_goes(crow: Wiki) -> None:
    add_folder(crow, "/", "sales", "Sales.")
    add_folder(crow, "/sales", "emea", "Emea.")
    add_note(crow, "/sales", "Deal one")
    add_note(crow, "/sales", "Deal two")
    add_note(crow, "/sales/emea", "Emea deal")

    tidy(crow, "sales", "sales/emea")

    assert labels(crow) == ["/sales"]
    assert len(note_rels(crow)) == 3 and all(r.startswith("sales/") and r.count("/") == 1 for r in note_rels(crow))
    assert len(log_lines(crow, "Moved")) == 1 and "emea/emea-deal.md" in log_lines(crow, "Moved")[0]
    assert len(log_lines(crow, "Removed folder")) == 1 and "/sales/emea" in log_lines(crow, "Removed folder")[0]
    assert crow.check() == []


def test_the_links_of_other_notes_to_the_note_moved_follow_it(crow: Wiki) -> None:
    add_folder(crow, "/", "sales", "Sales.")
    add_folder(crow, "/sales", "emea", "Emea.")
    other = add_note(crow, "/sales", "Deal one")
    alone = add_note(crow, "/sales/emea", "Emea deal")
    crow.store.link(other, alone)
    assert "emea/emea-deal.md" in crow.store.load().find("sales").notes[0].body

    tidy(crow, "sales/emea")

    after = {n.title: n for n in crow.store.load().all_notes()}
    assert after["Emea deal"].rel == "sales/emea-deal.md"
    assert "emea/emea-deal.md" not in after["Deal one"].body and "emea-deal.md" in after["Deal one"].body
    assert crow.check() == []


def test_a_wrapper_top_level_folder_takes_the_notes_and_subfolders_of_its_only_subfolder(crow: Wiki) -> None:
    add_folder(crow, "/", "infra", "Infrastructure.")
    add_folder(crow, "/infra", "demo", "Demo systems.")
    add_folder(crow, "/infra/demo", "labs", "Labs.")
    add_note(crow, "/infra/demo", "Demo one")
    add_note(crow, "/infra/demo", "Demo two")
    add_note(crow, "/infra/demo/labs", "Lab one")
    add_note(crow, "/infra/demo/labs", "Lab two")

    tidy(crow, "infra", "infra/demo", "infra/demo/labs")

    assert labels(crow) == ["/infra", "/infra/labs"]
    assert crow.store.load().find("infra").description == "Infrastructure."
    assert sorted(n.rel.rpartition("/")[0] for n in crow.store.load().all_notes()) == ["infra", "infra", "infra/labs", "infra/labs"]
    assert len(log_lines(crow, "Moved")) == 2 and len(log_lines(crow, "Moved folder")) == 1
    assert "/infra/demo" in log_lines(crow, "Removed folder")[0]
    assert crow.check() == []


def test_a_wrapper_whose_subfolder_is_not_planned_stays(crow: Wiki) -> None:
    add_folder(crow, "/", "infra", "Infrastructure.")
    add_folder(crow, "/infra", "demo", "Demo systems.")
    add_note(crow, "/infra/demo", "Demo one")
    add_note(crow, "/infra/demo", "Demo two")

    tidy(crow, "infra")

    assert labels(crow) == ["/infra", "/infra/demo"]


def test_a_folder_made_before_the_batch_stays(crow: Wiki) -> None:
    add_folder(crow, "/", "sales", "Sales.")
    add_folder(crow, "/sales", "emea", "Emea.")
    add_folder(crow, "/sales", "apac", "Apac.")
    add_note(crow, "/sales/emea", "Emea deal")
    add_note(crow, "/sales/apac", "Apac deal")

    tidy(crow, "sales/apac")  # only apac is in the plans of the batch

    assert labels(crow) == ["/sales", "/sales/emea"]
    assert sorted(note_rels(crow)) == ["sales/apac-deal.md", "sales/emea/emea-deal.md"]


def test_a_folder_the_routing_made_is_not_in_the_plans_and_stays(bundle: Path) -> None:
    wiki, llm = make_crow(bundle)
    llm.plan_reply = folders_json(("/red", "Red things."))
    wiki.ingest_many([item(n) for n in range(3)])
    add_folder(wiki, "/red", "routed", "Made by routing.")
    add_note(wiki, "/red/routed", "Routed")

    tidy(wiki, "red")

    assert "/red/routed" in labels(wiki)


def test_a_top_level_folder_with_one_note_stays(crow: Wiki) -> None:
    add_folder(crow, "/", "sales", "Sales.")
    add_note(crow, "/sales", "Deal one")

    tidy(crow, "sales")

    assert labels(crow) == ["/sales"] and note_rels(crow) == ["sales/deal-one.md"]


def test_a_folder_with_two_notes_stays(crow: Wiki) -> None:
    add_folder(crow, "/", "sales", "Sales.")
    add_folder(crow, "/sales", "emea", "Emea.")
    add_note(crow, "/sales", "Deal one")
    add_note(crow, "/sales/emea", "Emea deal")
    add_note(crow, "/sales/emea", "Emea plan")

    tidy(crow, "sales", "sales/emea")

    assert labels(crow) == ["/sales", "/sales/emea"] and log_lines(crow, "Moved") == []


def test_a_folder_with_one_note_and_a_subfolder_stays(crow: Wiki) -> None:
    add_folder(crow, "/", "sales", "Sales.")
    add_folder(crow, "/sales", "emea", "Emea.")
    add_folder(crow, "/sales/emea", "spain", "Spain.")
    add_note(crow, "/sales", "Deal one")
    add_note(crow, "/sales/emea", "Emea deal")
    add_note(crow, "/sales/emea/spain", "Spain deal")
    add_note(crow, "/sales/emea/spain", "Spain plan")

    tidy(crow, "sales", "sales/emea", "sales/emea/spain")

    assert labels(crow) == ["/sales", "/sales/emea", "/sales/emea/spain"]


def test_a_chain_goes_up_from_the_deepest_folder(crow: Wiki) -> None:
    add_folder(crow, "/", "sales", "Sales.")
    add_note(crow, "/sales", "Deal one")
    add_note(crow, "/sales", "Deal two")
    add_folder(crow, "/sales", "emea", "Emea.")
    add_folder(crow, "/sales/emea", "spain", "Spain.")
    add_note(crow, "/sales/emea/spain", "Spain deal")

    tidy(crow, "sales", "sales/emea", "sales/emea/spain")

    assert labels(crow) == ["/sales"] and all(r.startswith("sales/") and r.count("/") == 1 for r in note_rels(crow))
    assert len(note_rels(crow)) == 3 and crow.check() == []


def test_a_finished_batch_tidies_its_plans(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki, llm = make_crow(bundle)
    llm.plan_reply = folders_json(("/red", "Red things."))
    seen: list[list[str]] = []
    monkeypatch.setattr(Wiki, "_tidy", lambda self, made: seen.append([f.rel for f in made]) or [])

    wiki.ingest_many([item(n) for n in range(3)])

    assert seen == [["red"]]


def test_a_stopped_batch_does_not_tidy(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki, llm = make_crow(bundle)
    llm.cost = 1.0
    llm.plan_reply = folders_json(("/red", "Red things."))
    seen: list[object] = []
    monkeypatch.setattr(Wiki, "_tidy", lambda self, made: seen.append(made) or [])

    with wiki.tracker.limit(5.0):
        report = wiki.ingest_many([item(n) for n in range(6)])

    assert report.stopped and seen == []


def test_the_classic_mode_does_not_tidy(bundle: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    llm = ContentLLM(UsageTracker())
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", concurrency=1), llm=llm, classifier=FakeClassifier(llm.usage))
    wiki.init()
    seen: list[object] = []
    monkeypatch.setattr(Wiki, "_tidy", lambda self, made: seen.append(made) or [])

    wiki.ingest_many([item(n) for n in range(3)])

    assert seen == []


def result(note: str, parts: list[IngestResult] | None = None) -> IngestResult:
    return IngestResult("created", note, "T", "/" + note.rpartition("/")[0], [], [], [], parts=parts or [])


def test_the_results_of_a_batch_follow_the_notes_and_folders_tidy_moved() -> None:
    moved_note, in_folder, sheet, stays = result("a/b/x.md"), result("c/d/e/y.md"), result("a/b/s2.md"), result("f/z.md")
    report = BatchReport(results=[(item(0), moved_note), (item(1), in_folder), (item(2), result("a/s1.md", [sheet])), (item(3), stays)])
    report.results.append((item(4), ValueError("failed")))

    _follow(report, [("a/b/x.md", "a/x.md"), ("a/b/s2.md", "a/s2.md"), ("c/d/e", "c/e")])

    assert (moved_note.note, moved_note.folder) == ("a/x.md", "/a")
    assert (in_folder.note, in_folder.folder) == ("c/e/y.md", "/c/e")
    assert (sheet.note, sheet.folder) == ("a/s2.md", "/a")
    assert (stays.note, stays.folder) == ("f/z.md", "/f")
