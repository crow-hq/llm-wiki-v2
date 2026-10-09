# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The metrics of benchmarks/stability.py on assignments built by hand (benchmarks/ is not a package)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "stability.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("benchmarks_stability", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses and typing look the module up by name
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def stability() -> ModuleType:
    return load()


def docs(*folders: str) -> dict[str, str]:
    return {f"d{i}.md": folder for i, folder in enumerate(folders, 1)}


# -- ari --------------------------------------------------------------------------------------


def test_ari_when_the_partitions_are_identical_then_one(stability: ModuleType) -> None:
    a = docs("x", "x", "y", "y", "z")

    assert stability.ari(a, dict(a)) == pytest.approx(1.0)


def test_ari_when_only_the_labels_differ_then_one(stability: ModuleType) -> None:
    a = docs("customers", "customers", "ops", "ops", "ideas")
    b = docs("1", "1", "2", "2", "3")

    assert stability.ari(a, b) == pytest.approx(1.0)


@pytest.mark.parametrize("labels", [("a",) * 5, tuple("abcde")], ids=["one-cluster", "all-singletons"])
def test_ari_when_both_runs_are_degenerate_in_the_same_way_then_one(stability: ModuleType, labels: tuple[str, ...]) -> None:
    a = docs(*labels)
    b = {k: f"other-{v}" for k, v in a.items()}

    assert stability.ari(a, b) == pytest.approx(1.0)


def test_ari_on_a_textbook_contingency_table(stability: ModuleType) -> None:
    # x: p p q / y: q r r. Table [[2,1,0],[0,1,2]]: sum C(n_ij,2)=2, rows 3+3, columns 1+1+1... by hand:
    # rows C(3,2)*2=6, columns C(2,2)*3=3, expected=6*3/C(6,2)=1.2, max=(6+3)/2=4.5, ARI=(2-1.2)/(4.5-1.2)=8/33.
    a = docs("x", "x", "x", "y", "y", "y")
    b = docs("p", "p", "q", "q", "r", "r")

    assert stability.ari(a, b) == pytest.approx(8 / 33)


def test_ari_is_symmetric(stability: ModuleType) -> None:
    a = docs("x", "x", "x", "y", "y", "y", "z")
    b = docs("p", "p", "q", "q", "r", "r", "r")

    assert stability.ari(a, b) == pytest.approx(stability.ari(b, a))


def test_ari_when_one_document_moves_then_less_than_one_and_more_than_a_full_reshuffle(stability: ModuleType) -> None:
    a = docs("x", "x", "x", "y", "y", "y")
    moved = docs("x", "x", "y", "y", "y", "y")
    shuffled = docs("x", "y", "x", "y", "x", "y")

    assert 1.0 > stability.ari(a, moved) > stability.ari(a, shuffled)


def test_ari_when_the_partitions_are_unrelated_then_near_zero_or_negative(stability: ModuleType) -> None:
    a = docs("x", "x", "y", "y")
    b = docs("p", "q", "p", "q")

    assert stability.ari(a, b) <= 0.0 + 1e-9


# -- outcome_agreement --------------------------------------------------------------------------


def test_outcome_agreement_is_the_share_of_documents_with_an_identical_outcome(stability: ModuleType) -> None:
    a: dict[str, Any] = {
        "1": ("created",),
        "2": ("merged", "customers/refund-policy.md"),
        "3": ("merged-with", "03-x.md"),
        "4": ("created",),
    }
    b = {**a, "2": ("merged", "customers/other.md"), "4": ("merged-with", "03-x.md")}

    assert stability.outcome_agreement(a, b) == pytest.approx(0.5)


def test_outcome_agreement_when_all_or_none_agree_then_one_or_zero(stability: ModuleType) -> None:
    a = {"1": ("created",), "2": ("merged", "x.md")}
    flipped = {"1": ("merged", "x.md"), "2": ("created",)}

    assert (stability.outcome_agreement(a, dict(a)), stability.outcome_agreement(a, flipped)) == (1.0, 0.0)


def test_outcome_agreement_is_symmetric(stability: ModuleType) -> None:
    a = {"1": ("created",), "2": ("created",), "3": ("created",)}
    b = {"1": ("created",), "2": ("merged", "x.md"), "3": ("created",)}

    assert stability.outcome_agreement(a, b) == pytest.approx(stability.outcome_agreement(b, a)) == pytest.approx(2 / 3)


# -- set_agreement ------------------------------------------------------------------------------


def label(folders: list[str], kind: str = "existing", **more: Any) -> dict[str, Any]:
    return {"folders": folders, "kind": kind, "merge_into": None, "group": None, **more}


def test_set_agreement_counts_documents_inside_the_acceptable_folders_in_every_run(stability: ModuleType) -> None:
    labels = {"1": label(["customers"]), "2": label(["customers", "meetings"]), "3": label(["ops"]), "4": label(["ideas"])}
    runs = [
        {"1": "customers", "2": "customers", "3": "ops", "4": "ideas"},
        {"1": "customers", "2": "meetings", "3": "ops", "4": "customers"},
    ]

    # 4 is wrong in the second run only: it still does not count.
    assert stability.set_agreement(runs, labels) == pytest.approx(3 / 4)


def test_set_agreement_leaves_out_the_new_topic_documents(stability: ModuleType) -> None:
    labels = {"1": label(["customers"]), "2": label(["whatever"], kind="new")}
    runs = [{"1": "customers", "2": "elsewhere"}, {"1": "customers", "2": "somewhere"}]

    assert stability.set_agreement(runs, labels) == pytest.approx(1.0)


def test_set_agreement_when_every_run_is_inside_but_they_differ_then_still_agrees(stability: ModuleType) -> None:
    labels = {"1": label(["customers", "meetings"])}

    assert stability.set_agreement([{"1": "customers"}, {"1": "meetings"}], labels) == pytest.approx(1.0)


# -- accuracy -----------------------------------------------------------------------------------


def lab(kind: str, folders: list[str], merge_into: str | None = None, group: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "folders": folders, "merge_into": merge_into, "group": group}


LABELS = {
    "a": lab("existing", ["/customers"]),
    "b": lab("ambiguous", ["/customers", "/products"]),
    "c": lab("update", ["/customers"], "customers/x.md"),
    "d": lab("update", ["/ops"], "ops/y.md"),
    "e": lab("new", ["/lumen"], group="g1"),
    "f": {**lab("new", ["/lumen"], group="g1"), "merge_with_doc": "e"},  # joins the note e creates: an allowed merge
    "g": lab("new", ["/other"], group="g2"),
    "h": lab("new", ["/other"], group="g2"),
    "i": lab("existing", ["/ops"]),
}
EXISTING = ["/", "/customers", "/ops", "/products"]


def record(**docs: tuple[str, tuple[str, ...]]) -> dict[str, Any]:
    return {"docs": {d: {"folder": folder, "outcome": outcome} for d, (folder, outcome) in docs.items()}, "existing_folders": EXISTING}


CREATED = ("created",)


def test_accuracy_on_a_hand_built_run(stability: ModuleType) -> None:
    run = record(
        a=("/customers", CREATED),
        b=("/products", CREATED),  # an acceptable alternative
        c=("/customers", ("merged", "customers/x.md")),
        d=("/customers", CREATED),  # wrong folder and not merged
        e=("/coffee", CREATED),
        f=("/coffee", ("merged-with", "e")),  # the group lands together in a new folder
        g=("/tea", CREATED),
        h=("/herbs", CREATED),  # the group is split
        i=("/ops", ("merged", "ops/z.md")),  # a merge nobody wanted
    )

    got = stability.accuracy(run, LABELS)

    assert got["folder_accuracy"] == pytest.approx(4 / 5)  # a, b, c, i right; d wrong; the "new" ones are not counted
    assert got["merge_accuracy"] == pytest.approx(1 / 2)
    assert got["false_merge_rate"] == pytest.approx(1 / 7)  # i, of the seven documents that are not updates
    assert got["group_accuracy"] == pytest.approx(1 / 2)


def test_accuracy_when_everything_is_right_then_all_one_and_no_false_merge(stability: ModuleType) -> None:
    run = record(
        a=("/customers", CREATED), b=("/customers", CREATED), c=("/customers", ("merged", "customers/x.md")),
        d=("/ops", ("merged", "ops/y.md")), e=("/lumen", CREATED), f=("/lumen", ("merged-with", "e")),
        g=("/other", CREATED), h=("/other", CREATED), i=("/ops", CREATED),
    )

    got = stability.accuracy(run, LABELS)

    assert (got["folder_accuracy"], got["merge_accuracy"], got["group_accuracy"], got["false_merge_rate"]) == (1.0, 1.0, 1.0, 0.0)


def test_accuracy_when_an_update_is_merged_into_the_wrong_note_then_it_is_not_a_correct_merge(stability: ModuleType) -> None:
    run = record(c=("/customers", ("merged", "customers/other.md")), d=("/ops", ("merged", "ops/y.md")))

    assert stability.accuracy(run, {k: LABELS[k] for k in "cd"})["merge_accuracy"] == pytest.approx(1 / 2)


def test_accuracy_when_a_group_files_into_a_folder_the_wiki_already_had_then_it_is_not_a_new_folder(stability: ModuleType) -> None:
    run = record(e=("/customers", CREATED), f=("/customers", CREATED))

    assert stability.accuracy(run, {k: LABELS[k] for k in "ef"})["group_accuracy"] == 0.0


def test_accuracy_when_only_some_documents_are_in_the_run_then_only_those_count(stability: ModuleType) -> None:
    run = record(a=("/customers", CREATED), i=("/customers", CREATED))

    assert stability.accuracy(run, LABELS)["folder_accuracy"] == pytest.approx(1 / 2)


# -- the script itself --------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [[], ["--scenario", "a"]], ids=["default", "scenario-a"])
def test_dry_run_exits_zero_and_writes_the_json(tmp_path: Path, extra: list[str]) -> None:
    out = tmp_path / "result.json"

    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--runs", "2", "--out", str(out), *extra],
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
    )

    assert done.returncode == 0, done.stderr[-2000:]
    assert out.is_file() and json.loads(out.read_text(encoding="utf-8"))
