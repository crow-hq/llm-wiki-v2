"""Write the synthetic second-brain corpus (`benchmarks/corpus/synthetic-brain/`): a personal notes export and the wiki it is.

    python benchmarks/make_brain_corpus.py --source DIR   # copy a synthetic-brain export (notes/, gold.json, TAXONOMY.md) in, then build
    python benchmarks/make_brain_corpus.py                # build base/ again from docs/, gold.json and TAXONOMY.md (free, no model)
    python benchmarks/make_brain_corpus.py --check        # verify the corpus, free, exit 1 on a defect

`docs/` is the export as written: 477 notes in their folders, no frontmatter, links as URL-encoded relative paths or
`note://` ids. `base/` is the same brain as an OKF wiki, the reference organization: every note in its gold folder, under
the slug of its title, with `type`, `id`, `title` and a `description` (its first line of text); links to notes of the set
point to the new paths, `note://` links stay; the gold links of a note are its See-also entries; folder descriptions come
from TAXONOMY.md. No model writes anything, and the ids are hashes of the export paths, so a build is the same every time.
`gold.json` gives each export path its folder, title, links and `wiki`, the path of the note in `base/`.
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import posixpath
import re
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from llmw2.bundle.check import check as check_bundle  # noqa: E402
from llmw2.bundle.document import OKFDocument  # noqa: E402
from llmw2.bundle.store import WikiStore  # noqa: E402
from llmw2.bundle.tree import join_see_also, rel_link, slugify  # noqa: E402

OUT = HERE / "corpus" / "synthetic-brain"
ACTOR = "benchmarks/make_brain_corpus.py"
SUMMARY_CHARS = 180

_FOLDER = re.compile(r"^( *)- \*\*(.+?)/\*\* \(\d+\) — (.+)$")  # TAXONOMY.md: "  - **name/** (n) — description"
_DOC_LINK = re.compile(r"\]\(([^)\s]+\.md)\)")


def folder_descriptions(taxonomy: str) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    stack: list[str] = []
    for line in taxonomy.splitlines():
        if m := _FOLDER.match(line):
            stack[len(m[1]) // 2 :] = [m[2]]
            descriptions["/".join(stack)] = m[3].strip()
    return descriptions


def wiki_paths(gold: dict[str, dict]) -> dict[str, str]:
    """Export path -> wiki path: the gold folder, the slug of the title, -2, -3… when two titles share a slug there."""
    paths: dict[str, str] = {}
    taken: set[str] = set()
    for old, meta in sorted(gold.items()):
        base = slugify(meta["title"])
        slug, n = base, 2
        while f"{meta['folder']}/{slug}" in taken:
            slug, n = f"{base}-{n}", n + 1
        taken.add(f"{meta['folder']}/{slug}")
        paths[old] = f"{meta['folder']}/{slug}.md"
    return paths


def summary(body: str, title: str) -> str:
    """The first line of text of a note, without its Markdown, cut at a word; the title when there is none."""
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or set(line) <= set("-*_=|` "):
            continue
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"^([-*+]|\d+\.)\s+(\[[ xX]\]\s*)?", "", line)
        line = re.sub(r"[*_`>]", "", line).strip()
        if len(line) > 3:
            return line if len(line) <= SUMMARY_CHARS else line[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
    return title


def build(out: Path, dest: Path) -> dict[str, dict]:
    """Write the wiki of `out`'s docs/ into `dest` (empty or missing); the gold notes with their `wiki` path."""
    gold = json.loads((out / "gold.json").read_text(encoding="utf-8"))["notes"]
    paths = wiki_paths(gold)
    docs: dict[str, tuple[dict, str]] = {}
    for old, meta in gold.items():
        body = (out / "docs" / old).read_text(encoding="utf-8")
        rel = paths[old]

        def fix(m: re.Match[str], old_dir: str = posixpath.dirname(old), rel: str = rel) -> str:
            target = posixpath.normpath(posixpath.join(old_dir, unquote(m[1])))
            return f"]({rel_link(posixpath.dirname(rel), paths[target])})" if target in paths else m[0]

        body = _DOC_LINK.sub(fix, body).rstrip() + "\n"
        note_id = hashlib.sha256(old.encode("utf-8")).hexdigest()[:12]
        docs[old] = ({"type": "Note", "id": note_id, "title": meta["title"], "description": summary(body, meta["title"])}, body)
    for old, meta in gold.items():
        fm, body = docs[old]
        rel = paths[old]
        entries = [
            f"* [{' '.join(docs[t][0]['title'].split()).replace('[', '(').replace(']', ')')}]"
            f"({rel_link(posixpath.dirname(rel), paths[t])}) - {docs[t][0]['description']}"
            for t in meta["links"]
            if t in paths and t != old
        ]
        if entries:
            body = join_see_also(body, list(dict.fromkeys(entries)))
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(OKFDocument(fm, body).serialize(), encoding="utf-8")
    store = WikiStore(dest, actor=ACTOR)
    store.init()
    (dest / "raw").rmdir()  # empty: git would not keep it
    root = store.load()
    descriptions = folder_descriptions((out / "TAXONOMY.md").read_text(encoding="utf-8"))
    for folder in root.walk():
        folder.description = descriptions.get(folder.rel, folder.description)
    for folder in root.walk():
        store.write_index(folder)
    return {old: {**meta, "wiki": paths[old]} for old, meta in gold.items()}


def write(out: Path) -> None:
    shutil.rmtree(out / "base", ignore_errors=True)
    gold = build(out, out / "base")
    (out / "gold.json").write_text(json.dumps({"notes": gold}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(gold)} notes -> {out / 'base'}")


def check(out: Path) -> list[str]:
    defects = [f"base/{p}" for p in check_bundle(out / "base")]
    gold = json.loads((out / "gold.json").read_text(encoding="utf-8"))["notes"]
    docs = {p.relative_to(out / "docs").as_posix() for p in (out / "docs").rglob("*.md")}
    defects += [f"docs/{p}: not in gold.json" for p in sorted(docs - gold.keys())]
    defects += [f"gold.json: {p} has no document" for p in sorted(gold.keys() - docs)]
    for old, meta in gold.items():
        wiki = meta.get("wiki", "")
        if posixpath.dirname(wiki) != meta["folder"] or not (out / "base" / wiki).is_file():
            defects.append(f"gold.json: {old} -> {wiki!r} is not a note of base/ in {meta['folder']}")
    with tempfile.TemporaryDirectory() as tmp:
        build(out, Path(tmp))
        cmp = filecmp.dircmp(out / "base", tmp)
        stack = [(cmp, "")]
        while stack:
            d, prefix = stack.pop()
            defects += [f"base/{prefix}{n}: differs from a build of docs/" for n in d.left_only + d.right_only + d.diff_files]
            stack += [(sub, f"{prefix}{name}/") for name, sub in d.subdirs.items()]
    return defects


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--source", type=Path, help="a synthetic-brain export to copy in first: notes/, gold.json, TAXONOMY.md")
    p.add_argument("--check", action="store_true", help="only verify the corpus in --out (free): exit 1 on a defect")
    args = p.parse_args(argv)
    if args.check:
        defects = check(args.out)
        print("\n".join(defects) or "ok")
        return 1 if defects else 0
    if args.source:
        args.out.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(args.out / "docs", ignore_errors=True)
        shutil.copytree(args.source / "notes", args.out / "docs")
        for name in ("gold.json", "TAXONOMY.md"):
            shutil.copy2(args.source / name, args.out / name)
    write(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
