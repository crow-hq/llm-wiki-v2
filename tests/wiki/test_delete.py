# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Deleting a note, a folder, or the whole wiki: files, indexes, links from other notes, raw copies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from okf_wiki import Wiki
from okf_wiki.bundle.tree import INDEX, LOG, RAW, drop_links, parse_index
from okf_wiki.errors import InputError

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


@dataclass
class Filed:
    """/plan.md, /finance/budget.md, /finance/pricing/price-list.md, linked to each other, and their raw copies."""

    plan: str
    budget: str
    price_list: str
    raw_plan: str  # cited by the plan and the price list
    raw_budget: str  # cited by the budget only
    raw_prices: str  # cited by the price list only


@pytest.fixture
def filed(classic_wiki: Wiki) -> Filed:
    store = classic_wiki.store
    root = store.load()
    finance, _ = store.create_folder(root, "finance", "Money in and out.")
    pricing, _ = store.create_folder(finance, "pricing", "What things cost.")
    raw_plan = store.save_raw("The plan.", title="Plan", resource=None)
    raw_budget = store.save_raw("The budget.", title="Budget", resource=None)
    raw_prices = store.save_raw("The prices.", title="Prices", resource=None)

    def source(raw: str) -> dict[str, str]:
        return {"resource": raw, "title": raw}

    price_list = store.write_note(
        pricing, title="Price list", summary="Every price.", body="Prices.", tags=[], source=source(raw_prices)
    )
    store.update_note(price_list, title="Price list", summary="Every price.", body="Prices.", tags=[], source=source(raw_plan))
    budget = store.write_note(finance, title="Budget", summary="The year.", body="Spend.", tags=[], source=source(raw_budget))
    plan = store.write_note(
        root, title="Plan", summary="What we do.", tags=[], source=source(raw_plan),
        body="Read [the price list](finance/pricing/price-list.md#top) and [the web](https://example.com/finance/x.md).",
    )
    store.link(plan, price_list)
    store.link(plan, budget)
    store.link(budget, price_list)
    return Filed(plan.rel, budget.rel, price_list.rel, raw_plan, raw_budget, raw_prices)


def body(wiki: Wiki, rel: str) -> str:
    return wiki.store.read(rel).body


def exists(wiki: Wiki, rel: str) -> bool:
    return (wiki.cfg.bundle / rel.lstrip("/")).exists()


def listed(wiki: Wiki, folder: str) -> list[str]:
    return [link for _, link, _ in parse_index((wiki.cfg.bundle / folder / INDEX).read_text(encoding="utf-8"))]


def test_delete_note_when_other_notes_link_to_it_then_their_links_go_and_the_wiki_stays_valid(classic_wiki: Wiki, filed: Filed) -> None:
    # ACT
    deleted = classic_wiki.delete_note(filed.price_list)

    # ASSERT
    assert not exists(classic_wiki, filed.price_list)
    assert listed(classic_wiki, "finance/pricing") == []
    assert "price-list.md" not in body(classic_wiki, filed.plan) and "price-list.md" not in body(classic_wiki, filed.budget)
    assert "Read the price list and [the web](https://example.com/finance/x.md)." in body(classic_wiki, filed.plan)
    assert "budget.md" in body(classic_wiki, filed.plan)  # the other links stay
    assert deleted.to_dict() == {
        "notes": [filed.price_list], "folders": [], "sources": [filed.raw_prices], "unlinked": [filed.plan, filed.budget]
    }
    assert exists(classic_wiki, filed.raw_plan)  # the plan still cites it
    assert classic_wiki.check() == []


def test_delete_folder_when_it_has_subfolders_then_everything_in_it_goes(classic_wiki: Wiki, filed: Filed) -> None:
    # ACT
    deleted = classic_wiki.delete_folder("/finance")

    # ASSERT
    assert not exists(classic_wiki, "finance")
    assert listed(classic_wiki, "") == ["plan.md"]
    assert (deleted.folders, sorted(deleted.notes)) == (["/finance", "/finance/pricing"], sorted([filed.budget, filed.price_list]))
    assert sorted(deleted.sources) == sorted([filed.raw_budget, filed.raw_prices])
    assert "finance/" not in body(classic_wiki, filed.plan).replace("example.com/finance/", "")
    assert [n.rel for n in classic_wiki.tree().all_notes()] == [filed.plan]
    assert classic_wiki.check() == []
    assert "**Deleted folder**: /finance, with 2 notes" in (classic_wiki.cfg.bundle / LOG).read_text(encoding="utf-8")


def test_clear_when_the_wiki_is_full_then_it_is_empty_valid_and_other_files_stay(classic_wiki: Wiki, filed: Filed) -> None:
    # ARRANGE
    photo = classic_wiki.cfg.bundle / "photo.jpg"
    photo.write_bytes(b"\xff\xd8")
    orphan = classic_wiki.store.save_raw("No note cites it.", title="Orphan", resource=None)

    # ACT
    deleted = classic_wiki.clear()

    # ASSERT
    assert (len(deleted.notes), len(deleted.folders), len(deleted.sources)) == (3, 2, 4)
    assert orphan in deleted.sources
    assert classic_wiki.tree().to_dict() == {"path": "/", "description": "", "notes": [], "subfolders": []}
    assert list((classic_wiki.cfg.bundle / RAW).iterdir()) == []
    log = (classic_wiki.cfg.bundle / LOG).read_text(encoding="utf-8")
    assert "Emptied the wiki" in log and "Created" not in log
    assert photo.exists()
    assert classic_wiki.check() == []


@pytest.mark.parametrize(
    ("call", "path", "error"),
    [
        ("delete_note", "finance/missing.md", FileNotFoundError),
        ("delete_note", "../outside.md", FileNotFoundError),
        ("delete_note", "raw/anything.md", FileNotFoundError),  # raw copies go only with the notes that cite them
        ("delete_folder", "/missing", FileNotFoundError),
        ("delete_folder", "/", InputError),
    ],
)
def test_delete_when_the_path_is_no_note_or_folder_of_the_tree_then_nothing_goes(
    classic_wiki: Wiki, filed: Filed, call: str, path: str, error: type[Exception]
) -> None:
    # ACT
    with pytest.raises(error):
        getattr(classic_wiki, call)(path)

    # ASSERT
    assert len(classic_wiki.tree().all_notes()) == 3


def test_delete_note_when_a_source_points_outside_raw_then_that_file_stays(classic_wiki: Wiki) -> None:
    # ARRANGE
    store = classic_wiki.store
    note = store.write_note(store.load(), title="Odd", summary="s", body="b", tags=[], source={"resource": f"/{RAW}/../{LOG}"})

    # ACT
    deleted = classic_wiki.delete_note(note.rel)

    # ASSERT
    assert deleted.sources == [] and exists(classic_wiki, LOG)


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("See [a](a.md).", "See a.\n"),
        ("See [a](./a.md#part) and [b](b.md).", "See a and [b](b.md).\n"),
        ("See [site](https://x.test/a.md) and [top](#a).", "See [site](https://x.test/a.md) and [top](#a)."),
        ("Body.\n\n# See also\n\n* [A](a.md) - one\n* [B](b.md) - two\n", "Body.\n\n# See also\n\n* [B](b.md) - two\n"),
        ("Body.\n\n# See also\n\n* [A](a.md) - one\n", "Body.\n"),
    ],
    ids=["inline", "anchor-and-other", "web-and-anchor-only", "see-also", "last-see-also"],
)
def test_drop_links_when_a_link_points_to_a_deleted_note_then_only_that_link_goes(text: str, kept: str) -> None:
    assert drop_links(text, "notes/here.md", lambda target: target == "notes/a.md") == kept


def test_delete_endpoints_answer_with_what_went_and_404_for_what_is_missing(client: TestClient, filed: Filed) -> None:
    # ACT
    missing = client.delete("/note", params={"path": "finance/missing.md"})
    no_folder = client.delete("/folder", params={"path": "/missing"})
    root = client.delete("/folder", params={"path": "/"})
    note = client.delete("/note", params={"path": filed.budget})
    folder = client.delete("/folder", params={"path": "/finance"})
    everything = client.delete("/wiki")

    # ASSERT
    assert (missing.status_code, no_folder.status_code, root.status_code) == (404, 404, 422)
    assert note.status_code == folder.status_code == everything.status_code == 200
    assert note.json()["notes"] == [filed.budget]
    assert folder.json()["folders"] == ["/finance", "/finance/pricing"]
    assert everything.json()["notes"] == [filed.plan]
    assert client.get("/tree").json()["notes"] == []


def test_delete_from_another_site_is_refused(client: TestClient, classic_wiki: Wiki, filed: Filed) -> None:
    # ACT
    response = client.delete("/wiki", headers={"origin": "https://evil.test"})

    # ASSERT
    assert response.status_code == 403
    assert len(classic_wiki.tree().all_notes()) == 3
