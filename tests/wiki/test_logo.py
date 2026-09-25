# Copyright 2026 Federico Cesarini, Marco Sassarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The terminal logo: two pixel rows per line, coloured only on a terminal."""

from __future__ import annotations

import io

from okf_wiki.logo import PIXELS, WIDTH, logo, show_logo


def test_the_logo_packs_two_pixel_rows_per_line() -> None:
    lines = logo(color=False).split("\n")

    assert len(lines) == (len(PIXELS) + 1) // 2
    assert max(map(len, lines)) <= WIDTH
    assert set("".join(lines)) <= {" ", "▀", "▄", "█"}


def test_colour_uses_the_logo_palette() -> None:
    text = logo()

    assert "\x1b[38;2;255;77;46m" in text and "\x1b[38;2;243;234;216m" in text  # red crow, cream letters


def test_nothing_is_printed_into_a_file_or_a_pipe() -> None:
    out = io.StringIO()

    show_logo(out)

    assert out.getvalue() == ""
