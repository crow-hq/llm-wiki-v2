# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A note the limit on top-level folders deviated is marked in its frontmatter, listed in the reports and the log, and `rebuild`
(CLI and MCP) files the documents again on the same folders with the deviations shown."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

from mcp import Client

from llmw2 import Item, Wiki, cli
from llmw2.agents.librarian import IngestResult
from llmw2.batch import BatchReport, deviation_line, deviations
from llmw2.bundle.tree import LOG
from llmw2.memory import Memory
from llmw2.sources import LocalFolderSource, SyncReport, documents, sync
from tests.wiki.test_crow_plan import filed_all
from tests.wiki.test_own_roots import make, own, roots

RED, GREEN = "topic: red-1\nRed things happen.", "topic: green-1\nGreen things happen."
TAKEN = "/blue"  # the root the fake classifier gives a note about green: its first option when None of these leads


def capped(bundle: Path) -> Wiki:
    """A CROW wiki whose two top-level folders are the limit: a note about green cannot open its own."""
    wiki, _ = make(bundle, max_top_folders=2)
    own(wiki, "red", "blue")
    return wiki


def docs(folder: Path) -> Path:
    folder.mkdir()
    (folder / "red.md").write_text(RED, encoding="utf-8")
    (folder / "green.md").write_text(GREEN, encoding="utf-8")
    return folder


def notes(wiki: Wiki) -> dict[str, Any]:
    return {n.rel: n.frontmatter.get("overflow") for n in wiki.store.load().all_notes()}


# -- the mark, the result, the log ----------------------------------------------------------


def test_a_note_the_limit_deviated_is_marked_in_its_frontmatter_its_result_and_the_log(bundle: Path) -> None:
    # ARRANGE
    wiki = capped(bundle)

    # ACT
    result = wiki.ingest(GREEN, title="Green")
    kept = wiki.ingest(RED, title="Red")

    # ASSERT
    mark = notes(wiki)[result.note]
    assert result.folder == TAKEN and set(mark) == {"p_none", "p_root", "at"}
    assert result.overflow == {"p_none": mark["p_none"], "p_root": mark["p_root"]}
    assert kept.overflow is None and notes(wiki)[kept.note] is None
    lines = [x for x in (bundle / LOG).read_text(encoding="utf-8").splitlines() if "Deviated note" in x]
    assert len(lines) == 1 and f"({result.note})" in lines[0]
    assert f"p_none {mark['p_none']}, p_root {mark['p_root']}" in lines[0] and f"filed in {TAKEN}" in lines[0] and "limit of 2" in lines[0]


def test_a_note_filed_where_it_was_routed_leaves_no_mark_and_no_log_line(bundle: Path) -> None:
    # ARRANGE
    wiki = capped(bundle)

    # ACT
    result = wiki.ingest(RED, title="Red")

    # ASSERT
    assert result.overflow is None and result.to_dict()["overflow"] is None
    assert "Deviated note" not in (bundle / LOG).read_text(encoding="utf-8") and deviations([result]) == []


def test_a_deviation_chosen_by_words_is_listed_without_probabilities() -> None:
    # ARRANGE
    result = IngestResult("created", "red/x.md", "X", "/red", [], [], [], overflow={})

    # ACT
    found = deviations([result])

    # ASSERT
    assert found == [{"note": "red/x.md", "root": "/red", "p_none": None, "p_root": None}]
    assert deviation_line(found[0]) == "red/x.md -> /red (chosen by words)"


# -- the batch report and the sync report -----------------------------------------------------


def test_the_batch_report_counts_and_lists_the_deviated_notes(bundle: Path) -> None:
    # ARRANGE
    wiki = capped(bundle)
    items = [Item("red.md", data=RED.encode()), Item("green.md", data=GREEN.encode())]

    # ACT
    report = wiki.ingest_many(items)

    # ASSERT
    assert filed_all(report, 2)
    [one] = report.deviations
    assert one["root"] == TAKEN and set(one) == {"note", "root", "p_none", "p_root"}
    as_dict = report.to_dict()
    assert as_dict["deviated"] == 1 and as_dict["deviations"] == [one]
    assert "deviated" not in BatchReport().to_dict() and "deviations" not in BatchReport().to_dict()


def test_the_cli_sync_prints_the_deviations(
    bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # ARRANGE
    wiki = capped(bundle)
    monkeypatch.setattr(cli, "make_wiki", lambda **_: wiki)
    folder = docs(tmp_path / "docs")

    # ACT
    code = cli.main(["--bundle", str(bundle), "sync", str(folder)])

    # ASSERT
    out = capsys.readouterr().out
    assert code == 0 and "created 2" in out and "deviated 1" in out
    assert out.count("deviated: blue/") == 1 and f"-> {TAKEN} (p_none" in out and "top-level folders" in out
    assert "deviations" not in out.splitlines()[0]


def test_the_cli_sync_prints_the_deviations_as_json(
    bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # ARRANGE
    wiki = capped(bundle)
    monkeypatch.setattr(cli, "make_wiki", lambda **_: wiki)

    # ACT
    cli.main(["--bundle", str(bundle), "sync", str(docs(tmp_path / "docs")), "--json"])

    # ASSERT
    report = json.loads(capsys.readouterr().out)
    assert report["deviated"] == 1 and report["deviations"][0]["root"] == TAKEN


def test_an_ingest_prints_its_deviation(
    bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # ARRANGE
    wiki = capped(bundle)
    monkeypatch.setattr(cli, "make_wiki", lambda **_: wiki)
    path = tmp_path / "green.md"
    path.write_text(GREEN, encoding="utf-8")

    # ACT
    cli.main(["--bundle", str(bundle), "ingest", str(path)])

    # ASSERT
    assert "deviated: blue/" in capsys.readouterr().out


# -- rebuild ------------------------------------------------------------------------------------


def test_rebuild_marks_a_note_a_root_took_in_place_of_a_new_one_and_leaves_the_others(bundle: Path) -> None:
    # ARRANGE
    wiki = capped(bundle)
    items = [Item("red.md", data=RED.encode()), Item("green.md", data=GREEN.encode())]
    wiki.ingest_many(items)

    # ACT
    report = wiki.rebuild(items)

    # ASSERT
    assert filed_all(report, 2) and roots(wiki) == ["blue", "red"]
    assert [d["note"] for d in report.deviations] == [n for n, mark in notes(wiki).items() if mark is not None]
    assert len(report.deviations) == 1


def test_rebuild_from_a_folder_needs_yes_and_then_a_sync_of_it_files_nothing_again(
    bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # ARRANGE
    wiki = capped(bundle)
    monkeypatch.setattr(cli, "make_wiki", lambda **_: wiki)
    folder = docs(tmp_path / "docs")
    sync(wiki, LocalFolderSource(folder))
    before = sorted(notes(wiki))
    capsys.readouterr()

    # ACT
    refused = cli.main(["--bundle", str(bundle), "rebuild", str(folder)])
    untouched = sorted(notes(wiki))
    done = cli.main(["--bundle", str(bundle), "rebuild", str(folder), "--yes"])
    out = capsys.readouterr().out
    again = sync(wiki, LocalFolderSource(folder))

    # ASSERT
    assert refused == 2 and untouched == before
    assert done == 0 and "created 2" in out and "deviated 1" in out and "deviated: blue/" in out
    assert sorted(notes(wiki)) == before and roots(wiki) == ["blue", "red"]
    assert (again.created, again.merged, again.skipped) == (0, 0, 2) and len(wiki.store.load().all_notes()) == 2


def test_rebuild_without_a_folder_files_the_raw_copies_again_and_a_sync_of_the_folder_adds_nothing(
    bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # ARRANGE
    wiki = capped(bundle)
    monkeypatch.setattr(cli, "make_wiki", lambda **_: wiki)
    folder = docs(tmp_path / "docs")
    sync(wiki, LocalFolderSource(folder))
    wiki.ingest("topic: blue-1\nA decision to remember.", title="Decision")
    before = {n.title for n in wiki.store.load().all_notes()}
    capsys.readouterr()

    # ACT
    refused = cli.main(["--bundle", str(bundle), "rebuild"])
    done = cli.main(["--bundle", str(bundle), "rebuild", "--yes"])
    again = sync(wiki, LocalFolderSource(folder))

    # ASSERT
    assert refused == 2 and done == 0 and "created 3" in capsys.readouterr().out
    assert {n.title for n in wiki.store.load().all_notes()} == before and roots(wiki) == ["blue", "red"]
    assert again.created == 0 and len(wiki.store.load().all_notes()) == 3


def test_raw_items_take_each_text_once(bundle: Path) -> None:
    # ARRANGE
    wiki = capped(bundle)
    wiki.ingest("topic: red-1\nFirst version.", title="Same")
    wiki.ingest("topic: red-1\nFirst version.", title="Same")

    # ACT
    items = wiki.raw_items()

    # ASSERT
    assert [i.filename for i in items] == ["Same.md"] and items[0].text == "topic: red-1\nFirst version."


def test_documents_lists_what_a_connector_has(tmp_path: Path) -> None:
    # ARRANGE
    folder = docs(tmp_path / "docs")

    # ACT
    items = documents(LocalFolderSource(folder))

    # ASSERT
    assert sorted(i.filename for i in items) == ["green.md", "red.md"] and all(i.origin is not None for i in items)
    assert "deviated" not in SyncReport().to_dict() and "deviations" not in SyncReport().to_dict()


# -- the MCP memory -------------------------------------------------------------------------------


def mcp(wiki: Wiki, tool: str, arguments: dict[str, Any]) -> Any:
    wiki.cfg.mcp = True

    async def go() -> Any:
        async with Client(Memory(lambda: wiki).server) as client:
            return await client.call_tool(tool, arguments)

    return asyncio.run(go())


def test_remember_reports_the_deviation_of_the_note_it_filed(bundle: Path) -> None:
    # ARRANGE
    wiki = capped(bundle)

    # ACT
    deviated = mcp(wiki, "remember", {"text": GREEN, "title": "Green"}).structured_content
    plain = mcp(wiki, "remember", {"text": RED, "title": "Red"}).structured_content

    # ASSERT
    assert deviated["deviated"] == 1 and deviated["deviations"][0]["root"] == TAKEN
    assert deviated["deviations"][0]["note"] == deviated["note"]
    assert "deviated" not in plain and "deviations" not in plain


def test_the_rebuild_tool_refuses_without_confirm_and_then_files_the_raw_copies_again(bundle: Path) -> None:
    # ARRANGE
    wiki = capped(bundle)
    mcp(wiki, "remember", {"text": GREEN, "title": "Green"})

    # ACT
    refused = mcp(wiki, "rebuild", {})
    also = mcp(wiki, "rebuild", {"confirm": False})
    done = mcp(wiki, "rebuild", {"confirm": True})

    # ASSERT
    assert refused.is_error and also.is_error and "confirm=true" in refused.content[0].text
    assert not done.is_error and done.structured_content["created"] == 1 and done.structured_content["deviated"] == 1
    assert done.structured_content["deviations"][0]["root"] == TAKEN and done.structured_content["stopped"] is None
    assert len(notes(wiki)) == 1 and roots(wiki) == ["blue", "red"]


def test_the_rebuild_tool_runs_as_a_job_with_the_clients_models(bundle: Path, llm: Any, classifier: Any) -> None:
    # ARRANGE
    from tests.wiki.test_memory import NOTE, client, client_wiki, through

    wiki = client_wiki(bundle, llm, classifier)
    memory = Memory(lambda: wiki)
    through(memory, "remember", {"text": "Revenue is booked on delivery."})

    def answer(request: dict[str, Any]) -> str:
        return '{"action": "select", "folder": "/finance"}' if request["step"] == "librarian/route" else client(request)

    # ACT
    refused, _ = through(memory, "rebuild", {})
    done, requests = through(memory, "rebuild", {"confirm": True}, answer)

    # ASSERT
    assert refused.is_error and not done.is_error and done.structured_content["status"] == "done"
    assert done.structured_content["created"] == 1 and [n.rel for n in wiki.store.load().all_notes()] == [NOTE]
    assert requests and (llm.ops, classifier.ops) == ([], [])
