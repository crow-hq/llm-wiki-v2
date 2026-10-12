# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The step-3 rules of benchmarks/stability.py (with the deep base as a `--base`). Free, no model.

Written from the spec `benchmarks/results/specs/spec-giro2-passo3-deep.md` (with its revision R1-R8), not from the code.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from llmw2.agents.base import Decision
from tests.wiki.test_stability_metrics import load

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "src" / "llmw2" / "demo"
BASE = ROOT / "benchmarks" / "corpus" / "deep" / "base"


@pytest.fixture(scope="module")
def stability() -> ModuleType:
    return load()


# -- stability.py (spec 1, R1, R2, R7) ---------------------------------------------------------------


def fake_report(*decisions: Decision) -> Any:
    result = SimpleNamespace(action="created", note="x/n.md", folder="/ops", decisions=list(decisions))
    return SimpleNamespace(results=[(SimpleNamespace(filename="d.md"), result)])


def decision(step: str, choice: str, scores: dict[str, float] | None = None) -> Decision:
    return Decision(step, "classifier", choice, 0.8, False, scores or {})


def test_decision_records_keep_the_scores_of_the_classifier(stability: ModuleType) -> None:
    # "Each decision record in the run JSON gains `"scores"` from `Decision.scores`"
    docs = stability.outcomes_of(fake_report(decision("match", "ops/a.md", {"ops/a.md": 0.9, "ops/b.md": 0.1})), set())

    assert docs["d.md"]["decisions"][0]["scores"] == {"ops/a.md": 0.9, "ops/b.md": 0.1}


def test_decision_records_without_scores_have_an_empty_dict(stability: ModuleType) -> None:
    # "(empty dict when the decision has none)"
    docs = stability.outcomes_of(fake_report(decision("route", "/ops (here)")), set())

    assert docs["d.md"]["decisions"][0]["scores"] == {}


def test_a_route_step_record_has_the_folder_the_choice_was_asked_at(stability: ModuleType) -> None:
    # R1: "for a `route_step`, `"folder"`: the label of the folder the Choice was asked at (parse it from the choice string)"
    docs = stability.outcomes_of(
        fake_report(
            decision("route_step", "/: products | customers", {"products": 0.6, "customers": 0.3}),
            decision("route_step", "/ideas: products (2 votes)", {"products": 0.7}),
        ),
        set(),
    )
    steps = docs["d.md"]["decisions"]

    assert [d["folder"] for d in steps] == ["/", "/ideas"]
    assert steps[1]["scores"] == {"products": 0.7}


def test_the_record_still_has_its_older_fields(stability: ModuleType) -> None:
    # "Nothing else changes in the JSON"
    docs = stability.outcomes_of(fake_report(decision("route_step", "/: a")), set())

    assert {"step", "decider", "choice", "confidence", "fallback"} <= set(docs["d.md"]["decisions"][0])


def write_corpus(tmp: Path) -> Path:
    folder = tmp / "c"
    if not folder.exists():
        folder.mkdir()
        (folder / "a.md").write_text("# A\n\ntopic text\n", encoding="utf-8")
        label = {"kind": "existing", "folders": ["/customers"], "merge_into": None, "group": None}
        (folder / "labels.json").write_text(json.dumps({"docs": {"a.md": label}}), encoding="utf-8")
    return folder


def run_cli(stability: ModuleType, tmp: Path, *extra: str) -> int:
    argv = ["--dry-run", "--runs", "1", "--corpus", str(write_corpus(tmp)), *extra]
    try:
        return int(stability.main(argv) or 0)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 1


def result_of(out: Path) -> dict[str, Any]:
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_scores_reach_the_json_of_a_dry_run(stability: ModuleType, tmp_path: Path) -> None:
    # "Each decision record in the run JSON gains `scores`": through a whole (fake model) run
    out = tmp_path / "r.json"

    assert run_cli(stability, tmp_path, "--out", str(out)) == 0
    for run in result_of(out)["variants"]["base"]["runs"]:
        for doc in run["docs"].values():
            assert all("scores" in d for d in doc["decisions"])


def test_base_option_is_recorded_in_the_variant_args(stability: ModuleType, tmp_path: Path) -> None:
    # "`--base DIR` ... The run JSON records `"base"` (the path given, or `"demo"`) in the variant `args`"
    base = tmp_path / "mybase"
    shutil.copytree(DEMO, base)
    out = tmp_path / "r.json"

    assert run_cli(stability, tmp_path, "--base", str(base), "--out", str(out)) == 0
    assert result_of(out)["variants"]["base"]["args"]["base"] == str(base)


def test_without_base_the_args_say_demo(stability: ModuleType, tmp_path: Path) -> None:
    # "(default: the demo, as today) ... `"demo"`"
    out = tmp_path / "r.json"

    assert run_cli(stability, tmp_path, "--out", str(out)) == 0
    assert result_of(out)["variants"]["base"]["args"]["base"] == "demo"


def test_base_starts_scenario_b_from_that_wiki(stability: ModuleType, tmp_path: Path) -> None:
    # "`--base DIR`: the wiki scenario B starts from": the folders of the base are the existing ones of the run
    out = tmp_path / "r.json"

    assert run_cli(stability, tmp_path, "--base", str(BASE), "--out", str(out)) == 0
    assert "/operations/roasting/profiles" in result_of(out)["variants"]["base"]["runs"][0]["existing_folders"]


def test_several_fallback_menus_make_one_variant_each_named_menu(stability: ModuleType, tmp_path: Path) -> None:
    # R2: "with more than one, each menu is a variant of its own (named `menu-visited`, `menu-tree`, …)"
    out = tmp_path / "r.json"

    assert run_cli(stability, tmp_path, "--fallback-menu", "visited", "tree", "--runs", "2", "--out", str(out)) == 0
    variants = result_of(out)["variants"]
    assert list(variants) == ["menu-visited", "menu-tree"]
    assert all(len(v["runs"]) == 2 for v in variants.values())


def test_one_fallback_menu_keeps_working_as_today(stability: ModuleType, tmp_path: Path) -> None:
    # R2: "Keep `--fallback-menu visited` (one value) working as today"
    out = tmp_path / "r.json"

    assert run_cli(stability, tmp_path, "--fallback-menu", "visited", "--out", str(out)) == 0
    assert list(result_of(out)["variants"]) == ["base"]


def test_a_menu_the_core_does_not_have_is_refused(stability: ModuleType, tmp_path: Path) -> None:
    out = tmp_path / "r.json"

    assert run_cli(stability, tmp_path, "--fallback-menu", "scored", "--out", str(out)) != 0
    assert not out.exists()


# -- folder_prefix_accuracy (R7) -----------------------------------------------------------------------


def lab(kind: str, folders: list[str]) -> dict[str, Any]:
    return {"kind": kind, "folders": folders, "merge_into": None, "group": None}


def run_in(**docs: str) -> dict[str, Any]:
    quality = dict.fromkeys(
        ("folders", "near_duplicate_folders", "notes", "merge_rate", "links", "check_problems", "not_done", "seconds", "cost_usd"), 0
    )
    return {
        "docs": {d: {"folder": folder, "outcome": ["created"]} for d, folder in docs.items()},
        "existing_folders": ["/ops"],
        "providers": {},
        "quality": quality,
    }


def test_folder_prefix_accuracy_counts_a_folder_below_an_acceptable_one_as_right(stability: ModuleType) -> None:
    # R7: "a document is right also when its folder lies below an acceptable folder"
    labels = {"a.md": lab("existing", ["/ops"]), "b.md": lab("existing", ["/ops"]), "c.md": lab("existing", ["/ops"])}
    run = run_in(**{"a.md": "/ops", "b.md": "/ops/roasting/new-sub", "c.md": "/other"})

    result = stability.accuracy(run, labels)

    assert result["folder_prefix_accuracy"] == pytest.approx(2 / 3)
    assert result["folder_accuracy"] == pytest.approx(1 / 3)


def test_folder_prefix_accuracy_does_not_treat_a_sibling_with_the_same_start_as_below(stability: ModuleType) -> None:
    # R7: "lies below": "/ops2" is not below "/ops"
    result = stability.accuracy(run_in(**{"a.md": "/ops2"}), {"a.md": lab("existing", ["/ops"])})

    assert result["folder_prefix_accuracy"] == pytest.approx(0.0)


def test_folder_prefix_accuracy_leaves_out_the_new_documents_like_folder_accuracy(stability: ModuleType) -> None:
    # R7: "like `folder_accuracy`"
    labels = {"a.md": lab("existing", ["/ops"]), "n.md": lab("new", [])}

    result = stability.accuracy(run_in(**{"a.md": "/ops/x", "n.md": "/fresh"}), labels)

    assert result["folder_prefix_accuracy"] == pytest.approx(1.0)


def test_recompute_reports_the_prefix_accuracy_and_reads_old_jsons_without_scores(
    stability: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # R7 "(and `--recompute`)"; "`--recompute` must still read old JSONs without it [scores]"
    corpus = tmp_path / "c"
    corpus.mkdir()
    (corpus / "labels.json").write_text(json.dumps({"docs": {"a.md": lab("existing", ["/ops"])}}), encoding="utf-8")
    run = run_in(**{"a.md": "/ops/sub"})
    old_decision = {"step": "route_step", "decider": "classifier", "choice": "/: ops", "confidence": 0.9, "fallback": False}
    run["docs"]["a.md"]["decisions"] = [old_decision]
    raw = tmp_path / "old.json"
    raw.write_text(json.dumps({"scenario": "b", "model": "fake", "variants": {"base": {"runs": [run]}}}), encoding="utf-8")

    try:
        code = int(stability.main(["--recompute", str(raw), "--corpus", str(corpus)]) or 0)
    except SystemExit as stop:
        code = stop.code if isinstance(stop.code, int) else 1

    out = capsys.readouterr().out
    assert code == 0
    assert re.search(r"^folder_prefix_accuracy\s+1\b", out, re.M)
    assert re.search(r"^folder_accuracy\s+0\b", out, re.M)
