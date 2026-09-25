# Copyright 2026 Federico Cesarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The classic Librarian: every decision of an ingest is taken by the (fake) LLM."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from okf_wiki.document import OKFDocument

from okf_wiki import Wiki, WikiConfig
from okf_wiki.librarian import IngestResult
from okf_wiki.store import Note
from okf_wiki.usage import UsageTracker
from tests.wiki.fakes import FakeLLM

SUMMARIZE, ROUTE, NAME_FOLDER = "librarian/summarize", "librarian/route", "librarian/name_folder"
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


def test_first_ingest_here_files_note_at_root(classic_wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "here"})

    result = classic_wiki.ingest("Acme raised prices.", title="Acme memo", resource="https://acme.test/memo")

    assert (result.action, result.note, result.folder) == ("created", "acme-pricing.md", "/")
    assert result.created_folders == []
    fm = read(bundle / "acme-pricing.md").frontmatter
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
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "here"})

    classic_wiki.ingest("Acme raised prices.", title="Acme memo", resource="https://acme.test/memo")

    raw_path = read(bundle / "acme-pricing.md").frontmatter["sources"][0]["resource"]
    raw = read(bundle / raw_path.lstrip("/"))
    assert raw.frontmatter["type"] == "Source"
    assert (raw.frontmatter["title"], raw.frontmatter["resource"]) == ("Acme memo", "https://acme.test/memo")
    assert raw.body.strip() == "Acme raised prices."
    index = read(bundle / "index.md")
    assert index.frontmatter == {"okf_version": "0.2"}
    assert "* [Acme pricing](acme-pricing.md) - What Acme pricing says." in index.body.splitlines()
    log = (bundle / "log.md").read_text(encoding="utf-8")
    assert re.search(r"^## \d{4}-\d{2}-\d{2}$", log, re.MULTILINE)
    assert f"* **Created**: [Acme pricing](acme-pricing.md) - from {raw_path} (classic)" in log.splitlines()


def test_empty_pool_skips_find_match_and_relate(classic_wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "here"})

    result = classic_wiki.ingest("Acme raised prices.")

    assert llm.ops == [SUMMARIZE, ROUTE]
    assert result.related == []
    assert steps(result) == [("route", "llm", "/ (Here)")]


def test_route_new_at_root_creates_named_folder(classic_wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "new"})
    llm.add(NAME_FOLDER, {"name": "pricing", "description": "Prices and discounts of every supplier."})

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
    llm.add(SUMMARIZE, draft("Q3 revenue")).add(ROUTE, {"action": "descend", "subfolder": "marketing"})

    result = classic_wiki.ingest("Revenue grew.")

    assert llm.ops == [SUMMARIZE, ROUTE]
    assert (result.folder, result.note, result.created_folders) == ("/", "q3-revenue.md", [])
    assert steps(result) == [("route", "llm", "/ (Here)")]


def test_leaf_folder_at_max_depth_is_decided_by_rule(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm, max_depth=1)
    add_folder(wiki, "/", "finance", "Money matters.")
    llm.add(SUMMARIZE, draft("Q3 revenue")).add(ROUTE, {"action": "descend", "subfolder": "finance"})

    result = wiki.ingest("Revenue grew.")

    assert llm.ops == [SUMMARIZE, ROUTE]
    assert result.note == "finance/q3-revenue.md"
    assert steps(result) == [("route", "llm", "/finance"), ("route", "rule", "/finance")]


def test_route_never_reaches_below_max_depth(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm, max_depth=1)
    add_folder(wiki, "/", "finance", "Money matters.")
    add_folder(wiki, "/finance", "tax", "Taxes.")
    llm.add(SUMMARIZE, draft("VAT rates"))
    llm.add(ROUTE, {"action": "descend", "subfolder": "finance"}, {"action": "descend", "subfolder": "tax"})

    result = wiki.ingest("VAT is 22%.")

    assert result.folder == "/finance"


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
    add_note(classic_wiki, "/", "Acme contracts")
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "here"})
    llm.add(FIND_MATCH, {"match": None}).add(RELATE, {"related": []})

    result = classic_wiki.ingest("Acme raised prices.")

    assert llm.ops == [SUMMARIZE, ROUTE, FIND_MATCH, RELATE]
    assert (result.action, result.note) == ("created", "acme-pricing.md")
    assert ("match", "llm", "none") in steps(result)
    assert len(read(bundle / "acme-contracts.md").frontmatter["sources"]) == 1


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
        {"id": "s2", "resource": f"/raw/{raw.name}", "title": "Acme price rise"},
    ]
    assert "* **Merged**: [Acme pricing 2026](acme-pricing.md)" in (bundle / "log.md").read_text(encoding="utf-8")


def test_match_and_consolidate_new_writes_a_note_next_to_it(classic_wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    add_note(classic_wiki, "/", "Acme pricing")
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "here"})
    llm.add(FIND_MATCH, {"match": "acme-pricing.md"}).add(CONSOLIDATE, {"decision": "new"})
    llm.add(RELATE, {"related": []})

    result = classic_wiki.ingest("Acme raised prices.")

    assert llm.ops == [SUMMARIZE, ROUTE, FIND_MATCH, CONSOLIDATE, RELATE]
    assert (result.action, result.note) == ("created", "acme-pricing-2.md")
    assert read(bundle / "acme-pricing.md").body.startswith("# Overview\n\nSeeded.")
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
    for title in ("Apple", "Banana", "Cherry"):
        add_note(wiki, "/", title)
    llm.add(SUMMARIZE, draft("Fruit prices")).add(ROUTE, {"action": "here"}).add(FIND_MATCH, {"match": None})
    llm.add(RELATE, {"related": ["cherry.md", "apple.md", "banana.md"]})

    result = wiki.ingest("Fruit got dearer.")

    assert "At most 2 links" in prompt_of(llm, RELATE)
    assert result.related == ["cherry.md", "apple.md"]
    see_also = read(bundle / "fruit-prices.md").body.split("# See also\n\n")[1].splitlines()
    assert see_also == ["* [Cherry](cherry.md) - About Cherry.", "* [Apple](apple.md) - About Apple."]
    back_link = "* [Fruit prices](fruit-prices.md) - What Fruit prices says."
    assert back_link in read(bundle / "cherry.md").body.splitlines()
    assert back_link in read(bundle / "apple.md").body.splitlines()
    assert "# See also" not in read(bundle / "banana.md").body


# -- options, usage and results ---------------------------------------------------------------


def test_summarize_off_files_the_source_verbatim(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm, summarize=False)
    text = "# Quarterly Report\n\nRevenue grew 10%. Costs fell.\n"
    llm.add(ROUTE, {"action": "here"})

    result = wiki.ingest(text)

    assert llm.ops == [ROUTE]
    assert (result.title, result.note) == ("Quarterly Report", "quarterly-report.md")
    note = read(bundle / "quarterly-report.md")
    assert note.frontmatter["title"] == "Quarterly Report"
    assert note.body.strip() == text.strip()


def test_summarize_off_summary_has_no_heading_markup(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm, summarize=False)
    llm.add(ROUTE, {"action": "here"})

    wiki.ingest("# Quarterly Report\n\nRevenue grew 10%. Costs fell.\n")

    assert not read(bundle / "quarterly-report.md").frontmatter["description"].startswith("#")


def test_usage_goes_to_the_llm_ledger_only(classic_wiki: Wiki, llm: FakeLLM, tracker: UsageTracker) -> None:
    add_note(classic_wiki, "/", "Acme contracts")
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "here"})
    llm.add(FIND_MATCH, {"match": None}).add(RELATE, {"related": []})

    usage = classic_wiki.ingest("Acme raised prices.").usage

    assert (usage.llm.calls, usage.llm.input_tokens, usage.llm.output_tokens, usage.llm.missing) == (4, 400, 40, 0)
    assert usage.classifier.calls == 0 and usage.classifier.total_tokens == 0
    assert list(usage.by_model) == ["llm:fake/llm"]
    assert tracker.snapshot().to_dict() == usage.to_dict()


def test_usage_of_a_result_counts_only_its_own_ingest(classic_wiki: Wiki, llm: FakeLLM, tracker: UsageTracker) -> None:
    llm.add(SUMMARIZE, draft("Acme pricing"), draft("Acme contracts"))
    llm.add(ROUTE, {"action": "here"}, {"action": "here"})
    llm.add(FIND_MATCH, {"match": None}).add(RELATE, {"related": []})

    first = classic_wiki.ingest("Acme raised prices.")
    second = classic_wiki.ingest("Acme signed a contract.")

    assert (first.usage.llm.calls, second.usage.llm.calls, tracker.snapshot().llm.calls) == (2, 4, 6)


def test_result_to_dict_is_json_serialisable(classic_wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(SUMMARIZE, draft("Acme pricing")).add(ROUTE, {"action": "here"})

    data = json.loads(json.dumps(classic_wiki.ingest("Acme raised prices.").to_dict()))

    assert data["action"] == "created" and data["note"] == "acme-pricing.md"
    assert data["decisions"] == [
        {"step": "route", "decider": "llm", "choice": "/ (Here)", "confidence": None, "fallback": False, "scores": {}}
    ]
    assert data["usage"]["llm"]["calls"] == 2 and data["usage"]["classifier"]["calls"] == 0


@pytest.mark.parametrize("text", ["", "   \n\t"])
def test_empty_text_raises(classic_wiki: Wiki, llm: FakeLLM, text: str) -> None:
    with pytest.raises(ValueError):
        classic_wiki.ingest(text)
    assert llm.calls == []
