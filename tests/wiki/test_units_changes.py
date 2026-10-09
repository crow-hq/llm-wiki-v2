# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The sentence changes and the note units that the revisions step reads (`_sentence_changes`, `_units`)."""

from __future__ import annotations

import re

import pytest

from llmw2.agents import steps_classic as classic

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
T_OLD = "| Item | Price |\n| --- | --- |\n| Basic plan | 1,200 |\n| Pro plan | 3,400 |"
T_NEW = "| Item | Price |\n| --- | --- |\n| Basic plan | 1,200 |\n| Pro plan | 3,900 |"


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


AMBIG_BODY = (
    "# Overview\n\n"
    "The limit is 30 units. Owner: Anna Rossi.\n\n"
    "# Limit 30 units\n\n"
    "# Archive\n\n"
    "An older limit was 30 units in the archive. Owner: Luca Verdi.\n\n"
    "# Contacts\n\n"
    "Contact: Mario Bianchi.\n"
)
