# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The route fallback menu: only the folders the classifier visited ("visited"), or those first and then the rest of the tree ("tree")."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from llmw2 import Wiki, WikiConfig
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import CROW, decision_hash
from llmw2.config import CrowConfig
from llmw2.models.usage import UsageTracker
from llmw2.settings import FIELDS
from tests.wiki.fakes import FakeClassifier, FakeLLM

HERE, NEW, NONE = "Here", "New subfolder", "None of these"
SUMMARIZE, ROUTE_FALLBACK = "librarian/summarize", "librarian/route_fallback"
FALLBACK_MENU_CHARS = 8000
NL = chr(10)


# -- helpers ----------------------------------------------------------------------


def tag(rel: str) -> str:
    return "D:" + rel.replace("/", "_").replace("-", "_")


def run(bundle: Path, fallback_menu: str | None, extra: int = 0, pad: int = 0) -> tuple[Wiki, list[str]]:
    """Ingest one note into /alpha (with /alpha/deep), /beta, /gamma and `extra` more top-level folders, each described by its tag.

    The root answer follows /alpha with a low probability: the path stays under tau_path (0.5) and the LLM is asked.
    Returns the wiki and the folder rows ("- /path: description") of the prompt the fallback sent to the LLM.
    """
    tracker = UsageTracker()
    llm, classifier = FakeLLM(tracker), FakeClassifier(tracker)
    crow = CrowConfig(beam=1) if fallback_menu is None else CrowConfig(beam=1, fallback_menu=fallback_menu)  # type: ignore[arg-type]
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", crow=crow), llm=llm, classifier=classifier)
    wiki.init()
    root = wiki.store.load()
    alpha, _ = wiki.store.create_folder(root, "alpha", f"{tag('alpha')} things.")
    wiki.store.create_folder(alpha, "deep", f"{tag('alpha/deep')} things.")
    wiki.store.create_folder(root, "beta", f"{tag('beta')} things.")
    wiki.store.create_folder(root, "gamma", f"{tag('gamma')} things.")
    for i in range(extra):
        name = f"extra{i:02d}"
        wiki.store.create_folder(root, name, f"{tag(name)} " + "x" * pad)
    llm.add(SUMMARIZE, {"title": "Q3 revenue", "summary": "Revenue.", "tags": [], "body": "# Overview\n\nFacts."})
    llm.add(ROUTE_FALLBACK, {"action": "select", "folder": "/alpha"})
    classifier.add_choice("route", "alpha", {"alpha": 0.2, "beta": 0.1, NEW: 0.1}, 0.9)
    for _ in range(3):
        classifier.add_choice("route", HERE, {HERE: 0.9, NEW: 0.05, NONE: 0.05}, 0.9)

    wiki.ingest("Revenue grew.")

    prompts = [messages for op, messages in llm.calls if op == ROUTE_FALLBACK]
    assert len(prompts) == 1, "the route must have become uncertain and asked the LLM once"
    text = "\n".join(m["content"] for m in prompts[0])
    return wiki, [line for line in text.splitlines() if line.startswith("- /")]


def tags_of(rows: list[str]) -> list[str]:
    """The tag of each row; the root row has none."""
    return [m.group(0) if (m := re.search(r"D:\w+", row)) else "root" for row in rows]


def digest(tmp_path: Path, **crow: Any) -> str:
    return decision_hash(CROW, WikiConfig(bundle=tmp_path, mode="crow", crow=CrowConfig(**crow)), Prompts())


# -- config -----------------------------------------------------------------------


def test_config_defaults_when_nothing_is_set_then_the_menu_is_the_visited_folders() -> None:
    assert CrowConfig().fallback_menu == "visited"


@pytest.mark.parametrize("value", ["visited", "tree"])
def test_from_env_when_okf_fallback_menu_is_set_then_the_field_takes_it(tmp_path: Path, value: str) -> None:
    config = WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_FALLBACK_MENU": value})

    assert config.crow.fallback_menu == value


def test_from_env_when_okf_fallback_menu_is_not_a_known_value_then_raises(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="fallback_menu"):
        WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_FALLBACK_MENU": "everything"})


def test_config_when_the_menu_is_not_a_known_value_then_raises() -> None:
    with pytest.raises(ValidationError):
        CrowConfig(fallback_menu="all")  # type: ignore[arg-type]


def test_settings_fields_when_listed_then_the_fallback_menu_is_among_them() -> None:
    assert FIELDS["crow_fallback_menu"][-1] == "fallback_menu"


def test_decision_hash_when_the_fallback_menu_changes_then_the_hash_changes(tmp_path: Path) -> None:
    assert digest(tmp_path) == digest(tmp_path, fallback_menu="visited")
    assert digest(tmp_path) != digest(tmp_path, fallback_menu="tree")


# -- the menu ---------------------------------------------------------------------


def test_fallback_menu_when_visited_then_only_the_folders_the_classifier_visited(tmp_path: Path) -> None:
    _, rows = run(tmp_path / "w", "visited")

    assert rows
    assert set(tags_of(rows)) <= {"root", tag("alpha")}, rows  # beta, gamma and alpha/deep were never visited


def test_fallback_menu_when_not_set_then_it_is_the_visited_menu(tmp_path: Path) -> None:
    _, default = run(tmp_path / "a", None)
    _, visited = run(tmp_path / "b", "visited")

    assert default == visited


def test_fallback_menu_when_tree_then_the_visited_rows_come_first_unchanged(tmp_path: Path) -> None:
    _, visited = run(tmp_path / "a", "visited")
    _, tree = run(tmp_path / "b", "tree")

    assert tree[: len(visited)] == visited and len(tree) > len(visited)


def test_fallback_menu_when_tree_then_every_other_folder_follows_once_in_walk_order(tmp_path: Path) -> None:
    wiki, rows = run(tmp_path / "w", "tree")
    visited = {"root", tag("alpha")}
    walk = [tag(f.rel) if f.rel else "root" for f in wiki.store.load().walk()]

    got = tags_of(rows)

    assert len(got) == len(set(got))  # nobody twice
    others = [t for t in got if t not in visited]
    assert others == [t for t in walk if t not in visited]  # alpha/deep, beta, gamma: all of them, in walk order
    assert {tag("alpha/deep"), tag("beta"), tag("gamma")} <= set(others)


def test_fallback_menu_when_tree_and_the_wiki_is_large_then_the_limit_omits_only_unvisited_folders(tmp_path: Path) -> None:
    # ARRANGE/ACT: 60 extra rows of about 330 characters: far over the limit
    wiki, rows = run(tmp_path / "w", "tree", extra=60, pad=300)

    # ASSERT
    got = tags_of(rows)
    assert tag("alpha") in got  # a visited folder is never omitted
    assert len("\n".join(rows)) <= FALLBACK_MENU_CHARS
    rank = {tag(f.rel): n for n, f in enumerate(wiki.store.load().walk()) if f.rel}
    others = [t for t in got if t not in ("root", tag("alpha"))]
    assert [rank[t] for t in others] == sorted(rank[t] for t in others)  # what is kept stays in walk order
    omitted = [t for t in rank if t.startswith("D:extra") and t not in got]
    assert omitted, "the menu must have been cut"
    long_row = len(next(r for r in rows if "D:extra" in r))
    assert 8000 - len(NL.join(rows)) <= long_row + 1, "a long row was left out although it fitted"
    assert len([t for t in others if t.startswith("D:extra")]) >= 15, "only what does not fit may be omitted"
