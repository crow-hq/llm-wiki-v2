# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The classic Librarian: every decision of an ingest is taken by the (fake) LLM."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from llmw2 import Wiki, WikiConfig
from llmw2.agents.librarian import IngestResult
from llmw2.agents.steps import CLASSIC
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.origin import content_hash
from llmw2.bundle.tree import Note
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeLLM

SUMMARIZE, ROUTE, NAME_FOLDER = "librarian/summarize", "librarian/route", "librarian/name_folder"
PRICING = {"name": "pricing", "description": "Prices and discounts of every supplier."}  # the root holds no notes
FIND_MATCH, CONSOLIDATE, MERGE, RELATE = (
    "librarian/find_match",
    "librarian/consolidate",
    "librarian/merge",
    "librarian/relate",
)


# -- helpers ----------------------------------------------------------------------


def draft(title: str, body: str = "# Overview\n\nThe facts.") -> dict[str, Any]:
    """A librarian/summarize (or librarian/merge) reply."""
    return {"title": title, "summary": f"What {title} says.", "tags": ["Topic"], "body": body}


def make_wiki(bundle: Path, llm: FakeLLM, **cfg: Any) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", **cfg), llm=llm)
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


def read(path: Path) -> OKFDocument:
    return OKFDocument.parse(path.read_text(encoding="utf-8"))


def prompt_of(llm: FakeLLM, op: str) -> str:
    """The user message of the first call made for `op`."""
    return next(messages for o, messages in llm.calls if o == op)[-1]["content"]


def steps(result: IngestResult) -> list[tuple[str, str, str]]:
    return [(d.step, d.decider, d.choice) for d in result.decisions]


# -- first ingest and routing ------------------------------------------------------------


def test_first_ingest_when_the_llm_says_here_at_the_root_then_the_note_opens_a_folder(
    classic_wiki: Wiki, llm: FakeLLM, bundle: Path
) -> None:
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "here"}).add(NAME_FOLDER, PRICING)

    result = classic_wiki.ingest("Acme raised prices.", title="Acme memo", resource="https://acme.test/memo")

    assert (result.action, result.note, result.folder) == ("created", "pricing/acme-pricing.md", "/pricing")
    assert result.created_folders == ["/pricing"]  # the root holds no notes
    fm = read(bundle / "pricing" / "acme-pricing.md").frontmatter
    assert (fm["type"], fm["title"], fm["description"], fm["tags"]) == (
        "Note",
        "Acme pricing",
        "What Acme pricing says.",
        ["topic"],
    )
    assert fm["generated"]["by"] == classic_wiki.cfg.actor
    assert datetime.fromisoformat(fm["generated"]["at"]).tzinfo is not None
    [source] = fm["sources"]
    assert (source["id"], source["title"]) == ("s1", "Acme memo")
    assert re.fullmatch(r"/raw/[\w-]+\.md", source["resource"])


def test_first_ingest_writes_raw_copy_index_and_log(classic_wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "new"}).add(NAME_FOLDER, PRICING)

    classic_wiki.ingest("Acme raised prices.", title="Acme memo", resource="https://acme.test/memo")

    raw_path = read(bundle / "pricing" / "acme-pricing.md").frontmatter["sources"][0]["resource"]
    raw = read(bundle / raw_path.lstrip("/"))
    assert raw.frontmatter["type"] == "Source"
    assert (raw.frontmatter["title"], raw.frontmatter["resource"]) == ("Acme memo", "https://acme.test/memo")
    assert raw.body.strip() == "Acme raised prices."
    index = read(bundle / "index.md")
    assert index.frontmatter == {"okf_version": "0.2"}
    pricing = (bundle / "pricing" / "index.md").read_text(encoding="utf-8")
    assert "* [Acme pricing](acme-pricing.md) - What Acme pricing says." in pricing.splitlines()
    log = (bundle / "log.md").read_text(encoding="utf-8")
    assert re.search(r"^## \d{4}-\d{2}-\d{2}$", log, re.MULTILINE)
    assert f"* **Created**: [Acme pricing](pricing/acme-pricing.md) - from {raw_path} (classic {CLASSIC.hash})" in log.splitlines()


def test_empty_pool_skips_find_match_and_relate(classic_wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "new"}).add(NAME_FOLDER, PRICING)

    result = classic_wiki.ingest("Acme raised prices.")

    assert llm.ops == [SUMMARIZE, ROUTE, NAME_FOLDER]
    assert result.related == []
    assert steps(result) == [("route", "llm", "/ (New subfolder)")]


def test_route_new_at_root_creates_named_folder(classic_wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "new"})
    llm.add(NAME_FOLDER, PRICING)

    result = classic_wiki.ingest("Acme raised prices.")

    assert llm.ops == [SUMMARIZE, ROUTE, NAME_FOLDER]
    assert (result.created_folders, result.folder, result.note) == (["/pricing"], "/pricing", "pricing/acme-pricing.md")
    assert (bundle / "pricing" / "acme-pricing.md").is_file()
    root_index = read(bundle / "index.md").body.splitlines()
    assert "* [pricing](pricing/index.md) - Prices and discounts of every supplier." in root_index
    sub_index = (bundle / "pricing" / "index.md").read_text(encoding="utf-8")
    assert not sub_index.startswith("---")
    assert "* [Acme pricing](acme-pricing.md) - What Acme pricing says." in sub_index.splitlines()


def test_route_descends_into_existing_folder_then_stays(classic_wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    add_folder(classic_wiki, "/", "finance", "Money matters.")
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    llm.add(ROUTE, {"action": "descend", "subfolder": "finance"}, {"action": "here"})

    result = classic_wiki.ingest("Revenue grew.")

    assert llm.ops == [SUMMARIZE, ROUTE, ROUTE]
    assert "You are in the folder /finance: Money matters." in llm.calls[2][1][-1]["content"]
    assert (result.folder, result.note) == ("/finance", "finance/q3-revenue.md")
    assert (bundle / "finance" / "q3-revenue.md").is_file()
    assert steps(result) == [("route", "llm", "/finance"), ("route", "llm", "/finance (Here)")]


def test_descend_to_unknown_subfolder_is_treated_as_here(classic_wiki: Wiki, llm: FakeLLM) -> None:
    add_folder(classic_wiki, "/", "finance", "Money matters.")
    add_folder(classic_wiki, "/finance", "tax", "Taxes.")
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    llm.add(ROUTE, {"action": "descend", "subfolder": "finance"}, {"action": "descend", "subfolder": "marketing"})

    result = classic_wiki.ingest("Revenue grew.")

    assert llm.ops == [SUMMARIZE, ROUTE, ROUTE]
    assert (result.folder, result.note, result.created_folders) == ("/finance", "finance/q3-revenue.md", [])
    assert steps(result) == [("route", "llm", "/finance"), ("route", "llm", "/finance (Here)")]


def test_descend_to_unknown_subfolder_at_the_root_opens_a_folder(classic_wiki: Wiki, llm: FakeLLM) -> None:
    add_folder(classic_wiki, "/", "finance", "Money matters.")
    llm.add(SUMMARIZE, draft("Q3 revenue")).add(ROUTE, {"action": "descend", "subfolder": "marketing"})
    llm.add(NAME_FOLDER, {"name": "marketing", "description": "Campaigns and brand."})

    result = classic_wiki.ingest("Revenue grew.")

    assert (result.folder, result.note, result.created_folders) == ("/marketing", "marketing/q3-revenue.md", ["/marketing"])


def test_route_when_the_llm_enters_a_folder_at_max_depth_then_a_rule_stops_it_there(bundle: Path, llm: FakeLLM) -> None:
    # ARRANGE
    wiki = make_wiki(bundle, llm, max_depth=1)
    add_folder(wiki, "/", "finance", "Money matters.")
    add_folder(wiki, "/finance", "tax", "Taxes.")
    llm.add(SUMMARIZE, draft("Q3 revenue")).add(ROUTE, {"action": "descend", "subfolder": "finance"})

    # ACT
    result = wiki.ingest("Revenue grew.")

    # ASSERT
    assert llm.ops == [SUMMARIZE, ROUTE]
    assert (result.folder, result.note) == ("/finance", "finance/q3-revenue.md")
    assert steps(result) == [("route", "llm", "/finance"), ("route", "rule", "/finance")]


def test_at_max_depth_routing_is_decided_by_rule(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm, max_depth=0)
    add_folder(wiki, "/", "finance", "Money matters.")
    llm.add(SUMMARIZE, draft("Q3 revenue"))

    result = wiki.ingest("Revenue grew.")

    assert llm.ops == [SUMMARIZE]
    assert [(d.step, d.decider) for d in result.decisions] == [("route", "rule")]
    assert (result.folder, result.note, result.created_folders) == ("/", "q3-revenue.md", [])
    assert sorted(p.name for p in bundle.iterdir() if p.is_dir()) == ["finance", "raw"]


def test_name_folder_reuses_existing_sibling(classic_wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    add_folder(classic_wiki, "/", "finance", "Money matters.")
    llm.add(SUMMARIZE, draft("Q3 revenue")).add(ROUTE, {"action": "new"})
    llm.add(NAME_FOLDER, {"name": "Finance", "description": "A second description."})

    result = classic_wiki.ingest("Revenue grew.")

    assert (result.created_folders, result.note) == ([], "finance/q3-revenue.md")
    assert sorted(p.name for p in bundle.iterdir() if p.is_dir()) == ["finance", "raw"]
    subdirs = [line for line in read(bundle / "index.md").body.splitlines() if "/index.md)" in line]
    assert subdirs == ["* [finance](finance/index.md) - Money matters."]


# -- consolidation ----------------------------------------------------------------------------


def test_no_match_creates_a_new_note(classic_wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    add_folder(classic_wiki, "/", "acme", "The supplier Acme.")
    add_note(classic_wiki, "/acme", "Acme contracts")
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "descend", "subfolder": "acme"}, {"action": "here"})
    llm.add(FIND_MATCH, {"match": None}).add(RELATE, {"related": []})

    result = classic_wiki.ingest("Acme raised prices.")

    assert llm.ops == [SUMMARIZE, ROUTE, ROUTE, FIND_MATCH, RELATE]
    assert (result.action, result.note) == ("created", "acme/acme-pricing.md")
    assert ("match", "llm", "none") in steps(result)
    assert len(read(bundle / "acme" / "acme-contracts.md").frontmatter["sources"]) == 1


def test_match_and_modify_merges_the_note_in_place(classic_wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    pricing, contracts = add_note(classic_wiki, "/", "Acme pricing"), add_note(classic_wiki, "/", "Acme contracts")
    classic_wiki.store.link(pricing, contracts)
    llm.add(SUMMARIZE, draft("Acme price rise")).add(ROUTE, {"action": "new"})
    llm.add(FIND_MATCH, {"match": "acme-pricing.md"}).add(CONSOLIDATE, {"decision": "modify"})
    llm.add(MERGE, draft("Acme pricing 2026", body="# Overview\n\nMerged facts.")).add(RELATE, {"related": []})

    result = classic_wiki.ingest("Acme raised prices.")

    assert llm.ops == [SUMMARIZE, ROUTE, FIND_MATCH, CONSOLIDATE, MERGE, RELATE]
    assert (result.action, result.note, result.title, result.created_folders) == (
        "merged",
        "acme-pricing.md",
        "Acme pricing 2026",
        [],
    )
    assert sorted(p.name for p in bundle.glob("*.md")) == ["acme-contracts.md", "acme-pricing.md", "index.md", "log.md"]
    assert [p.name for p in bundle.iterdir() if p.is_dir()] == ["raw"]
    note = read(bundle / "acme-pricing.md")
    assert note.frontmatter["title"] == "Acme pricing 2026"
    assert note.body.startswith("# Overview\n\nMerged facts.\n\n# See also\n")
    assert "* [Acme contracts](acme-contracts.md) - About Acme contracts." in note.body.splitlines()
    [raw] = (bundle / "raw").iterdir()
    assert note.frontmatter["sources"] == [
        {"id": "s1", "resource": "/raw/seed.md", "title": "Seed"},
        {
            "id": "s2",
            "resource": f"/raw/{raw.name}",
            "title": "Acme price rise",
            "hash": content_hash("Acme raised prices."),
            "steps": {"name": "classic", "hash": CLASSIC.hash},
        },
    ]
    assert "* **Merged**: [Acme pricing 2026](acme-pricing.md)" in (bundle / "log.md").read_text(encoding="utf-8")


def test_match_and_consolidate_new_writes_a_note_next_to_it(classic_wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    add_folder(classic_wiki, "/", "acme", "The supplier Acme.")
    add_note(classic_wiki, "/acme", "Acme pricing")
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "descend", "subfolder": "acme"}, {"action": "here"})
    llm.add(FIND_MATCH, {"match": "acme/acme-pricing.md"}).add(CONSOLIDATE, {"decision": "new"})
    llm.add(RELATE, {"related": []})

    result = classic_wiki.ingest("Acme raised prices.")

    assert llm.ops == [SUMMARIZE, ROUTE, ROUTE, FIND_MATCH, CONSOLIDATE, RELATE]
    assert (result.action, result.note) == ("created", "acme/acme-pricing-2.md")
    assert read(bundle / "acme" / "acme-pricing.md").body.startswith("# Overview\n\nSeeded.")
    assert ("consolidate", "llm", "new") in steps(result)


def test_consolidation_candidates_include_notes_of_ancestor_folders(classic_wiki: Wiki, llm: FakeLLM) -> None:
    add_note(classic_wiki, "/", "Company overview")
    add_folder(classic_wiki, "/", "finance", "Money matters.")
    add_note(classic_wiki, "/finance", "Budget 2026")
    llm.add(SUMMARIZE, draft("Q3 revenue"))
    llm.add(ROUTE, {"action": "descend", "subfolder": "finance"}, {"action": "here"})
    llm.add(FIND_MATCH, {"match": None}).add(RELATE, {"related": []})

    classic_wiki.ingest("Revenue grew.")

    offered = prompt_of(llm, FIND_MATCH)
    assert "- company-overview.md: Company overview — About Company overview." in offered
    assert "- finance/budget-2026.md: Budget 2026 — About Budget 2026." in offered


# -- relating ---------------------------------------------------------------------------------


def test_relate_links_both_ways_capped_at_max_links(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm, max_links=2)
    add_folder(wiki, "/", "fruit", "Fruit and its prices.")
    for title in ("Apple", "Banana", "Cherry"):
        add_note(wiki, "/fruit", title)
    llm.add(SUMMARIZE, draft("Fruit prices")).add(ROUTE, {"action": "descend", "subfolder": "fruit"}, {"action": "here"})
    llm.add(FIND_MATCH, {"match": None}).add(RELATE, {"related": ["fruit/cherry.md", "fruit/apple.md", "fruit/banana.md"]})

    result = wiki.ingest("Fruit got dearer.")

    assert "At most 2 links" in prompt_of(llm, RELATE)
    assert result.related == ["fruit/cherry.md", "fruit/apple.md"]
    fruit = bundle / "fruit"
    see_also = read(fruit / "fruit-prices.md").body.split("# See also\n\n")[1].splitlines()
    assert see_also == ["* [Cherry](cherry.md) - About Cherry.", "* [Apple](apple.md) - About Apple."]
    back_link = "* [Fruit prices](fruit-prices.md) - What Fruit prices says."
    assert back_link in read(fruit / "cherry.md").body.splitlines()
    assert back_link in read(fruit / "apple.md").body.splitlines()
    assert "# See also" not in read(fruit / "banana.md").body


# -- options, usage and results ---------------------------------------------------------------


def test_ingest_when_summarize_is_off_then_files_the_source_verbatim(bundle: Path, llm: FakeLLM) -> None:
    # ARRANGE
    wiki = make_wiki(bundle, llm, summarize=False)
    text = "# Quarterly Report\n\nRevenue grew 10%. Costs fell.\n"
    llm.add(ROUTE, {"action": "new"}).add(NAME_FOLDER, {"name": "reports", "description": "Quarterly reports."})

    # ACT
    result = wiki.ingest(text)

    # ASSERT
    assert llm.ops == [ROUTE, NAME_FOLDER]
    assert (result.title, result.note) == ("Quarterly Report", "reports/quarterly-report.md")
    note = read(bundle / "reports" / "quarterly-report.md")
    assert note.frontmatter["title"] == "Quarterly Report"
    assert not note.frontmatter["description"].startswith("#")
    assert note.body.strip() == text.strip()


def test_usage_of_a_result_counts_only_its_own_ingest(classic_wiki: Wiki, llm: FakeLLM, tracker: UsageTracker) -> None:
    llm.add(SUMMARIZE, draft("Acme pricing"), draft("Acme contracts"))
    llm.add(ROUTE, {"action": "new"}).add(NAME_FOLDER, PRICING)
    llm.add(ROUTE, {"action": "descend", "subfolder": "pricing"}, {"action": "here"})
    llm.add(FIND_MATCH, {"match": None}).add(RELATE, {"related": []})

    first = classic_wiki.ingest("Acme raised prices.")
    second = classic_wiki.ingest("Acme signed a contract.")

    assert (first.usage.llm.calls, second.usage.llm.calls, tracker.snapshot().llm.calls) == (3, 5, 8)


def test_result_to_dict_is_json_serialisable(classic_wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "new"}).add(NAME_FOLDER, PRICING)

    data = json.loads(json.dumps(classic_wiki.ingest("Acme raised prices.").to_dict()))

    assert data["action"] == "created" and data["note"] == "pricing/acme-pricing.md"
    assert data["decisions"] == [
        {"step": "route", "decider": "llm", "choice": "/ (New subfolder)", "confidence": None, "fallback": False, "scores": {}}
    ]
    assert data["usage"]["llm"]["calls"] == 3 and data["usage"]["classifier"]["calls"] == 0

