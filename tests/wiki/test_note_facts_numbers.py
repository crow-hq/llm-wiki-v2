# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""benchmarks/note_facts.py, part C of spec-giro2-passo1: a space as thousands separator needs exactly three digits."""

from __future__ import annotations

from types import ModuleType

import pytest

from tests.wiki.test_note_facts import load_script


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return load_script()


def found(script: ModuleType, text: str) -> set[str]:
    return {str(n) for n in script.numbers(text)}


def test_a_quarter_label_does_not_glue_into_a_number(script: ModuleType) -> None:
    # C: "Q2 2026" -> numbers `2026` (`2` has one digit, so numbers() drops it)
    assert found(script, "Q2 2026") == {"2026"}


def test_a_year_after_a_one_digit_number_is_not_a_group(script: ModuleType) -> None:
    # C: a thousands group must be exactly three digits not followed by another digit
    assert found(script, "5 2026") == {"2026"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12 500 kg", {"12500"}),
        ("1.234,5", {"1234.5"}),
        ("3,85 €", {"3.85"}),
        ("3.14159", {"3.14159"}),
        ("in 2026 2,400 kg", {"2026", "2400"}),
    ],
)
def test_the_numbers_of_the_examples_of_the_spec(script: ModuleType, text: str, expected: set[str]) -> None:
    # C: "Expected:" list
    assert found(script, text) == expected


def test_has_fact_finds_the_year_after_a_quarter_label(script: ModuleType) -> None:
    # C: `has_fact("Revenue in Q2 2026 rose", "2026")` is true
    assert script.has_fact("Revenue in Q2 2026 rose", "2026")


def test_has_fact_still_finds_a_number_written_with_a_space_separator(script: ModuleType) -> None:
    # C: "Keep everything else"
    assert script.has_fact("The plant makes 12 500 kg a day", "12500")


def test_normalize_keeps_the_year_of_a_quarter_label(script: ModuleType) -> None:
    # C: the year stays readable after normalizing
    assert "2026" in script.normalize("Revenue in Q2 2026 rose")
