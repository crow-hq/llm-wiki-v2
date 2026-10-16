"""Unpack NapierOne tiny archives (members by content hash, no paths from the archive) and write a manifest, group uk.

usage: python napier_manifest.py <archives dir> <out dir> [TYPE...]     (default DOCX XLSX PPTX; ODS was unpacked alone)
Writes <out>/<sha256[:16]>.<type> and <out>/../manifest-uk.tsv (group, path, sha256, url = "napierone:" + member).
"""

import csv
import hashlib
import sys
import zipfile
from pathlib import Path

src, out = Path(sys.argv[1]), Path(sys.argv[2])
rows = []
for t in sys.argv[3:] or ("DOCX", "XLSX", "PPTX"):
    with zipfile.ZipFile(src / f"{t}-tiny.zip") as z:
        for m in z.infolist():
            if m.is_dir() or not m.filename.lower().endswith("." + t.lower()):
                continue
            data = z.read(m)
            h = hashlib.sha256(data).hexdigest()
            p = out / f"{h[:16]}.{t.lower()}"
            p.write_bytes(data)
            rows.append(("uk", p.as_posix(), h, "napierone:" + m.filename))
with open(out.parent / "manifest-uk.tsv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f, delimiter="\t")
    w.writerow(["group", "path", "sha256", "url"])
    w.writerows(rows)
print(len(rows))
