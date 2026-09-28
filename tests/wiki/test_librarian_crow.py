# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The CROW Librarian (§5.1): the typed classifier routes, matches, consolidates and relates."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from okf_wiki import Wiki, WikiConfig
from okf_wiki.agents.base import Decision
from okf_wiki.agents.librarian import IngestResult
from okf_wiki.bundle.document import OKFDocument
from okf_wiki.bundle.tree import Note
from okf_wiki.errors import ModelError
from tests.wiki.fakes import FakeClassifier, FakeLLM

HERE, NEW, NONE = "Here", "New subfolder", "None of these"
MODIFY, NEW_NOTE = "Modify", "New note"
SUMMARIZE, NAME_FOLDER, MERGE = "librarian/summarize", "librarian/name_folder", "librarian/merge"
ROUTE_FALLBACK, ROUTE = "librarian/route_fallback", "librarian/route"


# -- helpers ----------------------------------------------------------------------


def draft(title: str, body: str = "# Overview\n\nThe facts.") -> dict[str, Any]:
    """A librarian/summarize (or librarian/merge) reply."""
    return {"title": title, "summary": f"What {title} says.", "tags": [], "body": body}


def make_wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier, **cfg: Any) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", **cfg), llm=llm, classifier=classifier)
    wiki.init()
    return wiki


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


def two_folders(wiki: Wiki) -> None:
    add_folder(wiki, "/", "alpha", "Alpha things.")
    add_folder(wiki, "/", "beta", "Beta things.")


def read(path: Path) -> OKFDocument:
    return OKFDocument.parse(path.read_text(encoding="utf-8"))


def criteria(classifier: FakeClassifier, call: int) -> dict[str, str]:
    """Options offered by a routing Choice."""
    return classifier.calls[call][2]["q"]["criteria"]


def noul_questions(classifier: FakeClassifier, op: str) -> list[str]:
    return [q["instructions"] for o, _, qs in classifier.calls if o == op for q in qs.values()]


def final_route(result: IngestResult) -> Decision:
    return next(d for d in result.decisions if d.step == "route")


def split_at_root(classifier: FakeClassifier) -> None:
    """A low-confidence root Choice between alpha and beta: both paths are explored."""
    classifier.add_choice("route", "alpha", {"alpha": 0.5, "beta": 0.4, HERE: 0.1}, 0.3)


# -- routing -------------------------------------------------------------------------


def test_root_offers_only_here_and_new_on_an_empty_wiki(
    crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    llm.add(SUMMARIZE, draft("Acme pricing"))
    classifier.add_choice("route", HERE, {HERE: 0.9, NEW: 0.1}, 0.9)

    result = crow_wiki.ingest("Acme raised prices.")

    assert list(criteria(classifier, 0)) == [HERE, NEW]
    assert (classifier.ops, llm.ops) == (["route"], [SUMMARIZE])
    assert (result.folder, result.note) == ("/", "acme-pricing.md")
    assert final_route(result) == Decision("route", "classifier", "/ (Here)", 0.9)


def test_nothing_to_decide_when_only_here_is_possible(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, max_depth=0)
    llm.add(SUMMARIZE, draft("Acme pricing"))

    result = wiki.ingest("Acme raised prices.")

    assert classifier.calls == []
    assert result.note == "acme-pricing.md"
    assert final_route(result) == Decision("route", "classifier", "/ (Here)", 1.0)


def test_new_subfolder_at_root_is_named_by_the_llm(
    crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier, bundle: Path
) -> None:
    llm.add(SUMMARIZE, draft("Acme pricing")).add(NAME_FOLDER, {"name": "pricing", "description": "Supplier prices."})
    classifier.add_choice("route", NEW, {HERE: 0.2, NEW: 0.8}, 0.8)

    result = crow_wiki.ingest("Acme raised prices.")

    assert llm.ops == [SUMMARIZE, NAME_FOLDER]
    assert (result.created_folders, result.note) == (["/pricing"], "pricing/acme-pricing.md")
    assert "* [pricing](pricing/index.md) - Supplier prices." in read(bundle / "index.md").body.splitlines()
    assert final_route(result) == Decision("route", "classifier", f"/ ({NEW})", 0.8)


def test_descend_offers_none_of_these_below_the_root(
    crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier, bundle: Path
) -> None:
    add_folder(crow_wiki, "/", "finance", "Money matters.")
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    classifier.add_choice("route", "finance", {"finance": 0.9, HERE: 0.05, NEW: 0.05}, 0.9)
    classifier.add_choice("route", HERE, {HERE: 0.9, NEW: 0.05, NONE: 0.05}, 0.9)

    result = crow_wiki.ingest("Revenue grew.")

    assert list(criteria(classifier, 0)) == ["finance", HERE, NEW]
    assert criteria(classifier, 0)["finance"] == "Money matters."
    assert list(criteria(classifier, 1)) == [HERE, NEW, NONE]
    assert (result.folder, result.note) == ("/finance", "finance/q3-revenue.md")
    assert (bundle / "finance" / "q3-revenue.md").is_file()
    assert final_route(result).confidence == pytest.approx(0.9)


def test_confident_choice_follows_a_single_path(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    two_folders(crow_wiki)
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    classifier.add_choice("route", "alpha", {"alpha": 0.7, "beta": 0.3}, 0.8)
    classifier.add_choice("route", HERE, {HERE: 0.9}, 0.9)

    result = crow_wiki.ingest("Revenue grew.")

    assert classifier.ops == ["route", "route"]
    assert [d.choice for d in result.decisions if d.step == "route_step"] == ["/: alpha", "/alpha: Here"]
    assert result.folder == "/alpha"


def test_low_confidence_explores_the_top_beam_options(
    crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    two_folders(crow_wiki)
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    split_at_root(classifier)
    classifier.add_choice("route", HERE, {HERE: 0.9}, 0.9)
    classifier.add_choice("route", HERE, {HERE: 0.6}, 0.9)

    result = crow_wiki.ingest("Revenue grew.")

    assert classifier.ops == ["route", "route", "route"]
    assert "/alpha" in classifier.calls[1][2]["q"]["instructions"]
    assert "/beta" in classifier.calls[2][2]["q"]["instructions"]
    assert result.decisions[0].choice == "/: alpha | beta" and result.decisions[0].confidence == 0.3
    assert result.folder == "/alpha"


def test_path_ending_in_none_of_these_is_discarded(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    two_folders(crow_wiki)
    add_note(crow_wiki, "/alpha", "Alpha note")
    add_note(crow_wiki, "/beta", "Beta note")
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    split_at_root(classifier)
    classifier.add_choice("route", NONE, {NONE: 0.95}, 0.95)
    classifier.add_choice("route", HERE, {HERE: 0.8}, 0.9)

    result = crow_wiki.ingest("Revenue grew.")

    assert result.folder == "/beta"
    assert final_route(result).confidence == pytest.approx(math.sqrt(0.4 * 0.8))
    matched = " ".join(noul_questions(classifier, "match"))
    assert '"Beta note"' in matched and '"Alpha note"' not in matched


def test_note_is_filed_at_the_end_of_the_best_path_by_geometric_mean(
    crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    add_folder(crow_wiki, "/", "alpha", "Alpha things.")
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    # Root "Here" scores 0.5; alpha then Here scores sqrt(0.45 * 0.98) ≈ 0.66 (a product would give 0.44).
    classifier.add_choice("route", HERE, {HERE: 0.5, "alpha": 0.45, NEW: 0.05}, 0.3)
    classifier.add_choice("route", HERE, {HERE: 0.98}, 0.9)

    result = crow_wiki.ingest("Revenue grew.")

    assert (result.folder, result.note) == ("/alpha", "alpha/q3-revenue.md")
    score = pytest.approx(math.sqrt(0.45 * 0.98))
    assert final_route(result) == Decision("route", "classifier", "/alpha (Here)", score)


def test_notes_of_every_finished_path_feed_find_match(
    crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    two_folders(crow_wiki)
    add_note(crow_wiki, "/", "Company overview")
    add_note(crow_wiki, "/alpha", "Alpha note")
    add_note(crow_wiki, "/beta", "Beta note")
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    split_at_root(classifier)
    classifier.add_choice("route", HERE, {HERE: 0.9}, 0.9)
    classifier.add_choice("route", HERE, {HERE: 0.6}, 0.9)

    crow_wiki.ingest("Revenue grew.")

    matched = noul_questions(classifier, "match")
    assert len(matched) == 3
    assert all(any(f'"{t}"' in q for q in matched) for t in ("Company overview", "Alpha note", "Beta note"))


# -- routing fallbacks ----------------------------------------------------------------------


def uncertain_alpha(classifier: FakeClassifier) -> None:
    """Best finished path alpha → Here scores sqrt(0.45 * 0.4) ≈ 0.42, below tau_path (0.5)."""
    classifier.add_choice("route", "alpha", {"alpha": 0.45, HERE: 0.35, NEW: 0.2}, 0.3)
    classifier.add_choice("route", HERE, {HERE: 0.4, NEW: 0.3, NONE: 0.3}, 0.9)


def test_low_best_score_falls_back_to_llm_select(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    add_folder(crow_wiki, "/", "alpha", "Alpha things.")
    llm.add(SUMMARIZE, draft("Q3 revenue")).add(ROUTE_FALLBACK, {"action": "select", "folder": "/alpha"})
    uncertain_alpha(classifier)

    result = crow_wiki.ingest("Revenue grew.")

    assert llm.ops == [SUMMARIZE, ROUTE_FALLBACK]
    offered = llm.calls[1][1][-1]["content"]
    assert "- /: (no description)" in offered and "- /alpha: Alpha things." in offered
    assert (result.folder, result.note, result.created_folders) == ("/alpha", "alpha/q3-revenue.md", [])
    expected = Decision("route", "llm", "/alpha (Here)", pytest.approx(math.sqrt(0.45 * 0.4)), fallback=True)
    assert final_route(result) == expected


def test_low_best_score_falls_back_to_llm_create(
    crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier, bundle: Path
) -> None:
    add_folder(crow_wiki, "/", "alpha", "Alpha things.")
    llm.add(SUMMARIZE, draft("Q3 revenue")).add(ROUTE_FALLBACK, {"action": "create", "folder": "/alpha"})
    llm.add(NAME_FOLDER, {"name": "revenue", "description": "Revenue reports."})
    uncertain_alpha(classifier)

    result = crow_wiki.ingest("Revenue grew.")

    assert llm.ops == [SUMMARIZE, ROUTE_FALLBACK, NAME_FOLDER]
    assert (result.created_folders, result.note) == (["/alpha/revenue"], "alpha/revenue/q3-revenue.md")
    assert (bundle / "alpha" / "revenue" / "q3-revenue.md").is_file()
    assert final_route(result).choice == f"/alpha ({NEW})" and final_route(result).fallback


def test_every_path_ending_in_none_falls_back(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    two_folders(crow_wiki)
    llm.add(SUMMARIZE, draft("Q3 revenue")).add(ROUTE_FALLBACK, {"action": "select", "folder": "/beta"})
    split_at_root(classifier)
    classifier.add_choice("route", NONE, {NONE: 0.9}, 0.9)
    classifier.add_choice("route", NONE, {NONE: 0.9}, 0.9)

    result = crow_wiki.ingest("Revenue grew.")

    assert classifier.ops == ["route", "route", "route"]
    assert llm.ops == [SUMMARIZE, ROUTE_FALLBACK]
    assert result.folder == "/beta"
    assert final_route(result) == Decision("route", "llm", "/beta (Here)", None, fallback=True)


def broken_classifier(monkeypatch: pytest.MonkeyPatch, classifier: FakeClassifier) -> None:
    def ask(*args: object, **kwargs: object) -> dict[str, dict[str, Any]]:
        raise ModelError("classifier unreachable")

    monkeypatch.setattr(classifier, "ask", ask)


def test_classifier_error_while_routing_falls_back_to_llm_route(
    crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken_classifier(monkeypatch, classifier)
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "here"})

    result = crow_wiki.ingest("Acme raised prices.")

    assert llm.ops == [SUMMARIZE, ROUTE]
    assert result.decisions == [
        Decision("route", "classifier", "error", None, fallback=True),
        Decision("route", "llm", "/ (Here)"),
    ]
    assert result.note == "acme-pricing.md"


def test_classifier_error_in_later_hooks_falls_back_to_llm(
    crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_note(crow_wiki, "/", "Acme pricing")
    broken_classifier(monkeypatch, classifier)
    llm.add(SUMMARIZE, draft("Acme price rise")).add(ROUTE, {"action": "here"})
    llm.add("librarian/find_match", {"match": "acme-pricing.md"}).add("librarian/consolidate", {"decision": "new"})
    llm.add("librarian/relate", {"related": ["acme-pricing.md"]})

    result = crow_wiki.ingest("Acme raised prices.")

    assert llm.ops == [
        SUMMARIZE,
        ROUTE,
        "librarian/find_match",
        "librarian/consolidate",
        "librarian/relate",
    ]
    assert (result.action, result.related) == ("created", ["acme-pricing.md"])


# -- consolidation ------------------------------------------------------------------------------


def fruit_wiki(wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    """Root notes Apple, Banana, Cherry; the classifier files the incoming note at the root."""
    for title in ("Apple", "Banana", "Cherry"):
        add_note(wiki, "/", title)
    llm.add(SUMMARIZE, draft("Fruit prices"))
    classifier.add_choice("route", HERE, {HERE: 0.9, NEW: 0.1}, 0.9)


def test_match_asks_one_noul_per_pool_note_and_the_best_wins(
    crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    fruit_wiki(crow_wiki, llm, classifier)
    classifier.noul_scores["match"] = {'"Apple"': 0.7, '"Banana"': 0.9, '"Cherry"': 0.2}
    classifier.add_choice("consolidate", NEW_NOTE, {MODIFY: 0.1, NEW_NOTE: 0.9}, 0.9)

    result = crow_wiki.ingest("Fruit got dearer.")

    assert classifier.ops == ["route", "match", "consolidate", "relate"]
    assert len(noul_questions(classifier, "match")) == 3
    scores = {"apple.md": 0.7, "banana.md": 0.9, "cherry.md": 0.2}
    assert Decision("match", "classifier", "banana.md", 0.9, scores=scores) in result.decisions
    assert "Title: Banana" in classifier.calls[2][1]


def test_no_note_above_tau_ing_creates_a_new_note(crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    fruit_wiki(crow_wiki, llm, classifier)
    classifier.noul_scores["match"] = {'"Apple"': 0.45, '"Banana"': 0.3}

    result = crow_wiki.ingest("Fruit got dearer.")

    assert classifier.ops == ["route", "match", "relate"]
    assert (result.action, result.note) == ("created", "fruit-prices.md")
    match = next(d for d in result.decisions if d.step == "match")
    assert (match.choice, match.confidence, match.scores["apple.md"]) == ("none", 0.45, 0.45)


@pytest.mark.parametrize(
    ("choice", "confidence", "action"),
    [(MODIFY, 0.9, "merged"), (MODIFY, 0.5, "created"), (NEW_NOTE, 0.9, "created")],
)
def test_consolidation_merges_only_on_a_confident_modify(
    crow_wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier, bundle: Path, choice: str, confidence: float, action: str
) -> None:
    add_note(crow_wiki, "/", "Apple")
    llm.add(SUMMARIZE, draft("Apple prices")).add(MERGE, draft("Apple", body="# Overview\n\nMerged."))
    classifier.add_choice("route", HERE, {HERE: 0.9, NEW: 0.1}, 0.9)
    classifier.noul_scores["match"] = {'"Apple"': 0.9}
    classifier.add_choice("consolidate", choice, {choice: confidence}, confidence)

    result = crow_wiki.ingest("Apples got dearer.")

    merged = action == "merged"
    assert result.action == action
    assert result.note == ("apple.md" if merged else "apple-prices.md")
    assert llm.ops == ([SUMMARIZE, MERGE] if merged else [SUMMARIZE])
    expected = Decision("consolidate", "classifier", "modify" if merged else "new", confidence, scores={choice: confidence})
    assert expected in result.decisions
    assert len(read(bundle / "apple.md").frontmatter["sources"]) == (2 if merged else 1)


# -- relating ----------------------------------------------------------------------------------


def test_relate_links_notes_above_tau_link_capped_at_max_links(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier, max_links=2)
    fruit_wiki(wiki, llm, classifier)
    add_note(wiki, "/", "Date")
    classifier.noul_scores["relate"] = {'"Apple"': 0.9, '"Banana"': 0.7, '"Cherry"': 0.8, '"Date"': 0.55}

    result = wiki.ingest("Fruit got dearer.")

    assert len(noul_questions(classifier, "relate")) == 4
    assert result.related == ["apple.md", "cherry.md"]
    back_link = "* [Fruit prices](fruit-prices.md) - What Fruit prices says."
    assert back_link in read(bundle / "apple.md").body.splitlines()
    assert back_link in read(bundle / "cherry.md").body.splitlines()
    assert "# See also" not in read(bundle / "banana.md").body
    assert "# See also" not in read(bundle / "date.md").body

