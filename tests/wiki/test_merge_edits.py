# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Round 7, blind from spec-fase4.md: `merge_mode` ("rewrite" | "edits"), the merge of a long source by edits applied in code."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from llmw2 import CLASSIC, NoteDraft, Source, StepContext, Wiki, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import decision_hash
from llmw2.bundle.tree import Note
from llmw2.config import ClassifierConfig, LLMConfig
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_steps import MERGING

EDITS, MERGE, MERGE_SOURCE = "librarian/merge_edits", "librarian/merge", "librarian/merge_source"
READ = 14_000  # a text room of 8 000 next to a small note: the least a chunk may take
CHUNK = 8_000

BODY = (
    "# Overview\n\nThe limit is 30 units. Owner: Anna Rossi.\n\n"
    "## Details\n\nSub detail words.\n\n"
    "# Pricing\n\nPrice is 1,200 per month.\n\n"
    "# Contacts\n\nContact: Mario Bianchi.\n"
)
FILLER = "plain words go on and on here without any value at all. "
NO_EDITS: dict[str, Any] = {"edits": [], "additions": []}
OLD_COPY = "An older copy of some other document with entirely different words in it."


def edit(find: str, replace_: str) -> dict[str, str]:
    return {"find": find, "replace": replace_}


def reply(*edits: dict[str, str], additions: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"edits": list(edits), "additions": additions or []}


def long_raw(head: str = "", tail: str = "", size: int = 3_000) -> str:
    return head + (FILLER * (size // len(FILLER) + 1))[:size] + tail


def cfg_of(**fields: Any) -> WikiConfig:
    return WikiConfig(
        bundle=Path("wiki"),
        mode="classic",
        llm=LLMConfig(read_chars=READ),
        classifier=ClassifierConfig(model="fake/jev", api_key="fake-key"),
        note_chars=1_000,
        **fields,
    )


# -- config ----------------------------------------------------------------------------------------


def test_merge_mode_defaults_to_rewrite() -> None:
    assert cfg_of().merge_mode == "rewrite"


@pytest.mark.parametrize("value", ["rewrite", "edits"])
def test_merge_mode_from_the_environment(tmp_path: Path, value: str) -> None:
    assert WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_MERGE_MODE": value}).merge_mode == value


def test_merge_mode_without_the_variable_or_with_it_empty_is_rewrite(tmp_path: Path) -> None:
    assert WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path)}).merge_mode == "rewrite"
    assert WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_MERGE_MODE": ""}).merge_mode == "rewrite"


def test_merge_mode_rejects_other_values(tmp_path: Path) -> None:
    with pytest.raises((ValidationError, ValueError)):
        WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_MERGE_MODE": "patch"})


# -- decision_hash ------------------------------------------------------------------------------------


def hash_of(cfg: WikiConfig) -> str:
    return decision_hash(CLASSIC, cfg, Prompts())


def test_hash_with_rewrite_equals_the_hash_of_a_config_that_does_not_set_the_field() -> None:
    assert hash_of(cfg_of(merge_mode="rewrite")) == hash_of(cfg_of())


def test_hash_is_different_with_edits_and_stable() -> None:
    assert hash_of(cfg_of(merge_mode="edits")) != hash_of(cfg_of())
    assert hash_of(cfg_of(merge_mode="edits")) == hash_of(cfg_of(merge_mode="edits"))


# -- a merge to look at -------------------------------------------------------------------------------


def calls_of(llm: FakeLLM, op: str) -> list[str]:
    return [m[1]["content"] for o, m in llm.calls if o == op]


def run_merge(
    bundle: Path,
    llm: FakeLLM,
    raw: str,
    replies: list[dict[str, Any]],
    *,
    mode: str = "edits",
    body: str = BODY,
    copies: tuple[str, ...] = (OLD_COPY,),
    others: dict[str, list[Any]] | None = None,
) -> NoteDraft:
    """Ingest `raw` into a note /finance/seed.md of `body` that cites a raw copy of each text in `copies`; the merge's draft."""
    seen: list[NoteDraft] = []

    def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        seen.append(classic.merge(ctx, note, source))
        return seen[-1]

    cfg = WikiConfig(bundle=bundle, mode="classic", llm=LLMConfig(read_chars=READ), note_chars=1_000, merge_mode=mode)
    wiki = Wiki(cfg, llm=llm, steps=replace(MERGING, merge=merge))
    wiki.init()
    folder, _ = wiki.store.create_folder(wiki.store.load().find("/"), "finance", "Money in and out.")
    note: Note | None = None
    for n, text in enumerate(copies):
        resource = wiki.store.save_raw(text, title=f"Old {n}", resource=None)
        source = {"resource": resource, "title": f"Old {n}"}
        if note is None:
            note = wiki.store.write_note(folder, title="Seed", summary="Seeded.", body=body, tags=["alpha"], source=source)
        else:
            wiki.store.update_note(note, title="Seed", summary="Seeded.", body=body, tags=[], source=source)
    if replies:
        llm.add(EDITS, *replies)
    for op, queue in (others or {}).items():
        llm.add(op, *queue)
    wiki.ingest(raw, title="Memo")
    return seen[0]


# -- the modes that do not use edits ----------------------------------------------------------------------


def test_rewrite_mode_with_a_long_source_still_uses_merge_source(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The limit is now 40 units. ")

    draft = run_merge(
        bundle,
        llm,
        raw,
        [],
        mode="rewrite",
        others={MERGE_SOURCE: [{"title": "Seed", "summary": "S.", "tags": [], "body": BODY + "More.\n"}]},
    )

    assert MERGE_SOURCE in llm.ops
    assert EDITS not in llm.ops
    assert "More." in draft.body


def test_edits_mode_with_a_short_source_takes_the_old_path(bundle: Path, llm: FakeLLM) -> None:
    raw = "A short memo with plain words only."
    assert len(raw) < 2_000

    draft = run_merge(
        bundle, llm, raw, [], others={MERGE: [{"title": "Seed", "summary": "S.", "tags": [], "body": BODY + "Short memo added.\n"}]}
    )

    assert MERGE in llm.ops
    assert EDITS not in llm.ops
    assert MERGE_SOURCE not in llm.ops
    assert "Short memo added." in draft.body


# -- applying an edit -----------------------------------------------------------------------------------------


def test_an_edit_whose_find_occurs_once_is_applied(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The limit is now 40 units. ")

    draft = run_merge(bundle, llm, raw, [reply(edit("The limit is 30 units.", "The limit is 40 units."))])

    assert "The limit is 40 units." in draft.body
    assert "The limit is 30 units." not in draft.body
    assert draft.body.count("# Pricing") == 1
    assert "Price is 1,200 per month." in draft.body  # the rest of the note is the note
    assert EDITS in llm.ops and MERGE_SOURCE not in llm.ops and MERGE not in llm.ops


def test_title_summary_and_tags_stay_the_notes(bundle: Path, llm: FakeLLM) -> None:
    draft = run_merge(
        bundle, llm, long_raw("The limit is now 40 units. "), [reply(edit("The limit is 30 units.", "The limit is 40 units."))]
    )

    assert (draft.title, draft.summary, draft.tags) == ("Seed", "Seeded.", ["alpha"])


def test_a_replaced_value_is_kept_as_previously(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The limit is now 40 units. ")

    draft = run_merge(bundle, llm, raw, [reply(edit("The limit is 30 units.", "The limit is 40 units."))])

    assert "The limit is 40 units. (previously 30)" in draft.body


def test_several_replaced_values_are_listed_in_order_of_appearance(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The limit is now 40 units and the owner is Luca Verdi. ")
    find = "The limit is 30 units. Owner: Anna Rossi."

    draft = run_merge(bundle, llm, raw, [reply(edit(find, "The limit is 40 units. Owner: Luca Verdi."))])

    assert "Owner: Luca Verdi. (previously 30, Anna Rossi)" in draft.body


def test_a_replace_that_already_says_previously_gets_nothing_more(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The limit is now 40 units. ")

    draft = run_merge(bundle, llm, raw, [reply(edit("The limit is 30 units.", "The limit is 40 units, previously 30."))])

    assert "The limit is 40 units, previously 30." in draft.body
    assert draft.body.count("previously") == 1


def test_an_edit_that_removes_no_value_gets_no_previously(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The limit is now 40 units. ")

    draft = run_merge(bundle, llm, raw, [reply(edit("Sub detail words.", "Sub detail words, rephrased."))])

    assert "Sub detail words, rephrased." in draft.body
    assert "previously" not in draft.body


def test_an_edit_not_found_is_rejected_and_the_body_keeps_its_passage(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The limit is now 40 units. ")

    draft = run_merge(bundle, llm, raw, [reply(edit("The limit is 31 units.", "The limit is 40 units.")), reply()])

    assert "The limit is 30 units." in draft.body
    assert "40 units" not in draft.body


def test_an_ambiguous_edit_is_rejected_and_the_body_keeps_its_passage(bundle: Path, llm: FakeLLM) -> None:
    body = "# Overview\n\nStatus: active.\n\n# Pricing\n\nStatus: active.\n\nPrice is 1,200 per month.\n"
    raw = long_raw("The status is now closed. ")

    draft = run_merge(bundle, llm, raw, [reply(edit("Status: active.", "Status: closed.")), reply()], body=body)

    assert draft.body.count("Status: active.") == 2
    assert "closed" not in draft.body


def test_an_edit_that_invents_a_value_is_rejected(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The limit is now 40 units. ")

    draft = run_merge(bundle, llm, raw, [reply(edit("The limit is 30 units.", "The limit is 99 units.")), reply()])

    assert "The limit is 30 units." in draft.body
    assert "99" not in draft.body


def test_an_invented_name_in_an_edit_is_rejected(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The limit is now 40 units. ")

    draft = run_merge(bundle, llm, raw, [reply(edit("Contact: Mario Bianchi.", "Contact: Paolo Neri.")), reply()])

    assert "Contact: Mario Bianchi." in draft.body
    assert "Paolo Neri" not in draft.body


def test_good_edits_are_applied_next_to_rejected_ones(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The limit is now 40 units. ")
    edits = [
        edit("Nowhere to be found.", "x"),
        edit("The limit is 30 units.", "The limit is 40 units."),
        edit("Price is 1,200 per month.", "Price is 777."),
    ]

    draft = run_merge(bundle, llm, raw, [reply(*edits)])

    assert "The limit is 40 units. (previously 30)" in draft.body
    assert "Price is 1,200 per month." in draft.body
    assert "777" not in draft.body


# -- applying an addition -----------------------------------------------------------------------------------


def test_an_addition_goes_at_the_end_of_its_section(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("A setup fee of 250 applies. ")

    draft = run_merge(bundle, llm, raw, [reply(additions=[{"section": "# Pricing", "text": "Setup fee is 250."}])])

    body = draft.body
    assert body.index("Price is 1,200 per month.") < body.index("Setup fee is 250.") < body.index("# Contacts")


def test_the_heading_of_an_addition_matches_without_case(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("A setup fee of 250 applies. ")

    draft = run_merge(bundle, llm, raw, [reply(additions=[{"section": "# PRICING", "text": "Setup fee is 250."}])])

    body = draft.body
    assert body.count("Pricing") == 1 and body.lower().count("# pricing") == 1  # no new heading
    assert body.index("Price is 1,200 per month.") < body.index("Setup fee is 250.") < body.index("# Contacts")


def test_the_heading_of_an_addition_matches_without_its_marks(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("A setup fee of 250 applies. ")

    draft = run_merge(bundle, llm, raw, [reply(additions=[{"section": "Pricing", "text": "Setup fee is 250."}])])

    body = draft.body
    assert body.count("Pricing") == 1  # no new heading
    assert body.index("Price is 1,200 per month.") < body.index("Setup fee is 250.") < body.index("# Contacts")


def test_an_addition_to_the_last_section_goes_at_the_end(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("A second contact is 555. ")

    draft = run_merge(bundle, llm, raw, [reply(additions=[{"section": "# Contacts", "text": "Desk 555."}])])

    assert draft.body.index("Contact: Mario Bianchi.") < draft.body.index("Desk 555.")
    assert draft.body.rstrip().endswith("Desk 555.")


def test_an_addition_to_a_section_goes_after_its_subsections(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("A second owner exists, code 4455. ")

    draft = run_merge(bundle, llm, raw, [reply(additions=[{"section": "# Overview", "text": "Extra 4455."}])])

    body = draft.body
    assert body.index("Sub detail words.") < body.index("Extra 4455.") < body.index("# Pricing")


def test_an_addition_to_a_subsection_stops_at_the_next_heading(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("A second owner exists, code 4455. ")

    draft = run_merge(bundle, llm, raw, [reply(additions=[{"section": "## Details", "text": "Extra 4455."}])])

    body = draft.body
    assert body.index("Sub detail words.") < body.index("Extra 4455.") < body.index("# Pricing")


def test_an_addition_to_an_unknown_section_becomes_a_heading_at_the_end(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The warranty lasts 24 months. ")

    draft = run_merge(bundle, llm, raw, [reply(additions=[{"section": "Warranty", "text": "Warranty lasts 24 months."}])])

    assert draft.body.startswith(BODY.rstrip()[:40])
    assert "\n\n# Warranty\nWarranty lasts 24 months." in draft.body
    assert draft.body.index("Contact: Mario Bianchi.") < draft.body.index("# Warranty")


def test_an_unknown_section_already_written_as_a_heading_keeps_its_level(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The warranty lasts 24 months. ")

    draft = run_merge(bundle, llm, raw, [reply(additions=[{"section": "## Warranty", "text": "Warranty lasts 24 months."}])])

    assert "\n\n## Warranty\nWarranty lasts 24 months." in draft.body
    assert "# ## Warranty" not in draft.body


def test_an_addition_with_an_invented_value_is_rejected(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("A setup fee of 250 applies. ")

    draft = run_merge(bundle, llm, raw, [reply(additions=[{"section": "# Pricing", "text": "Setup fee is 999."}]), reply()])

    assert "999" not in draft.body
    assert draft.body.strip() == BODY.strip()


# -- chunks ----------------------------------------------------------------------------------------------------


def test_a_source_that_fits_is_read_in_one_call(bundle: Path, llm: FakeLLM) -> None:
    run_merge(
        bundle, llm, long_raw("The limit is now 40 units. ", size=5_000), [reply(edit("The limit is 30 units.", "The limit is 40 units."))]
    )

    assert len(calls_of(llm, EDITS)) == 1


def test_a_source_longer_than_the_room_is_read_in_several_chunks_each_seeing_the_edited_body(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The limit is now 40 units. ", " A setup fee of 250 applies.", size=20_000)
    replies = [
        reply(edit("The limit is 30 units.", "The limit is 40 units.")),
        reply(),
        reply(additions=[{"section": "# Pricing", "text": "Setup fee is 250."}]),
    ]

    draft = run_merge(bundle, llm, raw, replies)

    prompts = calls_of(llm, EDITS)
    assert len(prompts) == math.ceil(len(raw) / CHUNK) == 3
    assert "(previously 30)" not in prompts[0]
    assert "The limit is 30 units." in prompts[0]
    for later in prompts[1:]:
        assert "The limit is 40 units. (previously 30)" in later
        assert "The limit is 30 units." not in later
    assert "Setup fee is 250." in prompts[2] or "Setup fee is 250." in draft.body  # the third call's reply is applied
    assert "The limit is 40 units. (previously 30)" in draft.body
    body = draft.body
    assert body.index("Price is 1,200 per month.") < body.index("Setup fee is 250.") < body.index("# Contacts")


def test_a_bigger_note_leaves_less_room_to_a_chunk(bundle: Path, llm: FakeLLM) -> None:
    big = BODY + "\n# Notes\n\n" + ("Some note words here. " * 100) + "\n"
    raw = long_raw("The limit is now 40 units. ", size=7_900)  # fits 8 000, and not the room that is left next to a 4 000 note
    assert len(big) > 2_000

    run_merge(bundle, llm, raw, [reply(edit("The limit is 30 units.", "The limit is 40 units.")), reply()], body=big)

    # budget = max(8000, 14000 - 6000 - len(body)): never under the least chunk
    assert len(calls_of(llm, EDITS)) == math.ceil(len(raw) / max(CHUNK, READ - 6_000 - len(big)))


# -- the revision diff --------------------------------------------------------------------------------------------


def paragraph(i: int) -> str:
    return " ".join(f"lorem{chr(97 + i // 26)}{chr(97 + i % 26)}{chr(97 + j)}" for j in range(12))


def revision(shared: int = 40) -> tuple[str, str]:
    """(old copy of `shared` paragraphs, new text of 41: the 40 with the sixth changed and one added)."""
    old = [paragraph(i) for i in range(40)]
    changed = [*old]
    changed[5] = "Changed paragraph where the limit is raised to 40 units today."
    changed.insert(10, "Brand new paragraph where a fee of 250 applies for setup.")
    return "\n\n".join(old[:shared]), "\n\n".join(changed)


def test_a_revision_of_a_cited_copy_shows_the_changed_paragraphs_only(bundle: Path, llm: FakeLLM) -> None:
    old, new = revision()
    assert len(new) > 2_000

    draft = run_merge(bundle, llm, new, [reply(edit("The limit is 30 units.", "The limit is 40 units."))], copies=(old,))

    [prompt] = calls_of(llm, EDITS)
    assert "BEFORE:" in prompt and "AFTER:" in prompt and "ADDED:" in prompt
    assert paragraph(5) in prompt  # the old paragraph, under BEFORE
    assert "Changed paragraph where the limit is raised to 40 units today." in prompt
    assert "Brand new paragraph where a fee of 250 applies for setup." in prompt
    assert paragraph(20) not in prompt and paragraph(30) not in prompt  # the unchanged ones are not read
    assert "the changes between the previous version of this source and the new one" in prompt
    assert "The limit is 40 units." in draft.body


def test_the_blocks_of_a_diff_are_ordered_before_after_and_joined_by_rules(bundle: Path, llm: FakeLLM) -> None:
    old, new = revision()

    run_merge(bundle, llm, new, [reply(edit("The limit is 30 units.", "The limit is 40 units."))], copies=(old,))

    [prompt] = calls_of(llm, EDITS)
    assert prompt.index("BEFORE:") < prompt.index(paragraph(5)) < prompt.index("AFTER:") < prompt.index("Changed paragraph where")
    assert "\n\n---\n\n" in prompt
    assert prompt.index("Changed paragraph where") < prompt.index("ADDED:") < prompt.index("Brand new paragraph")


def test_the_revision_is_the_copy_that_shares_most_of_the_source(bundle: Path, llm: FakeLLM) -> None:
    old, new = revision()

    run_merge(bundle, llm, new, [reply(edit("The limit is 30 units.", "The limit is 40 units."))], copies=(OLD_COPY, old))

    [prompt] = calls_of(llm, EDITS)
    assert "BEFORE:" in prompt
    assert paragraph(20) not in prompt


def test_a_copy_sharing_more_than_half_of_the_source_is_a_revision(bundle: Path, llm: FakeLLM) -> None:
    old, new = revision(shared=28)  # the old copy has 28 paragraphs: most of the new text is there

    run_merge(bundle, llm, new, [reply(edit("The limit is 30 units.", "The limit is 40 units."))], copies=(old,))

    [prompt] = calls_of(llm, EDITS)
    assert "ADDED:" in prompt
    assert paragraph(3) not in prompt  # shared, unchanged
    assert paragraph(35) in prompt  # new with respect to the copy


def test_a_copy_sharing_less_than_half_of_the_source_is_not_a_revision(bundle: Path, llm: FakeLLM) -> None:
    old, new = revision(shared=12)

    run_merge(bundle, llm, new, [reply(edit("The limit is 30 units.", "The limit is 40 units."))], copies=(old,))

    [prompt] = calls_of(llm, EDITS)
    assert "BEFORE:" not in prompt and "ADDED:" not in prompt
    assert paragraph(3) in prompt and paragraph(20) in prompt and paragraph(35) in prompt  # the whole source
    assert "a new source" in prompt


def test_without_a_similar_copy_the_model_reads_the_source(bundle: Path, llm: FakeLLM) -> None:
    _, new = revision()

    run_merge(bundle, llm, new, [reply(edit("The limit is 30 units.", "The limit is 40 units."))])

    [prompt] = calls_of(llm, EDITS)
    assert "BEFORE:" not in prompt and "ADDED:" not in prompt
    assert new in prompt
    assert "a new source" in prompt
    assert "the changes between the previous version" not in prompt


def test_a_revision_diff_is_read_in_chunks_too(bundle: Path, llm: FakeLLM) -> None:
    old = [paragraph(i) for i in range(400)]  # about 42 000 characters
    new = [*old]
    for i in range(0, 400, 4):
        new[i] = f"Changed paragraph number {i} where the limit is raised to 40 units today and then some more words follow here."
    raw = "\n\n".join(new)

    run_merge(bundle, llm, raw, [reply(), reply(), reply(), reply(), reply(), reply()], copies=("\n\n".join(old),))

    prompts = calls_of(llm, EDITS)
    diff_chars = sum(len(p) for p in prompts)
    assert len(prompts) >= 2
    assert all("BEFORE:" in p for p in prompts)
    assert paragraph(1) not in "".join(prompts)  # the unchanged are never read
    assert diff_chars < len(raw)


# -- no change at all -----------------------------------------------------------------------------------------------


def test_no_change_with_values_the_note_lacks_asks_once_more_naming_them(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("Codes AB-1001 and AB-1002 and the owner Luca Verdi. ")

    draft = run_merge(bundle, llm, raw, [reply(), reply()])

    first, second = calls_of(llm, EDITS)
    assert second != first and len(second) > len(first)
    assert classic.MERGE_UNCHANGED_NOTE.strip()[:30] in second
    for value in ("AB-1001", "AB-1002", "Luca Verdi"):
        assert second.count(value) > first.count(value)  # named in the note the retry adds
    assert draft.body.strip() == BODY.strip()


def test_the_retry_does_not_list_the_values_the_note_has(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("Codes AB-1001 and the owner Anna Rossi at 1,200 and 30. ")

    run_merge(bundle, llm, raw, [reply(), reply()])

    first, second = calls_of(llm, EDITS)
    assert second.count("Anna Rossi") == first.count("Anna Rossi")
    assert second.count("1,200") == first.count("1,200")
    assert second.count("AB-1001") > first.count("AB-1001")


def test_the_retry_is_applied(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("Codes AB-1001 is new. ")

    draft = run_merge(bundle, llm, raw, [reply(), reply(additions=[{"section": "# Contacts", "text": "Code AB-1001."}])])

    assert "Code AB-1001." in draft.body
    assert len(calls_of(llm, EDITS)) == 2


def test_the_retry_happens_once(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("Codes AB-1001 is new. ")

    run_merge(bundle, llm, raw, [reply(), reply()])  # a third call would find no scripted reply

    assert len(calls_of(llm, EDITS)) == 2


def test_no_retry_when_the_source_has_no_values_the_note_lacks(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The owner Anna Rossi pays 1,200 per month and the limit is 30. ")

    draft = run_merge(bundle, llm, raw, [reply()])

    assert len(calls_of(llm, EDITS)) == 1
    assert draft.body.strip() == BODY.strip()


def test_no_retry_when_something_was_applied(bundle: Path, llm: FakeLLM) -> None:
    raw = long_raw("The limit is now 40 units. Codes AB-1001. ")

    run_merge(bundle, llm, raw, [reply(edit("The limit is 30 units.", "The limit is 40 units."))])

    assert len(calls_of(llm, EDITS)) == 1


# -- the log ----------------------------------------------------------------------------------------------------------


def by_edits(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level and "by edits" in r.getMessage()]


def test_the_warning_counts_applied_and_rejected_by_reason(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    raw = long_raw("The limit is now 40 units. ")
    body = BODY + "\nStatus: active. Status: active.\n"
    edits = [
        edit("The limit is 30 units.", "The limit is 40 units."),  # applied
        edit("Nowhere to be found.", "x"),  # not found
        edit("Status: active.", "Status: closed."),  # ambiguous
        edit("Price is 1,200 per month.", "Price is 777."),  # invented
    ]

    with caplog.at_level(logging.INFO):
        run_merge(bundle, llm, raw, [reply(*edits)], body=body)

    [message] = by_edits(caplog, logging.WARNING)
    assert re.search(r"1 applied, 3 rejected \(not found 1, ambiguous 1, invented 1\)", message)
    assert "merge" in message


def test_without_a_rejection_it_is_only_an_info(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    raw = long_raw("The limit is now 40 units. ")

    with caplog.at_level(logging.INFO):
        run_merge(bundle, llm, raw, [reply(edit("The limit is 30 units.", "The limit is 40 units."))])

    assert by_edits(caplog, logging.WARNING) == []
    [message] = by_edits(caplog, logging.INFO)
    assert "1 applied, 0 rejected" in message


def test_a_body_far_over_the_length_asked_warns_as_the_other_paths_do(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    big = (
        "# Overview\n\n" + "The limit is 30 units. " + ("Some filler words in the note. " * 120) + "\n"
    )  # about 3 700 characters, asked 1 000
    raw = long_raw("The limit is now 40 units. ")

    with caplog.at_level(logging.WARNING):
        run_merge(bundle, llm, raw, [reply(edit("The limit is 30 units.", "The limit is 40 units."))], body=big)

    assert [r for r in caplog.records if r.levelno == logging.WARNING and "characters, over" in r.getMessage()]
