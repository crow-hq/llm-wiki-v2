# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from pathlib import Path

import pytest

import llmw2
from llmw2.agents.prompts import Prompts

PROMPTS_DIR = Path(llmw2.__file__).parent / "agents" / "prompts"


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
    assert Prompts().placeholders("librarian/summarize") == {"source_title", "source_resource", "source_text", "length_rule"}


def test_a_missing_value_raises_key_error() -> None:
    with pytest.raises(KeyError, match="passage"):
        Prompts().render("classifier/retrieval_note", title="Pricing", summary="Plans")


def test_the_retrieval_note_prompt_ends_with_the_passage() -> None:
    text = Prompts().render("classifier/retrieval_note", title="Pricing", summary="Plans", passage="Three tiers.")

    assert text == (
        'Does the note titled "Pricing", summarized as "Plans", help answer the question? It contains: "Three tiers."'
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
    text = Prompts().render("classifier/retrieval_note", title="{{summary}}", summary="{{shared/untrusted}}", passage="{{title}}")

    assert text == (
        'Does the note titled "{{summary}}", summarized as "{{shared/untrusted}}", help answer the question? It contains: "{{title}}"'
    )


CANARY = "dd60f2b2-fab6-4351-826f-bc7357357178"


@pytest.mark.parametrize("path", sorted(PROMPTS_DIR.rglob("*.md")), ids=lambda path: path.relative_to(PROMPTS_DIR).as_posix())
def test_every_prompt_file_starts_with_the_license_header(path: Path) -> None:
    text = path.read_text(encoding="utf-8")

    assert text.startswith("<!--\nSPDX-FileCopyrightText:")
    assert CANARY in text.split("-->", 1)[0]


@pytest.mark.parametrize("name", Prompts.names())
def test_load_never_returns_the_license_header(name: str) -> None:
    text = Prompts().load(name)

    assert "<!--" not in text and CANARY not in text and "SPDX-" not in text


def test_override_header_is_stripped_and_a_headerless_override_is_kept(tmp_path: Path) -> None:
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "untrusted.md").write_text("<!--\nlicense header\n-->\nbody\n", encoding="utf-8")
    (tmp_path / "shared" / "note_rules.md").write_text("body\n<!-- kept -->\n", encoding="utf-8")
    prompts = Prompts(tmp_path)

    assert prompts.load("shared/untrusted") == "body\n"
    assert prompts.load("shared/note_rules") == "body\n<!-- kept -->\n"
