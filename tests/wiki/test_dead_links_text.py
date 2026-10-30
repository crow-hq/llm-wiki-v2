# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: when a note is written, links that lead nowhere become plain text; links that resolve are left alone."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2 import Origin, Wiki
from llmw2.agents import revisions
from llmw2.bundle.document import OKFDocument
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
)
from tests.wiki.test_revisions_replace import (
    NEW,
    OLD,
    ORIGIN_ID,
    REV_ORIGIN_ID,
    REV_TITLE,
    TITLE,
    seed,
    summarize_reply,
)
from tests.wiki.test_revisions_replace import make_wiki as make_replace_wiki

NOTE = "finance/revenue-recognition.md"


def read(bundle: Path, rel: str) -> OKFDocument:
    return OKFDocument.parse((bundle / rel).read_text(encoding="utf-8"))


@pytest.fixture
def wiki(bundle: Path, llm: FakeLLM) -> Wiki:
    """/overview.md at the root and /finance with one note; the next ingest files into /finance."""
    wiki = make_wiki(bundle, llm)
    add_note(wiki, "/", "Overview")
    add_folder(wiki, "/", "finance", "Money matters.")
    add_note(wiki, "/finance", "Budget")
    return wiki


def file_note(wiki: Wiki, llm: FakeLLM, body: str, *, text: str = "Booked on delivery.", related: list[str] | None = None) -> str:
    """Ingest `text` into /finance with the writer's body `body`; returns the body the note was written with."""
    llm.add(SUMMARIZE, draft("Revenue recognition", body=body))
    llm.add(ROUTE, {"action": "descend", "subfolder": "finance"}, {"action": "here"})
    llm.add(FIND_MATCH, {"match": None}).add(RELATE, {"related": related or []})
    result = wiki.ingest(text, title="Memo")
    assert result.note == NOTE
    return read(wiki.cfg.bundle, NOTE).body


# -- a link to a file that does not exist -----------------------------------------------------------------


def test_a_markdown_link_to_a_missing_note_becomes_text_and_its_target(wiki: Wiki, llm: FakeLLM) -> None:
    body = file_note(wiki, llm, "# Overview\n\nSee [the policy](policy.md) for more.")

    assert "See the policy (policy.md) for more." in body
    assert "](policy.md)" not in body


def test_a_link_whose_text_is_its_target_becomes_the_target_alone(wiki: Wiki, llm: FakeLLM) -> None:
    body = file_note(wiki, llm, "# Overview\n\nSee [policy.md](policy.md) for more.")

    assert "See policy.md for more." in body
    assert "[policy.md]" not in body and "(policy.md)" not in body


def test_a_link_with_an_anchor_keeps_the_anchor_in_the_text(wiki: Wiki, llm: FakeLLM) -> None:
    body = file_note(wiki, llm, "# Overview\n\nSee [limits](policy.md#limits) now.")

    assert "See limits (policy.md#limits) now." in body


def test_a_link_to_a_missing_note_in_another_folder_becomes_text(wiki: Wiki, llm: FakeLLM) -> None:
    body = file_note(wiki, llm, "# Overview\n\nSee [old](../archive/old-policy.md).")

    assert "See old (../archive/old-policy.md)." in body


def test_a_rooted_link_to_a_missing_note_becomes_text(wiki: Wiki, llm: FakeLLM) -> None:
    body = file_note(wiki, llm, "# Overview\n\nSee [gone](/nowhere/gone.md).")

    assert "See gone (/nowhere/gone.md)." in body


def test_a_link_to_a_file_outside_the_wiki_folder_counts_as_missing_even_if_it_exists(wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    (bundle.parent / "outside.md").write_text("Outside the wiki.", encoding="utf-8")  # /finance/../../outside.md

    body = file_note(wiki, llm, "# Overview\n\nSee [outside](../../outside.md).")

    assert "See outside (../../outside.md)." in body


# -- wiki links -------------------------------------------------------------------------------------------


def test_a_wiki_link_becomes_its_name(wiki: Wiki, llm: FakeLLM) -> None:
    body = file_note(wiki, llm, "# Overview\n\nSee [[Revenue policy]] first.")

    assert "See Revenue policy first." in body and "[[" not in body


def test_a_wiki_link_with_a_label_becomes_the_label(wiki: Wiki, llm: FakeLLM) -> None:
    body = file_note(wiki, llm, "# Overview\n\nSee [[revenue-policy|the policy]] first.")

    assert "See the policy first." in body
    assert "revenue-policy" not in body and "[[" not in body


def test_an_embedded_wiki_link_becomes_its_name(wiki: Wiki, llm: FakeLLM) -> None:
    body = file_note(wiki, llm, "# Overview\n\nPicture: ![[chart]] here.")

    assert "Picture: chart here." in body
    assert "![[" not in body and "[[" not in body


# -- what is left alone -----------------------------------------------------------------------------------


def test_links_that_resolve_inside_the_wiki_are_left_alone(wiki: Wiki, llm: FakeLLM) -> None:
    body = file_note(wiki, llm, "# Overview\n\nSee [budget](budget.md), [overview](../overview.md#top) and [rooted](/overview.md).")

    assert "[budget](budget.md)" in body
    assert "[overview](../overview.md#top)" in body
    assert "[rooted](/overview.md)" in body


def test_a_link_to_a_raw_copy_is_left_alone(wiki: Wiki, llm: FakeLLM) -> None:
    raw = wiki.store.save_raw("The original.", title="Original", resource=None)

    body = file_note(wiki, llm, f"# Overview\n\nFrom [the original]({raw}).")

    assert f"[the original]({raw})" in body


def test_see_also_entries_are_left_alone(wiki: Wiki, llm: FakeLLM) -> None:
    body = file_note(wiki, llm, "# Overview\n\nFacts.", related=["finance/budget.md", "overview.md"])

    assert "* [Budget](budget.md) - About Budget." in body.splitlines()
    assert "* [Overview](../overview.md) - About Overview." in body.splitlines()


@pytest.mark.parametrize(
    "link", ["[docs](https://example.com/guide.md)", "[docs](http://example.com/guide.md#top)", "[mail](mailto:team@example.md)"]
)
def test_http_and_mailto_links_are_left_alone(wiki: Wiki, llm: FakeLLM, link: str) -> None:
    body = file_note(wiki, llm, f"# Overview\n\nSee {link} for more.")

    assert f"See {link} for more." in body


def test_a_link_that_does_not_end_in_md_is_left_alone(wiki: Wiki, llm: FakeLLM) -> None:
    body = file_note(wiki, llm, "# Overview\n\nSee [sheet](budget.xlsx) and [site](https://example.com/).")

    assert "[sheet](budget.xlsx)" in body and "[site](https://example.com/)" in body


def test_code_is_left_alone(wiki: Wiki, llm: FakeLLM) -> None:
    code = "```\n[gone](gone.md) and [[wiki]]\n```"
    inline = "Write `[gone](gone.md)` or `[[wiki|label]]` like this."

    body = file_note(wiki, llm, f"# Overview\n\n{code}\n\n{inline}")

    assert code in body
    assert inline in body


def test_tilde_fenced_code_is_left_alone(wiki: Wiki, llm: FakeLLM) -> None:
    code = "~~~\n[gone](gone.md)\n~~~"

    body = file_note(wiki, llm, f"# Overview\n\n{code}\n\nOutside [gone](gone.md).")

    assert code in body
    assert "Outside gone (gone.md)." in body


def test_the_raw_copy_keeps_the_text_as_it_came(wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    text = "Source with [a dead link](gone.md) and [[wiki]] in it."

    file_note(wiki, llm, "# Overview\n\nFacts.", text=text)

    [raw] = (bundle / "raw").glob("*.md")
    assert text in raw.read_text(encoding="utf-8")


# -- afterwards -------------------------------------------------------------------------------------------


def test_check_reports_no_broken_link_in_a_note_whose_dead_links_became_text(wiki: Wiki, llm: FakeLLM) -> None:
    file_note(wiki, llm, "# Overview\n\nSee [the policy](policy.md), [x](../nope/x.md#a) and [[Wiki name]].")

    assert wiki.check() == []


def test_a_merge_turns_dead_links_into_text(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    add_folder(wiki, "/", "finance", "Money matters.")
    add_note(wiki, "/finance", "Acme pricing")
    llm.add(SUMMARIZE, draft("Acme price rise")).add(ROUTE, {"action": "descend", "subfolder": "finance"}, {"action": "here"})
    llm.add(FIND_MATCH, {"match": "finance/acme-pricing.md"}).add(CONSOLIDATE, {"decision": "modify"})
    llm.add(MERGE, draft("Acme pricing", body="# Overview\n\nMerged, see [terms](terms.md) and [[Acme]].")).add(RELATE, {"related": []})

    result = wiki.ingest("Acme raised prices.")

    body = read(bundle, result.note).body
    assert "Merged, see terms (terms.md) and Acme." in body
    assert wiki.check() == []


def test_a_revision_turns_dead_links_into_text(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_replace_wiki(bundle, llm)
    seed(wiki)
    reply = summarize_reply()
    reply["body"] = "# Summary\n\nNEWSUMMARYMARKER, see [terms](terms.md) and [[Annex]]."
    llm.add("librarian/summarize", reply).add("librarian/route", {"action": "new"})
    llm.add("librarian/find_match", {"match": "wholesale-programme-review-2026.md"}).add("librarian/consolidate", {"decision": "modify"})

    result = wiki.ingest(NEW, title=REV_TITLE, origin=Origin("local", "", REV_ORIGIN_ID))

    body = read(bundle, result.note).body
    assert "NEWSUMMARYMARKER, see terms (terms.md) and Annex." in body
    assert wiki.check() == []


def test_the_stored_body_hash_matches_the_saved_body_after_the_links_became_text(wiki: Wiki, llm: FakeLLM) -> None:
    file_note(wiki, llm, "# Overview\n\nSee [the policy](policy.md) and [[Wiki name]].")

    saved = read(wiki.cfg.bundle, NOTE)

    assert saved.frontmatter["generated"]["body_hash"] == revisions.body_hash(saved.body.split("# See also")[0])


def test_a_later_revision_of_a_document_filed_with_dead_links_is_still_recognised_as_a_revision(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_replace_wiki(bundle, llm)
    first = summarize_reply(TITLE)
    first["body"] = "# Overview\n\nOLDBODYMARKER, see [the annex](annex.md) and [[Wiki name]]."
    llm.add("librarian/summarize", first).add("librarian/route", {"action": "here"})
    llm.add("librarian/name_folder", {"name": "reviews", "description": "Reviews."})
    filed = wiki.ingest(OLD, title=TITLE, origin=Origin("local", "", ORIGIN_ID))
    llm.add("librarian/summarize", summarize_reply())
    llm.add("librarian/route", {"action": "descend", "subfolder": "reviews"}, {"action": "here"})
    llm.add("librarian/find_match", {"match": filed.note}).add("librarian/consolidate", {"decision": "modify"})
    llm.add("librarian/merge", {"title": TITLE, "summary": "Merged.", "tags": [], "body": "MERGEDBYLLM"})

    result = wiki.ingest(NEW, title=REV_TITLE, origin=Origin("local", "", REV_ORIGIN_ID))

    assert result.action == "merged" and result.note == filed.note
    assert "librarian/merge" not in llm.ops  # replaced from the summary already written, not merged by the model
    assert list((bundle / ".archive").rglob("*.md"))


def test_a_link_to_a_note_that_exists_follows_the_note_when_it_moves_and_stays_a_link(wiki: Wiki) -> None:
    store = wiki.store
    root = store.load()
    sales, _ = store.create_folder(root, "sales", "Sales.")
    ops, _ = store.create_folder(root, "ops", "Operations.")
    source = {"resource": "/raw/x.md", "title": "X"}
    target = store.write_note(sales, title="Terms", summary="T.", body="Terms.", tags=[], source=source)
    linking = store.write_note(ops, title="Plan", summary="P.", body="See [the terms](../sales/terms.md#top).", tags=[], source=source)
    assert "[the terms](../sales/terms.md#top)" in linking.body

    wiki.move_note(target.rel, "/")
    assert "[the terms](../terms.md#top)" in read(wiki.cfg.bundle, linking.rel).body
    wiki.move_note("terms.md", "/finance")

    assert "[the terms](../finance/terms.md#top)" in read(wiki.cfg.bundle, linking.rel).body
    assert wiki.check() == []


def test_inline_code_in_the_text_of_a_dead_link_does_not_keep_it_a_link(wiki: Wiki, llm: FakeLLM) -> None:
    text = "See [`beta` feature flag](./guide.md#flags), [`ok` one](budget.md), and write `[gone](gone.md)` as is."
    body = file_note(wiki, llm, f"# Overview\n\n{text}")

    assert "See `beta` feature flag (./guide.md#flags)," in body
    assert "[`ok` one](budget.md)" in body  # the target exists: still a link
    assert "write `[gone](gone.md)` as is." in body  # a link wholly in a code span is left alone
    assert "[`beta`" not in body
