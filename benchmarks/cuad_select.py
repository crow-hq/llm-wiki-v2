# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A real corpus of commercial contracts from CUAD v1 (https://zenodo.org/records/4595826, CC BY 4.0).

    curl -L -o CUAD_v1.zip https://zenodo.org/api/records/4595826/files/CUAD_v1.zip/content
    python benchmarks/cuad_select.py --zip CUAD_v1.zip --out DIR

Pin: Zenodo record 4595826, `CUAD_v1.zip`, md5 `c38f490a984420b8a62600db401fafd5` (as listed by Zenodo), sha256
`88b694d99007d39777fa44cd72daf8297773d285dc3eab0091ba32078888d18e` (checked by the script).

The 510 full-contract text files (`CUAD_v1/full_contract_txt/`) as they are in the zip, between 300 and 150 000 characters:
a seeded sample of N (default 180, seed 9) with at least a third of them over 20 000 characters (the long ones are taken first,
in seeded order; the rest is drawn from all the eligible, so the folder distribution is the natural one). Reference folder: the
contract type. The `full_contract_pdf/` folders (`Part_I`..`Part_III`) are only batches and the master CSV has no type column, so the
type is read from the file name, whose last part is the name the contract carries (`..._EX-10.31_Distributor Agreement`, `Company
- Manufacturing Agreement`): the first of the known types (strategic alliance, sponsorship, distributor, development,
endorsement, license, branding, supply, outsourcing, franchise, agency, intellectual property, maintenance, services, reseller,
cooperation, joint filing, collaboration, affiliate, promotion, joint venture, consulting, manufacturing, hosting, marketing,
transportation, non-compete) that appears in that part; `other` when none does. Each file is one `.md`: the file name (without extension) as
heading, then the text as written, with the line ends normalised. `DIR/cuad-manifest.json` gives the source, the pin, the
parameters and, for each contract, its file, folder (the gold of `incremental_score.py --gold`), original file name and the
sha256 of the file written. Nothing of the dataset is committed: only this script, the URL and the pin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import zipfile
from pathlib import Path

URL = "https://zenodo.org/records/4595826"
MD5 = "c38f490a984420b8a62600db401fafd5"
SHA256 = "88b694d99007d39777fa44cd72daf8297773d285dc3eab0091ba32078888d18e"
SUB = "CUAD_v1/full_contract_txt/"
MIN_CHARS = 300
MAX_CHARS = 150_000
LONG_CHARS = 20_000
TYPES = [
    ("strategic-alliance", r"STRATEGIC\s+ALLIANCE"),
    ("sponsorship", r"SPONSORSHIP"),
    ("distributor", r"DISTRIBUT"),
    ("development", r"DEVELOPMENT"),
    ("endorsement", r"ENDORSEMENT"),
    ("license", r"LICEN[SC]"),
    ("branding", r"BRANDING"),
    ("supply", r"SUPPLY"),
    ("outsourcing", r"OUTSOURCING"),
    ("franchise", r"FRANCHISE"),
    ("agency", r"AGENCY"),
    ("intellectual-property", r"INTELLECTUAL\s+PROPERTY"),
    ("maintenance", r"MAINTENANCE"),
    ("services", r"SERVIC"),
    ("reseller", r"RESELL|REMARKET"),
    ("cooperation", r"COOPERATION"),
    ("joint-filing", r"JOINT\s+FILING"),
    ("collaboration", r"COLLABORAT"),
    ("affiliate", r"AFFILIATE"),
    ("promotion", r"PROMOTION"),
    ("joint-venture", r"JOINT\s+VENTURE"),
    ("consulting", r"CONSULTING"),
    ("manufacturing", r"MANUFACTUR"),
    ("hosting", r"HOSTING"),
    ("marketing", r"MARKETING"),
    ("transportation", r"TRANSPORT"),
    ("non-compete", r"NON[- ]?COMPET"),
]


def contract_type(stem: str) -> str:
    part = re.sub(r"^(?:[\d.]+|6 MAT CTRCT)_", "", stem.rsplit("-", 1)[-1].strip()).upper()
    hits = [(m.start(), name) for name, rx in TYPES if (m := re.search(rx, part))]
    return min(hits)[1] if hits else "other"


def decode(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n").strip()


def main() -> None:
    ap = argparse.ArgumentParser(description="select the CUAD contract corpus from the zip")
    ap.add_argument("--zip", type=Path, required=True, help="CUAD_v1.zip of Zenodo record 4595826")
    ap.add_argument("--out", type=Path, required=True, help="where the cuad/ folder and cuad-manifest.json go")
    ap.add_argument("--n", type=int, default=180)
    ap.add_argument("--seed", type=int, default=9)
    a = ap.parse_args()
    sha = hashlib.sha256(a.zip.read_bytes()).hexdigest()
    if sha != SHA256:
        raise SystemExit(f"{a.zip} has sha256 {sha}, expected {SHA256}")
    eligible = []
    with zipfile.ZipFile(a.zip) as z:
        for info in sorted(z.infolist(), key=lambda i: i.filename):
            if info.filename.startswith(SUB) and info.filename.endswith(".txt"):
                text = decode(z.read(info))
                if MIN_CHARS <= len(text) <= MAX_CHARS:
                    eligible.append((info.filename[len(SUB) :], len(text), text))
    random.Random(a.seed).shuffle(eligible)
    long = [e for e in eligible if e[1] > LONG_CHARS][: math.ceil(a.n / 3)]
    taken = {e[0] for e in long}
    pick = sorted(long + [e for e in eligible if e[0] not in taken][: a.n - len(long)])
    out = a.out / "cuad"
    out.mkdir(parents=True, exist_ok=True)
    docs = []
    for i, (fname, _, body) in enumerate(pick, 1):
        stem = fname[: -len(".txt")]
        name = f"{i:03d}-{re.sub(r'[^a-z0-9]+', '-', stem.lower()).strip('-')[:60]}.md"
        data = f"# {stem}\n\n{body}\n".encode()
        (out / name).write_bytes(data)
        docs.append({"file": name, "folder": "/" + contract_type(stem), "path": SUB + fname, "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {
        "url": URL,
        "archive": "CUAD_v1.zip",
        "archive_md5": MD5,
        "archive_sha256": SHA256,
        "subtree": SUB,
        "n": a.n,
        "seed": a.seed,
        "min_chars": MIN_CHARS,
        "max_chars": MAX_CHARS,
        "license": "CC BY 4.0",
        "docs": docs,
    }
    (a.out / "cuad-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"{len(docs)} contracts of {len(eligible)} eligible, {len({d['folder'] for d in docs})} folders")


if __name__ == "__main__":
    main()
