# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The note-length rule only where a note may need compacting (spec-giro2-length-rule)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Source, StepContext, WikiConfig
from llmw2.agents import steps as steps_module
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import CLASSIC, decision_hash
from llmw2.config import ClassifierConfig, LLMConfig
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_merge_note import INCOMING, LLM_MERGING, MERGED, RAW, wiki_with
from tests.wiki.test_summarize_long import COMBINE, DRAFT, SUMMARIZE, RecordingLLM, document, notes_for

NOTE = 1_000  # note_chars, and effective_note_chars with this read_chars
RULE = (
    f"- Length: the body is at most about {NOTE} characters. When the subject needs more, first write more compactly "
    "(tables, lists, no repetition), then drop marginal details; never drop a fact that changed or one marked as previously."
)
# the other lines of note_rules.md, as of HEAD before the change, in order; the Length rule goes between Faithful and Dense
BEFORE = ["- One subject per note.", "- Title:", "- Summary:", "- Body:", "- Faithful:"]
AFTER = ["- Dense:", "- Language:", "- Tags:"]


def context(llm: Any, source: Source | None = None, **fields: Any) -> StepContext:
    llm_cfg = LLMConfig(read_chars=20_000)
    classifier = ClassifierConfig(model="fake/jev", api_key="fake-key")
    cfg = WikiConfig(bundle=Path("wiki"), mode="classic", llm=llm_cfg, classifier=classifier, note_chars=NOTE, **fields)
    return StepContext(llm, None, Prompts(), cfg, "librarian/system", source)


def one_call_prompt(tracker: Any, chars: int) -> str:
    llm = RecordingLLM(tracker, summarize=DRAFT)
    source = Source("a" * chars, title="Memo")
    classic.summarize(context(llm, source), source)
    assert llm.ops == [SUMMARIZE]
    return llm.prompts(SUMMARIZE)[0]


def has_rule(prompt: str) -> bool:
    return "- Length:" in prompt


def rule_lines(prompt: str) -> list[str]:
    return [line for line in prompt.splitlines() if line.startswith("- ")]


def test_constants() -> None:
    assert classic.SUMMARY_VERSION == 7
    assert not hasattr(classic, "LENGTH_RULE_FROM")  # round 5: the one-call summarize never has the rule


def test_length_rule_constant_is_the_line_with_a_format_field() -> None:
    rule = getattr(steps_module, "LENGTH_RULE", None) or getattr(classic, "LENGTH_RULE")  # noqa: B009
    assert "{note_chars}" in rule
    assert rule.format(note_chars=NOTE).strip() == RULE


def test_summarize_when_the_source_is_short_then_the_prompt_has_no_length_rule(tracker: Any) -> None:
    assert not has_rule(one_call_prompt(tracker, 500))


def test_summarize_when_the_source_is_exactly_two_notes_then_no_rule(tracker: Any) -> None:
    assert not has_rule(one_call_prompt(tracker, 2 * NOTE))


def test_summarize_when_the_source_is_one_char_over_two_notes_then_still_no_rule(tracker: Any) -> None:
    prompt = one_call_prompt(tracker, 2 * NOTE + 1)

    assert not has_rule(prompt)
    assert "{{" not in prompt


def test_summarize_when_the_source_is_long_but_in_one_call_then_no_rule(tracker: Any) -> None:
    assert not has_rule(one_call_prompt(tracker, 10 * NOTE))


def default_prompt(tracker: Any) -> str:
    """The summarize prompt as rendered with the default length rule (what the combine and merge prompts get)."""
    llm = RecordingLLM(tracker, summarize="ok")
    needed = Prompts().placeholders(SUMMARIZE)
    others = {key: "x" for key in needed if key not in ("length_rule", "note_chars")}
    context(llm).ask_text(SUMMARIZE, **others)
    return llm.prompts(SUMMARIZE)[0]


def test_summarize_combine_always_has_the_rule(tracker: Any) -> None:
    llm = RecordingLLM(tracker, summarize_part=notes_for, summarize_combine=DRAFT)
    cfg = WikiConfig(
        bundle=Path("wiki"), mode="classic", llm=LLMConfig(read_chars=8_000), classifier=ClassifierConfig(model="fake/jev", api_key="k")
    )
    source = Source(document(), title="Memo")

    classic.summarize(StepContext(llm, None, Prompts(), cfg, "librarian/system", source), source)

    [prompt] = llm.prompts(COMBINE)
    assert has_rule(prompt)
    assert re.search(r"- Length: the body is at most about \d+ characters\.", prompt)
    assert "{{" not in prompt


def test_merge_always_has_the_rule(bundle: Path, llm: FakeLLM) -> None:
    wiki = wiki_with(bundle, llm, LLM_MERGING)
    llm.add(SUMMARIZE, INCOMING).add("librarian/merge", MERGED)

    wiki.ingest(RAW, title="Memo")  # a short raw text: the summarize prompt has no rule, the merge prompt has it

    [(_, messages)] = [c for c in llm.calls if c[0] == "librarian/merge"]
    assert has_rule(messages[1]["content"])
    [(_, summ)] = [c for c in llm.calls if c[0] == SUMMARIZE]
    assert not has_rule(summ[1]["content"])


def test_when_the_rule_is_absent_no_empty_bullet_and_no_blank_line_where_it_was(tracker: Any) -> None:
    with_rule = one_call_prompt(tracker, 3 * NOTE)
    without = one_call_prompt(tracker, NOTE)

    lines = without.splitlines()
    assert not any(line.strip() == "-" for line in lines)
    faithful = next(i for i, line in enumerate(lines) if line.startswith("- Faithful:"))
    assert lines[faithful + 1].startswith("- Dense:")

    # the same prompt with the rule differs by exactly the rule line
    def frame(text: str) -> list[str]:
        return [line for line in text.splitlines() if not line.startswith("aaa") and line != RULE]

    assert frame(with_rule) == frame(without)


@pytest.mark.parametrize("with_rule", [False, True])
def test_the_other_note_rules_are_all_there_in_order(tracker: Any, with_rule: bool) -> None:
    prompt = default_prompt(tracker) if with_rule else one_call_prompt(tracker, NOTE)
    lines = rule_lines(prompt)
    wanted = BEFORE + (["- Length:"] if with_rule else []) + AFTER

    positions = [next(i for i, line in enumerate(lines) if line.startswith(prefix)) for prefix in wanted]

    assert positions == sorted(positions)
    assert [lines[p] for p in positions] == [line for line in lines if any(line.startswith(w) for w in wanted)]


def test_the_rule_sits_between_faithful_and_dense(tracker: Any) -> None:
    lines = rule_lines(default_prompt(tracker))
    i = next(i for i, line in enumerate(lines) if line.startswith("- Length:"))

    assert lines[i - 1].startswith("- Faithful:") and lines[i + 1].startswith("- Dense:")


def test_ask_text_when_the_caller_passes_length_rule_then_it_wins_over_the_default(tracker: Any) -> None:
    llm = RecordingLLM(tracker, summarize="ok")
    ctx = context(llm)
    needed = Prompts().placeholders(SUMMARIZE)
    assert "length_rule" in needed
    others = {key: "x" for key in needed if key not in ("length_rule", "note_chars")}

    ctx.ask_text(SUMMARIZE, length_rule="- CUSTOM LINE\n", **others)
    ctx.ask_text(SUMMARIZE, **others)

    custom, default = llm.prompts(SUMMARIZE)
    assert "- CUSTOM LINE" in custom and not has_rule(custom)
    assert RULE in default and "CUSTOM" not in default


def test_decision_hash_changes_with_the_summary_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = WikiConfig(bundle=tmp_path, mode="classic")
    now = decision_hash(CLASSIC, cfg, Prompts())

    monkeypatch.setattr(classic, "SUMMARY_VERSION", 3)

    assert decision_hash(CLASSIC, cfg, Prompts()) != now
