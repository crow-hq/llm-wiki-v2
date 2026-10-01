# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The examples in examples/: the model-free ones run on a temporary bundle, the others only import."""

from __future__ import annotations

import importlib.util
import runpy
import sys
from pathlib import Path
from types import ModuleType

import pytest

from llmw2.bundle.document import OKFDocument

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_labeled_dataset_files_every_row_under_its_category_path(tmp_path: Path) -> None:
    wiki = load("labeled_dataset").main(tmp_path / "wiki")
    tree = wiki.tree()
    assert {f.label for f in tree.walk() if not f.is_root} == {
        "/animals", "/animals/mammals", "/animals/mammals/dogs", "/animals/mammals/cats", "/animals/birds",
        "/rocks", "/rocks/igneous", "/rocks/metamorphic",
    }  # fmt: skip
    dogs = tree.find("/animals/mammals/dogs")
    assert dogs is not None and sorted(n.title for n in dogs.notes) == ["Border collie", "Greyhound"]
    assert dogs.notes[0].frontmatter["fields"]["category"] == ["Animals", "Mammals", "Dogs"]
    assert wiki.usage.llm.calls == 0 and wiki.usage.classifier.calls == 0
    assert wiki.check() == []


def test_custom_select_answers_by_field_with_citations(tmp_path: Path) -> None:
    wiki = load("custom_select").main(tmp_path / "wiki")
    answer = wiki.ask("Which devices are still in use?")
    assert answer.citations == ["devices/pocket-thermometer.md", "devices/router.md"]
    assert "Desk lamp" not in answer.text
    assert wiki.usage.llm.calls == 0


def test_sync_connector_creates_merges_and_marks_removed(tmp_path: Path) -> None:
    wiki, first, second = load("sync_connector").main(tmp_path / "wiki")
    assert (first.created, first.merged) == (3, 0)
    assert (second.created, second.merged, second.removed) == (0, 1, 1)
    holidays = OKFDocument.parse((tmp_path / "wiki" / "inbox" / "holidays.md").read_text(encoding="utf-8"))
    assert "2 January" in holidays.body
    assert (tmp_path / "wiki" / "inbox" / "parking.md").exists()  # a removal at the source keeps the notes
    assert wiki.usage.llm.calls == 0


def test_an_example_runs_as_a_script(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["labeled_dataset.py", str(tmp_path / "wiki")])
    runpy.run_path(str(EXAMPLES / "labeled_dataset.py"), run_name="__main__")
    assert "model calls: 0" in capsys.readouterr().out
    assert (tmp_path / "wiki" / "animals" / "mammals" / "dogs" / "greyhound.md").exists()


@pytest.mark.parametrize("name", ["quickstart", "multi_pass_summarize"])
def test_examples_that_need_a_key_import_without_side_effects(name: str, tmp_path: Path) -> None:
    assert callable(load(name).main)
    assert list(tmp_path.iterdir()) == []
