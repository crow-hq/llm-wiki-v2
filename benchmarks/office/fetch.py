"""Download the Office/ODF bench from its sources and check every file against its sha256.

usage: python fetch.py <out dir>        (needs `pip install warcio` for the Common Crawl records)

Writes <out>/cc/<sha256[:16]>.<format> for each row of cc-files.tsv, by byte range from data.commoncrawl.org, and
<out>/napierone/<sha256[:16]>.<format> for each row of napierone-files.tsv, from the NapierOne archives (about
410 MB, kept in <out>/napierone/archives and checked against napierone-archives.tsv). A file already there with
the right hash is not fetched again. A file that no longer matches is reported and not written.
"""

from __future__ import annotations

import csv
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).parent


def rows(name: str) -> list[dict[str, str]]:
    with open(HERE / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def have(path: Path, sha256: str) -> bool:
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256


def get(url: str, headers: dict[str, str] | None = None) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=120) as r:
        return bytes(r.read())


def common_crawl(out: Path) -> int:
    from warcio.archiveiterator import ArchiveIterator

    bad = 0
    for r in rows("cc-files.tsv"):
        path = out / "cc" / f"{r['sha256'][:16]}.{r['format']}"
        if have(path, r["sha256"]):
            continue
        start = int(r["warc_record_offset"])
        end = start + int(r["warc_record_length"]) - 1
        try:
            record = get("https://data.commoncrawl.org/" + r["warc_filename"], {"Range": f"bytes={start}-{end}"})
            body = next(iter(ArchiveIterator(io.BytesIO(record)))).content_stream().read()
        except Exception as e:  # one record out of reach does not stop the others
            print(f"cannot fetch {r['url']}: {type(e).__name__}: {e}", file=sys.stderr)
            bad += 1
            continue
        if hashlib.sha256(body).hexdigest() != r["sha256"]:
            print(f"sha256 differs, not written: {r['url']}", file=sys.stderr)
            bad += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return bad


def napierone(out: Path) -> int:
    bad = 0
    archives = out / "napierone" / "archives"
    archives.mkdir(parents=True, exist_ok=True)
    good = set()
    for a in rows("napierone-archives.tsv"):
        path = archives / a["archive"]
        if not have(path, a["sha256"]):
            print(f"downloading {a['archive']} ({int(a['bytes']) / 2**20:.0f} MB)", file=sys.stderr)
            try:
                path.write_bytes(get(a["url"]))
            except Exception as e:
                print(f"cannot fetch {a['url']}: {type(e).__name__}: {e}", file=sys.stderr)
        if have(path, a["sha256"]):
            good.add(a["archive"])
        else:
            print(f"sha256 differs or missing, its files are skipped: {a['archive']}", file=sys.stderr)
    for r in rows("napierone-files.tsv"):
        path = out / "napierone" / f"{r['sha256'][:16]}.{r['format']}"
        if have(path, r["sha256"]):
            continue
        if r["archive"] not in good:
            bad += 1
            continue
        with zipfile.ZipFile(archives / r["archive"]) as z:
            data = z.read(r["member"])  # read by the member name only: nothing of the archive's paths is used
        if hashlib.sha256(data).hexdigest() != r["sha256"]:
            print(f"sha256 differs, not written: {r['archive']}:{r['member']}", file=sys.stderr)
            bad += 1
            continue
        path.write_bytes(data)
    return bad


if __name__ == "__main__":
    target = Path(sys.argv[1])
    missing = napierone(target) + common_crawl(target)
    print(f"done, {missing} files missing or different" if missing else "done, every file matches its sha256")
