# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A real corpus of commercial contracts from CUAD v1 (https://zenodo.org/records/4595826, CC BY 4.0).

    curl -L -o CUAD_v1.zip https://zenodo.org/api/records/4595826/files/CUAD_v1.zip/content
    python benchmarks/cuad_select.py --zip CUAD_v1.zip --out DIR

Pin: Zenodo record 4595826, `CUAD_v1.zip`, md5 `c38f490a984420b8a62600db401fafd5` (as listed by Zenodo), sha256
`88b694d99007d39777fa44cd72daf8297773d285dc3eab0091ba32078888d18e` (checked by the script).

The 510 full-contract text files (`CUAD_v1/full_contract_txt/`) as they are in the zip, between 300 and `--max-chars` characters
(default 150 000; 350 000 takes all 510): a seeded sample of N (default 180, seed 9) with at least a third of them over 20 000
characters (the long ones are taken first, in seeded order; the rest is drawn from all the eligible, so the folder distribution is
the natural one). Reference folder: the contract type. It is the official type folder of the contract's PDF in
`full_contract_pdf/Part_*/<Type>/`, matched to the text file by name (case, spaces and punctuation ignored) and written with the
names of the 25 CUAD types (`license`, `strategic-alliance`, `intellectual-property`, ...). A contract with no PDF of that name
keeps the type read from its file name, whose last part is the name the contract carries (`..._EX-10.31_Distributor Agreement`): the
first of the known types (strategic alliance, sponsorship, distributor, ...) that appears in that part, `other` when none does;
how many contracts fall back is printed and written in the manifest. Each file is one `.md`: the file name (without extension) as
heading, then the text as written, with the line ends normalised. `DIR/cuad-manifest.json` gives the source, the pin, the
parameters and, for each contract, its file, folder (the gold of `incremental_score.py --gold`), the source of the folder
(`pdf-folder` or `file-name`), original file name and the sha256 of the file written. Nothing of the dataset is committed: only this
script, the URL and the pin.
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
PDF_SUB = "CUAD_v1/full_contract_pdf/"
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


# Official type folders of full_contract_pdf (their spelling varies from part to part), keyed by the folder name without
# case, spaces and punctuation.
FOLDERS = {
    "affiliateagreement": "affiliate",
    "affiliateagreements": "affiliate",
    "agencyagreements": "agency",
    "cobranding": "branding",
    "collaboration": "collaboration",
    "consultingagreements": "consulting",
    "development": "development",
    "distributor": "distributor",
    "endorsement": "endorsement",
    "endorsementagreement": "endorsement",
    "franchise": "franchise",
    "hosting": "hosting",
    "ip": "intellectual-property",
    "jointventure": "joint-venture",
    "jointventurefiling": "joint-venture",
    "licenseagreements": "license",
    "maintenance": "maintenance",
    "manufacturing": "manufacturing",
    "marketing": "marketing",
    "noncompetenonsolicit": "non-compete",
    "outsourcing": "outsourcing",
    "promotion": "promotion",
    "reseller": "reseller",
    "service": "services",
    "sponsorship": "sponsorship",
    "strategicalliance": "strategic-alliance",
    "supply": "supply",
    "transportation": "transportation",
}


def key(stem: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", stem.lower())


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
    ap.add_argument("--max-chars", type=int, default=MAX_CHARS, help="longest text taken (350000 for all 510)")
    a = ap.parse_args()
    sha = hashlib.sha256(a.zip.read_bytes()).hexdigest()
    if sha != SHA256:
        raise SystemExit(f"{a.zip} has sha256 {sha}, expected {SHA256}")
    eligible = []
    official = {}
    with zipfile.ZipFile(a.zip) as z:
        for name in z.namelist():
            parts = name.split("/")
            if name.startswith(PDF_SUB) and len(parts) >= 5 and parts[-1]:
                official[key(parts[-1][: -len('.pdf')])] = FOLDERS[key(parts[-2])]
        for info in sorted(z.infolist(), key=lambda i: i.filename):
            if info.filename.startswith(SUB) and info.filename.endswith(".txt"):
                text = decode(z.read(info))
                if MIN_CHARS <= len(text) <= a.max_chars:
                    eligible.append((info.filename[len(SUB) :], len(text), text))
    random.Random(a.seed).shuffle(eligible)
    long = [e for e in eligible if e[1] > LONG_CHARS][: math.ceil(a.n / 3)]
    taken = {e[0] for e in long}
    pick = sorted(long + [e for e in eligible if e[0] not in taken][: a.n - len(long)])
    out = a.out / "cuad"
    out.mkdir(parents=True, exist_ok=True)
    docs = []
    fallback = 0
    for i, (fname, _, body) in enumerate(pick, 1):
        stem = fname[: -len(".txt")]
        name = f"{i:03d}-{re.sub(r'[^a-z0-9]+', '-', stem.lower()).strip('-')[:60]}.md"
        data = f"# {stem}\n\n{body}\n".encode()
        (out / name).write_bytes(data)
        folder = official.get(key(stem))
        fallback += folder is None
        docs.append(
            {
                "file": name,
                "folder": "/" + (folder or contract_type(stem)),
                "folder_source": "pdf-folder" if folder else "file-name",
                "path": SUB + fname,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    manifest = {
        "url": URL,
        "archive": "CUAD_v1.zip",
        "archive_md5": MD5,
        "archive_sha256": SHA256,
        "subtree": SUB,
        "n": a.n,
        "seed": a.seed,
        "min_chars": MIN_CHARS,
        "max_chars": a.max_chars,
        "labels_from_file_name": fallback,
        "license": "CC BY 4.0",
        "docs": docs,
    }
    (a.out / "cuad-manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"{len(docs)} contracts of {len(eligible)} eligible, {len({d['folder'] for d in docs})} folders")
    print(f"{fallback} labelled from the file name (no PDF of that name)")


if __name__ == "__main__":
    main()
