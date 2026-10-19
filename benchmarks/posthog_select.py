# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A real corpus from the PostHog handbook (https://github.com/PostHog/posthog.com, `contents/` under the MIT licence).

    git clone --filter=blob:none --no-checkout https://github.com/PostHog/posthog.com.git REPO
    python benchmarks/posthog_select.py --repo REPO --head 2c6eef5ed782311675b28993df3c70fbbcbf2372 --out DIR

The pages of `contents/handbook` as they are at the head commit, in the order they were first added (git history): a seeded
sample of N (default 120, seed 9). Label: the folder the page is in at the head. Left out: pages at the root of the handbook
(they have no folder), `_snippets` and pages over 20 000 bytes. MDX import and export lines are dropped; the rest of the text
is kept as written, under its title as a heading. `DIR/manifest.json` gives the source, the head, the parameters and, for
each page, its file, folder (the gold of `incremental_score.py --gold`), first-added date, path in the repository and the
sha256 of the file written. Nothing of the handbook is committed: only this script, the URL and the head commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
from pathlib import Path

URL = "https://github.com/PostHog/posthog.com"
SUB = "contents/handbook"
MAX_BYTES = 20_000


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


def front(text: str) -> tuple[dict[str, str], str]:
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {k.strip(): v.strip().strip("\"'") for k, _, v in (line.partition(":") for line in m.group(1).splitlines()) if v}
    return meta, m.group(2)


def main() -> None:
    ap = argparse.ArgumentParser(description="select the PostHog handbook corpus from a clone")
    ap.add_argument("--repo", type=Path, required=True, help="a clone of the repository (no checkout needed)")
    ap.add_argument("--head", required=True, help="the commit the corpus is read at")
    ap.add_argument("--out", type=Path, required=True, help="where the .md files and manifest.json go")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--seed", type=int, default=9)
    a = ap.parse_args()
    sizes = {}
    for line in git(a.repo, "ls-tree", "-r", "-l", a.head, SUB).splitlines():
        meta, path = line.split("\t")
        if re.search(r"\.mdx?$", path):
            sizes[path] = int(meta.split()[3])
    first, date = {}, ""
    added = git(a.repo, "log", a.head, "--reverse", "--diff-filter=A", "--name-only", "--format=@%ad", "--date=short", "--", SUB)
    for line in added.splitlines():
        if line.startswith("@"):
            date = line[1:]
        elif line in sizes:
            first.setdefault(line, date)
    ok = sorted(p for p, s in sizes.items() if "/" in p[len(SUB) + 1 :] and "/_snippets/" not in p and s <= MAX_BYTES and p in first)
    random.Random(a.seed).shuffle(ok)
    pick = sorted(ok[: a.n], key=lambda p: (first[p], p))
    a.out.mkdir(parents=True, exist_ok=True)
    docs = []
    for i, p in enumerate(pick, 1):
        meta, body = front(git(a.repo, "show", f"{a.head}:{p}"))
        body = "\n".join(line for line in body.splitlines() if not re.match(r"^(import|export)\s", line)).strip()
        stem = Path(p).stem if Path(p).stem != "index" else Path(p).parent.name
        title = meta.get("title") or stem.replace("-", " ")
        name = f"{i:03d}-{stem[:50]}.md"
        text = f"# {title}\n\n{body}\n"
        (a.out / name).write_text(text, encoding="utf-8")
        folder = "/" + p[len(SUB) + 1 :].rpartition("/")[0]
        docs.append({"file": name, "folder": folder, "date": first[p], "path": p, "sha256": hashlib.sha256(text.encode()).hexdigest()})
    manifest = {
        "url": URL,
        "head": a.head,
        "subtree": SUB,
        "n": a.n,
        "seed": a.seed,
        "max_bytes": MAX_BYTES,
        "license": "MIT (contents/)",
        "docs": docs,
    }
    (a.out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"{len(docs)} pages of {len(ok)} eligible, {len({d['folder'] for d in docs})} folders")


if __name__ == "__main__":
    main()
