# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""BM25 ranking by words, in code: no model call. Used where a classifier request cannot hold every option."""

from __future__ import annotations

import math
import re

_WORD = re.compile(r"[^\W_]{3,}|\d+(?:[.,]\d+)*")
_K1, _B = 1.2, 0.75


def words(text: str) -> list[str]:
    """Lowercased words of three letters or more, and numbers."""
    return [w.lower() for w in _WORD.findall(text)]


def rank(query: str, docs: dict[str, str]) -> list[str]:
    """The keys of `docs`, best BM25 match for `query` first; ties keep the order of `docs`."""
    tokens = {key: words(text) for key, text in docs.items()}
    if not tokens:
        return []
    avg = sum(map(len, tokens.values())) / len(tokens) or 1.0
    df: dict[str, int] = {}
    for ws in tokens.values():
        for w in set(ws):
            df[w] = df.get(w, 0) + 1
    asked = set(words(query))
    n = len(tokens)

    def score(ws: list[str]) -> float:
        tf: dict[str, int] = {}
        for w in ws:
            if w in asked:
                tf[w] = tf.get(w, 0) + 1
        idf = {w: math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) for w in tf}
        return sum(idf[w] * f * (_K1 + 1) / (f + _K1 * (1 - _B + _B * len(ws) / avg)) for w, f in tf.items())

    scores = {key: score(ws) for key, ws in tokens.items()}
    return sorted(docs, key=lambda key: -scores[key])
