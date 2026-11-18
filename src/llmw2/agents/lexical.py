# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""BM25 ranking by words, in code: no model call. Used where a classifier request cannot hold every option."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import math
import re
from collections import Counter
from collections.abc import Sequence

_WORD = re.compile(r"[^\W_]{3,}|\d+(?:[.,]\d+)*")
_K1, _B = 1.2, 0.75


def words(text: str) -> list[str]:
    """Lowercased words of three letters or more, and numbers."""
    return [w.lower() for w in _WORD.findall(text)]


_FUNCTION_WORDS = {
    "Italian": "il lo la gli le un una di che e è non per con del della dei delle nel nella sono come più anche questo questa si al alla "
    "ha hanno essere loro suo sua ma se nei sul sulla sui degli dello dalla dal dai dalle perché quando può tra fra già ancora molto "
    "cui quello quella questi queste ogni solo ho ha abbiamo siamo era fatto fare da in a o i ed ad "
    "ci vi mi ti lei lui noi voi sua suoi nostro nostra",
    "English": "the of and to in is that for it with as was on are by this be from at or an have not which they their has but",
    "French": "le la les des du de un une et est que qui dans pour pas sur au aux avec ce cette sont par plus ou mais nous vous il elle "
    "être ont",
    "German": "der die das und ist nicht ein eine von zu mit den dem des auf für im sich auch als es an werden aus er sie wir bei nach",
    "Spanish": "el la los las de del y que en un una es por con para no se su al lo como más pero sus le ha son está también",
    "Portuguese": "os as de do da dos das e que em um uma é por com para não se seu sua ao como mais mas são também foi tem",
}
_FUNCTION_SETS = {name: frozenset(text.split()) for name, text in _FUNCTION_WORDS.items()}
_LANGUAGE_CHARS = 20_000
MIN_HITS = 2
_LETTERS = re.compile(r"[^\W\d_]+")
_ADDRESS = re.compile(r"\S+@\S+|(?:https?://|www\.)\S+")  # "com" of an address is not Portuguese


def language_of(text: str) -> str | None:
    """The language of `text` (English name) from its function words over the first 20000 characters: Italian, English,
    French, German, Spanish or Portuguese. None unless the best has at least `MIN_HITS` hits and over 1.3 times every other's."""
    words = _LETTERS.findall(_ADDRESS.sub(" ", text[:_LANGUAGE_CHARS]).lower())
    scores = sorted(((sum(w in fw for w in words), name) for name, fw in _FUNCTION_SETS.items()), reverse=True)
    (best, name), (second, _) = scores[0], scores[1]
    return name if best >= MIN_HITS and best > 1.3 * second else None


class Bm25:
    """BM25 over documents whose words were counted once (`counts`: key -> word -> times), so a query reads only the
    postings of its own words and no text is tokenized again.

    Documents can be added and removed one at a time (`add`, `remove`): a change to one document costs its own words, not the
    index. A query ranks the documents of any set of keys (`keys`, default all of them) as an index of just those documents
    would: the document count, the word frequencies across documents and the average length are those of the set. Lengths are
    summed as integers, so the scores do not depend on the order in which documents were added or removed."""

    def __init__(self, counts: dict[str, Counter[str]] | None = None) -> None:
        self.length: dict[str, int] = {}  # key -> number of words
        self.total = 0  # the sum of the lengths
        self.postings: dict[str, list[tuple[str, int]]] = {}  # word -> [(key, times)] in the order the documents were added
        self._words: dict[str, tuple[str, ...]] = {}  # key -> its distinct words, to take it out (a tuple: the collector does not track it)
        for key, c in (counts or {}).items():
            self.add(key, c)

    def __len__(self) -> int:
        return len(self.length)

    def __contains__(self, key: str) -> bool:
        return key in self.length

    @property
    def keys(self) -> list[str]:
        return list(self.length)

    def add(self, key: str, counts: Counter[str]) -> None:
        """Index a document; one already there under this key is replaced."""
        if key in self.length:
            self.remove(key)
        size = sum(counts.values())
        self.length[key] = size
        self.total += size
        self._words[key] = tuple(counts)
        for w, f in counts.items():
            self.postings.setdefault(w, []).append((key, f))

    def remove(self, key: str) -> None:
        """Take a document out (nothing if it is not there)."""
        size = self.length.pop(key, None)
        if size is None:
            return
        self.total -= size
        for w in self._words.pop(key):
            posting = [entry for entry in self.postings[w] if entry[0] != key]
            if posting:
                self.postings[w] = posting
            else:
                del self.postings[w]

    def scores(self, query: str, keys: Sequence[str] | None = None) -> dict[str, float]:
        """The score of every key of `keys` (all the documents, in the order they were added, by default), 0.0 for one with
        none of the query's words. Every key must be indexed."""
        keys = list(self.length) if keys is None else keys
        n = len(keys)
        if n == len(self.length):
            subset, size = None, self.total  # the set is the whole index
        else:
            subset, size = set(keys), sum(self.length[k] for k in keys)
        avg = (size / n if n else 1.0) or 1.0
        total = dict.fromkeys(keys, 0.0)
        for w in dict.fromkeys(words(query)):  # in the query's order: a set's changes with the process, and the sum's last bits with it
            posting = self.postings.get(w)
            if not posting:
                continue
            found = posting if subset is None else [(k, f) for k, f in posting if k in subset]
            idf = math.log(1 + (n - len(found) + 0.5) / (len(found) + 0.5))
            for key, f in found:
                total[key] += idf * f * (_K1 + 1) / (f + _K1 * (1 - _B + _B * self.length[key] / avg))
        return total

    def rank(self, query: str, keys: Sequence[str] | None = None) -> list[str]:
        """The keys, best match first; ties keep the order of the keys."""
        keys = list(self.length) if keys is None else list(keys)
        scores = self.scores(query, keys)
        return sorted(keys, key=lambda key: -scores[key])


def rank(query: str, docs: dict[str, str]) -> list[str]:
    """The keys of `docs`, best BM25 match for `query` first; ties keep the order of `docs`."""
    return Bm25({key: Counter(words(text)) for key, text in docs.items()}).rank(query)
