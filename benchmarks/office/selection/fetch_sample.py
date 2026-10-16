"""Draw and fetch a sample: N untruncated captures per group (it, us) and format, with a seed (42 by default).

usage: python fetch_sample.py <cc-docs.tsv> <out dir> <N> [seed] [manifest to exclude]...      (needs warcio)
Writes <out>/<group>/<sha256[:16]>.<ext> and <out>/manifest.tsv (group, path, sha256, url). One file per URL and
per content hash. Each record comes from data.commoncrawl.org by byte range; the payload is not opened here.
"""
import csv
import hashlib
import io
import random
import sys
import urllib.request
from pathlib import Path

from warcio.archiveiterator import ArchiveIterator

FORMATS = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
}
docs, out, n = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
seed = int(sys.argv[4]) if len(sys.argv) > 4 else 42
used_urls, used_hashes = set(), set()
for prev in sys.argv[5:]:
    with open(prev, encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            used_urls.add(r["url"])
            used_hashes.add(r["sha256"])
with open(docs, encoding="utf-8") as f:
    rows = [r for r in csv.DictReader(f, delimiter="\t") if r["mime"] in FORMATS and not r["truncated"] and r["url"] not in used_urls]
cells: dict[tuple[str, str], list[dict]] = {}
for r in rows:
    group = "it" if r["tld"] == "it" else "us"
    cells.setdefault((group, FORMATS[r["mime"]]), []).append(r)
rng = random.Random(seed)
manifest = []
for (group, fmt), cands in sorted(cells.items()):
    cands = sorted({r["url"]: r for r in cands}.values(), key=lambda r: r["url"])
    rng.shuffle(cands)
    got, seen = 0, set()
    for r in cands:
        if got >= n:
            break
        off, length = int(r["offset"]), int(r["length"])
        url = "https://data.commoncrawl.org/" + r["warc_filename"]
        req = urllib.request.Request(url, headers={"Range": f"bytes={off}-{off + length - 1}"})
        try:
            raw = urllib.request.urlopen(req, timeout=60).read()
            rec = next(iter(ArchiveIterator(io.BytesIO(raw))))
            body = rec.content_stream().read()
        except Exception as e:
            print("skip", r["url"], type(e).__name__, file=sys.stderr)
            continue
        h = hashlib.sha256(body).hexdigest()
        if h in seen or h in used_hashes or not body.startswith(b"PK"):
            continue
        seen.add(h)
        path = out / group / f"{h[:16]}.{fmt}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        manifest.append((group, path.as_posix(), h, r["url"]))
        got += 1
    print(group, fmt, got, "of", len(cands), file=sys.stderr, flush=True)
with open(out / "manifest.tsv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter="\t")
    w.writerow(["group", "path", "sha256", "url"])
    w.writerows(manifest)
