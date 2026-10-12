# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Filing errors per 150 short documents: where the runs of `stability.py` put each document, against the labels and
the blind judgements of readers. Free: no model is called.

    python benchmarks/filing_error.py score ARM=RUNS.json [ARM=RUNS.json ...] [--models m1,m2,m3]
    python benchmarks/filing_error.py cases OUT RUNS.json=KEEP [RUNS.json=KEEP ...]
    python benchmarks/filing_error.py merge OUT ANSWERS.json [ANSWERS.json ...]

`score` reads the raw JSON of `stability.py --out` run on the corpora `growth`, `t9a` and `t9b` (150 documents). A `{m}`
in a path is replaced by each model of `--models`, and `a.json+b.json` pools the runs of two files. Per run it counts:

- E1 wrong area: a document that is not "new", in a folder that is not a gold folder, not below one and not above one;
- E2 too high: a document that is not "new", in an ancestor of its gold folder (a more specific folder existed);
- E3 new subject misfiled: a "new" document left in a folder that existed before the run and is not one of its
  `alt_folders`;
- E4 superfluous folder: a folder created in the run that ends with one document, when that document is not "new";
- E5 merge: an "update" document not merged where it should be, plus a document merged where it may not be;
- E6 out of place: in each folder created in the run that ends with two or more documents, those that are not about
  the folder's main specific subject (documents already counted in E1 or E2 are not counted again);

and, apart, the routing decisions taken by the LLM fallback. Not errors: a document filed below its gold folder (more
specific, same branch), and a new subject in a new folder or in one of its `alt_folders`.

Readers decide what the labels cannot. An E1 or E3 document stays an error only where blind readers would not look for
it in that folder, and E6 is entirely their judgement. The judgements are kept in `benchmarks/judgements/` by document
and folder (E1, E3) or by set of documents (E6), so they are reused by every run, arm and model that makes the same
choice. A choice not judged yet is counted as an error for E1 and E3 and not counted for E6, and the table says how
many there are: until they are judged, E1 and E3 are upper bounds and E6 is a lower bound.

To judge them: `cases` writes the choices not judged yet, shuffled, as `OUT/readers.json` and `OUT/purity.json` (what a
judge reads: the folder, its description, its subfolders, the notes next to the document, or the documents of a
folder, each as "title: description" read from the wikis kept by `stability.py --keep KEEP`), and `OUT/key.json` (which
choice each case is; a judge never reads it). Give each case file to two fresh models, separately, with the prompt of
`judgements/reader-prompt.md` or `judgements/purity-prompt.md` (fill `{cases}` and `{answers}`). `merge` adds their
answers: a document is out of place when either judge says so. The labels of 17 documents were completed by a blind
adjudication (`judgements/labels-adjudicated.json`: acceptable folders and merges added, nothing removed).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import stability as S
import yaml

HERE = Path(__file__).resolve().parent
CORPORA = [HERE / "corpus" / name for name in ("growth", "t9a", "t9b")]
JUDGED = HERE / "judgements"
READERS, PURITY, ADJUDICATED = JUDGED / "readers.json", JUDGED / "purity.json", JUDGED / "labels-adjudicated.json"
PARTS = ["E1 area", "E2 high", "E3 new", "E4 superfluous", "E5 merge", "E6 out of place"]
MODELS = "deepseek/deepseek-v4-flash,z-ai/glm-5.3-flash,qwen/qwen3.8-flash"
INDEX_LINE = re.compile(r"^\* \[(.*?)\]\((.*?)\)(?: - (.*))?$")


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def labels() -> dict[str, dict[str, Any]]:
    """The labels of the three corpora, with the adjudicated alternatives added."""
    out: dict[str, dict[str, Any]] = {}
    for corpus in CORPORA:
        out.update(S.corpus_labels(corpus))
    for doc, fix in load(ADJUDICATED).items():
        lab = out[doc] = dict(out[doc])
        lab["folders"] = list(lab["folders"]) + fix.get("extra_folders", [])
        if target := fix.get("extra_merge"):
            if "/" in target:
                lab["merge_ok"] = [*S.names(lab.get("merge_ok")), target]
            else:
                lab["merge_with_doc"] = target
    return out


def below(a: str, b: str) -> bool:
    """Whether folder a is strictly below folder b."""
    return a.startswith(b.rstrip("/") + "/")


def off_branch(lab: dict[str, Any], folder: str) -> bool:
    """A document that is not "new" filed outside its gold branch (not in, below or above a gold folder): E1 by the labels."""
    gold = lab["folders"]
    return folder not in gold and not any(below(folder, g) or below(g, folder) for g in gold)


def misfiled_new(lab: dict[str, Any], folder: str, before: set[str]) -> bool:
    """A "new" document left in a folder that existed before the run: E3 by the labels."""
    return folder in before and folder not in lab.get("alt_folders", [])


def shared_folders(run: dict[str, Any]) -> dict[str, list[str]]:
    """The folders created in the run that hold two or more documents, with their documents (sorted)."""
    before, by = set(run["existing_folders"]), {}
    for name, d in run["docs"].items():
        if d["folder"] not in before and d["folder"] != "!error":
            by.setdefault(d["folder"], []).append(name)
    return {f: sorted(members) for f, members in by.items() if len(members) >= 2}


def errors(run: dict[str, Any], labs: dict[str, dict[str, Any]], readers: dict[str, int], purity: dict[str, list[int]]) -> Counter:
    docs, before = run["docs"], set(run["existing_folders"])
    e: Counter = Counter()
    counted = set()  # documents already in E1 or E2: E6 does not count them again
    for name, d in docs.items():
        lab, folder = labs.get(name), d["folder"]
        if not lab:
            continue
        if lab["kind"] == "new":
            if misfiled_new(lab, folder, before):
                judged = readers.get(f"{name}|{folder}")
                e["E3 new"] += 1 if judged is None else 1 - judged
                e["not judged"] += judged is None
            continue
        if not off_branch(lab, folder):
            gold = lab["folders"]
            if folder not in gold and not any(below(folder, g) for g in gold):  # above a gold folder only
                e["E2 high"] += 1
                counted.add(name)
            continue
        judged = readers.get(f"{name}|{folder}")
        if judged == 1:  # readers would look for it there: not an error, and it may still be out of place in E6
            continue
        e["E1 area"] += 1
        e["not judged"] += judged is None
        counted.add(name)
    size = Counter(d["folder"] for d in docs.values() if d["folder"] not in before)
    for folder, n in size.items():
        if n == 1:
            name = next(k for k, d in docs.items() if d["folder"] == folder)
            e["E4 superfluous"] += labs.get(name, {}).get("kind") != "new"
    acc = S.accuracy(run, labs)
    updates = sum(labs[n]["kind"] == "update" for n in docs if n in labs)
    others = sum(labs[n]["kind"] != "update" for n in docs if n in labs)
    e["E5 merge"] += round((1 - acc["merge_accuracy"]) * updates + acc["false_merge_rate"] * others)
    for members in shared_folders(run).values():
        verdict = purity.get(",".join(members))
        if verdict is None:
            e["not judged"] += 1
            continue
        e["E6 out of place"] += sum(1 - b for n, b in zip(members, verdict, strict=True) if n not in counted)
    e["fallbacks"] = sum(bool((d.get("route") or {}).get("fallback")) for d in docs.values())
    return e


def runs_of(path: Path) -> list[dict[str, Any]]:
    data = load(path)
    return [] if not data or "stopped" in data else [r for v in data["variants"].values() for r in v["runs"]]


def _mean(counts: list[Counter], key: str) -> float:
    return sum(c[key] for c in counts) / len(counts)


def score(arms: list[str], models: list[str]) -> None:
    labs, readers, purity = labels(), load(READERS), load(PURITY)
    print("| model | arm | errors/150 (runs) | " + " | ".join(PARTS) + " | fallbacks | not judged |")
    print("|---|---|---|" + "---|" * len(PARTS) + "---|---|")
    for model in models:
        short = model.rsplit("/", 1)[-1]
        for arm in arms:
            name, pattern = arm.split("=", 1)
            runs = [r for p in pattern.split("+") for r in runs_of(Path(p.format(m=short)))]
            if not runs:
                continue
            counts = [errors(r, labs, readers, purity) for r in runs]
            totals = [sum(c[p] for p in PARTS) for c in counts]
            print(
                f"| {short} | {name} | {sum(totals) / len(totals):.1f} ({', '.join(f'{t:g}' for t in totals)}) | "
                + " | ".join(f"{_mean(counts, p):.1f}" for p in PARTS)
                + f" | {_mean(counts, 'fallbacks'):.1f} | {_mean(counts, 'not judged'):.1f} |"
            )


# -- blind cases ----------------------------------------------------------------------------------------------------


def _index(wiki: Path, folder: str) -> tuple[list[str], list[str]]:
    """The subfolders ("name: description") and the note titles of a folder's index."""
    f = wiki / "index.md" if folder == "/" else wiki / folder.strip("/") / "index.md"
    subs, notes, section = [], [], ""
    for line in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
        if line.startswith("# "):
            section = line[2:].strip().lower()
            continue
        m = INDEX_LINE.match(line.strip())
        if m and section.startswith("subdir"):
            subs.append(f"{m.group(2).split('/')[0]}: {(m.group(3) or '').strip()}")
        elif m and section.startswith("note"):
            notes.append(m.group(1))
    return subs, notes


def _description(wiki: Path, folder: str) -> str:
    if folder == "/":
        return "The root of the wiki."
    parent, name = folder.rsplit("/", 1)
    return next((s.split(": ", 1)[1] for s in _index(wiki, parent or "/")[0] if s.split(": ", 1)[0] == name), "")


def _note_line(wiki: Path, rel: str | None, name: str) -> str:
    """A note as "title: description", from its frontmatter; the document's file name when the note is not there."""
    if not rel or not (wiki / rel).exists():
        return name
    text = (wiki / rel).read_text(encoding="utf-8")
    meta = (yaml.safe_load(text[3 : text.find("\n---", 3)]) if text.startswith("---") else {}) or {}
    return f"{meta.get('title') or Path(rel).stem}: {meta.get('description') or ''}"


def cases(out: Path, pairs: list[str]) -> None:
    labs, readers, purity = labels(), load(READERS), load(PURITY)
    reader_cases: dict[str, dict[str, Any]] = {}
    purity_cases: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        runs_file, keep = (Path(p) for p in pair.split("=", 1))
        data = load(runs_file)
        for variant, v in data["variants"].items():
            for i, run in enumerate(v["runs"]):
                wiki, before = keep / variant / f"run{i + 1}", set(run["existing_folders"])
                for name, d in run["docs"].items():
                    lab, folder = labs.get(name), d["folder"]
                    key = f"{name}|{folder}"
                    if not lab or key in readers or key in reader_cases:
                        continue
                    if not (misfiled_new(lab, folder, before) if lab["kind"] == "new" else off_branch(lab, folder)):
                        continue
                    subs, notes = _index(wiki, folder)
                    own = _note_line(wiki, d.get("note"), name)
                    reader_cases[key] = {
                        "folder": folder,
                        "folder_description": _description(wiki, folder),
                        "subfolders": subs,
                        "other_notes_in_folder": [t for t in notes if not own.startswith(t)][:40],
                        "document": own,
                    }
                for folder, members in shared_folders(run).items():
                    key = ",".join(members)
                    if key not in purity and key not in purity_cases:
                        docs = [_note_line(wiki, run["docs"][n].get("note"), n) for n in members]
                        purity_cases[key] = {"parent": folder.rsplit("/", 1)[0] or "/", "documents": docs}
    out.mkdir(parents=True, exist_ok=True)
    key_file: dict[str, dict[str, str]] = {}
    for kind, todo, prefix in (("readers", reader_cases, "e"), ("purity", purity_cases, "c")):
        keys = sorted(todo)
        random.Random(len(keys)).shuffle(keys)
        ids = {f"{prefix}{i:03d}": k for i, k in enumerate(keys)}
        key_file[kind] = ids
        (out / f"{kind}.json").write_text(
            json.dumps([{"id": i, **todo[k]} for i, k in ids.items()], indent=1, ensure_ascii=False), encoding="utf-8"
        )
    (out / "key.json").write_text(json.dumps(key_file, indent=1), encoding="utf-8")
    print(f"{len(reader_cases)} reader cases, {len(purity_cases)} purity cases in {out}")


def merge(out: Path, answer_files: list[str]) -> None:
    """Add judges' answers to the stores; where two judges disagree, the stricter answer stays."""
    key = load(out / "key.json")
    readers, purity = load(READERS), load(PURITY)
    added = Counter()
    for f in answer_files:
        for a in json.loads(Path(f).read_text(encoding="utf-8")):
            if a["id"] in key.get("readers", {}):
                k, value = key["readers"][a["id"]], int(bool(a["acceptable"]))
                added["readers"] += k not in readers
                readers[k] = min(readers.get(k, 1), value)
            elif a["id"] in key.get("purity", {}):
                k = key["purity"][a["id"]]
                n = len(k.split(","))
                b = [int(x) for x in a["belongs"][:n]] + [1] * max(0, n - len(a["belongs"]))
                added["purity"] += k not in purity
                purity[k] = [min(x, y) for x, y in zip(purity[k], b, strict=True)] if k in purity else b
    READERS.write_text(json.dumps(readers, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    PURITY.write_text(json.dumps(purity, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    print(f"added {added['readers']} reader and {added['purity']} purity judgements")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("score", help="the error table of some arms")
    s.add_argument("arms", nargs="+", metavar="ARM=RUNS.json", help="an arm's name and the raw JSON of its runs ({m}: the model)")
    s.add_argument("--models", default=MODELS, help=f"comma-separated, the {{m}} of the paths (default {MODELS})")
    c = sub.add_parser("cases", help="the choices not judged yet, as blind cases")
    c.add_argument("out", type=Path)
    c.add_argument("pairs", nargs="+", metavar="RUNS.json=KEEP", help="the raw JSON of some runs and their --keep folder")
    m = sub.add_parser("merge", help="add the judges' answers to the stores")
    m.add_argument("out", type=Path, help="the folder `cases` wrote (its key.json)")
    m.add_argument("answers", nargs="+")
    args = p.parse_args(argv)
    if args.command == "score":
        score(args.arms, args.models.split(","))
    elif args.command == "cases":
        cases(args.out, args.pairs)
    else:
        merge(args.out, args.answers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
