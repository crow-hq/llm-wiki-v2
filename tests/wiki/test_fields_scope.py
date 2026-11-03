# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The core writes a document's `fields` in its note (steps here ignore them), keeps differing ones apart; `where` filters."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("mcp")

from mcp import Client

from llmw2 import Candidate, LocalFolderSource, StepContext, Wiki, WikiConfig, cli, sync
from llmw2.agents.base import fields_conflict
from llmw2.bundle.tree import Folder, Note, matching
from llmw2.memory import Memory
from llmw2.server import create_app
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_steps import OFFLINE, o_route

SEED = "finance/seed.md"
SEEN: list[list[str]] = []  # the notes the match step was given, per document


def match_first(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
    SEEN.append([n.rel for n in pool])
    return pool[0] if pool else None


def answer(ctx: StepContext, question: str, notes: list[Note]) -> str:
    return "Answer " + " ".join(f"[{n.rel}]" for n in notes)


STEPS = replace(
    OFFLINE,
    name="scoped",
    route=o_route,
    match=match_first,
    consolidate=lambda ctx, note, cand: True,
    answer=answer,
)


@pytest.fixture(autouse=True)
def clear() -> None:
    SEEN.clear()


def build(bundle: Path, llm: FakeLLM, seed: dict[str, Any] | None) -> Wiki:
    """A classic wiki with /finance/seed.md (with `seed` as its fields) and the steps above."""
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm, steps=STEPS)
    wiki.init()
    folder, _ = wiki.store.create_folder(wiki.store.load(), "finance", "Money in and out.")
    wiki.store.write_note(
        folder, title="Seed", summary="Seeded.", body="Seed.", tags=[], source={"resource": "/raw/seed.md", "title": "Seed"}, fields=seed
    )
    return wiki


def fields_of(wiki: Wiki, rel: str) -> Any:
    return wiki.store.read(rel).frontmatter.get("fields")


# -- the rule -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "conflict"),
    [
        ({"team": "A"}, {"team": "B"}, True),
        ({"team": "A", "year": 1}, {"team": "A", "year": 2}, True),
        ({"team": "A"}, {"team": "A"}, False),
        ({"team": "A"}, {"project": "B"}, False),  # no key in common
        ({"team": "A"}, {}, False),
        (None, {"team": "B"}, False),  # a note without fields
        ({"team": "A"}, None, False),
        ({"n": 1}, {"n": "1"}, True),  # compared as they are
    ],
)
def test_two_fields_conflict_only_on_a_shared_key_with_different_values(a: Any, b: Any, conflict: bool) -> None:
    assert fields_conflict(a, b) is conflict


def test_a_document_is_not_merged_into_a_note_whose_fields_differ_on_a_key(bundle: Path, llm: FakeLLM) -> None:
    # ARRANGE
    wiki = build(bundle, llm, {"team": "A"})
    before = wiki.store.read(SEED).body

    # ACT
    result = wiki.ingest("Plans of B.", title="Plans", fields={"team": "B"})

    # ASSERT
    assert result.action == "created" and result.note != SEED
    assert fields_of(wiki, result.note) == {"team": "B"}
    assert fields_of(wiki, SEED) == {"team": "A"} and wiki.store.read(SEED).body == before
    assert ("match", "code", "1 excluded by fields") in [(d.step, d.decider, d.choice) for d in result.decisions]
    assert SEEN == [[]]  # the match step was given a pool without the note


@pytest.mark.parametrize(
    ("seed", "fields"),
    [({"team": "A"}, {"team": "A"}), (None, {"team": "B"}), ({"team": "A"}, {"project": "x"}), ({"team": "A"}, {})],
)
def test_a_document_is_merged_into_a_note_with_the_same_fields_none_or_other_keys(
    bundle: Path, llm: FakeLLM, seed: dict[str, Any] | None, fields: dict[str, Any]
) -> None:
    wiki = build(bundle, llm, seed)

    result = wiki.ingest("More.", title="More", fields=fields)

    assert (result.action, result.note) == ("merged", SEED)
    assert "excluded" not in " ".join(d.choice for d in result.decisions)


def test_the_fields_of_a_document_are_written_to_its_note_and_laid_over_those_of_a_merged_one(bundle: Path, llm: FakeLLM) -> None:
    wiki = build(bundle, llm, {"team": "A", "level": 1})

    created = wiki.ingest("Other.", title="Other", fields={"team": "B"})
    merged = wiki.ingest("More.", title="More", fields={"team": "A", "year": 2})

    assert fields_of(wiki, created.note) == {"team": "B"}
    assert merged.note == SEED and fields_of(wiki, SEED) == {"team": "A", "level": 1, "year": 2}


def test_without_fields_the_trace_has_no_mention_of_them(bundle: Path, llm: FakeLLM) -> None:
    wiki = build(bundle, llm, None)

    result = wiki.ingest("More.", title="More")

    assert result.action == "merged" and [d for d in result.decisions if d.step == "match"] == []


# -- connectors -------------------------------------------------------------------------------


def test_a_folder_source_gives_its_fields_to_the_notes_through_sync(bundle: Path, llm: FakeLLM, tmp_path: Path) -> None:
    # ARRANGE
    folder = tmp_path / "docs"
    for team in ("a", "b"):
        (folder / team).mkdir(parents=True)
        (folder / team / f"{team}.md").write_text(f"Text of {team}.", encoding="utf-8")
    wiki = build(bundle, llm, None)
    source = LocalFolderSource(folder, fields=lambda rel: {"team": rel.parts[0]})

    # ACT
    report = sync(wiki, source)

    # ASSERT: the first merges into the note without fields and takes its team, the second is kept apart from it
    assert report.errors == [] and (report.created, report.merged) == (1, 1)
    teams = sorted(str(n.frontmatter["fields"]["team"]) for n in wiki.store.load().all_notes())
    assert teams == ["a", "b"]


def test_a_folder_source_sends_fields_only_when_it_is_given_a_function(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("Text.", encoding="utf-8")
    seen: list[PurePosixPath] = []

    def fields(rel: PurePosixPath) -> dict[str, Any]:
        seen.append(rel)
        return {"k": "v"}

    assert [c.fields for c in LocalFolderSource(tmp_path).changes(None).changes] == [{}]
    assert [c.fields for c in LocalFolderSource(tmp_path, fields).changes(None).changes] == [{"k": "v"}]
    assert seen == [PurePosixPath("a.md")]


# -- where -------------------------------------------------------------------------------------


def two_teams(bundle: Path, llm: FakeLLM) -> Wiki:
    wiki = build(bundle, llm, {"team": "A", "level": 3})
    folder = wiki.store.load().find("/finance")
    assert folder is not None
    wiki.store.write_note(folder, title="Beta", summary="b", body="b", tags=[], source={"resource": "/raw/b.md"}, fields={"team": "B"})
    wiki.store.write_note(folder, title="Plain", summary="p", body="p", tags=[], source={"resource": "/raw/p.md"})
    return wiki


def test_ask_with_where_reads_and_cites_only_the_notes_with_those_fields(bundle: Path, llm: FakeLLM) -> None:
    wiki = two_teams(bundle, llm)

    everything = wiki.ask("What is there?")
    only_a = wiki.ask("What is there?", where={"team": "A"})
    by_text = wiki.ask("What is there?", where={"level": "3"})  # compared as text
    none = wiki.ask("What is there?", where={"team": "C"})

    assert len(everything.notes) == 3
    assert only_a.notes == only_a.citations == [SEED] and by_text.notes == [SEED]
    assert none.notes == [] and none.text.startswith("The wiki has no")


def test_where_does_not_change_the_tree_a_session_shares(bundle: Path, llm: FakeLLM) -> None:
    wiki = two_teams(bundle, llm)

    def shape(root: Folder) -> list[tuple[str, list[int], list[int]]]:
        return [(f.rel, [id(n) for n in f.notes], [id(s) for s in f.subfolders]) for f in root.walk()]

    with wiki.store.session():
        before = wiki.store.load()
        was = shape(before)
        wiki.ask("What is there?", where={"team": "B"})

        assert shape(before) == was  # the tree `load` gave is as it was
        assert len(wiki.ask("What is there?").notes) == 3


def test_matching_copies_the_folders_and_leaves_out_those_with_no_note(bundle: Path, llm: FakeLLM) -> None:
    root = two_teams(bundle, llm).store.load()

    kept = matching(root, {"team": "A"})

    assert kept is not root and [f.rel for f in kept.walk()] == ["", "finance"]
    assert kept.find("/finance") is not root.find("/finance") and [n.rel for n in kept.all_notes()] == [SEED]
    assert matching(root, {"team": "C"}).all_notes() == []


def test_the_cli_asks_with_where_pairs(
    bundle: Path, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki = two_teams(bundle, llm)
    monkeypatch.setattr(cli, "make_wiki", lambda **_: wiki)

    assert cli.main(["--bundle", str(bundle), "ask", "What?", "--where", "team=B"]) == 0
    assert capsys.readouterr().out == "Answer [finance/beta.md]\n"
    assert cli.main(["--bundle", str(bundle), "ask", "What?", "--where", "team=A", "--where", "level=3"]) == 0
    assert capsys.readouterr().out == f"Answer [{SEED}]\n"


def test_the_cli_refuses_a_where_that_is_not_key_value(
    bundle: Path, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki = two_teams(bundle, llm)
    monkeypatch.setattr(cli, "make_wiki", lambda **_: wiki)

    assert cli.main(["--bundle", str(bundle), "ask", "What?", "--where", "team"]) == 2
    assert "--where wants KEY=VALUE" in capsys.readouterr().err


def test_the_server_asks_with_where(bundle: Path, llm: FakeLLM) -> None:
    client = TestClient(create_app(two_teams(bundle, llm)))

    found = client.post("/ask", json={"question": "What?", "where": {"team": "B"}}).json()
    every = client.post("/ask", json={"question": "What?"}).json()

    assert found["notes"] == ["finance/beta.md"] and len(every["notes"]) == 3


def test_recall_asks_with_where(bundle: Path, llm: FakeLLM) -> None:
    wiki = two_teams(bundle, llm)
    wiki.cfg.mcp = True

    async def go() -> Any:
        async with Client(Memory(lambda: wiki).server) as client:
            return await client.call_tool("recall", {"question": "What?", "where": {"team": "A"}})

    result = asyncio.run(go())

    assert not result.is_error and result.structured_content["notes"] == [SEED]
