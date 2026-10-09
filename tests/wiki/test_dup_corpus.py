# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The duplicates corpus generator (benchmarks/make_dup_corpus.py), on its dry run: free, no model."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "make_dup_corpus.py"


def load() -> ModuleType:
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("benchmarks_make_dup_corpus", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen() -> ModuleType:
    return load()


@pytest.fixture(scope="module")
def corpus(gen: ModuleType, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("dup")
    assert gen.main(["--dry-run", "--out", str(out)]) == 0
    return out


def labels_of(corpus: Path) -> dict[str, Any]:
    return json.loads((corpus / "labels.json").read_text(encoding="utf-8"))


def test_counts(corpus: Path) -> None:
    docs = labels_of(corpus)["docs"]
    assert len(docs) == 40
    assert Counter(d["kind"] for d in docs.values()) == {"dup": 15, "contr": 10, "trap": 10, "distractor": 5}
    assert len(list((corpus / "docs").glob("*.md"))) == 40
    generated = [n for n in (corpus / "base").rglob("*.md") if n.name not in ("index.md", "log.md") and "raw" not in n.parts]
    assert 85 <= len(generated) <= 95


def test_labels_shape(corpus: Path) -> None:
    labels = labels_of(corpus)
    for name, d in labels["docs"].items():
        assert (corpus / "docs" / name).is_file()
        assert {"kind", "pair_note", "slot", "value_a", "value_b", "shared_values", "question", "expect"} <= d.keys()
        if d["kind"] in ("dup", "contr"):
            assert d["question"] and d["question"].endswith("?")
            assert d["expect"] == ([d["value_a"]] if d["kind"] == "dup" else [d["value_a"], d["value_b"]])
            assert (d["value_a"] == d["value_b"]) == (d["kind"] == "dup")
        if d["kind"] in ("dup", "contr", "trap"):
            assert d["pair_note"] in labels["notes"]
            assert name in labels["notes"][d["pair_note"]]["planted_for"]
            assert (corpus / "base" / d["pair_note"]).is_file()
        if d["kind"] == "trap":
            assert d["shared_values"]
        if d["kind"] == "distractor":
            assert d["pair_note"] is None
    assert all(not folder_is_same(d, labels) for d in labels["docs"].values() if d["pair_note"])


def folder_is_same(d: dict[str, Any], labels: dict[str, Any]) -> bool:
    return "/" + d["pair_note"].rsplit("/", 1)[0] == d["doc_area"] if "/" in d["pair_note"] else False


def test_values_present_and_not_shared(gen: ModuleType, corpus: Path) -> None:
    labels = labels_of(corpus)
    files = gen.corpus_files(corpus)
    for name, d in labels["docs"].items():
        text = files[f"docs/{name}"]
        assert all(gen.count(text, v) == 1 for v in d["values"]), name
    for rel, n in labels["notes"].items():
        assert all(gen.count(files[rel], v) == 1 for v in n["values"]), rel
    holders: dict[str, set[str]] = {}
    for name, d in labels["docs"].items():
        for v in d["values"]:
            holders.setdefault(v, set()).add(f"docs/{name}")
    for rel, n in labels["notes"].items():
        for v in n["values"]:
            holders.setdefault(v, set()).add(rel)
    for v, where in holders.items():
        assert {f for f, t in files.items() if gen.count(t, v)} == where, v
        assert len(where) <= 2, v
    for d in labels["docs"].values():
        if d["kind"] == "contr":
            assert holders[d["value_a"]] != holders[d["value_b"]]
        if d["kind"] == "trap":
            assert all(len(holders[v]) == 2 for v in d["shared_values"])
        if d["kind"] == "distractor":
            assert all(len(holders[v]) == 1 for v in d["values"])


def test_bundle_check_and_domains(gen: ModuleType, corpus: Path) -> None:
    assert gen.verify(corpus) == []
    assert gen.bad_domains("write to a@lumen.example, see https://shop.lumen.example/x") == []
    assert gen.bad_domains("write to a@lumen.com") == ["lumen.com"]
