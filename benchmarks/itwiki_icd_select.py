# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Italian Wikipedia articles about diseases with an ICD-10 code (CC BY-SA 4.0), labelled by ICD-10 chapter and block.

    python benchmarks/itwiki_icd_select.py --out DIR/itwiki-icd --cache DIR/_dl/itwiki-icd

Source: the items of Wikidata (https://query.wikidata.org/sparql, CC0) that have the property P494 (ICD-10) and a sitelink to
it.wikipedia.org, and the plain text of those articles from the MediaWiki API of it.wikipedia.org (`action=query`,
`prop=extracts|revisions|info`, `explaintext`). Text: CC BY-SA 4.0, by the contributors of Wikipedia; the manifest gives the
article URL and revision id of every document. Both services are live, so the pin is the revision id of every article and the
sha256 of every file written, not a version of the source: the query result (a few thousand items) is cached with the texts, so
a rerun on the cache gives the same corpus.

Seed 9. One SPARQL query; the first 3 characters of every ICD-10 code of an item give its categories; items with a code that
is not a plain category (`A00-A09`, ...) or outside the blocks below are dropped, and so are the items whose codes fall in more
than one chapter. An item with several codes in one chapter but in several blocks takes the block of its lowest code (all codes
and a flag are in the manifest). The chapter (L1) is read from the 22 official WHO chapter ranges; the block (L2) is the block
of the WHO ICD-10 (version 2019) block list embedded below (`J09-J18`, `K50-K52`, ...), a code being in the block whose range
contains its 3-character category. Gold folder: `/<chapter>-<slug>/<block>`, e.g. `/X-respiratory/J09-J18`.

Politeness: at most 1 request per second, a descriptive User-Agent (no personal address), every response cached under the
cache folder and the run resumable. A 403 or 429 stops the run with a message; nothing evades it. The wikitext size of the
candidates (`prop=info`, 50 titles per request) first drops the ones under 2 000 bytes; the others are fetched one by one in
the seeded order (the full extract of a page needs a request of its own), skipping a chapter once it has 20% of N, until N
articles of at least 1 500 characters are kept. Each file is Markdown: `# title`, then the text, the `== heading ==` lines
of the plain text rewritten as Markdown headings. `DIR/itwiki-icd-manifest.json` is written next to the folder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

SPARQL = "https://query.wikidata.org/sparql"
API = "https://it.wikipedia.org/w/api.php"
UA = "llm-wiki-v2-benchmarks/0.1 (corpus selection script, https://github.com/crow-hq/llm-wiki-v2)"
QUERY = (
    "SELECT ?item ?code ?title WHERE { ?item wdt:P494 ?code . "
    "?a schema:about ?item ; schema:isPartOf <https://it.wikipedia.org/> ; schema:name ?title . }"
)
MIN_CHARS = 1500
MIN_BYTES = 2000
SPACING = 1.0

CHAPTERS = [
    ("I", "A00", "B99", "infectious-parasitic"),
    ("II", "C00", "D48", "neoplasms"),
    ("III", "D50", "D89", "blood-immune"),
    ("IV", "E00", "E90", "endocrine-metabolic"),
    ("V", "F00", "F99", "mental-behavioural"),
    ("VI", "G00", "G99", "nervous-system"),
    ("VII", "H00", "H59", "eye"),
    ("VIII", "H60", "H95", "ear"),
    ("IX", "I00", "I99", "circulatory"),
    ("X", "J00", "J99", "respiratory"),
    ("XI", "K00", "K93", "digestive"),
    ("XII", "L00", "L99", "skin"),
    ("XIII", "M00", "M99", "musculoskeletal"),
    ("XIV", "N00", "N99", "genitourinary"),
    ("XV", "O00", "O99", "pregnancy-childbirth"),
    ("XVI", "P00", "P96", "perinatal"),
    ("XVII", "Q00", "Q99", "congenital"),
    ("XVIII", "R00", "R99", "symptoms-signs"),
    ("XIX", "S00", "T98", "injury-poisoning"),
    ("XX", "V01", "Y98", "external-causes"),
    ("XXI", "Z00", "Z99", "health-status-contact"),
    ("XXII", "U00", "U99", "special-purposes"),
]

# WHO ICD-10 (2019) blocks, "first-last" 3-character categories.
BLOCKS = """A00-A09 A15-A19 A20-A28 A30-A49 A50-A64 A65-A69 A70-A74 A75-A79 A80-A89 A90-A99 B00-B09 B15-B19 B20-B24 B25-B34
B35-B49 B50-B64 B65-B83 B85-B89 B90-B94 B95-B98 B99-B99 C00-C14 C15-C26 C30-C39 C40-C41 C43-C44 C45-C49 C50-C50 C51-C58
C60-C63 C64-C68 C69-C72 C73-C75 C76-C80 C81-C96 C97-C97 D00-D09 D10-D36 D37-D48 D50-D53 D55-D59 D60-D64 D65-D69 D70-D77
D80-D89 E00-E07 E10-E14 E15-E16 E20-E35 E40-E46 E50-E64 E65-E68 E70-E90 F00-F09 F10-F19 F20-F29 F30-F39 F40-F48 F50-F59
F60-F69 F70-F79 F80-F89 F90-F98 F99-F99 G00-G09 G10-G14 G20-G26 G30-G32 G35-G37 G40-G47 G50-G59 G60-G64 G70-G73 G80-G83
G90-G99 H00-H06 H10-H13 H15-H22 H25-H28 H30-H36 H40-H42 H43-H45 H46-H48 H49-H52 H53-H54 H55-H59 H60-H62 H65-H75 H80-H83
H90-H95 I00-I02 I05-I09 I10-I15 I20-I25 I26-I28 I30-I52 I60-I69 I70-I79 I80-I89 I95-I99 J00-J06 J09-J18 J20-J22 J30-J39
J40-J47 J60-J70 J80-J84 J85-J86 J90-J94 J95-J99 K00-K14 K20-K31 K35-K38 K40-K46 K50-K52 K55-K64 K65-K67 K70-K77 K80-K87
K90-K93 L00-L08 L10-L14 L20-L30 L40-L45 L50-L54 L55-L59 L60-L75 L80-L99 M00-M25 M30-M36 M40-M54 M60-M79 M80-M94 M95-M99
N00-N08 N10-N16 N17-N19 N20-N23 N25-N29 N30-N39 N40-N51 N60-N64 N70-N77 N80-N98 N99-N99 O00-O08 O10-O16 O20-O29 O30-O48
O60-O75 O80-O84 O85-O92 O94-O99 P00-P04 P05-P08 P10-P15 P20-P29 P35-P39 P50-P61 P70-P74 P75-P78 P80-P83 P90-P96 Q00-Q07
Q10-Q18 Q20-Q28 Q30-Q34 Q35-Q37 Q38-Q45 Q50-Q56 Q60-Q64 Q65-Q79 Q80-Q89 Q90-Q99 R00-R09 R10-R19 R20-R23 R25-R29 R30-R39
R40-R46 R47-R49 R50-R69 R70-R79 R80-R82 R83-R89 R90-R94 R95-R99 S00-S09 S10-S19 S20-S29 S30-S39 S40-S49 S50-S59 S60-S69
S70-S79 S80-S89 S90-S99 T00-T07 T08-T14 T15-T19 T20-T32 T33-T35 T36-T50 T51-T65 T66-T78 T79-T79 T80-T88 T90-T98 V01-X59
X60-X84 X85-Y09 Y10-Y34 Y35-Y36 Y40-Y84 Y85-Y89 Y90-Y98 Z00-Z13 Z20-Z29 Z30-Z39 Z40-Z54 Z55-Z65 Z70-Z76 Z80-Z99
U00-U49 U80-U89""".split()  # noqa: SIM905


def chapter_of(cat: str) -> tuple[str, str] | None:
    for num, lo, hi, slug in CHAPTERS:
        if lo <= cat <= hi:
            return num, slug
    return None


def block_of(cat: str) -> str | None:
    for b in BLOCKS:
        lo, hi = b.split("-")
        if lo <= cat <= hi:
            return b
    return None


class Blocked(SystemExit):
    pass


class Client:
    def __init__(self) -> None:
        self.last = 0.0
        self.requests = 0

    def get(self, url: str, params: dict) -> dict:
        wait = SPACING - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                raise Blocked(f"{url} answered {e.code}: stopping, nothing is evaded; rerun later (the cache is kept)") from e
            raise
        finally:
            self.last = time.monotonic()
            self.requests += 1
        return data


def cached(path: Path, make):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    data = make()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def candidates(rows: list[dict]) -> tuple[list[dict], Counter]:
    by_item: dict[str, dict] = {}
    for r in rows:
        it = by_item.setdefault(r["item"]["value"].rsplit("/", 1)[-1], {"title": r["title"]["value"], "codes": set()})
        it["codes"].add(r["code"]["value"].strip().upper())
    drops: Counter = Counter()
    out = []
    for qid, it in sorted(by_item.items()):
        cats = set()
        for code in it["codes"]:
            m = re.fullmatch(r"([A-Z]\d\d)(?:\.[0-9A-Z+*]{1,4})?", code)
            cats.add(m.group(1) if m else None)
        if None in cats or not cats:
            drops["code not a plain category"] += 1
            continue
        if any(chapter_of(c) is None or block_of(c) is None for c in cats):
            drops["category outside the WHO chapters/blocks"] += 1
            continue
        if len({chapter_of(c) for c in cats}) > 1:
            drops["codes in several chapters"] += 1
            continue
        low = min(cats)
        num, slug = chapter_of(low)
        out.append(
            {
                "qid": qid,
                "title": it["title"],
                "codes": sorted(it["codes"]),
                "code": min(c for c in it["codes"] if c.startswith(low)),
                "chapter": num,
                "slug": slug,
                "block": block_of(low),
                "several_blocks": len({block_of(c) for c in cats}) > 1,
            }
        )
    return out, drops


def to_markdown(title: str, text: str) -> str:
    lines = []
    for ln in text.replace("\r\n", "\n").split("\n"):
        m = re.fullmatch(r"(={2,6})\s*(.*?)\s*\1", ln.strip())
        lines.append(f"{'#' * len(m.group(1))} {m.group(2)}" if m and m.group(2) else ln.rstrip())
    return f"# {title}\n\n" + re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="select the Italian Wikipedia ICD-10 disease corpus")
    ap.add_argument("--out", type=Path, required=True, help="the folder of the .md files; the manifest is OUT-manifest.json")
    ap.add_argument("--cache", type=Path, required=True, help="download cache (resumable)")
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--seed", type=int, default=9)
    ap.add_argument("--max-share", type=float, default=0.2, help="largest share of N in one chapter")
    a = ap.parse_args()
    cl = Client()
    rows = cached(
        a.cache / "sparql.json",
        lambda: cl.get(SPARQL, {"query": QUERY, "format": "json"})["results"]["bindings"],
    )
    cands, drops = candidates(rows)
    print(f"{len(rows)} rows, {len(cands)} items usable; dropped {dict(drops)}")

    sizes_path = a.cache / "sizes.json"
    sizes = json.loads(sizes_path.read_text(encoding="utf-8")) if sizes_path.exists() else {}
    todo = [c["title"] for c in cands if c["title"] not in sizes]
    for i in range(0, len(todo), 50):
        batch = todo[i : i + 50]
        d = cl.get(
            API,
            {"action": "query", "prop": "info", "titles": "|".join(batch), "redirects": 1, "format": "json", "formatversion": 2},
        )
        q = d["query"]
        back = {x["to"]: x["from"] for x in q.get("redirects", [])}
        norm = {x["to"]: x["from"] for x in q.get("normalized", [])}
        for p in q["pages"]:
            src = back.get(p["title"], p["title"])
            sizes[norm.get(src, src)] = 0 if p.get("missing") else p.get("length", 0)
        for t in batch:
            sizes.setdefault(t, 0)
        sizes_path.write_text(json.dumps(sizes, ensure_ascii=False), encoding="utf-8")
    pool = [c for c in cands if sizes.get(c["title"], 0) >= MIN_BYTES]
    print(f"{len(pool)} candidates with at least {MIN_BYTES} bytes of wikitext")

    random.Random(a.seed).shuffle(pool)
    cap = int(a.n * a.max_share)
    per_chapter: Counter = Counter()
    kept = []
    short = 0
    for c in pool:
        if len(kept) >= a.n:
            break
        if per_chapter[c["chapter"]] >= cap:
            continue
        key = hashlib.sha1(c["title"].encode()).hexdigest()[:16]
        d = cached(
            a.cache / "pages" / f"{key}.json",
            lambda c=c: cl.get(
                API,
                {
                    "action": "query",
                    "prop": "extracts|revisions",
                    "explaintext": 1,
                    "exlimit": 1,
                    "rvprop": "ids",
                    "titles": c["title"],
                    "redirects": 1,
                    "format": "json",
                    "formatversion": 2,
                },
            ),
        )
        pages = [p for p in d["query"]["pages"] if not p.get("missing")]
        if not pages:
            continue
        p = pages[0]
        text = (p.get("extract") or "").strip()
        if len(text) < MIN_CHARS:
            short += 1
            continue
        per_chapter[c["chapter"]] += 1
        kept.append(
            {**c, "page_id": p["pageid"], "wiki_title": p["title"], "revision_id": p["revisions"][0]["revid"], "text": text}
        )
        if len(kept) % 50 == 0:
            print(f"{len(kept)} kept, {cl.requests} requests")
    kept.sort(key=lambda c: (c["chapter"], c["block"], c["page_id"]))

    a.out.mkdir(parents=True, exist_ok=True)
    docs = []
    for i, c in enumerate(kept, 1):
        slug = re.sub(r"[^a-z0-9]+", "-", c["wiki_title"].lower()).strip("-")[:60] or str(c["page_id"])
        name = f"{i:03d}-{slug}.md"
        data = to_markdown(c["wiki_title"], c["text"]).encode()
        (a.out / name).write_bytes(data)
        url = "https://it.wikipedia.org/wiki/" + urllib.parse.quote(c["wiki_title"].replace(" ", "_"))
        docs.append(
            {
                "file": name,
                "folder": f"/{c['chapter']}-{c['slug']}/{c['block']}",
                "icd10": c["code"],
                "icd10_all": c["codes"],
                "several_blocks": c["several_blocks"],
                "wikidata": c["qid"],
                "title": c["wiki_title"],
                "page_id": c["page_id"],
                "revision_id": c["revision_id"],
                "url": url,
                "text_chars": len(c["text"]),
                "sha256": hashlib.sha256(data).hexdigest(),
                "license": "CC BY-SA 4.0",
                "attribution": f"Wikipedia contributors, '{c['wiki_title']}', {url}, revision {c['revision_id']}",
            }
        )
    manifest = {
        "source": "Italian Wikipedia (it.wikipedia.org) articles of Wikidata items with ICD-10 (P494)",
        "wikidata_query": QUERY,
        "license": "CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/), by the contributors of Wikipedia",
        "n": len(docs),
        "seed": a.seed,
        "min_chars": MIN_CHARS,
        "max_share_per_chapter": a.max_share,
        "blocks": "WHO ICD-10 2019 block list embedded in itwiki_icd_select.py",
        "docs": docs,
    }
    mpath = a.out.parent / (a.out.name + "-manifest.json")
    mpath.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(docs)} articles ({short} under {MIN_CHARS} characters skipped), {cl.requests} requests this run")
    for num, _, _, slug in CHAPTERS:
        if per_chapter[num]:
            print(f"  {num:6} {slug:22} {per_chapter[num]}")
    print(f"{len({d['folder'] for d in docs})} gold folders; manifest {mpath}")


if __name__ == "__main__":
    main()
