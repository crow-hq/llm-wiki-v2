# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`Wiki(redact=...)`: the texts the wiki derives and repeats outside a note's body go through the caller's function; the body does not."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.bundle.tree import INDEX, LOG, Folder
from llmw2.models.usage import UsageTracker
from tests.wiki.areas import STEPS, file
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_plan_groups import PAY, ROAST

WORD = "Zorro"


def hide(text: str) -> str:
    return text.replace(WORD, "XXX")


def redacting(bundle: Path, redact: Callable[[str], str] | None = hide) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", max_links=8), llm=FakeLLM(UsageTracker()), steps=STEPS, redact=redact)
    wiki.init()
    for name in ("red", "blue"):
        wiki.create_folder("/", name, f"{name.capitalize()} things.")
    return wiki


def everything_but_bodies(bundle: Path) -> str:
    """Every file of the wiki, a note without its body: the frontmatter, the indexes, the log, the See-also lists."""
    parts = []
    for path in sorted(bundle.rglob("*.md")):
        if "raw" in path.relative_to(bundle).parts:
            continue
        text = path.read_text(encoding="utf-8")
        if path.name not in (INDEX, LOG):
            front, _, body = text.partition("\n---\n")
            _, _, see = body.partition("# See also")
            text = front + "\n" + see
        parts.append(text)
    return "\n".join(parts)


def test_title_and_summary_are_redacted_in_the_note_the_indexes_the_log_and_the_see_also_lists(bundle: Path) -> None:
    wiki = redacting(bundle)

    first = file(wiki, "/red", f"Project {WORD}", f"{WORD} leads the plan.")
    second = file(wiki, "/blue", "Other", "Nothing special.")

    note = wiki.store.read(first)
    assert note.frontmatter["title"] == "Project XXX" and note.frontmatter["description"] == "XXX leads the plan."
    assert first == "red/project-xxx.md"  # the file name follows the title
    assert f"{WORD} leads the plan." in note.body  # the body is the document's own
    other = wiki.store.read(second).body
    assert "[Project XXX](../red/project-xxx.md) - XXX leads the plan." in other
    assert WORD not in everything_but_bodies(bundle)
    assert "XXX" in (bundle / "red" / INDEX).read_text(encoding="utf-8")
    assert "Project XXX" in (bundle / LOG).read_text(encoding="utf-8")


def test_a_merge_redacts_the_title_and_summary_of_the_draft(bundle: Path) -> None:
    from dataclasses import replace

    from llmw2.agents.data import NoteDraft

    steps = replace(
        STEPS,
        match=lambda ctx, pool, cand: pool[0] if pool else None,
        consolidate=lambda ctx, note, cand: True,
        merge=lambda ctx, note, source: NoteDraft(title=f"All {WORD}", summary=f"About {WORD}.", body=source.text),
    )
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=FakeLLM(UsageTracker()), steps=steps, redact=hide)
    wiki.init()
    wiki.create_folder("/", "red", "Red things.")
    rel = file(wiki, "/red", "First")

    merged = file(wiki, "/red", "Second", f"{WORD} again.")

    note = wiki.store.read(merged)
    assert merged == rel and note.frontmatter["title"] == "All XXX" and note.frontmatter["description"] == "About XXX."
    assert WORD not in everything_but_bodies(bundle) and f"{WORD} again." in note.body


def test_the_folders_a_plan_makes_are_named_and_described_through_it(bundle: Path) -> None:
    wiki = redacting(bundle)
    texts = [(f"{WORD} roast {i}", t.replace("Aurora", WORD)) for i, t in enumerate(ROAST)] + [(None, PAY[0]), (None, PAY[1])]

    made: list[Folder] = []
    wiki.plan_groups(texts, made=made)

    assert made and all(WORD not in f.label and WORD not in f.description for f in made)
    assert WORD not in everything_but_bodies(bundle)


def test_a_person_s_own_text_is_not_redacted(bundle: Path) -> None:
    wiki = redacting(bundle)

    wiki.create_folder("/", "legal", f"The {WORD} files.")
    wiki.describe_folder("/legal", f"Still {WORD}.")

    assert f"Still {WORD}." in (bundle / INDEX).read_text(encoding="utf-8")


def test_without_a_redactor_nothing_changes(bundle: Path) -> None:
    wiki = redacting(bundle, None)

    rel = file(wiki, "/red", f"Project {WORD}")

    assert wiki.store.read(rel).frontmatter["title"] == f"Project {WORD}" and rel == "red/project-zorro.md"


def test_a_redactor_that_raises_stops_the_filing(bundle: Path) -> None:
    def refuse(text: str) -> str:
        raise RuntimeError("no")

    wiki = redacting(bundle, refuse)

    with pytest.raises(RuntimeError):
        file(wiki, "/red", "Title")

    assert wiki.store.load().all_notes() == []


def test_a_title_the_redactor_empties_becomes_untitled(bundle: Path) -> None:
    wiki = redacting(bundle, lambda text: "")

    rel = file(wiki, "/red", "Whatever")

    assert wiki.store.read(rel).frontmatter["title"] == "Untitled"
