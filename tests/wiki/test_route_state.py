# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Routing reads a deterministic extract of the source (`source_state`), not the note the LLM wrote."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import CROW, Candidate, Source, StepContext, StepError, Steps, Wiki, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.agents.state import STATE_VERSION, source_state
from llmw2.agents.steps import check_summarize, decision_hash
from llmw2.config import ClassifierConfig
from tests.wiki.fakes import FakeClassifier, FakeLLM

HERE, NEW, NONE = "Here", "New subfolder", "None of these"
NEW_NOTE = "New note"
SUMMARIZE, ROUTE_FALLBACK = "librarian/summarize", "librarian/route_fallback"
FILLER = "The parties agree on the terms that follow and on nothing else at all, as the text says here."


# -- helpers ----------------------------------------------------------------------


def sections(*pairs: tuple[str, str]) -> str:
    """A Markdown document: a `## heading` and a body per pair."""
    return "\n\n".join(f"## {h}\n\n{b}" for h, b in pairs)


def filler(n: int) -> str:
    return " ".join([FILLER] * n)


def part(state: str, name: str) -> str:
    """The text of one labelled part of a state ("Summary", "Sections" or "Excerpts"), up to the next label."""
    head = f"{name}:\n"
    assert head in state, f"no {name!r} part in {state!r}"
    rest = state.split(head, 1)[1]
    for label in ("Summary:\n", "Sections:\n", "Excerpts:\n"):
        rest = rest.split("\n\n" + label, 1)[0]
    return rest


def long_doc() -> str:
    return sections(
        ("Introduction", "Intro words. " * 80),
        ("Pricing", filler(30)),
        ("Executive summary", "Key point. " * 200),
        ("Closing", filler(30)),
    )


def context(llm: FakeLLM, classifier: FakeClassifier, source: Source | None, **classifier_cfg: int) -> StepContext:
    cfg = WikiConfig(bundle=Path("wiki"), mode="crow", classifier=ClassifierConfig(model="fake/jev", api_key="fake-key", **classifier_cfg))
    return StepContext(llm, classifier, Prompts(), cfg, "librarian/system", source)


# -- source_state: short text -----------------------------------------------------


@pytest.mark.parametrize(("title", "expected"), [(None, "Some text."), ("  ", "Some text."), (" Memo ", "Title: Memo\n\nSome text.")])
def test_short_text_is_returned_verbatim_with_or_without_a_title(title: str | None, expected: str) -> None:
    assert source_state(title, "  Some text.\n", 1000) == expected


def test_text_exactly_max_chars_long_is_short() -> None:
    text = "x" * 40

    assert source_state(None, text, 40) == text
    assert source_state("T", "y" * 30, len("Title: T\n\n") + 30) == "Title: T\n\n" + "y" * 30


# -- source_state: structured text ------------------------------------------------


def test_native_summary_not_first_is_emitted_capped_at_040_of_the_budget() -> None:
    state = source_state(None, long_doc(), 1000)

    summary = part(state, "Summary")
    assert state.startswith("Summary:\nKey point.")
    assert 300 < len(summary.strip()) <= 400
    assert "Intro words" not in summary


def test_summary_budget_excludes_the_title_header() -> None:
    state = source_state("A title", long_doc(), 1000)

    assert state.startswith("Title: A title\n\nSummary:\nKey point.")
    assert len(part(state, "Summary").strip()) <= int(0.4 * (1000 - len("Title: A title\n\n")))


def test_executive_summary_beats_an_earlier_introduction() -> None:
    state = source_state(None, long_doc(), 1000)

    assert "Key point." in part(state, "Summary")
    assert "[Introduction] " in state  # the introduction stays among the excerpts
    assert "[Executive summary]" not in state  # the native summary's section is not repeated


def test_only_group_two_heading_is_used_when_no_summary_heading_exists() -> None:
    text = sections(("Pricing", filler(30)), ("Overview", "Overview words. " * 100), ("Closing", filler(30)))

    state = source_state(None, text, 1000)

    assert part(state, "Summary").startswith("Overview words.")


def test_no_native_summary_when_no_such_heading() -> None:
    text = sections(("Pricing", filler(30)), ("Delivery", filler(30)), ("Closing", filler(30)))

    state = source_state(None, text, 1000)

    assert "Summary:" not in state
    assert state.startswith("Sections:\n")


def test_italian_summary_headings_follow_the_same_groups() -> None:
    text = sections(("Premessa", "Parole di premessa. " * 60), ("Prezzi", filler(30)), ("Sommario", "Parole di sommario. " * 60))

    assert part(source_state(None, text, 1000), "Summary").startswith("Parole di sommario.")
    assert part(source_state(None, text.replace("Sommario", "Altro"), 1000), "Summary").startswith("Parole di premessa.")


def test_flat_text_headings_are_detected_in_the_outline() -> None:
    heads = ["1. Scopo", "2.1 Ambito", "Art. 3 Obblighi", "CONDIZIONI GENERALI", "IV. Varie"]
    lines = [x for h in heads for x in (h, filler(5))]

    outline = part(source_state(None, "\n".join(lines), 1000), "Sections")

    for heading in ("Scopo", "Ambito", "Obblighi", "CONDIZIONI GENERALI", "Varie"):
        assert heading in outline


def test_list_item_sentence_and_long_line_are_not_headings() -> None:
    long_caps = "THIS LINE IS WRITTEN IN CAPITALS BUT IT RUNS ON FAR BEYOND EIGHTY CHARACTERS WITHOUT ANY END MARK"
    lines = ["1. Scopo", filler(5), "2. Pay the invoice within thirty days.", filler(5), long_caps, filler(5), "3. Ambito", filler(5)]

    outline = part(source_state(None, "\n".join(lines), 1000), "Sections")

    assert "Scopo" in outline
    assert "Ambito" in outline
    assert "Pay the invoice" not in outline
    assert "RUNS ON FAR" not in outline


def test_isolated_short_line_is_a_heading() -> None:
    text = f"Prices\n\n{filler(5)}\n\nDelivery\n\n{filler(5)}\n\n{filler(5)}"

    outline = part(source_state(None, text, 300), "Sections")

    assert "Prices" in outline
    assert "Delivery" in outline


def test_outline_with_many_headings_is_truncated_with_an_ellipsis() -> None:
    text = sections(*((f"Heading number {i}", filler(2)) for i in range(60)))

    outline = part(source_state(None, text, 1000), "Sections")

    assert outline.rstrip().endswith("- …")
    assert len("Sections:\n" + outline) <= int(0.25 * 1000) + len("\n\n")
    assert "- Heading number 0" in outline


def test_every_non_summary_section_gets_an_excerpt_and_the_preamble_is_marked() -> None:
    preamble = "Opening words before any heading. " * 5
    text = preamble + "\n\n" + sections(("Pricing", filler(20)), ("Summary", "Gist. " * 50), ("Delivery", filler(20)))

    state = source_state(None, text, 1500)

    excerpts = part(state, "Excerpts")
    assert "[…] Opening words" in excerpts
    assert "[Pricing] " in excerpts
    assert "[Delivery] " in excerpts
    assert "[Summary]" not in excerpts
    assert "Gist." in part(state, "Summary")


def test_sections_without_a_body_get_no_excerpt() -> None:
    text = "## Empty\n\n## Pricing\n\n" + filler(20) + "\n\n## Delivery\n\n" + filler(20)

    excerpts = part(source_state(None, text, 600), "Excerpts")

    assert "[Empty]" not in excerpts
    assert "[Pricing] " in excerpts


@pytest.mark.parametrize("max_chars", [300, 1000, 6000])
@pytest.mark.parametrize("title", [None, "A fairly long document title"])
def test_state_never_exceeds_max_chars(max_chars: int, title: str | None) -> None:
    docs = [
        long_doc() * 3,
        sections(*((f"Part {i}", filler(i + 3)) for i in range(40))),
        "\n".join(f"{i}. Clause {i}\n{filler(4)}" for i in range(1, 50)),
        " ".join(f"w{i:04d}" for i in range(5000)),
        "word " * 20_000,
    ]

    for doc in docs:
        assert len(source_state(title, doc, max_chars)) <= max_chars


@pytest.mark.parametrize("max_chars", [0, 4, 11])
def test_max_chars_within_the_header_returns_a_cut_header(max_chars: int) -> None:
    header = "Title: Very long title\n\n"

    assert source_state("Very long title", filler(50), max_chars) == header[:max_chars]


# -- source_state: unstructured text ----------------------------------------------


def test_unstructured_long_text_is_sampled_from_start_to_end() -> None:
    text = " ".join(f"w{i:05d}" for i in range(4000))

    state = source_state("Words", text, 3000)  # about 2975 characters left: 5 passages of 500 or more

    body = state.removeprefix("Title: Words\n\n")
    assert body.startswith("Excerpts:\n")
    assert "w00000" in body
    assert "w03999" in body
    assert len(body.splitlines()) - 1 == 5
    assert len(state) <= 3000


def test_state_is_deterministic() -> None:
    for doc in (long_doc(), filler(300)):
        assert source_state("T", doc, 700) == source_state("T", doc, 700)


# -- Candidate.state and the summarize steps --------------------------------------


def test_candidate_state_defaults_to_empty() -> None:
    assert Candidate("T", "S", [], "B").state == ""


def test_llm_summarize_sets_the_state_from_the_source(llm: FakeLLM, classifier: FakeClassifier) -> None:
    llm.add(SUMMARIZE, {"title": "LLMTITLE", "summary": "LLMSUMMARY", "tags": [], "body": "LLMBODY"})
    source = Source(long_doc() * 2, title="Memo")
    ctx = context(llm, classifier, source, state_chars=500)

    cand = classic.summarize(ctx, source)

    assert cand.state == source_state("Memo", source.text, 500)
    assert len(cand.state) <= 500


def test_verbatim_summarize_sets_the_state_from_the_source(llm: FakeLLM, classifier: FakeClassifier) -> None:
    source = Source(long_doc(), title="Memo")
    ctx = context(llm, classifier, source)
    ctx.cfg.summarize = False

    cand = classic.summarize(ctx, source)

    assert cand.state == source_state("Memo", source.text, ctx.cfg.classifier.state_chars)
    assert llm.calls == []


# -- StepContext.route_state ------------------------------------------------------


def test_route_state_prefers_the_candidate_state(llm: FakeLLM, classifier: FakeClassifier) -> None:
    ctx = context(llm, classifier, Source("source text"))

    assert ctx.route_state(Candidate("T", "S", [], "B", state="X")) == "X"


def test_route_state_without_a_candidate_state_reads_the_source(llm: FakeLLM, classifier: FakeClassifier) -> None:
    ctx = context(llm, classifier, Source("source text", title="Memo"))

    assert ctx.route_state(Candidate("T", "S", [], "B")) == source_state("Memo", "source text", ctx.cfg.classifier.state_chars)


def test_route_state_without_a_source_falls_back_to_the_compact_state(llm: FakeLLM, classifier: FakeClassifier) -> None:
    ctx = context(llm, classifier, None)
    cand = Candidate("T", "S", [], "B")

    assert ctx.route_state(cand) == ctx.state(cand) == cand.compact(ctx.cfg.classifier.state_chars)


# -- CROW routing through an ingest -----------------------------------------------

RAW = "RAWMARKER prices went up by ten percent."
LLM_REPLY = {"title": "LLMTITLE", "summary": "LLMSUMMARY", "tags": [], "body": "# Overview\n\nLLMBODY"}


def make_wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier, steps: Steps = CROW, **cfg: Any) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", **cfg), llm=llm, classifier=classifier, steps=steps)
    wiki.init()
    root = wiki.store.load()
    wiki.store.create_folder(root, "fruit", "Fruit and its prices.")
    wiki.store.create_folder(root, "beta", "Beta things.")
    return wiki


def to_fruit(classifier: FakeClassifier) -> None:
    classifier.add_choice("route", "fruit", {"fruit": 0.9, NEW: 0.1}, 0.9)
    classifier.add_choice("route", HERE, {HERE: 0.9, NEW: 0.05, NONE: 0.05}, 0.9)


def states(classifier: FakeClassifier, op: str) -> list[str]:
    return [state for o, state, _ in classifier.calls if o == op]


def test_crow_route_reads_the_source_extract_at_every_level(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    llm.add(SUMMARIZE, LLM_REPLY)
    to_fruit(classifier)

    wiki.ingest(RAW, title="Memo")

    expected = source_state("Memo", RAW, wiki.cfg.classifier.state_chars)
    assert states(classifier, "route") == [expected, expected]
    assert "LLMSUMMARY" not in expected


def test_route_fallback_prompt_gets_the_source_extract(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    llm.add(SUMMARIZE, LLM_REPLY).add(ROUTE_FALLBACK, {"action": "select", "folder": "/beta"})
    classifier.add_choice("route", "fruit", {"fruit": 0.5, "beta": 0.4, NEW: 0.1}, 0.3)
    for _ in range(2):
        classifier.add_choice("route", NONE, {NONE: 0.9}, 0.9)

    wiki.ingest(RAW, title="Memo")

    prompt = next(msgs for op, msgs in llm.calls if op == ROUTE_FALLBACK)[1]["content"]
    assert source_state("Memo", RAW, wiki.cfg.classifier.state_chars) in prompt
    assert "LLMBODY" not in prompt


def test_match_consolidate_and_relate_still_read_the_llm_summary(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    fruit = wiki.store.load().find("/fruit")
    source = {"resource": "/raw/s.md", "title": "S"}
    wiki.store.write_note(fruit, title="Apple", summary="About Apple.", body="Seeded.", tags=[], source=source)
    llm.add(SUMMARIZE, LLM_REPLY)
    to_fruit(classifier)
    classifier.noul_scores["match"] = {'"Apple"': 0.9}
    classifier.add_choice("consolidate", NEW_NOTE, {NEW_NOTE: 0.9}, 0.9)

    wiki.ingest(RAW, title="Memo")

    for op in ("match", "relate"):
        (state,) = states(classifier, op)
        assert "LLMTITLE" in state
        assert "RAWMARKER" not in state
    (consolidating,) = states(classifier, "consolidate")  # round 5: a short source is read as the source state
    assert "RAWMARKER" in consolidating and "LLMTITLE" not in consolidating
    assert "LLMSUMMARY" in states(classifier, "match")[0]


def test_custom_summarize_state_is_what_routing_reads(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    def summarize(ctx: StepContext, source: Source) -> Candidate:
        return Candidate("Memo", "A memo.", [], source.text, state="X")

    wiki = make_wiki(bundle, llm, classifier, steps=replace(CROW, summarize=summarize))
    to_fruit(classifier)

    wiki.ingest(RAW)

    assert states(classifier, "route") == ["X", "X"]


def test_custom_summarize_without_state_routes_on_the_source_extract(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    def summarize(ctx: StepContext, source: Source) -> Candidate:
        return Candidate("Memo", "A memo.", [], source.text)

    wiki = make_wiki(bundle, llm, classifier, steps=replace(CROW, summarize=summarize))
    to_fruit(classifier)

    wiki.ingest(RAW, title="Memo")

    expected = source_state("Memo", RAW, wiki.cfg.classifier.state_chars)
    assert states(classifier, "route") == [expected, expected]


def test_verbatim_mode_routes_on_the_source_extract(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, summarize=False)
    to_fruit(classifier)

    wiki.ingest(RAW, title="Memo")

    expected = source_state("Memo", RAW, wiki.cfg.classifier.state_chars)
    assert states(classifier, "route") == [expected, expected]
    assert llm.calls == []


# -- checks and provenance --------------------------------------------------------


def test_check_summarize_rejects_a_state_that_is_not_a_str() -> None:
    cand = Candidate("T", "S", [], "B", state=5)  # type: ignore[arg-type]

    with pytest.raises(StepError, match="summarize"):
        check_summarize(cand)


def test_check_summarize_accepts_a_str_state() -> None:
    cand = Candidate("T", "S", [], "B", state="X")

    assert check_summarize(cand) is cand


def test_decision_hash_changes_with_state_chars(tmp_path: Path) -> None:
    def hashed(chars: int) -> str:
        classifier = ClassifierConfig(model="fake/jev", api_key="k", state_chars=chars)
        return decision_hash(CROW, WikiConfig(bundle=tmp_path / "w", mode="crow", classifier=classifier), Prompts())

    assert hashed(6000) == hashed(6000)
    assert hashed(6000) != hashed(3000)


def test_state_version_is_an_int() -> None:
    assert isinstance(STATE_VERSION, int)


# -- a native summary with subsections (added in review) --------------------------


def test_native_summary_takes_its_subsections_when_it_has_no_text_of_its_own() -> None:
    doc = "\n\n".join([
        "## Executive summary",
        "### Key findings\n\nFindings words. " + "x " * 10,
        "### Recommendations\n\nRecommend words.",
        "## Pricing\n\n" + filler(30),
        "## Delivery\n\n" + filler(30),
    ])

    state = source_state(None, doc, 1000)

    summary = part(state, "Summary")
    assert summary.startswith("Key findings Findings words.")
    assert "Recommendations Recommend words." in summary
    excerpts = part(state, "Excerpts")
    assert "[Key findings]" not in excerpts and "[Recommendations]" not in excerpts
    assert "[Pricing]" in excerpts and "[Delivery]" in excerpts


def test_numbered_native_summary_stops_at_the_next_heading_of_its_level() -> None:
    doc = "\n".join([
        "1. Summary",
        "1.1 Scope of the review",
        "Scope words.",
        "2. Costs",
        filler(30),
        "2.1 Energy",
        filler(30),
    ])

    state = source_state(None, doc, 1000)

    assert part(state, "Summary").startswith("1.1 Scope of the review Scope words.")
    assert "Costs" not in part(state, "Summary")
    assert "[2.1 Energy]" in part(state, "Excerpts")
