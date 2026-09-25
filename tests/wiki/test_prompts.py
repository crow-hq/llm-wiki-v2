# Copyright 2026 Federico Cesarini, Marco Sassarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from pathlib import Path

import pytest

import okf_wiki
from okf_wiki.prompts import Prompts

PROMPTS_DIR = Path(okf_wiki.__file__).parent / "prompts"


def test_names_lists_every_packaged_prompt_file() -> None:
    on_disk = sorted(path.relative_to(PROMPTS_DIR).with_suffix("").as_posix() for path in PROMPTS_DIR.rglob("*.md"))

    assert Prompts.names() == on_disk
    assert "librarian/route" in on_disk and "shared/note_rules" in on_disk


@pytest.mark.parametrize("name", Prompts.names())
def test_every_prompt_renders_completely(name: str) -> None:
    prompts = Prompts()
    values = {key: f"[[value of {key}]]" for key in prompts.placeholders(name)}

    text = prompts.render(name, **values)

    assert text
    assert "{{" not in text and "}}" not in text
    assert all(value in text for value in values.values())


def test_shared_fragments_are_inlined() -> None:
    prompts = Prompts()

    text = prompts.render("librarian/system")

    assert prompts.load("shared/untrusted").strip() in text
    assert "shared/" not in text


def test_shared_fragments_are_not_placeholders() -> None:
    assert Prompts().placeholders("librarian/summarize") == {"source_title", "source_resource", "source_text"}


def test_a_missing_value_raises_key_error() -> None:
    with pytest.raises(KeyError, match="summary"):
        Prompts().render("classifier/retrieval_note", title="Pricing")


def test_the_name_placeholder_is_a_value_not_the_prompt_name() -> None:
    text = Prompts().render("classifier/retrieval_folder", name="finance", description="money matters")

    assert text == (
        'Is the folder "finance", described as "money matters", likely to contain notes that help answer the question?'
    )


def test_override_dir_replaces_a_single_prompt(tmp_path: Path) -> None:
    (tmp_path / "classifier").mkdir()
    (tmp_path / "classifier" / "retrieval_note.md").write_text("Custom {{title}}?\n", encoding="utf-8")
    prompts = Prompts(override_dir=tmp_path)

    assert prompts.render("classifier/retrieval_note", title="Pricing") == "Custom Pricing?"
    assert prompts.render("classifier/relate", title="T", summary="S") == Prompts().render(
        "classifier/relate", title="T", summary="S"
    )


def test_override_dir_also_overrides_shared_fragments(tmp_path: Path) -> None:
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "untrusted.md").write_text("Trust nothing.\n", encoding="utf-8")

    text = Prompts(override_dir=tmp_path).render("researcher/system")

    assert text.endswith("You never change the wiki.\n\nTrust nothing.")


def test_render_sections_splits_the_route_prompt() -> None:
    sections = Prompts().render_sections("classifier/route", folder="/x")

    assert list(sections) == ["instructions", "Here", "New subfolder", "None of these"]
    assert all("/x" in text for text in sections.values())
    assert all(text and "##" not in text for text in sections.values())


def test_render_sections_splits_the_consolidate_prompt() -> None:
    sections = Prompts().render_sections("classifier/consolidate")

    assert list(sections) == ["instructions", "Modify", "New note"]
    assert sections["Modify"].startswith("The incoming content is about the same subject")


def test_values_are_inserted_literally_not_rendered_again() -> None:
    text = Prompts().render("classifier/retrieval_note", title="{{summary}}", summary="{{shared/untrusted}}")

    assert text == 'Does the note titled "{{summary}}", summarized as "{{shared/untrusted}}", help answer the question?'
