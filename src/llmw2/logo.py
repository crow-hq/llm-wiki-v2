# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The CROW logo for the terminal: the pixel crow and its name, two pixels per character (▀ ▄ █)."""

from __future__ import annotations

import os
import shutil
import sys
from typing import TextIO

# One letter per pixel: r red outline, b body, c cream letters, space the night. Drawn from the web logo.
PIXELS = (
    "                    rr",
    "                  rrrrrr",
    "                  rrbbrrrr",
    "                rrrb   rrrr",
    "              rrrrrr  rr  rr",
    "            rrrbbbbr rr r    cccc    cccccc    cccc   cc      cc",
    "           rr bbbbrr r  rr cccccccc cccccccc  cccccc  ccc    ccc",
    "         rrrbbbbbbbr r     ccc  ccc ccc  ccc ccc  ccc ccc c  ccc",
    "        rrrbbb  bbr  r     cc       ccc   cc cc    cc ccc cc ccc",
    "       rr bbbbb br  rr     cc       cccccccc cc    cc cccccc ccc",
    "     rrrbb rrbbbr  rr      cc       cccccc   cc    cc cccccccccc",
    "    rrrrbbr  b    rr       cc    cc ccc ccc  cc   ccc  cccccccc",
    "  rrrrr rrrrr  r rr        cccccccc ccc  cc   cccccc   ccc cccc",
    "rrrr  bbrrr rbrrrr          cccccc  cc   ccc  ccccc     cc  cc",
    "    rbbr    rrr rr",
    "  rrr r      rr  rr",
    "  r  r      rrrr  rrrrr",
    "            r  rrr  rrr",
)
COLORS = {"r": (255, 77, 46), "b": (60, 72, 118), "c": (243, 234, 216)}
WIDTH = max(map(len, PIXELS))


def logo(*, color: bool = True) -> str:
    """The logo as text; without colour every pixel is drawn the same."""
    rows = [row.ljust(WIDTH) for row in PIXELS]
    lines = []
    for top, bottom in zip(rows[::2], rows[1::2], strict=True):
        line = ""
        for a, b in zip(top, bottom, strict=True):
            if a == b == " ":
                line += " "
            elif not color:
                line += "█" if a != " " and b != " " else "▀" if a != " " else "▄"
            elif a == " ":
                line += f"\x1b[38;2;{';'.join(map(str, COLORS[b]))}m▄\x1b[0m"
            elif b == " " or a == b:
                line += f"\x1b[38;2;{';'.join(map(str, COLORS[a]))}m{'█' if a == b else '▀'}\x1b[0m"
            else:
                fg, bg = (";".join(map(str, COLORS[p])) for p in (a, b))
                line += f"\x1b[38;2;{fg};48;2;{bg}m▀\x1b[0m"
        lines.append(line.rstrip())
    return "\n".join(lines)


def show_logo(out: TextIO = sys.stdout) -> None:
    """Print the logo on a terminal wide enough for it; nothing when the output is a file or a pipe."""
    if not out.isatty() or shutil.get_terminal_size().columns < WIDTH:
        return
    color = not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
    print(logo(color=color) + "\n" + " " * 28 + "LLM WIKI V2 · setup\n", file=out)
