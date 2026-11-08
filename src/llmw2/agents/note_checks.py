# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What `llm.note_steps` builds in code around the two calls of a small writer: context for the title, exact items for the
body, and the checks of what comes back. No model call; every rule is a safety net, not a rule of the corpus."""

from __future__ import annotations

import difflib
import re
from collections import Counter

from llmw2.agents import lexical

ITEMS_MAX = 15  # most exact items listed in the body prompt
NAMES_MAX = 5  # most proper names of the source shown to the title call
HEADING_MAX = 120
CLOSE = 0.8  # the least similarity of two spellings of a name for one to be taken for the other
NEAR = 0.5  # the least similarity of a title word to a name of the source with the same initial and a close length
ITEM_CLOSE = 0.85  # the same, for an item of the body
_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_CAPITALIZED = re.compile(r"(?<![\w'\u2019-])[^\W\d_a-z][^\W\d_]*(?:['\u2019-][^\W\d_]+)*")
_SENTENCE_START = re.compile(r"(?:\A|[.!?:;]|[\n\r])[\s#>*\-+\"'“”«»(\[]*\Z")
_CODE = re.compile(r"\b(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9_-]{2,}\b|\b[A-Z]{3,}\b")
_ID_SUFFIX = re.compile(r"(?:[\s_-]+(?:[0-9a-f]{6,}|\d{4,}))+\Z", re.IGNORECASE)
_HEX = re.compile(r"[0-9a-f]{6,}", re.IGNORECASE)
_DIGITS = re.compile(r"\d{4,}")
_TITLE_END = re.compile(r"[,;:(\-\u2013\u2014&/\[]\s*\Z|\.\.\.\s*\Z|\u2026\s*\Z")
_SHORT_WORDS = frozenset().union(*lexical._FUNCTION_SETS.values())
_WRAPPER = r"(?:note|source|document|body)"
_OPEN_TAG = re.compile(rf"\A\s*<{_WRAPPER}(?:\s[^>]*)?>[ \t]*\n?", re.IGNORECASE)
_CLOSE_TAG = re.compile(rf"\n?[ \t]*</{_WRAPPER}>\s*\Z", re.IGNORECASE)
_OPEN_FENCE = re.compile(r"\A\s*```[ \t]*(?:markdown|md)?[ \t]*\n", re.IGNORECASE)
_CLOSE_FENCE = re.compile(r"\n[ \t]*```\s*\Z")

_URL = r"https?://[^\s<>\"'`)\]]+[^\s<>\"'`)\].,;:!?]"
_EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
_PATH = (
    r"(?<![\w/:@.-])(?:[A-Za-z]:\\[^\s\"'`<>|]+|(?:~|\.{1,2})?/[\w.~-]+(?:/[\w.~-]+)*/?"
    r"|[\w.-]+(?:/[\w.-]+)*/[\w-]+\.\w{1,10}|[\w.-]+/[\w.-]+/[\w.-]+(?:/[\w.-]+)*)(?![\w])"  # never stop inside a word
)
_SPAN = r"`([^`\n]+)`"
_IDENT = (
    r"\b[A-Za-z]+(?:_[A-Za-z0-9]+)+\b|\b[a-z]+(?:[A-Z][a-z0-9]*)+\b|\b[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+\b"
    r"|\b[A-Za-z]+\d[A-Za-z0-9]*\b|\b[A-Z]{2,}-\d+\b"
)
_ITEM = re.compile("|".join(f"(?:{p})" for p in (_URL, _EMAIL, _SPAN, _PATH, _IDENT)))


# -- title ---------------------------------------------------------------------------------


def heading_of(text: str) -> str:
    """The document's own heading: its first Markdown heading, else its first non-empty line when that is short enough to
    be a title; "" when there is neither."""
    lines = [line for line in text.splitlines() if line.strip()]
    for line in lines[:40]:
        if m := _HEADING_LINE.match(line):
            return m.group(1).strip()[:HEADING_MAX]
    first = lines[0].strip() if lines else ""
    return first if 0 < len(first) <= HEADING_MAX else ""


def _names(text: str) -> list[str]:
    """Capitalized words that do not open a sentence, most frequent first."""
    found: Counter[str] = Counter()
    for m in _CAPITALIZED.finditer(text):
        if len(m.group()) > 2 and not _SENTENCE_START.search(text[max(0, m.start() - 12) : m.start()]):
            found[m.group()] += 1
    return [w for w, _ in found.most_common()]


def distinctive(text: str) -> list[str]:
    """The source's proper names, a short list (what the title call is asked to keep as written)."""
    return _names(text)[:NAMES_MAX]


def title_hints(names: list[str]) -> str:
    """The line the metadata prompt shows after its first paragraph; "" when there are no names."""
    if not names:
        return ""
    return f"\nNames in the document, keep them spelled as written: {', '.join(names)}"


def _id_like(token: str, haystack: str) -> bool:
    """A hex or digit run that the source does not have, such as an id in a file name."""
    bare = token.strip(".,;:()[]\"'").lower()
    if bare in haystack:
        return False
    return bool(_DIGITS.search(bare) or (_HEX.fullmatch(bare) and any(c.isdigit() for c in bare)))


def _name_repair(word: str, names: list[str]) -> str | None:
    """The source's spelling of a capitalized word the source does not have: itself when it needs none, the one close spelling
    (else the one name of the same initial, a length within 2 and a similarity of at least `NEAR`, as NAT for NETA), None when
    there are several."""
    close = difflib.get_close_matches(word, names, n=3, cutoff=CLOSE)
    close = [c for c in close if c.lower() != word.lower()]
    if not close:
        low = word.lower()
        close = [
            n
            for n in dict.fromkeys(names)
            if n[0].lower() == low[0]
            and abs(len(n) - len(word)) <= 2
            and difflib.SequenceMatcher(None, low, n.lower()).ratio() >= NEAR
        ]
    return close[0] if len(close) == 1 else (None if close else word)


def short_language(text: str) -> str | None:
    """The language of a short text: `lexical.language_of` when it decides, else the one whose function words occur (at least
    `lexical.MIN_HITS` times) while the others' do not occur at all; None otherwise."""
    if decided := lexical.language_of(text):
        return decided
    words = lexical._LETTERS.findall(lexical._ADDRESS.sub(" ", text[: lexical._LANGUAGE_CHARS]).lower())
    hits = {name: sum(w in fw for w in words) for name, fw in lexical._FUNCTION_SETS.items()}
    found = [name for name, n in hits.items() if n]
    return found[0] if len(found) == 1 and hits[found[0]] >= lexical.MIN_HITS else None


def file_title(file_name: str | None) -> str:
    """A file name without its extension and its id-like suffix, as words."""
    stem = (file_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    return " ".join(_ID_SUFFIX.sub("", stem).replace("_", " ").replace("-", " ").split())


def checked_title(title: str, source_text: str, file_name: str | None, fallback: str | None = None) -> str:
    """The title a writer gave, repaired or replaced when it looks wrong.

    Rejected: a token that looks like an id (a hex or digit run) the source does not have; a title cut off or ending on a short
    word; a capitalized name the source does not have while a close spelling of it is there (replaced by the source spelling when
    there is one such, rejected when there are several). A rejected title becomes the document's heading, else the file name
    without its id-like suffix, else `fallback`, else the title as it is.
    """
    title = " ".join(title.split())
    haystack = source_text.lower()
    words = title.split()
    last = words[-1].strip(".,;:()") if words else ""
    bad = not title or any(_id_like(w, haystack) for w in words) or bool(_TITLE_END.search(title))
    bad = bad or (len(words) > 1 and last.islower() and (len(last) <= 2 or last in _SHORT_WORDS))
    if not bad:
        names = list(dict.fromkeys(_names(source_text) + _CODE.findall(source_text)))
        repaired = []
        for word in words:
            core = word.strip(".,;:()\"'")
            if len(core) >= 3 and core[0].isupper() and core.lower() not in haystack:
                fixed = _name_repair(core, names)
                if fixed is None:
                    bad = True
                    break
                word = word.replace(core, fixed)
            repaired.append(word)
        title = " ".join(repaired)
    if not bad:
        return title
    return heading_of(source_text) or file_title(file_name) or fallback or title


# -- body ----------------------------------------------------------------------------------


def wrapper_free(text: str) -> str:
    """A reply without the tags of the prompt around it (`<note>`, `<source>`, `<document>`) and without an outer code fence,
    a missing closing fence included."""
    text = text.strip()
    for _ in range(3):
        before = text
        text = _CLOSE_TAG.sub("", _OPEN_TAG.sub("", text, count=1), count=1).strip()
        if m := re.match(r"\A\s*```[\w-]*[ \t]*\n(.*?)\n?```\s*\Z", text, re.DOTALL):
            text = m.group(1).strip()
        elif _OPEN_FENCE.match(text):
            text = _CLOSE_FENCE.sub("", _OPEN_FENCE.sub("", text, count=1)).strip()
        if text == before:
            break
    return text


def exact_items(text: str, cap: int | None = None) -> list[str]:
    """The strings a rewrite must keep as they are: URLs, e-mail addresses, file paths, code spans and identifiers (with an
    underscore, inner capitals, or letters and digits), in the order of the text; the first `cap` when given."""
    found: dict[str, None] = {}
    for m in _ITEM.finditer(text):
        item = (m.group(1) if m.group().startswith("`") else m.group()).strip()
        if len(item) > 2 and not re.fullmatch(r"[\d/.,-]+", item):
            found.setdefault(item)
    items = [i for i in found if not any(i != j and i in j for j in found)]
    return items[:cap]


def items_rule(items: list[str]) -> str:
    """The line of the body prompt that lists the items; "" when there are none."""
    if not items:
        return ""
    return "\n- Copy these exactly, character for character:" + "".join(f"\n  - {i}" for i in items)


_TAG = re.compile(r"</?([a-z][a-z_]*)>")


def without_stray_tags(body: str, source_text: str) -> str:
    """The body without the bare tags (`<content>`, `</source>`) a small writer leaves inside it, when the source has no such tag."""
    return _TAG.sub(lambda m: m.group() if m.group() in source_text else "", body)


def _letters(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def altered(body: str, source_text: str) -> list[str]:
    """The items in the body that the source does not have: a string of the closed classes that was changed or invented.
    Not counted: a lone `/word` (the rest of a tag), a span of several words (prose, not a code), and an item whose letters
    and digits are in the source in the same order (`neq0` from a formula `\\neq 0`, a path written with other separators)."""
    letters = _letters(source_text)
    return [
        i
        for i in exact_items(body)
        if i not in source_text and not re.fullmatch(r"/\w+", i) and i.count(" ") <= 3 and _letters(i) not in letters
    ]


# a quotation opens after a space or a bracket and starts with a word; a straight quote also closes before a space or a mark
_QUOTED = re.compile(
    r'(?<![\w"])“(\w[^”\n]{10,398})”'
    r'|(?<![\w"])"(\w[^"\n]{10,398}[^\s"])"(?=[\s.,;:!?)\]]|\Z)'
    r'|(?<![\w"])«\s?(\w[^»\n]{10,398})»'
)


def misquoted(body: str, source_text: str) -> list[str]:
    """The quotations of four words or more in the body whose words the source does not have in that order: a quote that was
    changed, made up or moved to another speaker. Letters and digits are compared, so spacing, punctuation and quote marks
    do not count."""
    letters = _letters(source_text)
    spans = (next(g for g in m.groups() if g) for m in _QUOTED.finditer(body))
    return [q for q in spans if len(q.split()) >= 4 and _letters(q) not in letters]


def restored(body: str, source_text: str) -> str:
    """The body with each altered item put back to the source's near-identical item (small edit distance)."""
    wrong = altered(body, source_text)
    if not wrong:
        return body
    ours = exact_items(source_text)
    for item in wrong:
        near = difflib.get_close_matches(item, ours, n=1, cutoff=ITEM_CLOSE)
        if near:
            body = body.replace(item, near[0])
    return body


_SECTION = re.compile(r"^(?=#{1,6}\s)", re.MULTILINE)


def _flat(text: str) -> str:
    return " ".join(text.lower().split())


def without_repeated_sections(body: str, source_text: str) -> str:
    """The body without a section whose heading and content repeat an earlier one, unless the source repeats the heading."""
    parts = _SECTION.split(body)
    if len(parts) < 3:
        return body
    seen: set[str] = set()
    source = _flat(source_text)
    kept = []
    for part in parts:
        key = _flat(part)
        heading = _flat(part.splitlines()[0]) if part.strip() else ""
        if part.lstrip().startswith("#") and key in seen and source.count(heading) <= 1:
            continue
        seen.add(key)
        kept.append(part)
    return "".join(kept).strip()


_NOT_PROSE = re.compile(r"\s*(?:#|[-*+]\s|\d+[.)]\s|>|\||```|~~~|<)")


def first_sentence(body: str) -> str:
    """The first sentence of the body's first line of prose (with letters; no heading, rule, list item, quote, table row, tag, code);
    without one, of its first line that is not a heading, with the list or quote mark taken off."""
    lines, fence = [], False
    for ln in body.splitlines():
        if ln.lstrip().startswith(("```", "~~~")):
            fence = not fence
        elif ln.strip() and not fence:
            lines.append(ln)
    line = next((ln for ln in lines if not _NOT_PROSE.match(ln) and re.search(r"[^\W\d_]", ln)), "")
    if not line:
        line = next((ln for ln in lines if not ln.lstrip().startswith("#")), "")
        line = re.sub(r"^\s*(?:[-*+>]|\d+[.)])\s*", "", line)
    return re.split(r"(?<=[.!?])\s", " ".join(line.split()), maxsplit=1)[0][:200]


def echoes(body: str, instruction: str, context: str, run: int = 6) -> bool:
    """Whether `body` copies `run` words in a row of `instruction` that `context` (what the note is written from) lacks:
    a small writer that repeats the prompt's own request instead of doing it."""
    words = re.findall(r"\w+", instruction.lower())
    have, seen = " ".join(re.findall(r"\w+", body.lower())), " ".join(re.findall(r"\w+", context.lower()))
    return any((g := " ".join(words[i : i + run])) in have and g not in seen for i in range(len(words) - run + 1))
