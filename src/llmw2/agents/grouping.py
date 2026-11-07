# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Groups of documents that share a subject, in code: TF-IDF over the words of their whole text, cosine similarity,
average-linkage clustering cut at one threshold. No model call.

A small local classifier routes poorly among many folders and a small writer plans a tree poorly; grouping the
documents of a batch here, and naming each group from its own words, gives them the structure they cannot lay out
(`Wiki.plan_groups`).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable

from llmw2.agents import echo, lexical
from llmw2.bundle.tree import slugify

GROUP_CUT = 0.15  # average similarity two groups of a batch window need to be merged
ATTACH_CUT = 0.1  # average similarity a group needs to an existing folder's notes to join that folder


def vectors(texts: list[str], words: Callable[[str], list[str]] = lexical.words) -> list[dict[str, float]]:
    """Unit TF-IDF vectors; words in one text only, or in more than half of them (two, among few), carry no weight.
    `words` splits a text (`lexical.words`; a caller that splits the same texts again passes one that remembers)."""
    tokens = [words(t) for t in texts]
    n = len(tokens)
    df = Counter(w for ws in tokens for w in set(ws))
    common = max(2.5, 0.5 * n)
    out = []
    for ws in tokens:
        weights = {w: (1 + math.log(c)) * math.log((n + 1) / df[w]) for w, c in Counter(ws).items() if 1 < df[w] < common}
        norm = math.sqrt(sum(x * x for x in weights.values())) or 1.0
        out.append({w: x / norm for w, x in weights.items()})
    return out


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def groups(texts: list[str], cut: float = GROUP_CUT) -> list[list[int]]:
    """The indexes of the texts in groups of two or more, largest first; a text that shares its subject with no other
    is in none."""
    vecs = vectors(texts)
    n = len(vecs)
    sim = [[cosine(vecs[i], vecs[j]) for j in range(n)] for i in range(n)]
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    link = {(i, j): sim[i][j] for i in range(n) for j in range(i + 1, n)}
    while link:
        (a, b), best = max(link.items(), key=lambda kv: kv[1])
        if best < cut:
            break
        members[a] += members.pop(b)
        link = {k: v for k, v in link.items() if a not in k and b not in k}
        for c in members:
            if c != a:
                link[(min(a, c), max(a, c))] = sum(sim[x][y] for x in members[a] for y in members[c]) / (len(members[a]) * len(members[c]))
    return sorted((sorted(m) for m in members.values() if len(m) > 1), key=lambda m: (-len(m), m[0]))


def name(texts: list[str], group: list[int]) -> str:
    """A folder name for a group from its own words: the two that weigh most in its documents (TF-IDF over `texts`,
    the batch window); "" when they share none."""
    weight: Counter[str] = Counter()
    vecs = vectors(texts)
    for i in group:
        weight.update(vecs[i])
    words = [w for w, _ in weight.most_common() if w.isalpha() and w not in echo.FORMAT_WORDS and slugify(w, fallback="")]
    return "-".join(words[:2])


def closest(
    group_texts: list[str], folders: dict[str, list[str]], cut: float = ATTACH_CUT, words: Callable[[str], list[str]] = lexical.words
) -> str | None:
    """The folder (key of `folders`, its notes' texts) whose notes are on average most similar to the group, if that
    similarity reaches `cut`; None otherwise. The group counts as one text, so the words its documents share are not
    taken for common ones; weights come from it and every folder's notes together."""
    return closest_each([group_texts], folders, cut, words)[0]


def closest_each(
    groups: list[list[str]], folders: dict[str, list[str]], cut: float = ATTACH_CUT, words: Callable[[str], list[str]] = lexical.words
) -> list[str | None]:
    """`closest` for each group, against the same folders: their texts are split and weighed once, and a group reads
    only the texts that share a word with it. The scores are `closest`'s: a group only moves the weights of its own
    words (one more text holds them), so each text's norm is adjusted for those words alone."""
    keys = [k for k, notes in folders.items() if notes]
    if not keys:
        return [None] * len(groups)
    texts = [Counter(words(t)) for k in keys for t in folders[k]]
    owner = [k for k in keys for _ in folders[k]]
    n = len(texts) + 1  # the group is one more text
    common = max(2.5, 0.5 * n)
    df = Counter(w for t in texts for w in t)

    def weight(count: int, held: int) -> float:  # 0 for a word in one text only, or in more than half of them
        return (1 + math.log(count)) * math.log((n + 1) / held) if 1 < held < common else 0.0

    squares = [sum(weight(c, df[w]) ** 2 for w, c in t.items()) for t in texts]
    holding: dict[str, list[int]] = {}
    for i, t in enumerate(texts):
        for w in t:
            holding.setdefault(w, []).append(i)
    out: list[str | None] = []
    for group in groups:
        mine = Counter(words(" ".join(group)))
        own = {w: weight(c, df[w] + 1) for w, c in mine.items()}
        norm = math.sqrt(sum(x * x for x in own.values())) or 1.0
        dot: dict[int, float] = {}
        square: dict[int, float] = {}
        for w in mine:
            if df[w] >= common:  # in more than half the texts with or without the group: no weight either way
                continue
            for i in holding.get(w, ()):
                c = texts[i][w]
                square[i] = square.get(i, squares[i]) - weight(c, df[w]) ** 2 + weight(c, df[w] + 1) ** 2
                if own[w]:
                    dot[i] = dot.get(i, 0.0) + own[w] / norm * weight(c, df[w] + 1)
        scores = dict.fromkeys(keys, 0.0)
        for i, x in dot.items():
            scores[owner[i]] += x / (math.sqrt(max(square[i], 0.0)) or 1.0)
        for k in keys:
            scores[k] /= len(folders[k])
        best = max(scores, key=lambda k: scores[k])
        out.append(best if scores[best] >= cut else None)
    return out
