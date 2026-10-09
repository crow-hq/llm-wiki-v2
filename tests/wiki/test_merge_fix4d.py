# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Round 7, blind from spec-fase4d.md: "previously" never loses a value; a located rewrite may drop only what its changes replace."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from llmw2.agents import steps_classic as classic
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_merge_located import (
    LIMIT_UNIT,
    ROW_UNIT,
    S_NEW,
    S_OLD,
    STD_BODY,
    T_NEW,
    T_OLD,
    doc,
    prompt_of,
    reply_l,
    run,
    summary_lines,
    uid,
)

Edit = classic.Edit


def after_previously(text: str) -> str:
    """What follows the first "previously" of the text."""
    assert "previously" in text
    return text.split("previously", 1)[1]


# == 1. _note_removed: a value is skipped only if it is already there =============================================


def test_a_line_with_a_previous_note_still_gets_a_note_for_another_removed_value() -> None:
    find = "The limit is 30 units. Owner: Anna Rossi."
    replace = "The limit is 40 units. Owner: Anna Rossi. (previously 25)"

    result = classic._note_removed(find, replace, ["30"])

    assert "(previously 25" in result
    assert "30" in after_previously(result)
    assert "25" in result
    assert result.startswith("The limit is 40 units. Owner: Anna Rossi.")


def test_a_value_already_on_the_line_is_not_noted_again() -> None:
    find = "The limit is 30 units."
    replace = "The limit is 40 units (was 30 before)."

    assert classic._note_removed(find, replace, ["30"]) == replace


def test_a_value_already_in_the_previous_note_of_the_line_is_not_noted_again() -> None:
    find = "The limit is 30 units."
    replace = "The limit is 40 units. (previously 30)"

    assert classic._note_removed(find, replace, ["30"]) == replace


def test_only_the_values_not_already_there_are_noted() -> None:
    find = "The limit is 30 units. Owner: Anna Rossi."
    replace = "The limit is 40 units. Owner: Luca Verdi. (previously 30)"

    result = classic._note_removed(find, replace, ["30", "Anna Rossi"])

    assert "Anna Rossi" in after_previously(result)
    assert result.count("30") == 1  # 30 is not written a second time
    assert "Luca Verdi" in result


def test_nothing_is_added_when_every_removed_value_is_already_there() -> None:
    find = "The limit is 30 units. Owner: Anna Rossi."
    replace = "The limit is 40 units. Owner: Luca Verdi. (previously 30, Anna Rossi)"

    assert classic._note_removed(find, replace, ["30", "Anna Rossi"]) == replace


def test_a_value_is_compared_in_its_normalised_form() -> None:
    find = "The price is 1,200 per month."
    replace = "The price is 1200 per month, up from 1.200 before."

    result = classic._note_removed(find, replace, ["1,200"])

    assert result == replace


def test_a_table_cell_with_a_previous_note_still_gets_a_note_for_another_removed_value() -> None:
    find = "| Pro plan | 3,400 |"
    replace = "| Pro plan | 3,900 (previously 3,000) |"

    result = classic._note_removed(find, replace, ["3,400"])

    assert result.startswith("| Pro plan |") and result.endswith("|")
    assert "3,400" in after_previously(result)
    assert "3,000" in result
    assert "3,900" in result


def test_a_value_already_in_the_cell_is_not_noted_again() -> None:
    find = "| Pro plan | 3,400 |"
    replace = "| Pro plan | 3,900 (previously 3,400) |"

    assert classic._note_removed(find, replace, ["3,400"]) == replace


def test_a_table_cell_with_every_removed_value_already_there_gets_nothing() -> None:
    find = "| Pro plan | 3,400 | Anna Rossi |"
    replace = "| Pro plan | 3,900 (previously 3,400) | Luca Verdi (previously Anna Rossi) |"

    assert classic._note_removed(find, replace, ["3,400", "Anna Rossi"]) == replace


def test_a_table_row_without_a_note_gets_the_note_in_the_cell_as_before() -> None:
    assert classic._note_removed(ROW_UNIT, "| Pro plan | 3,900 |", ["3,400"]) == "| Pro plan | 3,900 (previously 3,400) |"


# == 2. _apply_edit, single line: the same two rules ===============================================================

LINE_BODY = "# Overview\n\nThe limit is 30 units. Owner: Anna Rossi.\n\n# Contacts\n\nContact: Mario Bianchi.\n"


def apply(find: str, replace: str, known: set[str] | None = None, body: str = LINE_BODY) -> str:
    new, why = classic._apply_edit(body, Edit(find=find, replace=replace), known or set())
    assert why == ""
    return new


def test_an_edit_whose_replace_has_a_previous_note_still_gets_the_note_of_another_removed_value() -> None:
    body = apply("The limit is 30 units.", "The limit is 40 units. (previously 25)", {"25", "40"})

    line = next(x for x in body.splitlines() if x.startswith("The limit is 40"))
    assert "(previously 25" in line
    assert "30" in after_previously(line)


def test_an_edit_whose_replace_already_holds_the_removed_value_gets_no_note() -> None:
    body = apply("The limit is 30 units.", "The limit is 40 units, previously 30.", {"40"})

    assert "The limit is 40 units, previously 30." in body
    assert body.count("previously") == 1
    assert "30 units" not in body  # 30 is written once, in the replace


def test_an_edit_whose_replace_has_the_value_without_the_word_gets_no_note() -> None:
    body = apply("The limit is 30 units.", "The limit is 40 units (30 before).", {"40"})

    assert "previously" not in body


def test_an_edit_with_every_removed_value_already_in_the_replace_gets_nothing() -> None:
    body = apply(
        "The limit is 30 units. Owner: Anna Rossi.",
        "The limit is 40 units. Owner: Luca Verdi. (previously 30, Anna Rossi)",
        {"lucaverdi", "40"},
    )

    assert body == LINE_BODY.replace(
        "The limit is 30 units. Owner: Anna Rossi.", "The limit is 40 units. Owner: Luca Verdi. (previously 30, Anna Rossi)"
    )


def test_an_edit_that_removes_a_value_still_gets_the_plain_note() -> None:
    body = apply("The limit is 30 units.", "The limit is 40 units.", {"40"})

    assert "The limit is 40 units. (previously 30) Owner: Anna Rossi." in body or "The limit is 40 units. (previously 30)" in body


# == 3. no number repeated from a code ==============================================================================


def test_a_number_inside_a_listed_code_is_left_out_of_the_note() -> None:
    result = classic._note_removed("Reference AJP-5870 applies.", "Reference DVE-3853 applies.", ["AJP-5870", "5870"])

    assert "(previously AJP-5870)" in result
    assert "AJP-5870, 5870" not in result
    assert "5870" not in result.replace("AJP-5870", "")


def test_the_number_is_left_out_whatever_its_place_in_the_list() -> None:
    result = classic._note_removed("Reference AJP-5870 applies.", "Reference DVE-3853 applies.", ["5870", "AJP-5870"])

    assert "(previously AJP-5870)" in result
    assert "5870" not in result.replace("AJP-5870", "")


def test_several_codes_each_drop_their_own_number() -> None:
    find = "Codes VKA-5329 and FEN-1209 apply."
    result = classic._note_removed(find, "Codes apply.", ["VKA-5329", "5329", "FEN-1209", "1209"])

    assert "(previously VKA-5329, FEN-1209)" in result


def test_a_standalone_number_that_is_not_part_of_a_listed_code_is_kept() -> None:
    find, replace = "Reference AJP-5870 and limit 5871 apply.", "Reference DVE-3853 and limit 40 apply."

    result = classic._note_removed(find, replace, ["AJP-5870", "5871"])

    assert "(previously AJP-5870, 5871)" in result


def test_a_number_is_kept_when_no_code_is_listed() -> None:
    result = classic._note_removed("The limit is 5870 units.", "The limit is 40 units.", ["5870"])

    assert result == "The limit is 40 units. (previously 5870)"


def test_the_single_line_edit_leaves_out_the_number_of_a_code() -> None:
    body = apply(
        "Reference AJP-5870 applies.", "Reference DVE-3853 applies.", {"dve-3853", "3853"}, body="# R\n\nReference AJP-5870 applies.\n"
    )

    assert "(previously AJP-5870)" in body
    assert "AJP-5870, 5870" not in body


def test_the_single_line_edit_keeps_a_standalone_number() -> None:
    body = apply(
        "Reference AJP-5870 and limit 5871 apply.",
        "Reference DVE-3853 and limit 4000 apply.",
        {"dve-3853", "3853", "4000"},
        body="# R\n\nReference AJP-5870 and limit 5871 apply.\n",
    )

    assert "(previously AJP-5870, 5871)" in body


# == 4. merge_located: a rewritten unit may drop only what its changes replace =====================================

W_OLD = "The watchlist reference is XGF-2009 for five accounts."
W_NEW = "The watchlist reference is DVE-3853 for five accounts."
W_BODY = (
    "# Watch\n\nItems:\n"
    '- A watchlist (reference "XGF-2009"; also cited under codes VKA-5329, FEN-1209) covers five accounts.\n'
    "- Other item without values.\n"
)
W_UNIT = '- A watchlist (reference "XGF-2009"; also cited under codes VKA-5329, FEN-1209) covers five accounts.'
W_KEPT = '- A watchlist (reference "DVE-3853"; also cited under codes VKA-5329, FEN-1209) covers five accounts.'
W_DROPS = '- A watchlist (reference "DVE-3853") covers five accounts.'

SUMMARY4 = re.compile(r"rejected \((?:invented (\d+), empty (\d+), unknown (\d+), dropped (\d+))\)")


def rejects(caplog: pytest.LogCaptureFixture) -> list[int]:
    [line] = summary_lines(caplog)
    m = SUMMARY4.search(line.getMessage())
    assert m, line.getMessage()
    return [int(x) for x in m.groups()]


def test_a_unit_that_drops_a_value_its_changes_do_not_replace_is_rejected_and_kept(bundle: Path, llm: FakeLLM) -> None:
    unit = uid(STD_BODY, LIMIT_UNIT)

    draft = run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l({unit: "The limit is 40 units."})]).draft  # Anna Rossi is lost

    assert draft.body.strip() == STD_BODY.strip()


def test_a_unit_that_drops_a_code_is_rejected_and_kept(bundle: Path, llm: FakeLLM) -> None:
    draft = run(bundle, llm, doc(W_NEW), doc(W_OLD), [reply_l({uid(W_BODY, W_UNIT): W_DROPS})], body=W_BODY).draft

    assert draft.body.strip() == W_BODY.strip()
    assert "VKA-5329" in draft.body and "FEN-1209" in draft.body


def test_a_dropped_unit_is_counted_in_the_summary_line(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        run(bundle, llm, doc(W_NEW), doc(W_OLD), [reply_l({uid(W_BODY, W_UNIT): W_DROPS})], body=W_BODY)

    [line] = summary_lines(caplog)
    assert line.levelno == logging.WARNING
    assert rejects(caplog) == [0, 0, 0, 1]
    assert "0 units rewritten" in line.getMessage()
    assert re.search(r"rejected \(invented \d+, empty \d+, unknown \d+, dropped \d+\)", line.getMessage())


def test_a_dropped_unit_is_logged_at_debug_with_the_reason_dropped(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="llmw2"):
        run(bundle, llm, doc(W_NEW), doc(W_OLD), [reply_l({uid(W_BODY, W_UNIT): W_DROPS})], body=W_BODY)

    [message] = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG and "rejected for" in r.getMessage()]
    assert "dropped" in message
    assert "finance/seed.md" in message
    assert repr(W_DROPS[:120]) in message


def test_dropped_is_counted_apart_from_the_other_reasons(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    old, new = doc(S_OLD, T_OLD), doc(S_NEW, T_NEW)
    writer = reply_l(
        {
            uid(STD_BODY, LIMIT_UNIT): "The limit is 40 units.",  # drops Anna Rossi
            uid(STD_BODY, ROW_UNIT): "| Pro plan | 9,999 |",  # invents
            "u98": "x",  # unknown
        }
    )

    with caplog.at_level(logging.INFO):
        run(bundle, llm, new, old, [writer])

    assert rejects(caplog) == [1, 0, 1, 1]


def test_a_unit_that_drops_only_what_its_changes_replace_is_applied_with_previously(bundle: Path, llm: FakeLLM) -> None:
    unit = uid(STD_BODY, LIMIT_UNIT)

    draft = run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l({unit: "The limit is 40 units. Owner: Anna Rossi."})]).draft

    assert draft.body.strip() == STD_BODY.replace(LIMIT_UNIT, "The limit is 40 units. Owner: Anna Rossi. (previously 30)").strip()


def test_a_replaced_code_is_noted_and_the_codes_kept_stay(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        draft = run(bundle, llm, doc(W_NEW), doc(W_OLD), [reply_l({uid(W_BODY, W_UNIT): W_KEPT})], body=W_BODY).draft

    assert W_KEPT in draft.body
    assert "(previously XGF-2009)" in draft.body
    assert "XGF-2009, 2009" not in draft.body
    assert rejects(caplog) == [0, 0, 0, 0]
    assert "Other item without values." in draft.body


def test_a_unit_that_already_holds_a_previously_note_still_notes_the_replaced_code(bundle: Path, llm: FakeLLM) -> None:
    body = W_BODY.replace("accounts.\n- Other", "accounts. (previously XGF-1111)\n- Other")
    old_unit = W_UNIT + " (previously XGF-1111)"
    new_unit = W_KEPT + " (previously XGF-1111)"

    draft = run(bundle, llm, doc(W_NEW), doc(W_OLD), [reply_l({uid(body, old_unit): new_unit})], body=body).draft

    line = next(x for x in draft.body.splitlines() if "DVE-3853" in x)
    assert "XGF-1111" in after_previously(line)
    assert "XGF-2009" in after_previously(line)  # the replaced code is not lost
    assert "VKA-5329" in line and "FEN-1209" in line
    assert "XGF-2009, 2009" not in line


def test_two_units_one_dropping_and_one_fine_are_judged_apart(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    old, new = doc(S_OLD, T_OLD), doc(S_NEW, T_NEW)
    writer = reply_l({uid(STD_BODY, LIMIT_UNIT): "The limit is 40 units.", uid(STD_BODY, ROW_UNIT): "| Pro plan | 3,900 |"})

    with caplog.at_level(logging.INFO):
        draft = run(bundle, llm, new, old, [writer]).draft

    assert "The limit is 30 units. Owner: Anna Rossi." in draft.body  # kept
    assert "| Pro plan | 3,900 (previously 3,400) |" in draft.body  # applied
    assert rejects(caplog) == [0, 0, 0, 1]
    [line] = summary_lines(caplog)
    assert "1 units rewritten" in line.getMessage()


# == 5. the prompt =====================================================================================================


def test_the_prompt_tells_the_writer_to_keep_what_its_changes_do_not_replace(bundle: Path, llm: FakeLLM) -> None:
    run(bundle, llm, doc(S_NEW), doc(S_OLD), [reply_l()])

    prompt = prompt_of(llm).lower()
    assert "not applied" in prompt or "keep every value" in prompt
