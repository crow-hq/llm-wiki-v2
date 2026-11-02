# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Top-level areas made by hand (`create_folder`) count in `max_top_folders`: with as many as the limit the wiki is closed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from llmw2 import Item, Origin, Wiki, WikiConfig
from llmw2.agents import steps_crow
from llmw2.agents.data import Candidate, Route
from llmw2.models.usage import UsageTracker
from tests.wiki.test_crow_plan import (
    DOCS,
    OLLAMA,
    PLAN_MORE_TEXT,
    PlanLLM,
    TopicClassifier,
    filed_all,
    folders_json,
    user_text,
)

OPEN = """a new top-level area only for a broad area none of the existing ones
covers (never a single client, supplier, project or product: that is a subfolder of the broad area it belongs to), a new"""
FIXED = "The top-level areas are fixed: never propose a new one; put every new folder under the area it belongs to. A new"
LINES = ["- (untitled): Acme raised prices.", "- Acme terms: New terms."]


def make(bundle: Path, **cfg: Any) -> tuple[Wiki, PlanLLM]:
    llm = PlanLLM(UsageTracker())
    config = WikiConfig(bundle=bundle, mode="crow", concurrency=1, **cfg)
    wiki = Wiki(config, llm=llm, classifier=TopicClassifier(llm.usage))
    wiki.init()
    return wiki, llm


def own(wiki: Wiki, *names: str) -> None:
    for name in names:
        wiki.create_folder("/", name, f"{name.capitalize()} things.")


def roots(wiki: Wiki) -> list[str]:
    return [f.name for f in wiki.store.load().subfolders]


def expected(roots_text: str, tree: str) -> str:
    text = PLAN_MORE_TEXT.replace("<tree>", tree).replace("<documents>", "\n".join(LINES)).replace("<depth>", "3")
    return text.replace(OPEN, roots_text)


# -- the prompt ------------------------------------------------------------------------------


def test_plan_more_says_the_areas_are_fixed_when_the_roots_are_at_the_limit(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make(bundle, max_top_folders=2)
    own(wiki, "alpha", "beta")

    # ACT
    wiki.plan(DOCS)

    # ASSERT
    tree = "- /alpha: Alpha things.; 0 notes\n- /beta: Beta things.; 0 notes"
    assert user_text(llm.calls[0]) == expected(FIXED, tree)
    assert "a new top-level area only" not in user_text(llm.calls[0])


def test_plan_more_keeps_its_text_when_the_roots_are_below_the_limit(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make(bundle, max_top_folders=3)
    own(wiki, "alpha", "beta")

    # ACT
    wiki.plan(DOCS)

    # ASSERT
    tree = "- /alpha: Alpha things.; 0 notes\n- /beta: Beta things.; 0 notes"
    assert user_text(llm.calls[0]) == expected(OPEN, tree) == PLAN_MORE_TEXT.replace("<tree>", tree).replace(
        "<documents>", "\n".join(LINES)
    ).replace("<depth>", "3")
    assert "are fixed" not in user_text(llm.calls[0])


# -- a plan on the roots given ---------------------------------------------------------------


def test_roots_made_by_hand_up_to_the_limit_drop_a_proposed_root_with_its_subfolders(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make(bundle, max_top_folders=2)
    own(wiki, "alpha", "beta")
    llm.plan_more_reply = folders_json(("/gamma", "Gamma."), ("/gamma/sub", "Sub."), ("/alpha/sub", "Sub of alpha."))

    # ACT
    made = wiki.plan(DOCS)

    # ASSERT
    assert [f.label for f in made] == ["/alpha/sub"] and roots(wiki) == ["alpha", "beta"]


def test_create_folder_over_the_limit_works_and_then_the_wiki_is_closed(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = make(bundle, max_top_folders=2)

    # ACT
    own(wiki, "alpha", "beta", "gamma")
    llm.plan_more_reply = folders_json(("/delta", "Delta."), ("/gamma/sub", "Sub."))
    made = wiki.plan(DOCS)

    # ASSERT
    assert [f.label for f in made] == ["/gamma/sub"] and roots(wiki) == ["alpha", "beta", "gamma"]
    assert FIXED in user_text(llm.calls[0])


# -- routing and the groups ------------------------------------------------------------------


def test_a_route_that_opens_a_root_goes_to_the_likeliest_given_root(bundle: Path) -> None:
    # ARRANGE
    wiki, _ = make(bundle, max_top_folders=2)
    own(wiki, "red", "blue")
    tree = wiki.store.load()
    ctx = wiki.librarian().ctx
    cand = Candidate("A note", "", [], "topic: blue-1")

    # ACT
    new = steps_crow.cap_roots(ctx, tree, cand, Route(tree, True, [tree]))
    create = steps_crow.cap_roots(ctx, tree, cand, Route(tree, False, [tree], [("brand-new", "New.")]))

    # ASSERT: the classifier's flat Choice puts the note in /blue, neither route opens a root
    assert new.folder.label == "/blue" and not new.new
    assert create.folder.label == "/blue" and not create.create and sorted(roots(wiki)) == ["blue", "red"]


def test_plan_groups_opens_no_root_when_the_given_roots_are_the_limit(bundle: Path) -> None:
    # ARRANGE
    llm = PlanLLM(UsageTracker())
    config = WikiConfig(bundle=bundle, mode="crow", concurrency=1, llm=OLLAMA, plan="groups", max_top_folders=1)
    wiki = Wiki(config, llm=llm, classifier=TopicClassifier(llm.usage))
    wiki.init()
    own(wiki, "roast")
    texts = {
        "roast-1": "Aurora roast profile: the Aurora espresso blend is roasted to 212 degrees, first crack at nine minutes.",
        "roast-2": "Aurora roast adjustments after cupping: the Aurora espresso blend roasted darker, first crack later.",
        "pay-1": "Payroll calendar: salaries are paid on the 27th, payroll closes on the 20th, payslips by email.",
        "pay-2": "Payroll changes: the payroll closing day moves to the 18th, salaries paid on the 27th, payslips unchanged.",
    }
    items = [Item(f"{t}.md", data=f"topic: {t}\n{text}".encode(), origin=Origin("test", "acct", t)) for t, text in texts.items()]

    # ACT
    report = wiki.ingest_many(items)

    # ASSERT
    assert filed_all(report, 4) and roots(wiki) == ["roast"]


def test_rebuild_keeps_the_roots_given(bundle: Path) -> None:
    # ARRANGE
    wiki, _ = make(bundle, max_top_folders=3)
    own(wiki, "red", "blue", "green")
    item = Item("doc.md", data=b"topic: red-1\nOne line.", origin=Origin("test", "acct", "doc"))
    assert filed_all(wiki.ingest_many([item]), 1)

    # ACT
    report = wiki.rebuild([item])

    # ASSERT: the empty roots stay, and the note goes to the one it was in
    assert filed_all(report, 1) and sorted(roots(wiki)) == ["blue", "green", "red"]
    assert [n.folder.label for n in wiki.store.load().all_notes() if n.folder is not None] == ["/red"]
