# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Round 5, written blind from benchmarks/results/specs/spec-giro5.md: parity with main on long documents."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from llmw2 import CLASSIC, CROW, Candidate, NoteDraft, Origin, Source, StepContext, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents import steps_crow as crow
from llmw2.agents.prompts import Prompts
from llmw2.agents.state import source_state
from llmw2.bundle.tree import Note
from llmw2.config import ClassifierConfig, LLMConfig
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_merge_note import MODIFIED, RAW, wiki_with
from tests.wiki.test_note_facts import load_script
from tests.wiki.test_steps import MERGING
from tests.wiki.test_summarize_long import COMBINE, DRAFT, SUMMARIZE, RecordingLLM, document, notes_for

ROOT = Path(__file__).resolve().parents[2]
MERGE, MERGE_SOURCE = "librarian/merge", "librarian/merge_source"
NOTE = 1_000
LLM_MERGING = replace(MERGING, summarize=CLASSIC.summarize, merge=CLASSIC.merge)
CODES = ["AB-1001", "AB-1002", "AB-1003", "AB-1004"]
INCOMING = {"title": "In", "summary": "In.", "tags": [], "body": "# Overview\n\nCodes " + " ".join(CODES) + "."}


def main_file(path: str) -> str:
    out = subprocess.run(["git", "show", f"origin/main:{path}"], cwd=ROOT, capture_output=True, check=True)
    return out.stdout.decode("utf-8")


def draft(body: str) -> dict[str, Any]:
    return {"title": "Seed", "summary": "Merged.", "tags": [], "body": body}


def context(llm: Any, source: Source | None = None, **fields: Any) -> StepContext:
    cfg = WikiConfig(
        bundle=Path("wiki"),
        mode="crow",
        llm=LLMConfig(read_chars=20_000),
        classifier=ClassifierConfig(model="fake/jev", api_key="fake-key"),
        note_chars=NOTE,
        **fields,
    )
    return StepContext(llm, FakeClassifier(llm.usage), Prompts(), cfg, "librarian/system", source)


# -- 1. route ---------------------------------------------------------------------------------------


def test_route_prompt_is_main_s_text_byte_for_byte() -> None:
    # "route.md becomes byte-identical to origin/main"
    assert Prompts().load("classifier/route") == main_file("src/llmw2/agents/prompts/classifier/route.md")


# -- 2. summarize without the length rule ------------------------------------------------------------


@pytest.mark.parametrize("chars", [500, 2 * NOTE, 2 * NOTE + 1, 5 * NOTE])
def test_one_call_summarize_never_has_the_length_rule(tracker: Any, chars: int) -> None:
    # "the one-call branch always passes length_rule="""
    llm = RecordingLLM(tracker, summarize=DRAFT)
    source = Source("a" * chars, title="Memo")

    classic.summarize(context(llm, source), source)

    assert llm.ops == [SUMMARIZE]
    assert "- Length:" not in llm.prompts(SUMMARIZE)[0]


def test_summarize_combine_keeps_the_length_rule(tracker: Any) -> None:
    # "summarize_combine and merge keep the default length rule"
    llm = RecordingLLM(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    cfg = WikiConfig(
        bundle=Path("wiki"), mode="classic", llm=LLMConfig(read_chars=8_000), classifier=ClassifierConfig(model="fake/jev", api_key="k")
    )
    source = Source(document(), title="Memo")

    classic.summarize(StepContext(llm, None, Prompts(), cfg, "librarian/system", source), source)

    assert "- Length: the body is at most about" in llm.prompts(COMBINE)[0]


def test_merge_keeps_the_length_rule(bundle: Path, llm: FakeLLM) -> None:
    wiki = wiki_with(bundle, llm, LLM_MERGING)
    llm.add(SUMMARIZE, INCOMING).add(MERGE, draft("# Overview\n\nSeed. " + " ".join(CODES)))

    wiki.ingest(RAW, title="Memo")

    [(_, messages)] = [c for c in llm.calls if c[0] == MERGE]
    assert "- Length:" in messages[1]["content"]


def test_summary_version_is_5() -> None:
    assert classic.SUMMARY_VERSION == 7


# -- 3. consolidate state ---------------------------------------------------------------------------


def cand_and_source(chars: int) -> tuple[Candidate, Source]:
    text = ("Alpha beta gamma. " * 200)[:chars]
    return Candidate("Cand title", "Cand summary.", [], "Cand body. " * 50), Source(text, title="Memo")


def test_consolidate_state_of_a_short_source_is_the_source_state(llm: FakeLLM) -> None:
    # "ctx.route_state(cand) when len(ctx.source.text) <= 2 * effective_note_chars"
    cand, source = cand_and_source(2 * NOTE)
    ctx = context(llm, source)

    assert crow.consolidate_state(ctx, cand) == ctx.route_state(cand) == source_state("Memo", source.text, ctx.cfg.classifier.state_chars)
    assert crow.consolidate_state(ctx, cand) != ctx.state(cand)


def test_consolidate_state_of_a_long_source_is_the_candidate_state(llm: FakeLLM) -> None:
    # boundary: one character over 2 * effective_note_chars
    cand, source = cand_and_source(2 * NOTE + 1)
    ctx = context(llm, source)

    assert crow.consolidate_state(ctx, cand) == ctx.state(cand)
    assert crow.consolidate_state(ctx, cand) != ctx.route_state(cand)


def test_consolidate_state_without_a_source_is_the_candidate_state(llm: FakeLLM) -> None:
    cand, _ = cand_and_source(10)
    ctx = context(llm, None)

    assert crow.consolidate_state(ctx, cand) == ctx.state(cand)


def test_consolidate_state_follows_effective_note_chars_at_call_time(llm: FakeLLM) -> None:
    # "computed at call time": a source of 2500 chars is long for note_chars 1000 and short for 1500
    cand, source = cand_and_source(2_500)
    long_ctx = context(llm, source)
    short_ctx = context(llm, source)
    short_ctx.cfg.note_chars = 1_500

    assert crow.consolidate_state(long_ctx, cand) == long_ctx.state(cand)
    assert crow.consolidate_state(short_ctx, cand) == short_ctx.route_state(cand)


def test_crow_consolidate_sends_that_state_to_the_classifier(bundle: Path, llm: FakeLLM) -> None:
    wiki = wiki_with(bundle, llm, CROW)
    note = wiki.store.load().find("/finance").notes[0]
    cand, source = cand_and_source(500)
    ctx = context(llm, source)
    classifier = ctx.classifier
    assert isinstance(classifier, FakeClassifier)
    classifier.add_choice("consolidate", "New note", {"New note": 0.9}, 0.9)

    crow.consolidate(ctx, note, cand)

    [state] = [s for op, s, _ in classifier.calls if op == "consolidate"]
    assert state.startswith(crow.consolidate_state(ctx, cand))
    assert "--- Existing note ---" in state
    assert "Cand body." not in state


# -- 4. merge ---------------------------------------------------------------------------------------


def test_merge_prompt_has_no_modified_date(bundle: Path, llm: FakeLLM) -> None:
    # "the modified date of the origin no longer enters incoming_from"
    wiki = wiki_with(bundle, llm, LLM_MERGING)
    llm.add(SUMMARIZE, INCOMING).add(MERGE, draft("# Overview\n\nSeed. " + " ".join(CODES)))

    wiki.ingest(RAW, title="Memo", origin=Origin("local", "", "memo.md", modified=MODIFIED))

    [(_, messages)] = [c for c in llm.calls if c[0] == MERGE]
    prompt = messages[1]["content"]
    assert "modified" not in prompt
    assert MODIFIED not in prompt


def test_incoming_values_finds_codes() -> None:
    values = classic.incoming_values("See ED-2026 and AB-1001, also xy-12 and ABCDE-12345.")

    assert {"ed-2026", "ab-1001"} <= values


def test_incoming_values_normalises_amounts_with_separators_and_currency() -> None:
    values = classic.incoming_values("Price: 12 500 € per year.")

    assert "12500" in values


def test_incoming_values_ignores_single_digits_and_finds_two_word_names() -> None:
    values = classic.incoming_values("Mario Rossi, 3 times, 45% done.")

    assert "mariorossi" in values
    assert "3" not in values
    assert "45" in values


def test_incoming_values_is_a_set_of_str() -> None:
    values = classic.incoming_values("AB-1001 AB-1001")

    assert isinstance(values, set) and "ab-1001" in values and all(isinstance(v, str) for v in values)


def run_merge(bundle: Path, llm: FakeLLM, first: dict[str, Any], *later: dict[str, Any], summarize: dict[str, Any] = INCOMING) -> NoteDraft:
    """Ingest into the seed note with the classic merge; return the draft the merge step returned."""
    seen: list[NoteDraft] = []

    def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        seen.append(classic.merge(ctx, note, source))
        return seen[-1]

    wiki = wiki_with(bundle, llm, replace(LLM_MERGING, merge=merge))
    llm.add(SUMMARIZE, summarize).add(MERGE, first)
    if later:
        llm.add(MERGE_SOURCE, *later)
    wiki.ingest(RAW, title="Memo")
    return seen[0]


def test_identical_body_asks_again_with_merge_source_reading_the_source(bundle: Path, llm: FakeLLM) -> None:
    # "fails the guard when draft.body.strip() == existing_body.strip()"
    final = {"title": "Seed", "summary": "Fixed.", "tags": [], "body": "Seed.\n\nFrom the source."}

    result = run_merge(bundle, llm, draft("  Seed.\n"), final)

    assert llm.ops.count(MERGE) == 1 and llm.ops.count(MERGE_SOURCE) == 1
    prompt = next(m[1]["content"] for op, m in llm.calls if op == MERGE_SOURCE)
    assert RAW in prompt
    assert "- Length:" not in prompt
    assert result.body == final["body"]


def test_values_half_missing_falls_back(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    # "fewer than half of new are in incoming_values(draft.body)"
    final = draft("Seed. " + " ".join(CODES))

    with caplog.at_level(logging.WARNING):
        result = run_merge(bundle, llm, draft("Seed. AB-1001"), final)

    assert llm.ops.count(MERGE_SOURCE) == 1
    assert result.body == final["body"]
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_half_of_the_values_present_is_enough(bundle: Path, llm: FakeLLM) -> None:
    # "fewer than half": exactly half is not a failure
    result = run_merge(bundle, llm, draft("Seed. AB-1001 AB-1002"))

    assert MERGE_SOURCE not in llm.ops
    assert result.body == "Seed. AB-1001 AB-1002"


def test_values_present_means_no_fallback(bundle: Path, llm: FakeLLM) -> None:
    result = run_merge(bundle, llm, draft("Seed. " + " ".join(CODES)))

    assert MERGE_SOURCE not in llm.ops
    assert "AB-1004" in result.body


def test_no_new_values_and_a_changed_body_does_not_fall_back(bundle: Path, llm: FakeLLM) -> None:
    # "when new is non-empty and ...": with no new values only the identical body fails
    plain = {"title": "In", "summary": "In.", "tags": [], "body": "Plain words only."}

    run_merge(bundle, llm, draft("Seed.\n\nPlain words."), summarize=plain)

    assert MERGE_SOURCE not in llm.ops


def test_the_fallback_result_is_returned_as_is_with_no_second_guard(bundle: Path, llm: FakeLLM) -> None:
    # "Its result is returned whatever it holds (no second guard)"; a third call would raise in FakeLLM
    result = run_merge(bundle, llm, draft("Seed."), draft("Seed."))

    assert result.body == "Seed."
    assert llm.ops.count(MERGE_SOURCE) == 1
    assert llm.ops.count(MERGE) == 1


def test_merge_source_is_main_s_merge_prompt() -> None:
    # "whose text is main's merge prompt, verbatim"
    prompts = Prompts()
    text = prompts.load("librarian/merge_source")

    assert text == main_file("src/llmw2/agents/prompts/librarian/merge.md")
    assert "Never shorten the note" in text
    assert "librarian/merge_source" in Prompts.names()
    values = {key: f"[[{key}]]" for key in prompts.placeholders("librarian/merge_source")}
    assert {"title", "summary", "tags", "body", "source_text"} <= set(values)
    assert "{{" not in prompts.render("librarian/merge_source", **values)


# -- 5. benchmark metric ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return load_script()


def found(script: ModuleType, text: str) -> set[str]:
    return {str(n) for n in script.numbers(text)}


def test_note_facts_a_code_does_not_glue_into_a_number(script: ModuleType) -> None:
    # "ED-2026-01 300 kg" gives 2026, 01, 300 (not 01300)
    values = found(script, "ED-2026-01 300 kg")

    assert "01300" not in values and "1300" not in values
    assert "2026" in values and "300" in values


def test_note_facts_space_thousands_still_group(script: ModuleType) -> None:
    assert "12500" in found(script, "12 500 kg")


def test_note_facts_quarter_label_still_gives_the_year(script: ModuleType) -> None:
    assert found(script, "Q2 2026") == {"2026"}
