# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: a note whose title or summary changes is shown with the new ones in the See-also entries that link to it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from llmw2 import Origin, Wiki
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_librarian_classic import (
    CONSOLIDATE,
    FIND_MATCH,
    MERGE,
    RELATE,
    ROUTE,
    SUMMARIZE,
    add_folder,
    add_note,
    draft,
    make_wiki,
    read,
)
from tests.wiki.test_revisions_replace import (
    NEW,
    REV_ORIGIN_ID,
    REV_TITLE,
    TITLE,
    archive_files,
    crow_setup,
    merge_reply,
    seed,
    summarize_reply,
)
from tests.wiki.test_revisions_replace import make_wiki as make_replace_wiki

OLD_ENTRY = "* [Acme pricing](acme-pricing.md) - About Acme pricing."
REV_NOTE = "wholesale-programme-review-2026.md"
NEW_REV_TITLE = "Wholesale programme review 2026 rev 2"


def entries(body: str, target: str) -> list[str]:
    """The See-also lines of `body` that link to a file called `target`."""
    section = body.split("# See also", 1)[1] if "# See also" in body else ""
    return [line for line in section.splitlines() if line.startswith("* [") and line.split("](", 1)[1].split(")", 1)[0].endswith(target)]


def merge_setup(bundle: Path, llm: FakeLLM, new_title: str, new_summary: str) -> tuple[Wiki, Paths]:
    """/finance holds Acme pricing, linked to Acme contracts (same folder) and to Overview (at the root); a merge into pricing follows."""
    wiki = make_wiki(bundle, llm)
    add_folder(wiki, "/", "finance", "Money matters.")
    overview = add_note(wiki, "/", "Overview")
    pricing, contracts = add_note(wiki, "/finance", "Acme pricing"), add_note(wiki, "/finance", "Acme contracts")
    wiki.store.link(pricing, contracts)
    wiki.store.link(pricing, overview)
    llm.add(SUMMARIZE, draft("Acme price rise")).add(ROUTE, {"action": "descend", "subfolder": "finance"}, {"action": "here"})
    llm.add(FIND_MATCH, {"match": "finance/acme-pricing.md"}).add(CONSOLIDATE, {"decision": "modify"})
    llm.add(MERGE, {"title": new_title, "summary": new_summary, "tags": [], "body": "# Overview\n\nMerged facts."})
    llm.add(RELATE, {"related": []})
    return wiki, Paths(pricing.rel, contracts.rel, overview.rel)


class Paths:
    def __init__(self, pricing: str, contracts: str, overview: str) -> None:
        self.pricing, self.contracts, self.overview = pricing, contracts, overview


def text_of(wiki: Wiki, rel: str) -> str:
    return (wiki.cfg.bundle / rel).read_text(encoding="utf-8")


# -- a merge ----------------------------------------------------------------------------------------------


def test_a_merge_that_changes_the_title_and_summary_updates_every_see_also_entry_that_links_to_the_note(bundle: Path, llm: FakeLLM) -> None:
    wiki, notes = merge_setup(bundle, llm, "Acme pricing 2026", "The 2026 price list.")

    result = wiki.ingest("Acme raised prices.")

    assert result.action == "merged" and result.note == notes.pricing
    [in_contracts] = entries(read(bundle / notes.contracts).body, "acme-pricing.md")
    assert in_contracts == "* [Acme pricing 2026](acme-pricing.md) - The 2026 price list."
    [in_overview] = entries(read(bundle / notes.overview).body, "acme-pricing.md")
    assert in_overview == "* [Acme pricing 2026](finance/acme-pricing.md) - The 2026 price list."


def test_a_merge_that_changes_only_the_summary_updates_the_entries(bundle: Path, llm: FakeLLM) -> None:
    wiki, notes = merge_setup(bundle, llm, "Acme pricing", "A brand new summary.")

    wiki.ingest("Acme raised prices.")

    [entry] = entries(read(bundle / notes.contracts).body, "acme-pricing.md")
    assert entry == "* [Acme pricing](acme-pricing.md) - A brand new summary."


def test_a_merge_that_changes_only_the_title_updates_the_entries(bundle: Path, llm: FakeLLM) -> None:
    wiki, notes = merge_setup(bundle, llm, "Acme pricing, renamed", "About Acme pricing.")

    wiki.ingest("Acme raised prices.")

    [entry] = entries(read(bundle / notes.contracts).body, "acme-pricing.md")
    assert entry == "* [Acme pricing, renamed](acme-pricing.md) - About Acme pricing."


def test_only_the_entry_changes_in_the_notes_that_link_to_the_merged_one(bundle: Path, llm: FakeLLM) -> None:
    wiki, notes = merge_setup(bundle, llm, "Acme pricing 2026", "The 2026 price list.")
    before = text_of(wiki, notes.contracts)
    assert OLD_ENTRY in before.splitlines()

    wiki.ingest("Acme raised prices.")

    after = text_of(wiki, notes.contracts)
    assert after == before.replace(OLD_ENTRY, "* [Acme pricing 2026](acme-pricing.md) - The 2026 price list.")


def test_a_merge_that_changes_nothing_leaves_the_linking_notes_as_they_were(bundle: Path, llm: FakeLLM) -> None:
    wiki, notes = merge_setup(bundle, llm, "Acme pricing", "About Acme pricing.")
    before = {rel: text_of(wiki, rel) for rel in (notes.contracts, notes.overview)}

    wiki.ingest("Acme raised prices.")

    assert {rel: text_of(wiki, rel) for rel in before} == before


def test_the_file_of_a_merged_note_is_not_renamed_when_its_title_changes(bundle: Path, llm: FakeLLM) -> None:
    wiki, notes = merge_setup(bundle, llm, "Acme pricing 2026", "The 2026 price list.")
    files = sorted(p.name for p in (bundle / "finance").glob("*.md"))

    result = wiki.ingest("Acme raised prices.")

    assert result.note == notes.pricing == "finance/acme-pricing.md"
    assert sorted(p.name for p in (bundle / "finance").glob("*.md")) == files
    assert read(bundle / notes.pricing).frontmatter["title"] == "Acme pricing 2026"


def test_a_link_target_stays_the_same_when_the_entry_is_refreshed(bundle: Path, llm: FakeLLM) -> None:
    wiki, _ = merge_setup(bundle, llm, "Acme pricing 2026", "The 2026 price list.")

    wiki.ingest("Acme raised prices.")

    assert (bundle / "finance" / "acme-pricing.md").is_file()
    assert wiki.check() == []


def test_archived_versions_are_not_refreshed(bundle: Path, llm: FakeLLM) -> None:
    wiki, notes = merge_setup(bundle, llm, "Acme pricing 2026", "The 2026 price list.")
    contracts = wiki.store.load().find("/finance").notes
    archived = wiki.store.archive_note(next(n for n in contracts if n.rel == notes.contracts))
    before = text_of(wiki, archived.lstrip("/"))
    assert OLD_ENTRY in before.splitlines()

    wiki.ingest("Acme raised prices.")

    assert text_of(wiki, archived.lstrip("/")) == before


# -- a revision with revisions: replace -------------------------------------------------------------------


def revise(bundle: Path, llm: FakeLLM, *, with_links: bool = True) -> tuple[Wiki, Any, Any]:
    """A note filed from a document, one other note linking to it, and a revision of the document filed with `replace`."""
    wiki = make_replace_wiki(bundle, llm)
    note = seed(wiki)
    other = wiki.store.write_note(
        wiki.store.load(), title="Other", summary="O.", body="Other.", tags=[], source={"resource": "/raw/o.md", "title": "O"}
    )
    if with_links:
        wiki.store.link(note, other)
    llm.add(SUMMARIZE, summarize_reply()).add(ROUTE, {"action": "new"})
    llm.add(FIND_MATCH, {"match": REV_NOTE}).add(CONSOLIDATE, {"decision": "modify"}).add(MERGE, merge_reply())
    llm.add(RELATE, {"related": []})
    return wiki, wiki.ingest(NEW, title=REV_TITLE, origin=Origin("local", "", REV_ORIGIN_ID)), other


def test_a_revision_gives_the_note_the_title_written_for_the_new_version(bundle: Path, llm: FakeLLM) -> None:
    _, result, _ = revise(bundle, llm)

    assert result.action == "merged" and MERGE not in llm.ops  # the replace path, not the normal merge
    assert read(bundle / REV_NOTE).frontmatter["title"] == NEW_REV_TITLE
    assert result.title == NEW_REV_TITLE


def test_a_revision_keeps_the_notes_file_path_and_archives_the_old_version(bundle: Path, llm: FakeLLM) -> None:
    _, result, _ = revise(bundle, llm)

    assert result.note == REV_NOTE
    assert sorted(p.name for p in bundle.glob("*.md")) == ["index.md", "log.md", "other.md", REV_NOTE]
    [old] = archive_files(bundle)
    assert read(old).frontmatter["title"] == TITLE  # the archived version is the note as it was
    assert read(bundle / REV_NOTE).frontmatter["previous_version"].startswith("/.archive/")


def test_a_revision_refreshes_the_see_also_entries_that_link_to_the_note(bundle: Path, llm: FakeLLM) -> None:
    _, _, other = revise(bundle, llm)

    [entry] = entries(read(bundle / other.rel).body, REV_NOTE)
    assert entry == f"* [{NEW_REV_TITLE}]({REV_NOTE}) - What {NEW_REV_TITLE} says."


def test_a_revision_lists_the_new_title_in_its_folder_index(bundle: Path, llm: FakeLLM) -> None:
    revise(bundle, llm)

    index = (bundle / "index.md").read_text(encoding="utf-8")
    assert f"* [{NEW_REV_TITLE}]({REV_NOTE}) - What {NEW_REV_TITLE} says." in index.splitlines()
    assert f"[{TITLE}]({REV_NOTE})" not in index


def test_a_revision_leaves_the_archived_version_of_the_linking_note_alone(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_replace_wiki(bundle, llm)
    note = seed(wiki)
    other = wiki.store.write_note(
        wiki.store.load(), title="Other", summary="O.", body="Other.", tags=[], source={"resource": "/raw/o.md", "title": "O"}
    )
    wiki.store.link(note, other)
    archived = wiki.store.archive_note(other).lstrip("/")
    before = text_of(wiki, archived)
    llm.add(SUMMARIZE, summarize_reply()).add(ROUTE, {"action": "new"})
    llm.add(FIND_MATCH, {"match": REV_NOTE}).add(CONSOLIDATE, {"decision": "modify"}).add(MERGE, merge_reply())
    llm.add(RELATE, {"related": []})

    wiki.ingest(NEW, title=REV_TITLE, origin=Origin("local", "", REV_ORIGIN_ID))

    assert text_of(wiki, archived) == before


def test_crow_a_revision_gives_the_note_the_title_written_for_the_new_version(bundle: Path, llm: FakeLLM, tracker: UsageTracker) -> None:
    wiki, _ = crow_setup(bundle, llm, tracker, identity=0.1)

    result = wiki.ingest(NEW, title=REV_TITLE, origin=Origin("local", "", REV_ORIGIN_ID))

    assert result.note == f"wholesale/{REV_NOTE}"
    assert read(bundle / result.note).frontmatter["title"] == NEW_REV_TITLE
    assert MERGE not in llm.ops
