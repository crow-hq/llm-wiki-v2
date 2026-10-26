# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Small local models: a writer's unusable reply is worked around on Ollama (the source text, a section, a name from the
note). The failure of another writer stops the ingest, and so does an endpoint that fails, on every provider."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import httpx
import pytest

from llmw2 import CLASSIC, Candidate, ModelError, ReplyInvalid, Route, StepContext, StepError, Steps, Wiki, WikiConfig
from llmw2.agents.librarian import IngestResult, _name_words
from llmw2.bundle.tree import Folder
from llmw2.errors import ReplyCut
from llmw2.models.client import HttpModel
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeLLM
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
