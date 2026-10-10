# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Task 9: a real corpus from the GitLab handbook (https://gitlab.com/gitlab-com/content-sites/handbook, CC BY-SA 4.0).

    git clone --filter=blob:none --no-checkout https://gitlab.com/gitlab-com/content-sites/handbook.git REPO
    python benchmarks/t9_handbook_select.py --repo REPO --head 2c77a1f5b8c8a80cb7b5151ff11cfad84f98bfbb --out DIR

The subtree content/handbook/marketing. Pages first added from 2024-01-01 on, at most N (seeded sample, even over time),
each as its first version. A real quarter is a simulated month (manifest: 2024Q1 -> "2024-01" ... so the health has enough
pages a month). Labels: the folder the page was added in. Seed: the folders (and their _index.md titles/descriptions) of
the subtree at 2024-01-01. Events: every rename (git mv) of a page in the window, with its quarter, old and new folder.
Nothing of the handbook is committed: only this script, the URL and the head commit.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
from pathlib import Path

SUB = "content/handbook/marketing"
SINCE = "2024-01-01"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout


def quarter_month(date: str) -> str:
    y, m = date[:4], int(date[5:7])
    return f"{y}-{(m - 1) // 3 * 3 + 1:02d}"


def folder_of(path: str) -> str:
    rel = path[len(SUB) :].strip("/")
    parent = rel.rpartition("/")[0]
    return "/" + parent if parent else "/"


def front(text: str) -> tuple[dict[str, str], str]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {k.strip(): v.strip().strip("\"'") for k, _, v in (line.partition(":") for line in m.group(1).splitlines()) if v}
    return meta, m.group(2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--seed", type=int, default=9)
    a = ap.parse_args()
    log = git(
        a.repo,
        "log",
        a.head,
        "--reverse",
        "--diff-filter=AR",
        "-M100%",
        "--name-status",
        "--format=@%H %ad",
        "--date=short",
        f"--since={SINCE}",
        "--",
        SUB,
    )
    adds, events, commit, date = [], [], "", ""
    for line in log.splitlines():
        if line.startswith("@"):
            commit, date = line[1:].split()
        elif line.startswith("A") and line.endswith(".md") and not line.endswith("_index.md"):
            adds.append({"path": line.split("\t")[1], "commit": commit, "date": date})
        elif line.startswith("R") and line.endswith(".md"):
            _, old, new = line.split("\t")
            if folder_of(old) != folder_of(new):
                events.append({"date": date, "month": quarter_month(date), "from": folder_of(old), "to": folder_of(new), "page": new})
    random.Random(a.seed).shuffle(adds)
    adds = sorted(adds[: a.n], key=lambda x: (x["date"], x["path"]))
    a.out.mkdir(parents=True, exist_ok=True)
    manifest, labels = {}, {}
    for i, add in enumerate(adds, 1):
        text = git(a.repo, "show", f"{add['commit']}:{add['path']}")
        meta, body = front(text)
        title = meta.get("title") or Path(add["path"]).stem.replace("-", " ")
        name = f"{i:03d}-{Path(add['path']).stem[:50]}.md"
        (a.out / name).write_text(f"# {title}\n\n{body.strip()}\n", encoding="utf-8")
        manifest[name] = quarter_month(add["date"])
        labels[name] = {"folder": folder_of(add["path"]), "date": add["date"], "path": add["path"], "commit": add["commit"]}
    seed_commit = git(a.repo, "rev-list", "-1", f"--before={SINCE}", a.head).strip()
    seed = []
    for p in git(a.repo, "ls-tree", "-r", "--name-only", seed_commit, SUB).splitlines():
        if p.endswith("_index.md"):
            meta, _ = front(git(a.repo, "show", f"{seed_commit}:{p}"))
            label = folder_of(p.rpartition("/")[0] + "/x.md")
            if label != "/":
                seed.append({"folder": label, "title": meta.get("title", ""), "description": meta.get("description", "")})
    seed.sort(key=lambda s: s["folder"])
    for f, v in (("manifest.json", manifest), ("labels.json", labels), ("events.json", events), ("seed.json", seed)):
        (a.out / f).write_text(json.dumps(v, indent=2, ensure_ascii=False), encoding="utf-8")
    source = {
        "url": "https://gitlab.com/gitlab-com/content-sites/handbook",
        "head": a.head,
        "seed_commit": seed_commit,
        "subtree": SUB,
        "since": SINCE,
        "n": a.n,
        "seed": a.seed,
        "license": "CC BY-SA 4.0",
    }
    (a.out / "source.json").write_text(json.dumps(source, indent=2), encoding="utf-8")
    print(f"{len(adds)} pages, {len(events)} folder-changing renames, {len(seed)} seed folders; months {sorted(set(manifest.values()))}")


if __name__ == "__main__":
    main()
