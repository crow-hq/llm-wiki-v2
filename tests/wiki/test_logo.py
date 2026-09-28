# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The terminal logo: two pixel rows per line, coloured only on a terminal."""

from __future__ import annotations

import io

from okf_wiki.logo import PIXELS, WIDTH, logo, show_logo


def test_the_logo_packs_two_pixel_rows_per_line() -> None:
    lines = logo(color=False).split("\n")

    assert len(lines) == (len(PIXELS) + 1) // 2
    assert max(map(len, lines)) <= WIDTH
    assert set("".join(lines)) <= {" ", "▀", "▄", "█"}


def test_logo_when_colour_is_on_then_uses_ansi_colours() -> None:
    assert "\x1b[38;2;" in logo()


def test_nothing_is_printed_into_a_file_or_a_pipe() -> None:
    out = io.StringIO()

    show_logo(out)

    assert out.getvalue() == ""
