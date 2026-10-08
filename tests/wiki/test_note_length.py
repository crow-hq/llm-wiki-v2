# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Round 7, blind from spec-fase3.md: `note_length` (fixed | proportional) and three merge fixes."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from llmw2 import CLASSIC, NoteDraft, Source, StepContext, Wiki, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import decision_hash
from llmw2.bundle.tree import Note
from llmw2.config import ClassifierConfig, LLMConfig
from llmw2.models.llm import reply_tokens
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_giro5 import CODES
from tests.wiki.test_giro5c import ALL, people, reply
from tests.wiki.test_giro6 import CapLLM, run_merge
from tests.wiki.test_llm_json_retry import GOOD, Title, completion, make_llm
from tests.wiki.test_merge_note import INCOMING, MERGED, RAW
from tests.wiki.test_steps import MERGING
from tests.wiki.test_summarize_long import document

PROMPT_ROOM = 6_000
NOTE_RATIO = 4
NOTE_MAX = 45_000
MERGE, MERGE_SOURCE, SUMMARIZE = "librarian/merge", "librarian/merge_source", "librarian/summarize"
COMBINE = "librarian/summarize_combine"
LLM_MERGING = replace(MERGING, summarize=CLASSIC.summarize, merge=CLASSIC.merge)


def cfg_of(note_length: str | None, read_chars: int = 20_000, note_chars: int | None = 1_000) -> WikiConfig:
    extra = {} if note_length is None else {"note_length": note_length}
    return WikiConfig(
        bundle=Path("wiki"),
        mode="classic",
        llm=LLMConfig(read_chars=read_chars),
        classifier=ClassifierConfig(model="fake/jev", api_key="fake-key"),
        note_chars=note_chars,
        **extra,
    )


def expected(cfg: WikiConfig, source: int, existing: int = 0) -> int:
    """The spec's formula, written out here and not taken from the code under test."""
    half = (cfg.effective_read_chars - PROMPT_ROOM) // 2
    note = max(1_000, min(cfg.note_chars or 10_000, half))
    ceiling = max(note, min(NOTE_MAX, half))
    own = min(ceiling, max(note, source // NOTE_RATIO))
    return min(ceiling, existing + own)


# -- config ----------------------------------------------------------------------------------------


def test_note_length_defaults_to_fixed() -> None:
    assert cfg_of(None).note_length == "fixed"


@pytest.mark.parametrize("value", ["fixed", "proportional"])
def test_note_length_from_the_environment(tmp_path: Path, value: str) -> None:
    config = WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_NOTE_LENGTH": value})

    assert config.note_length == value


def test_note_length_without_the_variable_is_fixed(tmp_path: Path) -> None:
    assert WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path)}).note_length == "fixed"


def test_note_length_when_the_variable_is_empty_then_it_is_fixed(tmp_path: Path) -> None:
    assert WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_NOTE_LENGTH": ""}).note_length == "fixed"


def test_note_length_rejects_other_values(tmp_path: Path) -> None:
    with pytest.raises((ValidationError, ValueError)):
        WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_NOTE_LENGTH": "huge"})


# -- target_note_chars ---------------------------------------------------------------------------------


@pytest.mark.parametrize("source,existing", [(0, 0), (500, 0), (50_000, 0), (1_000_000, 30_000), (100, 90_000)])
@pytest.mark.parametrize("note_chars", [None, 1_000, 4_000])
def test_fixed_target_is_always_the_effective_note_chars(note_chars: int | None, source: int, existing: int) -> None:
    cfg = cfg_of("fixed", note_chars=note_chars)

    assert cfg.target_note_chars(source, existing) == cfg.effective_note_chars


def test_fixed_target_with_no_second_argument() -> None:
    cfg = cfg_of("fixed")

    assert cfg.target_note_chars(80_000) == cfg.effective_note_chars == 1_000


def test_proportional_short_source_gets_the_floor() -> None:
    cfg = cfg_of("proportional")  # note 1000, ceiling 7000

    assert cfg.target_note_chars(2_000) == 1_000
    assert cfg.target_note_chars(0) == 1_000


def test_proportional_source_between_floor_and_ceiling_is_a_quarter() -> None:
    cfg = cfg_of("proportional")

    assert cfg.target_note_chars(20_000) == 5_000
    assert cfg.target_note_chars(12_000) == 3_000
    assert cfg.target_note_chars(4_001) == 1_000  # integer quarter, under the floor


def test_proportional_big_source_stops_at_the_ceiling_from_read_chars() -> None:
    cfg = cfg_of("proportional")  # (20000 - 6000) // 2 = 7000

    assert cfg.target_note_chars(100_000) == 7_000
    assert cfg.target_note_chars(10_000_000) == 7_000


def test_proportional_ceiling_is_note_max_with_a_big_read_chars() -> None:
    cfg = cfg_of("proportional", read_chars=200_000)  # (200000 - 6000) // 2 > NOTE_MAX

    assert cfg.target_note_chars(120_000) == 30_000
    assert cfg.target_note_chars(400_000) == NOTE_MAX
    assert cfg.target_note_chars(10_000_000) == NOTE_MAX


def test_proportional_ceiling_follows_read_chars() -> None:
    assert cfg_of("proportional", read_chars=100_000).target_note_chars(1_000_000) == NOTE_MAX
    assert cfg_of("proportional", read_chars=40_000).target_note_chars(1_000_000) == 17_000


def test_proportional_small_read_chars_leaves_only_the_floor() -> None:
    cfg = cfg_of("proportional", read_chars=8_000)  # ceiling = max(1000, 1000)

    assert cfg.target_note_chars(1_000_000) == 1_000


def test_proportional_note_chars_above_note_max_is_never_cut_by_it() -> None:
    cfg = cfg_of("proportional", read_chars=200_000, note_chars=60_000)

    assert cfg.effective_note_chars == 60_000
    assert cfg.target_note_chars(10) == 60_000
    assert cfg.target_note_chars(1_000_000) == 60_000
    assert cfg_of("fixed", read_chars=200_000, note_chars=60_000).target_note_chars(1_000_000) == 60_000


def test_proportional_merge_adds_the_existing_note() -> None:
    cfg = cfg_of("proportional")

    assert cfg.target_note_chars(500, 3_000) == 4_000  # own = floor 1000
    assert cfg.target_note_chars(12_000, 2_000) == 5_000  # own = 3000
    assert cfg.target_note_chars(500, 0) == cfg.target_note_chars(500)


def test_proportional_merge_never_exceeds_the_ceiling() -> None:
    cfg = cfg_of("proportional")

    assert cfg.target_note_chars(500, 9_000) == 7_000
    assert cfg.target_note_chars(100_000, 1_000) == 7_000
    assert cfg.target_note_chars(500, 7_000) == 7_000


def test_proportional_merge_does_not_shrink_below_the_existing_note_when_the_ceiling_allows() -> None:
    cfg = cfg_of("proportional")

    for existing in (1_000, 2_500, 5_000, 6_000):
        assert cfg.target_note_chars(100, existing) >= existing


@pytest.mark.parametrize("read_chars", [8_000, 20_000, 60_000, 200_000])
@pytest.mark.parametrize("source", [0, 3_000, 30_000, 600_000])
@pytest.mark.parametrize("existing", [0, 2_000, 40_000])
def test_proportional_matches_the_formula_of_the_spec(read_chars: int, source: int, existing: int) -> None:
    cfg = cfg_of("proportional", read_chars=read_chars)

    assert cfg.target_note_chars(source, existing) == expected(cfg, source, existing)


# -- decision_hash ------------------------------------------------------------------------------------


def hash_of(cfg: WikiConfig) -> str:
    return decision_hash(CLASSIC, cfg, Prompts())


def test_hash_with_fixed_equals_the_hash_of_a_config_that_does_not_set_the_field() -> None:
    assert hash_of(cfg_of("fixed")) == hash_of(cfg_of(None))


def test_hash_is_different_with_proportional() -> None:
    assert hash_of(cfg_of("proportional")) != hash_of(cfg_of("fixed"))


def test_hash_with_proportional_is_stable() -> None:
    assert hash_of(cfg_of("proportional")) == hash_of(cfg_of("proportional"))


# -- the combine call --------------------------------------------------------------------------------------


COMBINE_READ = 12_000  # a text room of 6 000: the document below takes parts


def combine_run(tracker: UsageTracker, note_length: str | None, note_chars: int | None = 1_000) -> tuple[CapLLM, int]:
    text = document(sections=6)
    assert 6_000 < len(text) < 12_000
    cfg = cfg_of(note_length, read_chars=COMBINE_READ, note_chars=note_chars)
    llm = CapLLM(tracker, [])
    source = Source(text, title="Memo")

    classic.summarize(StepContext(llm, None, Prompts(), cfg, "librarian/system", source), source)

    return llm, len(text)


def combine_call(llm: CapLLM) -> tuple[int | None, str]:
    [(cap, prompt)] = [(c, p) for (op, c), p in zip(llm.caps, llm.prompts, strict=True) if op == COMBINE]
    return cap, prompt


@pytest.mark.parametrize("note_length", ["fixed", None])
def test_combine_with_fixed_states_the_effective_note_chars_and_the_default_cap(tracker: UsageTracker, note_length: str | None) -> None:
    llm, _ = combine_run(tracker, note_length)

    cap, prompt = combine_call(llm)

    assert "at most about 1000 characters" in prompt
    assert cap == reply_tokens(1_000)


def test_combine_with_fixed_and_automatic_note_chars(tracker: UsageTracker) -> None:
    llm, _ = combine_run(tracker, "fixed", note_chars=None)  # effective: (12000 - 6000) // 2 = 3000

    cap, prompt = combine_call(llm)

    assert "at most about 3000 characters" in prompt
    assert cap == reply_tokens(3_000)


def test_combine_with_proportional_states_the_target_of_the_source(tracker: UsageTracker) -> None:
    llm, size = combine_run(tracker, "proportional")
    target = expected(cfg_of("proportional", read_chars=COMBINE_READ), size)
    assert 1_000 < target <= 3_000

    cap, prompt = combine_call(llm)

    assert f"at most about {target} characters" in prompt
    assert "at most about 1000 characters" not in prompt
    assert cap in {reply_tokens(math.floor(1.2 * target)), reply_tokens(math.ceil(1.2 * target))}
    assert cap > reply_tokens(1_000)


# -- the plain merge ---------------------------------------------------------------------------------------


class CapFake(FakeLLM):
    """A FakeLLM that also keeps the output cap of each call."""

    def __init__(self, usage: UsageTracker) -> None:
        super().__init__(usage)
        self.caps: list[tuple[str, int | None]] = []

    def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
        self.caps.append((op, max_tokens))
        return super().chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)


def seed_body(chars: int) -> str:
    return ("Seed line. " * (chars // 11 + 1))[:chars].rstrip()


def make_wiki(bundle: Path, fake: FakeLLM, steps: Any, body: str, **cfg: Any) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", **cfg), llm=fake, steps=steps)
    wiki.init()
    folder, _ = wiki.store.create_folder(wiki.store.load().find("/"), "finance", "Money in and out.")
    source = {"resource": "/raw/seed.md", "title": "Seed"}
    wiki.store.write_note(folder, title="Seed", summary="Seeded.", body=body, tags=[], source=source)
    return wiki


def plain_merge(tracker: UsageTracker, bundle: Path, note_length: str | None, existing_chars: int) -> tuple[CapFake, int, str]:
    """A plain merge of a short source into a note of about `existing_chars`: (llm, real existing length, merge prompt)."""
    llm = CapFake(tracker)
    seen: list[int] = []

    def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        seen.append(len(note.main_body()))
        return classic.merge(ctx, note, source)

    fields: dict[str, Any] = {} if note_length is None else {"note_length": note_length}
    steps = replace(LLM_MERGING, merge=merge)
    wiki = make_wiki(bundle, llm, steps, seed_body(existing_chars), llm=LLMConfig(read_chars=20_000), note_chars=1_000, **fields)
    llm.add(SUMMARIZE, INCOMING).add(MERGE, MERGED)

    wiki.ingest(RAW, title="Memo")

    [(_, messages)] = [c for c in llm.calls if c[0] == MERGE]
    return llm, seen[0], messages[1]["content"]


@pytest.mark.parametrize("note_length", ["fixed", None])
def test_merge_with_fixed_states_the_effective_note_chars_and_caps_at_existing_plus_note(
    tracker: UsageTracker, bundle: Path, note_length: str | None
) -> None:
    llm, existing, prompt = plain_merge(tracker, bundle, note_length, 3_000)

    assert existing > 2_000
    assert "at most about 1000 characters" in prompt
    assert [cap for op, cap in llm.caps if op == MERGE] == [reply_tokens(existing + 1_000)]


def test_merge_with_proportional_states_the_merge_target(tracker: UsageTracker, bundle: Path) -> None:
    llm, existing, prompt = plain_merge(tracker, bundle, "proportional", 3_000)
    cfg = cfg_of("proportional")

    target = cfg.target_note_chars(len(RAW), existing)
    assert target == existing + 1_000

    assert f"at most about {target} characters" in prompt
    assert "at most about 1000 characters" not in prompt
    assert [cap for op, cap in llm.caps if op == MERGE] == [reply_tokens(existing + cfg.target_note_chars(len(RAW)))]


def test_merge_with_proportional_never_states_more_than_the_ceiling(tracker: UsageTracker, bundle: Path) -> None:
    llm, existing, prompt = plain_merge(tracker, bundle, "proportional", 9_000)

    assert existing > 6_000
    assert "at most about 7000 characters" in prompt  # (20000 - 6000) // 2
    assert [cap for op, cap in llm.caps if op == MERGE] == [reply_tokens(existing + 1_000)]


# -- _warn_if_long under proportional ---------------------------------------------------------------------------


def one_call_summary(tracker: UsageTracker, note_length: str, body_chars: int, caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    cfg = cfg_of(note_length)  # a 12 000-character source is one call: room is 14 000
    draft = {"title": "T", "summary": "S.", "tags": [], "body": "w" * body_chars}
    llm = FakeLLM(tracker).add(SUMMARIZE, draft)
    source = Source("s" * 12_000, title="Memo")

    with caplog.at_level(logging.WARNING):
        classic.summarize(StepContext(llm, None, Prompts(), cfg, "librarian/system", source), source)

    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_long_body_warns_under_fixed_and_not_under_proportional_when_the_source_justifies_it(
    tracker: UsageTracker, caplog: pytest.LogCaptureFixture
) -> None:
    assert one_call_summary(tracker, "fixed", 2_000, caplog)
    caplog.clear()
    assert one_call_summary(tracker, "proportional", 2_000, caplog) == []  # target 3000, limit 3600


def test_a_body_over_one_point_two_targets_warns_under_proportional(tracker: UsageTracker, caplog: pytest.LogCaptureFixture) -> None:
    assert one_call_summary(tracker, "proportional", 4_000, caplog)


# -- B1: the cut warning of merge_source ---------------------------------------------------------------------


def cut_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "cut" in r.getMessage()]


def test_merge_source_cut_warning_names_the_source_and_the_budget_once(
    tracker: UsageTracker, bundle: Path, caplog: pytest.LogCaptureFixture
) -> None:
    llm = FakeLLM(tracker)
    seen: list[int] = []

    def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        seen.append(len(note.main_body()))
        return classic.merge(ctx, note, source)

    wiki = make_wiki(bundle, llm, replace(MERGING, merge=merge), seed_body(220), llm=LLMConfig(read_chars=8_000), note_chars=1_000)
    raw = "RAWSOURCE words. " * 180  # 3 060 characters: over 2 * note_chars, over the budget of the call
    llm.add(MERGE_SOURCE, reply(seed_body(220) + " More."))

    with caplog.at_level(logging.WARNING):
        wiki.ingest(raw, title="Memo")

    budget = 8_000 - PROMPT_ROOM - seen[0]
    assert 0 < budget < len(raw)
    cut = cut_warnings(caplog)
    assert len(cut) == 1
    assert str(len(raw)) in cut[0] and str(budget) in cut[0]


def test_merge_source_without_a_cut_logs_no_cut_warning(tracker: UsageTracker, bundle: Path, caplog: pytest.LogCaptureFixture) -> None:
    llm = FakeLLM(tracker)
    wiki = make_wiki(bundle, llm, MERGING, seed_body(220), llm=LLMConfig(read_chars=16_000), note_chars=1_000)
    llm.add(MERGE_SOURCE, reply(seed_body(220) + " More."))

    with caplog.at_level(logging.WARNING):
        wiki.ingest("RAWSOURCE words. " * 180, title="Memo")

    assert cut_warnings(caplog) == []


# -- B2: the retry of an unchanged merge_source draft ------------------------------------------------------------

LONG_TEXT = "RAWSOURCE words. " * 150


def merge_source_prompts(llm: FakeLLM) -> list[str]:
    return [m[1]["content"] for o, m in llm.calls if o == MERGE_SOURCE]


def test_unchanged_retry_adds_the_missing_values_and_differs_from_the_first_request(bundle: Path, llm: FakeLLM) -> None:
    existing = ALL[:20]
    raw = LONG_TEXT + " Codes " + " ".join(CODES) + "."

    run_merge(bundle, llm, existing, raw, [], [reply(people(existing)), reply(people(existing) + " Done.")])

    first, second = merge_source_prompts(llm)
    assert second != first
    assert len(second) > len(first)
    for code in CODES:
        assert second.count(code) > first.count(code)  # named again in the note the retry adds
    assert second.count("previously") > first.count("previously")


def test_unchanged_retry_does_not_list_the_values_the_note_has(bundle: Path, llm: FakeLLM) -> None:
    existing = ALL[:20]
    raw = LONG_TEXT + " Codes " + " ".join(CODES) + "."

    run_merge(bundle, llm, existing, raw, [], [reply(people(existing)), reply(people(existing) + " Done.")])

    first, second = merge_source_prompts(llm)
    for name in existing:
        assert second.count(name) == first.count(name)


def test_no_retry_when_the_source_has_no_values_the_note_lacks(bundle: Path, llm: FakeLLM) -> None:
    existing = ALL[:20]

    run_merge(bundle, llm, existing, LONG_TEXT, [], [reply(people(existing))])

    assert len(merge_source_prompts(llm)) == 1


# -- B3: LLM.json warns when a cut reply is asked again --------------------------------------------------------


def test_json_cut_reply_asked_again_logs_a_warning_with_the_op_and_the_cap(tracker: UsageTracker, caplog: pytest.LogCaptureFixture) -> None:
    llm, requests = make_llm(tracker, completion('{"title": "Ca', "length"), GOOD)

    with caplog.at_level(logging.WARNING):
        result = llm.json("system", "Name it.", Title, op="librarian/title", max_tokens=1234)

    assert result.title == "Cats"
    assert len(requests) == 2
    assert "much shorter" in json.loads(requests[1].content)["messages"][1]["content"]  # behaviour unchanged
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("librarian/title" in m and "1234" in m for m in warned)


def test_json_without_a_cut_logs_no_warning(tracker: UsageTracker, caplog: pytest.LogCaptureFixture) -> None:
    llm, _ = make_llm(tracker, GOOD)

    with caplog.at_level(logging.WARNING):
        llm.json("system", "Name it.", Title, op="librarian/title", max_tokens=1234)

    # the fake reports no usage, and that warning is not this one
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "1234" in r.getMessage()]
