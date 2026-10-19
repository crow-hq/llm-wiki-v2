# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: `Wiki.plan` on a wiki with folders (plan_more) and the plan of every window of a CROW batch."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Item, ModelError, Origin, Wiki, WikiConfig
from llmw2.agents.prompts import Prompts
from llmw2.bundle.tree import LOG, Note
from llmw2.config import PROMPT_ROOM, LLMConfig
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM, FakeClassifier, FakeLLM

HERE, NEW, NONE = "Here", "New subfolder", "None of these"
PLAN, PLAN_MORE = "librarian/plan", "librarian/plan_more"
SUMMARIZE = "librarian/summarize"
JSON_SUFFIX = "\n\nReply with a single JSON object and nothing else."  # what `LLM.json` adds to every user message

PLAN_MORE_TEXT = """A knowledge base already has these folders (path: description; notes directly in it):
<tree>

These documents are about to be filed (title: opening):
<documents>

Lay out only the NEW folders these documents need: a new top-level area only for a broad area none of the existing ones
covers, a new subfolder only for a specific subject (one client, one supplier, a project, a product line, a recurring kind
of document) that at least 2 of these documents share and no existing folder covers. Never repeat, rename or move an
existing folder. At most <depth> levels in all. Names: 1 to 3 lowercase words joined by hyphens, letters a-z and digits.
It is fine to answer with no folders.

Reply with JSON, parents before their subfolders:
{"folders": [{"path": "/area/subject", "description": "..."}]}"""


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


def folders_json(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {"folders": [{"path": p, "description": d} for p, d in pairs]}


def labels(wiki: Wiki) -> list[str]:
    return [f.label for f in wiki.store.load().walk()][1:]


def note_rels(wiki: Wiki) -> list[str]:
    return sorted(n.rel for n in wiki.store.load().all_notes())


def log_lines(wiki: Wiki, verb: str) -> list[str]:
    text = (wiki.cfg.bundle / LOG).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.startswith(f"* **{verb}**")]


def user_text(call: tuple[str, list[dict[str, str]]]) -> str:
    return call[1][-1]["content"].removesuffix(JSON_SUFFIX)


DOCS = [(None, "Acme  raised\nprices."), ("Acme terms", "New terms.")]


@pytest.fixture
def populated(crow_wiki: Wiki) -> Wiki:
    """/sales (2 notes), /sales/emea (no description, no notes) and /support (1 note)."""
    add_folder(crow_wiki, "/", "sales", "Sales stuff.")
    add_folder(crow_wiki, "/sales", "emea", "")
    add_folder(crow_wiki, "/", "support", "Help.")
    for folder, title in (("/sales", "Deal one"), ("/sales", "Deal two"), ("/support", "Ticket")):
        add_note(crow_wiki, folder, title)
    return crow_wiki


def expected_plan_more(populated: Wiki, lines: list[str], depth: int = 3) -> str:
    tree = "\n".join(
        f"- {f.label}: {f.description or '(no description)'}; {len(f.notes)} notes" for f in populated.store.load().walk() if not f.is_root
    )
    return PLAN_MORE_TEXT.replace("<tree>", tree).replace("<documents>", "\n".join(lines)).replace("<depth>", str(depth))


# -- the constants ----------------------------------------------------------------------


def test_plan_batch_is_fifty() -> None:
    from llmw2 import wiki

    assert wiki.PLAN_BATCH == 50


# -- Wiki.plan on an empty wiki: as today ---------------------------------------------------


def test_plan_on_an_empty_wiki_makes_the_proposed_folders(crow_wiki: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    llm.add(PLAN, folders_json(("/sales", "Sales."), ("/sales/renewals", "Renewals.")))

    # ACT
    made = crow_wiki.plan(DOCS)

    # ASSERT
    assert [f.label for f in made] == ["/sales", "/sales/renewals"]
    assert llm.ops == [PLAN]
    assert labels(crow_wiki) == ["/sales", "/sales/renewals"]


def test_plan_on_an_empty_wiki_marks_the_log_lines_with_by_plan(crow_wiki: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    llm.add(PLAN, folders_json(("/sales", "Sales."), ("/sales/renewals", "Renewals.")))

    # ACT
    crow_wiki.plan(DOCS)

    # ASSERT
    created = log_lines(crow_wiki, "Created folder")
    assert len(created) == 2 and all(line.endswith(", by plan") for line in created)


@pytest.mark.parametrize("docs", [[], [("Only one", "Single document.")]])
@pytest.mark.parametrize("with_folders", [False, True])
def test_plan_of_fewer_than_two_documents_makes_no_call(
    crow_wiki: Wiki, llm: FakeLLM, docs: list[tuple[str | None, str]], with_folders: bool
) -> None:
    # ARRANGE
    if with_folders:
        add_folder(crow_wiki, "/", "sales", "Sales stuff.")

    # ACT
    made = crow_wiki.plan(docs)

    # ASSERT
    assert made == [] and llm.calls == []


# -- Wiki.plan on a wiki with folders: plan_more ---------------------------------------------------


def test_plan_more_asks_librarian_plan_more_and_creates_only_new_folders(populated: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    notes = note_rels(populated)
    problems = populated.check()  # /sales/emea has no description: check says so before the plan as after it
    llm.add(
        PLAN_MORE,
        folders_json(
            ("/sales", "A NEW DESCRIPTION"),  # exists: never re-described
            ("/sales/emea", "Europe."),  # exists with no description: never described by it either
            ("/legal", "Legal."),
            ("/sales/partners", "Partners."),  # new, under an existing folder
            ("/legal/contracts", "Contracts."),  # new, under a new one
        ),
    )

    # ACT
    made = populated.plan(DOCS)

    # ASSERT
    assert llm.ops == [PLAN_MORE]
    assert [f.label for f in made] == ["/legal", "/sales/partners", "/legal/contracts"]
    tree = populated.store.load()
    assert tree.find("/sales").description == "Sales stuff."
    assert tree.find("/sales/emea").description == ""
    assert tree.find("/legal").description == "Legal."
    assert note_rels(populated) == notes  # no note moved
    assert populated.check() == problems


def test_plan_more_marks_the_log_lines_with_by_plan_more(populated: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    llm.add(PLAN_MORE, folders_json(("/legal", "Legal."), ("/legal/contracts", "Contracts.")))

    # ACT
    populated.plan(DOCS)

    # ASSERT
    mine = [line for line in log_lines(populated, "Created folder") if "/legal" in line]
    assert len(mine) == 2 and all(line.endswith(", by plan_more") for line in mine)


def test_plan_more_with_only_existing_folders_makes_nothing(populated: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    before = labels(populated)
    llm.add(PLAN_MORE, folders_json(("/sales", "X."), ("/sales/emea", "Y."), ("/support", "Z.")))

    # ACT
    made = populated.plan(DOCS)

    # ASSERT
    assert made == [] and labels(populated) == before
    assert llm.ops == [PLAN_MORE]


def test_plan_more_with_no_folders_makes_nothing(populated: Wiki, llm: FakeLLM) -> None:
    llm.add(PLAN_MORE, {"folders": []})

    assert populated.plan(DOCS) == []
    assert llm.ops == [PLAN_MORE]


def test_plan_more_skips_a_missing_parent_an_empty_description_and_an_empty_name(populated: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    llm.add(
        PLAN_MORE,
        folders_json(
            ("/nowhere/child", "Parent missing."),
            ("/legal", "   "),  # empty description
            ("/hr//people", "Empty name part."),
            ("/Brand New", "Slugified."),
        ),
    )

    # ACT
    made = populated.plan(DOCS)

    # ASSERT
    assert [f.label for f in made] == ["/brand-new"]
    assert labels(populated) == ["/brand-new", "/sales", "/sales/emea", "/support"]  # the loaded tree lists folders by name


def test_plan_more_skips_a_folder_deeper_than_the_plan_depth(populated: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    llm.add(PLAN_MORE, folders_json(("/sales/emea/uk", "UK."), ("/sales/emea/uk/london", "London: four levels.")))

    # ACT
    made = populated.plan(DOCS)

    # ASSERT
    assert [f.label for f in made] == ["/sales/emea/uk"]  # three levels is PLAN_DEPTH


def test_plan_more_depth_follows_max_depth(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", max_depth=1), llm=llm, classifier=classifier)
    wiki.init()
    add_folder(wiki, "/", "sales", "Sales stuff.")
    llm.add(PLAN_MORE, folders_json(("/sales/emea", "Too deep."), ("/legal", "Fine.")))

    # ACT
    made = wiki.plan(DOCS)

    # ASSERT
    assert [f.label for f in made] == ["/legal"]
    assert llm.calls[0][1][-1]["content"].count("At most 1 levels in all.") == 1


def test_plan_more_sends_exactly_the_specified_prompt(populated: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    llm.add(PLAN_MORE, {"folders": []})
    lines = ["- (untitled): Acme raised prices.", "- Acme terms: New terms."]

    # ACT
    populated.plan(DOCS)

    # ASSERT
    op, messages = llm.calls[0]
    assert op == PLAN_MORE
    assert messages[0]["content"] == Prompts().render("librarian/system")
    assert user_text(llm.calls[0]) == expected_plan_more(populated, lines)
    assert "(no description)" in user_text(llm.calls[0])  # /sales/emea


def test_plan_more_samples_the_documents_as_the_plan_does(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    cfg = WikiConfig(bundle=bundle, mode="crow", llm=LLMConfig(model="fake/llm", read_chars=8_000))
    wiki = Wiki(cfg, llm=llm, classifier=classifier)
    wiki.init()
    add_folder(wiki, "/", "sales", "Sales stuff.")
    docs = [(f"Doc {n}", f"opening {n} " + "x" * 400) for n in range(10)]
    lines = [f"- {title}: {' '.join(text.split())[:220]}" for title, text in docs]
    while len(lines) > 2 and sum(len(line) + 1 for line in lines) > cfg.effective_read_chars - PROMPT_ROOM:
        lines = lines[::2]
    assert len(lines) < 10  # the arrangement really does sample
    llm.add(PLAN_MORE, {"folders": []})

    # ACT
    wiki.plan(docs)

    # ASSERT
    assert user_text(llm.calls[0]) == expected_plan_more(wiki, lines)


class FilingLLM(FakeLLM):
    """While it answers, another writer files a note: the wiki changes under the plan's feet."""

    def __init__(self, usage: UsageTracker, wiki: Wiki | None = None) -> None:
        super().__init__(usage)
        self.wiki = wiki
        self.meanwhile: Any = None

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        reply = super().chat(messages, **kwargs)
        if self.meanwhile:
            self.meanwhile()
        return reply


def test_plan_of_an_empty_wiki_gives_up_when_a_document_was_filed_meanwhile(bundle: Path) -> None:
    # ARRANGE
    llm = FilingLLM(UsageTracker())
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=FakeClassifier(llm.usage))
    wiki.init()

    def filed() -> None:
        add_folder(wiki, "/", "other", "Other.")
        add_note(wiki, "/other", "Filed meanwhile")

    llm.meanwhile = filed
    llm.add(PLAN, folders_json(("/sales", "Sales."), ("/support", "Help.")))

    # ACT
    made = wiki.plan(DOCS)

    # ASSERT
    assert made == [] and labels(wiki) == ["/other"]


def test_plan_more_has_no_guard_against_a_document_filed_meanwhile(bundle: Path) -> None:
    # ARRANGE
    llm = FilingLLM(UsageTracker())
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=FakeClassifier(llm.usage))
    wiki.init()
    add_folder(wiki, "/", "sales", "Sales stuff.")
    llm.meanwhile = lambda: add_note(wiki, "/sales", "Filed meanwhile")
    llm.add(PLAN_MORE, folders_json(("/legal", "Legal.")))

    # ACT
    made = wiki.plan(DOCS)

    # ASSERT
    assert [f.label for f in made] == ["/legal"]


# -- ingest_many: a plan per window of PLAN_BATCH ----------------------------------------------------


class PlanLLM(ContentLLM):
    """ContentLLM that also answers the plan and split prompts, with what the test sets."""

    def __init__(self, usage: UsageTracker) -> None:
        super().__init__(usage)
        self.plan_reply: Any = folders_json(("/red", "Red things."))
        self.plan_more_reply: Any = {"folders": []}
        self.plan_error: Exception | None = None

    def _plan(self, user: str) -> Any:
        if self.plan_error:
            raise self.plan_error
        return self.plan_reply

    def _plan_more(self, user: str) -> Any:
        if self.plan_error:
            raise self.plan_error
        return self.plan_more_reply

    def _split(self, user: str) -> Any:
        return {"folders": []}


class TopicClassifier(FakeClassifier):
    """Files a note, flat or by levels, in the top-level folder named after its group ("About red-3" goes to red).

    Nothing depends on the order of the calls. A match or a link is never found.
    """

    def ask(self, state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
        self.calls.append((op, state, questions))
        self.usage.record("classifier", self.model, 50, 0, op=op)
        group = (re.search(r"About ([a-z]+)-\d+", state) or re.search(r"topic: ([a-z]+)-\d+", state)).group(1)  # type: ignore[union-attr]
        answers: dict[str, dict[str, Any]] = {}
        for key, question in questions.items():
            if question["type"] != "choice":
                answers[key] = {"type": "noul", "noul": 0.0}
                continue
            options = list(question["criteria"])
            pick = next((o for o in (f"/{group}", group, HERE, NEW) if o in options), NONE if NONE in options else options[0])
            other = next((o for o in options if o != pick), pick)
            probabilities = {pick: 0.95, other: 0.05} if other != pick else {pick: 1.0}
            answers[key] = {"type": "choice", "choice": pick, "probabilities": probabilities, "confidence": 0.9}
        return answers


def item(n: int) -> Item:
    return Item(f"doc-{n:03d}.md", data=f"topic: red-{n}\nline of document {n}.".encode(), origin=Origin("test", "acct", f"doc-{n:03d}"))


def make_crow(bundle: Path, **cfg: Any) -> tuple[Wiki, PlanLLM]:
    llm = PlanLLM(UsageTracker())
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", concurrency=1, **cfg), llm=llm, classifier=TopicClassifier(llm.usage))
    wiki.init()
    return wiki, llm


def filed_all(report: Any, n: int) -> bool:
    return (
        report.stopped is None
        and not report.not_done
        and len(report.results) == n
        and all(not isinstance(r, Exception) for _, r in report.results)
    )


def plans(llm: PlanLLM) -> list[str]:
    return [op for op in llm.ops if op in (PLAN, PLAN_MORE)]


def test_batch_on_an_empty_wiki_plans_once_before_the_first_filing(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make_crow(bundle)

    # ACT
    report = wiki.ingest_many([item(n) for n in range(3)])

    # ASSERT
    assert filed_all(report, 3)
    assert plans(llm) == [PLAN]
    assert llm.ops.index(PLAN) < llm.ops.index(SUMMARIZE)
    assert [f.label for f in wiki.store.load().walk()][1:] == ["/red"]
    assert [line.endswith(", by plan") for line in log_lines(wiki, "Created folder")] == [True]


def test_batch_on_a_wiki_with_folders_asks_plan_more(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make_crow(bundle)
    add_folder(wiki, "/", "red", "Red things.")
    llm.plan_more_reply = folders_json(("/blue", "Blue things."))

    # ACT
    report = wiki.ingest_many([item(n) for n in range(3)])

    # ASSERT
    assert filed_all(report, 3)
    assert plans(llm) == [PLAN_MORE]
    assert llm.ops.index(PLAN_MORE) < llm.ops.index(SUMMARIZE)
    assert "/blue" in [f.label for f in wiki.store.load().walk()]
    assert [line.endswith(", by plan_more") for line in log_lines(wiki, "Created folder")[:1]] == [True]


def test_a_window_of_fifty_documents_is_one_plan_and_the_next_window_plans_again(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make_crow(bundle)
    llm.plan_more_reply = {"folders": []}

    # ACT
    report = wiki.ingest_many([item(n) for n in range(52)])

    # ASSERT
    assert filed_all(report, 52)
    assert plans(llm) == [PLAN, PLAN_MORE]
    first, second = (i for i, op in enumerate(llm.ops) if op in (PLAN, PLAN_MORE))
    assert llm.ops[:second].count(SUMMARIZE) == 50  # the second plan comes when the first window is filed
    assert llm.ops[first:second].count(SUMMARIZE) == 50
    first_text, second_text = (user_text(c) for c in llm.calls if c[0] in (PLAN, PLAN_MORE))
    assert first_text.count("line of document") == 50 and "line of document 50." not in first_text
    assert second_text.count("line of document") == 2 and "line of document 50." in second_text and "line of document 51." in second_text


def test_a_second_window_of_one_document_makes_no_plan(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make_crow(bundle)

    # ACT
    report = wiki.ingest_many([item(n) for n in range(51)])

    # ASSERT
    assert filed_all(report, 51)
    assert plans(llm) == [PLAN]


def test_a_batch_of_one_document_makes_no_plan(bundle: Path) -> None:
    wiki, llm = make_crow(bundle)

    report = wiki.ingest_many([item(0)])

    assert filed_all(report, 1) and plans(llm) == []


def test_unchanged_documents_do_not_count_and_are_not_sent_to_the_plan(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make_crow(bundle)
    wiki.ingest_many([item(n) for n in range(3)])
    before = len(llm.calls)

    # ACT
    again = wiki.ingest_many([item(n) for n in range(5)])  # three known ones and two new

    # ASSERT
    assert filed_all(again, 5)
    new_plans = [c for c in llm.calls[before:] if c[0] in (PLAN, PLAN_MORE)]
    assert [c[0] for c in new_plans] == [PLAN_MORE]
    text = user_text(new_plans[0])
    assert text.count("line of document") == 2 and "line of document 3." in text and "line of document 4." in text
    assert "line of document 0." not in text


def test_a_window_with_one_new_document_among_unchanged_ones_makes_no_plan(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make_crow(bundle)
    wiki.ingest_many([item(n) for n in range(3)])
    before = len(llm.calls)

    # ACT
    again = wiki.ingest_many([item(n) for n in range(4)])

    # ASSERT
    assert filed_all(again, 4)
    assert [c[0] for c in llm.calls[before:] if c[0] in (PLAN, PLAN_MORE)] == []


@pytest.mark.parametrize(
    "failure",
    ["error", "malformed"],
)
def test_a_failing_plan_never_fails_the_batch(bundle: Path, failure: str, caplog: pytest.LogCaptureFixture) -> None:
    # ARRANGE
    wiki, llm = make_crow(bundle)
    if failure == "error":
        llm.plan_error = ModelError("down")
    else:
        llm.plan_reply = {"folders": "not a list"}

    # ACT
    with caplog.at_level(logging.WARNING):
        report = wiki.ingest_many([item(n) for n in range(3)])

    # ASSERT
    assert filed_all(report, 3)
    assert len(wiki.store.load().all_notes()) == 3
    assert any(r.levelno >= logging.WARNING for r in caplog.records)
    assert wiki.check() == []


def test_classic_mode_never_plans_or_splits(bundle: Path) -> None:
    # ARRANGE
    llm = PlanLLM(UsageTracker())
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", concurrency=1), llm=llm)
    wiki.init()

    # ACT
    report = wiki.ingest_many([item(n) for n in range(4)])

    # ASSERT
    assert filed_all(report, 4)
    assert not [op for op in llm.ops if op in (PLAN, PLAN_MORE, "librarian/split")]
