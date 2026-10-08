# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The long2 corpus (benchmarks/make_long2_corpus.py, dry run only, no models) and the new rules of benchmarks/stability.py."""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from llmw2.agents.state import source_state
from tests.wiki.test_stability_metrics import load

BENCHMARKS = Path(__file__).resolve().parents[2] / "benchmarks"
SCRIPT = BENCHMARKS / "make_long2_corpus.py"

CREATED = ["created"]


@pytest.fixture(scope="module")
def stability() -> ModuleType:
    return load()


# -- stability.py: merge_with_doc by note, alt_folders, extra label keys ------------------------


def lab(kind: str, folders: list[str], **more: Any) -> dict[str, Any]:
    return {"kind": kind, "folders": folders, "merge_into": None, "group": None, **more}


def run_of(existing: list[str] | None = None, **docs: tuple[str, list[str], str | None]) -> dict[str, Any]:
    """A run record: each document is (folder, outcome, note it ended in)."""
    return {
        "docs": {d: {"folder": folder, "outcome": outcome, "note": note} for d, (folder, outcome, note) in docs.items()},
        "existing_folders": existing or ["/", "/customers", "/operations"],
    }


def test_merge_accuracy_when_the_update_merged_into_the_note_where_the_other_document_ended_then_it_is_correct(
    stability: ModuleType,
) -> None:
    labels = {"first": lab("existing", ["/customers"]), "u": lab("update", ["/customers"], merge_with_doc="first")}
    run = run_of(
        first=("/customers", ["merged", "customers/a.md"], "customers/a.md"),
        u=("/customers", ["merged", "customers/a.md"], "customers/a.md"),
    )

    assert stability.accuracy(run, labels)["merge_accuracy"] == 1.0


def test_merge_accuracy_when_the_update_merged_into_another_note_than_the_other_documents_then_it_is_not_correct(
    stability: ModuleType,
) -> None:
    labels = {"first": lab("existing", ["/customers"]), "u": lab("update", ["/customers"], merge_with_doc="first")}
    run = run_of(
        first=("/customers", ["merged", "customers/a.md"], "customers/a.md"),
        u=("/customers", ["merged", "customers/b.md"], "customers/b.md"),
    )

    assert stability.accuracy(run, labels)["merge_accuracy"] == 0.0


def test_merge_accuracy_when_the_update_merged_with_the_named_document_then_it_is_still_correct(stability: ModuleType) -> None:
    labels = {"first": lab("existing", ["/customers"]), "u": lab("update", ["/customers"], merge_with_doc="first")}
    run = run_of(first=("/customers", CREATED, "customers/a.md"), u=("/customers", ["merged-with", "first"], "customers/a.md"))

    assert stability.accuracy(run, labels)["merge_accuracy"] == 1.0


def test_false_merge_rate_when_a_document_merged_into_the_note_of_its_merge_with_doc_then_it_is_not_false(stability: ModuleType) -> None:
    labels = {"first": lab("new", ["/x"]), "u": lab("new", ["/x"], merge_with_doc="first")}
    run = run_of(first=("/x", CREATED, "x/a.md"), u=("/x", ["merged", "x/a.md"], "x/a.md"))

    assert stability.accuracy(run, labels)["false_merge_rate"] == 0.0


def test_false_merge_rate_when_a_document_merged_into_a_note_of_another_document_then_it_is_false(stability: ModuleType) -> None:
    labels = {"first": lab("new", ["/x"]), "u": lab("new", ["/x"], merge_with_doc="first")}
    run = run_of(first=("/x", CREATED, "x/a.md"), u=("/x", ["merged", "x/other.md"], "x/other.md"))

    # one of the two documents (u) is a false merge
    assert stability.accuracy(run, labels)["false_merge_rate"] == pytest.approx(1 / 2)


def test_new_alt_rate_is_the_share_of_new_documents_in_one_of_their_alt_folders(stability: ModuleType) -> None:
    labels = {k: lab("new", ["/lumen"], group="g", alt_folders=["/operations"]) for k in "abc"}
    run = run_of(a=("/operations", CREATED, None), b=("/coffee", CREATED, None), c=("/tea", CREATED, None))

    assert stability.accuracy(run, labels)["new_alt_rate"] == pytest.approx(1 / 3)


def test_new_alt_rate_when_the_new_documents_have_no_alt_folders_then_zero(stability: ModuleType) -> None:
    labels = {k: lab("new", ["/lumen"]) for k in "ab"}
    run = run_of(a=("/coffee", CREATED, None), b=("/tea", CREATED, None))

    assert stability.accuracy(run, labels)["new_alt_rate"] == 0.0


def test_new_alt_rate_leaves_out_the_documents_that_are_not_new(stability: ModuleType) -> None:
    labels = {"a": lab("new", ["/lumen"], alt_folders=["/operations"]), "b": lab("existing", ["/customers"], alt_folders=["/operations"])}
    run = run_of(a=("/operations", CREATED, None), b=("/operations", CREATED, None))

    assert stability.accuracy(run, labels)["new_alt_rate"] == 1.0


def test_alt_folders_when_present_then_new_accuracy_and_group_accuracy_do_not_change(stability: ModuleType) -> None:
    plain = {k: lab("new", ["/lumen"], group="g") for k in "abc"}
    alt = {k: lab("new", ["/lumen"], group="g", alt_folders=["/operations"]) for k in "abc"}
    run = run_of(a=("/operations", CREATED, None), b=("/coffee", CREATED, None), c=("/tea", CREATED, None))

    without, with_alt = stability.accuracy(run, plain), stability.accuracy(run, alt)

    assert with_alt["new_accuracy"] == without["new_accuracy"] == pytest.approx(2 / 3)  # the existing folder does not count
    assert with_alt["group_accuracy"] == without["group_accuracy"]


def test_accuracy_when_the_labels_have_secondary_sections_and_facts_then_no_metric_changes(stability: ModuleType) -> None:
    base = {
        "a": lab("existing", ["/customers"]),
        "u": lab("update", ["/operations"], merge_into=["ops/x.md"]),
        "n": lab("new", ["/lumen"], group="g", alt_folders=["/operations"]),
    }
    extra = {
        k: {
            **v,
            "secondary": ["/customers", "/operations"],
            "sections": [{"heading": "H", "folder": "/customers", "chars": 100}],
            "facts": [{"fact": "LMN-4471", "kind": "headline", "section": "H", "offset": 1, "position": "begin", "beyond_100k": False}],
        }
        for k, v in base.items()
    }
    run = run_of(a=("/operations", CREATED, None), u=("/customers", ["merged", "ops/x.md"], "ops/x.md"), n=("/coffee", CREATED, None))

    assert stability.accuracy(run, extra) == stability.accuracy(run, base)


# -- the corpus script, through its CLI in dry run -----------------------------------------------


def load_script() -> ModuleType:
    sys.path.insert(0, str(BENCHMARKS))  # it may import corpus_common as a sibling
    try:
        spec = importlib.util.spec_from_file_location("benchmarks_make_long2_corpus", SCRIPT)
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


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return load_script()


class Corpus:
    """What a dry run wrote: the labels (documents sorted by filename: 01..15) and the text of each document."""

    def __init__(self, root: Path) -> None:
        self.root = root
        data = json.loads((root / "labels.json").read_text(encoding="utf-8"))
        labels = data["docs"] if isinstance(data, dict) and "docs" in data else data
        self.names = sorted(labels)
        self.labels: dict[str, dict[str, Any]] = {n: labels[n] for n in self.names}
        self.texts = {n: (root / n).read_text(encoding="utf-8") for n in self.names}

    def label(self, number: int) -> dict[str, Any]:
        return self.labels[self.names[number - 1]]

    def text(self, number: int) -> str:
        return self.texts[self.names[number - 1]]


@pytest.fixture(scope="module")
def built(script: ModuleType, tmp_path_factory: pytest.TempPathFactory) -> Corpus:
    out = tmp_path_factory.mktemp("long2") / "c"
    assert run_main(script, ["--dry-run", "--out", str(out)]) == 0
    return Corpus(out)


NUMBERS = list(range(1, 16))


def norm(path: str) -> str:
    return path.lstrip("/")


# (kind, folders, merge_into, merge_ok, merge_with_doc number, group, alt_folders) as in the table of the spec
TABLE: dict[int, dict[str, Any]] = {
    1: {
        "kind": "existing",
        "folders": ["/customers"],
        "merge_ok": ["customers/cafe-portello.md", "customers/wholesale-cafe-onboarding.md", "products/price-list-from-july-2026.md"],
    },
    2: {
        "kind": "ambiguous",
        "folders": ["/operations/roasting", "/ideas", "/operations"],
        "merge_ok": ["operations/roasting/roaster-maintenance.md", "meetings/board-meeting-june-2026.md"],
    },
    3: {
        "kind": "existing",
        "folders": ["/sourcing"],
        "merge_ok": [
            "sourcing/green-coffee-price-hedging.md",
            "products/price-list-from-july-2026.md",
            "sourcing/andes-direct-importer.md",
        ],
    },
    4: {
        "kind": "ambiguous",
        "folders": ["/customers", "/products"],
        "merge_ok": ["customers/cafe-portello.md", "products/night-shift-decaf.md"],
    },
    5: {
        "kind": "ambiguous",
        "folders": ["/ideas", "/operations/shipping"],
        "merge_ok": ["operations/shipping/subscription-packing-day.md", "ideas/pause-instead-of-cancel.md"],
    },
    6: {"kind": "new", "group": "sust", "alt_folders": ["/operations"]},
    7: {"kind": "new", "group": "sust", "alt_folders": []},
    8: {"kind": "new", "group": "sust", "alt_folders": ["/sourcing"]},
    9: {"kind": "update", "folders": ["/products"], "merge_into": ["products/aurora-espresso-blend.md"]},
    10: {
        "kind": "update",
        "folders": ["/operations/shipping"],
        "merge_into": ["carriers-and-delivery-times.md", "subscription-packing-day.md"],
    },  # basenames
    11: {"kind": "existing", "folders": ["/sourcing"], "merge_ok": ["sourcing/green-coffee-price-hedging.md"]},  # corrected 2026-10-06
    12: {"kind": "existing", "folders": ["/operations/roasting"], "merge_ok": ["operations/roasting/quality-control-and-cupping.md"]},
    13: {"kind": "update", "folders": ["/customers"], "merge_with_doc": 1},
    14: {"kind": "update", "folders": ["/customers"], "merge_with_doc": 13},
    15: {"kind": "existing", "folders": ["/customers"], "merge_ok": ["customers/refund-policy.md", "customers/cafe-portello.md"]},
}

# the folders of the sections of each document (the new sustainability ones: a single folder, whose name the spec leaves open)
SECTION_FOLDERS: dict[int, set[str] | int] = {
    1: {"/customers", "/products", "/operations/shipping"},
    2: {"/operations/roasting", "/ideas", "/sourcing"},
    3: {"/sourcing", "/products", "/customers"},
    4: {"/customers", "/products"},
    5: {"/ideas", "/operations/shipping", "/customers"},
    6: 1,
    7: 1,
    8: 1,
    9: {"/products"},
    10: {"/operations/shipping"},
    11: {"/sourcing"},
    12: {"/operations/roasting"},
    13: {"/customers", "/products", "/operations/shipping"},
    14: {"/customers", "/products", "/operations/shipping"},
    15: {"/customers"},
}

SUMMARY_TERMS = {
    1: "executive summary",
    2: "introduction",
    5: "summary",
    6: "executive summary",
    7: "purpose",
    8: "premessa",
    10: "purpose",
    12: "executive summary",
}
ALL_SUMMARY = (
    "abstract",
    "executive summary",
    "summary",
    "sommario",
    "sintesi",
    "riassunto",
    "riepilogo",
    "introduction",
    "introduzione",
    "premessa",
    "overview",
    "scope",
    "oggetto",
    "purpose",
    "scopo",
)
ITALIAN = {3, 8}


def is_summary_heading(heading: str) -> bool:
    dash = re.escape(chr(0x2013) + chr(0x2014) + "-")
    return bool(re.sub(rf"^[#\s\d.{dash}]*(art\w*\.?\s*\d+\s*[{dash}:.]?\s*)?", "", heading.lower()).startswith(ALL_SUMMARY))


def real_sections(label: dict[str, Any]) -> list[dict[str, Any]]:
    """The sections of a label that count for the shares: the native summary does not."""
    return [s for s in label["sections"] if not is_summary_heading(s["heading"])]


def test_corpus_when_dry_run_then_fifteen_documents_with_labels_json(built: Corpus) -> None:
    assert len(built.names) == 15
    assert all(built.texts[n].strip() for n in built.names)


@pytest.mark.parametrize("number", NUMBERS)
def test_labels_when_dry_run_then_each_document_has_the_labels_of_the_table(built: Corpus, number: int) -> None:
    label, want = built.label(number), TABLE[number]

    assert label["kind"] == want["kind"]
    if "folders" in want:
        assert {norm(f) for f in label["folders"]} == {norm(f) for f in want["folders"]}
    if "merge_ok" in want:
        assert {norm(m) for m in label["merge_ok"]} == {norm(m) for m in want["merge_ok"]}
    if "merge_into" in want:
        got = label["merge_into"]
        got = [got] if isinstance(got, str) else list(got)
        if number == 10:
            assert {Path(m).name for m in got} == set(want["merge_into"])
        else:
            assert {norm(m) for m in got} == {norm(m) for m in want["merge_into"]}
    else:
        assert not label.get("merge_into")
    if "merge_with_doc" in want:
        assert label["merge_with_doc"] == built.names[want["merge_with_doc"] - 1]
    else:
        assert not label.get("merge_with_doc")
    if "group" in want:
        assert label["group"] == want["group"]
    else:
        assert not label.get("group")
    if "alt_folders" in want:
        assert {norm(f) for f in label.get("alt_folders") or []} == {norm(f) for f in want["alt_folders"]}
    else:
        assert not label.get("alt_folders")


def test_labels_when_the_quality_control_manual_then_refund_policy_is_not_an_allowed_merge(built: Corpus) -> None:
    assert "customers/refund-policy.md" not in {norm(m) for m in built.label(12)["merge_ok"]}


@pytest.mark.parametrize("number", NUMBERS)
def test_sections_when_dry_run_then_they_have_the_folders_of_the_table(built: Corpus, number: int) -> None:
    sections = real_sections(built.label(number))
    folders = {s["folder"] for s in sections}
    want = SECTION_FOLDERS[number]

    assert all(s["chars"] > 0 for s in sections)
    if isinstance(want, int):
        assert len(folders) == want
    else:
        assert {norm(f) for f in folders} == {norm(f) for f in want}


@pytest.mark.parametrize("number", NUMBERS)
def test_secondary_when_dry_run_then_it_is_every_folder_with_a_share_of_at_least_twenty_percent(built: Corpus, number: int) -> None:
    sections = real_sections(built.label(number))
    total = sum(s["chars"] for s in sections)
    chars: dict[str, int] = {}
    for s in sections:
        chars[norm(s["folder"])] = chars.get(norm(s["folder"]), 0) + s["chars"]
    shares = {f: c / total for f, c in chars.items()}

    assert sum(shares.values()) == pytest.approx(1.0)
    assert {norm(f) for f in built.label(number)["secondary"]} == {f for f, share in shares.items() if share >= 0.20}


# -- the planted facts --------------------------------------------------------------------------


def summary_span(corpus: Corpus, number: int) -> tuple[int, int] | None:
    """Offsets of the native summary: from its heading line to the line of the next section heading; None if it has none."""
    term = SUMMARY_TERMS.get(number if number not in (13, 14) else 1)
    if term is None:
        return None
    text = corpus.text(number)
    # the labels list no native summary, and a heading such as "Scope and method" is a section, not a summary
    others = [s["heading"].lower() for s in corpus.label(number)["sections"]]
    pos, start = 0, None
    for line in text.splitlines(keepends=True):
        stripped = line.strip().lower()
        if start is None:
            if 0 < len(stripped) <= 120 and term in stripped:
                start = pos
        elif 0 < len(stripped) <= 160 and any(h and h in stripped for h in others):
            return start, pos
        pos += len(line)
    assert start is not None, f"no native summary heading ({term}) in document {number}"
    raise AssertionError(f"no section heading after the summary of document {number}")


@pytest.mark.parametrize("number", NUMBERS)
def test_facts_when_dry_run_then_each_one_appears_exactly_once_in_its_document(built: Corpus, number: int) -> None:
    facts = built.label(number)["facts"]
    text = built.text(number)

    assert facts
    assert [f["fact"] for f in facts if text.count(f["fact"]) != 1] == []
    assert {f["kind"] for f in facts} <= {"headline", "marginal"}
    assert any(f["kind"] == "headline" for f in facts)


@pytest.mark.parametrize("number", NUMBERS)
def test_facts_when_dry_run_then_position_and_beyond_100k_match_the_real_offset(built: Corpus, number: int) -> None:
    text = built.text(number)

    for f in built.label(number)["facts"]:
        at = text.find(f["fact"])
        want = "begin" if at < len(text) / 3 else "middle" if at < 2 * len(text) / 3 else "tail"
        assert f["position"] == want, f
        assert f["beyond_100k"] == (at >= 100_000), f


@pytest.mark.parametrize("number", [*sorted(SUMMARY_TERMS), 13, 14])
def test_facts_when_the_document_has_a_native_summary_then_none_is_in_its_lines(built: Corpus, number: int) -> None:
    start, end = summary_span(built, number)
    text = built.text(number)

    assert [f["fact"] for f in built.label(number)["facts"] if start <= text.find(f["fact"]) < end] == []


@pytest.mark.parametrize("number", [n for n in NUMBERS if n not in (13, 14)])
def test_facts_when_dry_run_then_headlines_and_marginals_are_balanced(built: Corpus, number: int) -> None:
    kinds = [f["kind"] for f in built.label(number)["facts"]]

    # Contract decision: a section written in several parts plants one marginal fact per part, so a long section is
    # measured along its length; a headline stays one per section.
    assert kinds.count("marginal") >= kinds.count("headline") > 0


MONTHS_EN = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b")
MONTHS_IT = re.compile(r"\b(gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\b")


@pytest.mark.parametrize("number", NUMBERS)
def test_facts_when_dry_run_then_their_formats_are_in_the_language_of_the_document(built: Corpus, number: int) -> None:
    facts = [f["fact"] for f in built.label(number)["facts"]]

    if number in ITALIAN:
        assert [f for f in facts if re.search(r"€\s?\d", f) or MONTHS_EN.search(f)] == []
    else:
        assert [f for f in facts if re.search(r"\d\s?€", f) or MONTHS_IT.search(f)] == []


@pytest.mark.parametrize("number", [13, 14])
def test_facts_when_a_revision_then_four_headlines_supersede_old_values_that_are_gone(built: Corpus, number: int) -> None:
    text = built.text(number)
    changed = [f for f in built.label(number)["facts"] if f.get("supersedes")]

    assert len(changed) == 4
    assert all(f["kind"] == "headline" for f in changed)
    assert [f for f in changed if f["supersedes"] in text] == []
    assert [f for f in changed if text.count(f["fact"]) != 1] == []


@pytest.mark.parametrize("number", [13, 14])
def test_facts_when_a_revision_then_the_old_values_were_in_the_previous_document(built: Corpus, number: int) -> None:
    previous = built.text(1 if number == 13 else 13)  # assumption: what rev. 3 supersedes is the value of rev. 2

    assert [f for f in built.label(number)["facts"] if f.get("supersedes") and f["supersedes"] not in previous] == []


def test_facts_when_the_first_review_then_no_fact_supersedes_anything(built: Corpus) -> None:
    assert not [f for f in built.label(1)["facts"] if f.get("supersedes")]


def test_revision_when_dry_run_then_the_title_and_the_first_line_say_so(built: Corpus) -> None:
    assert "rev. 2" in built.text(13)[:800].lower()
    assert "rev. 3" in built.text(14)[:800].lower()
    assert len(built.text(14)) > len(built.text(13))


# -- boundaries ---------------------------------------------------------------------------------


def test_the_archive_when_dry_run_then_the_2027_headlines_of_document_11_are_beyond_100k(built: Corpus) -> None:
    text = built.text(11)
    late = [f for f in built.label(11)["facts"] if f["kind"] == "headline" and text.find(f["fact"]) >= 100_000]

    assert len(text) > 105_000
    assert len(late) >= 4
    assert all(f["beyond_100k"] for f in late)


def test_the_mailbox_when_dry_run_then_it_is_longer_than_120k_and_state_sees_no_headings(built: Corpus) -> None:
    text = built.text(15)

    assert len(text) > 120_000
    state = source_state("Café complaints mailbox, 2025-2026", text, 6000)
    assert "Sections:" not in state and "Excerpts:" in state


def test_source_state_when_the_text_has_markdown_headings_then_it_has_an_outline(built: Corpus) -> None:
    # the contrast for the mailbox above: the same long text with real headings gives a list of sections
    body = "\n\n".join(f"## Part {i}\n\n" + "A paragraph of plain words about the café complaints. " * 80 for i in range(1, 8))

    assert "Sections:" in source_state("Title", body, 3000)


# -- determinism and --check --------------------------------------------------------------------


def test_dry_run_when_repeated_then_the_facts_are_the_same(script: ModuleType, built: Corpus, tmp_path: Path) -> None:
    assert run_main(script, ["--dry-run", "--out", str(tmp_path / "c")]) == 0
    again = Corpus(tmp_path / "c")

    assert [again.label(n)["facts"] for n in NUMBERS] == [built.label(n)["facts"] for n in NUMBERS]


@pytest.fixture
def copy(built: Corpus, tmp_path: Path) -> Corpus:
    shutil.copytree(built.root, tmp_path / "copy")
    return Corpus(tmp_path / "copy")


def check(script: ModuleType, corpus: Corpus) -> int:
    return run_main(script, ["--check", "--out", str(corpus.root)])


def test_check_when_the_dry_run_corpus_is_intact_then_it_exits_zero(script: ModuleType, copy: Corpus) -> None:
    assert check(script, copy) == 0


def test_check_when_a_fact_is_deleted_from_a_document_then_it_exits_one(script: ModuleType, copy: Corpus) -> None:
    name = copy.names[0]
    fact = copy.label(1)["facts"][0]["fact"]
    (copy.root / name).write_text(copy.text(1).replace(fact, ""), encoding="utf-8")

    assert check(script, copy) == 1


def test_check_when_a_fact_is_duplicated_in_a_document_then_it_exits_one(script: ModuleType, copy: Corpus) -> None:
    name = copy.names[0]
    fact = copy.label(1)["facts"][0]["fact"]
    (copy.root / name).write_text(copy.text(1) + f"\n\nAgain: {fact}\n", encoding="utf-8")

    assert check(script, copy) == 1
