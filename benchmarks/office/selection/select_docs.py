"""Office documents in Common Crawl CC-MAIN-2026-39 on Italian (.it) and US (.gov, .edu, .us) hosts.

Reads only the index parts whose SURT range covers those TLDs (cc-ranges.tsv) and writes every capture with a
document MIME type, fetched with status 200, to docs.tsv: group, mime, truncated, url, warc file, offset, length.

usage: python select_docs.py <cc-ranges.tsv> <cc-index-table.paths> <cc-docs.tsv>      (needs duckdb)
"""
import csv
import sys

import duckdb

ranges, paths, out = sys.argv[1], sys.argv[2], sys.argv[3]
TLDS = {"it": "it", "gov": "us", "edu": "us", "us": "us"}
MIMES = [
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.ms-outlook",
    "message/rfc822",
    "application/rtf",
    "application/epub+zip",
    "application/pdf",
]
parts = {}
with open(ranges, encoding="utf-8") as f:
    for line in f:
        name, lo, hi = line.rstrip("\n").split("\t")
        for tld in TLDS:
            if lo <= tld + ",~" and hi >= tld + ",":
                parts[name] = True
with open(paths) as f:
    urls = ["https://data.commoncrawl.org/" + p.strip() for p in f if p.strip().split("/")[-1][:10] in parts]
print(f"{len(urls)} index parts", file=sys.stderr)
con = duckdb.connect()
con.execute("LOAD httpfs")
mimes = ", ".join(f"'{m}'" for m in MIMES)
tlds = ", ".join(f"'{t}'" for t in TLDS)
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter="\t")
    w.writerow(["tld", "mime", "truncated", "url", "warc_filename", "offset", "length"])
    for u in urls:
        rows = con.execute(
            "select url_host_tld, content_mime_detected, coalesce(content_truncated, ''), url, warc_filename, "
            "warc_record_offset, warc_record_length "
            f"from read_parquet('{u}') where url_host_tld in ({tlds}) and fetch_status = 200 and content_mime_detected in ({mimes})"
        ).fetchall()
        w.writerows(rows)
        print(u.split("/")[-1][:10], len(rows), file=sys.stderr, flush=True)
