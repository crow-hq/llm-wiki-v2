# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`read_chars` (characters a prompt may hold) and `note_chars` (how long a note should be): defaults, limits, prompts, hash."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from llmw2 import CLASSIC, Candidate, NoteDraft, Route, Source, StepContext, Steps, Wiki, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import LENGTH_RULE, decision_hash
from llmw2.config import ClassifierConfig, LLMConfig
from tests.wiki.fakes import FakeLLM

ROOM = 6_000  # what a prompt holds besides the text (PROMPT_ROOM of the spec)
BODY = {"title": "T", "summary": "S.", "tags": [], "body": "B."}
SUMMARIZE = "librarian/summarize"


def env_for(bundle: Path, **variables: str) -> dict[str, str]:
    return {"OKF_BUNDLE": str(bundle), **variables}


def cfg_of(tmp_path: Path, **fields: Any) -> WikiConfig:
    return WikiConfig(bundle=tmp_path / "wiki", mode="classic", **fields)


def llm_of(provider: str) -> dict[str, str]:
    if provider == "custom":
        return {"provider": "custom", "base_url": "http://127.0.0.1:8000/v1", "model": "m"}
    return {"provider": provider}


# -- read_chars: defaults, environment, limits -------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("openrouter", 100_000), ("openai", 100_000), ("gemini", 100_000), ("ollama", 16_000), ("custom", 16_000)],
)
def test_effective_read_chars_when_nothing_is_set_then_the_provider_default_applies(tmp_path: Path, provider: str, expected: int) -> None:
    config = cfg_of(tmp_path, llm=llm_of(provider))

    assert config.llm.read_chars is None
    assert config.effective_read_chars == expected


def test_read_chars_when_set_on_the_llm_then_it_wins_over_the_provider_default(tmp_path: Path) -> None:
    assert cfg_of(tmp_path, llm={"read_chars": 20_000}).effective_read_chars == 20_000
    assert cfg_of(tmp_path, llm={**llm_of("ollama"), "read_chars": 40_000}).effective_read_chars == 40_000


def test_read_chars_from_the_environment(tmp_path: Path) -> None:
    config = WikiConfig.from_env(env_for(tmp_path, OKF_LLM_READ_CHARS="24000"))

    assert config.llm.read_chars == 24_000
    assert config.effective_read_chars == 24_000


def test_read_chars_when_the_variable_is_empty_then_it_is_automatic(tmp_path: Path) -> None:
    config = WikiConfig.from_env(env_for(tmp_path, OKF_LLM_READ_CHARS=""))

    assert config.llm.read_chars is None
    assert config.effective_read_chars == 100_000


def test_read_chars_accepts_the_minimum_and_refuses_less(tmp_path: Path) -> None:
    assert cfg_of(tmp_path, llm={"read_chars": 8_000}).effective_read_chars == 8_000
    with pytest.raises(ValidationError, match="read_chars"):
        cfg_of(tmp_path, llm={"read_chars": 7_999})
    with pytest.raises(ValidationError, match="read_chars"):
        WikiConfig.from_env(env_for(tmp_path, OKF_LLM_READ_CHARS="7999"))


# -- source_chars: the old name still works, with a warning --------------------------------------


def test_effective_read_chars_when_source_chars_is_set_then_it_is_used_with_a_deprecation_warning(tmp_path: Path) -> None:
    with pytest.warns(DeprecationWarning):
        config = cfg_of(tmp_path, source_chars=30_000)

    assert config.effective_read_chars == 30_000


def test_effective_read_chars_when_both_are_set_then_read_chars_wins(tmp_path: Path) -> None:
    with pytest.warns(DeprecationWarning):
        config = cfg_of(tmp_path, source_chars=30_000, llm={"read_chars": 12_000})

    assert config.effective_read_chars == 12_000


def test_source_chars_when_not_set_then_no_warning(tmp_path: Path, recwarn: pytest.WarningsRecorder) -> None:
    cfg_of(tmp_path, llm={"read_chars": 12_000})

    assert [w for w in recwarn if issubclass(w.category, DeprecationWarning) and "source_chars" in str(w.message)] == []


# -- note_chars: defaults, environment, limits ---------------------------------------------------


def test_note_chars_from_the_environment(tmp_path: Path) -> None:
    config = WikiConfig.from_env(env_for(tmp_path, OKF_NOTE_CHARS="4000"))

    assert config.note_chars == 4_000
    assert config.effective_note_chars == 4_000


def test_note_chars_when_the_variable_is_empty_then_it_is_automatic(tmp_path: Path) -> None:
    config = WikiConfig.from_env(env_for(tmp_path, OKF_NOTE_CHARS=""))

    assert config.note_chars is None
    assert config.effective_note_chars == 10_000


def test_note_chars_accepts_the_minimum_and_refuses_less(tmp_path: Path) -> None:
    assert cfg_of(tmp_path, note_chars=1_000).note_chars == 1_000
    with pytest.raises(ValidationError, match="note_chars"):
        cfg_of(tmp_path, note_chars=999)
    with pytest.raises(ValidationError, match="note_chars"):
        WikiConfig.from_env(env_for(tmp_path, OKF_NOTE_CHARS="999"))


@pytest.mark.parametrize(
    ("read_chars", "note_chars", "expected"),
    [
        (100_000, None, 10_000),  # the soft cap of 10 000, half of the room is far above it
        (16_000, None, 5_000),  # (16 000 - 6 000) // 2
        (8_000, None, 1_000),  # (8 000 - 6 000) // 2
        (12_001, None, 3_000),  # integer division
        (100_000, 3_000, 3_000),  # a smaller cap is kept
        (100_000, 50_000, 47_000),  # an explicit cap above the default is allowed, up to half of the room
        (16_000, 9_000, 5_000),
        (16_000, 3_000, 3_000),
        (8_000, 4_000, 1_000),
        (8_000, 1_000, 1_000),
    ],
)
def test_effective_note_chars_is_the_cap_or_half_of_the_text_room_whichever_is_smaller(
    tmp_path: Path, read_chars: int, note_chars: int | None, expected: int
) -> None:
    config = cfg_of(tmp_path, llm={"read_chars": read_chars}, note_chars=note_chars)

    assert config.effective_note_chars == expected


def test_effective_note_chars_follows_the_provider_default_read_chars(tmp_path: Path) -> None:
    assert cfg_of(tmp_path, llm=llm_of("ollama")).effective_note_chars == 5_000
    assert cfg_of(tmp_path, llm=llm_of("openrouter")).effective_note_chars == 10_000


# -- note_chars reaches the prompts, also from a custom step --------------------------------------


def context(llm: FakeLLM, tmp_path: Path, source: Source | None = None, /, **fields: Any) -> StepContext:
    classifier = ClassifierConfig(model="fake/jev", api_key="fake-key")
    cfg = WikiConfig(bundle=tmp_path / "wiki", mode="classic", classifier=classifier, **fields)
    return StepContext(llm, None, Prompts(), cfg, "librarian/system", source)


def test_a_custom_step_rendering_summarize_gets_note_chars_without_passing_it(llm: FakeLLM, tmp_path: Path) -> None:
    llm.add(SUMMARIZE, BODY)
    ctx = context(llm, tmp_path, llm={"read_chars": 16_000})

    ctx.ask_json(SUMMARIZE, NoteDraft, source_title="Memo", source_resource="(none)", source_text="Text.")

    prompt = llm.calls[0][1][1]["content"]
    assert "5000" in prompt and "characters" in prompt


def test_the_prompt_states_the_length_rule_with_the_effective_number(llm: FakeLLM, tmp_path: Path) -> None:
    llm.add(SUMMARIZE, BODY)
    ctx = context(llm, tmp_path, llm={"read_chars": 100_000}, note_chars=3_000)

    ctx.ask_json(SUMMARIZE, NoteDraft, source_title="Memo", source_resource="(none)", source_text="Text.")

    assert "about 3000 characters" in llm.calls[0][1][1]["content"]


def test_a_value_passed_to_ask_json_wins_over_the_default_note_chars(llm: FakeLLM, tmp_path: Path) -> None:
    llm.add(SUMMARIZE, BODY)
    ctx = context(llm, tmp_path, note_chars=3_000)

    ctx.ask_json(SUMMARIZE, NoteDraft, source_title="M", source_resource="-", source_text="Text.", note_chars=1_234)

    prompt = llm.calls[0][1][1]["content"]
    assert "about 1234 characters" in prompt and "3000" not in prompt


def test_ask_text_also_passes_note_chars(llm: FakeLLM, tmp_path: Path) -> None:
    llm.add(SUMMARIZE, "plain reply")
    ctx = context(llm, tmp_path, note_chars=2_500)

    ctx.ask_text(SUMMARIZE, source_title="M", source_resource="-", source_text="Text.")

    assert "about 2500 characters" in llm.calls[0][1][1]["content"]


def test_note_rules_ask_to_write_compactly_before_dropping_and_never_to_drop_a_changed_fact() -> None:
    rules = Prompts().load("shared/note_rules")

    assert "{{length_rule}}" in rules
    assert "compactly" in LENGTH_RULE and "previously" in LENGTH_RULE and "{note_chars}" in LENGTH_RULE


def test_a_custom_summarize_step_in_a_whole_ingest_renders_the_length_rule(bundle: Path, llm: FakeLLM) -> None:
    def summarize(ctx: StepContext, source: Source) -> Candidate:
        draft = ctx.ask_json(SUMMARIZE, NoteDraft, source_title="Memo", source_resource="(none)", source_text=source.text)
        return Candidate(draft.title, draft.summary, [], draft.body)

    steps = Steps(
        summarize=summarize,
        route=lambda ctx, root, cand: Route(root, False, []),
        match=lambda ctx, pool, cand: None,
        consolidate=lambda ctx, note, cand: False,
        relate=lambda ctx, note, others: [],
    )
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", note_chars=2_000), llm=llm, steps=steps)
    wiki.init()
    llm.add(SUMMARIZE, BODY)

    wiki.ingest("Some text.")

    assert "about 2000 characters" in llm.calls[0][1][1]["content"]


# -- the soft cap: a warning, never a cut -----------------------------------------------------------


def long_reply(chars: int) -> dict[str, Any]:
    return {"title": "Long note", "summary": "S.", "tags": [], "body": "# Overview\n\n" + "w" * chars}


def test_summarize_when_the_body_is_over_one_point_two_times_the_cap_then_it_warns_and_keeps_the_body(
    llm: FakeLLM, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = context(llm, tmp_path, Source("Short source."), llm={"read_chars": 8_000})  # the cap is 1 000
    reply = long_reply(1_400)
    llm.add(SUMMARIZE, reply)

    with caplog.at_level(logging.WARNING):
        cand = classic.summarize(ctx, Source("Short source.", title="Memo"))

    assert cand.body.strip() == reply["body"].strip()  # no truncation
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(llm.calls) == 1  # no retry


def test_summarize_when_the_body_is_within_one_point_two_times_the_cap_then_no_warning(
    llm: FakeLLM, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = context(llm, tmp_path, Source("Short source."), llm={"read_chars": 8_000})
    llm.add(SUMMARIZE, long_reply(1_100))  # over the cap of 1 000, under 1 200

    with caplog.at_level(logging.WARNING):
        classic.summarize(ctx, Source("Short source.", title="Memo"))

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


# -- the hash: what is read and how long the note is changes the decisions ------------------------


def digest(tmp_path: Path, **fields: Any) -> str:
    return decision_hash(CLASSIC, cfg_of(tmp_path, **fields), Prompts())


def test_decision_hash_changes_with_read_chars(tmp_path: Path) -> None:
    assert digest(tmp_path, llm={"read_chars": 20_000}) == digest(tmp_path, llm={"read_chars": 20_000})
    assert digest(tmp_path, llm={"read_chars": 20_000}) != digest(tmp_path, llm={"read_chars": 30_000})


def test_decision_hash_changes_with_note_chars(tmp_path: Path) -> None:
    assert digest(tmp_path, note_chars=2_000) == digest(tmp_path, note_chars=2_000)
    assert digest(tmp_path, note_chars=2_000) != digest(tmp_path, note_chars=3_000)


def test_decision_hash_follows_the_effective_values_not_the_way_they_were_set(tmp_path: Path) -> None:
    assert digest(tmp_path) == digest(tmp_path, llm={"read_chars": 100_000}, note_chars=10_000)
    assert digest(tmp_path, llm={"provider": "ollama"}) != digest(tmp_path)  # another provider default, another read_chars


def test_decision_hash_when_read_chars_moves_with_the_provider_then_it_changes_even_with_a_cap_in_place(tmp_path: Path) -> None:
    custom = {"provider": "custom", "base_url": "http://127.0.0.1:8000/v1", "model": "m"}
    assert digest(tmp_path, llm=custom, note_chars=2_000) != digest(tmp_path, llm={**custom, "read_chars": 50_000}, note_chars=2_000)


def test_llm_config_read_chars_defaults_to_none() -> None:
    assert LLMConfig().read_chars is None
