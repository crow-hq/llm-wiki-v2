# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from spec-move-and-describe-2026-10-09.md: move a note to another folder, edit a folder's description."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from llmw2 import Origin, Wiki, WikiConfig, cli
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.origin import content_hash
from llmw2.bundle.store import body_hash
from llmw2.bundle.tree import INDEX, LOG, RAW, link_target, parse_index, split_see_also
from llmw2.errors import InputError
from tests.wiki.fakes import FakeClassifier, FakeLLM

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

ORIGIN = Origin("local", "", "docs/tea.md")
TEA_TEXT = "Tea is sourced from two estates, one in Assam and one in Darjeeling."
LINK = re.compile(r"\]\(([^)\s]+)\)")
OLD_TEA, NEW_TEA = "finance/tea-sourcing.md", "sourcing/green/tea-sourcing.md"


@dataclass
class Filed:
    """The notes are linked in both directions, in several folders:

    /finance/tea-sourcing.md (the one that moves; inline link to the price list with an anchor and a web link),
    /finance/budget.md, /finance/pricing/price-list.md, /ops/runbook.md (inline link back to the tea note with an anchor),
    /plan.md (root), /sourcing/suppliers.md; folders /finance /finance/pricing /ops /sourcing /sourcing/green.
    """

    tea: str = OLD_TEA
    budget: str = "finance/budget.md"
    price_list: str = "finance/pricing/price-list.md"
    runbook: str = "ops/runbook.md"
    plan: str = "plan.md"
    suppliers: str = "sourcing/suppliers.md"
    raw: str = ""


@pytest.fixture
def filed(classic_wiki: Wiki) -> Filed:
    store = classic_wiki.store
    root = store.load()
    finance, _ = store.create_folder(root, "finance", "Money in and out.")
    pricing, _ = store.create_folder(finance, "pricing", "What things cost.")
    ops, _ = store.create_folder(root, "ops", "How the work is done.")
    sourcing, _ = store.create_folder(root, "sourcing", "Where goods come from.")
    store.create_folder(sourcing, "green", "Sustainable suppliers.")
    raw = store.save_raw(TEA_TEXT, title="Tea", resource=None, origin=ORIGIN)
    ref = {"resource": raw, "title": "Tea", "hash": content_hash(TEA_TEXT), "origin": ORIGIN.to_dict()}
    other = {"resource": "/raw/other.md", "title": "Other"}

    def note(folder: Any, title: str, body: str, source: dict[str, Any] = other) -> Any:
        return store.write_note(folder, title=title, summary=f"About {title.lower()}.", body=body, tags=["tea"], source=source)

    tea = note(
        finance,
        "Tea sourcing",
        "Prices are in [the price list](pricing/price-list.md#top).\n\nSee [the web](https://example.com/finance/x.md) and [top](#top).",
        ref,
    )
    budget = note(finance, "Budget", "Spend. Tea is covered in [tea](tea-sourcing.md).")
    price_list = note(pricing, "Price list", "Prices.")
    runbook = note(ops, "Runbook", "Steps. Tea details in [tea](../finance/tea-sourcing.md#x).")
    plan = note(root, "Plan", "What we do. Read [the tea note](finance/tea-sourcing.md) first.")
    suppliers = note(sourcing, "Suppliers", "Who sells.")
    for other_note in (price_list, runbook, plan, budget):
        store.link(tea, other_note)
    store.link(suppliers, budget)
    return Filed(tea.rel, budget.rel, price_list.rel, runbook.rel, plan.rel, suppliers.rel, raw)


def exists(wiki: Wiki, rel: str) -> bool:
    return (wiki.cfg.bundle / rel.lstrip("/")).exists()


def listed(wiki: Wiki, folder: str) -> list[str]:
    return [link for _, link, _ in parse_index((wiki.cfg.bundle / folder / INDEX).read_text(encoding="utf-8"))]


def entry(wiki: Wiki, parent: str, link: str) -> str:
    """The description in `parent`'s index.md of the entry that links to `link`."""
    text = (wiki.cfg.bundle / parent / INDEX).read_text(encoding="utf-8")
    return next(description for _, target, description in parse_index(text) if target == link)


def targets(wiki: Wiki, rel: str) -> list[str]:
    """Where the links of a note lead, as bundle paths, in order (web links and anchors excluded)."""
    found = (link_target(rel, link) for link in LINK.findall(wiki.store.read(rel).body))
    return [t for t in found if t]


def log_text(wiki: Wiki) -> str:
    return (wiki.cfg.bundle / LOG).read_text(encoding="utf-8")


def snapshot(wiki: Wiki) -> dict[str, str]:
    """Every file of the bundle with its text: a refused call must leave all of it as it was."""
    root = wiki.cfg.bundle
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.md")) if p.name != LOG}


def graph_by_title(wiki: Wiki) -> dict[str, set[str]]:
    """Who links to whom, by title: it does not change when notes move."""
    notes = wiki.tree().all_notes()
    title_of = {n.rel: n.title for n in notes}
    return {n.title: {title_of[t] for t in targets(wiki, n.rel) if t in title_of} for n in notes}


def archive(wiki: Wiki, rel: str) -> str:
    """Archive a note as it is; the archived copy's bundle path ("/.archive/…")."""
    note = wiki.store.read(rel)
    return wiki.store.archive_note(note)


def read_doc(wiki: Wiki, rel: str) -> OKFDocument:
    return OKFDocument.parse((wiki.cfg.bundle / rel.lstrip("/")).read_text(encoding="utf-8"))


# -- move_note: the file ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "folder"), [(OLD_TEA, "/sourcing/green"), ("/" + OLD_TEA, "sourcing/green")])
def test_move_note_when_the_folder_exists_then_the_file_is_at_the_new_path_and_the_old_one_is_gone(
    classic_wiki: Wiki, filed: Filed, path: str, folder: str
) -> None:
    # ARRANGE
    before = classic_wiki.store.read(OLD_TEA)

    # ACT
    moved = classic_wiki.move_note(path, folder)

    # ASSERT
    assert moved.rel == NEW_TEA
    assert exists(classic_wiki, NEW_TEA) and not exists(classic_wiki, OLD_TEA)
    now = classic_wiki.store.read(NEW_TEA)
    assert (now.title, now.summary, now.tags) == (before.title, before.summary, before.tags)
    assert now.frontmatter["sources"] == before.frontmatter["sources"]
    assert {k: v for k, v in now.frontmatter["generated"].items() if k != "body_hash"} == {
        k: v for k, v in before.frontmatter["generated"].items() if k != "body_hash"
    }
    assert now.frontmatter["generated"]["body_hash"] == body_hash(split_see_also(now.body)[0])  # the links in it were rewritten
    assert now.main_body().startswith("Prices are in")
    assert [n.rel for n in classic_wiki.tree().all_notes()].count(NEW_TEA) == 1
    assert classic_wiki.check() == []


def test_move_note_when_moved_to_the_root_then_it_lives_there(classic_wiki: Wiki, filed: Filed) -> None:
    # ACT
    moved = classic_wiki.move_note(filed.tea, "/")

    # ASSERT
    assert moved.rel == "tea-sourcing.md"
    assert exists(classic_wiki, "tea-sourcing.md") and not exists(classic_wiki, filed.tea)
    assert "tea-sourcing.md" in listed(classic_wiki, "")
    assert filed.price_list in targets(classic_wiki, "tea-sourcing.md")
    assert "tea-sourcing.md" in targets(classic_wiki, filed.runbook)
    assert classic_wiki.check() == []


def test_move_note_when_the_name_is_taken_in_the_target_then_the_slug_is_made_unique(classic_wiki: Wiki, filed: Filed) -> None:
    # ARRANGE
    store = classic_wiki.store
    green = store.load().find("/sourcing/green")
    assert green is not None
    taken = store.write_note(green, title="Tea sourcing", summary="Another.", body="Other.", tags=[], source={"resource": "/raw/o.md"})

    # ACT
    moved = classic_wiki.move_note(filed.tea, "/sourcing/green")

    # ASSERT
    assert moved.rel == "sourcing/green/tea-sourcing-2.md"
    assert store.read(taken.rel).main_body().strip() == "Other."  # the note already there is untouched
    assert sorted(listed(classic_wiki, "sourcing/green")) == ["tea-sourcing-2.md", "tea-sourcing.md"]
    assert "sourcing/green/tea-sourcing-2.md" in targets(classic_wiki, filed.runbook)
    assert taken.rel not in targets(classic_wiki, filed.runbook)
    assert classic_wiki.check() == []


# -- move_note: the links ---------------------------------------------------------------------------------------------


def test_move_note_when_it_links_to_others_then_its_own_links_still_lead_there(classic_wiki: Wiki, filed: Filed) -> None:
    # ARRANGE
    before = targets(classic_wiki, filed.tea)
    assert {filed.price_list, filed.runbook, filed.plan, filed.budget} <= set(before)

    # ACT
    classic_wiki.move_note(filed.tea, "/sourcing/green")

    # ASSERT
    assert targets(classic_wiki, NEW_TEA) == before
    body = classic_wiki.store.read(NEW_TEA).body
    assert "finance/pricing/price-list.md#top)" in body  # the anchor stays
    assert "(https://example.com/finance/x.md)" in body and "(#top)" in body  # web links and anchors are not paths
    assert "(pricing/price-list.md#top)" not in body  # relative to the old folder: stale now


def test_move_note_when_others_link_to_it_then_their_links_lead_to_the_new_path(classic_wiki: Wiki, filed: Filed) -> None:
    # ARRANGE
    others = (filed.budget, filed.price_list, filed.runbook, filed.plan)
    before = {rel: targets(classic_wiki, rel) for rel in others}
    assert all(OLD_TEA in t for t in before.values())

    # ACT
    classic_wiki.move_note(filed.tea, "/sourcing/green")

    # ASSERT
    for rel, old in before.items():
        assert targets(classic_wiki, rel) == [NEW_TEA if t == OLD_TEA else t for t in old], rel
    assert "#x)" in classic_wiki.store.read(filed.runbook).body  # an anchor survives the rewrite
    inline = classic_wiki.store.read(filed.plan).main_body()
    assert "[the tea note](" in inline and "Read" in inline  # the text of the link is kept
    assert classic_wiki.check() == []


def test_move_note_then_both_indexes_are_rewritten(classic_wiki: Wiki, filed: Filed) -> None:
    # ACT
    classic_wiki.move_note(filed.tea, "/sourcing/green")

    # ASSERT
    assert "tea-sourcing.md" not in listed(classic_wiki, "finance")
    assert sorted(listed(classic_wiki, "finance")) == ["budget.md", "pricing/index.md"]
    text = (classic_wiki.cfg.bundle / "sourcing/green" / INDEX).read_text(encoding="utf-8")
    assert parse_index(text) == [("Tea sourcing", "tea-sourcing.md", "About tea sourcing.")]


def test_move_note_then_the_log_says_where_from_and_to(classic_wiki: Wiki, filed: Filed) -> None:
    # ACT
    classic_wiki.move_note(filed.tea, "/sourcing/green")

    # ASSERT
    line = next(entry for entry in log_text(classic_wiki).splitlines() if "**Moved**" in entry)
    assert f"from /{OLD_TEA} to /{NEW_TEA}" in line
    assert NEW_TEA in line and "Tea sourcing" in line  # linked to the new note by its title


def test_move_note_when_it_is_moved_back_then_the_links_are_as_they_were(classic_wiki: Wiki, filed: Filed) -> None:
    # ARRANGE
    watched = (filed.tea, filed.budget, filed.runbook, filed.plan)
    before = {rel: targets(classic_wiki, rel) for rel in watched}

    # ACT
    classic_wiki.move_note(filed.tea, "/sourcing/green")
    classic_wiki.move_note(NEW_TEA, "/finance")

    # ASSERT
    assert {rel: targets(classic_wiki, rel) for rel in watched} == before
    assert classic_wiki.check() == []


def test_check_after_many_random_moves_reports_nothing_new(classic_wiki: Wiki, filed: Filed) -> None:
    # ARRANGE
    wiki = classic_wiki
    rng = random.Random(20261009)
    folders = [f.label for f in wiki.tree().walk()]
    notes = len(wiki.tree().all_notes())
    graph_before = graph_by_title(wiki)
    assert wiki.check() == []

    # ACT
    moves = 0
    for _ in range(40):
        note = rng.choice(wiki.tree().all_notes())
        folder = rng.choice(folders)
        if note.folder is not None and note.folder.label == folder:
            continue
        moved = wiki.move_note(note.rel, folder)
        moves += 1

        # ASSERT
        assert wiki.check() == [], (note.rel, folder)
        assert moved.rel.startswith("" if folder == "/" else folder.strip("/") + "/")
        assert len(wiki.tree().all_notes()) == notes
        assert graph_by_title(wiki) == graph_before
    assert moves >= 20
    assert len(re.findall(r"\*\*Moved\*\*", log_text(wiki))) == moves


# -- move_note: archives and origins -----------------------------------------------------------------------------------


def test_move_note_when_it_has_archived_copies_then_their_archived_from_follows_and_nothing_else_changes(
    classic_wiki: Wiki, filed: Filed
) -> None:
    # ARRANGE
    store = classic_wiki.store
    first = archive(classic_wiki, filed.tea)
    tea = store.read(filed.tea)
    tea.folder = store.load().find("/finance")
    store.update_note(
        tea, title=tea.title, summary=tea.summary, body="Revised.", tags=[], source={"resource": filed.raw}, previous_version=first
    )
    other = archive(classic_wiki, filed.budget)
    first_before, other_before = read_doc(classic_wiki, first), read_doc(classic_wiki, other)

    # ACT
    classic_wiki.move_note(filed.tea, "/sourcing/green")

    # ASSERT
    assert exists(classic_wiki, first)  # the archive does not move
    archived = read_doc(classic_wiki, first)
    assert archived.frontmatter["archived_from"] == f"/{NEW_TEA}"
    assert {k: v for k, v in archived.frontmatter.items() if k != "archived_from"} == {
        k: v for k, v in first_before.frontmatter.items() if k != "archived_from"
    }
    assert archived.body == first_before.body
    assert store.read(NEW_TEA).frontmatter["previous_version"] == first
    after_other = read_doc(classic_wiki, other)  # another note's archive is not touched
    assert (after_other.frontmatter, after_other.body) == (other_before.frontmatter, other_before.body)
    assert [n.rel for n in store.load().all_notes() if n.rel.startswith(".archive")] == []
    assert classic_wiki.check() == []


def test_move_note_when_its_origin_is_ingested_again_unchanged_then_the_note_is_found_at_the_new_path(
    classic_wiki: Wiki, filed: Filed
) -> None:
    # ARRANGE
    record = classic_wiki.store.find_origin(ORIGIN.key)
    assert record is not None and record.notes == [filed.tea]

    # ACT
    classic_wiki.move_note(filed.tea, "/sourcing/green")

    # ASSERT
    record = classic_wiki.store.find_origin(ORIGIN.key)
    assert record is not None and record.notes == [NEW_TEA]
    again = classic_wiki.ingest(TEA_TEXT, title="Tea", origin=ORIGIN)
    assert (again.action, again.note) == ("unchanged", NEW_TEA)


def test_move_note_when_the_origin_comes_back_changed_then_the_note_at_the_new_path_is_updated(
    classic_wiki: Wiki, filed: Filed, llm: FakeLLM
) -> None:
    # ARRANGE
    classic_wiki.move_note(filed.tea, "/sourcing/green")
    notes = len(classic_wiki.tree().all_notes())
    merged = {"title": "Tea sourcing", "summary": "Tea, updated.", "tags": [], "body": "# Overview\n\nMERGEDTEA facts."}
    llm.add("librarian/summarize", {"title": "Tea sourcing", "summary": "Tea.", "tags": [], "body": "# Summary\n\nNEWTEA."})
    llm.add(
        "librarian/route", {"action": "descend", "subfolder": "sourcing"}, {"action": "descend", "subfolder": "green"}, {"action": "here"}
    )
    llm.add("librarian/find_match", {"match": NEW_TEA})
    llm.add("librarian/consolidate", {"decision": "modify"}).add("librarian/merge", merged).add("librarian/relate", {"related": []})

    # ACT
    result = classic_wiki.ingest(TEA_TEXT + " A third estate joined in Nilgiri.", title="Tea", origin=ORIGIN)

    # ASSERT
    assert result.note == NEW_TEA
    assert len(classic_wiki.tree().all_notes()) == notes  # updated, not filed a second time
    assert not exists(classic_wiki, OLD_TEA)
    record = classic_wiki.store.find_origin(ORIGIN.key)
    assert record is not None and record.notes == [NEW_TEA]
    assert classic_wiki.check() == []


# -- move_note: errors ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "folder"),
    [
        ("finance/missing.md", "/ops"),
        ("raw/anything.md", "/ops"),
        (INDEX, "/ops"),
        ("finance/index.md", "/ops"),
        (LOG, "/ops"),
        ("../outside.md", "/ops"),
        ("finance/budget.md", "/missing"),
        ("finance/budget.md", "/finance/missing"),
        ("finance/budget.md", "/finance"),
        ("/finance/budget.md", "finance"),
        ("plan.md", "/"),
    ],
    ids=[
        "no-note",
        "raw-name",
        "root-index",
        "folder-index",
        "log",
        "outside",
        "no-folder",
        "no-subfolder",
        "same-folder",
        "same-folder-slashes",
        "root-same-folder",
    ],
)
def test_move_note_when_the_request_is_not_possible_then_input_error_and_nothing_changes(
    classic_wiki: Wiki, filed: Filed, path: str, folder: str
) -> None:
    # ARRANGE
    before = snapshot(classic_wiki)

    # ACT
    with pytest.raises(InputError):
        classic_wiki.move_note(path, folder)

    # ASSERT
    assert snapshot(classic_wiki) == before
    assert "**Moved**" not in log_text(classic_wiki)


def test_move_note_when_the_path_is_a_raw_source_that_exists_then_it_is_refused(classic_wiki: Wiki, filed: Filed) -> None:
    # ARRANGE
    before = snapshot(classic_wiki)
    assert filed.raw.startswith(f"/{RAW}/") and exists(classic_wiki, filed.raw)

    # ACT
    with pytest.raises(InputError):
        classic_wiki.move_note(filed.raw, "/ops")

    # ASSERT
    assert snapshot(classic_wiki) == before


def test_move_note_when_the_path_is_an_archived_copy_then_it_is_refused(classic_wiki: Wiki, filed: Filed) -> None:
    # ARRANGE
    archived = archive(classic_wiki, filed.budget)
    before = snapshot(classic_wiki)

    # ACT
    with pytest.raises(InputError):
        classic_wiki.move_note(archived, "/ops")

    # ASSERT
    assert snapshot(classic_wiki) == before and exists(classic_wiki, archived)


def test_move_note_when_the_target_is_the_archive_or_raw_then_it_is_refused(classic_wiki: Wiki, filed: Filed) -> None:
    # ARRANGE
    archive(classic_wiki, filed.budget)
    before = snapshot(classic_wiki)

    # ACT
    for folder in ("/.archive", "/.archive/finance", "/raw"):
        with pytest.raises(InputError):
            classic_wiki.move_note(filed.budget, folder)

    # ASSERT
    assert snapshot(classic_wiki) == before


# -- describe_folder ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/finance/pricing", "finance/pricing"])
def test_describe_folder_then_the_entry_in_the_parent_index_is_rewritten(classic_wiki: Wiki, filed: Filed, path: str) -> None:
    # ACT
    folder = classic_wiki.describe_folder(path, "Every price, quote and discount.")

    # ASSERT
    assert (folder.label, folder.description) == ("/finance/pricing", "Every price, quote and discount.")
    assert entry(classic_wiki, "finance", "pricing/index.md") == "Every price, quote and discount."
    loaded = classic_wiki.store.load().find("/finance/pricing")
    assert loaded is not None and loaded.description == "Every price, quote and discount."
    assert classic_wiki.tree().to_dict()["subfolders"][0]["subfolders"][0]["description"] == "Every price, quote and discount."
    assert sorted(listed(classic_wiki, "finance")) == ["budget.md", "pricing/index.md", "tea-sourcing.md"]  # nothing else lost
    assert classic_wiki.check() == []


def test_describe_folder_when_the_folder_is_at_the_top_then_the_root_index_is_rewritten(classic_wiki: Wiki, filed: Filed) -> None:
    # ACT
    classic_wiki.describe_folder("/ops", "Runbooks.")

    # ASSERT
    assert entry(classic_wiki, "", "ops/index.md") == "Runbooks."
    assert entry(classic_wiki, "", "finance/index.md") == "Money in and out."  # the others stay


def test_describe_folder_collapses_whitespace(classic_wiki: Wiki, filed: Filed) -> None:
    # ACT
    folder = classic_wiki.describe_folder("/ops", "  How the\n  work   is\tdone,\n\nstep by step.  ")

    # ASSERT
    assert folder.description == "How the work is done, step by step."
    assert entry(classic_wiki, "", "ops/index.md") == "How the work is done, step by step."
    assert classic_wiki.check() == []


def test_describe_folder_then_the_log_has_a_described_folder_line(classic_wiki: Wiki, filed: Filed) -> None:
    # ACT
    classic_wiki.describe_folder("/finance/pricing", "Every price.")

    # ASSERT
    line = next(entry for entry in log_text(classic_wiki).splitlines() if "**Described folder**" in entry)
    assert "Every price." in line or "/finance/pricing" in line


@pytest.mark.parametrize(
    ("path", "description"),
    [
        ("/", "The whole wiki."),
        ("", "The whole wiki."),
        ("/missing", "Anything."),
        ("/finance/missing", "Anything."),
        ("/ops", ""),
        ("/ops", " \n\t "),
    ],
    ids=["root", "empty-path", "missing", "missing-nested", "empty", "blank"],
)
def test_describe_folder_when_it_cannot_be_done_then_input_error_and_nothing_changes(
    classic_wiki: Wiki, filed: Filed, path: str, description: str
) -> None:
    # ARRANGE
    before = snapshot(classic_wiki)

    # ACT
    with pytest.raises(InputError):
        classic_wiki.describe_folder(path, description)

    # ASSERT
    assert snapshot(classic_wiki) == before
    assert "Described folder" not in log_text(classic_wiki)


def test_describe_folder_when_the_next_document_is_routed_then_the_router_reads_the_new_description(
    classic_wiki: Wiki, filed: Filed, llm: FakeLLM
) -> None:
    # ARRANGE
    classic_wiki.describe_folder("/ops", "Night-shift handover procedures.")
    llm.add("librarian/summarize", {"title": "Handover", "summary": "Who hands what over.", "body": "Details."})
    llm.add("librarian/route", {"action": "new"}).add("librarian/name_folder", {"name": "handover", "description": "Handovers."})
    llm.add("librarian/find_match", {"match": None}).add("librarian/relate", {"related": []})

    # ACT
    classic_wiki.ingest("The night shift hands the keys to the day shift.")

    # ASSERT
    routed = [messages for op, messages in llm.calls if op == "librarian/route"]
    assert routed and any("Night-shift handover procedures." in m["content"] for m in routed[0])


# -- HTTP -----------------------------------------------------------------------------------------------------------------


def test_http_move_answers_with_the_note_as_get_note_returns_it(client: TestClient, filed: Filed) -> None:
    # ACT
    response = client.post("/note/move", json={"path": filed.tea, "folder": "/sourcing/green"})

    # ASSERT
    assert response.status_code == 200
    body = response.json()
    assert body == client.get("/note", params={"path": NEW_TEA}).json()
    assert (body["path"], body["title"]) == (NEW_TEA, "Tea sourcing")
    assert set(body) == {"path", "title", "summary", "tags", "frontmatter", "body"}
    assert client.get("/note", params={"path": filed.tea}).status_code == 404
    assert client.get("/check").json() == []


def test_http_move_when_the_note_is_missing_then_404(client: TestClient, filed: Filed) -> None:
    # ACT
    response = client.post("/note/move", json={"path": "finance/missing.md", "folder": "/ops"})

    # ASSERT
    assert response.status_code == 404
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize(
    ("path", "folder"),
    [("finance/index.md", "/ops"), ("finance/budget.md", "/finance"), ("plan.md", "/")],
    ids=["index", "same-folder", "root-same-folder"],
)
def test_http_move_when_the_request_is_not_possible_then_400(client: TestClient, filed: Filed, path: str, folder: str) -> None:
    # ACT
    response = client.post("/note/move", json={"path": path, "folder": folder})

    # ASSERT
    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str) and response.json()["detail"]


def test_http_move_when_the_path_is_a_raw_source_then_400(client: TestClient, filed: Filed) -> None:
    # ACT
    response = client.post("/note/move", json={"path": filed.raw, "folder": "/ops"})

    # ASSERT
    assert response.status_code == 400


def test_http_move_when_the_path_is_an_archived_copy_then_it_is_refused(client: TestClient, classic_wiki: Wiki, filed: Filed) -> None:
    # ARRANGE
    archived = archive(classic_wiki, filed.budget)

    # ACT
    response = client.post("/note/move", json={"path": archived, "folder": "/ops"})

    # ASSERT
    assert response.status_code == 400
    assert exists(classic_wiki, archived)


def test_http_move_when_the_folder_is_missing_then_the_note_stays_and_the_answer_is_a_client_error(
    client: TestClient, filed: Filed
) -> None:
    # ACT
    response = client.post("/note/move", json={"path": filed.budget, "folder": "/missing"})

    # ASSERT
    assert response.status_code in (400, 404)  # the spec says 400 for an InputError and 404 "when missing": it does not say which
    assert client.get("/note", params={"path": filed.budget}).status_code == 200


def test_http_move_when_a_field_is_missing_then_422(client: TestClient, filed: Filed) -> None:
    assert client.post("/note/move", json={"path": filed.budget}).status_code == 422
    assert client.post("/note/move", json={"folder": "/ops"}).status_code == 422


def test_http_move_from_another_site_is_refused(client: TestClient, classic_wiki: Wiki, filed: Filed) -> None:
    # ACT
    response = client.post("/note/move", json={"path": filed.tea, "folder": "/ops"}, headers={"origin": "https://evil.test"})

    # ASSERT
    assert response.status_code == 403
    assert exists(classic_wiki, filed.tea)


def test_http_describe_answers_with_the_path_and_the_description(client: TestClient, filed: Filed) -> None:
    # ACT
    response = client.post("/folder/describe", json={"path": "/finance/pricing", "description": "  Every   price\nwe quote. "})

    # ASSERT
    assert response.status_code == 200
    assert response.json() == {"path": "/finance/pricing", "description": "Every price we quote."}
    pricing = client.get("/tree").json()["subfolders"][0]["subfolders"][0]
    assert (pricing["path"], pricing["description"]) == ("/finance/pricing", "Every price we quote.")


@pytest.mark.parametrize(
    ("path", "description"),
    [("/", "Whole."), ("/missing", "Anything."), ("/ops", ""), ("/ops", "   ")],
    ids=["root", "missing", "empty", "blank"],
)
def test_http_describe_when_it_cannot_be_done_then_a_client_error_and_nothing_changes(
    client: TestClient, classic_wiki: Wiki, filed: Filed, path: str, description: str
) -> None:
    # ARRANGE
    before = snapshot(classic_wiki)

    # ACT
    response = client.post("/folder/describe", json={"path": path, "description": description})

    # ASSERT
    assert response.status_code in (400, 404, 422)  # an InputError: the spec does not name the code for this endpoint
    assert isinstance(response.json()["detail"], str)
    assert snapshot(classic_wiki) == before


def test_http_describe_when_a_field_is_missing_then_422(client: TestClient, filed: Filed) -> None:
    assert client.post("/folder/describe", json={"path": "/ops"}).status_code == 422


def test_http_describe_from_another_site_is_refused(client: TestClient, filed: Filed) -> None:
    # ACT
    response = client.post("/folder/describe", json={"path": "/ops", "description": "x"}, headers={"origin": "https://evil.test"})

    # ASSERT
    assert response.status_code == 403


# -- CLI --------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def made(monkeypatch: pytest.MonkeyPatch, llm: FakeLLM, classifier: FakeClassifier) -> None:
    def make_wiki(*, bundle: Path | None = None, mode: str | None = None) -> Wiki:
        return Wiki(WikiConfig(bundle=bundle, mode=mode or "classic"), llm=llm, classifier=classifier)

    monkeypatch.setattr(cli, "make_wiki", make_wiki)


def run(bundle: Path, *args: str) -> int:
    return cli.main(["--bundle", str(bundle), *args])


def test_cli_move_prints_the_new_path_and_moves_the_note(
    made: None, classic_wiki: Wiki, filed: Filed, bundle: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # ACT
    code = run(bundle, "move", filed.tea, "/sourcing/green")

    # ASSERT
    out, _ = capsys.readouterr()
    assert code == 0
    assert NEW_TEA in out and len(out.strip().splitlines()) == 1
    assert exists(classic_wiki, NEW_TEA) and not exists(classic_wiki, OLD_TEA)
    assert classic_wiki.check() == []


@pytest.mark.parametrize(
    ("path", "folder"),
    [("finance/missing.md", "/ops"), ("finance/budget.md", "/missing"), ("finance/budget.md", "/finance"), ("raw/x.md", "/ops")],
    ids=["no-note", "no-folder", "same-folder", "raw"],
)
def test_cli_move_when_it_cannot_be_done_then_a_one_line_error_and_nothing_changes(
    made: None, classic_wiki: Wiki, filed: Filed, bundle: Path, capsys: pytest.CaptureFixture[str], path: str, folder: str
) -> None:
    # ARRANGE
    before = snapshot(classic_wiki)

    # ACT
    code = run(bundle, "move", path, folder)

    # ASSERT
    out, err = capsys.readouterr()
    assert code != 0 and out == ""
    assert err.startswith("llmwiki2:") and "Traceback" not in err
    assert snapshot(classic_wiki) == before


def test_cli_describe_prints_the_folder_and_its_description(
    made: None, classic_wiki: Wiki, filed: Filed, bundle: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # ACT
    code = run(bundle, "describe", "/finance/pricing", "Every  price\nwe quote.")

    # ASSERT
    out, _ = capsys.readouterr()
    assert code == 0
    assert "/finance/pricing" in out and "Every price we quote." in out
    assert entry(classic_wiki, "finance", "pricing/index.md") == "Every price we quote."


@pytest.mark.parametrize(
    ("path", "description"), [("/", "Whole."), ("/missing", "Anything."), ("/ops", "  ")], ids=["root", "missing", "blank"]
)
def test_cli_describe_when_it_cannot_be_done_then_a_one_line_error_and_nothing_changes(
    made: None, classic_wiki: Wiki, filed: Filed, bundle: Path, capsys: pytest.CaptureFixture[str], path: str, description: str
) -> None:
    # ARRANGE
    before = snapshot(classic_wiki)

    # ACT
    code = run(bundle, "describe", path, description)

    # ASSERT
    out, err = capsys.readouterr()
    assert code != 0 and out == ""
    assert err.startswith("llmwiki2:") and "Traceback" not in err
    assert snapshot(classic_wiki) == before
