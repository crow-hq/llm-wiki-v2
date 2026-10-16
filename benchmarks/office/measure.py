"""The free check of a change to the Office/ODF readers: what two versions of the library make of the same files.

usage: python measure.py <src before> <src after> <folder or file>...

<src> is a library source folder (the `src` of a checkout, e.g. `git worktree add ../before main`). Files are the
.docx/.pptx/.xlsx/.odt/.ods under the folders (fetch.py writes them), each counted once by content. No model is
called. Printed, before -> after: files that fail, sheets with binary float noise ("5.5100000000000003E-2"), sheets
with a caption line, sheet sources (names must not change), long documents whose own summary is found and those
with an outline, and characters per format.
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter
from pathlib import Path

SUFFIXES = {".docx", ".pptx", ".xlsx", ".odt", ".ods"}
NOISY = re.compile(r"\d\.\d{12,}|\dE[-+]\d")
NUMBER = re.compile(r"[-\d.eE+]+%?")
LONG = 6_000  # characters past which a document is summarized in parts and its own summary or outline counts


def files(args: list[str]) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for a in args:
        p = Path(a)
        for f in sorted(p.rglob("*") if p.is_dir() else [p]):
            if f.suffix.lower() in SUFFIXES and f.is_file():
                data = f.read_bytes()
                out.setdefault(hashlib.sha256(data).hexdigest(), data)
    return out


def outputs(src: str, data: dict[str, bytes]) -> tuple[dict[str, tuple[str, object]], object]:
    """sha256 -> (kind, Markdown or [(sheet, source text)] or the exception), read by the library in `src`."""
    for name in [m for m in sys.modules if m == "llmw2" or m.startswith("llmw2.")]:
        del sys.modules[name]
    sys.path.insert(0, src)
    try:
        from llmw2.agents import state
        from llmw2.bundle import files as fm
        from llmw2.bundle import formats

        out: dict[str, tuple[str, object]] = {}
        for h, d in data.items():
            try:
                k = formats.kind(d)
            except Exception as e:  # not an Office/ODF package, or one past the size limits: a failure of the type "?"
                out[h] = ("?", e)
                continue
            try:
                out[h] = (k, fm.sheet_sources(d, f"{h[:16]}.{k}") if k in ("xlsx", "ods") else formats.markdown(d))
            except Exception as e:
                out[h] = (k, e)
        return out, state
    finally:
        sys.path.remove(src)


def main() -> None:
    before_src, after_src, *where = sys.argv[1:]
    data = files(where)
    before, _ = outputs(before_src, data)
    after, state = outputs(after_src, data)
    kinds = Counter(k for k, _ in after.values())
    print(f"files: {len(after)} {dict(sorted(kinds.items()))} (\"?\": not recognised as an Office/ODF file)")

    def failed(res: dict[str, tuple[str, object]]) -> Counter[str]:
        return Counter(k for k, v in res.values() if isinstance(v, Exception))

    print(f"failures: {dict(failed(before))} -> {dict(failed(after))}")
    new = [h for h in after if isinstance(after[h][1], Exception) and not isinstance(before.get(h, ("", None))[1], Exception)]
    print(f"new failures: {len(new)}", *(f"\n  {h[:16]}.{after[h][0]}: {after[h][1]}" for h in new))

    def sheets(res: dict[str, tuple[str, object]]) -> list[tuple[str, str]]:
        return [s for k, v in res.values() if isinstance(v, list) for s in v]

    def captioned(text: str) -> bool:
        return "\nCaption: " in text.split("\n\n", 1)[0]

    def names(res: dict[str, tuple[str, object]], h: str) -> list[str] | None:
        v = res.get(h, ("", None))[1]
        return [n for n, _ in v] if isinstance(v, list) else None

    sb, sa = sheets(before), sheets(after)
    def noisy(text: str) -> bool:  # a percentage to 12 digits ("0.0699645953854%") is a value, not noise
        return any(NOISY.search(m) and not m.endswith("%") for m in NUMBER.findall(text))

    print(f"sheets with float noise: {sum(map(noisy, (t for _, t in sb)))}/{len(sb)} -> {sum(map(noisy, (t for _, t in sa)))}/{len(sa)}"
          f" (with 12-digit percentages: {sum(1 for _, t in sb if NOISY.search(t))} -> {sum(1 for _, t in sa if NOISY.search(t))})")
    print(f"sheets with a caption line: {sum(1 for _, t in sb if captioned(t))} -> {sum(1 for _, t in sa if captioned(t))}")
    renamed = sum(1 for h in after if names(before, h) is not None and names(after, h) is not None and names(before, h) != names(after, h))
    print(f"sheet sources: {len(sb)} -> {len(sa)}; workbooks whose sheet names changed: {renamed}")
    for k in ("xlsx", "ods"):
        c1, c2 = (sum(len(t) for kk, v in res.values() if kk == k and isinstance(v, list) for _, t in v) for res in (before, after))
        print(f"{k}: characters of the sheet sources {c1:,} -> {c2:,} ({100 * (c2 - c1) / max(c1, 1):+.1f}%)")

    for k in ("pptx", "docx", "odt"):
        stats = []
        for res in (before, after):
            texts = [v for kk, v in res.values() if kk == k and isinstance(v, str)]
            long = [state._sections(t) for t in texts if len(t) > LONG]
            native = sum(1 for s in long if state._native(s) is not None)
            outline = sum(1 for s in long if len(s) > 2)
            stats.append((len(long), native, outline, sum(map(len, texts))))
        (n1, a1, b1, c1), (n2, a2, b2, c2) = stats
        print(f"{k}: over {LONG:,} characters {n1} -> {n2}, own summary found {a1} -> {a2}, outline of 2+ headings {b1} -> {b2}; "
              f"characters {c1:,} -> {c2:,} ({100 * (c2 - c1) / max(c1, 1):+.1f}%)")


if __name__ == "__main__":
    main()
