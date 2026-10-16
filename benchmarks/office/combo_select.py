"""The 24 files and frozen questions of the paid check (seed 61), from readers independent of llmw2.

usage: python combo_select.py <data dir of fetch.py> <out dir>
needs: pip install pypandoc_binary==1.17 openpyxl==3.1.5 pandas==3.0.6 odfpy==1.4.1  (the versions it was run with)

Picks, from the samples cc6, cc5 and cc4 of cc-files.tsv (in that order) and away from the 15 files of the human
review (REVIEW), 6 DOCX (3 with no heading styles), 3 ODT, 5 PPTX, 6 XLSX and 4 ODS, favouring workbooks with
several sheets and documents long enough to need retrieval. Copies them with their real names into <out>/files and
writes <out>/questions.json: 2 per document (cloze), 2-3 per workbook (lookups, one past row 20 when there is one,
and a column total). The questions quote the documents, so they are not kept in the repository: with the same
inputs this script writes them again, and their sha256 must be the one in SOURCES.md.
"""

from __future__ import annotations

import csv
import datetime
import json
import random
import re
import shutil
import sys
import urllib.parse
import zipfile
from collections import Counter
from pathlib import Path

REVIEW = (Path(__file__).parent / "review-sha256.txt").read_text(encoding="utf-8").split()  # the 15 reviewed files
SAMPLES = ("cc6", "cc5", "cc4")
WANT = {"docx": 6, "odt": 3, "pptx": 5, "xlsx": 6, "ods": 4}


def _name(r: dict[str, str], size: int) -> str:
    """A file's real name from its URL, as the review (90 characters) and this run (80) named it."""
    name = urllib.parse.unquote(r["url"].split("?")[0].rstrip("/").rsplit("/", 1)[-1])
    name = re.sub(r"[^\w.\- ()àèéìòù]", "_", name)[:size] or Path(r["path"]).name
    return name if name.lower().endswith("." + r["format"]) else name + "." + r["format"]


def _iso(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime.datetime):
        return v.strftime("%Y-%m-%d") if v.time() == datetime.time() else v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, datetime.date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, datetime.time):
        return v.strftime("%H:%M")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def ref_text(path: Path, k: str) -> str:
    """A document's text by pandoc."""
    import pypandoc

    return str(pypandoc.convert_file(str(path), "plain", format=k, extra_args=["--wrap=none"]))


def sheets_of(path: Path, ext: str) -> list[tuple[str, list[list[str]]]]:
    """A workbook's sheets by openpyxl (XLSX) or pandas with odfpy (ODS)."""
    if ext == "xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(path, data_only=True)
        return [(ws.title, [[_iso(v).strip() for v in row] for row in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    import pandas as pd

    frames = pd.read_excel(path, engine="odf", sheet_name=None, header=None, dtype=object)
    def cell(v: object) -> str:
        return "" if (isinstance(v, float) and v != v) else _iso(v).strip()

    return [(n, [[cell(v) for v in row] for row in f.itertuples(index=False)]) for n, f in frames.items()]


def styled_headings(path: Path) -> int:
    z = zipfile.ZipFile(path)
    styles = z.read("word/styles.xml").decode("utf-8", "replace") if "word/styles.xml" in z.namelist() else ""
    ids = set(re.findall(r'w:styleId="([^"]+)"[^>]*>\s*<w:name w:val="(?:heading \d|Title)"', styles, re.I))
    doc = z.read("word/document.xml").decode("utf-8", "replace")
    return sum(doc.count(f'<w:pStyle w:val="{i}"/>') for i in ids)


def cloze(rng: random.Random, text: str, k: int = 2) -> list[dict[str, str]]:
    sents = [s.strip() for s in re.split(r"(?<=[.!?;:])\s+|\n{2,}", text) if 60 <= len(s.strip()) <= 300]
    if len(sents) < 8:
        return []
    qs = []
    for j in range(k):
        s = sents[round((j + 1) * (len(sents) - 1) / (k + 1))]  # the middle and late parts, not the opening
        toks = [t for t in re.finditer(r"\b(\d[\d.,/]*\d|\d|[A-ZÀ-Ý][\wÀ-ÿ'-]{3,})\b", s) if t.start() > 0]
        if toks:
            t = rng.choice(toks)
            q = f'Fill the blank with the exact word or number from the document: "{s[: t.start()]}____{s[t.end() :]}"'
            qs.append({"kind": "cloze", "q": q, "a": t.group(0)})
    return qs


def sheet_qs(rng: random.Random, path: Path, ext: str) -> tuple[list[dict[str, str]], int]:
    sheets = [(n, [r for r in rs if any(c for c in r)]) for n, rs in sheets_of(path, ext)]
    sheets = [(n, rs) for n, rs in sheets if len(rs) >= 6]
    if not sheets:
        return [], 0
    qs = []
    for n, rs in rng.sample(sheets, min(2, len(sheets))):
        width = max(len(r) for r in rs)
        rs = [r + [""] * (width - len(r)) for r in rs]
        hdr_i = next((i for i, r in enumerate(rs[:6]) if sum(1 for c in r if c) >= max(2, width // 2)), 0)
        header, body = rs[hdr_i], rs[hdr_i + 1 :]
        if len(body) < 4:
            continue
        cols = [c for c in range(width) if header[c]]
        distinct = {c: len({r[c] for r in body if r[c]}) for c in cols}
        key = max(cols, key=lambda c: (distinct[c], -c)) if cols else 0
        for i in ([len(body) * 3 // 4] if len(body) > 20 else []) + [len(body) // 3]:
            r = body[i]
            common = {c: Counter(x[c] for x in body).most_common(1)[0][0] for c in cols}
            others = [c for c in cols if c != key and r[c] and len(r[c]) <= 40 and distinct[c] > 1 and r[c] != common[c]]
            if r[key] and others and sum(1 for x in body if x[key] == r[key]) == 1:
                c = rng.choice(others)
                kind = "lookup>20" if i >= 20 else "lookup"
                q = f'In the spreadsheet, sheet "{n}": in the row where "{header[key]}" is "{r[key]}", what is "{header[c]}"?'
                qs.append({"kind": kind, "q": q, "a": r[c]})
        numeric = [c for c in cols if sum(1 for r in body if re.fullmatch(r"-?\d+(\.\d+)?", r[c].replace(",", ""))) >= 0.9 * len(body)]
        nums = [c for c in numeric if distinct[c] > 2]
        if nums:
            c = nums[0]
            total = sum(float(r[c].replace(",", "")) for r in body if r[c])
            q = f'In the spreadsheet, sheet "{n}": what is the total of the column "{header[c]}" over all rows?'
            qs.append({"kind": "total", "q": q, "a": f"{total:.10g}"})
    return qs[:3], len(sheets)


def main() -> None:
    data, out = Path(sys.argv[1]), Path(sys.argv[2])
    (out / "files").mkdir(parents=True, exist_ok=True)
    with open(Path(__file__).parent / "cc-files.tsv", encoding="utf-8", newline="") as f:
        table = list(csv.DictReader(f, delimiter="\t"))
    rows = [{**r, "path": str(data / "cc" / f"{r['sha256'][:16]}.{r['format']}")} for s in SAMPLES for r in table if r["sample"] == s]
    used = {_name(r, 90) for r in rows if r["sha256"] in REVIEW}
    rng = random.Random(61)
    rng.shuffle(rows)
    picked: dict[str, list[dict[str, object]]] = {k: [] for k in WANT}
    questions = []
    multi_ok: Counter[str] = Counter()
    for r in rows:
        path = Path(r["path"])
        ext = path.suffix[1:].lower()
        if ext not in WANT or len(picked[ext]) >= WANT[ext] or not 8_000 <= path.stat().st_size <= 600_000:
            continue
        name = _name(r, 80)
        if name in used or any(name == x["name"] for v in picked.values() for x in v):
            continue
        try:
            if ext in ("docx", "odt", "pptx"):
                if ext == "docx":
                    flat = styled_headings(path) < 2
                    if sum(1 for x in picked["docx"] if x["flat"] == flat) >= 3:  # 3 with heading styles, 3 without
                        continue
                text = ref_text(path, ext)
                if len(text) < 6_000:
                    continue
                qs = cloze(rng, text)
                nsheets = 0
            else:
                qs, nsheets = sheet_qs(rng, path, ext)
                if nsheets < 2 and multi_ok[ext] < 2:  # half of the workbooks with several sheets
                    multi_ok[ext] += 1
                elif nsheets < 2:
                    continue
        except Exception:
            continue
        if len(qs) < 2:
            continue
        shutil.copyfile(path, out / "files" / name)
        picked[ext].append({"name": name, "url": r["url"], "flat": ext == "docx" and styled_headings(path) < 2, "sheets": nsheets})
        for q in qs:
            questions.append({**q, "file": name})
    with open(out / "questions.json", "w", encoding="utf-8") as f:
        json.dump({"seed": 61, "files": picked, "questions": questions}, f, ensure_ascii=False, indent=1)
    print({k: len(v) for k, v in picked.items()}, "questions", len(questions), Counter(q["kind"] for q in questions))


if __name__ == "__main__":
    main()
