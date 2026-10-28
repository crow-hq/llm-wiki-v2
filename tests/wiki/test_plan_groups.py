# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The batch plan of a local writer: documents grouped in code, each group filed together in a folder named in code (or
an existing folder its notes' sources resemble), with no model call."""

from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path

import pytest

from llmw2 import Candidate, Route, Source, StepContext, Wiki, WikiConfig
from llmw2.agents import grouping
from llmw2.bundle.tree import Folder
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_steps import OFFLINE

ROAST = [
    "Aurora roast profile: the Aurora espresso blend is roasted to 212 degrees, first crack at nine minutes, light roast.",
    "Aurora roast adjustments after cupping: Aurora espresso blend now roasted two degrees darker, first crack later.",
]
PAY = [
    "Payroll calendar: salaries are paid on the 27th, payroll closes on the 20th, payslips by email.",
    "Payroll changes: the payroll closing day moves to the 18th, salaries still paid on the 27th, payslips unchanged.",
]
LONE = "Holiday rota for August: who is off in which week, and who covers the shop."


def test_groups_puts_documents_on_one_subject_together_and_leaves_a_lone_one_out() -> None:
    found = grouping.groups([ROAST[0], PAY[0], LONE, ROAST[1], PAY[1]])

    assert sorted(found) == [[0, 3], [1, 4]]


def test_groups_of_unrelated_documents_is_empty() -> None:
    assert grouping.groups([ROAST[0], PAY[0], LONE]) == []


def test_a_group_whose_words_a_folder_name_cannot_spell_gets_no_name() -> None:
    texts = ["Годовой отчёт: выручка выросла.", "Годовой отчёт: выручка и расходы."]

    assert grouping.name(texts, [0, 1]) == ""  # the group is left to routing, not filed in a folder named "topics"


def test_closest_is_the_folder_whose_notes_the_group_resembles_and_none_below_the_cut() -> None:
    folders = {"/roasting": [ROAST[0]], "/people": [PAY[0]]}

    assert grouping.closest([ROAST[1]], folders) == "/roasting"
    assert grouping.closest([LONE], folders) is None


def one_by_one(group_texts: list[str], folders: dict[str, list[str]], cut: float) -> str | None:
    """`closest` as it reads in full: every folder text weighed again with the group among them."""
    keys = [k for k, notes in folders.items() if notes]
    if not keys:
        return None
    vecs = grouping.vectors([" ".join(group_texts)] + [t for k in keys for t in folders[k]])
    scores, at = {}, 1
    for k in keys:
        scores[k] = sum(grouping.cosine(vecs[0], b) for b in vecs[at : at + len(folders[k])]) / len(folders[k])
        at += len(folders[k])
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] >= cut else None


@pytest.mark.parametrize("seed", range(30))
def test_closest_each_answers_as_closest_one_group_at_a_time(seed: int) -> None:
    rng = random.Random(seed)
    vocab = ["".join(rng.choices("abcdefgh", k=5)) for _ in range(rng.choice([20, 60, 200]))]
    say = lambda: " ".join(rng.choices(vocab, k=rng.randint(4, 40)))  # noqa: E731
    folders = {f"/f{k}": [say() for _ in range(rng.randint(0, 5))] for k in range(rng.randint(1, 8))}
    groups = [[say() for _ in range(rng.randint(1, 3))] for _ in range(5)]

    for cut in (0.0, 0.05, 0.1, 0.2):
        assert grouping.closest_each(groups, folders, cut) == [one_by_one(g, folders, cut) for g in groups]


@pytest.fixture
def wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    local = {"provider": "ollama", "model": "small", "base_url": "http://localhost:11434/v1"}
    w = Wiki(WikiConfig(bundle=bundle, mode="crow", llm=local, plan="groups"), llm=llm, classifier=classifier)
    w.init()
    return w


def test_plan_groups_on_an_empty_wiki_names_a_folder_per_group_in_code_and_places_its_documents(wiki: Wiki, llm: FakeLLM) -> None:
    docs = [("roast.md", ROAST[0]), ("pay.md", PAY[0]), ("rota.md", LONE), ("Aurora adjustments", ROAST[1]), ("", PAY[1])]

    placed = wiki.plan_groups(docs)

    roast, pay = f"/{grouping.name([t for _, t in docs], [0, 3])}", f"/{grouping.name([t for _, t in docs], [1, 4])}"
    assert placed == {0: roast, 3: roast, 1: pay, 4: pay}  # the lone one is routed as usual
    root = wiki.store.load()
    assert {f.label for f in root.walk() if not f.is_root} == {roast, pay}
    assert all(w in " ".join(ROAST).lower() for w in roast.strip("/").split("-"))
    # described by the titles of its first documents, the opening words of one that has none
    assert root.find(roast.strip("/")).description == "roast.md; Aurora adjustments"  # type: ignore[union-attr]
    assert root.find(pay.strip("/")).description == "pay.md; Payroll changes: the payroll closing day moves to"  # type: ignore[union-attr]
    assert ", by plan" in (wiki.cfg.bundle / "log.md").read_text(encoding="utf-8")
    assert llm.ops == []  # no model call


def test_plan_groups_sends_a_group_to_an_existing_folder_it_resembles_without_asking_the_writer(wiki: Wiki, llm: FakeLLM) -> None:
    folder, _ = wiki.store.create_folder(wiki.store.load(), "roasting", "How each blend is roasted.")
    source = {"resource": "/raw/a.md", "title": "a"}
    wiki.store.write_note(folder, title="Aurora roast", summary="Aurora.", body=ROAST[0], tags=[], source=source)

    placed = wiki.plan_groups([("r1.md", ROAST[1]), ("r2.md", ROAST[1] + " Second batch, same Aurora roast.")])

    assert placed == {0: "/roasting", 1: "/roasting"}
    assert [f.label for f in wiki.store.load().walk()][1:] == ["/roasting"]


def test_plan_groups_compares_a_group_with_the_documents_a_folder_was_made_from_not_its_notes(wiki: Wiki, llm: FakeLLM) -> None:
    folder, _ = wiki.store.create_folder(wiki.store.load(), "roasting", "How each blend is roasted.")
    raw = wiki.store.save_raw(ROAST[0], title="roast", resource=None)
    source = {"resource": raw, "title": "roast"}
    wiki.store.write_note(folder, title="Notes", summary="Notes.", body="A short note.", tags=[], source=source)

    placed = wiki.plan_groups([("r1.md", ROAST[1]), ("r2.md", ROAST[1] + " Second batch, same Aurora roast.")])

    assert placed == {0: "/roasting", 1: "/roasting"}
    assert wiki.store.source_text(wiki.store.load().find("roasting").notes[0]) == ROAST[0]


def test_plan_groups_leaves_a_group_to_routing_when_its_documents_share_no_word_to_name_it_by(wiki: Wiki) -> None:
    same = "Folder name path 4521 subfolder 7788."  # only the words that say how to name a folder, and numbers

    assert grouping.groups([same, same]) == [[0, 1]]
    assert wiki.plan_groups([("a.md", same), ("b.md", same)]) == {}
    assert [f.label for f in wiki.store.load().walk()][1:] == []


def test_file_with_a_planned_folder_skips_routing(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    def route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
        raise AssertionError("a planned document is not routed")

    w = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier, steps=replace(OFFLINE, route=route))
    w.init()
    w.store.create_folder(w.store.load(), "roasting", "How each blend is roasted.")
    prepared = w.librarian().prepare(Source(text=ROAST[0], title="roast.md"))

    result = w._file(prepared, "/roasting")

    assert result.folder == "/roasting"
    assert any(d.step == "route" and d.decider == "code" for d in result.decisions)
