# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Phase 2 of benchmarks/stability.py: richer labels and metrics (B2), several corpora and --only (B3), --recompute (B6)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.wiki.test_stability_metrics import load

CREATED = ["created"]
EXISTING = ["/", "/customers", "/ops"]


@pytest.fixture(scope="module")
def stability() -> ModuleType:
    return load()


def lab(kind: str, folders: list[str], **more: Any) -> dict[str, Any]:
    """A label; `merge_into` and `group` default to null, any other field is passed as given (or left out)."""
    return {"kind": kind, "folders": folders, "merge_into": None, "group": None, **more}


def run_of(**docs: tuple[str, list[str]]) -> dict[str, Any]:
    # `quality` and `providers` are there as in the raw files stability.py writes (the report averages them).
    quality = dict.fromkeys(
        ("folders", "near_duplicate_folders", "notes", "merge_rate", "links", "check_problems", "not_done", "seconds", "cost_usd"), 0
    )
    return {
        "docs": {d: {"folder": folder, "outcome": outcome} for d, (folder, outcome) in docs.items()},
        "existing_folders": EXISTING,
        "providers": {},
        "quality": quality,
    }


# -- merge_accuracy -----------------------------------------------------------------------------


def test_merge_accuracy_when_merge_into_is_a_string_then_it_counts_as_a_list_of_one(stability: ModuleType) -> None:
    labels = {"u": lab("update", ["/ops"], merge_into="ops/x.md")}

    right = stability.accuracy(run_of(u=("/ops", ["merged", "ops/x.md"])), labels)["merge_accuracy"]
    wrong = stability.accuracy(run_of(u=("/ops", ["merged", "ops/y.md"])), labels)["merge_accuracy"]

    assert (right, wrong) == (1.0, 0.0)


def test_merge_accuracy_when_merge_into_is_a_list_then_any_of_its_notes_is_right(stability: ModuleType) -> None:
    labels = {"u": lab("update", ["/ops"], merge_into=["ops/x.md", "ops/y.md"])}

    got = [stability.accuracy(run_of(u=("/ops", ["merged", n])), labels)["merge_accuracy"] for n in ("ops/x.md", "ops/y.md", "ops/z.md")]

    assert got == [1.0, 1.0, 0.0]


def test_merge_accuracy_when_an_update_is_created_or_unchanged_or_failed_then_it_is_not_correct(stability: ModuleType) -> None:
    labels = {"u": lab("update", ["/ops"], merge_into="ops/x.md")}

    got = [stability.accuracy(run_of(u=("/ops", outcome)), labels)["merge_accuracy"] for outcome in (CREATED, ["unchanged"], ["error"])]

    assert got == [0.0, 0.0, 0.0]


def test_merge_accuracy_when_the_update_merges_with_the_document_named_then_it_is_correct(stability: ModuleType) -> None:
    labels = {"first": lab("new", ["/lumen"]), "u": lab("update", ["/lumen"], merge_with_doc="first")}
    right = run_of(first=("/lumen", CREATED), u=("/lumen", ["merged-with", "first"]))
    wrong = run_of(first=("/lumen", CREATED), u=("/lumen", ["merged-with", "someone-else"]))

    assert stability.accuracy(right, labels)["merge_accuracy"] == 1.0
    assert stability.accuracy(wrong, labels)["merge_accuracy"] == 0.0


def test_merge_accuracy_when_the_update_wants_a_document_but_merges_into_a_note_then_it_is_not_correct(stability: ModuleType) -> None:
    labels = {"u": lab("update", ["/ops"], merge_with_doc="first")}

    assert stability.accuracy(run_of(u=("/ops", ["merged", "ops/x.md"])), labels)["merge_accuracy"] == 0.0


def test_merge_accuracy_when_there_is_no_update_document_then_one(stability: ModuleType) -> None:
    labels = {"a": lab("existing", ["/ops"])}

    assert stability.accuracy(run_of(a=("/ops", CREATED)), labels)["merge_accuracy"] == 1.0


def test_merge_accuracy_counts_each_update_by_its_own_rule(stability: ModuleType) -> None:
    labels = {
        "n": lab("update", ["/ops"], merge_into="ops/x.md"),
        "l": lab("update", ["/ops"], merge_into=["ops/x.md", "ops/y.md"]),
        "d": lab("update", ["/ops"], merge_with_doc="first"),
        "w": lab("update", ["/ops"], merge_into="ops/x.md"),
    }
    run = run_of(
        n=("/ops", ["merged", "ops/x.md"]), l=("/ops", ["merged", "ops/y.md"]), d=("/ops", ["merged-with", "first"]), w=("/ops", CREATED)
    )

    assert stability.accuracy(run, labels)["merge_accuracy"] == pytest.approx(3 / 4)


# -- false_merge_rate ---------------------------------------------------------------------------


def test_false_merge_rate_when_a_document_merges_into_an_allowed_note_then_it_is_not_a_false_merge(stability: ModuleType) -> None:
    labels = {
        "a": lab("existing", ["/ops"], merge_ok=["ops/ok.md"]),
        "b": lab("existing", ["/ops"], merge_into="ops/own.md"),
        "c": lab("existing", ["/ops"], merge_into=["ops/p.md", "ops/q.md"]),
    }
    run = run_of(a=("/ops", ["merged", "ops/ok.md"]), b=("/ops", ["merged", "ops/own.md"]), c=("/ops", ["merged", "ops/q.md"]))

    assert stability.accuracy(run, labels)["false_merge_rate"] == 0.0


def test_false_merge_rate_when_a_document_merges_into_a_note_nobody_allowed_then_it_is_a_false_merge(stability: ModuleType) -> None:
    labels = {
        "a": lab("existing", ["/ops"], merge_ok=["ops/ok.md"]),
        "b": lab("existing", ["/ops"]),
        "c": lab("ambiguous", ["/ops"]),
        "d": lab("new", ["/lumen"]),
    }
    run = run_of(a=("/ops", ["merged", "ops/other.md"]), b=("/ops", ["merged", "ops/z.md"]), c=("/ops", CREATED), d=("/lumen", CREATED))

    assert stability.accuracy(run, labels)["false_merge_rate"] == pytest.approx(2 / 4)


def test_false_merge_rate_when_merged_with_a_document_then_only_the_named_one_is_allowed(stability: ModuleType) -> None:
    labels = {
        "first": lab("new", ["/lumen"]),
        "ok": lab("new", ["/lumen"], merge_with_doc="first"),
        "bad": lab("new", ["/lumen"], merge_with_doc="first"),
        "free": lab("new", ["/lumen"]),
    }
    run = run_of(
        first=("/lumen", CREATED),
        ok=("/lumen", ["merged-with", "first"]),
        bad=("/lumen", ["merged-with", "free"]),
        free=("/lumen", ["merged-with", "first"]),  # nothing named it as a target: a false merge
    )

    assert stability.accuracy(run, labels)["false_merge_rate"] == pytest.approx(2 / 4)


def test_false_merge_rate_when_a_document_wants_another_document_but_merges_into_a_note_then_it_is_false(stability: ModuleType) -> None:
    labels = {"ok": lab("new", ["/lumen"], merge_with_doc="first")}

    assert stability.accuracy(run_of(ok=("/lumen", ["merged", "ops/x.md"])), labels)["false_merge_rate"] == 1.0


def test_false_merge_rate_when_every_document_is_an_update_then_zero(stability: ModuleType) -> None:
    labels = {"u": lab("update", ["/ops"], merge_into="ops/x.md")}

    assert stability.accuracy(run_of(u=("/ops", ["merged", "ops/other.md"])), labels)["false_merge_rate"] == 0.0


# -- new_accuracy and single_doc_folders --------------------------------------------------------


def test_new_accuracy_when_new_documents_land_in_new_or_existing_or_failed_folders_then_only_new_folders_count(
    stability: ModuleType,
) -> None:
    labels = {k: lab("new", ["/lumen"]) for k in "abcd"}
    run = run_of(a=("/coffee", CREATED), b=("/customers", CREATED), c=("!error", ["error"]), d=("/tea", CREATED))

    assert stability.accuracy(run, labels)["new_accuracy"] == pytest.approx(2 / 4)


def test_new_accuracy_leaves_out_the_documents_that_are_not_new(stability: ModuleType) -> None:
    labels = {"a": lab("new", ["/lumen"]), "b": lab("existing", ["/ops"])}
    run = run_of(a=("/coffee", CREATED), b=("/customers", CREATED))

    assert stability.accuracy(run, labels)["new_accuracy"] == 1.0


def test_new_accuracy_when_there_is_no_new_document_then_one(stability: ModuleType) -> None:
    assert stability.accuracy(run_of(a=("/ops", CREATED)), {"a": lab("existing", ["/ops"])})["new_accuracy"] == 1.0


def test_single_doc_folders_counts_the_created_folders_with_exactly_one_document(stability: ModuleType) -> None:
    labels = {k: lab("new", ["/lumen"]) for k in "abcdefg"}
    run = run_of(
        a=("/solo1", CREATED),  # alone in a new folder
        b=("/pair", CREATED),
        c=("/pair", CREATED),  # two in one: not counted
        d=("/solo2", CREATED),  # alone in a new folder
        e=("/ops", CREATED),  # an existing folder: not counted
        f=("!error", ["error"]),
        g=("!error", ["error"]),  # not folders
    )

    assert stability.accuracy(run, labels)["single_doc_folders"] == 2


def test_single_doc_folders_when_every_new_folder_holds_several_documents_then_zero(stability: ModuleType) -> None:
    labels = {"a": lab("new", ["/lumen"]), "b": lab("new", ["/lumen"])}

    assert stability.accuracy(run_of(a=("/x", CREATED), b=("/x", CREATED)), labels)["single_doc_folders"] == 0


def test_the_new_metrics_are_in_the_accuracy_result_with_the_older_ones(stability: ModuleType) -> None:
    got = stability.accuracy(run_of(a=("/ops", CREATED)), {"a": lab("existing", ["/ops"])})

    assert {"folder_accuracy", "merge_accuracy", "false_merge_rate", "group_accuracy", "new_accuracy", "single_doc_folders"} <= set(got)


# -- backward compatibility ---------------------------------------------------------------------


def test_accuracy_when_the_labels_have_only_the_old_fields_then_it_works_as_before(stability: ModuleType) -> None:
    # No merge_ok, merge_with_doc or secondary; merge_into a plain string, as the existing labels.json files have it.
    labels = {
        "a": {"kind": "existing", "folders": ["/customers"], "merge_into": None, "group": None},
        "u": {"kind": "update", "folders": ["/ops"], "merge_into": "ops/x.md", "group": None},
        "e": {"kind": "new", "folders": ["/lumen"], "merge_into": None, "group": "g1"},
        "f": {"kind": "new", "folders": ["/lumen"], "merge_into": None, "group": "g1"},
    }
    run = run_of(a=("/customers", CREATED), u=("/ops", ["merged", "ops/x.md"]), e=("/coffee", CREATED), f=("/coffee", CREATED))

    got = stability.accuracy(run, labels)

    assert (got["folder_accuracy"], got["merge_accuracy"], got["false_merge_rate"], got["group_accuracy"]) == (1.0, 1.0, 0.0, 1.0)


def test_accuracy_when_a_label_has_secondary_folders_then_no_metric_reads_them(stability: ModuleType) -> None:
    labels = {"a": lab("existing", ["/customers"], secondary=["/ops"])}

    assert stability.accuracy(run_of(a=("/ops", CREATED)), labels)["folder_accuracy"] == 0.0


# -- B3: several corpora and --only (through the CLI, --dry-run) --------------------------------


def write_corpus(root: Path, name: str, docs: dict[str, dict[str, Any]]) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    for filename in docs:
        (folder / filename).write_text(f"# {filename}\n\nTopic: {name} {filename}. Some text about it.\n", encoding="utf-8")
    (folder / "labels.json").write_text(json.dumps({"docs": docs}), encoding="utf-8")
    return folder


def runs_in(raw: Any) -> list[dict[str, Any]]:
    """Every run record (a dict with `docs` and `existing_folders`) anywhere in a raw result."""
    if isinstance(raw, dict):
        found = [raw] if "docs" in raw and "existing_folders" in raw else []
        return found + [r for v in raw.values() for r in runs_in(v)]
    if isinstance(raw, list):
        return [r for v in raw for r in runs_in(v)]
    return []


def invoke(stability: ModuleType, argv: list[str]) -> int:
    try:
        return int(stability.main(argv) or 0)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 1
    except Exception:
        return 1


@pytest.fixture
def two_corpora(tmp_path: Path) -> tuple[Path, Path]:
    short = write_corpus(tmp_path, "short", {"a1.md": lab("existing", ["/customers"]), "a2.md": lab("existing", ["/customers"])})
    long = write_corpus(tmp_path, "long", {"b1.md": lab("existing", ["/ops"]), "b2.md": lab("existing", ["/ops"])})
    return short, long


def test_corpus_when_repeated_then_the_documents_of_all_the_folders_are_ingested(
    stability: ModuleType, tmp_path: Path, two_corpora: tuple[Path, Path]
) -> None:
    short, long = two_corpora
    out = tmp_path / "o.json"

    code = invoke(
        stability, ["--dry-run", "--runs", "1", "--variant", "base", "--out", str(out), "--corpus", str(short), "--corpus", str(long)]
    )

    assert code == 0
    runs = runs_in(json.loads(out.read_text(encoding="utf-8")))
    assert runs and all(set(r["docs"]) == {"a1.md", "a2.md", "b1.md", "b2.md"} for r in runs)


def test_only_when_patterns_are_given_then_just_the_matching_documents_are_ingested(
    stability: ModuleType, tmp_path: Path, two_corpora: tuple[Path, Path]
) -> None:
    short, long = two_corpora
    out = tmp_path / "o.json"
    argv = ["--dry-run", "--runs", "1", "--variant", "base", "--out", str(out), "--corpus", str(short), "--corpus", str(long)]

    code = invoke(stability, [*argv, "--only", "long/b1.md", "short/a2*"])

    assert code == 0
    runs = runs_in(json.loads(out.read_text(encoding="utf-8")))
    assert runs and all(set(r["docs"]) == {"b1.md", "a2.md"} for r in runs)


def test_corpus_when_two_documents_share_a_filename_then_it_stops_at_the_start(stability: ModuleType, tmp_path: Path) -> None:
    one = write_corpus(tmp_path, "one", {"same.md": lab("existing", ["/ops"])})
    two = write_corpus(tmp_path, "two", {"same.md": lab("existing", ["/ops"])})
    out = tmp_path / "o.json"

    code = invoke(stability, ["--dry-run", "--runs", "1", "--out", str(out), "--corpus", str(one), "--corpus", str(two)])

    assert code != 0 and not out.exists()


# -- B6: --recompute ----------------------------------------------------------------------------


def metric(report: str, name: str) -> float:
    """The first number on the report row of `name` (the mean)."""
    match = re.search(rf"^{re.escape(name)}\s+(-?[0-9.]+(?:e-?[0-9]+)?)", report, re.MULTILINE)
    assert match, f"no row for {name} in the report:\n{report}"
    return float(match.group(1))


def raw_result(*runs: dict[str, Any], variant: str = "base") -> dict[str, Any]:
    return {"scenario": "b", "model": "fake", "variants": {variant: {"runs": list(runs)}}}


def test_recompute_when_given_a_raw_json_then_it_reports_the_metrics_with_the_labels_of_the_corpus(
    stability: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # ARRANGE
    corpus = write_corpus(
        tmp_path,
        "c",
        {"u.md": lab("update", ["/ops"], merge_into="ops/x.md"), "n.md": lab("new", ["/lumen"]), "e.md": lab("existing", ["/ops"])},
    )
    good = run_of(**{"u.md": ("/ops", ["merged", "ops/x.md"]), "n.md": ("/coffee", CREATED), "e.md": ("/ops", CREATED)})
    bad = run_of(**{"u.md": ("/ops", CREATED), "n.md": ("/ops", CREATED), "e.md": ("/ops", ["merged", "ops/q.md"])})
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(raw_result(good, bad)), encoding="utf-8")

    # ACT
    code = invoke(stability, ["--recompute", str(raw), "--corpus", str(corpus)])

    # ASSERT: means over the two runs
    out = capsys.readouterr().out
    assert code == 0
    assert metric(out, "merge_accuracy") == pytest.approx(0.5)  # right in one run, wrong in the other
    assert metric(out, "new_accuracy") == pytest.approx(0.5)
    assert metric(out, "false_merge_rate") == pytest.approx(0.25)  # 0 in the good run, 1 of 2 in the bad one


def test_recompute_does_not_change_the_json_it_reads_and_writes_nothing_else(stability: ModuleType, tmp_path: Path) -> None:
    corpus = write_corpus(tmp_path, "c", {"e.md": lab("existing", ["/ops"])})
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(raw_result(run_of(**{"e.md": ("/ops", CREATED)}))), encoding="utf-8")
    before = raw.read_bytes()

    code = invoke(stability, ["--recompute", str(raw), "--corpus", str(corpus)])

    assert code == 0 and raw.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c", "raw.json"]


def test_recompute_when_given_several_files_then_each_variant_is_reported(
    stability: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = write_corpus(tmp_path, "c", {"e.md": lab("existing", ["/ops"])})
    first, second = tmp_path / "one.json", tmp_path / "two.json"
    first.write_text(json.dumps(raw_result(run_of(**{"e.md": ("/ops", CREATED)}), variant="alpha")), encoding="utf-8")
    second.write_text(json.dumps(raw_result(run_of(**{"e.md": ("/ops", CREATED)}), variant="omega")), encoding="utf-8")

    invoke(stability, ["--recompute", str(first), str(second), "--corpus", str(corpus)])

    out = capsys.readouterr().out
    assert "alpha" in out and "omega" in out


def test_recompute_with_two_corpora_then_a_group_is_prefixed_with_its_corpus_and_merge_with_doc_stays_a_filename(
    stability: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The same group name "g" in two corpora: two groups. Each is right on its own (all in one new folder).
    one = write_corpus(tmp_path, "one", {"a1.md": lab("new", ["/x"], group="g"), "a2.md": lab("new", ["/x"], group="g")})
    two = write_corpus(
        tmp_path,
        "two",
        {
            "b1.md": lab("new", ["/y"], group="g"),
            "b2.md": lab("new", ["/y"], group="g"),
            "b3.md": lab("update", ["/y"], merge_with_doc="b1.md"),
        },
    )
    run = run_of(
        **{
            "a1.md": ("/alpha", CREATED),
            "a2.md": ("/alpha", ["merged-with", "a1.md"]),
            "b1.md": ("/beta", CREATED),
            "b2.md": ("/beta", CREATED),
            "b3.md": ("/beta", ["merged-with", "b1.md"]),
        }
    )
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(raw_result(run)), encoding="utf-8")

    code = invoke(stability, ["--recompute", str(raw), "--corpus", str(one), "--corpus", str(two)])

    out = capsys.readouterr().out
    assert code == 0
    assert metric(out, "group_accuracy") == pytest.approx(1.0)  # without the prefix the four documents would be one split group
    assert metric(out, "merge_accuracy") == pytest.approx(1.0)
