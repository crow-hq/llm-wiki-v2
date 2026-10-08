# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The deep demo base and corpus (benchmarks/corpus/deep) and the step-3 rules of benchmarks/stability.py. Free, no model.

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

from llmw2 import Wiki
from llmw2.agents.base import Decision
from llmw2.settings import Settings
from tests.wiki.test_stability_metrics import load

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "src" / "llmw2" / "demo"
GROWTH = ROOT / "benchmarks" / "corpus" / "growth"
DEEP = ROOT / "benchmarks" / "corpus" / "deep"
BASE = DEEP / "base"

# The map of the spec (section 2, with the level-1 descriptions of R4): folder -> description, and note -> folder.
DESCRIPTIONS = {
    "/customers": "Wholesale cafés and home subscribers: who they are, what they buy, how we serve them.",
    "/customers/wholesale": "Cafés that buy from us wholesale: accounts, onboarding, training.",
    "/customers/subscriptions": "Home subscribers: churn, retention, what they ask for.",
    "/customers/policies": "Customer policies: refunds, returns, terms.",
    "/products": "Coffees we sell and their prices.",
    "/products/coffees": "Our coffees: blends, single origins, decaf.",
    "/products/pricing": "Price lists and price changes.",
    "/sourcing": "Green coffee: importers, origins and price risk.",
    "/sourcing/importers": "The importers we buy from and our terms with them.",
    "/sourcing/origins": "Farms, washing stations and origin trips.",
    "/sourcing/risk": "Price risk and hedging of green coffee.",
    "/operations": "Roasting and shipping at the roastery.",
    "/operations/roasting": "Roasting coffee.",
    "/operations/roasting/profiles": "Roast profiles of our coffees.",
    "/operations/roasting/quality": "Quality control, cupping and defects.",
    "/operations/roasting/equipment": "The roaster and its maintenance.",
    "/operations/shipping": "Shipping orders and subscriptions.",
    "/operations/shipping/carriers": "Carriers, rates and delivery times.",
    "/operations/shipping/packing": "Packing days and how boxes are packed.",
    "/meetings": "Notes of board and team meetings.",
    "/meetings/board": "Board meetings.",
    "/meetings/team": "Team and working meetings.",
    "/ideas": "Ideas for products, customers and events not decided yet.",
    "/ideas/products": "Ideas for new products.",
    "/ideas/customers": "Ideas for serving customers better.",
    "/ideas/experiences": "Ideas for events and experiences.",
}
NOTES = {
    "/customers/wholesale": ["cafe-portello", "wholesale-cafe-onboarding"],
    "/customers/subscriptions": ["subscription-churn-q2-2026"],
    "/customers/policies": ["refund-policy"],
    "/products/coffees": ["aurora-espresso-blend", "ethiopia-guji-seasonal-single-origin", "night-shift-decaf"],
    "/products/pricing": ["price-list-from-july-2026"],
    "/sourcing/importers": ["andes-direct-importer"],
    "/sourcing/origins": ["guji-washing-station-visit-february-2026"],
    "/sourcing/risk": ["green-coffee-price-hedging"],
    "/operations/roasting/profiles": ["aurora-roast-profile"],
    "/operations/roasting/quality": ["quality-control-and-cupping"],
    "/operations/roasting/equipment": ["roaster-maintenance"],
    "/operations/shipping/carriers": ["carriers-and-delivery-times"],
    "/operations/shipping/packing": ["subscription-packing-day"],
    "/meetings/board": ["board-meeting-june-2026"],
    "/meetings/team": ["pricing-meeting-may-2026", "weekly-sync-10-june-2026"],
    "/ideas/products": ["cold-brew-cans"],
    "/ideas/customers": ["pause-instead-of-cancel"],
    "/ideas/experiences": ["roastery-tours"],
}
NOTE_OF = {slug: f"{folder.strip('/')}/{slug}.md" for folder, slugs in NOTES.items() for slug in slugs}

LINK = re.compile(r"\]\(([^)\s]+)\)")


def descendants(folder: str) -> set[str]:
    """The folder and every folder of the deep map below it."""
    return {f for f in DESCRIPTIONS if f == folder or f.startswith(folder + "/")}


@pytest.fixture(scope="module")
def stability() -> ModuleType:
    return load()


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> Any:
    assert BASE.is_dir(), f"{BASE} does not exist: run benchmarks/make_deep_demo.py"
    copy = tmp_path_factory.mktemp("deepbase") / "wiki"
    shutil.copytree(BASE, copy)
    return Wiki(Settings(bundle=copy).config()).tree()


@pytest.fixture(scope="module")
def deep_labels() -> dict[str, dict[str, Any]]:
    path = DEEP / "labels.json"
    assert path.is_file(), f"{path} does not exist: run benchmarks/make_deep_demo.py and make_deep_corpus.py"
    return json.loads(path.read_text(encoding="utf-8"))["docs"]


@pytest.fixture(scope="module")
def growth_labels() -> dict[str, dict[str, Any]]:
    return json.loads((GROWTH / "labels.json").read_text(encoding="utf-8"))["docs"]


# -- the deep base (spec 2, R3, R4) ---------------------------------------------------------------


def test_base_when_checked_then_the_library_finds_no_problem(tmp_path: Path) -> None:
    # "`Wiki.check()` passes"
    assert BASE.is_dir()
    copy = tmp_path / "wiki"
    shutil.copytree(BASE, copy)

    assert Wiki(Settings(bundle=copy).config()).check() == []


def test_base_has_every_folder_of_the_map_with_its_description_verbatim(tree: Any) -> None:
    # "every folder of the map present with its description"; "Descriptions are part of the spec; keep them verbatim"
    for label, description in DESCRIPTIONS.items():
        folder = tree.find(label)
        assert folder is not None, f"missing folder {label}"
        assert folder.description == description, label


def test_base_has_no_folder_outside_the_map(tree: Any) -> None:
    # "a tree of 3 levels per the map below"
    assert {f.label for f in tree.walk() if not f.is_root} == set(DESCRIPTIONS)


def test_base_level_one_descriptions_are_the_specific_ones_of_r4(tree: Any) -> None:
    # R4: "Level-1 descriptions specific, not generic"
    for label in ("/products", "/sourcing", "/operations", "/meetings", "/ideas"):
        assert tree.find(label).description == DESCRIPTIONS[label]


def test_base_has_the_22_notes_each_in_its_mapped_folder(tree: Any) -> None:
    # "22 notes in the mapped folders"
    assert len(NOTE_OF) == 22
    for folder, slugs in NOTES.items():
        found = tree.find(folder)
        assert found is not None
        assert sorted(n.rel for n in found.notes) == sorted(f"{folder.strip('/')}/{s}.md" for s in slugs), folder


def test_base_keeps_about_this_demo_at_the_root(tree: Any) -> None:
    # "`about-this-demo.md` stays at the root"
    assert (BASE / "about-this-demo.md").is_file()
    assert "about-this-demo.md" in {n.rel for n in tree.notes}
    assert not list(BASE.glob("*/**/about-this-demo.md"))


def test_base_notes_keep_their_title_and_description_from_the_demo() -> None:
    # "Note frontmatter unchanged apart from what a move requires"
    def field(text: str, key: str) -> str:
        match = re.search(rf"^{key}: (.*?)(?=^\S+:|^---)", text.split("---", 2)[1] + "---", re.S | re.M)
        assert match, key
        return " ".join(match.group(1).split())

    old = {p.stem: p.read_text(encoding="utf-8") for p in DEMO.rglob("*.md") if p.stem in NOTE_OF}
    assert len(old) == 22
    for slug, rel in NOTE_OF.items():
        text = (BASE / rel).read_text(encoding="utf-8")
        for key in ("title", "description"):
            assert field(text, key) == field(old[slug], key), f"{slug} {key}"


def test_base_raw_is_copied_as_is() -> None:
    # "`raw/` copied as is"
    old = {p.name: p.read_bytes() for p in (DEMO / "raw").iterdir()}
    new = {p.name: p.read_bytes() for p in (BASE / "raw").iterdir()}

    assert new == old


def test_base_has_no_broken_relative_link_in_any_markdown_file() -> None:
    # R3: "Moving a note must rewrite every relative Markdown link in every note body (and See-also) that points to a moved note"
    broken = []
    for file in BASE.rglob("*.md"):
        for target in LINK.findall(file.read_text(encoding="utf-8")):
            if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                continue
            if not (file.parent / target.split("#")[0]).resolve().exists():
                broken.append(f"{file.relative_to(BASE).as_posix()} -> {target}")

    assert broken == []


def test_base_log_has_a_line_saying_the_tree_was_reorganised() -> None:
    # R3: "`log.md`: copy the demo's, then add one line ... saying the tree was reorganised for the benchmark"
    # (the library's log is newest first, so "at the end" is read as "the newest entry"; the line only has to be there)
    text = (BASE / "log.md").read_text(encoding="utf-8").lower()

    assert "reorgani" in text


# -- the remapped labels of the growth documents (spec 3, R5) ---------------------------------------


def test_deep_labels_hold_the_same_growth_documents_with_the_same_files(deep_labels: dict[str, Any], growth_labels: dict[str, Any]) -> None:
    # "copied (same files, same names) from `benchmarks/corpus/growth/`"
    for name in growth_labels:
        assert name in deep_labels, name
        assert (DEEP / name).read_bytes() == (GROWTH / name).read_bytes(), name


def test_growth_labels_folders_are_the_old_folders_plus_their_descendants_in_the_deep_base(
    deep_labels: dict[str, Any], growth_labels: dict[str, Any]
) -> None:
    # "`folders` = each old label folder plus all its descendants in the deep base"
    for name, old in growth_labels.items():
        expected = set().union(*(descendants(f) for f in old["folders"])) if old["folders"] else set()
        assert set(deep_labels[name]["folders"]) == expected, name


def test_growth_labels_note_paths_are_rewritten_to_existing_notes_of_the_base(
    deep_labels: dict[str, Any], growth_labels: dict[str, Any]
) -> None:
    # "`merge_into` / `merge_ok` note paths rewritten to the notes' new paths"
    def as_list(value: Any) -> list[str]:
        return [] if not value else [value] if isinstance(value, str) else list(value)

    checked = 0
    for name, old in growth_labels.items():
        for key in ("merge_into", "merge_ok"):
            new_notes = as_list(deep_labels[name].get(key))
            assert new_notes == [NOTE_OF[Path(n).stem] for n in as_list(old.get(key))], (name, key)
            for note in new_notes:
                assert (BASE / note).is_file(), (name, note)
            checked += len(new_notes)
    assert checked >= 7


def test_growth_labels_keep_kind_group_and_merge_with_doc(deep_labels: dict[str, Any], growth_labels: dict[str, Any]) -> None:
    # "`kind`, `group`, `merge_with_doc` unchanged"
    for name, old in growth_labels.items():
        for key in ("kind", "group", "merge_with_doc"):
            assert deep_labels[name].get(key) == old.get(key), (name, key)


def test_growth_alt_folders_get_the_additions_of_r5(deep_labels: dict[str, Any]) -> None:
    # R5: "g2 (08, 19, 30) get `alt_folders` += `/operations/shipping/packing` for 19 only; g3 (11, 23) += `/ideas/experiences`"
    def alt(prefix: str) -> set[str]:
        (name,) = [d for d in deep_labels if d.startswith(prefix)]
        return set(deep_labels[name].get("alt_folders") or [])

    assert "/operations/shipping/packing" in alt("19-")
    assert "/operations/shipping/packing" not in alt("08-") | alt("30-")
    assert "/ideas/experiences" in alt("11-") and "/ideas/experiences" in alt("23-")


def test_growth_documents_other_than_the_r5_ones_have_no_alt_folders(deep_labels: dict[str, Any], growth_labels: dict[str, Any]) -> None:
    # R5 adds alt_folders to 19, 11 and 23 only; the growth labels had none, and "`alt_folders` likewise" is remapped
    for name in growth_labels:
        if name[:2] not in ("19", "11", "23"):
            assert not deep_labels[name].get("alt_folders"), name


# -- the 12 new documents (R6) -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def new_labels(deep_labels: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {d: v for d, v in deep_labels.items() if re.match(r"^(3[1-9]|4[0-9]|5[0-2])-", d)}  # 31-42 in the spec, 43-52 added
    assert len(out) == 22, f"expected 22 documents named 31-... to 52-..., found {sorted(out)}"  # 12 in the spec, +10 added with the user
    return out


def test_new_documents_are_named_31_to_42_and_sized_1500_to_6000_characters(new_labels: dict[str, Any]) -> None:
    # "1,500-4,000 characters each, English, named `31-…` to `42-…`"
    assert sorted(int(d[:2]) for d in new_labels) == list(range(31, 53))  # 31-42 in the spec, 43-52 added
    for name in new_labels:
        assert 1500 <= len((DEEP / name).read_text(encoding="utf-8")) <= 6000, name  # 4000 in the spec, relaxed


def test_new_documents_do_not_name_a_folder_path_of_their_labels(new_labels: dict[str, Any]) -> None:
    # "Each document's text must not name the folder"
    for name, label in new_labels.items():
        text = (DEEP / name).read_text(encoding="utf-8")
        for folder in [*label["folders"], *label.get("alt_folders", [])]:
            assert folder not in text, (name, folder)


def test_four_new_documents_have_a_misleading_surface_and_one_right_leaf(new_labels: dict[str, Any]) -> None:
    # R6: "4 misleading surface (kind `existing`, one right leaf)": hedging, roaster fault, price list applied, carrier rates
    existing = [v for v in new_labels.values() if v["kind"] == "existing"]

    assert len(existing) == 4
    assert sorted(v["folders"][0] for v in existing if len(v["folders"]) == 1) == [
        "/operations/roasting/equipment", "/operations/shipping/carriers", "/products/pricing", "/sourcing/risk",
    ]  # fmt: skip


def test_four_new_documents_are_ambiguous_across_branches_with_the_leaves_and_their_parents(new_labels: dict[str, Any]) -> None:
    # R6: "`folders` = the two leaves and their parents"
    expected = [
        {"/sourcing/risk", "/sourcing", "/products/pricing", "/products"},
        {"/customers/wholesale", "/customers", "/operations/shipping/carriers", "/operations/shipping"},
        {"/customers/subscriptions", "/customers", "/ideas/products", "/ideas"},
        {"/meetings/board", "/meetings", "/operations/roasting/equipment", "/operations/roasting"},
    ]
    found = [set(v["folders"]) for v in new_labels.values() if v["kind"] == "ambiguous"]

    assert sorted(map(sorted, found)) == sorted(map(sorted, expected))


def test_four_new_documents_are_new_in_groups_g4_and_g5_of_two(new_labels: dict[str, Any]) -> None:
    # R6: "4 new in 2 groups of 2: g4 ...; g5 ..."
    new = [v for k, v in new_labels.items() if v["kind"] == "new" and int(k[:2]) <= 42]  # 43-52 were added later, all new

    assert len(new) == 4
    assert sorted(v["group"] for v in new) == ["g4", "g4", "g5", "g5"]
    assert all(v["folders"] == [] for v in new)


def test_new_documents_alt_folders_per_group(new_labels: dict[str, Any]) -> None:
    # R6: "g4 (`alt_folders` `/operations/roasting/equipment`); g5 (`alt_folders` `/ideas/experiences`, `/customers`)"
    wanted = {"g4": {"/operations/roasting/equipment"}, "g5": {"/ideas/experiences", "/customers"}}
    for v in (v for k, v in new_labels.items() if v["kind"] == "new" and int(k[:2]) <= 42):
        assert wanted[v["group"]] <= set(v.get("alt_folders") or [])


def test_every_folder_of_the_labels_exists_in_the_deep_map(deep_labels: dict[str, Any]) -> None:
    # the labels point at folders of the deep base
    for name, label in deep_labels.items():
        for folder in [*label["folders"], *label.get("alt_folders", [])]:
            assert folder in DESCRIPTIONS, (name, folder)


def test_labels_cover_exactly_the_documents_of_the_corpus(deep_labels: dict[str, Any]) -> None:
    # "`labels.json` of `corpus/deep` holds both parts": 30 growth documents and 12 new ones
    files = {p.name for p in DEEP.glob("*.md")}

    assert set(deep_labels) == files
    assert len(files) == 52  # 42 in the spec, +10 added with the user


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
