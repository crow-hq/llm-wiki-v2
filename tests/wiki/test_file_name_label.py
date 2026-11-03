# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: a document that came from a file is shown to the writer by its file name, never by the title made from its stem."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Item, Wiki, WikiConfig
from llmw2.bundle.document import OKFDocument
from llmw2.config import LLMConfig
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_crow_plan import PLAN, PLAN_MORE, add_folder, folders_json, item, make_crow, plans
from tests.wiki.test_summarize_long import COMBINE, DRAFT, PART, READ, RecordingLLM, document, notes_for

SUMMARIZE = "librarian/summarize"
FILE = "q3-board-minutes.md"
STEM_TITLE = "q3 board minutes"
FOLDER = {"name": "boards", "description": "Board papers."}
BODY = b"Revenue is booked on delivery."


def user_prompts(llm: Any, op: str) -> list[str]:
    return [msgs[-1]["content"] for o, msgs in llm.calls if o == op]


def script(llm: FakeLLM) -> None:
    llm.add(SUMMARIZE, {"title": "Board minutes", "summary": "What the board said.", "body": "Body."})
    llm.add("librarian/route", {"action": "new"}).add("librarian/name_folder", FOLDER)


def long_wiki(bundle: Path, llm: RecordingLLM) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", llm=LLMConfig(read_chars=READ)), llm=llm)
    wiki.init()
    return wiki


def long_llm(tracker: Any) -> RecordingLLM:
    return RecordingLLM(
        tracker, summarize_part=notes_for, summarize_combine=DRAFT, route={"action": "new"}, name_folder=FOLDER
    )


# -- the summarize prompt ---------------------------------------------------------------------------------


def test_a_file_is_shown_by_its_file_name_with_its_extension(classic_wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)

    classic_wiki.ingest_file(BODY, FILE)

    [prompt] = user_prompts(llm, SUMMARIZE)
    assert re.search(rf"^File name: {re.escape(FILE)}$", prompt, re.MULTILINE)
    assert BODY.decode() in prompt


def test_a_file_has_no_source_title_line(classic_wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)

    classic_wiki.ingest_file(BODY, FILE)

    [prompt] = user_prompts(llm, SUMMARIZE)
    assert "Source title:" not in prompt
    assert STEM_TITLE not in prompt


@pytest.mark.parametrize("name", ["notes_final.txt", "Budget-2026_v2.md", "plan.markdown"])
def test_the_file_name_is_given_as_it_is_with_hyphens_and_underscores(classic_wiki: Wiki, llm: FakeLLM, name: str) -> None:
    script(llm)

    classic_wiki.ingest_file(BODY, name)

    [prompt] = user_prompts(llm, SUMMARIZE)
    assert f"File name: {name}" in prompt.splitlines()


def test_a_title_given_by_the_caller_is_shown_as_the_source_title(classic_wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)

    classic_wiki.ingest(BODY.decode(), title="Q3 board")

    [prompt] = user_prompts(llm, SUMMARIZE)
    assert "Source title: Q3 board" in prompt.splitlines()
    assert "File name:" not in prompt


def test_a_source_with_neither_shows_source_title_none(classic_wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)

    classic_wiki.ingest(BODY.decode())

    [prompt] = user_prompts(llm, SUMMARIZE)
    assert "Source title: (none)" in prompt.splitlines()
    assert "File name:" not in prompt


def test_an_item_of_a_batch_is_shown_by_its_file_name(classic_wiki: Wiki, llm: FakeLLM) -> None:
    script(llm)

    classic_wiki.ingest_many([Item(FILE, data=BODY)])

    [prompt] = user_prompts(llm, SUMMARIZE)
    assert f"File name: {FILE}" in prompt.splitlines() and "Source title:" not in prompt


# -- the long source: summarize_part and summarize_combine --------------------------------------------------


def test_every_part_and_the_combine_show_the_file_name(bundle: Path, tracker: Any) -> None:
    llm = long_llm(tracker)
    wiki = long_wiki(bundle, llm)

    wiki.ingest_file(document().encode(), FILE)

    parts, combines = user_prompts(llm, PART), user_prompts(llm, COMBINE)
    assert len(parts) > 1 and len(combines) == 1
    for prompt in [*parts, *combines]:
        assert f"File name: {FILE}" in prompt.splitlines()
        assert "Source title:" not in prompt


def test_every_part_and_the_combine_show_the_callers_title_as_the_source_title(bundle: Path, tracker: Any) -> None:
    llm = long_llm(tracker)
    wiki = long_wiki(bundle, llm)

    wiki.ingest(document(), title="Q3 board")

    for prompt in [*user_prompts(llm, PART), *user_prompts(llm, COMBINE)]:
        assert "Source title: Q3 board" in prompt.splitlines()
        assert "File name:" not in prompt


def test_every_part_and_the_combine_of_a_source_with_no_title_show_source_title_none(bundle: Path, tracker: Any) -> None:
    llm = long_llm(tracker)
    wiki = long_wiki(bundle, llm)

    wiki.ingest(document())

    for prompt in [*user_prompts(llm, PART), *user_prompts(llm, COMBINE)]:
        assert "Source title: (none)" in prompt.splitlines()


# -- Source.title keeps its value ---------------------------------------------------------------------------


def test_the_title_made_from_the_stem_is_still_what_the_note_and_raw_copy_record(classic_wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    script(llm)

    result = classic_wiki.ingest_file(BODY, FILE)

    note = OKFDocument.parse((bundle / result.note).read_text(encoding="utf-8"))
    [source] = note.frontmatter["sources"]
    assert source["title"] == STEM_TITLE
    [raw] = (bundle / "raw").glob("*.md")
    assert OKFDocument.parse(raw.read_text(encoding="utf-8")).frontmatter["title"] == STEM_TITLE


# -- the plan prompts ---------------------------------------------------------------------------------------


def test_the_plan_prompt_shows_the_stem_title_of_a_document_that_came_from_a_file(bundle: Path) -> None:
    wiki, llm = make_crow(bundle)

    wiki.ingest_many([item(n) for n in range(3)])

    [prompt] = user_prompts(llm, PLAN)
    for n in range(3):
        assert f"doc {n:03d}: " in prompt
    assert "File name:" not in prompt and "doc-000.md" not in prompt


def test_the_plan_more_prompt_shows_the_stem_title_of_a_document_that_came_from_a_file(bundle: Path) -> None:
    wiki, llm = make_crow(bundle)
    add_folder(wiki, "/", "red", "Red things.")
    llm.plan_more_reply = folders_json(("/blue", "Blue things."))

    wiki.ingest_many([item(n) for n in range(3)])

    assert plans(llm) == [PLAN_MORE]
    [prompt] = user_prompts(llm, PLAN_MORE)
    for n in range(3):
        assert f"doc {n:03d}: " in prompt
    assert "File name:" not in prompt and "doc-000.md" not in prompt


DOCS = [("Acme terms", "New terms for the year."), ("Other memo", "Something else entirely.")]


def test_the_plan_prompt_shows_a_title_given_by_the_caller_as_today(crow_wiki: Wiki, llm: FakeLLM) -> None:
    llm.add(PLAN, folders_json(("/sales", "Sales.")))

    crow_wiki.plan(DOCS)

    [prompt] = user_prompts(llm, PLAN)
    assert "Acme terms" in prompt and "Other memo" in prompt
    assert "File name:" not in prompt


def test_the_plan_more_prompt_shows_a_title_given_by_the_caller_as_today(crow_wiki: Wiki, llm: FakeLLM) -> None:
    add_folder(crow_wiki, "/", "sales", "Sales stuff.")
    llm.add(PLAN_MORE, {"folders": []})

    crow_wiki.plan(DOCS)

    [prompt] = user_prompts(llm, PLAN_MORE)
    assert "Acme terms" in prompt and "Other memo" in prompt
    assert "File name:" not in prompt



def test_the_plan_prompt_lists_the_documents_exactly_as_v0_7_0_does(bundle: Path) -> None:
    wiki, llm = make_crow(bundle)

    wiki.ingest_many([item(n) for n in range(3)])

    [prompt] = user_prompts(llm, PLAN)
    listing = "\n".join(f"- doc {n:03d}: topic: red-{n} line of document {n}." for n in range(3))
    assert listing in prompt
