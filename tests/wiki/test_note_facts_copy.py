# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""benchmarks/note_facts.py, phase 0: headline recall read next to the note length and the verbatim share of the note."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.wiki.test_note_facts import CAP, D1, D2, load_script, metric, note, run_main, write

SCRIPT_ARGS = ("--note-chars", str(CAP))

SRC_A = "The quarterly price list states that coffee costs 3.85 EUR a bag and order K-77 shipped from the main warehouse yesterday morning."
SRC_B = "Revision two of the contract says coffee costs 4.10 EUR a bag and payment terms are thirty days after delivery to the customer."
SUMMARY_B = "Brief remarks on the arrangement."

FACT_A = {"fact": "€3.85", "kind": "headline", "section": "P", "offset": 1, "position": "begin", "beyond_100k": False}
FACT_A2 = {"fact": "K-77", "kind": "marginal", "section": "O", "offset": 2, "position": "middle", "beyond_100k": False}
FACT_B = {"fact": "€4.10", "kind": "headline", "section": "P", "offset": 1, "position": "begin", "beyond_100k": False}


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return load_script()


# -- shingles ---------------------------------------------------------------------------------------------------


def test_constants(script: ModuleType) -> None:
    assert script.SHINGLE == 8
    assert pytest.approx(0.3) == script.COPIED


def test_shingles_are_lowercased_word_runs_joined_by_one_space(script: ModuleType) -> None:
    got = script.shingles("One, TWO; three!  four-five", n=3)

    assert got == {"one two three", "two three four", "three four five"}


def test_shingles_ignore_case_and_punctuation_differences(script: ModuleType) -> None:
    assert script.shingles("A b C d", n=2) == script.shingles("a... B, c? D", n=2)


def test_shingles_with_fewer_words_than_n_is_empty(script: ModuleType) -> None:
    assert script.shingles("one two three", n=4) == set()
    assert script.shingles("", n=1) == set()
    assert script.shingles(" ".join(["w"] * 7)) == set()  # the default n is 8


def test_shingles_with_exactly_n_words_is_one_shingle(script: ModuleType) -> None:
    assert script.shingles("a b c", n=3) == {"a b c"}
    assert script.shingles(" ".join(f"w{i}" for i in range(8))) == {" ".join(f"w{i}" for i in range(8))}


# -- verbatim_share ---------------------------------------------------------------------------------------------


def test_a_full_copy_is_one(script: ModuleType) -> None:
    assert script.verbatim_share(SRC_A, [SRC_A]) == pytest.approx(1.0)


def test_a_copy_with_other_case_and_punctuation_is_still_one(script: ModuleType) -> None:
    assert script.verbatim_share(SRC_A.upper().replace(" ", ",  "), [SRC_A]) == pytest.approx(1.0)


def test_disjoint_texts_share_nothing(script: ModuleType) -> None:
    assert script.verbatim_share(SRC_A, [SRC_B]) == 0.0


def test_a_partial_copy_is_the_share_of_the_shingles_of_the_body(script: ModuleType) -> None:
    source = "a1 a2 a3 a4 a5 a6"
    body = "a1 a2 a3 a4 a5 a6 z1 z2"  # n=3: 6 shingles, the first 4 are all in the source
    assert script.verbatim_share(body, [source], n=3) == pytest.approx(4 / 6)


def test_the_share_is_over_the_body_not_over_the_source(script: ModuleType) -> None:
    source = "a1 a2 a3 a4 a5 a6 a7 a8 a9"
    body = "a1 a2 a3 a4"
    assert script.verbatim_share(body, [source], n=3) == pytest.approx(1.0)


def test_a_body_without_shingles_gives_zero(script: ModuleType) -> None:
    assert script.verbatim_share("", [SRC_A]) == 0.0
    assert script.verbatim_share("too short", [SRC_A]) == 0.0


def test_no_sources_gives_zero(script: ModuleType) -> None:
    assert script.verbatim_share(SRC_A, []) == 0.0


def test_several_sources_are_pooled(script: ModuleType) -> None:
    body = "a1 a2 a3 a4 b1 b2 b3 b4"  # n=3: 6 shingles; a1..a4 gives 2, b1..b4 gives 2, the two across sources are in neither
    one = script.verbatim_share(body, ["a1 a2 a3 a4"], n=3)
    both = script.verbatim_share(body, ["a1 a2 a3 a4", "b1 b2 b3 b4"], n=3)

    assert one == pytest.approx(2 / 6)
    assert both == pytest.approx(4 / 6)


def test_sources_can_be_a_generator(script: ModuleType) -> None:
    assert script.verbatim_share(SRC_A, (s for s in [SRC_A])) == pytest.approx(1.0)


# -- score_run, through the script ------------------------------------------------------------------------------


def build(
    tmp_path: Path,
    bodies: dict[str, str | None],
    notes: dict[str, str | None] | None = None,
    sources: dict[str, str] | None = None,
    facts: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Path]:
    """One variant, one run. bodies: note path -> body (kept in the wiki); notes: document -> note path (None: no note)."""
    sources = sources or {D1: SRC_A, D2: SRC_B}
    notes = notes or {D1: "f/a.md", D2: "f/b.md"}
    facts = facts or {D1: [FACT_A, FACT_A2], D2: [FACT_B]}
    corpus = tmp_path / "corpus"
    labels = {}
    for doc, text in sources.items():
        write(corpus / doc, text)
        labels[doc] = {"kind": "new", "folders": ["/f"], "merge_into": None, "group": None, "facts": facts.get(doc, [])}
    write(corpus / "labels.json", json.dumps({"docs": labels}))
    keep = tmp_path / "keep"
    for rel, body in bodies.items():
        if body is not None:
            write(keep / "v" / "run1" / rel, note(rel, body))
    docs = {doc: {"folder": "/f", "outcome": ["created"], "note": notes.get(doc)} for doc in sources}
    results = tmp_path / "results.json"
    write(results, json.dumps({"scenario": "b", "variants": {"v": {"runs": [{"docs": docs}]}}}))
    return {"corpus": corpus, "keep": keep, "results": results, "out": tmp_path / "facts.json"}


def run(script: ModuleType, inputs: dict[str, Path]) -> dict[str, Any]:
    argv = ["--corpus", str(inputs["corpus"]), "--keep", str(inputs["keep"]), "--raw", str(inputs["results"]), "--out", str(inputs["out"])]
    assert run_main(script, [*argv, *SCRIPT_ARGS]) == 0
    return json.loads(inputs["out"].read_text(encoding="utf-8"))


def scored(script: ModuleType, inputs: dict[str, Path]) -> dict[str, Any]:
    """The run's records from `score_run` (the --out JSON holds only the means, as before)."""
    labels = json.loads((inputs["corpus"] / "labels.json").read_text(encoding="utf-8"))["docs"]
    facts = {name: label["facts"] for name, label in labels.items()}
    texts = {name: (inputs["corpus"] / name).read_text(encoding="utf-8") for name in labels}
    docs = json.loads(inputs["results"].read_text(encoding="utf-8"))["variants"]["v"]["runs"][0]["docs"]
    return script.score_run(inputs["keep"] / "v" / "run1", docs, facts, texts, inputs["corpus"] / "no-base", None)


def dicts_with(obj: Any, *keys: str) -> list[dict[str, Any]]:
    """Every dict, at any depth, that has all the keys."""
    found: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        if all(k in obj for k in keys):
            found.append(obj)
        for value in obj.values():
            found += dicts_with(value, *keys)
    elif isinstance(obj, list):
        for value in obj:
            found += dicts_with(value, *keys)
    return found


def note_record(out: dict[str, Any], suffix: str) -> dict[str, Any]:
    found = [r for r in dicts_with(out, "note", "verbatim") if str(r["note"]).endswith(suffix)]
    assert found, f"no note record for {suffix}"
    return found[0]


def test_a_copied_note_has_its_sources_length_ratio_and_verbatim_share(script: ModuleType, tmp_path: Path) -> None:
    out = scored(script, build(tmp_path, {"f/a.md": SRC_A, "f/b.md": SUMMARY_B}))

    copy = note_record(out, "a.md")
    assert copy["source_chars"] == len(SRC_A)
    assert copy["ratio"] == pytest.approx(len(SRC_A) / len(SRC_A), rel=0.05)
    assert copy["verbatim"] == pytest.approx(1.0)
    assert copy["copied"] is True


def test_a_summary_is_short_against_its_source_and_not_copied(script: ModuleType, tmp_path: Path) -> None:
    out = scored(script, build(tmp_path, {"f/a.md": SRC_A, "f/b.md": SUMMARY_B}))

    summary = note_record(out, "b.md")
    assert summary["source_chars"] == len(SRC_B)
    assert summary["ratio"] == pytest.approx(len(SUMMARY_B) / len(SRC_B), rel=0.1)
    assert summary["verbatim"] == 0.0
    assert summary["copied"] is False


def test_the_copied_flag_flips_exactly_at_the_threshold(script: ModuleType, tmp_path: Path) -> None:
    source = "s1 s2 s3 s4 s5 s6 s7 s8 s9 s10 s11 s12"
    at = "s1 s2 s3 s4 s5 s6 s7 s8 s9 s10 n1 n2 n3 n4 n5 n6 n7"  # 17 words, 10 shingles, the first 3 in the source: 0.3
    under = "s1 s2 s3 s4 s5 s6 s7 s8 s9 n0 n1 n2 n3 n4 n5 n6 n7"  # the first 2 only: 0.2
    assert script.verbatim_share(at, [source]) == pytest.approx(0.3)
    assert script.verbatim_share(under, [source]) == pytest.approx(0.2)

    inputs = build(tmp_path, {"f/a.md": at, "f/b.md": under}, sources={D1: source, D2: source}, facts={D1: [], D2: []})
    out = scored(script, inputs)

    assert note_record(out, "a.md")["copied"] is True
    assert note_record(out, "b.md")["copied"] is False


def test_two_documents_in_one_note_pool_their_sources_and_share_the_copied_flag(script: ModuleType, tmp_path: Path) -> None:
    both = f"{SRC_A} {SRC_B}"
    inputs = build(tmp_path, {"f/a.md": both}, notes={D1: "f/a.md", D2: "f/a.md"})
    out = scored(script, inputs)

    record = note_record(out, "a.md")
    assert record["source_chars"] == len(SRC_A) + len(SRC_B)
    assert record["verbatim"] == pytest.approx(script.verbatim_share(both, [SRC_A, SRC_B]))  # the shingles across the join are in neither
    flags = [r["copied"] for r in dicts_with(out, "fact", "copied")]
    assert flags and all(flag is True for flag in flags)


def test_a_document_without_a_note_has_its_facts_not_copied(script: ModuleType, tmp_path: Path) -> None:
    out = scored(script, build(tmp_path, {"f/a.md": SRC_A}, notes={D1: "f/a.md", D2: None}))

    by_fact = {r["fact"]: r["copied"] for r in dicts_with(out, "fact", "copied")}
    assert by_fact[FACT_B["fact"]] is False
    assert by_fact[FACT_A["fact"]] is True
    assert by_fact[FACT_A2["fact"]] is True


def test_every_fact_record_has_the_copied_flag_of_the_note_of_its_document(script: ModuleType, tmp_path: Path) -> None:
    out = scored(script, build(tmp_path, {"f/a.md": SRC_A, "f/b.md": SUMMARY_B}))

    records = dicts_with(out, "fact", "kind", "copied")
    assert {r["fact"] for r in records} >= {"€3.85", "K-77", "€4.10"}
    assert {r["fact"]: r["copied"] for r in records} == {"€3.85": True, "K-77": True, "€4.10": False}


def test_recall_is_split_by_summarized_and_copied_in_the_json(script: ModuleType, tmp_path: Path) -> None:
    out = run(script, build(tmp_path, {"f/a.md": SRC_A, "f/b.md": SUMMARY_B}))

    def one(name: str) -> list[float]:
        return metric(out, name, without=(D1, D2))

    assert one("recall_headline_copied") and all(v == pytest.approx(1.0) for v in one("recall_headline_copied"))  # €3.85 copied and found
    assert one("recall_headline_summarized") and all(v == 0.0 for v in one("recall_headline_summarized"))  # €4.10 not in the summary
    assert one("recall_marginal_copied") and all(v == pytest.approx(1.0) for v in one("recall_marginal_copied"))


def test_the_new_keys_are_in_the_json_next_to_the_old_ones(script: ModuleType, tmp_path: Path) -> None:
    text = json.dumps(run(script, build(tmp_path, {"f/a.md": SRC_A, "f/b.md": SUMMARY_B})))

    for name in ("note_ratio", "verbatim", "copied_share", "recall_headline_summarized", "recall_marginal_copied"):
        assert name in text
    for name in ("fact_recall", "superseded_ok", "superseded_kept", "invented_numbers", "note_chars"):
        assert name in text


def test_copied_share_over_the_notes_in_the_json(script: ModuleType, tmp_path: Path) -> None:
    out = run(script, build(tmp_path, {"f/a.md": SRC_A, "f/b.md": SUMMARY_B}))

    assert any(v == pytest.approx(0.5) for v in metric(out, "copied_share", without=(D1, D2)))


# -- recall_metrics ---------------------------------------------------------------------------------------------


def fact(kind: str, found: bool, copied: bool | None = None) -> dict[str, Any]:
    """A fact record as the metrics read it; `copied` None leaves the key out (an old record)."""
    record: dict[str, Any] = {"fact": "x", "kind": kind, "found": found, "position": "begin", "beyond_100k": False}
    if copied is not None:
        record["copied"] = copied
    return record


def test_the_four_splits_are_the_share_found_in_each_kind_and_copied_group(script: ModuleType) -> None:
    records = [
        fact("headline", True, False),
        fact("headline", False, False),
        fact("headline", True, True),
        fact("headline", True, True),
        fact("marginal", False, False),
        fact("marginal", True, True),
        fact("marginal", False, True),
        fact("marginal", False, True),
    ]

    got = script.recall_metrics(records)

    assert got["recall_headline_summarized"] == pytest.approx(0.5)
    assert got["recall_headline_copied"] == pytest.approx(1.0)
    assert got["recall_marginal_summarized"] == pytest.approx(0.0)
    assert got["recall_marginal_copied"] == pytest.approx(1 / 3)


def test_a_split_without_records_is_none(script: ModuleType) -> None:
    got = script.recall_metrics([fact("headline", True, False), fact("marginal", True, False)])

    assert got["recall_headline_copied"] is None
    assert got["recall_marginal_copied"] is None
    assert got["recall_headline_summarized"] == pytest.approx(1.0)


def test_with_no_records_every_split_is_none(script: ModuleType) -> None:
    got = script.recall_metrics([])

    for name in ("recall_headline_summarized", "recall_headline_copied", "recall_marginal_summarized", "recall_marginal_copied"):
        assert got[name] is None


def test_records_without_the_copied_key_are_left_out_of_the_splits_only(script: ModuleType) -> None:
    old = [fact("headline", False), fact("headline", False)]
    with_flag = [fact("headline", True, True)]

    got = script.recall_metrics(old + with_flag)

    assert got["recall_headline_copied"] == pytest.approx(1.0)
    assert got["recall_headline_summarized"] is None
    assert got["recall_headline"] == pytest.approx(1 / 3)  # the old key still counts every record


def test_only_old_records_leave_the_splits_none_and_the_old_keys_alone(script: ModuleType) -> None:
    got = script.recall_metrics([fact("headline", True), fact("marginal", False)])

    assert all(got[n] is None for n in ("recall_headline_summarized", "recall_headline_copied", "recall_marginal_summarized"))
    assert got["recall_headline"] == pytest.approx(1.0)
    assert got["recall_marginal"] == pytest.approx(0.0)


# -- note_metrics -----------------------------------------------------------------------------------------------


def n(chars: int, ratio: float | None, verbatim: float, copied: bool) -> dict[str, Any]:
    return {"note": "f/a.md", "chars": chars, "invented": 0, "source_chars": 100, "ratio": ratio, "verbatim": verbatim, "copied": copied}


def test_note_metrics_means_and_copied_share(script: ModuleType) -> None:
    got = script.note_metrics([n(50, 0.5, 0.0, False), n(100, 1.0, 1.0, True), n(150, 1.5, 0.5, True), n(10, 0.1, 0.1, False)], CAP)

    assert got["note_ratio"] == pytest.approx((0.5 + 1.0 + 1.5 + 0.1) / 4)
    assert got["verbatim"] == pytest.approx((0.0 + 1.0 + 0.5 + 0.1) / 4)
    assert got["copied_share"] == pytest.approx(0.5)


def test_note_metrics_of_no_notes_has_the_new_keys_none(script: ModuleType) -> None:
    got = script.note_metrics([], CAP)

    assert got["note_ratio"] is None
    assert got["verbatim"] is None
    assert got["copied_share"] is None


def test_note_metrics_accepts_old_records_with_only_chars_and_invented(script: ModuleType) -> None:
    got = script.note_metrics([{"chars": 50, "invented": 0}, {"chars": 200, "invented": 1}], CAP)

    assert got["note_ratio"] is None
    assert got["verbatim"] is None
    assert got["copied_share"] is None
    assert "note_chars" in got  # the old keys are still there
    assert got["note_chars"] == pytest.approx(125)


def test_note_ratio_skips_the_notes_whose_ratio_is_none(script: ModuleType) -> None:
    got = script.note_metrics([n(50, None, 0.0, False), n(100, 2.0, 1.0, True), n(100, 1.0, 0.0, False)], CAP)

    assert got["note_ratio"] == pytest.approx(1.5)
    assert got["verbatim"] == pytest.approx(1 / 3)  # the other means still take every note
    assert got["copied_share"] == pytest.approx(1 / 3)


# -- the report -------------------------------------------------------------------------------------------------

FIRST_ROWS = [
    "recall_headline",
    "recall_headline_summarized",
    "recall_headline_copied",
    "note_ratio",
    "copied_share",
    "recall_marginal",
    "recall_marginal_summarized",
]


def shown_rows(shown: str) -> list[str]:
    """The first word of every line (borders and bars left out) before the "per document" tables."""
    rows = []
    for line in shown.splitlines():
        if "per document" in line:
            break
        match = re.match(r"^[\s|]*([a-z_0-9]+)", line)
        if match:
            rows.append(match.group(1))
    return rows


def test_the_report_prints_the_new_rows_first_and_in_order(script: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(script, build(tmp_path, {"f/a.md": SRC_A, "f/b.md": SUMMARY_B}))

    rows = shown_rows(capsys.readouterr().out)

    start = rows.index("recall_headline")
    assert rows[start : start + len(FIRST_ROWS)] == FIRST_ROWS
    later = rows[start + len(FIRST_ROWS) :]
    assert not set(FIRST_ROWS) & set(later)  # no row twice


def test_the_report_has_a_recall_headline_table_per_document_after_the_fact_recall_one(
    script: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(script, build(tmp_path, {"f/a.md": SRC_A, "f/b.md": SUMMARY_B}))

    shown = capsys.readouterr().out

    assert "fact_recall per document" in shown
    assert "recall_headline per document" in shown
    assert shown.index("fact_recall per document") < shown.index("recall_headline per document")
    table = shown[shown.index("recall_headline per document") :]
    assert D1 in table and D2 in table


def test_the_old_report_rows_are_still_printed(script: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(script, build(tmp_path, {"f/a.md": SRC_A, "f/b.md": SUMMARY_B}))

    shown = capsys.readouterr().out

    for name in ("fact_recall", "superseded_ok", "superseded_kept", "invented_numbers", "note_chars"):
        assert name in shown
