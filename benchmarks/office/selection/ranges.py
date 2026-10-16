"""Per parquet part of the CC index: min/max url_surtkey (footer only), to find the parts holding .it and US hosts.

usage: python ranges.py <cc-index-table.paths> > cc-ranges.tsv      (needs duckdb; CC-MAIN-2026-39)
"""

import sys

import duckdb

with open(sys.argv[1]) as f:
    paths = [line.strip() for line in f if line.strip()]
con = duckdb.connect()
con.execute("INSTALL httpfs; LOAD httpfs;")
for p in paths:
    u = "https://data.commoncrawl.org/" + p
    query = f"select min(stats_min_value), max(stats_max_value) from parquet_metadata('{u}') where path_in_schema='url_surtkey'"
    r = con.execute(query).fetchone()
    print(p.split("/")[-1][:10], r[0][:40], r[1][:40], sep="\t", flush=True)
