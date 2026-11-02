# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The author of a change: declared by the caller, written at the end of the log.md lines, and invisible to every reader of them."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig, cli
from llmw2.audit import last_split_at, taxonomy_health
from llmw2.bundle.check import check
from llmw2.bundle.store import clean_author
from llmw2.bundle.tree import LOG
from llmw2.sources import LocalFolderSource, sync
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_ingest_many import item, make_wiki
from tests.wiki.test_managed_roots import Built
from tests.wiki.test_server import script_ingest

BY = " · by "


def lines(wiki: Wiki) -> list[str]:
    """The entries of log.md, as written."""
    return [line for line in (wiki.cfg.bundle / LOG).read_text(encoding="utf-8").splitlines() if line.startswith("* **")]


def authors(wiki: Wiki) -> list[str | None]:
    return [line.partition(BY)[2] or None for line in lines(wiki)]


@pytest.fixture
def built(classic_wiki: Wiki) -> Built:
    return Built(classic_wiki)


# -- every method that changes the wiki ---------------------------------------------------------


def test_ingest_writes_the_author_and_the_calls_wins_over_the_wikis(classic_wiki: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    script_ingest(llm)
    classic_wiki.author = "wiki-author"

    # ACT
    classic_wiki.ingest("Revenue is booked on delivery.", title="Memo", author="ada")

    # ASSERT
    assert lines(classic_wiki)[0].endswith(f"{BY}ada")
    assert set(authors(classic_wiki)) == {"ada"}  # the folder it opened, too


def test_the_wikis_author_is_used_when_the_call_names_none(bundle: Path, llm: FakeLLM) -> None:
    # ARRANGE
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm, author="grace")
    wiki.init()
    script_ingest(llm)

    # ACT
    wiki.ingest("Revenue is booked on delivery.")

    # ASSERT
    assert set(authors(wiki)) == {"grace"}


def test_without_an_author_the_log_is_what_it_always_was(classic_wiki: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    script_ingest(llm)

    # ACT
    classic_wiki.ingest("Revenue is booked on delivery.")

    # ASSERT
    assert lines(classic_wiki) and BY not in (classic_wiki.cfg.bundle / LOG).read_text(encoding="utf-8")


def test_ingest_file_and_ingest_many_write_the_author_from_every_lane(bundle: Path) -> None:
    # ARRANGE
    wiki = make_wiki(bundle, concurrency=3)

    # ACT
    wiki.ingest_file(b"topic: solo\nline.", "solo.md", author="ada")
    report = wiki.ingest_many([item(n) for n in range(1, 7)], author="lin")

    # ASSERT
    assert not report.not_done and len(report.results) == 6
    assert authors(wiki).count("lin") >= 6 and authors(wiki).count("ada") >= 1
    assert set(authors(wiki)) == {"ada", "lin"}


def test_rebuild_and_clear_write_the_author(bundle: Path) -> None:
    # ARRANGE
    wiki = make_wiki(bundle)
    wiki.ingest_many([item(1), item(2)])

    # ACT
    wiki.rebuild(wiki.raw_items(), author="ada")
    rebuilt = authors(wiki)
    wiki.clear(author="lin")

    # ASSERT: a rebuild empties the log, then files again; a clear leaves its own line alone
    assert rebuilt and set(rebuilt) == {"ada"}
    assert len(lines(wiki)) == 1 and authors(wiki) == ["lin"]


def test_folders_notes_and_areas_write_the_author(built: Built) -> None:
    # ARRANGE
    wiki = built.wiki
    tea = built.tea.rel

    # ACT
    wiki.create_folder("/", "labs", "Experiments.", author="a1")
    wiki.create_area("sales", "Selling.", author="a2")
    wiki.describe_folder("/legal", "Contracts and terms.", author="a3")
    wiki.move_note(tea, "/ops", author="a4")
    wiki.rename_root("/legal", "contracts", author="a5")
    wiki.merge_roots("/labs", "/ops", author="a6")
    proposal = wiki.propose_area("Suppliers", "Who we buy from.", [str(built.runbook.id)], author="a7")
    wiki.confirm_proposal(proposal.id, author="a8")
    second = wiki.propose_area("Misc", "Odds and ends.", [str(built.terms.id)], author="a9")
    wiki.dismiss_proposal(second.id, author="a10")
    wiki.delete_note(built.budget.rel, author="a11")
    wiki.delete_folder("/sales", author="a12")

    # ASSERT: the newest line first, the proposals' lines (a7, a8, a9, a10) among them
    assert authors(wiki)[:12] == ["a12", "a11", "a10", "a9", "a8", "a7", "a6", "a5", "a4", "a3", "a2", "a1"]
    assert lines(wiki)[2].startswith("* **Dismissed proposal**") and lines(wiki)[5].startswith("* **Proposed area**")


def test_mark_removed_writes_the_author(bundle: Path) -> None:
    # ARRANGE
    wiki = make_wiki(bundle)
    wiki.ingest_many([item(1)])
    origin = item(1).origin
    assert origin is not None

    # ACT
    wiki.mark_removed(origin.key, author="ada")

    # ASSERT
    assert lines(wiki)[0].startswith("* **Source removed**") and lines(wiki)[0].endswith(f"{BY}ada")


def test_sync_writes_the_author(bundle: Path, tmp_path: Path) -> None:
    # ARRANGE
    wiki = make_wiki(bundle)
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "a.md").write_text("topic: a\nline.", encoding="utf-8")

    # ACT
    done = sync(wiki, LocalFolderSource(folder), author="sync-bot")

    # ASSERT
    assert done.created == 1 and set(authors(wiki)) == {"sync-bot"}


# -- the author is for the call -----------------------------------------------------------------


def test_two_threads_with_different_authors_on_one_wiki_do_not_mix(classic_wiki: Wiki) -> None:
    # ARRANGE
    barrier = threading.Barrier(2, timeout=5)

    def work(author: str) -> None:
        barrier.wait()
        for n in range(8):
            classic_wiki.create_folder("/", f"{author}-{n}", f"Folder {n} of {author}.", author=author)

    # ACT
    threads = [threading.Thread(target=work, args=(a,)) for a in ("alice", "bob")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    # ASSERT
    folders = [line for line in lines(classic_wiki) if "Created folder" in line]
    assert len(folders) == 16
    for line in folders:
        name = "alice" if "alice-" in line else "bob"
        assert line.endswith(f"{BY}{name}")


def test_a_call_inside_another_keeps_the_outer_author(classic_wiki: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    script_ingest(llm)
    classic_wiki.author = "wiki-author"

    # ACT: ingest names "ada"; the folder it opens goes through other decorated code, with the wiki's author as default
    classic_wiki.ingest("Revenue is booked on delivery.", author="ada")

    # ASSERT
    assert set(authors(classic_wiki)) == {"ada"}


def test_an_author_is_cleaned_to_one_short_line_without_markup() -> None:
    assert clean_author("  Ada *Lovelace*\n[admin](x)  ") == "Ada Lovelace admin x"
    assert clean_author("a" * 500) == "a" * 60
    assert clean_author("***") is None and clean_author("") is None and clean_author(None) is None


def test_a_dirty_author_cannot_break_the_log_line(classic_wiki: Wiki) -> None:
    # ACT
    classic_wiki.create_folder("/", "labs", "Experiments.", author="eve\n## 2026-01-01\n* **Split**: [x](y)")

    # ASSERT
    text = (classic_wiki.cfg.bundle / LOG).read_text(encoding="utf-8")
    assert sum(line.startswith("## ") for line in text.splitlines()) == 1 and [str(p) for p in check(classic_wiki.cfg.bundle)] == []
    assert lines(classic_wiki)[0].endswith(f"{BY}eve ## 2026-01-01 Split : x y")


# -- the readers of log.md ----------------------------------------------------------------------

LOG_TEXT = """# Wiki log

## 2026-10-02

* **Created folder**: [/sales/emea](sales/emea/index.md) - Europe, routed by nothing
* **Created**: [Offer A](sales/offer-a.md) - sources, routed by fallback
* **Merged**: [Offer B](sales/offer-b.md) - sources
* **Split**: [/sales](sales/index.md) - at 12 notes, 2 opened: /sales/emea, /sales/apac
* **Created folder**: [/sales/apac](sales/apac/index.md) - Asia, by split
* **Created folder**: [/ops](ops/index.md) - Work, by plan

## 2026-09-30

* **Created**: [Plan](ops/plan.md)
* **Revised**: [Plan](ops/plan.md) - routed by fallback
"""


def test_the_readers_of_the_log_give_the_same_with_and_without_the_suffix() -> None:
    # ARRANGE
    marked = "\n".join(
        f"{line}{BY}ada" if line.startswith("* **") else line for line in LOG_TEXT.splitlines()
    )

    # ASSERT
    assert taxonomy_health(marked, 1) == taxonomy_health(LOG_TEXT, 1) != []
    assert last_split_at(marked, "sales") == last_split_at(LOG_TEXT, "sales") == 12
    assert last_split_at(marked, "ops") == 0


def test_check_and_health_read_a_real_log_written_with_authors(built: Built) -> None:
    # ARRANGE
    wiki = built.wiki
    before = wiki.taxonomy_health(1, 0.0)

    # ACT
    wiki.create_folder("/finance", "tax", "Taxes.", author="ada")

    # ASSERT
    assert [str(p) for p in wiki.check()] == []
    assert wiki.taxonomy_health(1, 0.0) != before  # the new subfolder counts, suffix or not
    assert any(r.new_subfolders for r in wiki.taxonomy_health(1, 0.0) if r.folder == "/finance")


# -- the CLI, HTTP and MCP ----------------------------------------------------------------------


def test_the_cli_files_with_the_login_name(
    monkeypatch: pytest.MonkeyPatch, bundle: Path, llm: FakeLLM, capsys: pytest.CaptureFixture[str]
) -> None:
    # ARRANGE
    made: list[Wiki] = []

    def make_wiki_(*, bundle: Path | None = None, mode: str | None = None) -> Wiki:
        made.append(Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm))
        return made[-1]

    monkeypatch.setattr(cli, "make_wiki", make_wiki_)
    monkeypatch.setattr(cli.getpass, "getuser", lambda: "marco")
    assert cli.main(["--bundle", str(bundle), "init"]) == 0

    # ACT
    assert cli.main(["--bundle", str(bundle), "rename-root", "x", "y"]) == 2  # no such folder: still a changing command
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm)
    wiki.create_folder("/", "labs", "Experiments.")
    assert cli.main(["--bundle", str(bundle), "rename-root", "/labs", "lab"]) == 0

    # ASSERT
    assert made[-1].author == "marco" and made[0].author is None  # init changes nothing in the log
    assert lines(wiki)[0].endswith(f"{BY}marco")
    assert capsys.readouterr().out.strip().endswith("lab")


def test_the_cli_goes_without_an_author_when_the_system_has_no_user(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> str:
        raise OSError("no user")

    monkeypatch.setattr(cli.getpass, "getuser", fail)

    assert cli.login() is None


def test_http_routes_that_change_the_wiki_take_the_x_author_header(client: Any, classic_wiki: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    script_ingest(llm)

    # ACT
    client.post("/ingest", json={"text": "Booked on delivery.", "title": "Memo"}, headers={"X-Author": "ada"})
    client.post("/folder", json={"parent": "/", "name": "labs", "description": "Experiments."}, headers={"X-Author": "lin"})
    client.post("/folder", json={"parent": "/", "name": "ops", "description": "Work."})

    # ASSERT
    assert authors(classic_wiki)[0] is None and authors(classic_wiki)[1] == "lin" and set(authors(classic_wiki)[2:]) == {"ada"}


def test_a_read_does_not_carry_the_author_anywhere(client: Any, classic_wiki: Wiki) -> None:
    client.get("/tree", headers={"X-Author": "ada"})

    assert authors(classic_wiki) == []


def call_tool(wiki: Wiki, tool: str, arguments: dict[str, Any], name: str) -> None:
    from mcp import Client
    from mcp.types import Implementation

    from llmw2.memory import Memory

    wiki.cfg.mcp = True

    async def go() -> None:
        async with Client(Memory(lambda: wiki).server, client_info=Implementation(name=name, version="1")) as client:
            await client.call_tool(tool, arguments)

    asyncio.run(go())


def test_the_memory_files_under_the_name_of_the_client(classic_wiki: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    pytest.importorskip("mcp")
    script_ingest(llm)

    # ACT
    call_tool(classic_wiki, "remember", {"text": "Booked on delivery.", "title": "Memo"}, "claude-code")
    call_tool(classic_wiki, "create_area", {"name": "labs", "description": "Experiments."}, "codex")

    # ASSERT
    assert authors(classic_wiki)[0] == "codex" and set(authors(classic_wiki)[1:]) == {"claude-code"}


def test_the_memory_over_http_names_the_user_agent_or_x_author(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    pytest.importorskip("mcp")
    from fastapi.testclient import TestClient

    from llmw2.server import create_app
    from tests.wiki.test_memory import ACCEPT, INITIALIZE

    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", mcp=True), llm=llm, classifier=classifier)
    wiki.init()

    def area(name: str, description: str) -> dict[str, Any]:
        params = {"name": "create_area", "arguments": {"name": name, "description": description}}
        return {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": params}

    headers = {**ACCEPT, "MCP-Protocol-Version": "2025-06-18"}

    # ACT
    with TestClient(create_app(wiki)) as http:
        http.post("/mcp", json=INITIALIZE, headers=ACCEPT)
        http.post("/mcp", json=area("labs", "Experiments."), headers={**headers, "User-Agent": "claude-code/2.1 (cli)"})
        http.post("/mcp", json=area("ops", "Work."), headers={**headers, "User-Agent": "claude-code/2.1", "X-Author": "ada"})

    # ASSERT
    assert authors(wiki) == ["ada", "claude-code"]
