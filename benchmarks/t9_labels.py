# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Task 9: the two blind annotations of a synthetic half merged into one labels.json (stability.py's format), and a copy of
the half with prefixed file names (a01-..., b01-...) so the halves and growth can be filed together.

    python benchmarks/t9_labels.py        # both halves -> t9-corpus/stab/t9a, t9b (+ agreement.json)

Rules (fixed in the spec before the runs):
- kind: the same for both, else "ambiguous". Two "update"s with different targets are "ambiguous".
- folders: the folders both accept; if none, or the kind is ambiguous, the folders either accepts.
- update: merge_into / merge_with_doc as both gave; merge_ok the union of both annotators' merge_ok.
- new: two documents share a group when both annotators put them in one group; alt_folders the union.
- topic: the same for both, else "ambiguous"; primary only when both say so.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

CORPUS = Path(__file__).resolve().parent / "results" / "t9-corpus"  # the annotators' output; corpus/t9a and t9b are its copy


def as_list(v: Any) -> list[str]:
    return [] if not v else [v] if isinstance(v, str) else [x for x in v if x]


def merge(a: dict[str, Any], b: dict[str, Any], rename: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {"merge_into": None, "merge_with_doc": None, "merge_ok": [], "group": None, "alt_folders": []}
    fa, fb = set(as_list(a.get("folders"))), set(as_list(b.get("folders")))
    kind = a.get("kind") if a.get("kind") == b.get("kind") else "ambiguous"
    if kind == "update":
        ta = (a.get("merge_into"), rename.get(a.get("merge_with_doc") or ""))
        tb = (b.get("merge_into"), rename.get(b.get("merge_with_doc") or ""))
        if ta != tb:
            kind = "ambiguous"
        else:
            out["merge_into"], out["merge_with_doc"] = ta
            out["merge_ok"] = sorted(set(as_list(a.get("merge_ok"))) | set(as_list(b.get("merge_ok"))))
    both = fa & fb
    out["folders"] = sorted(both if both and kind != "ambiguous" else fa | fb)
    if kind not in ("new", "ambiguous") and not both:
        kind = "ambiguous"
    if kind == "new":
        out["folders"] = []
        out["alt_folders"] = sorted(set(as_list(a.get("alt_folders"))) | set(as_list(b.get("alt_folders"))))
    out["kind"] = kind
    out["topic"] = a.get("topic") if a.get("topic") == b.get("topic") else "ambiguous"
    out["primary"] = bool(a.get("primary")) and bool(b.get("primary")) and out["topic"] != "ambiguous"
    return out


def groups(files: list[str], a: dict[str, Any], b: dict[str, Any], kinds: dict[str, str]) -> dict[str, str]:
    """Components of "both annotators grouped these two new documents together"."""
    new = [f for f in files if kinds[f] == "new"]
    parent = {f: f for f in new}

    def find(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x

    for i, x in enumerate(new):
        for y in new[i + 1 :]:
            ga, gb = a[x].get("group"), b[x].get("group")
            if ga and ga == a[y].get("group") and gb and gb == b[y].get("group"):
                parent[find(y)] = find(x)
    return {f: f"g-{find(f)[:3]}" for f in new}


def main() -> None:
    agreement = {}
    for half in ("A", "B"):
        src = CORPUS / half
        a = json.loads((src / "labels-opus.json").read_text(encoding="utf-8"))
        b = json.loads((src / "labels-api.json").read_text(encoding="utf-8"))
        files = sorted(json.loads((src / "manifest.json").read_text(encoding="utf-8")))
        rename = {f: f"{half.lower()}{f}" for f in files}
        docs = {rename[f]: merge(a[f], b[f], rename) for f in files}
        g = groups(
            [rename[f] for f in files],
            {rename[f]: a[f] for f in files},
            {rename[f]: b[f] for f in files},
            {k: v["kind"] for k, v in docs.items()},
        )
        for f, gid in g.items():
            docs[f]["group"] = gid
        dst = CORPUS / "stab" / f"t9{half.lower()}"
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)
        for f in files:
            shutil.copy(src / f, dst / rename[f])
        about = f"Task 9 synthetic half {half}: blind writer, labels merged from two blind annotators (t9_labels.py)."
        (dst / "labels.json").write_text(json.dumps({"about": about, "docs": docs}, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
        renamed = {rename[f]: m for f, m in manifest.items()}
        (CORPUS / "stab" / f"manifest-{half}.json").write_text(json.dumps(renamed, indent=2), encoding="utf-8")
        kinds: dict[str, int] = {}
        for d in docs.values():
            kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
        same_kind = sum(a[f].get("kind") == b[f].get("kind") for f in files) / len(files)
        same_topic = sum(a[f].get("topic") == b[f].get("topic") for f in files) / len(files)
        overlap = sum(bool(set(as_list(a[f].get("folders"))) & set(as_list(b[f].get("folders")))) for f in files) / len(files)
        agreement[half] = {
            "kind": round(same_kind, 3),
            "topic": round(same_topic, 3),
            "merged_kinds": kinds,
            "folders_overlap": round(overlap, 3),
        }
    (CORPUS / "stab" / "agreement.json").write_text(json.dumps(agreement, indent=2), encoding="utf-8")
    print(json.dumps(agreement, indent=2))


if __name__ == "__main__":
    main()
