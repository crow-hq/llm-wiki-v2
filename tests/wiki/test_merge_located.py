# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Round 7, blind from spec-fase4c.md: `merge_mode="located"`, a revision merged unit by unit (changes, units, location, writer, apply)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import NoteDraft, Source, StepContext, Wiki, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.bundle.tree import Note
from llmw2.config import LLMConfig
from llmw2.models.classifier import ChoiceAnswer
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_merge_edits import (
    EDITS,
    MERGE,
    MERGE_SOURCE,
    OLD_COPY,
    READ,
    calls_of,
    cfg_of,
    hash_of,
    long_raw,
    paragraph,
    reply,
    run_merge,
)
from tests.wiki.test_merge_edits_routing import LIMIT, SHORT, body_of, rewrite_logs, rewrite_reply, share_of
from tests.wiki.test_steps import MERGING

LOCATED = "librarian/merge_located"

# -- the note and its revisions -----------------------------------------------------------------------------------

STD_BODY = (
    "# Overview\n\n"
    "The limit is 30 units. Owner: Anna Rossi.\n\n"
    "## Details\n\n"
    "Sub detail words.\n\n"
    "# Pricing\n\n"
    "| Item | Price |\n| --- | --- |\n| Basic plan | 1,200 |\n| Pro plan | 3,400 |\n\n"
    "# Contacts\n\n"
    "Contact: Mario Bianchi.\n"
)
LIMIT_UNIT = "The limit is 30 units. Owner: Anna Rossi."
ROW_UNIT = "| Pro plan | 3,400 |"

S_OLD = "The service starts early. The limit is 30 units. It ends late."
S_NEW = "The service starts early. The limit is 40 units. It ends late."
T_OLD = "| Item | Price |\n| --- | --- |\n| Basic plan | 1,200 |\n| Pro plan | 3,400 |"
T_NEW = "| Item | Price |\n| --- | --- |\n| Basic plan | 1,200 |\n| Pro plan | 3,900 |"
R_OLD = "Reviewer was Paolo Neri."
R_NEW = "Reviewer is Anna Rossi."
FEE = "A setup fee of 250 applies."


FILL = 12  # filler paragraphs per run: enough for the text to be a long source (over twice note_chars)


def doc(*specials: str | None) -> str:
    """A long text: a run of filler paragraphs before each special paragraph (None: it is not there) and after the last."""
    parts: list[str] = []
    for k, special in enumerate(specials):
        parts += [paragraph(k * FILL + j) for j in range(FILL)]
        if special is not None:
            parts.append(special)
    parts += [paragraph(100 + j) for j in range(FILL)]
    return "\n\n".join(parts)


def uid(body: str, text: str) -> str:
    """The id (`u<index>`, 1-based) of the unit of `body` that reads exactly `text`."""
    return next(f"u{i}" for i, (a, b) in enumerate(classic._units(body), 1) if body[a:b] == text)


def reply_l(units: dict[str, str] | None = None, additions: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    return {
        "units": [{"id": k, "text": v} for k, v in (units or {}).items()],
        "additions": [{"section": s, "text": t} for s, t in (additions or [])],
    }


# -- a classifier to look at ---------------------------------------------------------------------------------------


@dataclass
class Call:
    state: str
    instructions: str
    options: dict[str, str]
    op: str


@dataclass
class Picker:
    """A fake classifier: picks the option whose text holds `picks[n]` ("new" picks the none-of-these option), and records."""

    picks: list[str] = field(default_factory=lambda: ["new"])
    top: float = 0.7
    second: float = 0.1
    error: Exception | None = None
    calls: list[Call] = field(default_factory=list)

    def choice(self, state: str, instructions: str, options: dict[str, str], *, op: str = "") -> ChoiceAnswer:
        self.calls.append(Call(state, instructions, dict(options), op))
        if self.error is not None:
            raise self.error
        pick = self.picks[min(len(self.calls) - 1, len(self.picks) - 1)]
        key = "new" if pick == "new" else next(k for k, text in options.items() if k != "new" and pick in text)
        others = [k for k in options if k != key]
        probabilities = {k: 0.0 for k in options}
        probabilities[key] = self.top
        if others:
            probabilities[others[0]] = self.second
        return ChoiceAnswer(key, probabilities, self.top)


# -- a merge to look at ----------------------------------------------------------------------------------------------


@dataclass
class Merged:
    draft: NoteDraft
    decided: int  # decisions the step added to the trace


Step = Callable[[StepContext, Note, Source], NoteDraft]


def ingest(bundle: Path, llm: FakeLLM, new: str, old: str, step: Step, *, mode: str = "located", body: str = STD_BODY) -> None:
    cfg = WikiConfig(bundle=bundle, mode="classic", llm=LLMConfig(read_chars=READ), note_chars=1_000, merge_mode=mode)
    wiki = Wiki(cfg, llm=llm, steps=replace(MERGING, merge=step))
    wiki.init()
    folder, _ = wiki.store.create_folder(wiki.store.load().find("/"), "finance", "Money in and out.")
    resource = wiki.store.save_raw(old, title="Old 0", resource=None)
    wiki.store.write_note(
        folder, title="Seed", summary="Seeded.", body=body, tags=["alpha"], source={"resource": resource, "title": "Old 0"}
    )
    wiki.ingest(new, title="Memo")


def run(
    bundle: Path,
    llm: FakeLLM,
    new: str,
    old: str,
    replies: list[dict[str, Any]] | None = None,
    *,
    body: str = STD_BODY,
    classifier: Any = None,
) -> Merged:
    """Merge `new`, a revision of `old` (the raw copy the note cites), into a note of `body`; `replies` are the writer's."""
    seen: list[Merged] = []

    def step(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        ctx.classifier = classifier
        before = len(ctx.decisions)
        draft = classic.merge(ctx, note, source)
        seen.append(Merged(draft, len(ctx.decisions) - before))
        return draft

    if replies:
        llm.add(LOCATED, *replies)
    ingest(bundle, llm, new, old, step, body=body)
    return seen[0]


def summary_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "located:" in r.getMessage()]


def prompt_of(llm: FakeLLM) -> str:
    [prompt] = calls_of(llm, LOCATED)
    return prompt


# == 1. config ===================================================================================================


def test_located_is_a_merge_mode() -> None:
    assert cfg_of(merge_mode="located").merge_mode == "located"


@pytest.mark.parametrize("value", ["rewrite", "edits", "located"])
def test_located_and_the_others_come_from_the_environment(tmp_path: Path, value: str) -> None:
    assert WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_MERGE_MODE": value}).merge_mode == value


def test_the_default_is_still_rewrite() -> None:
    assert cfg_of().merge_mode == "rewrite"


def test_the_hash_with_located_differs_from_rewrite_and_edits_and_is_stable() -> None:
    assert hash_of(cfg_of(merge_mode="located")) != hash_of(cfg_of())
    assert hash_of(cfg_of(merge_mode="located")) != hash_of(cfg_of(merge_mode="edits"))
    assert hash_of(cfg_of(merge_mode="located")) == hash_of(cfg_of(merge_mode="located"))
    assert hash_of(cfg_of(merge_mode="rewrite")) == hash_of(cfg_of())


# == 2. routing ==================================================================================================


def test_located_with_a_revision_takes_merge_located_not_edits(bundle: Path, llm: FakeLLM) -> None:
    old, new = doc(S_OLD), doc(S_NEW)

    draft = run(bundle, llm, new, old, [reply_l({uid(STD_BODY, LIMIT_UNIT): "The limit is 40 units. Owner: Anna Rossi."})]).draft

    assert LOCATED in llm.ops
    assert EDITS not in llm.ops and MERGE_SOURCE not in llm.ops and MERGE not in llm.ops
    assert "The limit is 40 units." in draft.body


def test_located_calls_merge_edits_never(bundle: Path, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[int] = []
    real = classic.merge_edits
    monkeypatch.setattr(classic, "merge_edits", lambda *a, **k: (called.append(1), real(*a, **k))[1])

    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()])

    assert called == []


def test_the_cited_copy_is_chosen_once_per_merge(bundle: Path, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = classic._revised_copy
    monkeypatch.setattr(classic, "_revised_copy", lambda *a, **k: (calls.append(1), real(*a, **k))[1])

    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()])

    assert calls == [1]


def test_revised_copy_gives_the_resource_the_old_body_and_the_share(bundle: Path, llm: FakeLLM) -> None:
    old, new = doc(S_OLD), doc(S_NEW)
    got: list[Any] = []

    def step(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        got.append(classic._revised_copy(ctx, note, source))
        return NoteDraft(title=note.title, summary=note.summary, tags=list(note.tags), body=note.main_body())

    ingest(bundle, llm, new, old, step)

    [(resource, body, share)] = got
    assert resource.startswith("/raw/")
    assert body.strip() == old.strip()
    assert 0.5 <= share <= 1.0


def test_revised_copy_is_none_when_nothing_cited_is_revised(bundle: Path, llm: FakeLLM) -> None:
    got: list[Any] = []

    def step(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        got.append(classic._revised_copy(ctx, note, source))
        return NoteDraft(title=note.title, summary=note.summary, tags=list(note.tags), body=note.main_body())

    ingest(bundle, llm, doc(S_NEW), OLD_COPY, step)

    assert got == [None]


def test_located_with_a_long_source_that_revises_nothing_and_a_big_note_follows_the_edits(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The limit is now 40 units. ")

    draft = run_merge(bundle, llm, raw, [reply(LIMIT)], mode="located", body=body_of(share_of(raw) + 500))

    assert EDITS in llm.ops
    assert LOCATED not in llm.ops and MERGE_SOURCE not in llm.ops
    assert "The limit is 40 units. (previously 30)" in draft.body


def test_located_with_a_long_source_that_revises_nothing_and_a_short_note_takes_the_rewrite_with_its_log(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    raw = long_raw("The limit is now 40 units. ")

    with caplog.at_level(logging.INFO):
        draft = run_merge(bundle, llm, raw, [], mode="located", body=SHORT, others=rewrite_reply(SHORT))

    assert MERGE_SOURCE in llm.ops
    assert LOCATED not in llm.ops and EDITS not in llm.ops
    assert "More." in draft.body
    assert len(rewrite_logs(caplog)) == 1


def test_located_with_a_short_source_takes_the_old_merge(bundle: Path, llm: FakeLLM) -> None:
    run_merge(
        bundle,
        llm,
        "A short memo with plain words only.",
        [],
        mode="located",
        others={MERGE: [{"title": "Seed", "summary": "S.", "tags": [], "body": SHORT + "Memo.\n"}]},
    )

    assert MERGE in llm.ops
    assert LOCATED not in llm.ops and EDITS not in llm.ops


def test_a_revision_in_edits_mode_still_takes_the_edits(bundle: Path, llm: FakeLLM) -> None:
    old, new = doc(S_OLD), doc(S_NEW)

    run_merge(bundle, llm, new, [reply(LIMIT)], mode="edits", body=STD_BODY, copies=(old,))

    assert EDITS in llm.ops
    assert LOCATED not in llm.ops


def test_a_revision_in_rewrite_mode_still_takes_the_rewrite(bundle: Path, llm: FakeLLM) -> None:
    old, new = doc(S_OLD), doc(S_NEW)

    run_merge(bundle, llm, new, [], mode="rewrite", body=STD_BODY, copies=(old,), others=rewrite_reply(STD_BODY))

    assert MERGE_SOURCE in llm.ops
    assert LOCATED not in llm.ops and EDITS not in llm.ops


def test_a_body_far_over_the_length_asked_warns_after_merge_located(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    big = "# Overview\n\nThe limit is 30 units. Owner: Anna Rossi.\n\n" + ("Some filler words in the note. " * 120) + "\n"

    with caplog.at_level(logging.WARNING):
        run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l({"u2": "The limit is 40 units. Owner: Anna Rossi."})], body=big)

    assert [r for r in caplog.records if r.levelno == logging.WARNING and "characters, over" in r.getMessage()]


# == 3. the changes: _sentence_changes ===========================================================================


def changes(old: str, new: str) -> list[tuple[str, str, str]]:
    return [(c.before, c.after, c.context) for c in classic._sentence_changes(old, new)]


def test_a_changed_sentence_inside_a_replaced_paragraph_is_the_only_change() -> None:
    old = "First paragraph.\n\nAlpha stays. The limit is 30 units. Omega stays.\n\nLast paragraph."
    new = "First paragraph.\n\nAlpha stays. The limit is 40 units. Omega stays.\n\nLast paragraph."

    assert changes(old, new) == [("The limit is 30 units.", "The limit is 40 units.", "Alpha stays. The limit is 40 units. Omega stays.")]


def test_the_context_is_empty_when_the_paragraph_is_the_change() -> None:
    assert changes("Keep.\n\nThe limit is 30 units.\n\nEnd.", "Keep.\n\nThe limit is 40 units.\n\nEnd.") == [
        ("The limit is 30 units.", "The limit is 40 units.", "")
    ]


def test_the_context_is_cut_at_600_characters() -> None:
    tail = ("word " * 200).strip()
    old = f"Keep.\n\nOpening stays. The limit is 30 units. {tail}\n\nEnd."
    new = f"Keep.\n\nOpening stays. The limit is 40 units. {tail}\n\nEnd."
    paragraph_ = f"Opening stays. The limit is 40 units. {tail}"
    assert len(paragraph_) > 600

    [(_, _, context)] = changes(old, new)

    assert len(context) == 600
    assert context == paragraph_[:600]


def test_an_inserted_paragraph_is_a_change_with_nothing_before_and_no_context() -> None:
    assert changes("One.\n\nTwo.", "One.\n\nBrand new paragraph. With two sentences.\n\nTwo.") == [
        ("", "Brand new paragraph. With two sentences.", "")
    ]


def test_each_inserted_paragraph_is_a_change_in_order() -> None:
    got = changes("One.\n\nTwo.", "One.\n\nNew first.\n\nNew second.\n\nTwo.")

    assert got == [("", "New first.", ""), ("", "New second.", "")]


def test_a_deleted_paragraph_gives_nothing() -> None:
    assert changes("One.\n\nGone paragraph.\n\nTwo.", "One.\n\nTwo.") == []


def test_a_deleted_sentence_inside_a_replaced_paragraph_gives_nothing() -> None:
    assert changes("One.\n\nAlpha. Beta gone. Gamma.\n\nTwo.", "One.\n\nAlpha. Gamma.\n\nTwo.") == []


def test_identical_texts_have_no_changes() -> None:
    assert changes("One.\n\nTwo. Three.", "One.\n\nTwo. Three.") == []


def test_separate_changes_of_one_paragraph_are_separate_and_in_the_order_of_the_new_text() -> None:
    old = "Keep.\n\nA one. B 11 here. C two. D 22 here.\n\nEnd."
    new = "Keep.\n\nA one. B 33 here. C two. D 44 here.\n\nEnd."

    got = changes(old, new)

    assert [(b, a) for b, a, _ in got] == [("B 11 here.", "B 33 here."), ("D 22 here.", "D 44 here.")]
    assert all(c == "A one. B 33 here. C two. D 44 here." for _, _, c in got)


def test_adjacent_changed_sentences_are_one_change_joined_by_a_space() -> None:
    old = "Keep.\n\nA one. B 11 here. C 22 here. D end.\n\nEnd."
    new = "Keep.\n\nA one. B 33 here. C 44 here. D end.\n\nEnd."

    assert [(b, a) for b, a, _ in changes(old, new)] == [("B 11 here. C 22 here.", "B 33 here. C 44 here.")]


def test_a_sentence_ends_at_a_question_or_exclamation_mark_followed_by_a_space() -> None:
    old = "Keep.\n\nIs it? Yes it is 10! Done now."
    new = "Keep.\n\nIs it? Yes it is 20! Done now."

    assert [(b, a) for b, a, _ in changes(old, new)] == [("Yes it is 10!", "Yes it is 20!")]


def test_a_dot_not_followed_by_whitespace_does_not_end_a_sentence() -> None:
    old = "Keep.\n\nThe ratio is 3.5 now. Next one."
    new = "Keep.\n\nThe ratio is 4.5 now. Next one."

    assert [(b, a) for b, a, _ in changes(old, new)] == [("The ratio is 3.5 now.", "The ratio is 4.5 now.")]


def test_a_changed_table_row_is_a_change_of_its_own() -> None:
    old = "Keep.\n\n" + T_OLD + "\n\nEnd."
    new = "Keep.\n\n" + T_NEW + "\n\nEnd."

    assert changes(old, new) == [("| Pro plan | 3,400 |", "| Pro plan | 3,900 |", T_NEW)]


def test_adjacent_changed_table_rows_are_joined_by_a_newline() -> None:
    old = "Keep.\n\n| a | b |\n| --- | --- |\n| x | 11 |\n| y | 22 |\n\nEnd."
    new = "Keep.\n\n| a | b |\n| --- | --- |\n| x | 33 |\n| y | 44 |\n\nEnd."

    [(before, after, _)] = changes(old, new)

    assert before == "| x | 11 |\n| y | 22 |"
    assert after == "| x | 33 |\n| y | 44 |"


def test_list_lines_are_sentences_each() -> None:
    old = "Keep.\n\nItems:\n- apple 10\n- pear 20\n- fig 30\n\nEnd."
    new = "Keep.\n\nItems:\n- apple 10\n- pear 25\n- fig 30\n\nEnd."

    assert changes(old, new) == [("- pear 20", "- pear 25", "Items:\n- apple 10\n- pear 25\n- fig 30")]


def test_a_sentence_added_to_a_replaced_paragraph_is_a_change_with_nothing_before() -> None:
    old = "Keep one.\n\nOld fact A. Old fact B.\n\nKeep two."
    new = "Keep one.\n\nNew fact A. Old fact B.\n\nExtra paragraph here. Another sentence.\n\nKeep two."

    assert changes(old, new) == [
        ("Old fact A.", "New fact A.", "New fact A. Old fact B."),
        ("", "Extra paragraph here. Another sentence.", ""),
    ]


# == 4. the units: _units ========================================================================================


def texts_of(body: str) -> list[str]:
    return [body[a:b] for a, b in classic._units(body)]


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def assert_covers(body: str) -> None:
    spans = classic._units(body)
    assert all(0 <= a < b <= len(body) for a, b in spans)
    assert all(spans[i][1] <= spans[i + 1][0] for i in range(len(spans) - 1))
    assert squash("".join(body[a:b] for a, b in spans)) == squash(body)
    assert not any(body[a:b].endswith("\n") for a, b in spans)


def test_blocks_separated_by_blank_lines_are_one_unit_each() -> None:
    assert texts_of("One line\nstill one.\n\nTwo.\n\n\nThree.\n") == ["One line\nstill one.", "Two.", "Three."]


def test_a_blank_line_may_hold_spaces() -> None:
    assert texts_of("One.\n \t \nTwo.") == ["One.", "Two."]


def test_the_spans_exclude_the_trailing_newline() -> None:
    body = "One.\n\nTwo.\n"
    assert classic._units(body) == [(0, 4), (6, 10)]


def test_an_empty_body_has_no_units() -> None:
    assert classic._units("") == []
    assert classic._units("\n\n  \n") == []


def test_the_rows_of_a_table_are_a_unit_each() -> None:
    assert texts_of("| a | b |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |") == ["| a | b |", "| --- | --- |", "| 1 | 2 |", "| 3 | 4 |"]


def test_a_line_of_prose_before_the_rows_is_a_unit_too() -> None:
    assert texts_of("Prices:\n| a | b |\n| 1 | 2 |") == ["Prices:", "| a | b |", "| 1 | 2 |"]


def test_list_items_are_a_unit_each() -> None:
    assert texts_of("Items:\n- one\n* two\n+ three") == ["Items:", "- one", "* two", "+ three"]
    assert texts_of("Steps:\n1. first\n2) second") == ["Steps:", "1. first", "2) second"]


def test_a_block_with_a_line_that_is_neither_a_row_nor_an_item_stays_one_unit() -> None:
    assert texts_of("Text\n| a |\nplain line") == ["Text\n| a |\nplain line"]
    assert texts_of("Text\n- one\nplain line") == ["Text\n- one\nplain line"]


def test_a_heading_line_is_its_own_unit_even_inside_a_block() -> None:
    assert texts_of("# Title\nBody line\nmore body") == ["# Title", "Body line\nmore body"]
    assert texts_of("Intro\n## Sub\nBody") == ["Intro", "## Sub", "Body"]


def test_a_heading_alone_is_a_unit() -> None:
    assert texts_of("# One\n\nText.\n\n## Two\n\nMore.") == ["# One", "Text.", "## Two", "More."]


def test_the_units_of_the_standard_body() -> None:
    assert texts_of(STD_BODY) == [
        "# Overview",
        LIMIT_UNIT,
        "## Details",
        "Sub detail words.",
        "# Pricing",
        "| Item | Price |",
        "| --- | --- |",
        "| Basic plan | 1,200 |",
        ROW_UNIT,
        "# Contacts",
        "Contact: Mario Bianchi.",
    ]


@pytest.mark.parametrize(
    "body",
    [
        STD_BODY,
        "# T\nBody\n\n\n- a\n- b\n\nTail  \n\n   \nEnd",
        "Intro\n## Sub\nBody\n| a | b |\n| 1 | 2 |\n\n1. x\n2. y\n",
        "\n\n  Leading blank lines.\n\nAnd more.\n\n\n",
    ],
)
def test_the_units_cover_every_non_blank_character_in_order_without_overlap(body: str) -> None:
    assert_covers(body)


# == 5. location =================================================================================================

AMBIG_BODY = (
    "# Overview\n\n"
    "The limit is 30 units. Owner: Anna Rossi.\n\n"
    "# Limit 30 units\n\n"
    "# Archive\n\n"
    "An older limit was 30 units in the archive. Owner: Luca Verdi.\n\n"
    "# Contacts\n\n"
    "Contact: Mario Bianchi.\n"
)


def test_a_unit_that_alone_holds_the_removed_values_is_located_by_code_without_the_classifier(bundle: Path, llm: FakeLLM) -> None:
    picker = Picker()

    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()], classifier=picker)

    assert picker.calls == []
    prompt = prompt_of(llm)
    assert f"[{uid(STD_BODY, LIMIT_UNIT)}]\n{LIMIT_UNIT}\n" in prompt


def test_an_ambiguous_change_asks_the_classifier_with_the_candidates_and_a_none_option(bundle: Path, llm: FakeLLM) -> None:
    picker = Picker(picks=["Anna Rossi"])

    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()], body=AMBIG_BODY, classifier=picker)

    [call] = picker.calls
    assert call.op == "merge/locate"
    assert call.state == "A change to a document.\nBEFORE:\nThe limit is 30 units.\nAFTER:\nThe limit is 40 units."
    assert call.instructions == "Which paragraph of the note states what this change updates?"
    assert list(call.options) == ["u1", "u2", "new"]  # the heading that holds 30 is not a candidate
    assert call.options["new"] == "None of these: the change adds a fact the note does not state"
    assert sorted([call.options["u1"], call.options["u2"]]) == sorted(
        ["The limit is 30 units. Owner: Anna Rossi.", "An older limit was 30 units in the archive. Owner: Luca Verdi."]
    )


@pytest.mark.parametrize(("pick", "other"), [("Anna Rossi", "Luca Verdi"), ("Luca Verdi", "Anna Rossi")])
def test_the_unit_the_classifier_picks_is_the_one_sent_to_the_writer(bundle: Path, llm: FakeLLM, pick: str, other: str) -> None:
    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()], body=AMBIG_BODY, classifier=Picker(picks=[pick]))

    prompt = prompt_of(llm)
    assert pick in prompt
    assert other not in prompt


def test_long_candidates_are_cut_at_600_characters_in_the_options(bundle: Path, llm: FakeLLM) -> None:
    body = (
        "# Overview\n\nThe limit is 30 units. Owner: Anna Rossi. " + ("padding words " * 100) + "\n\n"
        "# Archive\n\nAn older limit was 30 units. Owner: Luca Verdi.\n"
    )
    picker = Picker(picks=["Luca Verdi"])

    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()], body=body, classifier=picker)

    [call] = picker.calls
    assert all(len(text) <= 600 for key, text in call.options.items() if key != "new")
    assert any(len(text) == 600 for key, text in call.options.items() if key != "new")


def many_items_body() -> str:
    items = [f"Item {i:02d} caps the limit at 30 units here." for i in range(1, 21)]
    items[16] = "The limit is 30 units in item 17."  # shares words with the change: the best candidate
    return "# Limit 30 units\n\n" + "\n\n".join(items) + "\n"


def test_at_most_15_candidates_best_first_ties_by_order(bundle: Path, llm: FakeLLM) -> None:
    picker = Picker(picks=["new"])

    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()], body=many_items_body(), classifier=picker)

    [call] = picker.calls
    assert list(call.options) == [*[f"u{i}" for i in range(1, 16)], "new"]
    assert call.options["u1"] == "The limit is 30 units in item 17."
    shown = " ".join(call.options.values())
    assert "Item 01 " in shown and "Item 14 " in shown
    assert "Item 15 " not in shown and "Item 20 " not in shown
    assert not any(text.startswith("#") for text in call.options.values())


@pytest.mark.parametrize(("top", "second"), [(0.55, 0.46), (0.5, 0.45)])
def test_a_margin_under_0_1_means_not_located(bundle: Path, llm: FakeLLM, top: float, second: float) -> None:
    picker = Picker(picks=["Anna Rossi"], top=top, second=second)

    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()], body=AMBIG_BODY, classifier=picker)

    prompt = prompt_of(llm)
    assert "Anna Rossi" not in prompt and "Luca Verdi" not in prompt  # no unit sent: the change is a new fact
    assert "- The limit is 40 units." in prompt


def test_a_clear_margin_locates(bundle: Path, llm: FakeLLM) -> None:
    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()], body=AMBIG_BODY, classifier=Picker(picks=["Anna Rossi"], top=0.7, second=0.3))

    assert "Anna Rossi" in prompt_of(llm)


def test_the_none_option_means_not_located_and_the_change_is_a_new_fact(bundle: Path, llm: FakeLLM) -> None:
    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()], body=AMBIG_BODY, classifier=Picker(picks=["new"]))

    prompt = prompt_of(llm)
    assert "Anna Rossi" not in prompt and "Luca Verdi" not in prompt
    assert "- The limit is 40 units." in prompt


FALL_BODY = (
    "# Archive\n\nOld note: 30 units were once the cap.\n\n# Overview\n\nThe limit is 30 units for the whole team. Owner: Anna Rossi.\n"
)
FALL_OLD = "The limit is 30 units for the whole team."
FALL_NEW = "The limit is 40 units for the whole team."


@pytest.mark.parametrize("how", ["no classifier", "flag off", "classifier fails"])
def test_without_a_usable_classifier_the_best_candidate_holding_a_removed_value_is_taken(
    bundle: Path, llm: FakeLLM, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    picker = Picker(error=RuntimeError("down")) if how == "classifier fails" else Picker()
    if how == "flag off":
        monkeypatch.setattr(classic, "LOCATE_BY_CLASSIFIER", False)

    run(bundle, llm, doc(FALL_NEW), doc(FALL_OLD), [reply_l()], body=FALL_BODY, classifier=None if how == "no classifier" else picker)

    prompt = prompt_of(llm)
    assert "Owner: Anna Rossi." in prompt
    assert "Old note" not in prompt
    if how == "flag off":
        assert picker.calls == []
    if how == "classifier fails":
        assert len(picker.calls) >= 1


def test_among_equal_candidates_the_earlier_unit_wins_the_fallback(bundle: Path, llm: FakeLLM) -> None:
    body = "# One\n\nThe limit is 30 units.\n\n# Two\n\nThe limit is 30 units.\n"

    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()], body=body, classifier=None)

    prompt = prompt_of(llm)
    assert f"[{uid(body, 'The limit is 30 units.')}]\n" in prompt  # the first such unit
    assert prompt.count("The limit is 30 units.\nChanges:") == 1


def test_a_failing_classifier_is_logged_at_warning_once_per_merge(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    old = doc("The first limit is 30 units here. Then more.", "The second cap is 55 units there. Then more.")
    new = doc("The first limit is 40 units here. Then more.", "The second cap is 66 units there. Then more.")
    body = (
        "# One\n\nThe first limit is 30 units here. The second cap is 55 units there.\n\n"
        "# Two\n\nThe first limit was 30 units here. The second cap was 55 units there.\n"
    )
    picker = Picker(error=RuntimeError("down"))

    with caplog.at_level(logging.INFO):
        run(bundle, llm, new, old, [reply_l()], body=body, classifier=picker)

    warned = [r for r in caplog.records if r.levelno == logging.WARNING and "characters, over" not in r.getMessage()]
    assert len(warned) == 1
    assert LOCATED in llm.ops  # the merge went on with the code fallback


def test_the_classifier_may_locate_a_unit_that_holds_no_removed_value(bundle: Path, llm: FakeLLM) -> None:
    body = "# Caps\n\nThe cap is 50 units today. Owner: Anna Rossi.\n\n# Contacts\n\nContact: Mario Bianchi.\n"
    old, new = doc("The cap is 77 units today."), doc("The cap is 88 units today.")
    picker = Picker(picks=["The cap is 50"])

    run(
        bundle,
        llm,
        new,
        old,
        [reply_l({uid(body, "The cap is 50 units today. Owner: Anna Rossi."): "The cap is 88 units today. Owner: Anna Rossi."})],
        body=body,
        classifier=picker,
    )

    assert len(picker.calls) == 1
    assert "The cap is 50 units today." in prompt_of(llm)


def test_without_a_classifier_a_candidate_holding_no_removed_value_is_not_located(bundle: Path, llm: FakeLLM) -> None:
    body = "# Caps\n\nThe cap is 50 units today. Owner: Anna Rossi.\n\n# Contacts\n\nContact: Mario Bianchi.\n"
    old, new = doc("The cap is 77 units today."), doc("The cap is 88 units today.")

    result = run(bundle, llm, new, old, [reply_l(additions=[("# Caps", "The cap is 88 units today.")])], body=body)

    prompt = prompt_of(llm)
    assert "- The cap is 88 units today." in prompt
    assert "The cap is 50 units today." not in prompt
    assert "The cap is 88 units today." in result.draft.body


def test_a_change_with_no_removed_value_and_no_classifier_is_not_located(bundle: Path, llm: FakeLLM) -> None:
    old, new = doc("Some intro here. Sub detail words."), doc("Some intro here. Sub detail words and 77 boxes.")

    run(bundle, llm, new, old, [reply_l()])

    prompt = prompt_of(llm)
    assert "- Sub detail words and 77 boxes." in prompt
    assert f"[{uid(STD_BODY, 'Sub detail words.')}]" not in prompt


def test_a_change_with_no_removed_value_is_asked_to_the_classifier_when_there_are_candidates(bundle: Path, llm: FakeLLM) -> None:
    old, new = doc("Some intro here. Sub detail words."), doc("Some intro here. Sub detail words and 77 boxes.")
    picker = Picker(picks=["Sub detail words."])

    result = run(
        bundle, llm, new, old, [reply_l({uid(STD_BODY, "Sub detail words."): "Sub detail words and 77 boxes."})], classifier=picker
    )

    assert [c.op for c in picker.calls] == ["merge/locate"]
    assert "Sub detail words and 77 boxes." in result.draft.body


def test_a_not_located_change_with_values_the_note_lacks_is_a_new_fact_with_its_context(bundle: Path, llm: FakeLLM) -> None:
    body = "# Overview\n\nOwner: Anna Rossi.\n"

    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()], body=body)

    prompt = prompt_of(llm)
    assert "- The limit is 40 units." in prompt
    assert "(in: The service starts early. The limit is 40 units. It ends late.)" in prompt


def test_a_not_located_change_with_nothing_new_for_the_note_is_dropped_and_asks_nothing(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    picker = Picker()

    with caplog.at_level(logging.INFO):
        result = run(bundle, llm, doc(R_NEW), doc(R_OLD), [], classifier=picker)

    assert llm.calls == []
    assert picker.calls == []
    assert result.draft.body.strip() == STD_BODY.strip()
    [line] = summary_lines(caplog)
    assert "1 changes (0 by code, 0 by classifier, 0 additions, 1 dropped)" in line.getMessage()


def test_inserted_paragraphs_are_new_facts_in_source_order_and_are_not_located(bundle: Path, llm: FakeLLM) -> None:
    old, new = doc(None, None), doc("Second fee of 300 applies.", FEE)
    picker = Picker()

    run(bundle, llm, new, old, [reply_l()], classifier=picker)

    prompt = prompt_of(llm)
    assert picker.calls == []
    assert prompt.index("- Second fee of 300 applies.") < prompt.index("- " + FEE)
    assert "(in:" not in prompt


# == 6. the writer's prompt ======================================================================================


def test_the_prompt_has_only_the_affected_units_with_their_ids_and_changes(bundle: Path, llm: FakeLLM) -> None:
    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()])

    prompt = prompt_of(llm)
    unit = (
        f"[{uid(STD_BODY, LIMIT_UNIT)}]\n{LIMIT_UNIT}\nChanges:\n"
        f"- BEFORE: The limit is 30 units.\n  AFTER: The limit is 40 units.\n  (in: {S_NEW})"
    )
    assert unit in prompt
    for untouched in ("Sub detail words.", "Contact: Mario Bianchi.", "| Basic plan | 1,200 |"):
        assert untouched not in prompt


def test_the_prompt_has_the_title_the_summary_and_the_outline(bundle: Path, llm: FakeLLM) -> None:
    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()])

    prompt = prompt_of(llm)
    assert "Seed" in prompt and "Seeded." in prompt
    for heading in ("# Overview", "## Details", "# Pricing", "# Contacts"):
        assert heading in prompt


def test_a_body_without_headings_has_an_outline_that_says_so(bundle: Path, llm: FakeLLM) -> None:
    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()], body="The limit is 30 units. Owner: Anna Rossi.\n\nSub detail words.\n")

    assert "(no headings)" in prompt_of(llm)


def test_only_units_means_no_facts_and_only_facts_means_no_units(bundle: Path, llm: FakeLLM) -> None:
    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()])
    only_units = prompt_of(llm)
    llm.calls.clear()
    llm.replies.clear()

    run_ = Path(bundle.parent / "again")
    run(run_, llm, doc(None, FEE), doc(None, None), [reply_l()])
    only_facts = prompt_of(llm)

    assert "(none)" in only_units and "- BEFORE:" in only_units
    assert "(none)" in only_facts and "- BEFORE:" not in only_facts
    assert "- " + FEE in only_facts


def test_a_unit_with_two_changes_is_sent_once_with_both(bundle: Path, llm: FakeLLM) -> None:
    old = doc("The limit is 30 units. The rest is stable. Owner: Anna Rossi.")
    new = doc("The limit is 40 units. The rest is stable. Owner: Luca Verdi.")

    run(bundle, llm, new, old, [reply_l()])

    prompt = prompt_of(llm)
    assert prompt.count(f"[{uid(STD_BODY, LIMIT_UNIT)}]\n") == 1
    assert prompt.count("- BEFORE:") == 2
    assert prompt.index("The limit is 40 units.") < prompt.index("Owner: Luca Verdi.")


def test_a_big_revision_is_written_in_batches_in_note_order_each_unit_once(bundle: Path, llm: FakeLLM) -> None:
    n = 20

    def unit(i: int, code: int) -> str:
        return f"Unit {i:02d} says the code is AB-{code + i} today. " + " ".join(paragraph(100 + i * 8 + k) for k in range(8))

    body = "# Codes\n\n" + "\n\n".join(unit(i, 1000) for i in range(n)) + "\n"
    old, new = "\n\n".join(unit(i, 1000) for i in range(n)), "\n\n".join(unit(i, 2000) for i in range(n))

    run(bundle, llm, new, old, [reply_l() for _ in range(12)], body=body)

    prompts = calls_of(llm, LOCATED)
    assert 2 <= len(prompts) <= 12
    for i in range(n):
        assert sum(f"AB-{1000 + i} today" in p for p in prompts) == 1  # each unit goes in one batch
    first_seen = [next(k for k, p in enumerate(prompts) if f"AB-{1000 + i} today" in p) for i in range(n)]
    assert first_seen == sorted(first_seen)  # note order, batch after batch


# == 7. applying =================================================================================================


def test_a_rewritten_unit_replaces_only_its_span_and_lost_values_get_previously_in_the_right_place(bundle: Path, llm: FakeLLM) -> None:
    old, new = doc(S_OLD, T_OLD), doc(S_NEW, T_NEW)
    units = {
        uid(STD_BODY, LIMIT_UNIT): "The limit is 40 units. Owner: Anna Rossi.",
        uid(STD_BODY, ROW_UNIT): "| Pro plan | 3,900 |",
    }

    draft = run(bundle, llm, new, old, [reply_l(units)]).draft

    expected = STD_BODY.replace(LIMIT_UNIT, "The limit is 40 units. Owner: Anna Rossi. (previously 30)").replace(
        ROW_UNIT, "| Pro plan | 3,900 (previously 3,400) |"
    )
    assert draft.body.strip() == expected.strip()


def test_title_summary_and_tags_stay_the_notes(bundle: Path, llm: FakeLLM) -> None:
    draft = run(
        bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l({uid(STD_BODY, LIMIT_UNIT): "The limit is 40 units. Owner: Anna Rossi."})]
    ).draft

    assert (draft.title, draft.summary, draft.tags) == ("Seed", "Seeded.", ["alpha"])


def test_surrounding_newlines_of_a_returned_unit_are_stripped(bundle: Path, llm: FakeLLM) -> None:
    unit = uid(STD_BODY, LIMIT_UNIT)

    draft = run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l({unit: "\n\nThe limit is 40 units. Owner: Anna Rossi.\n\n"})]).draft

    assert draft.body.strip() == STD_BODY.replace(LIMIT_UNIT, "The limit is 40 units. Owner: Anna Rossi. (previously 30)").strip()


def test_a_unit_that_is_not_returned_stays_as_it_is(bundle: Path, llm: FakeLLM) -> None:
    result = run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()])

    assert result.draft.body.strip() == STD_BODY.strip()


def test_a_returned_unit_that_invents_a_value_is_rejected_and_the_unit_kept(bundle: Path, llm: FakeLLM) -> None:
    draft = run(
        bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l({uid(STD_BODY, LIMIT_UNIT): "The limit is 99 units. Owner: Anna Rossi."})]
    ).draft

    assert draft.body.strip() == STD_BODY.strip()


def test_a_returned_unit_that_invents_a_name_is_rejected(bundle: Path, llm: FakeLLM) -> None:
    draft = run(
        bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l({uid(STD_BODY, LIMIT_UNIT): "The limit is 40 units. Owner: Paolo Neri."})]
    ).draft

    assert "Paolo Neri" not in draft.body
    assert LIMIT_UNIT in draft.body


def test_a_value_of_the_context_of_a_change_is_not_invented(bundle: Path, llm: FakeLLM) -> None:
    old = doc("The service starts on day 12. The limit is 30 units. It ends late.")
    new = doc("The service starts on day 12. The limit is 40 units. It ends late.")

    draft = run(bundle, llm, new, old, [reply_l({uid(STD_BODY, LIMIT_UNIT): "The limit is 40 units on day 12. Owner: Anna Rossi."})]).draft

    assert "The limit is 40 units on day 12. Owner: Anna Rossi. (previously 30)" in draft.body


def test_a_value_the_unit_already_had_may_be_kept(bundle: Path, llm: FakeLLM) -> None:
    draft = run(
        bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l({uid(STD_BODY, LIMIT_UNIT): "Owner: Anna Rossi. The limit is 40 units."})]
    ).draft

    assert "Owner: Anna Rossi. The limit is 40 units. (previously 30)" in draft.body


def test_an_empty_text_is_rejected_and_the_unit_kept(bundle: Path, llm: FakeLLM) -> None:
    draft = run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l({uid(STD_BODY, LIMIT_UNIT): "  \n "})]).draft

    assert draft.body.strip() == STD_BODY.strip()


def test_an_unknown_id_and_an_id_not_sent_are_ignored(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    units = {"u99": "Whatever 12.", uid(STD_BODY, "Contact: Mario Bianchi."): "Contact: Mario Bianchi, changed."}

    with caplog.at_level(logging.INFO):
        draft = run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l(units)]).draft

    assert draft.body.strip() == STD_BODY.strip()
    [line] = summary_lines(caplog)
    assert "unknown 2, dropped 0)" in line.getMessage()


def test_an_id_returned_twice_is_applied_once(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    unit = uid(STD_BODY, LIMIT_UNIT)
    writer = {
        "units": [
            {"id": unit, "text": "The limit is 40 units. Owner: Anna Rossi."},
            {"id": unit, "text": "The limit is 40 units. Owner: Anna Rossi. Extra."},
        ]
    }

    with caplog.at_level(logging.INFO):
        draft = run(bundle, llm, doc(S_NEW), doc(S_OLD), [writer]).draft

    assert "Extra." not in draft.body
    assert "The limit is 40 units. Owner: Anna Rossi. (previously 30)" in draft.body
    [line] = summary_lines(caplog)
    assert "unknown 1, dropped 0)" in line.getMessage()


def test_an_addition_goes_at_the_end_of_its_section(bundle: Path, llm: FakeLLM) -> None:
    draft = run(bundle, llm, doc(None, FEE), doc(None, None), [reply_l(additions=[("# Pricing", "Setup fee is 250.")])]).draft

    body = draft.body
    assert body.index(ROW_UNIT) < body.index("Setup fee is 250.") < body.index("# Contacts")


def test_additions_are_applied_in_order_and_an_unknown_section_becomes_a_heading(bundle: Path, llm: FakeLLM) -> None:
    old, new = doc(None, None), doc("The warranty lasts 24 months.", FEE)
    additions = [("Warranty", "Warranty lasts 24 months."), ("# Pricing", "Setup fee is 250.")]

    draft = run(bundle, llm, new, old, [reply_l(additions=additions)]).draft

    body = draft.body
    assert body.index(ROW_UNIT) < body.index("Setup fee is 250.") < body.index("# Contacts") < body.index("# Warranty")
    assert "# Warranty\nWarranty lasts 24 months." in body


def test_an_addition_with_a_value_no_fact_has_is_rejected(bundle: Path, llm: FakeLLM) -> None:
    draft = run(bundle, llm, doc(None, FEE), doc(None, None), [reply_l(additions=[("# Pricing", "Setup fee is 999.")])]).draft

    assert "999" not in draft.body
    assert draft.body.strip() == STD_BODY.strip()


def test_an_addition_may_use_a_value_of_the_context_of_its_fact(bundle: Path, llm: FakeLLM) -> None:
    body = "# Overview\n\nOwner: Anna Rossi.\n"
    old = doc("The service starts on day 12. The limit is 30 units. It ends late.")
    new = doc("The service starts on day 12. The limit is 40 units. It ends late.")

    draft = run(bundle, llm, new, old, [reply_l(additions=[("# Overview", "The limit is 40 units from day 12.")])], body=body).draft

    assert "The limit is 40 units from day 12." in draft.body


def test_units_and_additions_of_one_reply_are_applied_together(bundle: Path, llm: FakeLLM) -> None:
    old, new = doc(S_OLD, None), doc(S_NEW, FEE)
    writer = reply_l({uid(STD_BODY, LIMIT_UNIT): "The limit is 40 units. Owner: Anna Rossi."}, [("# Pricing", "Setup fee is 250.")])

    draft = run(bundle, llm, new, old, [writer]).draft

    assert "The limit is 40 units. Owner: Anna Rossi. (previously 30)" in draft.body
    assert draft.body.index(ROW_UNIT) < draft.body.index("Setup fee is 250.") < draft.body.index("# Contacts")


def test_several_replaced_units_do_not_disturb_each_other(bundle: Path, llm: FakeLLM) -> None:
    old = doc("The limit is 30 units here.", "Contact is Mario Bianchi.")
    new = doc("The limit is 40 units here.", "Contact is Luca Verdi.")
    body = "# A\n\nThe limit is 30 units here.\n\n# B\n\nContact is Mario Bianchi.\n\n# C\n\nTail words.\n"
    units = {
        uid(body, "The limit is 30 units here."): "The limit is 40 units here.",
        uid(body, "Contact is Mario Bianchi."): "Contact is Luca Verdi.",
    }

    draft = run(bundle, llm, new, old, [reply_l(units)], body=body).draft

    assert draft.body.strip() == (
        "# A\n\nThe limit is 40 units here. (previously 30)\n\n"
        "# B\n\nContact is Luca Verdi. (previously Mario Bianchi)\n\n"
        "# C\n\nTail words."
    )


# == 8. no changes, decisions, logs ==============================================================================


def test_a_revision_that_only_deletes_leaves_the_note_alone_without_asking_the_model(bundle: Path, llm: FakeLLM) -> None:
    picker = Picker()

    result = run(bundle, llm, doc(None, T_OLD), doc(S_OLD, T_OLD), classifier=picker)

    assert llm.calls == []
    assert picker.calls == []
    assert result.draft.body.strip() == STD_BODY.strip()
    assert (result.draft.title, result.draft.summary, result.draft.tags) == ("Seed", "Seeded.", ["alpha"])


def test_a_deleted_sentence_only_leaves_the_note_alone_without_asking_the_model(bundle: Path, llm: FakeLLM) -> None:
    result = run(bundle, llm, doc("The service starts early. It ends late."), doc(S_OLD))

    assert llm.calls == []
    assert result.draft.body.strip() == STD_BODY.strip()


def test_no_decision_is_recorded(bundle: Path, llm: FakeLLM) -> None:
    result = run(
        bundle,
        llm,
        doc(S_NEW, FEE),
        doc(S_OLD, None),
        [reply_l({uid(STD_BODY, LIMIT_UNIT): "The limit is 40 units. Owner: Anna Rossi."})],
        classifier=Picker(),
    )

    assert result.decided == 0


SUMMARY = re.compile(
    r"merge: \S+ located: (\d+) changes \((\d+) by code, (\d+) by classifier, (\d+) additions, (\d+) dropped\), "
    r"(\d+) units rewritten, (\d+) additions applied, (\d+) rejected \(invented (\d+), empty (\d+), unknown (\d+), dropped (\d+)\)"
)


def test_the_summary_line_counts_and_is_an_info_when_nothing_was_rejected(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    old, new = doc(S_OLD, R_OLD, None), doc(S_NEW, R_NEW, FEE)
    writer = reply_l({uid(STD_BODY, LIMIT_UNIT): "The limit is 40 units. Owner: Anna Rossi."}, [("# Pricing", "Setup fee is 250.")])

    with caplog.at_level(logging.INFO):
        run(bundle, llm, new, old, [writer])

    [line] = summary_lines(caplog)
    assert line.levelno == logging.INFO
    m = SUMMARY.search(line.getMessage())
    assert m
    assert [int(x) for x in m.groups()] == [3, 1, 0, 1, 1, 1, 1, 0, 0, 0, 0, 0]
    assert "finance/seed.md" in line.getMessage()


def test_the_summary_line_is_a_warning_when_something_was_rejected(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    writer = reply_l(
        {uid(STD_BODY, LIMIT_UNIT): "The limit is 99 units. Owner: Anna Rossi.", uid(STD_BODY, ROW_UNIT): " ", "u98": "x"},
    )

    with caplog.at_level(logging.INFO):
        run(bundle, llm, doc(S_NEW, T_NEW), doc(S_OLD, T_OLD), [writer])

    [line] = summary_lines(caplog)
    assert line.levelno == logging.WARNING
    m = SUMMARY.search(line.getMessage())
    assert m
    assert [int(x) for x in m.groups()[5:]] == [0, 0, 3, 1, 1, 1, 0]


def test_a_by_classifier_location_is_counted(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()], body=AMBIG_BODY, classifier=Picker(picks=["Anna Rossi"]))

    [line] = summary_lines(caplog)
    m = SUMMARY.search(line.getMessage())
    assert m
    assert [int(x) for x in m.groups()[:5]] == [1, 0, 1, 0, 0]


def test_a_rejected_unit_is_logged_at_debug_with_reason_note_and_the_first_120_characters(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    text = "The limit is 99 units. Owner: Anna Rossi. " + "plain words " * 20

    with caplog.at_level(logging.DEBUG, logger="llmw2"):
        run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l({uid(STD_BODY, LIMIT_UNIT): text})])

    [message] = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG and "rejected for" in r.getMessage()]
    assert "invented" in message
    assert "finance/seed.md" in message
    assert repr(text[:120]) in message
    assert text[:121] not in message


def test_a_rejected_addition_is_logged_at_debug_too(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="llmw2"):
        run(bundle, llm, doc(None, FEE), doc(None, None), [reply_l(additions=[("# Pricing", "Setup fee is 999.")])])

    [message] = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG and "rejected for" in r.getMessage()]
    assert "invented" in message
    assert repr("Setup fee is 999.") in message
