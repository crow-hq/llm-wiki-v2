# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A plan that holds still: at most `max_top_folders` top-level folders in `plan` and in routing, a batch planned in full before
it is filed, and `rebuild` on the folders a wiki has."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Item, ModelError, Origin, Wiki, WikiConfig
from llmw2.agents import steps_crow
from llmw2.agents.data import Candidate, Route
from llmw2.errors import ReplyInvalid
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_crow_plan import (
    DOCS,
    NONE,
    OLLAMA,
    PLAN,
    PLAN_MORE,
    SUMMARIZE,
    PlanLLM,
    TopicClassifier,
    add_folder,
    add_note,
    filed_all,
    folders_json,
    labels,
    log_lines,
    user_text,
)

GROUPS = ("alpha", "beta", "gamma")


def doc(n: int) -> Item:
    """Documents 0-49 are about alpha, 50-99 about beta, the rest about gamma: one window each, in this order."""
    group = GROUPS[min(n // 50, 2)]
    data = f"topic: {group}-{n}\nline of document {n}.".encode()
    return Item(f"doc-{n:03d}.md", data=data, origin=Origin("test", "acct", f"doc-{n:03d}"))


class WindowsLLM(PlanLLM):
    """The plan of the first window opens /alpha, the next ones /beta and /gamma (what they are shown is kept)."""

    def __init__(self, usage: UsageTracker) -> None:
        super().__init__(usage)
        self.more = [folders_json(("/beta", "Beta things.")), folders_json(("/gamma", "Gamma things."))]
        self.plan_reply = folders_json(("/alpha", "Alpha things."))

    def _plan_more(self, user: str) -> Any:
        return self.more.pop(0)


def make(bundle: Path, llm: PlanLLM | None = None, **cfg: Any) -> tuple[Wiki, Any]:
    llm = llm or WindowsLLM(UsageTracker())
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", concurrency=1, **cfg), llm=llm, classifier=TopicClassifier(llm.usage))
    wiki.init()
    return wiki, llm


def folder_of(wiki: Wiki) -> dict[str, str]:
    return {n.title: n.folder.label for n in wiki.store.load().all_notes() if n.folder is not None}


def roots(wiki: Wiki) -> list[str]:
    return [f.name for f in wiki.store.load().subfolders]


# -- the limit in plan --------------------------------------------------------------------


def test_plan_more_with_ten_roots_makes_no_new_root_and_none_of_their_subfolders(crow_wiki: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    for n in range(crow_wiki.cfg.max_top_folders):
        add_folder(crow_wiki, "/", f"old{n}", "Old.")
    llm.add(PLAN_MORE, folders_json(*[(f"/new{n}", "New.") for n in range(15)], ("/new3/sub", "Sub."), ("/old1/sub", "Sub.")))

    # ACT
    made = crow_wiki.plan(DOCS)

    # ASSERT
    assert [f.label for f in made] == ["/old1/sub"]  # a subfolder of a root there is stays possible
    assert len(roots(crow_wiki)) == crow_wiki.cfg.max_top_folders


def test_plan_more_fills_the_roots_up_to_the_limit(crow_wiki: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    for n in range(8):
        add_folder(crow_wiki, "/", f"old{n}", "Old.")
    llm.add(PLAN_MORE, folders_json(*[(p, "New.") for n in range(5) for p in (f"/new{n}", f"/new{n}/sub")]))

    # ACT
    made = crow_wiki.plan(DOCS)

    # ASSERT
    assert [f.label for f in made] == ["/new0", "/new0/sub", "/new1", "/new1/sub"]


def test_plan_of_an_empty_wiki_makes_at_most_ten_roots(crow_wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(PLAN, folders_json(*[(f"/area{n}", "Area.") for n in range(14)]))

    made = crow_wiki.plan(DOCS)

    top = crow_wiki.cfg.max_top_folders
    assert len(made) == top and len(roots(crow_wiki)) == top


def test_plan_shows_the_counts_it_is_given_beside_the_notes_there_are(crow_wiki: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    add_folder(crow_wiki, "/", "sales", "Sales stuff.")
    add_note(crow_wiki, "/sales", "Deal one")
    llm.add(PLAN_MORE, {"folders": []})

    # ACT
    crow_wiki.plan(DOCS, {"/sales": 7})

    # ASSERT
    assert "- /sales: Sales stuff.; 8 notes" in user_text(llm.calls[0])


# -- the limit in routing -------------------------------------------------------------------


@pytest.fixture
def full(crow_wiki: Wiki) -> Wiki:
    """Ten roots, /f2 with a subfolder."""
    for n in range(crow_wiki.cfg.max_top_folders):
        add_folder(crow_wiki, "/", f"f{n}", f"Folder number {n}.")
    add_folder(crow_wiki, "/f2", "sub", "Sub.")
    return crow_wiki


def capped(wiki: Wiki, route_of: Any, cand: Candidate | None = None) -> Route:
    root = wiki.store.load()
    ctx = wiki.librarian().ctx
    return steps_crow.cap_roots(ctx, root, cand or Candidate("A note", "", [], "text"), route_of(root))


def test_a_new_root_with_ten_roots_goes_to_the_roots_likeliest_by_the_flat_choice(full: Wiki, classifier: FakeClassifier) -> None:
    # ARRANGE: None of these on top, then /f2/sub: the likeliest folder that is not None, and its root
    classifier.add_choice("route", NONE, {NONE: 0.5, "/f2/sub": 0.3, "/f1": 0.2}, 0.5)

    # ACT
    route = capped(full, lambda root: Route(root, True, [root]))

    # ASSERT
    assert route.folder.label == "/f2" and not route.new and not route.create


def test_a_create_that_opens_a_root_is_capped_too(full: Wiki, classifier: FakeClassifier) -> None:
    classifier.add_choice("route", "/f4", {"/f4": 0.7, NONE: 0.3}, 0.7)

    route = capped(full, lambda root: Route(root, False, [root], [("brand-new", "New."), ("deeper", "Deeper.")]))

    assert route.folder.label == "/f4" and not route.create


def test_a_create_into_a_root_there_is_opens_nothing(full: Wiki) -> None:
    route = capped(full, lambda root: Route(root, False, [root], [("f1", "Same."), ("deeper", "Deeper.")]))

    assert route.folder.is_root and route.create == [("f1", "Same."), ("deeper", "Deeper.")]


def test_a_route_below_a_root_and_a_wiki_under_the_limit_are_untouched(crow_wiki: Wiki, full: Wiki) -> None:
    below = capped(full, lambda root: Route(root.find("/f2"), True, [root]))
    assert below.folder.label == "/f2" and below.new

    full.delete_folder("/f9")
    under = capped(full, lambda root: Route(root, True, [root]))
    assert under.folder.is_root and under.new


class DownClassifier(FakeClassifier):
    def ask(self, state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
        raise ModelError("down")


def test_with_the_classifier_down_the_root_is_the_lexical_rank(crow_wiki: Wiki) -> None:
    # ARRANGE
    for name, text in (("sales", "Pricing and deals."), ("support", "Tickets and help."), ("legal", "Contracts.")):
        add_folder(crow_wiki, "/", name, text)
    for n in range(crow_wiki.cfg.max_top_folders - 3):
        add_folder(crow_wiki, "/", f"misc{n}", "Other.")
    crow_wiki.classifier = DownClassifier(UsageTracker())

    # ACT
    route = capped(crow_wiki, lambda root: Route(root, True, [root]), Candidate("Help", "", [], "Open tickets and help requests."))

    # ASSERT
    assert route.folder.label == "/support"


class NoneClassifier(TopicClassifier):
    """Always answers None of these when it can, giving 0.4 to the folder of the note's group."""

    def ask(self, state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
        self.calls.append((op, state, questions))
        group = re.search(r"topic: ([a-z0-9]+)-\d+", state).group(1)  # type: ignore[union-attr]
        answers: dict[str, dict[str, Any]] = {}
        for key, question in questions.items():
            if question["type"] != "choice":
                answers[key] = {"type": "noul", "noul": 0.0}
                continue
            options = list(question["criteria"])
            pick = NONE if NONE in options else options[0]
            own = f"/{group}" if f"/{group}" in options else group
            probabilities = {pick: 0.6, own: 0.4} if own in options and own != pick else {pick: 1.0}
            answers[key] = {"type": "choice", "choice": pick, "probabilities": probabilities, "confidence": 0.6}
        return answers


class NoFallbackLLM(PlanLLM):
    def _route_fallback(self, user: str) -> Any:
        raise ReplyInvalid("not JSON")


def test_the_local_writers_fallback_with_ten_roots_files_in_the_likeliest_root(bundle: Path) -> None:
    # ARRANGE: None of these everywhere, the fallback fails: it would open a root (the best path ends at the tree's root)
    llm = NoFallbackLLM(UsageTracker())
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", concurrency=1, llm=OLLAMA), llm=llm, classifier=NoneClassifier(llm.usage))
    wiki.init()
    for n in range(wiki.cfg.max_top_folders):
        add_folder(wiki, "/", f"g{n}", f"Group {n}.")

    # ACT
    result = wiki.ingest_file(b"topic: g3-1\nOne line.", "doc.md")

    # ASSERT
    assert result.folder == "/g3" and roots(wiki) == [f"g{n}" for n in range(wiki.cfg.max_top_folders)]
    assert "librarian/name_folder" not in llm.ops
    assert any(d.step == "route" and d.decider == "code" and d.fallback for d in result.decisions)


def test_without_the_limit_reached_a_new_root_is_still_opened(bundle: Path) -> None:
    llm = NoFallbackLLM(UsageTracker())
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", concurrency=1, llm=OLLAMA), llm=llm, classifier=NoneClassifier(llm.usage))
    wiki.init()
    for n in range(wiki.cfg.max_top_folders - 1):
        add_folder(wiki, "/", f"g{n}", f"Group {n}.")

    wiki.ingest_file(b"topic: g3-1\nOne line.", "doc.md")

    assert len(roots(wiki)) == wiki.cfg.max_top_folders  # the tenth is opened


# -- the limit is an option ------------------------------------------------------------------------


def test_plan_of_an_empty_wiki_makes_at_most_max_top_folders_roots_and_tells_the_writer(bundle: Path) -> None:
    # ARRANGE
    llm = WindowsLLM(UsageTracker())
    llm.plan_reply = folders_json(*[(p, "Area.") for n in range(6) for p in (f"/area{n}", f"/area{n}/sub")])
    wiki, _ = make(bundle, llm, max_top_folders=3)

    # ACT
    made = wiki.plan(DOCS)

    # ASSERT
    assert roots(wiki) == ["area0", "area1", "area2"] and len(made) == 6
    assert "At most 3 top-level folders" in user_text(llm.calls[0])


def test_cap_roots_opens_a_root_only_below_max_top_folders(bundle: Path) -> None:
    # ARRANGE
    wiki, _ = make(bundle, max_top_folders=3)
    for name in ("alpha", "beta"):
        add_folder(wiki, "/", name, f"{name} things.")
    cand = Candidate("A note", "", [], "topic: beta-1")

    # ACT
    below = capped(wiki, lambda root: Route(root, True, [root]), cand)
    add_folder(wiki, "/", "gamma", "Gamma things.")
    full = capped(wiki, lambda root: Route(root, True, [root]), cand)

    # ASSERT: with two roots the third is opened; with three the fourth is not
    assert below.folder.is_root and below.new
    assert full.folder.label == "/beta" and not full.new and roots(wiki) == ["alpha", "beta", "gamma"]


def test_plan_groups_opens_no_root_over_max_top_folders(bundle: Path) -> None:
    # ARRANGE
    llm = PlanLLM(UsageTracker())
    config = WikiConfig(bundle=bundle, mode="crow", concurrency=1, llm=OLLAMA, plan="groups", max_top_folders=1)
    wiki = Wiki(config, llm=llm, classifier=TopicClassifier(llm.usage))
    wiki.init()
    texts = {
        "roast-1": "Aurora roast profile: the Aurora espresso blend is roasted to 212 degrees, first crack at nine minutes.",
        "roast-2": "Aurora roast adjustments after cupping: the Aurora espresso blend roasted darker, first crack later.",
        "pay-1": "Payroll calendar: salaries are paid on the 27th, payroll closes on the 20th, payslips by email.",
        "pay-2": "Payroll changes: the payroll closing day moves to the 18th, salaries paid on the 27th, payslips unchanged.",
    }
    items = [Item(f"{t}.md", data=f"topic: {t}\n{text}".encode(), origin=Origin("test", "acct", t)) for t, text in texts.items()]

    # ACT
    report = wiki.ingest_many(items)

    # ASSERT: one group has the only root, the documents of the other are routed as usual
    assert filed_all(report, 4) and len(roots(wiki)) == 1


# -- two rounds: every plan, then every filing --------------------------------------------------


def test_every_plan_comes_before_the_first_filing_and_plan_more_is_shown_the_counts(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make(bundle)

    # ACT
    report = wiki.ingest_many([doc(n) for n in range(120)])

    # ASSERT
    assert filed_all(report, 120)
    first_filing = llm.ops.index(SUMMARIZE)
    assert llm.ops[:first_filing] == [PLAN, PLAN_MORE, PLAN_MORE]
    second, third = (user_text(c) for c in llm.calls if c[0] == PLAN_MORE)
    assert "- /alpha: Alpha things.; 50 notes" in second and "/beta" not in second
    assert "- /alpha: Alpha things.; 50 notes" in third and "- /beta: Beta things.; 50 notes" in third


def test_the_counts_the_plans_are_shown_ask_the_classifier_nothing(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make(bundle)
    routes: list[int] = []  # the classifier's route calls made when each of the last two plans and the first filing began
    real = llm.chat

    def spy(messages: Any, *, op: str = "", **kwargs: Any) -> Any:
        if op in (PLAN_MORE, SUMMARIZE) and len(routes) < 3:
            routes.append(wiki.classifier.ops.count("route"))  # type: ignore[union-attr]
        return real(messages, op=op, **kwargs)

    # ACT
    llm.chat = spy  # type: ignore[method-assign]
    report = wiki.ingest_many([doc(n) for n in range(120)])

    # ASSERT: no route call before the two plans that are shown counts, nor before the first filing
    assert filed_all(report, 120) and routes == [0, 0, 0]


def test_a_finished_batch_takes_back_the_folders_of_its_plans_that_no_document_went_to(bundle: Path) -> None:
    # ARRANGE: the plan of the first window also opens /unused, which no document is about
    llm = WindowsLLM(UsageTracker())
    llm.plan_reply = folders_json(("/alpha", "Alpha things."), ("/unused", "Nothing is about this."), ("/unused/deep", "Nor this."))
    wiki, _ = make(bundle, llm)
    # ACT
    report = wiki.ingest_many([doc(n) for n in range(120)])

    # ASSERT: the folders made empty by the plans are gone; the others stay
    assert filed_all(report, 120) and report.stopped is None
    assert labels(wiki) == ["/alpha", "/beta", "/gamma"]
    removed = log_lines(wiki, "Removed folder")
    assert sorted(("/unused/deep" in line, "/unused," in line) for line in removed) == [(False, True), (True, False)]


def test_the_notes_go_where_a_reassignment_on_the_final_tree_puts_them(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make(bundle)

    # ACT
    wiki.ingest_many([doc(n) for n in range(120)])

    # ASSERT
    assert labels(wiki) == ["/alpha", "/beta", "/gamma"]
    placed = folder_of(wiki)
    assert len(placed) == 120 and all(folder == "/" + title.split()[1].split("-")[0] for title, folder in placed.items())
    assert all(op != PLAN_MORE for op in llm.ops[llm.ops.index(SUMMARIZE) :])


def test_a_batch_of_one_window_is_planned_and_filed_as_before(bundle: Path) -> None:
    wiki, llm = make(bundle)

    report = wiki.ingest_many([doc(n) for n in range(5)])

    assert filed_all(report, 5) and [op for op in llm.ops if op in (PLAN, PLAN_MORE)] == [PLAN]


def test_a_batch_stopped_while_filing_keeps_the_folders_with_notes_and_takes_back_the_empty_ones(bundle: Path) -> None:
    # ARRANGE: the model fails for good on document 60, in the second window
    llm = WindowsLLM(UsageTracker())
    llm.fail_on = {"line of document 60.": ModelError("down")}
    wiki, _ = make(bundle, llm)

    # ACT
    report = wiki.ingest_many([doc(n) for n in range(120)])

    # ASSERT
    assert report.stopped is not None and report.stopped.startswith("doc-060.md")
    assert len(report.results) == 60 and len(report.not_done) == 60
    assert labels(wiki) == ["/alpha", "/beta"]  # gamma was planned, and is empty: taken back
    assert len(folder_of(wiki)) == 60


def test_a_second_batch_moves_no_note_that_was_filed(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make(bundle)
    wiki.ingest_many([doc(n) for n in range(60)])
    before = folder_of(wiki)
    llm.more.append(folders_json(("/delta", "Delta things.")))

    # ACT
    report = wiki.ingest_many([doc(n) for n in range(60, 120)])

    # ASSERT: a note changes folder only for a descendant of its own (a split)
    after = folder_of(wiki)
    assert filed_all(report, 60)
    assert all(after[title] == folder or after[title].startswith(folder + "/") for title, folder in before.items())


# -- rebuild ------------------------------------------------------------------------------------


def test_rebuild_files_the_same_documents_on_the_same_tree_without_a_plan(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make(bundle)
    items = [doc(n) for n in range(120)]
    wiki.ingest_many(items)
    tree = [(f.label, f.description) for f in wiki.store.load().walk()]
    placed = folder_of(wiki)
    rels = sorted(n.rel for n in wiki.store.load().all_notes())
    calls = len(llm.calls)

    # ACT
    report = wiki.rebuild(items)

    # ASSERT
    assert filed_all(report, 120)
    assert [op for op, _ in llm.calls[calls:] if op in (PLAN, PLAN_MORE)] == []
    assert [(f.label, f.description) for f in wiki.store.load().walk()] == tree
    assert folder_of(wiki) == placed
    assert sorted(n.rel for n in wiki.store.load().all_notes()) == rels


def test_rebuild_removes_the_notes_raw_copies_and_archive_but_not_the_folders(bundle: Path) -> None:
    # ARRANGE
    wiki, _ = make(bundle)
    wiki.ingest_many([doc(n) for n in range(60)])

    # ACT
    deleted = wiki.store.clear(keep_folders=True)

    # ASSERT
    assert len(deleted.notes) == 60 and deleted.folders == []
    assert labels(wiki) == ["/alpha", "/beta"] and not wiki.store.load().all_notes()
    assert not list((bundle / "raw").glob("*.md"))
