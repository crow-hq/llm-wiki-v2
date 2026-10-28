# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A small local writer's folder names and descriptions must come from the documents it was shown, not from its prompt
(plan, split, name_folder); a hosted writer's are taken as they are. A broken reply is no plan."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from llmw2 import Candidate, Route, Source, StepContext, Wiki, WikiConfig
from llmw2.agents import echo
from llmw2.agents.data import FolderPlan
from llmw2.agents.prompts import Prompts
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.tree import Folder
from llmw2.models.llm import extract_json
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_crow_split import file_note, groups, seed
from tests.wiki.test_plan_groups import PAY, ROAST
from tests.wiki.test_steps import OFFLINE

PLAN, SPLIT, NAME_FOLDER = "librarian/plan", "librarian/split", "librarian/name_folder"
COFFEE = "Cupping evening checklist: grind the Aurora samples, set the cups, score aroma and acidity at the bar."
POSTHOG = "How we review pull requests: every change needs one approval, small PRs, and tests for the product analytics."


def config(bundle: Path, provider: str, mode: str = "crow", **crow: Any) -> WikiConfig:
    return WikiConfig(
        bundle=bundle, mode=mode, crow=crow, llm={"provider": provider, "model": "small", "base_url": "http://localhost:1234/v1"}
    )


def folders_json(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {"folders": [{"path": p, "description": d} for p, d in pairs]}


def labels(wiki: Wiki) -> list[str]:
    return [f.label for f in wiki.store.load().walk()][1:]


# -- the guard ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("1-to-3-lowercase-words-joined-by-hyphens", ROAST[0]),  # the naming rule of the prompt
        ("1-3", ROAST[0]),
        ("new-top-level-area", POSTHOG),  # "a new top-level area" of plan_more
        ("area", POSTHOG),  # the reply format "/area/subject"
        ("opening", "Opening weekend rota for the bar."),  # "(title: opening)", even where the documents say it
        ("a13a14a20a40a43", "a13-bar-licence.md a14-bar-menu.md"),  # file-name codes
        ("engineering", COFFEE),  # an example of name_folder, for a coffee roaster
        ("suppliers", POSTHOG),  # an example of the folder rules
        ("lavoro", POSTHOG),
    ],
)
def test_a_name_copied_from_the_prompt_is_not_the_writers_own(name: str, shown: str) -> None:
    assert echo.own_name(name, shown) == ""


@pytest.mark.parametrize(
    ("name", "shown", "own"),
    [
        ("roasting", ROAST[0], "roasting"),  # "roasted" in the text: the word opens one of the documents'
        ("payroll", PAY[0], "payroll"),
        ("1-viaggio-puglia", "Viaggio in Puglia ad aprile.", "viaggio-puglia"),  # the count that opens it goes
        ("engineering-reviews", POSTHOG, "engineering-reviews"),  # a template word counts where the documents have it
        ("analisi-tempo-produttivit", "Analisi del tempo e produttività.", "analisi-tempo-produttivit"),  # cut short
    ],
)
def test_a_name_with_a_word_of_the_documents_is_the_writers_own(name: str, shown: str, own: str) -> None:
    assert echo.own_name(name, shown) == own


def test_documents_in_a_script_names_cannot_spell_leave_the_name_and_description_as_written() -> None:
    shown = "Годовой отчёт: выручка выросла на 12%."

    assert echo.own_name("Финансы", shown) == "Финансы"  # as on main: nothing to judge it by
    assert echo.own_text("Отчёты и выручка.", shown, Prompts(), NAME_FOLDER)


def test_the_words_of_the_parent_path_do_not_make_a_name() -> None:
    assert echo.own_name("benefits-benefits", "Benefits: health, pension.", ["benefits"]) == ""
    assert echo.own_name("benefits-pension", "Benefits: health, pension.", ["benefits"]) == "benefits-pension"


def test_a_description_must_have_a_word_of_the_documents_that_the_prompt_lacks() -> None:
    prompts = Prompts()

    assert echo.own_text("How the Aurora blend is roasted.", ROAST[0], prompts, NAME_FOLDER)
    assert not echo.own_text("...", ROAST[0], prompts, NAME_FOLDER)
    assert not echo.own_text("<what belongs in this folder>", ROAST[0], prompts, NAME_FOLDER)


# -- a broken reply is no plan -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        '{"folders": [{"path": "/a", "description": "x"} {"path": "/b", "description": "y"}]}',  # a comma missing
        '{"folders": [{"path": "/a", "description": "x"}, {"path": "/b", "desc',  # cut short
    ],
    ids=["missing-comma", "cut-short"],
)
def test_a_dict_inside_a_broken_reply_is_never_taken_for_it(reply: str) -> None:
    assert extract_json(reply) == reply


def test_an_object_after_chatter_with_broken_braces_is_still_found() -> None:
    assert extract_json('Use {braces} like this: {"a": 1}') == '{"a": 1}'


def test_a_reply_without_folders_is_no_plan() -> None:
    with pytest.raises(ValidationError):
        FolderPlan.model_validate({"path": "/a", "description": "x"})
    assert FolderPlan.model_validate({"folders": []}).folders == []


# -- where the writer names folders ------------------------------------------------------------------------------------


DOCS = [("Roast profile", ROAST[0]), ("Roast adjustments", ROAST[1]), ("Payroll calendar", PAY[0])]
PLANNED = folders_json(
    ("/area", "Area."),
    ("/area/subject", "Area subject."),
    ("/1-to-3-lowercase-words-joined-by-hyphens", "Documents."),
    ("/roasting", "How each blend is roasted."),
    ("/roasting/aurora", "The Aurora espresso blend."),
    ("/engineering", "Engineering documents."),
    ("/1-payroll", "Salaries and payroll dates."),
)


def test_a_local_writers_plan_keeps_only_folders_named_and_described_from_the_documents(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = Wiki(config(bundle, "ollama"), llm=llm, classifier=classifier)
    wiki.init()
    llm.add(PLAN, PLANNED)

    wiki.plan(DOCS)

    assert labels(wiki) == ["/payroll", "/roasting", "/roasting/aurora"]


def test_a_hosted_writers_plan_is_taken_as_it_is(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = Wiki(config(bundle, "openrouter"), llm=llm, classifier=classifier)
    wiki.init()
    llm.add(PLAN, PLANNED)

    wiki.plan(DOCS)

    assert "/engineering" in labels(wiki) and "/area/subject" in labels(wiki)


def test_a_local_writers_split_proposal_named_after_its_folder_is_left_out(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = Wiki(config(bundle, "ollama"), llm=llm, classifier=classifier)
    wiki.init()
    wiki.store.create_folder(wiki.store.load().find("/"), "sales", "Sales documents.")
    seed(wiki, 11, n1="Renewals of the Acme contract.", n2="Renewals of the Bolt contract.", n4="Sales figures.", n5="Sales targets.")
    llm.add(SPLIT, groups(("renewals", [1, 2]), ("sales-sales", [4, 5]), ("area", [6, 7])))

    file_note(wiki, llm, classifier, 12)

    assert [f.label for f in wiki.store.load().find("/sales").subfolders] == ["/sales/renewals"]  # type: ignore[union-attr]


@pytest.mark.parametrize(("provider", "folder"), [("ollama", "/cupping-evening-checklist"), ("openrouter", "/engineering")])
def test_a_local_writers_folder_name_copied_from_the_prompt_is_replaced_by_the_notes(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier, provider: str, folder: str
) -> None:
    def new_at_root(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
        return Route(root, True, [])

    def naming(ctx: StepContext, parent: Folder, cand: Candidate) -> tuple[str, str]:
        return "engineering", "Engineering documents."

    wiki = Wiki(config(bundle, provider, mode="classic"), llm=llm, steps=replace(OFFLINE, route=new_at_root, name_folder=naming))
    wiki.init()

    result = wiki.ingest(COFFEE, title="Cupping evening checklist")

    assert result.folder == folder


# -- a title or summary copied from the summarize prompts ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "copy"),
    [
        ("<a specific title written from the content>", True),  # gemma, the reply format of summarize
        ("<one sentence: what the note answers>", True),
        ("Take notes from a source", True),  # granite: summarize_combine paraphrasing summarize_part
        ("Academy course calendar autumn (v2)", False),
        ("Weekly sync - packaging", False),
        ("Viaggio in Puglia ad aprile", False),
        ("...", False),  # no word: the placeholder rule handles it
    ],
)
def test_a_text_made_only_of_words_of_the_summarize_prompts_is_copied(text: str, copy: bool) -> None:
    assert echo.copied(text, Prompts(), echo.SUMMARIZE_PROMPTS) is copy


SOURCE = "# Academy course calendar, autumn\n\nThe calendar of the autumn courses: espresso basics on 3 October, latte art on 10."


def copies_the_format(ctx: StepContext, source: Source) -> Candidate:
    body = "# Overview\n\nEspresso basics on 3 October."
    return Candidate("<a specific title written from the content>", "<one sentence: what the note answers>", [], body)


@pytest.mark.parametrize(
    ("provider", "title", "summary"),
    [
        ("ollama", "Academy course calendar, autumn", "Espresso basics on 3 October."),
        ("openrouter", "<a specific title written from the content>", "<one sentence: what the note answers>"),
    ],
)
def test_a_local_writers_copied_title_and_summary_give_way_to_the_texts_own_lines(
    bundle: Path, llm: FakeLLM, provider: str, title: str, summary: str
) -> None:
    def to_finance(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
        return Route(root.find("/finance"), False, [])  # type: ignore[arg-type]

    steps = replace(OFFLINE, summarize=copies_the_format, route=to_finance)
    wiki = Wiki(config(bundle, provider, mode="classic"), llm=llm, steps=steps)
    wiki.init()
    wiki.store.create_folder(wiki.store.load(), "finance", "Money in and out.")

    result = wiki.ingest(SOURCE, title="a04-academy-calendar.md")

    assert result.title == title
    assert OKFDocument.read(bundle / result.note).frontmatter["description"] == summary
