# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Round 5c, blind from spec-giro5c.md: the merge keeps the replaced values; route reads the candidate on long sources."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import CLASSIC, Candidate, NoteDraft, Source, StepContext, Wiki, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents import steps_crow as crow
from llmw2.agents.prompts import Prompts
from llmw2.agents.state import source_state
from llmw2.bundle.tree import Note
from llmw2.config import ClassifierConfig, LLMConfig
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_giro5 import CODES
from tests.wiki.test_merge_note import RAW
from tests.wiki.test_route_state import LLM_REPLY, NONE, ROUTE_FALLBACK, SUMMARIZE, make_wiki, states, to_fruit
from tests.wiki.test_steps import MERGING

MERGE, MERGE_SOURCE = "librarian/merge", "librarian/merge_source"
MARK = "dropped these values of the existing note"
NOTE_HEAD = (
    "\n\nThe previous version of the merged note dropped these values of the existing note; keep each of them, "
    'stating the newer value next to it ("previously …") where the incoming text replaces it:\n'
)
FIRST = ["Anna", "Bruno", "Carla", "Dario", "Elena", "Fabio", "Giada", "Hugo", "Irene", "Luca"]
LAST = ["Alberti", "Bianchi", "Conti", "Donati", "Esposito", "Fabbri", "Greco", "Leone", "Marini", "Neri"]
ALL = [f"{f} {last}" for last in LAST for f in FIRST][::-1]  # 100 distinct names, not in alphabetical order
PLAIN = {"title": "In", "summary": "In.", "tags": [], "body": "# Overview\n\nPlain words only."}
LLM_MERGING = replace(MERGING, summarize=CLASSIC.summarize, merge=CLASSIC.merge)


def people(names: list[str]) -> str:
    return "# Overview\n\nPeople: " + ", ".join(names) + "."


def reply(body: str) -> dict[str, Any]:
    return {"title": "Seed", "summary": "Merged.", "tags": [], "body": body}


def run_merge(
    bundle: Path,
    llm: FakeLLM,
    existing: list[str],
    merges: list[dict[str, Any]],
    sources: list[dict[str, Any]] | None = None,
    incoming: dict[str, Any] = PLAIN,
) -> NoteDraft:
    """Ingest into a seed note holding `existing` names; return the draft the classic merge returned."""
    seen: list[NoteDraft] = []

    def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        seen.append(classic.merge(ctx, note, source))
        return seen[-1]

    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm, steps=replace(LLM_MERGING, merge=merge))
    wiki.init()
    folder, _ = wiki.store.create_folder(wiki.store.load().find("/"), "finance", "Money in and out.")
    wiki.store.write_note(
        folder, title="Seed", summary="Seeded.", body=people(existing), tags=[], source={"resource": "/raw/seed.md", "title": "Seed"}
    )
    llm.add(SUMMARIZE, incoming).add(MERGE, *merges)
    if sources:
        llm.add(MERGE_SOURCE, *sources)
    wiki.ingest(RAW, title="Memo")
    return seen[0]


def prompts(llm: FakeLLM, op: str) -> list[str]:
    return [m[1]["content"] for o, m in llm.calls if o == op]


def note_for(lost: list[str]) -> str:
    return NOTE_HEAD + "\n".join(f"- {v}" for v in lost)


def listed(prompt: str) -> list[str]:
    """The values listed in the note appended to a retry prompt."""
    tail = prompt.split(MARK, 1)[1]
    return [line[2:] for line in tail.splitlines() if line.startswith("- ")]


# -- 1. the lost-values check in merge --------------------------------------------------------------


def test_names_are_values() -> None:
    assert len(classic.incoming_values(people(ALL[:20]))) == 20


def test_ten_percent_lost_is_not_retried(bundle: Path, llm: FakeLLM) -> None:
    # 2 of 20 values lost is exactly 10%: it takes more than that to retry
    existing = ALL[:20]

    result = run_merge(bundle, llm, existing, [reply(people(existing[:18]))])

    assert llm.ops.count(MERGE) == 1 and MERGE_SOURCE not in llm.ops
    assert result.body == people(existing[:18])


def test_nothing_lost_is_not_retried(bundle: Path, llm: FakeLLM) -> None:
    result = run_merge(bundle, llm, ALL[:20], [reply(people(ALL[:20]) + " More.")])

    assert llm.ops.count(MERGE) == 1
    assert "More." in result.body


def test_note_without_values_is_not_retried(bundle: Path, llm: FakeLLM) -> None:
    result = run_merge(bundle, llm, [], [reply("# Overview\n\nPlain and different.")])

    assert llm.ops.count(MERGE) == 1
    assert result.body.endswith("different.")


def test_more_than_ten_percent_lost_asks_again_and_takes_the_better_draft(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    existing = ALL[:20]
    better = reply(people(existing[:19]))

    with caplog.at_level(logging.WARNING):
        result = run_merge(bundle, llm, existing, [reply(people(existing[:17])), better])  # 3 lost of 20

    assert llm.ops.count(MERGE) == 2 and MERGE_SOURCE not in llm.ops
    assert result.body == better["body"]
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_the_retry_is_the_same_prompt_plus_the_lost_values(bundle: Path, llm: FakeLLM) -> None:
    existing = ALL[:20]

    run_merge(bundle, llm, existing, [reply(people(existing[:17])), reply(people(existing[:19]))])

    first, second = prompts(llm, MERGE)
    assert second != first
    assert second.replace(note_for(existing[17:]), "") == first


def test_lost_values_are_original_substrings_in_order_of_appearance(bundle: Path, llm: FakeLLM) -> None:
    existing = ALL[:20]

    run_merge(bundle, llm, existing, [reply(people(existing[::2])), reply(people(existing))])

    assert listed(prompts(llm, MERGE)[1]) == existing[1::2]  # "Anna Bianchi", not "annabianchi"


@pytest.mark.parametrize("kept", [10, 9], ids=["as many lost", "more lost"])
def test_the_new_draft_is_taken_only_if_it_loses_fewer_values(bundle: Path, llm: FakeLLM, kept: int) -> None:
    existing = ALL[:20]
    first = reply(people(existing[:10]))  # 10 lost
    second = reply(people(existing[:kept]) + " Reworded.")  # 10 or 11 lost

    result = run_merge(bundle, llm, existing, [first, second])

    assert llm.ops.count(MERGE) == 2
    assert result.body == first["body"]


def test_there_is_no_further_retry_even_if_the_new_draft_still_loses_too_much(bundle: Path, llm: FakeLLM) -> None:
    existing = ALL[:20]
    second = reply(people(existing[:12]))  # 8 lost: fewer than 10, still over 10%

    result = run_merge(bundle, llm, existing, [reply(people(existing[:10])), second])  # a third call would fail in FakeLLM

    assert llm.ops.count(MERGE) == 2
    assert result.body == second["body"]


def test_at_most_80_lost_values_are_listed(bundle: Path, llm: FakeLLM) -> None:
    run_merge(bundle, llm, ALL, [reply(people([])), reply(people(ALL[:5]))])

    assert listed(prompts(llm, MERGE)[1]) == ALL[:80]


def test_after_the_fallback_the_retry_uses_the_merge_source_prompt(bundle: Path, llm: FakeLLM) -> None:
    # the first draft drops the new codes (fallback to merge_source), whose draft drops names: merge_source is asked again
    existing = ALL[:20]
    codes = " ".join(CODES)
    incoming = {"title": "In", "summary": "In.", "tags": [], "body": "# Overview\n\nCodes " + codes + "."}
    second = reply(people(existing[:5]) + " " + codes)
    third = reply(people(existing[:19]) + " " + codes)

    result = run_merge(bundle, llm, existing, [reply(people(existing))], [second, third], incoming)

    assert llm.ops.count(MERGE) == 1 and llm.ops.count(MERGE_SOURCE) == 2
    first, again = prompts(llm, MERGE_SOURCE)
    assert again.replace(note_for(existing[5:]), "") == first
    assert RAW in again
    assert result.body == third["body"]


# -- 2. routing_state -------------------------------------------------------------------------------

NOTE = 1_000


def context(llm: FakeLLM, source: Source | None) -> StepContext:
    cfg = WikiConfig(
        bundle=Path("wiki"),
        mode="crow",
        llm=LLMConfig(read_chars=20_000),
        classifier=ClassifierConfig(model="fake/jev", api_key="fake-key"),
        note_chars=NOTE,
    )
    return StepContext(llm, FakeClassifier(llm.usage), Prompts(), cfg, "librarian/system", source)


def cand_and_source(chars: int, **cand: Any) -> tuple[Candidate, Source]:
    text = ("Alpha beta gamma. " * 200)[:chars]
    return Candidate("Cand title", "Cand summary.", [], "Cand body. " * 50, **cand), Source(text, title="Memo")


def test_routing_state_of_a_short_source_is_the_source_state(llm: FakeLLM) -> None:
    cand, source = cand_and_source(2 * NOTE)
    ctx = context(llm, source)

    assert crow.routing_state(ctx, cand) == ctx.route_state(cand) == source_state("Memo", source.text, ctx.cfg.classifier.state_chars)
    assert crow.routing_state(ctx, cand) != ctx.state(cand)


def test_routing_state_of_a_long_source_is_the_source_state_without_the_native_summary(llm: FakeLLM) -> None:
    cand, source = cand_and_source(2 * NOTE + 1)
    ctx = context(llm, source)

    expected = source_state(source.title, source.text, ctx.cfg.classifier.state_chars, native=False)
    assert crow.routing_state(ctx, cand) == expected  # round 6 (R1): no native summary


def test_routing_state_of_a_long_source_replaces_the_default_source_state_set_by_summarize(llm: FakeLLM) -> None:
    _, source = cand_and_source(3 * NOTE)
    ctx = context(llm, source)
    default = source_state("Memo", source.text, ctx.cfg.classifier.state_chars)
    cand, _ = cand_and_source(3 * NOTE, state=default)

    assert crow.routing_state(ctx, cand) == source_state(source.title, source.text, ctx.cfg.classifier.state_chars, native=False)


@pytest.mark.parametrize("chars", [500, 3 * NOTE])
def test_routing_state_keeps_an_explicit_custom_state(llm: FakeLLM, chars: int) -> None:
    cand, source = cand_and_source(chars, state="CUSTOM")

    assert crow.routing_state(context(llm, source), cand) == "CUSTOM"


# -- 3. CROW route and the routing fallback send it -------------------------------------------------

LONG = "RAWMARKER " + "Prices went up by ten percent in the quarter. " * 80


def test_crow_route_sends_the_state_without_summary_for_a_long_source(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, note_chars=NOTE)
    llm.add(SUMMARIZE, LLM_REPLY)
    to_fruit(classifier)
    assert len(LONG) > 2 * NOTE

    wiki.ingest(LONG, title="Memo")

    routed = states(classifier, "route")
    assert len(routed) == 2 and routed[0] == routed[1]
    assert "LLMTITLE" not in routed[0] and "RAWMARKER" in routed[0]


def test_crow_route_still_sends_the_source_state_for_a_short_source(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, note_chars=NOTE)
    llm.add(SUMMARIZE, LLM_REPLY)
    to_fruit(classifier)
    short = "RAWMARKER prices went up."

    wiki.ingest(short, title="Memo")

    expected = source_state("Memo", short, wiki.cfg.classifier.state_chars)
    assert states(classifier, "route") == [expected, expected]


def test_route_fallback_gets_the_state_without_summary_for_a_long_source(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, note_chars=NOTE)
    llm.add(SUMMARIZE, LLM_REPLY).add(ROUTE_FALLBACK, {"action": "select", "folder": "/beta"})
    classifier.add_choice("route", "fruit", {"fruit": 0.5, "beta": 0.4, "New subfolder": 0.1}, 0.3)
    for _ in range(2):
        classifier.add_choice("route", NONE, {NONE: 0.9}, 0.9)

    wiki.ingest(LONG, title="Memo")

    prompt = prompts(llm, ROUTE_FALLBACK)[0]
    assert "LLMTITLE" not in prompt
    assert "RAWMARKER" in prompt
