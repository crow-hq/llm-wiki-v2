# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Below the top level the routing Choice describes each subfolder by its distinctive words and example titles."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from llmw2 import StepContext, WikiConfig
from llmw2.agents import grouping, route_hints
from llmw2.agents import steps_crow as crow
from llmw2.agents.prompts import Prompts
from llmw2.bundle.tree import Folder, Note
from llmw2.config import ClassifierConfig
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeClassifier, FakeLLM

COMMON = "the report states overall results"  # in every note: a word in more than 30% of the notes is no hint


@pytest.fixture(autouse=True)
def fresh_caches() -> None:
    for cache in (route_hints._words, route_hints._common, route_hints._hints):
        cache.clear()


def tree(spec: dict[str, dict[str, list[tuple[str, str]]]]) -> Folder:
    """root > area > subfolder > notes (title, body); the areas and subfolders have no description."""
    base = Path("wiki")
    root = Folder(base, "")
    for area, subs in spec.items():
        a = Folder(base / area, area, parent=root)
        root.subfolders.append(a)
        for sub, notes in subs.items():
            s = Folder(base / area / sub, f"{area}/{sub}", parent=a)
            a.subfolders.append(s)
            for i, (title, body) in enumerate(notes):
                s.notes.append(Note(base / area / sub / f"n{i}.md", f"{area}/{sub}/n{i}.md", {"title": title}, f"{COMMON} {body}", s))
    return root


def options_at(root: Folder, folder: Folder, hints: bool = True) -> dict[str, str]:
    """The option texts the classifier is shown when `folder` is expanded."""
    classifier = FakeClassifier(UsageTracker(), choices={"route": [("New subfolder", {"New subfolder": 1.0}, 1.0)]})
    clf = ClassifierConfig(model="fake/jev", api_key="fake-key", route_hints=hints)
    cfg = WikiConfig(bundle=Path("wiki"), mode="crow", classifier=clf)
    ctx = StepContext(FakeLLM(UsageTracker()), classifier, Prompts(), cfg, "librarian/system")
    crow._expand(ctx, crow._Path(tuple(folder.ancestors())), "state", [], [], {}, False)
    return classifier.calls[0][2]["q"]["criteria"]


SPEC = {
    "science": {
        "astronomy": [("Stellar parallax", "galaxy telescope"), ("Dark galaxies", "galaxy nebula")],
        "physics": [("Quantum states", "galaxy quantum"), ("Wave motion", "galaxy waves")],
        "baking": [("Bread basics", "flour oven yeast"), ("Sourdough", "flour oven starter")],
        "cooking": [("Pasta dough", "flour oven semolina"), ("Sauces", "flour oven butter")],
    },
    "misc": {"filler": [(f"Other {i}", f"ubiquity filler{'abcdefghij'[i]}" if i < 6 else f"filler{'abcdefghij'[i]}") for i in range(10)]},
}


def test_non_root_options_carry_distinctive_words_and_titles() -> None:
    root = tree(SPEC)

    options = options_at(root, root.subfolder("science"))

    assert "galaxy" in options["astronomy"] and "Stellar parallax" in options["astronomy"]
    assert "flour" in options["cooking"] and "flour" not in options["astronomy"]
    assert "Typical words:" in options["cooking"] and "Examples:" in options["cooking"]


def test_hints_are_off_by_default_and_options_are_the_descriptions() -> None:
    root = tree(SPEC)
    root.subfolder("science").description = "Natural sciences"
    root.subfolder("science").subfolder("cooking").description = "Kitchen"

    assert ClassifierConfig(api_key="k").route_hints is False
    assert options_at(root, root, hints=False)["science"] == "Natural sciences"
    off = options_at(root, root.subfolder("science"), hints=False)
    assert off["cooking"] == "Kitchen" and off["astronomy"] == "astronomy"


def test_root_options_are_the_descriptions() -> None:
    root = tree(SPEC)
    root.subfolder("science").description = "Natural sciences"

    options = options_at(root, root)

    assert options["science"] == "Natural sciences"
    assert options["misc"] == "misc"


def test_common_words_and_markup_never_appear() -> None:
    spec = {
        "a": {
            "x": [("Alpha one", "begin nbsp mathbf shared ubiquity"), ("Alpha two", "shared ubiquity begin nbsp mathbf")],
            "y": [("Beta one", "begin nbsp mathbf shared ubiquity"), ("Beta two", "shared ubiquity begin nbsp mathbf")],
        },
        "b": {"z": [(f"Other {i}", f"ubiquity filler{'abcdefghij'[i]}" if i < 6 else f"filler{'abcdefghij'[i]}") for i in range(10)]},
    }
    root = tree(spec)

    text = options_at(root, root.subfolder("a"))["x"]

    words = set(re.findall(r"[a-z]+", text))
    assert "shared" in words  # ubiquity is in 10 of 14 notes, the markup tokens are in the stoplist
    assert not {"the", "report", "states", "overall", "results", "begin", "nbsp", "mathbf", "ubiquity"} & words


def test_fewer_than_two_populated_siblings_gets_no_hints() -> None:
    root = tree({"a": {"x": [("Alpha one", "quasar quasar"), ("Alpha two", "quasar")], "y": []}})

    options = options_at(root, root.subfolder("a"))

    assert options["x"] == "x" and options["y"] == "y"


def test_hints_follow_the_notes_when_they_change() -> None:
    root = tree(SPEC)
    area = root.subfolder("science")
    assert "zircon" not in options_at(root, area)["astronomy"]

    for sub in ("astronomy", "physics"):  # a word needs two siblings and two notes of its subfolder
        for note in area.subfolder(sub).notes:
            note.body += " zircon"

    assert "zircon" in options_at(root, area)["astronomy"]


def test_option_text_format_and_caps() -> None:
    root = tree(SPEC)

    text = options_at(root, root.subfolder("science"))["astronomy"]

    assert text.startswith("astronomy Typical words: galaxy. Examples: Stellar parallax; Dark galaxies.")
    assert len(text) <= 600


def test_deeper_subfolders_are_described_as_before() -> None:
    root = tree(SPEC)
    astronomy = root.subfolder("science").subfolder("astronomy")
    for name in ("optics", "radio"):  # a third level, each with notes
        sub = Folder(astronomy.path / name, f"{astronomy.rel}/{name}", parent=astronomy)
        sub.notes.append(Note(sub.path / "n.md", f"{sub.rel}/n.md", {"title": f"Note on {name}"}, "galaxy flour oven", sub))
        astronomy.subfolders.append(sub)

    options = options_at(root, astronomy)

    assert options["optics"] == "optics" and options["radio"] == "radio"


def test_hints_are_computed_once_for_a_tree_read_again(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    real = grouping.weigh
    monkeypatch.setattr(grouping, "weigh", lambda counts: calls.append(1) or real(counts))

    first = options_at(tree(SPEC), tree(SPEC).subfolder("science"))
    root = tree(SPEC)  # the store reads the whole tree again for every document
    again = options_at(root, root.subfolder("science"))

    assert first == again and len(calls) == 1
