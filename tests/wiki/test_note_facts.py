# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""benchmarks/note_facts.py (fact recall in the final notes, no model) and `--keep` / `--merge-reads` of benchmarks/stability.py."""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.wiki.test_stability_metrics import load as load_stability

BENCHMARKS = Path(__file__).resolve().parents[2] / "benchmarks"
SCRIPT = BENCHMARKS / "note_facts.py"
CAP = 100  # --note-chars of the runs below: a note over 120 characters is over 1.2 times the cap


def load_script() -> ModuleType:
    sys.path.insert(0, str(BENCHMARKS))
    try:
        spec = importlib.util.spec_from_file_location("benchmarks_note_facts", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(BENCHMARKS))
    return module


def run_main(module: ModuleType, argv: list[str]) -> int:
    try:
        if inspect.signature(module.main).parameters:
            return int(module.main(argv) or 0)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(sys, "argv", [str(SCRIPT), *argv])
            return int(module.main() or 0)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)


def leaves(obj: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Every number of a JSON document with the keys that lead to it (list items add their index)."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield from leaves(value, (*path, str(key)))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from leaves(value, (*path, str(i)))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield path, float(obj)


def metric(out: dict[str, Any], *tokens: str, without: tuple[str, ...] = ()) -> list[float]:
    """The numbers whose path (its keys joined by "/") holds every token and none of `without`: the key names are the script's."""
    paths = [("/".join(p), v) for p, v in leaves(out)]
    return [v for p, v in paths if all(t in p for t in tokens) and not any(w in p for w in without)]


# -- a hand-made corpus, wiki runs and stability results ------------------------------------------------------

D1, D2 = "d1.md", "d2.md"
SOURCE_1 = "Price list. Coffee costs 3.85 EUR a bag. Order K-77 shipped. Stock 12500 kg in the warehouse. Margin 15 percent."
SOURCE_2 = "Revision 2. Coffee costs 4.10 EUR a bag (was 3.85 EUR). Terms are 30 days instead of 45 days. Margin 15 percent."

FACTS_1 = [
    {"fact": "€3.85", "kind": "headline", "section": "Prices", "offset": 20, "position": "begin", "beyond_100k": False},
    {"fact": "K-77", "kind": "marginal", "section": "Orders", "offset": 40, "position": "middle", "beyond_100k": False},
    {"fact": "12 500 kg", "kind": "marginal", "section": "Stock", "offset": 60, "position": "tail", "beyond_100k": True},
]
FACTS_2 = [
    {
        "fact": "€4.10",
        "kind": "headline",
        "section": "Prices",
        "offset": 20,
        "position": "begin",
        "beyond_100k": False,
        "supersedes": "€3.85",
    },
    {
        "fact": "30 days",
        "kind": "headline",
        "section": "Terms",
        "offset": 50,
        "position": "middle",
        "beyond_100k": False,
        "supersedes": "45 days",
    },
]

# The notes of the runs (d1 ends in f/a.md, d2 in f/b.md). What each finds, by the normalized comparison:
#   base run 1: d1 = EUR 3.85 (written "3,85 €") yes, K-77 (written "k-77") yes, 12 500 kg no; one invented number (99421), a long note.
#               d2 = EUR 4.10 (written "4,10 €") yes, 30 days yes; the old price is kept, the old terms are not.
#   base run 2: d1 = EUR 3.85 no, K-77 yes, 12 500 kg yes (written "12500 kg").   d2 = EUR 4.10 yes, 30 days no; no old value.
#   good:       everything, with the old values ("previously"), nothing invented.
BASE_A1 = "Price 3,85 € a bag, k-77 shipped. Ref 99421. " + "x" * 80
BASE_B1 = "Price 4,10 € (previously 3.85 EUR). Terms 30 days."
BASE_A2 = "k-77 shipped; stock 12500 kg."
BASE_B2 = "Price 4,10 € a bag."
GOOD_A = "3.85 EUR, K-77, 12 500 kg."
GOOD_B = "4.10 EUR (previously 3.85 EUR), terms 30 days (previously 45 days)."
OVER = int(1.2 * CAP)
assert len(BASE_A1) > OVER >= max(len(t) for t in (BASE_B1, BASE_A2, BASE_B2, GOOD_A, GOOD_B))


def note(title: str, body: str) -> str:
    """A note file; the frontmatter is long on purpose: it must not count in the lengths."""
    return f"---\ntitle: {title}\ndescription: {'long description ' * 20}\ntags: [a]\n---\n\n{body}\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def stability_json(variants: dict[str, int]) -> dict[str, Any]:
    """Raw results as stability.py writes them: for each variant and run, the note each document ended in."""
    docs = {D1: {"folder": "/f", "outcome": ["created"], "note": "f/a.md"}, D2: {"folder": "/f", "outcome": ["created"], "note": "f/b.md"}}
    return {"scenario": "b", "variants": {name: {"runs": [{"docs": docs} for _ in range(runs)]} for name, runs in variants.items()}}


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    """Corpus with labels, and two variants kept by --keep: "base" (2 runs, imperfect) and "good" (1 run, perfect)."""
    corpus = tmp_path / "corpus"
    write(corpus / D1, SOURCE_1)
    write(corpus / D2, SOURCE_2)
    labels = {
        D1: {"kind": "existing", "folders": ["/f"], "merge_into": None, "group": None, "facts": FACTS_1},
        D2: {"kind": "new", "folders": ["/f"], "merge_into": None, "group": None, "facts": FACTS_2},
    }
    write(corpus / "labels.json", json.dumps({"docs": labels}))

    keep = tmp_path / "keep"
    for variant, run, a, b in [("base", "run1", BASE_A1, BASE_B1), ("base", "run2", BASE_A2, BASE_B2), ("good", "run1", GOOD_A, GOOD_B)]:
        write(keep / variant / run / "f" / "a.md", note("A", a))
        write(keep / variant / run / "f" / "b.md", note("B", b))

    results = tmp_path / "results.json"
    write(results, json.dumps(stability_json({"base": 2, "good": 1})))
    return {"corpus": corpus, "keep": keep, "results": results, "out": tmp_path / "facts.json"}


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return load_script()


def run_facts(script: ModuleType, inputs: dict[str, Path]) -> tuple[int, dict[str, Any]]:
    argv = [
        "--corpus", str(inputs["corpus"]), "--keep", str(inputs["keep"]), "--raw", str(inputs["results"]),
        "--note-chars", str(CAP), "--out", str(inputs["out"]),
    ]  # fmt: skip
    code = run_main(script, argv)
    return code, json.loads(inputs["out"].read_text(encoding="utf-8")) if inputs["out"].is_file() else {}


def found(values: list[float], expected: float) -> bool:
    return any(v == pytest.approx(expected) for v in values)


# -- the script -----------------------------------------------------------------------------------------------


def test_the_script_runs_prints_a_table_per_variant_and_writes_json(
    script: ModuleType, inputs: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run_facts(script, inputs)

    shown = capsys.readouterr().out
    assert code == 0 and out
    assert "base" in shown and "good" in shown
    for name in ("fact_recall", "superseded_ok", "superseded_kept", "invented_numbers", "note_chars"):
        assert name in json.dumps(out)


def test_a_perfect_variant_recalls_every_fact_keeps_the_old_values_and_invents_nothing(script: ModuleType, inputs: dict[str, Path]) -> None:
    _, out = run_facts(script, inputs)

    recall = metric(out, "good", "recall", without=(D1, D2))
    assert recall and all(v == pytest.approx(1.0) for v in recall)
    for name in ("superseded_ok", "superseded_kept"):
        values = metric(out, "good", name, without=(D1, D2))
        assert values and all(v == pytest.approx(1.0) for v in values), name
    assert all(v == 0 for v in metric(out, "good", "invented_numbers"))


def test_recall_by_kind_is_the_mean_over_the_runs_and_money_and_codes_are_compared_normalized(
    script: ModuleType, inputs: dict[str, Path]
) -> None:
    _, out = run_facts(script, inputs)

    # headlines (EUR 3.85, EUR 4.10, 30 days): run 1 finds 3 of 3 ("3,85 €" is "€3.85"), run 2 finds 1 of 3
    assert found(metric(out, "base", "recall", "headline", without=(D1, D2)), (1 + 1 / 3) / 2)
    # marginals (K-77, 12 500 kg): run 1 finds 1 of 2 ("k-77" is "K-77"), run 2 finds 2 of 2 ("12500 kg" is "12 500 kg")
    assert found(metric(out, "base", "recall", "marginal", without=(D1, D2)), 0.75)


def test_recall_by_position_and_beyond_100k(script: ModuleType, inputs: dict[str, Path]) -> None:
    _, out = run_facts(script, inputs)

    def by(token: str) -> list[float]:
        return metric(out, "base", "recall", token, without=(D1, D2))

    assert found(by("begin"), 0.75)  # EUR 3.85 and EUR 4.10: 2 of 2, then 1 of 2
    assert found(by("middle"), 0.75)  # K-77 and 30 days: 2 of 2, then 1 of 2
    assert found(by("tail"), 0.5)  # 12 500 kg: missed in run 1, found in run 2
    assert found(by("beyond_100k"), 0.5)  # the same fact: the only one past 100k


def test_recall_per_document(script: ModuleType, inputs: dict[str, Path]) -> None:
    _, out = run_facts(script, inputs)

    assert found(metric(out, "base", "recall", D1), 2 / 3)  # run 1: 2 of 3, run 2: 2 of 3
    assert found(metric(out, "base", "recall", D2), 0.75)  # run 1: 2 of 2, run 2: 1 of 2


def test_superseded_ok_is_the_share_with_the_new_value_and_kept_the_share_that_also_has_the_old_one(
    script: ModuleType, inputs: dict[str, Path]
) -> None:
    _, out = run_facts(script, inputs)

    # d2 supersedes EUR 3.85 (by 4.10) and 45 days (by 30 days).
    # run 1: both new values are there (ok 1), the old price is kept and the old terms are not (kept 1/2); run 2: ok 1/2, kept 0
    assert found(metric(out, "base", "superseded_ok", without=(D1, D2)), 0.75)
    assert found(metric(out, "base", "superseded_kept", without=(D1, D2)), 0.25)


def test_invented_numbers_are_the_numbers_of_the_note_that_the_source_does_not_have(script: ModuleType, inputs: dict[str, Path]) -> None:
    _, out = run_facts(script, inputs)

    # 99421 in run 1 is the only number of the notes that no source has; "3.85 EUR" and "4,10 €" are in theirs, however written
    base, good = metric(out, "base", "invented_numbers"), metric(out, "good", "invented_numbers")
    assert base and any(v > 0 for v in base)
    assert good and all(v == 0 for v in good)


def test_note_chars_is_the_body_without_the_frontmatter_and_the_share_over_one_point_two_times_the_cap(
    script: ModuleType, inputs: dict[str, Path]
) -> None:
    _, out = run_facts(script, inputs)

    mean = sum(len(t) for t in (BASE_A1, BASE_B1, BASE_A2, BASE_B2)) / 4
    values = metric(out, "base", "note_chars", without=(D1, D2))
    assert values and all(v < 400 for v in values)  # the frontmatter alone is over 300 characters: it is left out
    assert any(abs(v - mean) <= 3 for v in values)  # the mean length of the four bodies (a trailing newline may count)
    assert found(metric(out, "base", "over", without=(D1, D2)), 0.25)  # one of the four notes is over 120 characters


def test_the_share_over_the_cap_is_zero_for_short_notes(script: ModuleType, inputs: dict[str, Path]) -> None:
    _, out = run_facts(script, inputs)

    assert found(metric(out, "good", "over", without=(D1, D2)), 0.0)
    assert all(v < 400 for v in metric(out, "good", "note_chars", without=(D1, D2)))


def test_the_script_fails_when_a_kept_folder_is_missing(script: ModuleType, inputs: dict[str, Path], tmp_path: Path) -> None:
    inputs["keep"] = tmp_path / "nowhere"

    try:
        code, _ = run_facts(script, inputs)
    except Exception:  # a clear exception is as good as a non-zero exit
        code = 1

    assert code != 0


# -- stability.py: --keep and --merge-reads -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def stability() -> ModuleType:
    return load_stability()


def test_keep_writes_the_final_wiki_of_every_run_under_the_variant_and_the_run(stability: ModuleType, tmp_path: Path) -> None:
    keep, out = tmp_path / "kept", tmp_path / "result.json"

    code = stability.main(["--dry-run", "--runs", "2", "--keep", str(keep), "--out", str(out)])

    assert code in (0, None)
    variants = sorted(json.loads(out.read_text(encoding="utf-8"))["variants"])
    assert variants and sorted(p.name for p in keep.iterdir()) == variants
    for variant in variants:
        runs = sorted(p for p in (keep / variant).iterdir() if p.name.startswith("run"))
        assert len(runs) == 2
        for run in runs:
            assert (run / "index.md").is_file()  # the whole wiki, copied before the temporary folder goes
            assert [p for p in run.rglob("*.md") if p.name != "index.md" and "raw" not in p.parts]  # with its notes


def test_without_keep_nothing_is_written_beside_the_result(stability: ModuleType, tmp_path: Path) -> None:
    out = tmp_path / "sub" / "result.json"
    out.parent.mkdir()

    stability.main(["--dry-run", "--runs", "1", "--out", str(out)])

    assert [p.name for p in out.parent.iterdir()] == ["result.json"]


def test_merge_reads_is_recorded_in_the_args_of_the_result(stability: ModuleType, tmp_path: Path) -> None:
    source, default = tmp_path / "source.json", tmp_path / "default.json"

    stability.main(["--dry-run", "--runs", "1", "--merge-reads", "source", "--out", str(source)])
    stability.main(["--dry-run", "--runs", "1", "--out", str(default)])

    def recorded(path: Path) -> list[Any]:
        found: list[Any] = []

        def walk(obj: Any) -> None:
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key in ("merge_reads", "merge-reads"):
                        found.append(value)
                    walk(value)
            elif isinstance(obj, list):
                for value in obj:
                    walk(value)

        walk(json.loads(path.read_text(encoding="utf-8")))
        return found

    assert recorded(source) and set(recorded(source)) == {"source"}
    assert set(recorded(default)) <= {"note"}  # the default is "note", recorded or implicit


def test_merge_reads_rejects_a_value_that_is_neither_note_nor_source(stability: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as stop:
        stability.main(["--dry-run", "--runs", "1", "--merge-reads", "summary", "--out", str(tmp_path / "r.json")])

    assert stop.value.code not in (0, None)
