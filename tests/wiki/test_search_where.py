# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki.search` with `where` reads the fields the index kept, not the tree, and returns what the tree filter would."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.bundle.tree import matching
from llmw2.models.usage import UsageTracker
from llmw2.search import Hit
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_search import Embedder
from tests.wiki.test_search_follows_files import _age

DOCS = [
    ("Alpha", "pricing of plans", "pricing pricing plans sales", {"teams": ["sales", "ops"], "year": 2024, "owner": "ana"}),
    ("Beta", "pricing of shops", "pricing shops ops", {"teams": ["ops"], "year": [2023, 2024], "owner": "bruno"}),
    ("Gamma", "pricing of the rest", "pricing rest", {"teams": [], "owner": "ana"}),
    ("Delta", "pricing no fields", "pricing delta delta delta", None),
    ("Epsilon", "pricing odd fields", "pricing epsilon", {"odd": 1}),  # rewritten below: `fields` that is not a mapping
    ("Zeta", "pricing none value", "pricing zeta pricing", {"owner": None, "flag": True}),
    ("Eta", "other topic", "weather rain clouds", {"owner": "ana", "teams": ["sales"]}),
]
WHERES: list[dict[str, Any]] = [
    {"owner": "ana"},
    {"teams": "ops"},
    {"teams": "sales", "owner": "ana"},
    {"year": 2024},
    {"year": "2023"},
    {"year": "2024.0"},
    {"flag": "True"},
    {"flag": True},
    {"owner": "None"},
    {"owner": "nobody"},
    {"missing": "x"},
    {"teams": "['sales', 'ops']"},
    {"owner": "ana", "missing": "x"},
]


def build(tmp_path: Path, embedder: Embedder | None = None) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=tmp_path / "wiki", mode="classic"), llm=FakeLLM(UsageTracker()), embedder=embedder)
    wiki.init()
    for title, summary, body, fields in DOCS:
        wiki.store.write_note(
            wiki.store.load(), title=title, summary=summary, body=body, tags=[], source={"resource": "/raw/x.md"}, fields=fields
        )
    odd = tmp_path / "wiki" / "epsilon.md"
    odd.write_text(re.sub(r"fields:\n(  .*\n)+", "fields: not a mapping\n", odd.read_text(encoding="utf-8")), encoding="utf-8")
    return wiki


def reference(wiki: Wiki, query: str, k: int, where: dict[str, Any]) -> list[str]:
    """What the search did before: the tree filtered by `matching`, ranked as an index of just those notes."""
    root = wiki.store.load()
    return [n.rel for n in wiki._search.search(matching(root, where).all_notes(), root.all_notes(), query, k)]


def paths(hits: list[Hit]) -> list[str]:
    return [h.path for h in hits]


@pytest.mark.parametrize("where", WHERES)
@pytest.mark.parametrize("query", ["pricing", "pricing delta epsilon zeta", "weather", "zzz"])
def test_where_gives_what_the_tree_filter_gives(tmp_path: Path, where: dict[str, Any], query: str) -> None:
    wiki = build(tmp_path)

    for k in (1, 3, 8):
        assert paths(wiki.search(query, k=k, where=where)) == reference(wiki, query, k, where)


def test_where_does_not_load_the_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki = build(tmp_path)
    monkeypatch.setattr(wiki.store, "load", lambda: (_ for _ in ()).throw(AssertionError("tree loaded")))

    assert paths(wiki.search("pricing", k=5, where={"teams": "ops"})) == ["alpha.md", "beta.md"]


def test_where_ranks_as_an_index_of_just_the_matching_notes(tmp_path: Path) -> None:
    wiki = build(tmp_path)

    assert paths(wiki.search("pricing", k=5, where={"owner": "ana"})) == ["alpha.md", "gamma.md"]
    assert wiki.search("pricing", k=5, where={"missing": "x"}) == []
    assert paths(wiki.search("pricing", k=2, where={"owner": "None"})) == ["zeta.md"]


def test_a_note_with_no_fields_or_odd_fields_matches_no_where(tmp_path: Path) -> None:
    wiki = build(tmp_path)

    assert {"delta.md", "epsilon.md"}.isdisjoint(paths(wiki.search("pricing", k=8, where={"owner": "ana"})))
    assert {"delta.md", "epsilon.md"} <= set(paths(wiki.search("pricing", k=8)))


def test_where_follows_a_write_an_edit_and_a_deletion(tmp_path: Path) -> None:
    wiki = build(tmp_path)
    assert paths(wiki.search("pricing", k=8, where={"owner": "carla"})) == []

    note = wiki.store.write_note(
        wiki.store.load(),
        title="Theta",
        summary="pricing",
        body="pricing theta",
        tags=[],
        source={"resource": "/raw/t.md"},
        fields={"owner": "carla"},
    )
    assert paths(wiki.search("pricing", k=8, where={"owner": "carla"})) == ["theta.md"]

    wiki.store.update_note(
        note, title="Theta", summary="pricing", body="pricing theta", tags=[], source={}, fields={"owner": "dario", "teams": ["ops"]}
    )
    assert paths(wiki.search("pricing", k=8, where={"owner": "carla"})) == []
    assert paths(wiki.search("pricing", k=8, where={"owner": "dario", "teams": "ops"})) == ["theta.md"]

    wiki.store.delete_note("theta.md")
    assert paths(wiki.search("pricing", k=8, where={"owner": "dario"})) == []


@pytest.mark.parametrize("embedded", [False, True])
def test_a_tree_older_than_a_write_given_to_the_researchers_search_does_not_undo_the_write(tmp_path: Path, embedded: bool) -> None:
    """`ask` ranks the notes of the tree it loaded; a tree loaded before a write holds no new note, and must not take it out of `search`."""
    wiki = build(tmp_path, Embedder({"pricing": [1.0, 0.0, 0.0]}) if embedded else None)
    for f in (tmp_path / "wiki").glob("*.md"):
        _age(f)
    older = wiki.store.load()
    wiki.store.write_note(
        wiki.store.load(),
        title="Fresh zorblax",
        summary="pricing",
        body="zorblax pricing",
        tags=[],
        source={"resource": "/raw/y.md"},
        fields={"owner": "carla"},
    )
    _age(tmp_path / "wiki" / "fresh-zorblax.md")
    for _ in range(2):  # the second listing finds the folder as it was
        assert "fresh-zorblax.md" in paths(wiki.search("zorblax", k=8))
        assert "fresh-zorblax.md" in paths(wiki.search("zorblax", k=8, where={"owner": "carla"}))

    wiki._search.search(older.all_notes(), older.all_notes(), "pricing", 3)  # the researcher's path, with the older tree

    assert "fresh-zorblax.md" in paths(wiki.search("zorblax", k=8))
    assert "fresh-zorblax.md" in paths(wiki.search("zorblax", k=8, where={"owner": "carla"}))
    assert "fresh-zorblax.md" in paths(wiki.search("pricing", k=8, where={"owner": "carla"}))


def test_a_field_changed_or_taken_away_by_another_program_is_seen(tmp_path: Path) -> None:
    wiki = build(tmp_path)
    assert paths(wiki.search("pricing", k=8, where={"owner": "bruno"})) == ["beta.md"]
    note = tmp_path / "wiki" / "beta.md"
    text = note.read_text(encoding="utf-8")
    note.write_text(text.replace("owner: bruno", "owner: zed"), encoding="utf-8")

    assert paths(wiki.search("pricing", k=8, where={"owner": "bruno"})) == []
    assert paths(wiki.search("pricing", k=8, where={"owner": "zed"})) == ["beta.md"]

    note.write_text(re.sub(r"fields:\n(  .*\n)+", "", note.read_text(encoding="utf-8")), encoding="utf-8")

    assert paths(wiki.search("pricing", k=8, where={"owner": "zed"})) == []
    assert "beta.md" in paths(wiki.search("pricing", k=8))


def test_a_filter_that_keeps_every_note_gives_the_search_without_one(tmp_path: Path) -> None:
    wiki = build(tmp_path)
    for f in (tmp_path / "wiki").glob("*.md"):
        f.write_text(f.read_text(encoding="utf-8").replace("fields:", "fields:\n  all: x", 1), encoding="utf-8")

    for query in ("pricing", "weather", "pricing delta"):
        assert paths(wiki.search(query, k=8, where={"all": "x"})) == reference(wiki, query, 8, {"all": "x"})
    assert {"alpha.md", "beta.md"} <= set(paths(wiki.search("pricing", k=8, where={"all": "x"})))


def test_where_with_an_embedder_matches_the_tree_filter(tmp_path: Path) -> None:
    wiki = build(tmp_path, Embedder({"pricing": [1.0, 0.0, 0.0], "weather": [0.0, 1.0, 0.0]}))

    for where in WHERES:
        assert paths(wiki.search("weather", k=3, where=where)) == reference(wiki, "weather", 3, where)


def test_any_number_of_different_wheres_reads_the_fields_once_until_the_file_changes(tmp_path: Path) -> None:
    """The notes are grouped by their fields text once: forty different `where` (more than any small cache of answers) read `docbody`
    once, and a write reads it again."""
    wiki = build(tmp_path)
    assert wiki.search("pricing", k=3)  # the index is there, no `where` asked yet
    db = wiki._search.index._open()
    seen: list[str] = []
    db.set_trace_callback(seen.append)
    try:
        wheres = [{"owner": f"nobody{i}"} for i in range(36)] + WHERES[:4]
        for where in wheres:
            assert paths(wiki.search("pricing", k=8, where=where)) == reference(wiki, "pricing", 8, where)
        scans = [s for s in seen if "FROM docbody" in s and "fields" in s]
        assert len(scans) == 1
        wiki.store.write_note(
            wiki.store.load(), title="Iota", summary="pricing", body="pricing iota", tags=[], source={"resource": "/raw/i.md"},
            fields={"owner": "nobody3"},
        )
        assert paths(wiki.search("pricing", k=8, where={"owner": "nobody3"})) == ["iota.md"]
        assert len([s for s in seen if "FROM docbody" in s and "fields" in s]) == 2
    finally:
        db.set_trace_callback(None)
