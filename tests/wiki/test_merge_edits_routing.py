# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Round 7, blind from spec-fase4b.md: when merge_mode="edits" takes the edits path, "(previously ...)" placement, rejected edits logged."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import NoteDraft, Source, StepContext, Wiki, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.bundle.tree import Note
from llmw2.config import LLMConfig
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_merge_edits import (
    EDITS,
    MERGE_SOURCE,
    OLD_COPY,
    READ,
    cfg_of,
    edit,
    long_raw,
    reply,
    revision,
    run_merge,
)
from tests.wiki.test_steps import MERGING

SHORT = (  # a note well under its share (the BODY of test_merge_edits is about as long as it)
    "# Overview\n\nThe limit is 30 units. Owner: Anna Rossi.\n\n"
    "## Details\n\nSub detail words.\n\n"
    "# Pricing\n\nPrice is 1,200 per month.\n\n"
    "# Contacts\n\nContact: Mario Bianchi.\n"
)
LIMIT = edit("The limit is 30 units.", "The limit is 40 units.")
NEW_LIMIT = "The limit is now 40 units. "


def share_of(raw: str) -> int:
    """What the source alone would get: the length the note must reach for the edits path."""
    return cfg_of(merge_mode="edits").target_note_chars(len(raw))


def body_of(chars: int, prefix: str = SHORT) -> str:
    """A note body that starts with `prefix` and whose main body (stripped) is `chars` characters long."""
    room = chars - len(prefix.rstrip()) - len("\n\n# Notes\n\n")
    assert room > 0
    pad = ("wordy " * (room // 6 + 2))[:room].rstrip()
    return prefix.rstrip() + "\n\n# Notes\n\n" + pad + "y" * (room - len(pad)) + "\n"


class Spy:
    """Records the calls of `merge_edits` and `_revision_diff` of a merge, and lets them through."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.edits_calls: list[tuple[int, int]] = []  # (characters of the note body, characters of the source)
        self.diff_calls = 0
        real_edits, real_diff = classic.merge_edits, classic._revision_diff

        def merge_edits(ctx: StepContext, note: Note, source: Source, *args: Any, **kw: Any) -> NoteDraft:
            self.edits_calls.append((len(note.main_body()), len(source.text)))
            return real_edits(ctx, note, source, *args, **kw)

        def revision_diff(ctx: StepContext, note: Note, source: Source) -> str | None:
            self.diff_calls += 1
            return real_diff(ctx, note, source)

        monkeypatch.setattr(classic, "merge_edits", merge_edits)
        monkeypatch.setattr(classic, "_revision_diff", revision_diff)


def rewrite_reply(body: str) -> dict[str, list[Any]]:
    return {MERGE_SOURCE: [{"title": "Seed", "summary": "S.", "tags": [], "body": body + "More.\n"}]}


def rewrite_logs(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "takes the rewrite" in r.getMessage()]


# -- 1. routing -------------------------------------------------------------------------------------------------


def test_a_short_note_and_a_source_that_revises_nothing_take_the_rewrite(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = long_raw(NEW_LIMIT)
    spy = Spy(monkeypatch)
    assert len(SHORT) < share_of(raw)

    with caplog.at_level(logging.INFO):
        draft = run_merge(bundle, llm, raw, [], body=SHORT, others=rewrite_reply(SHORT))

    assert MERGE_SOURCE in llm.ops
    assert EDITS not in llm.ops
    assert spy.edits_calls == []
    assert "More." in draft.body
    [message] = rewrite_logs(caplog)
    m = re.search(
        r"takes the rewrite: the source revises no cited copy and the note \((\d+) characters\) is under its share \((\d+)\)", message
    )
    assert m
    assert int(m.group(1)) == len(SHORT.rstrip())
    assert int(m.group(2)) == share_of(raw)
    assert "finance/seed.md" in message


def test_a_short_note_and_a_source_that_revises_a_cited_copy_take_the_edits(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    old, new = revision()
    assert len(SHORT) < share_of(new)

    with caplog.at_level(logging.INFO):
        draft = run_merge(bundle, llm, new, [reply(LIMIT)], body=SHORT, copies=(old,))

    assert EDITS in llm.ops
    assert MERGE_SOURCE not in llm.ops
    assert "The limit is 40 units. (previously 30)" in draft.body
    assert rewrite_logs(caplog) == []


def test_a_note_as_long_as_the_share_takes_the_edits_even_if_nothing_is_revised(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = long_raw(NEW_LIMIT)
    share = share_of(raw)
    spy = Spy(monkeypatch)

    with caplog.at_level(logging.INFO):
        draft = run_merge(bundle, llm, raw, [reply(LIMIT)], body=body_of(share))

    assert spy.edits_calls == [(share, len(raw))]  # exactly at the share: "at least as long"
    assert EDITS in llm.ops
    assert MERGE_SOURCE not in llm.ops
    assert "The limit is 40 units. (previously 30)" in draft.body
    assert rewrite_logs(caplog) == []


def test_a_note_longer_than_the_share_takes_the_edits(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw(NEW_LIMIT)

    run_merge(bundle, llm, raw, [reply(LIMIT)], body=body_of(share_of(raw) + 500))

    assert EDITS in llm.ops
    assert MERGE_SOURCE not in llm.ops


def test_a_note_one_character_under_the_share_takes_the_rewrite(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = long_raw(NEW_LIMIT)
    share = share_of(raw)
    body = body_of(share - 1)
    spy = Spy(monkeypatch)

    with caplog.at_level(logging.INFO):
        run_merge(bundle, llm, raw, [], body=body, others=rewrite_reply(body))

    assert MERGE_SOURCE in llm.ops
    assert EDITS not in llm.ops
    assert spy.edits_calls == []
    [message] = rewrite_logs(caplog)
    assert f"the note ({share - 1} characters) is under its share ({share})" in message


@pytest.mark.parametrize("big", [False, True])
def test_in_rewrite_mode_nothing_changes(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture, big: bool) -> None:
    old, new = revision()
    body = body_of(share_of(new) + 500) if big else SHORT

    with caplog.at_level(logging.INFO):
        draft = run_merge(bundle, llm, new, [], mode="rewrite", body=body, copies=(old,), others=rewrite_reply(body))

    assert MERGE_SOURCE in llm.ops
    assert EDITS not in llm.ops
    assert "More." in draft.body
    assert rewrite_logs(caplog) == []


def test_a_short_source_in_edits_mode_is_not_routed_by_this(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    raw = "A short memo with plain words only."

    with caplog.at_level(logging.INFO):
        run_merge(
            bundle, llm, raw, [], others={"librarian/merge": [{"title": "Seed", "summary": "S.", "tags": [], "body": SHORT + "Memo.\n"}]}
        )

    assert EDITS not in llm.ops
    assert rewrite_logs(caplog) == []


@pytest.mark.parametrize("case", ["revision", "big note", "no revision"])
def test_the_revision_diff_is_computed_once_per_merge(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    spy = Spy(monkeypatch)
    old, new = revision()
    with caplog.at_level(logging.INFO):
        if case == "revision":
            run_merge(bundle, llm, new, [reply(LIMIT)], body=SHORT, copies=(old,))
        elif case == "big note":
            raw = long_raw(NEW_LIMIT)
            run_merge(bundle, llm, raw, [reply(LIMIT)], body=body_of(share_of(raw) + 100))
        else:
            run_merge(bundle, llm, long_raw(NEW_LIMIT), [], body=SHORT, others=rewrite_reply(SHORT))

    assert spy.diff_calls == 1
    said = [r for r in caplog.records if "is a revision of" in r.getMessage()]
    assert len(said) == (1 if case == "revision" else 0)


def test_merge_edits_is_still_callable_with_three_arguments(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    old, new = revision()
    seen: list[NoteDraft] = []

    def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        seen.append(classic.merge_edits(ctx, note, source))
        return seen[-1]

    cfg = WikiConfig(bundle=bundle, mode="classic", llm=LLMConfig(read_chars=READ), note_chars=1_000, merge_mode="edits")
    wiki = Wiki(cfg, llm=llm, steps=replace(MERGING, merge=merge))
    wiki.init()
    folder, _ = wiki.store.create_folder(wiki.store.load().find("/"), "finance", "Money in and out.")
    resource = wiki.store.save_raw(old, title="Old 0", resource=None)
    wiki.store.write_note(
        folder, title="Seed", summary="Seeded.", body=SHORT, tags=["alpha"], source={"resource": resource, "title": "Old 0"}
    )
    llm.add(EDITS, reply(LIMIT))

    with caplog.at_level(logging.INFO):
        wiki.ingest(new, title="Memo")

    assert "The limit is 40 units. (previously 30)" in seen[0].body
    # called without `revised`, merge_edits finds the revision itself
    assert len([r for r in caplog.records if "is a revision of" in r.getMessage()]) == 1
    [prompt] = [m[1]["content"] for o, m in llm.calls if o == EDITS]
    assert "BEFORE:" in prompt or "ADDED:" in prompt


def test_the_old_copy_that_shares_nothing_is_not_a_revision(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw(NEW_LIMIT)

    run_merge(bundle, llm, raw, [], body=SHORT, copies=(OLD_COPY,), others=rewrite_reply(SHORT))

    assert MERGE_SOURCE in llm.ops
    assert EDITS not in llm.ops


# -- 2. where "(previously ...)" goes ---------------------------------------------------------------------------------

PRE, POST = "# Table\n\nIntro words.\n\n", "\n\nOutro words.\n"


def applied(find: str, replace_: str) -> str:
    """The text that `_apply_edit` writes in place of `find` in a body that has it once."""
    body = PRE + find + POST
    known = classic.incoming_values(find + "\n" + replace_)
    out, why = classic._apply_edit(body, classic.Edit(find=find, replace=replace_), known)
    assert why == ""
    assert out.startswith(PRE) and out.endswith(POST)
    segment = out[len(PRE) : len(out) - len(POST)]
    assert [line.count("|") for line in segment.split("\n")] == [line.count("|") for line in replace_.split("\n")]
    return segment


def test_one_plain_line_is_as_today() -> None:
    assert applied("The limit is 30 units.", "The limit is 40 units.") == "The limit is 40 units. (previously 30)"


def test_one_plain_line_with_several_values_lists_them_in_order() -> None:
    got = applied("The limit is 30 units. Owner: Anna Rossi.", "The limit is 40 units. Owner: Luca Verdi.")

    assert got == "The limit is 40 units. Owner: Luca Verdi. (previously 30, Anna Rossi)"


def test_one_plain_line_that_says_previously_gets_nothing_more() -> None:
    assert applied("The limit is 30 units.", "The limit is 40 units, previously 30.") == "The limit is 40 units, previously 30."


def test_the_example_of_the_spec_puts_each_note_in_the_cell_of_its_row() -> None:
    find = "| Brazil Cerrado, natural | 60% | body |\n| Ethiopia Guji, washed | 40% | florals |"
    replace_ = (
        "| Brazil Cerrado, natural | 50% | body |\n| Ethiopia Guji, washed | 30% | florals |\n| Colombia Huila, washed | 20% | fruit |"
    )

    assert applied(find, replace_) == (
        "| Brazil Cerrado, natural | 50% (previously 60) | body |\n"
        "| Ethiopia Guji, washed | 30% (previously 40) | florals |\n"
        "| Colombia Huila, washed | 20% | fruit |"
    )


def test_a_single_table_row_gets_the_note_inside_the_cell_not_after_the_last_bar() -> None:
    assert applied("| cerrado | 60% | body |", "| cerrado | 50% | body |") == "| cerrado | 50% (previously 60) | body |"


def test_the_value_goes_to_the_cell_that_held_it() -> None:
    assert applied("| cerrado | 60% | body 12 |", "| cerrado | 60% | body |") == "| cerrado | 60% | body (previously 12) |"


def test_several_values_of_one_cell_are_joined_in_one_note() -> None:
    assert applied("| cerrado | 60% 70% | body |", "| cerrado | 55% | body |") == "| cerrado | 55% (previously 60, 70) | body |"


def test_values_of_different_cells_get_a_note_each() -> None:
    got = applied("| cerrado | 60% | body 12 |", "| cerrado | 50% | body |")

    assert got == "| cerrado | 50% (previously 60) | body (previously 12) |"


def test_rows_with_a_different_cell_count_get_the_note_in_the_last_cell() -> None:
    assert applied("| cerrado | 60% | body |", "| cerrado | 50% | body | extra |") == "| cerrado | 50% | body | extra (previously 60) |"
    assert applied("| cerrado | 60% | body | extra |", "| cerrado | 50% | body |") == "| cerrado | 50% | body (previously 60) |"


def test_a_cell_that_already_says_previously_gets_nothing_more() -> None:
    got = applied("| cerrado | 60% | 12 |", "| cerrado | 50% previously sixty | 14 |")

    assert got == "| cerrado | 50% previously sixty | 14 (previously 12) |"


def test_a_table_row_that_says_previously_in_the_row_but_not_in_the_cell_gets_the_note_in_the_cell() -> None:
    got = applied("| cerrado | 60% | body |", "| cerrado | 50% | previously other |")

    assert got == "| cerrado | 50% (previously 60) | previously other |"


def test_a_removed_table_row_hands_its_values_to_the_last_cell_of_the_last_replace_line() -> None:
    find = "| cerrado | 60% | body |\n| guji | 40% | florals |"
    replace_ = "| cerrado | 50% | body |"

    assert applied(find, replace_) == "| cerrado | 50% (previously 60) | body (previously 40) |"


def test_plain_lines_are_paired_one_to_one() -> None:
    got = applied("Limit: 30 units\nOwner: Anna Rossi", "Limit: 40 units\nOwner: Luca Verdi")

    assert got == "Limit: 40 units (previously 30)\nOwner: Luca Verdi (previously Anna Rossi)"


def test_an_unchanged_line_gets_nothing_and_a_changed_one_gets_its_note() -> None:
    got = applied("Intro words\nLimit: 30 units\nTail words", "Intro words\nLimit: 40 units\nTail words")

    assert got == "Intro words\nLimit: 40 units (previously 30)\nTail words"


def test_an_inserted_line_does_not_take_the_note_of_the_next_one() -> None:
    got = applied("Limit: 30 units\nOwner: Anna Rossi", "New heading line\nLimit: 30 units\nOwner: Luca Verdi")

    assert got == "New heading line\nLimit: 30 units\nOwner: Luca Verdi (previously Anna Rossi)"


def test_a_value_is_found_in_its_line_by_its_normalised_form() -> None:
    got = applied("Intro words\nPrice 1,200 per month", "Intro words\nPrice 1,300 per month")

    assert got == "Intro words\nPrice 1,300 per month (previously 1,200)"


def test_the_note_goes_before_the_trailing_whitespace_of_the_line() -> None:
    got = applied("Limit: 30 units\nOwner: Anna Rossi", "Limit: 40 units  \nOwner: Anna Rossi")

    assert got == "Limit: 40 units (previously 30)  \nOwner: Anna Rossi"


def test_unpaired_lines_hand_their_values_to_the_last_non_blank_line_of_the_replace() -> None:
    find = "Limit: 30 units\nOwner: Anna Rossi\nExtra: 77 box"

    got = applied(find, "Limit: 40 units\nOwner: Luca Verdi")

    assert got == "Limit: 40 units\nOwner: Luca Verdi (previously 30, Anna Rossi, 77)"


def test_the_last_non_blank_line_skips_trailing_blank_lines() -> None:
    find = "Limit: 30 units\nOwner: Anna Rossi\nExtra: 77 box"

    got = applied(find, "Limit: 40 units\nOwner: Luca Verdi\n\n")

    assert got == "Limit: 40 units\nOwner: Luca Verdi (previously 30, Anna Rossi, 77)\n\n"


def test_a_plain_line_that_says_previously_gets_nothing_more_in_a_block() -> None:
    got = applied("Limit: 30 units\nOwner: Anna Rossi", "Limit: 40 units, previously thirty\nOwner: Luca Verdi")

    assert got == "Limit: 40 units, previously thirty\nOwner: Luca Verdi (previously Anna Rossi)"


def test_an_edit_that_removes_no_value_adds_no_note_in_a_table() -> None:
    assert applied("| cerrado | 60% | body |", "| cerrado | 60% | softer body |") == "| cerrado | 60% | softer body |"


def test_a_rejected_edit_leaves_the_body_alone() -> None:
    body = PRE + "| cerrado | 60% |" + POST

    out, why = classic._apply_edit(body, classic.Edit(find="| cerrado | 61% |", replace="| cerrado | 50% |"), set())

    assert (out, why) == (body, "not found")


# -- 3. rejected edits are logged -------------------------------------------------------------------------------------

STATUS_BODY = (
    "# Overview\n\nStatus: active. Owner: Anna Rossi. The limit is 30.\n\n# Pricing\n\nStatus: active.\n\nPrice is 1,200 per month.\n"
)
QUIET = long_raw("The owner Anna Rossi pays 1,200 per month and the limit is 30. ")  # no new value: no second ask


def rejected_logs(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG and "rejected for" in r.getMessage()]


def run_big(bundle: Path, llm: FakeLLM, replies: list[dict[str, Any]], prefix: str = SHORT) -> NoteDraft:
    return run_merge(bundle, llm, QUIET, replies, body=body_of(share_of(QUIET) + 100, prefix))


@pytest.mark.parametrize(
    ("why", "prefix", "the_edit"),
    [
        ("not found", SHORT, edit("Nowhere to be found.", "x")),
        ("ambiguous", STATUS_BODY, edit("Status: active.", "Status: closed.")),
        ("invented", SHORT, edit("Price is 1,200 per month.", "Price is 777.")),
    ],
)
def test_a_rejected_edit_is_logged_at_debug_with_its_reason_and_find(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture, why: str, prefix: str, the_edit: dict[str, str]
) -> None:
    with caplog.at_level(logging.DEBUG, logger="llmw2"):
        run_big(bundle, llm, [reply(the_edit)], prefix)

    [message] = rejected_logs(caplog)
    assert why in message
    assert "finance/seed.md" in message
    assert repr(the_edit["find"]) in message


def test_an_applied_edit_is_not_logged_as_rejected(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="llmw2"):
        run_big(bundle, llm, [reply(edit("The limit is 30 units.", "The limit is 30 units, as before."))])

    assert rejected_logs(caplog) == []


def test_only_the_first_120_characters_of_a_long_find_are_logged(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    find = "zz" * 150

    with caplog.at_level(logging.DEBUG, logger="llmw2"):
        run_big(bundle, llm, [reply(edit(find, "x"))])

    [message] = rejected_logs(caplog)
    assert repr(find[:120]) in message
    assert "z" * 121 not in message


def test_a_rejected_addition_is_logged_with_the_first_120_characters_of_its_text(
    bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:
    text = "Setup fee is 999. " + "plain words " * 20

    with caplog.at_level(logging.DEBUG, logger="llmw2"):
        run_big(bundle, llm, [reply(additions=[{"section": "# Pricing", "text": text}])])

    [message] = rejected_logs(caplog)
    assert "invented" in message
    assert "finance/seed.md" in message
    assert repr(text[:120]) in message
    assert text[:121] not in message


def test_the_summary_line_of_the_merge_stays_as_it_was(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="llmw2"):
        run_big(
            bundle, llm, [reply(edit("Nowhere to be found.", "x"), edit("The limit is 30 units.", "The limit is 30 units, as before."))]
        )

    [message] = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "by edits" in r.getMessage()]
    assert re.search(r"1 applied, 1 rejected \(not found 1, ambiguous 0, invented 0\)", message)
