# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Round 6, blind from spec-giro6.md: long sources route without the native summary, parts cap their reply, merge reads the source."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

from llmw2 import CLASSIC, Candidate, NoteDraft, Source, StepContext, Wiki, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents import steps_crow as crow
from llmw2.agents.prompts import Prompts
from llmw2.agents.state import STATE_VERSION, source_state
from llmw2.bundle.tree import Note
from llmw2.config import ClassifierConfig, LLMConfig
from llmw2.errors import ReplyCut
from llmw2.models.llm import LLM, reply_tokens
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_giro5 import CODES
from tests.wiki.test_giro5c import ALL, people, reply
from tests.wiki.test_merge_note import RAW
from tests.wiki.test_route_state import LLM_REPLY, NONE, ROUTE_FALLBACK, SUMMARIZE, make_wiki, sections, states, to_fruit
from tests.wiki.test_steps import MERGING

MERGE, MERGE_SOURCE = "librarian/merge", "librarian/merge_source"
PART, COMBINE = "librarian/summarize_part", "librarian/summarize_combine"
NOTE = 1_000
READ = 16_000
TEXT_ROOM = READ - 6_000
BIG = 4_000  # note_chars of the part tests: the cap of a part's notes is BIG // 2
LLM_MERGING = replace(MERGING, summarize=CLASSIC.summarize, merge=CLASSIC.merge)
DRAFT = {"title": "Whole note", "summary": "All of it.", "tags": ["t"], "body": "# Overview\n\nThe whole."}
FILLER = "The parties agree on the terms that follow and on nothing else at all, as the text says here."


def doc() -> str:
    """A structured document of several thousand characters with a native summary section that is not first."""
    return sections(
        ("Introduction", "RAWMARKER intro words. " * 80),
        ("Pricing", " ".join([FILLER] * 30)),
        ("Executive summary", "Key point. " * 200),
        ("Closing", " ".join([FILLER] * 30)),
    )


# -- 1. source_state(native=False) --------------------------------------------------------------------


def test_default_source_state_still_has_the_summary_block() -> None:
    text = doc()

    assert source_state("Memo", text, 1000).startswith("Title: Memo\n\nSummary:\nKey point.")
    assert source_state("Memo", text, 1000, native=True) == source_state("Memo", text, 1000)


def test_native_false_has_no_summary_block() -> None:
    state = source_state("Memo", doc(), 1000, native=False)

    assert "Summary:\n" not in state


def test_native_false_keeps_the_summary_section_as_an_ordinary_section() -> None:
    text = doc()

    plain = source_state("Memo", text, 1000, native=False)

    assert "Executive summary" in plain
    assert plain != source_state("Memo", text, 1000)
    assert len(plain) <= 1000


def test_native_false_is_deterministic_and_within_max_chars() -> None:
    for max_chars in (300, 700, 2000):
        first = source_state("Memo", doc(), max_chars, native=False)
        assert first == source_state("Memo", doc(), max_chars, native=False)
        assert len(first) <= max_chars


def test_native_false_leaves_a_short_text_verbatim() -> None:
    assert source_state("Memo", "Some text.", 1000, native=False) == "Title: Memo\n\nSome text."


def test_state_version_was_bumped() -> None:
    assert STATE_VERSION > 1  # HEAD has 1


# -- 2. routing_state ---------------------------------------------------------------------------------


def context(llm: FakeLLM, source: Source | None) -> StepContext:
    cfg = WikiConfig(
        bundle=Path("wiki"),
        mode="crow",
        llm=LLMConfig(read_chars=20_000),
        classifier=ClassifierConfig(model="fake/jev", api_key="fake-key"),
        note_chars=NOTE,
    )
    return StepContext(llm, FakeClassifier(llm.usage), Prompts(), cfg, "librarian/system", source)


def cand_for(**fields: Any) -> Candidate:
    return Candidate("Cand title", "Cand summary.", [], "Cand body. " * 50, **fields)


def test_routing_state_of_a_long_source_has_no_native_summary(llm: FakeLLM) -> None:
    source = Source(doc(), title="Memo")
    ctx = context(llm, source)
    assert len(source.text) > 2 * NOTE
    state_chars = ctx.cfg.classifier.state_chars

    got = crow.routing_state(ctx, cand_for())

    assert got == source_state("Memo", source.text, state_chars, native=False)
    assert got != source_state("Memo", source.text, state_chars)


def test_routing_state_of_a_long_source_with_the_default_state_is_also_no_native(llm: FakeLLM) -> None:
    source = Source(doc(), title="Memo")
    ctx = context(llm, source)
    state_chars = ctx.cfg.classifier.state_chars
    cand = cand_for(state=source_state("Memo", source.text, state_chars))

    assert crow.routing_state(ctx, cand) == source_state("Memo", source.text, state_chars, native=False)


def test_routing_state_of_a_long_source_keeps_a_custom_state(llm: FakeLLM) -> None:
    ctx = context(llm, Source(doc(), title="Memo"))

    assert crow.routing_state(ctx, cand_for(state="CUSTOM")) == "CUSTOM"


def test_routing_state_of_a_short_source_is_unchanged(llm: FakeLLM) -> None:
    source = Source(doc()[: 2 * NOTE], title="Memo")
    ctx = context(llm, source)
    cand = cand_for()

    assert len(source.text) <= 2 * NOTE
    assert crow.routing_state(ctx, cand) == ctx.route_state(cand)


# -- 3. route and the fallback send it ------------------------------------------------------------------


def test_crow_route_sends_the_no_native_state_for_a_long_source(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, note_chars=NOTE)
    llm.add(SUMMARIZE, LLM_REPLY)
    to_fruit(classifier)
    text = doc()

    wiki.ingest(text, title="Memo")

    expected = source_state("Memo", text, wiki.cfg.classifier.state_chars, native=False)
    assert states(classifier, "route") == [expected, expected]
    assert "Summary:\n" not in expected


def test_route_fallback_gets_the_no_native_state_for_a_long_source(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, note_chars=NOTE)
    llm.add(SUMMARIZE, LLM_REPLY).add(ROUTE_FALLBACK, {"action": "select", "folder": "/beta"})
    classifier.add_choice("route", "fruit", {"fruit": 0.5, "beta": 0.4, "New subfolder": 0.1}, 0.3)
    for _ in range(2):
        classifier.add_choice("route", NONE, {NONE: 0.9}, 0.9)
    text = doc()

    wiki.ingest(text, title="Memo")

    prompt = next(m[1]["content"] for o, m in llm.calls if o == ROUTE_FALLBACK)
    assert source_state("Memo", text, wiki.cfg.classifier.state_chars, native=False) in prompt


# -- 4. summarize_part: the reply cap -------------------------------------------------------------------


class CapLLM(LLM):
    """Records the output cap of each call; a part reply is the next of `parts` (a ReplyCut is raised as a cut reply)."""

    def __init__(self, usage: UsageTracker, parts: list[str | ReplyCut]) -> None:
        super().__init__(LLMConfig(model="fake/llm"), usage)
        self.parts = list(parts)
        self.caps: list[tuple[str, int | None]] = []
        self.prompts: list[str] = []

    def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
        self.caps.append((op, max_tokens))
        self.prompts.append(messages[1]["content"])
        if op == COMBINE:
            return json.dumps(DRAFT)
        reply_ = self.parts.pop(0) if self.parts else "- a note line.\n" * 100
        if isinstance(reply_, ReplyCut):
            raise reply_
        return reply_

    def part_caps(self) -> list[int | None]:
        return [cap for op, cap in self.caps if op == PART]


def part_context(llm: LLM, source: Source) -> StepContext:
    cfg = WikiConfig(
        bundle=Path("wiki"),
        mode="classic",
        llm=LLMConfig(read_chars=READ),
        classifier=ClassifierConfig(model="fake/jev", api_key="fake-key"),
        note_chars=BIG,
    )
    return StepContext(llm, None, Prompts(), cfg, "librarian/system", source)


def long_source() -> Source:
    return Source("word " * (TEXT_ROOM * 5 // 2 // 5), title="Memo")  # three blocks; each part's notes cap is BIG // 2


def test_summary_version_is_seven() -> None:
    assert classic.SUMMARY_VERSION == 7


def test_the_part_reply_cap_is_1_3_times_the_cap_of_the_notes(tracker: UsageTracker) -> None:
    source = long_source()
    llm = CapLLM(tracker, [])

    classic.summarize(part_context(llm, source), source)

    caps = llm.part_caps()
    assert len(caps) >= 2
    assert set(caps) == {reply_tokens(max(classic.MIN_PART, math.ceil(1.3 * (BIG // 2))))}
    assert caps[0] < reply_tokens(BIG)  # no longer the output cap of a whole note


def test_a_short_cut_part_is_asked_once_more_at_the_note_cap(tracker: UsageTracker) -> None:
    source = long_source()
    llm = CapLLM(tracker, [ReplyCut("cut", text="- tiny start")])  # 12 characters, under cap // 2

    classic.summarize(part_context(llm, source), source)

    caps = llm.part_caps()
    assert caps[:2] == [reply_tokens(math.ceil(1.3 * (BIG // 2))), reply_tokens(BIG)]
    assert caps.count(reply_tokens(BIG)) == 1  # one retry, for that part only


def test_the_retry_reply_replaces_the_cut_one(tracker: UsageTracker) -> None:
    source = long_source()
    llm = CapLLM(tracker, [ReplyCut("cut", text="- tiny start"), "- RETRYMARK notes."])

    classic.summarize(part_context(llm, source), source)

    combine = next(p for (op, _), p in zip(llm.caps, llm.prompts, strict=True) if op == COMBINE)
    assert "RETRYMARK" in combine
    assert "tiny start" not in combine


def test_a_cut_part_that_keeps_enough_is_not_asked_again(tracker: UsageTracker) -> None:
    source = long_source()
    llm = CapLLM(tracker, [ReplyCut("cut", text="- " + "x" * (BIG // 4 + 100))])  # over cap // 2

    classic.summarize(part_context(llm, source), source)

    assert reply_tokens(BIG) not in llm.part_caps()


def test_a_short_reply_that_was_not_cut_is_not_asked_again(tracker: UsageTracker) -> None:
    source = long_source()
    llm = CapLLM(tracker, ["- tiny"])

    classic.summarize(part_context(llm, source), source)

    assert reply_tokens(BIG) not in llm.part_caps()


# -- 5. merge of long sources reads the source directly -------------------------------------------------

LONG_TEXT = "RAWSOURCE words. " * 150  # 2 550 characters, over 2 * NOTE


def run_merge(
    bundle: Path, llm: FakeLLM, existing: list[str], raw: str, merges: list[dict[str, Any]], sources: list[dict[str, Any]]
) -> NoteDraft:
    seen: list[NoteDraft] = []

    def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        seen.append(classic.merge(ctx, note, source))
        return seen[-1]

    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", note_chars=NOTE), llm=llm, steps=replace(LLM_MERGING, merge=merge))
    wiki.init()
    folder, _ = wiki.store.create_folder(wiki.store.load().find("/"), "finance", "Money in and out.")
    wiki.store.write_note(
        folder, title="Seed", summary="Seeded.", body=people(existing), tags=[], source={"resource": "/raw/seed.md", "title": "Seed"}
    )
    llm.add(SUMMARIZE, {"title": "In", "summary": "In.", "tags": [], "body": "# Overview\n\nPlain words only."})
    if merges:
        llm.add(MERGE, *merges)
    if sources:
        llm.add(MERGE_SOURCE, *sources)
    wiki.ingest(raw, title="Memo")
    return seen[0]


def merge_ops(llm: FakeLLM) -> list[str]:
    return [op for op in llm.ops if op in (MERGE, MERGE_SOURCE)]


def test_a_long_source_goes_straight_to_merge_source(bundle: Path, llm: FakeLLM) -> None:
    existing = ALL[:20]
    assert len(LONG_TEXT) > 2 * NOTE

    result = run_merge(bundle, llm, existing, LONG_TEXT, [], [reply(people(existing) + " More.")])

    assert merge_ops(llm) == [MERGE_SOURCE]
    assert MERGE not in llm.ops
    assert "More." in result.body


def test_the_long_source_merge_prompt_holds_the_source_text(bundle: Path, llm: FakeLLM) -> None:
    run_merge(bundle, llm, ALL[:20], LONG_TEXT, [], [reply(people(ALL[:20]) + " More.")])

    prompt = next(m[1]["content"] for o, m in llm.calls if o == MERGE_SOURCE)
    assert "RAWSOURCE words." in prompt


def test_an_unchanged_draft_with_new_values_is_asked_once_more(bundle: Path, llm: FakeLLM) -> None:
    existing = ALL[:20]
    raw = LONG_TEXT + " Codes " + " ".join(CODES) + "."
    changed = reply(people(existing) + " " + " ".join(CODES))

    result = run_merge(bundle, llm, existing, raw, [], [reply(people(existing)), changed])

    assert merge_ops(llm) == [MERGE_SOURCE, MERGE_SOURCE]
    assert result.body == changed["body"]


def test_an_unchanged_draft_is_not_asked_again_when_the_source_has_no_new_values(bundle: Path, llm: FakeLLM) -> None:
    existing = ALL[:20]

    result = run_merge(bundle, llm, existing, LONG_TEXT, [], [reply(people(existing))])

    assert merge_ops(llm) == [MERGE_SOURCE]
    assert result.body == people(existing)


def test_the_retry_for_new_values_is_at_most_one(bundle: Path, llm: FakeLLM) -> None:
    existing = ALL[:20]
    raw = LONG_TEXT + " Codes " + " ".join(CODES) + "."

    run_merge(bundle, llm, existing, raw, [], [reply(people(existing)), reply(people(existing))])  # a third call would fail

    assert merge_ops(llm) == [MERGE_SOURCE, MERGE_SOURCE]


def test_the_lost_values_check_follows_the_new_values_retry(bundle: Path, llm: FakeLLM) -> None:
    existing = ALL[:20]
    codes = " ".join(CODES)
    raw = LONG_TEXT + " Codes " + codes + "."
    lossy = reply(people(existing[:10]) + " " + codes)  # half of the names dropped
    better = reply(people(existing[:19]) + " " + codes)

    result = run_merge(bundle, llm, existing, raw, [], [reply(people(existing)), lossy, better])

    assert merge_ops(llm) == [MERGE_SOURCE] * 3
    assert result.body == better["body"]


def test_a_short_source_still_starts_with_the_candidate_merge(bundle: Path, llm: FakeLLM) -> None:
    existing = ALL[:20]
    assert len(RAW) <= 2 * NOTE

    result = run_merge(bundle, llm, existing, RAW, [reply(people(existing) + " More.")], [])

    assert merge_ops(llm) == [MERGE]
    assert "More." in result.body


def test_the_constant_lives_in_steps_classic_and_crow_uses_it() -> None:
    assert classic.CONSOLIDATE_SOURCE_MAX == crow.CONSOLIDATE_SOURCE_MAX == 2
