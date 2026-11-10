# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A real corpus of EU legislation from MultiEURLEX (https://huggingface.co/datasets/coastalcph/multi_eurlex, CC BY 4.0), in two languages.

    curl -L -o multi_eurlex.tar.gz https://huggingface.co/datasets/coastalcph/multi_eurlex/resolve/d0318b7f68f43f7d77797437ce172d899254cd0f/data/multi_eurlex.tar.gz
    curl -L -o eurovoc_descriptors.json https://raw.githubusercontent.com/nlpaueb/multi-eurlex/af2da31b54ca4272d880a46aee8dbbf346712e90/data/eurovoc_descriptors.json
    python benchmarks/eurlex_select.py --archive multi_eurlex.tar.gz --names eurovoc_descriptors.json --out DIR

Pin: the dataset revision `d0318b7f68f43f7d77797437ce172d899254cd0f` (the datasets-server cannot read this script-based dataset, so
the archive `data/multi_eurlex.tar.gz` of that revision is read directly; its sha256 goes in the manifest) and, for the readable
names of the labels, `data/eurovoc_descriptors.json` of github.com/nlpaueb/multi-eurlex at `af2da31b54ca4272d880a46aee8dbbf346712e90`.

The documents of the `test` split (EU regulations, directives and decisions) that exist in both Italian and English, whose two
texts are between 300 and 150 000 characters: a seeded sample of N (default 180, seed 9) with at least a third of them over
20 000 characters in both languages (the long ones are taken first, in seeded order; the rest is drawn from all the eligible,
so the folder distribution is the natural one). The same celex ids go into both corpora: `DIR/eurlex-it/` and `DIR/eurlex-en/`,
one `.md` per document named after its celex id, the first line of the text as heading and the rest as written. Reference
folder: the first EUROVOC level-1 label of the document, named with its English description (the same in both corpora).
`DIR/eurlex-<lang>-manifest.json` gives the source, the pin, the parameters and, for each document, its file, folder (the gold
of `incremental_score.py --gold`), celex id and the sha256 of the file written. Nothing of the dataset is committed: only this
script, the URL and the pin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import tarfile
from pathlib import Path

URL = "https://huggingface.co/datasets/coastalcph/multi_eurlex"
REVISION = "d0318b7f68f43f7d77797437ce172d899254cd0f"
NAMES_URL = "https://github.com/nlpaueb/multi-eurlex/blob/af2da31b54ca4272d880a46aee8dbbf346712e90/data/eurovoc_descriptors.json"
LANGS = ("it", "en")
MIN_CHARS = 300
MAX_CHARS = 150_000
LONG_CHARS = 20_000


def rows(archive: Path, split: str):
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            if member.name == f"{split}.jsonl":
                f = tar.extractfile(member)
                assert f is not None
                yield from enumerate(f)
                return
    raise SystemExit(f"{split}.jsonl not found in {archive}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def main() -> None:
    ap = argparse.ArgumentParser(description="select the MultiEURLEX corpora (Italian and English, same documents)")
    ap.add_argument("--archive", type=Path, required=True, help="data/multi_eurlex.tar.gz of the pinned revision")
    ap.add_argument("--names", type=Path, required=True, help="eurovoc_descriptors.json of multi-eurlex (names of the labels)")
    ap.add_argument("--out", type=Path, required=True, help="where eurlex-it/, eurlex-en/ and their manifests go")
    ap.add_argument("--split", default="test")
    ap.add_argument("--n", type=int, default=180)
    ap.add_argument("--seed", type=int, default=9)
    a = ap.parse_args()
    names = json.loads(a.names.read_text(encoding="utf-8"))
    eligible = []  # (celex, row index, characters, label id)
    for i, line in rows(a.archive, a.split):
        d = json.loads(line)
        texts = [d["text"].get(lang) or "" for lang in LANGS]
        labels = d["eurovoc_concepts"]["level_1"]
        if labels and all(MIN_CHARS <= len(t) <= MAX_CHARS for t in texts):
            eligible.append((d["celex_id"], i, min(len(t) for t in texts), labels[0]))
    eligible.sort()
    random.Random(a.seed).shuffle(eligible)
    long = [e for e in eligible if e[2] > LONG_CHARS][: math.ceil(a.n / 3)]
    taken = {e[0] for e in long}
    pick = long + [e for e in eligible if e[0] not in taken][: a.n - len(long)]
    pick.sort()
    wanted = {e[1]: e for e in pick}
    folders = {e[0]: "/" + slug(names[e[3]]["en"]) for e in pick}
    outs = {lang: a.out / f"eurlex-{lang}" for lang in LANGS}
    for o in outs.values():
        o.mkdir(parents=True, exist_ok=True)
    docs: dict[str, list] = {lang: [] for lang in LANGS}
    for i, line in rows(a.archive, a.split):
        if i not in wanted:
            continue
        d = json.loads(line)
        for lang in LANGS:
            first, _, rest = d["text"][lang].strip().partition("\n")
            text = f"# {first.strip()}\n\n{rest.strip()}\n"
            name = f"{d['celex_id']}.md"
            (outs[lang] / name).write_bytes(text.encode("utf-8"))
            sha = hashlib.sha256(text.encode()).hexdigest()
            docs[lang].append({"file": name, "folder": folders[d["celex_id"]], "celex": d["celex_id"], "sha256": sha})
    archive_sha = sha256_file(a.archive)
    for lang in LANGS:
        manifest = {
            "url": URL,
            "revision": REVISION,
            "archive": "data/multi_eurlex.tar.gz",
            "archive_sha256": archive_sha,
            "label_names": NAMES_URL,
            "split": a.split,
            "language": lang,
            "n": a.n,
            "seed": a.seed,
            "min_chars": MIN_CHARS,
            "max_chars": MAX_CHARS,
            "license": "CC BY 4.0",
            "docs": sorted(docs[lang], key=lambda x: x["file"]),
        }
        (a.out / f"eurlex-{lang}-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"{len(pick)} documents of {len(eligible)} eligible, {len(set(folders.values()))} folders")


if __name__ == "__main__":
    main()
