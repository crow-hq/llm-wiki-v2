# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Small local models: a writer's unusable reply is worked around on Ollama (the source text, a section, a name from the
note, the classifier's best path for a failing route fallback). The failure of another writer stops the ingest, and so
does an endpoint that fails, on every provider. A title that is the file name or empty is replaced from the text."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import httpx
import pytest

from llmw2 import CLASSIC, Candidate, ModelError, ReplyInvalid, Route, Source, StepContext, StepError, Steps, Wiki, WikiConfig
from llmw2.agents import steps_crow
from llmw2.agents.librarian import IngestResult, _name_words, _unusable_title
from llmw2.agents.steps import CROW
from llmw2.bundle.tree import Folder
from llmw2.errors import ReplyCut
from llmw2.models.client import HttpModel
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_reply_cap import make_llm
from tests.wiki.test_steps import MERGING, OFFLINE, SEED, finance

REPLY_ERRORS = [
    ReplyInvalid("llm json reply invalid after retry: not JSON"),
    ReplyCut("llm json: reply cut at 8000 output tokens (finish_reason length)"),
    StepError("step", "the title is empty"),
]
REPLY_IDS = ["invalid", "cut", "step-error"]


def build(bundle: Path, llm: LLM, steps: Steps, *, provider: str = "ollama") -> Wiki:
    """A wiki with /finance/seed.md in it, its writer on `provider`."""
    cfg = WikiConfig(bundle=bundle, mode="classic", llm={"provider": provider, "model": "small", "base_url": "http://localhost:1234/v1"})
    wiki = Wiki(cfg, llm=llm, steps=steps)
    wiki.init()
    folder, _ = wiki.store.create_folder(wiki.store.load().find("/"), "finance", "Money in and out.")
    wiki.store.write_note(
        folder, title="Seed", summary="Seeded.", body="Seed.", tags=[], source={"resource": "/raw/seed.md", "title": "Seed"}
    )
    return wiki


def failing(error: Exception) -> Callable[..., NoReturn]:
    """A step that raises `error`."""

    def step(ctx: StepContext, *args: object) -> NoReturn:
        raise error

    return step


def new_folder(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
    return Route(finance(root), True, [])


# The steps in which the writer can fail, each with the offline steps that reach it: any document is summarized; one that
# matches the seed note is merged into it; one routed to a new folder gets that folder named.
SITES = {"summarize": OFFLINE, "merge": MERGING, "name_folder": replace(OFFLINE, route=new_folder)}


def ingest(bundle: Path, llm: FakeLLM, site: str, error: Exception, *, provider: str = "ollama") -> IngestResult:
    """A document ingested into a wiki whose `site` step raises `error`."""
    steps = replace(SITES[site], **{site: failing(error)})
    return build(bundle, llm, steps, provider=provider).ingest("Payroll runs monthly.", title="Payroll calendar")


@pytest.mark.parametrize("error", REPLY_ERRORS, ids=REPLY_IDS)
def test_summarize_failing_on_ollama_files_the_source_text(bundle: Path, llm: FakeLLM, error: Exception) -> None:
    result = ingest(bundle, llm, "summarize", error)

    assert result.action == "created"
    assert "Payroll runs monthly." in (bundle / result.note).read_text(encoding="utf-8")
    assert any(d.step == "summarize" and d.choice == "verbatim" for d in result.decisions)


@pytest.mark.parametrize("error", REPLY_ERRORS, ids=REPLY_IDS)
def test_merge_failing_on_ollama_adds_the_incoming_note_as_a_section(bundle: Path, llm: FakeLLM, error: Exception) -> None:
    result = ingest(bundle, llm, "merge", error)

    assert (result.action, result.note) == ("merged", SEED)
    text = (bundle / SEED).read_text(encoding="utf-8")
    assert "Seed." in text and "# Payroll calendar" in text and "Payroll runs monthly." in text
    assert any(d.step == "merge" and d.choice == "appended" for d in result.decisions)


@pytest.mark.parametrize("error", REPLY_ERRORS, ids=REPLY_IDS)
def test_name_folder_failing_on_ollama_names_the_folder_after_the_note(bundle: Path, llm: FakeLLM, error: Exception) -> None:
    result = ingest(bundle, llm, "name_folder", error)

    assert result.created_folders == [result.folder] == ["/finance/payroll-calendar"]
    assert any(d.step == "name_folder" and d.choice == "Payroll calendar" for d in result.decisions)


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("provider", ["openrouter", "custom"])
def test_a_failing_reply_of_another_writer_stops_the_ingest(bundle: Path, llm: FakeLLM, site: str, provider: str) -> None:
    error = REPLY_ERRORS[0]

    with pytest.raises(ReplyInvalid) as raised:
        ingest(bundle, llm, site, error, provider=provider)

    assert raised.value is error


def test_a_step_error_of_a_hosted_writer_is_still_raised(bundle: Path, llm: FakeLLM) -> None:
    with pytest.raises(StepError):
        ingest(bundle, llm, "summarize", StepError("summarize", "not a candidate"), provider="openrouter")


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("connection refused"), httpx.Response(404, json={"error": "model 'small' not found"})],
    ids=["unreachable", "wrong-model"],
)
def test_an_endpoint_error_on_ollama_stops_the_ingest(
    bundle: Path, tracker: UsageTracker, monkeypatch: pytest.MonkeyPatch, site: str, failure: httpx.Response | Exception
) -> None:
    monkeypatch.setattr(HttpModel, "backoff", 0)  # an unreachable endpoint is tried three times
    writer, _ = make_llm(tracker, failure, provider="ollama")
    wiki = build(bundle, writer, replace(SITES[site], **{site: getattr(CLASSIC, site)}))  # the real step, over a writer that fails

    with pytest.raises(ModelError) as raised:
        wiki.ingest("Payroll runs monthly.", title="Payroll calendar")

    assert not isinstance(raised.value, ReplyInvalid)  # a reply error would have been worked around


def test_a_reply_the_server_stops_for_repeating_itself_is_worked_around_on_ollama(
    bundle: Path, tracker: UsageTracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(HttpModel, "backoff", 0)  # the 500 is tried three times
    looped = httpx.Response(500, json={"error": {"message": "prediction aborted, token repeat limit reached", "type": "api_error"}})
    writer, _ = make_llm(tracker, looped, provider="ollama")
    wiki = build(bundle, writer, replace(OFFLINE, summarize=CLASSIC.summarize))  # the real summarize, over a writer that loops

    result = wiki.ingest("Payroll runs monthly.", title="Payroll calendar")

    assert any(d.step == "summarize" and d.choice == "verbatim" for d in result.decisions)


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("8 event ratios per account", ["event", "ratios", "per", "account"]),
        ("26. New hire checklist", ["New", "hire", "checklist"]),
        ("", []),
    ],
)
def test_a_folder_named_after_its_note_leaves_the_leading_count_out(text: str, words: list[str]) -> None:
    assert _name_words(text) == words


# -- a title that is the file name or a placeholder ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "name", "unusable"),
    [
        ("26 new hire induction checklist", "26-new-hire-induction-checklist.md", True),
        ("a03 guji test roast note", "a03-guji-test-roast-note.md", True),
        ("26-new-hire-checklist-md", "anything.md", True),
        ("...", "memo.md", True),
        ("— · —", "memo.md", True),
        ("Offices in Baltimore, MD", "offices.md", False),
        ("Годовой отчёт", "report.md", False),
        ("年度报告", "report.md", False),
        ("Contract", "contract.md", False),
        ("Board minutes", "board-minutes.md", False),
        ("New hire induction checklist", "26-new-hire-induction-checklist.md", False),
    ],
)
def test_unusable_title(title: str, name: str, unusable: bool) -> None:
    assert _unusable_title(title, Source(text="x", title=name)) is unusable


def test_a_copied_file_name_or_placeholder_is_replaced_from_the_text(bundle: Path, llm: FakeLLM) -> None:
    def copies(ctx: StepContext, source: Source) -> Candidate:
        return Candidate("26 payroll calendar", "...", [], source.text)

    text = "# Payroll calendar for 2027\n\nSalaries are paid on the 27th."
    result = build(bundle, llm, replace(OFFLINE, summarize=copies), provider="ollama").ingest(text, title="26-payroll-calendar.md")

    assert result.title == "Payroll calendar for 2027"
    assert "description: Salaries are paid on the 27th." in (bundle / result.note).read_text(encoding="utf-8")


def test_a_hosted_writers_title_and_summary_are_kept_as_written(bundle: Path, llm: FakeLLM) -> None:
    def copies(ctx: StepContext, source: Source) -> Candidate:
        return Candidate("26 payroll calendar", "...", [], source.text)

    text = "# Payroll calendar for 2027\n\nSalaries are paid on the 27th."
    result = build(bundle, llm, replace(OFFLINE, summarize=copies), provider="openrouter").ingest(text, title="26-payroll-calendar.md")

    assert result.title == "26 payroll calendar"  # as on main: the cloud path is unchanged


SALES = "Выручка выросла на 12%."


def test_a_title_and_summary_in_another_script_are_kept(bundle: Path, llm: FakeLLM) -> None:
    def writes(ctx: StepContext, source: Source) -> Candidate:
        return Candidate("Годовой отчёт", SALES, [], source.text)

    text = f"# Отчёт\n\n{SALES}"
    result = build(bundle, llm, replace(OFFLINE, summarize=writes), provider="ollama").ingest(text, title="otchet.md")

    assert result.title == "Годовой отчёт"
    assert SALES in (bundle / result.note).read_text(encoding="utf-8")


# -- the route fallback of an Ollama writer whose reply fails: the classifier's best path -----------------------------


@pytest.mark.parametrize(("provider", "goes_on"), [("ollama", True), ("openrouter", False)])
def test_a_failing_route_fallback_of_an_ollama_writer_takes_the_classifiers_best_path(
    bundle: Path, llm: FakeLLM, tracker, provider: str, goes_on: bool
) -> None:
    classifier = FakeClassifier(tracker)
    cfg = WikiConfig(
        bundle=bundle, mode="crow", crow={"beam": 1}, strict_new_folders=False,
        llm={"provider": provider, "model": "small", "base_url": "http://localhost:1234/v1"},
    )
    levels = replace(CROW, name="levels", route=steps_crow._route_by_levels)
    wiki = Wiki(cfg, llm=llm, classifier=classifier, steps=levels)
    wiki.init()
    root = wiki.store.load()
    wiki.store.create_folder(root, "alpha", "Alpha things.")
    wiki.store.create_folder(root, "beta", "Beta things.")
    llm.add("librarian/summarize", {"title": "Q3 revenue", "summary": "Revenue.", "tags": [], "body": "# Overview\n\nFacts."})
    llm.add("librarian/route_fallback", {"action": "create"}, {"action": "create"})  # no folder, twice
    classifier.add_choice("route", "alpha", {"alpha": 0.2, "beta": 0.1, "New subfolder": 0.1}, 0.9)  # under tau_path
    for _ in range(3):
        classifier.add_choice("route", "Here", {"Here": 0.9, "New subfolder": 0.05, "None of these": 0.05}, 0.9)

    if not goes_on:
        with pytest.raises(ModelError):
            wiki.ingest("Revenue grew.")
        return
    result = wiki.ingest("Revenue grew.")

    assert result.folder == "/alpha"
    assert any(d.step == "route" and d.decider == "code" for d in result.decisions)


def test_a_route_fallback_whose_endpoint_fails_still_stops_the_ingest(
    bundle: Path, llm: FakeLLM, tracker, monkeypatch: pytest.MonkeyPatch
) -> None:
    classifier = FakeClassifier(tracker)
    cfg = WikiConfig(
        bundle=bundle, mode="crow", crow={"beam": 1}, strict_new_folders=False,
        llm={"provider": "ollama", "model": "small", "base_url": "http://localhost:1234/v1"},
    )
    wiki = Wiki(cfg, llm=llm, classifier=classifier, steps=replace(CROW, name="levels", route=steps_crow._route_by_levels))
    wiki.init()
    wiki.store.create_folder(wiki.store.load(), "alpha", "Alpha things.")
    llm.add("librarian/summarize", {"title": "Q3 revenue", "summary": "Revenue.", "tags": [], "body": "# Overview\n\nFacts."})
    classifier.add_choice("route", "alpha", {"alpha": 0.2, "New subfolder": 0.1}, 0.9)  # under tau_path: the LLM routes
    for _ in range(3):
        classifier.add_choice("route", "Here", {"Here": 0.9, "New subfolder": 0.05, "None of these": 0.05}, 0.9)
    chat = llm.chat

    def down(messages: list[dict[str, str]], *, op: str = "", **kw: object) -> str:
        if op == "librarian/route_fallback":
            raise ModelError("llm: timed out")
        return chat(messages, op=op, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(llm, "chat", down)

    with pytest.raises(ModelError, match="timed out"):  # a failing endpoint is not a reply to work around
        wiki.ingest("Revenue grew.")
