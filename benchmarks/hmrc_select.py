# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A real corpus from the HMRC internal manuals (https://www.gov.uk/hmrc-internal-manuals, Open Government Licence v3.0).

    python benchmarks/hmrc_select.py --snapshot SNAP --out DIR

The source is live and has no revision to pin, so the corpus is read from a local snapshot of the GOV.UK Content API
(`https://www.gov.uk/api/content/hmrc-internal-manuals/<manual>[/<section>]`, the manual list from
`https://www.gov.uk/api/search.json?filter_format=hmrc_manual`). If SNAP has no `crawl.json` the script builds it: at most two
requests a second, User-Agent `llmwiki2-benchmark-research`, one raw JSON per section in `SNAP/<manual>/<section>.json` and
`SNAP/crawl.json` with the date, the parameters, the manuals and the sha256 of every file. Run again on an existing snapshot
it fetches nothing and gives the same corpus, so the snapshot (its date and the sha256 of its files, copied into the manifest)
is the pin. Do not commit it.

Manuals: the manuals of the search API in a seeded order (seed 9); the first M (default 10) whose root has at least 10 top-level
sections. Sections: from each manual root a seeded random walk of the tree (a random section of the frontier is fetched, its
children join the frontier) until K sections (default 300) are fetched or the manual is exhausted. From the fetched sections,
those with a body of 300 to 150 000 characters once converted to text (the contents pages have none) are eligible; a seeded
sample of N (default 180) with at least a third of them over 20 000 characters, if the snapshot has that many (the long ones
are taken first, in seeded order; the rest is drawn from all the eligible, so the folder distribution is the natural one).
Reference folder: the manual (its slug in the URL). Each section is one `.md`: its title as heading, then the body converted
from HTML (headings, paragraphs, lists and tables as text; links keep their text; the list of jump links at the top is dropped).
`DIR/hmrc-manifest.json` gives the source, the snapshot date, the parameters and, for each section, its file, folder (the gold of
`incremental_score.py --gold`), path, last update, the sha256 of the raw JSON and the sha256 of the file written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx

URL = "https://www.gov.uk/hmrc-internal-manuals"
API = "https://www.gov.uk/api/content"
SEARCH = "https://www.gov.uk/api/search.json?filter_format=hmrc_manual&count=500&fields=link&fields=title"
AGENT = "llmwiki2-benchmark-research"
MIN_CHARS = 300
MAX_CHARS = 150_000
LONG_CHARS = 20_000
HEADINGS = {"h1": 2, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


class Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.cur: list[str] = []
        self.prefix = ""
        self.anchors: list[str] = []
        self.in_anchor = False
        self.row: list[str] = []
        self.in_cell = False

    def flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self.cur)).strip()
        self.cur = []
        prefix, self.prefix = self.prefix, ""
        links, self.anchors = "".join(self.anchors), []
        if not text or (links and re.sub(r"\s+", "", text) == re.sub(r"\s+", "", links)):
            return
        self.blocks.append(prefix + text)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in HEADINGS:
            self.flush()
            self.prefix = "#" * HEADINGS[tag] + " "
        elif tag in ("p", "div", "ul", "ol", "table", "blockquote"):
            self.flush()
        elif tag == "li":
            self.flush()
            self.prefix = "- "
        elif tag == "br":
            self.cur.append(" " if self.in_cell else "\n")
        elif tag == "tr":
            self.flush()
            self.row = []
        elif tag in ("td", "th"):
            self.in_cell, self.cur = True, []
        elif tag == "a":
            self.in_anchor = str(dict(attrs).get("href") or "").startswith("#")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self.in_anchor = False
        elif tag in ("td", "th"):
            self.row.append(re.sub(r"\s+", " ", "".join(self.cur)).strip())
            self.cur, self.in_cell = [], False
        elif tag == "tr":
            if any(self.row):
                self.blocks.append("| " + " | ".join(self.row) + " |")
            self.row = []
        elif tag in HEADINGS or tag in ("p", "div", "li", "table", "blockquote"):
            self.flush()

    def handle_data(self, data: str) -> None:
        self.cur.append(data)
        if self.in_anchor:
            self.anchors.append(data)


def html_to_text(html: str) -> str:
    parser = Text()
    parser.feed(html)
    parser.close()
    parser.flush()
    out: list[str] = []
    for block in parser.blocks:  # consecutive table rows and list items stay together
        if out and block[:2] in ("| ", "- ") and out[-1][:2] == block[:2] and "\n\n" not in out[-1]:
            out[-1] += "\n" + block
        else:
            out.append(block)
    return "\n\n".join(out).strip()


class Api:
    def __init__(self, snap: Path) -> None:
        self.snap = snap
        self.client = httpx.Client(headers={"User-Agent": AGENT}, timeout=60, follow_redirects=True)
        self.last = 0.0

    def get(self, url: str) -> bytes:
        for attempt in range(6):
            time.sleep(max(0.0, 0.5 - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                r = self.client.get(url)
            except httpx.HTTPError:
                r = None
            if r is not None and r.status_code == 200:
                return r.content
            if r is not None and r.status_code == 404:
                return b""
            time.sleep(2 ** (attempt + 1))
        raise SystemExit(f"{url}: no answer")

    def section(self, manual: str, path: str) -> dict | None:
        f = self.snap / manual / (path.rsplit("/", 1)[-1] + ".json")
        if not f.exists():
            raw = self.get(API + path)
            if not raw:
                return None
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(raw)
        return json.loads(f.read_bytes())


def children(d: dict) -> list[str]:
    return [c["base_path"] for g in d.get("details", {}).get("child_section_groups", []) for c in g.get("child_sections", [])]


def crawl(snap: Path, m: int, k: int, seed: int) -> None:
    api = Api(snap)
    snap.mkdir(parents=True, exist_ok=True)
    found = sorted(r["link"].rsplit("/", 1)[-1] for r in json.loads(api.get(SEARCH))["results"])
    random.Random(seed).shuffle(found)
    manuals: list[str] = []
    for slug in found:
        root = api.section(slug, f"/hmrc-internal-manuals/{slug}")
        if root and len(children(root)) >= 10:
            manuals.append(slug)
        if len(manuals) == m:
            break
    manuals.sort()
    for slug in manuals:
        rng = random.Random(f"{seed}/{slug}")
        frontier = children(api.section(slug, f"/hmrc-internal-manuals/{slug}") or {})
        seen = set(frontier)
        for _ in range(k):
            if not frontier:
                break
            path = frontier.pop(rng.randrange(len(frontier)))
            d = api.section(slug, path)
            for c in children(d or {}):
                if c not in seen:
                    seen.add(c)
                    frontier.append(c)
    files = {p.relative_to(snap).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(snap.glob("*/*.json"))}
    info = {"date": datetime.now(UTC).strftime("%Y-%m-%d"), "m": m, "k": k, "seed": seed, "manuals": manuals, "files": files}
    (snap / "crawl.json").write_text(json.dumps(info, indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="select the HMRC internal manuals corpus from a snapshot of the GOV.UK Content API")
    ap.add_argument("--snapshot", type=Path, required=True, help="the snapshot folder; built if it has no crawl.json")
    ap.add_argument("--out", type=Path, required=True, help="where the hmrc/ folder and hmrc-manifest.json go")
    ap.add_argument("--manuals", type=int, default=10)
    ap.add_argument("--sections", type=int, default=300, help="sections fetched per manual when the snapshot is built")
    ap.add_argument("--n", type=int, default=180)
    ap.add_argument("--seed", type=int, default=9)
    a = ap.parse_args()
    if not (a.snapshot / "crawl.json").exists():
        crawl(a.snapshot, a.manuals, a.sections, a.seed)
    info = json.loads((a.snapshot / "crawl.json").read_text(encoding="utf-8"))
    eligible = []
    for rel, sha in info["files"].items():
        raw = (a.snapshot / rel).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise SystemExit(f"{rel}: the snapshot changed")
        d = json.loads(raw)
        if rel.split("/")[0] not in info["manuals"]:
            continue
        body = html_to_text(d.get("details", {}).get("body") or "")
        if MIN_CHARS <= len(body) <= MAX_CHARS:
            eligible.append((rel, len(body), body, d, sha))
    random.Random(a.seed).shuffle(eligible)
    long = [e for e in eligible if e[1] > LONG_CHARS][: math.ceil(a.n / 3)]
    taken = {e[0] for e in long}
    pick = sorted(long + [e for e in eligible if e[0] not in taken][: a.n - len(long)])
    out = a.out / "hmrc"
    out.mkdir(parents=True, exist_ok=True)
    docs = []
    for rel, _, body, d, raw_sha in pick:
        manual, sid = rel[: -len(".json")].split("/")
        data = f"# {d['title'].strip()}\n\n{body}\n".encode()
        name = f"{manual}-{sid}.md"
        (out / name).write_bytes(data)
        docs.append(
            {
                "file": name,
                "folder": "/" + manual,
                "path": d["base_path"],
                "updated_at": d.get("updated_at"),
                "raw_sha256": raw_sha,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    manifest = {
        "url": URL,
        "api": API,
        "snapshot_date": info["date"],
        "manuals": info["manuals"],
        "sections_per_manual": info["k"],
        "n": a.n,
        "seed": a.seed,
        "min_chars": MIN_CHARS,
        "max_chars": MAX_CHARS,
        "license": "Open Government Licence v3.0",
        "docs": docs,
    }
    (a.out / "hmrc-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"{len(docs)} sections of {len(eligible)} eligible, {len({d['folder'] for d in docs})} manuals")


if __name__ == "__main__":
    main()
