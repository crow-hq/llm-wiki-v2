# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`cut_notes` (spec-giro2-passo1, part B): where the notes of one part are cut when they are over their cap."""

from __future__ import annotations

import random

import pytest

from llmw2.agents import steps_classic as classic
from llmw2.agents.steps_classic import cut_notes


def test_summary_version_is_three() -> None:
    # B: "Bump SUMMARY_VERSION to 2", then 3 when the part notes became plain text (spec keepcut)
    assert classic.SUMMARY_VERSION == 7


@pytest.mark.parametrize("notes", ["", "short", "x" * 100, "ends with space   ", "a\nb\n"])
def test_notes_within_the_cap_are_unchanged(notes: str) -> None:
    # B: "len(notes) <= cap: unchanged" (also when len == cap)
    assert cut_notes(notes, 100) == notes
    assert cut_notes(notes, len(notes)) == notes


@pytest.mark.parametrize("cap", [0, -1, -100])
def test_a_cap_of_zero_or_less_returns_an_empty_string(cap: int) -> None:
    # B: "`cap <= 0` returns an empty string"
    assert cut_notes("some notes that are long enough", cap) == ""


def test_the_cut_is_at_the_last_sentence_end_and_drops_the_whitespace() -> None:
    # B: "a sentence end is ... `.`, `!`, `?` followed by whitespace (the cut keeps the punctuation, drops the whitespace)"
    notes = "First sentence here. Second sentence here. Third sentence here."

    assert cut_notes(notes, 45) == "First sentence here. Second sentence here."


@pytest.mark.parametrize("mark", [".", "!", "?"])
def test_each_of_the_three_marks_ends_a_sentence(mark: str) -> None:
    # B: one of `.`, `!`, `?`
    notes = f"Alpha beta gamma{mark} Delta epsilon zeta eta theta"

    assert cut_notes(notes, 30) == f"Alpha beta gamma{mark}"


def test_a_newline_is_a_sentence_end_and_is_dropped() -> None:
    # B: "a sentence end is: a newline"
    notes = "line one\nline two\nline three"

    assert cut_notes(notes, 20) == "line one\nline two"


def test_a_mark_not_followed_by_whitespace_is_not_a_sentence_end() -> None:
    # B: "followed by whitespace": "3.14159" is not a sentence end, so the last whitespace is used
    notes = "pi is 3.14159 and e is 2.71828 and so on and on"

    assert cut_notes(notes, 20) == "pi is 3.14159 and e"


def test_the_whitespace_after_the_punctuation_may_sit_at_index_cap() -> None:
    # B: "the last sentence end ending at or before index cap - 1 (the whitespace ... may sit at index `cap`)"
    notes = "Hello world. More text follows"
    assert notes[12] == " "

    assert cut_notes(notes, 12) == "Hello world."


def test_the_last_sentence_end_is_preferred_to_a_later_whitespace() -> None:
    # B: sentence end first, whitespace only as a fallback
    notes = "Alpha beta gamma. Delta epsilon zeta eta"

    assert cut_notes(notes, 30) == "Alpha beta gamma."


def test_a_sentence_end_leaving_exactly_half_the_cap_is_used() -> None:
    # B: "Use it if it leaves at least `cap // 2` characters"
    notes = "abcde. fgh ijk lmn opqrstuvwxyz"

    assert cut_notes(notes, 12) == "abcde."


def test_a_sentence_end_leaving_less_than_half_the_cap_falls_back_to_the_last_whitespace() -> None:
    # B: "Else the last whitespace that leaves at least cap // 2 characters (cut before it)"
    notes = "abcde. fgh ijk lmn opqrstuvwxyz"

    assert cut_notes(notes, 15) == "abcde. fgh ijk"


def test_one_newline_near_the_start_does_not_cut_a_long_note_to_nothing() -> None:
    # B: the 33-of-53,377 regression: "one newline near the start" must not leave a few characters
    notes = "Head of the notes\n" + "lorem ipsum dolor " * 3_000
    assert len(notes) > 50_000

    result = cut_notes(notes, 5_000)

    assert 2_500 <= len(result) <= 5_000
    assert notes.startswith(result)
    assert result.endswith(("lorem", "ipsum", "dolor"))


def test_a_sentence_end_too_early_falls_back_to_the_last_whitespace() -> None:
    # B: "Hi." ends at index 2 and leaves 3 < 50 characters
    notes = "Hi. " + "word " * 400

    result = cut_notes(notes, 100)

    assert 50 <= len(result) <= 100
    assert result.endswith("word")
    assert notes.startswith(result)


def test_a_text_with_no_usable_whitespace_is_cut_hard_at_the_cap() -> None:
    # B: "Else the hard cut notes[:cap]"
    assert cut_notes("a" * 500, 100) == "a" * 100


def test_a_whitespace_too_early_is_not_used() -> None:
    # B: the only whitespace leaves 2 < cap // 2 characters
    notes = "ab " + "c" * 500

    assert cut_notes(notes, 100) == notes[:100]


def test_the_result_has_no_trailing_whitespace() -> None:
    # B: "The result is `.rstrip()`-ed"
    notes = "word  \n  another word. And more!  \n\n then some text " * 3
    for cap in range(5, 40):
        result = cut_notes(notes, cap)
        assert result == result.rstrip()


def test_a_cap_of_one_gives_the_first_character() -> None:
    # B: "never empty if notes.strip() is non-empty and cap >= 1"
    assert cut_notes("ab cd", 1) == "a"


def test_a_note_starting_with_whitespace_is_never_cut_to_empty() -> None:
    # B: "never empty if `notes.strip()` is non-empty and cap >= 1" (a leading newline is a sentence end at index 2)
    result = cut_notes("  \n  hello world", 3)

    assert result.strip() != ""


def test_the_result_is_never_longer_than_the_cap_and_comes_from_the_notes() -> None:
    # B: "never longer than `cap`"
    rng = random.Random(7)
    alphabet = ["word", "a.", "b!", "c?", "\n", " ", "  ", "x" * 30, "3.14", "e.g."]
    for _ in range(300):
        notes = "".join(rng.choice(alphabet) + rng.choice(["", " "]) for _ in range(rng.randint(1, 80)))
        cap = rng.randint(1, 120)

        result = cut_notes(notes, cap)

        assert len(result) <= cap
        assert result in notes  # not "a prefix": the spec only says rstrip, an implementation may also lstrip
        if len(notes) > cap and notes.strip():
            assert result.strip() != ""
