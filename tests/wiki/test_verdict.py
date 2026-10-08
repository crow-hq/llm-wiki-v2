# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The criterion of benchmarks/verdict.py on synthetic run records (benchmarks/ is not a package)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "verdict.py"
MERGED = ["merged", "customers/a.md"]
CREATED = ["created"]


@pytest.fixture(scope="module")
def verdict() -> ModuleType:
    spec = importlib.util.spec_from_file_location("benchmarks_verdict", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    docs = {
        "a.md": {"kind": "existing", "folders": ["/customers"], "merge_into": None, "group": None},
        "b.md": {"kind": "existing", "folders": ["/operations"], "merge_into": None, "group": None},
        "u.md": {"kind": "update", "folders": ["/customers"], "merge_into": "customers/a.md", "group": None},
    }
    (tmp_path / "labels.json").write_text(json.dumps({"docs": docs}), encoding="utf-8")
    return tmp_path


def run(a_folder: str = "/customers", u_outcome: list[str] = MERGED) -> dict[str, Any]:
    return {
        "docs": {
            "a.md": {"folder": a_folder, "outcome": CREATED, "note": "customers/a.md"},
            "b.md": {"folder": "/operations", "outcome": CREATED, "note": "operations/b.md"},
            "u.md": {"folder": "/customers", "outcome": u_outcome, "note": "customers/a.md"},
        },
        "existing_folders": ["/", "/customers", "/operations"],
    }


def write(path: Path, runs: list[dict[str, Any]], stopped: bool = False) -> Path:
    data = {"stopped": "interrupted", "runs": {"base": runs}} if stopped else {"variants": {"base": {"runs": runs}}}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_main_when_the_candidate_files_better_and_is_never_worse_then_it_passes(
    verdict: ModuleType, corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main = write(tmp_path / "main.json", [run(a_folder="/operations")] * 6)
    cand = write(tmp_path / "cand.json", [run()] * 6)

    code = verdict.main([str(main), str(cand), "--corpus", str(corpus), "--permutations", "2000"])

    assert code == 0
    out = capsys.readouterr().out
    assert "better than main (p < 0.1): ['folder_accuracy']" in out
    assert "growth criterion: PASS" in out


def test_main_when_the_candidate_merges_worse_then_it_fails_even_if_it_files_better(
    verdict: ModuleType, corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main = write(tmp_path / "main.json", [run(a_folder="/operations")] * 6)
    cand = write(tmp_path / "cand.json", [run(u_outcome=CREATED)] * 6)

    verdict.main([str(main), str(cand), "--corpus", str(corpus), "--permutations", "2000"])

    out = capsys.readouterr().out
    assert "worse than main (p < 0.1): ['merge_accuracy']" in out
    assert "growth criterion: FAIL" in out


def test_main_when_nothing_differs_then_it_fails_for_want_of_a_gain(
    verdict: ModuleType, corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main = write(tmp_path / "main.json", [run()] * 4)
    cand = write(tmp_path / "cand.json", [run()] * 4)

    verdict.main([str(main), str(cand), "--corpus", str(corpus), "--permutations", "500"])

    out = capsys.readouterr().out
    assert "worse than main (p < 0.1): none" in out
    assert "growth criterion: FAIL" in out


def test_runs_of_when_the_file_is_a_stopped_run_then_it_reads_the_runs_of_the_first_variant(verdict: ModuleType, tmp_path: Path) -> None:
    runs = [run(), run(a_folder="/operations")]

    assert verdict.runs_of(write(tmp_path / "stopped.json", runs, stopped=True)) == runs
    assert verdict.runs_of(write(tmp_path / "done.json", runs)) == runs
