# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`split_blocks`: a long source cut in blocks that fit a prompt, at the best boundary near an even share."""

from __future__ import annotations

import math

import pytest

from llmw2.agents.state import split_blocks

FILLER = "The parties agree on the terms that follow and on nothing else at all, as the text says here."


def filler(n: int) -> str:
    return " ".join([FILLER] * n)


def markdown(sections: int, paragraphs: int = 3, words: int = 4) -> str:
    return "\n\n".join(f"## Section {i}\n\n" + "\n\n".join(filler(words) for _ in range(paragraphs)) for i in range(1, sections + 1))


def numbered(sections: int) -> str:
    """Flat text, as a PDF gives it: numbered heading lines and paragraphs."""
    return "\n".join(f"{i}. Clause {i}\n{filler(10)}\n" for i in range(1, sections + 1))


def sentences(n: int) -> str:
    return " ".join(f"Sentence number {i} is written here." for i in range(n))


def check(text: str, max_chars: int) -> list:
    blocks = split_blocks(text, max_chars)
    assert "".join(b.text for b in blocks) == text
    assert all(0 < len(b.text) <= max_chars for b in blocks)
    assert all(isinstance(b.heading, str) for b in blocks)
    assert len(blocks) >= math.ceil(len(text) / max_chars)
    return blocks


# -- the invariant: the blocks are the text, whole ---------------------------------------------------


@pytest.mark.parametrize("max_chars", [500, 2_000, 3_000, 10_000])
@pytest.mark.parametrize(
    "text",
    [
        markdown(12),
        markdown(3, paragraphs=12),
        numbered(14),
        sentences(900),
        "x" * 9_000,
        "\n\n\n  " + markdown(6) + "   \n\n",
        markdown(6).replace("\n", "\r\n"),
        "word " * 3_000,
    ],
    ids=["markdown", "few-long-sections", "numbered", "sentences", "one-word", "padded", "crlf", "words"],
)
def test_the_blocks_joined_are_the_original_text_and_none_is_over_the_limit(text: str, max_chars: int) -> None:
    check(text, max_chars)


def test_hash_marks_and_spacing_are_kept_in_the_blocks() -> None:
    text = "  ## Title \n\n" + filler(40) + "\n\n   ### Sub\n\n" + filler(40) + "  \n"

    blocks = check(text, 2_000)

    assert blocks[0].text.startswith("  ## Title ")
    assert blocks[-1].text.endswith("  \n")


def test_a_text_that_fits_is_one_block() -> None:
    text = markdown(2)

    blocks = split_blocks(text, len(text))

    assert [b.text for b in blocks] == [text]


def test_a_text_that_fits_and_has_a_title_heading_names_it() -> None:
    blocks = split_blocks("## Pricing\n\nShort.", 1_000)

    assert len(blocks) == 1 and "Pricing" in blocks[0].heading
    assert not blocks[0].heading.endswith("(continued)")


# -- Markdown: cut at section boundaries -----------------------------------------------------------


def test_markdown_is_cut_at_the_section_boundary_nearest_the_even_share() -> None:
    text = markdown(6)  # six sections of the same size
    size = len(text)
    max_chars = size // 2 + 100  # n = 2, target size / 2: the boundary between sections 3 and 4

    blocks = check(text, max_chars)

    assert len(blocks) == 2
    assert blocks[1].text.startswith("## Section 4")
    assert "Section 1" in blocks[0].heading and "Section 4" in blocks[1].heading
    assert not blocks[1].heading.endswith("(continued)")


def test_a_block_starting_at_a_heading_is_not_continued_and_one_starting_mid_section_is() -> None:
    text = "## Intro\n\nShort intro.\n\n## Big\n\n" + "\n\n".join(filler(8) for _ in range(12))

    blocks = check(text, 2_500)

    assert len(blocks) >= 3
    for block in blocks:
        if block.text.lstrip().startswith("#"):
            assert not block.heading.endswith(" (continued)"), block.heading
        else:
            assert block.heading.endswith(" (continued)"), block.heading
            assert "Big" in block.heading
    assert "Intro" in blocks[0].heading


def test_a_continued_heading_is_the_section_it_was_cut_from() -> None:
    text = "## Alpha\n\n" + "\n\n".join(filler(8) for _ in range(10)) + "\n\n## Beta\n\n" + "\n\n".join(filler(8) for _ in range(10))

    blocks = check(text, 3_000)

    continued = [b for b in blocks if b.heading.endswith(" (continued)")]
    assert continued
    at = 0
    for block in blocks:
        owner = "Alpha" if at < text.index("## Beta") else "Beta"  # the section the block starts in
        assert owner in block.heading
        at += len(block.text)


def test_one_huge_section_without_paragraphs_is_cut_at_sentences() -> None:
    text = "## Big\n" + sentences(500)
    max_chars = 3_000

    blocks = check(text, max_chars)

    assert len(blocks) >= math.ceil(len(text) / max_chars)
    for block in blocks[:-1]:
        assert block.text.rstrip().endswith(".")  # cut where a sentence ends
    assert "Big" in blocks[0].heading
    assert all(b.heading.endswith(" (continued)") for b in blocks[1:])


def test_one_huge_section_without_sentence_ends_is_cut_to_a_fixed_length() -> None:
    text = "## Big\n" + "x" * 10_000

    blocks = check(text, 3_000)

    assert len(blocks) >= 4


# -- flat text and text without headings -------------------------------------------------------------


def test_flat_text_with_numbered_headings_is_cut_at_a_numbered_line() -> None:
    text = numbered(6)  # six clauses of the same size, each a heading line and a paragraph
    max_chars = len(text) // 2 + 100

    blocks = check(text, max_chars)

    assert len(blocks) == 2
    assert blocks[1].text.lstrip().startswith("4. Clause 4")
    assert "Clause 4" in blocks[1].heading and not blocks[1].heading.endswith("(continued)")
    assert "Clause 1" in blocks[0].heading


def test_text_without_headings_has_empty_headings_and_is_cut_at_paragraphs() -> None:
    paragraphs = [filler(6) + f" Ending {i}." for i in range(20)]
    text = "\n\n".join(paragraphs)

    blocks = check(text, 3_000)

    assert {b.heading for b in blocks} == {""}
    assert all(b.text.lstrip("\n").startswith("The parties") for b in blocks)  # no block starts inside a paragraph


# -- just above the limit: balanced blocks -----------------------------------------------------------


@pytest.mark.parametrize("over", [1, 10, 300])
def test_a_text_just_over_the_limit_makes_two_balanced_blocks_none_over_the_limit(over: int) -> None:
    max_chars = 4_000
    text = "\n\n".join(filler(1) for _ in range(60))
    text = text[: max_chars + over]  # cut anywhere: still about the same paragraph size

    blocks = check(text, max_chars)

    assert len(blocks) == 2
    assert all(abs(len(b.text) - len(text) / 2) <= 250 for b in blocks)  # each about half, not a full block and a crumb


def test_a_text_of_exactly_n_times_the_limit_makes_n_blocks() -> None:
    max_chars = 1_000
    text = "a" * (3 * max_chars)

    blocks = check(text, max_chars)

    assert [len(b.text) for b in blocks] == [1_000, 1_000, 1_000]


def test_split_blocks_is_deterministic_and_pure() -> None:
    text = markdown(10)

    assert split_blocks(text, 3_000) == split_blocks(text, 3_000)
