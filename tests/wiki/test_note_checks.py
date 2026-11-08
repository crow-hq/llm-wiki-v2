# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`llm.note_steps`: context for the title, exact items for the body, and the checks of what a small writer returns."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2 import Source, StepContext, WikiConfig
from llmw2.agents import note_checks as nc
from llmw2.agents import steps_classic as classic
from llmw2.agents.prompts import Prompts
from llmw2.config import LLMConfig
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeLLM

IT = (
    "Il contratto è stato firmato dalla società che non ha pagato le fatture, e il cliente ha detto che nel mese di maggio "
    "sarebbe stato più chiaro per tutti. Questa è la nostra posizione sul tema."
)
ES = (
    "El contrato fue firmado por la empresa que no ha pagado las facturas, y el cliente dijo que en el mes de mayo "
    "sería más claro para todos. Esta es la posición de los socios sobre el tema."
)
META = {"title": "Contratto", "summary": "Cosa dice il contratto.", "tags": ["contratti"]}


class SeedLLM(FakeLLM):
    def chat(self, messages, *, op="", json_mode=False, max_tokens=None, seed=None):  # type: ignore[no-untyped-def,override]
        return super().chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)


def context() -> tuple[StepContext, FakeLLM]:
    llm = SeedLLM(UsageTracker())
    cfg = WikiConfig(bundle=Path("wiki"), mode="classic", llm=LLMConfig(model="fake/llm", note_steps=True))
    return StepContext(llm, None, Prompts(), cfg, "librarian/system"), llm


def prompt_of(llm: FakeLLM, k: int) -> str:
    return llm.calls[k][1][-1]["content"]


# -- title ----------------------------------------------------------------------------------


def test_heading_is_the_first_markdown_heading_or_a_short_first_line() -> None:
    assert nc.heading_of("Preamble line\n\n## Real Heading\n\ntext") == "Real Heading"
    assert nc.heading_of("\n\nShort first line\nmore") == "Short first line"
    assert nc.heading_of("x" * 300) == ""


def test_metadata_prompt_gets_heading_and_names() -> None:
    ctx, llm = context()
    text = f"# Consorzio Difesa Contratto\n\n{IT} Ne ha parlato Mario Rossi con la Acme."
    llm.add("librarian/summarize_body", "Il contratto è firmato da Mario Rossi.").add("librarian/summarize_meta", META)

    classic.summarize(ctx, Source(text=text, title="doc-1234", file_name="doc-1234.md"))

    sent = prompt_of(llm, 1)
    assert "The document is called: Consorzio Difesa Contratto." in sent
    assert "Closest folder" not in sent
    assert "Mario" in sent and "Rossi" in sent and "Acme" in sent


def test_title_with_an_id_from_the_file_name_falls_back_to_the_heading() -> None:
    assert nc.checked_title("Contratto a3f9c2d1e7", "# Contratto Acme\n\ntesto", "contratto-a3f9c2d1e7.md") == "Contratto Acme"


def test_title_with_an_id_that_the_source_has_is_kept() -> None:
    assert nc.checked_title("Fattura 20240105", "Fattura 20240105 pagata", None) == "Fattura 20240105"


@pytest.mark.parametrize("title", ["Il contratto di", "Contratto con Acme,", "Contratto della società..."])
def test_cut_or_dangling_titles_fall_back_to_the_file_name_without_its_id(title: str) -> None:
    got = nc.checked_title(title, "x " * 100, "contratto_acme_8a7f6e5d4c.md")
    assert got == "contratto acme"


def test_misspelt_name_is_replaced_by_the_source_spelling() -> None:
    text = "Ieri Federico Cesarini ha incontrato la Acme per il contratto."
    assert nc.checked_title("Incontro con Cesarino e Acme", text, None) == "Incontro con Cesarini e Acme"


def test_name_with_two_close_spellings_is_rejected() -> None:
    text = "Ieri Marco Rossi e poi Marca Rossa hanno parlato di questo con Mario."
    assert nc.checked_title("Incontro di Marcu", "# Verbale\n\n" + text, None) == "Verbale"


def test_acronym_near_a_source_name_is_replaced_by_it() -> None:
    text = "Il consorzio NETA ha firmato. Mario Rossi ha parlato con Acme."
    assert nc.checked_title("Contratto del NAT", text, None) == "Contratto del NETA"


def test_acronym_near_two_source_names_is_rejected() -> None:
    text = "# Verbale\n\nIl consorzio NETA e la sigla NOTA hanno firmato."
    assert nc.checked_title("Contratto del NAT", text, None) == "Verbale"


def test_title_case_words_the_source_does_not_have_are_kept() -> None:
    text = "Ieri Federico Cesarini ha incontrato la Acme per il contratto."
    assert nc.checked_title("Request Summary Acme", text, None) == "Request Summary Acme"


# -- body -----------------------------------------------------------------------------------

SOURCE = f"{IT} Vedi https://example.org/a_b?x=1 e il file consorzio_difesa.md, scrivere a info@example.org."


def test_items_are_listed_in_the_body_prompt_only_when_there_are_any() -> None:
    ctx, llm = context()
    llm.add("librarian/summarize_body", SOURCE, IT).add("librarian/summarize_meta", META, META)

    classic.summarize(ctx, Source(text=SOURCE, title="Nota"))
    classic.summarize(ctx, Source(text=IT, title="Nota"))

    assert "https://example.org/a_b?x=1" in prompt_of(llm, 0) and "consorzio_difesa" in prompt_of(llm, 0)
    assert "Copy these exactly" not in prompt_of(llm, 2)
    assert nc.exact_items("bare 12 kg and 2024-01-05") == []


def test_stray_tags_are_removed_and_are_not_altered_items() -> None:
    source = "Use the API at https://example.org/a_b and keep <br> as it is."
    body = "Use the API at https://example.org/a_b.\n</source>\nkeep <br> as it is.\n<content>"

    assert nc.without_stray_tags(body, source) == "Use the API at https://example.org/a_b.\n\nkeep <br> as it is.\n"
    assert nc.altered("see </note> here", "see here") == []


def test_paths_are_listed_whole() -> None:
    text = "Edit android/app/build.gradle and android/local.properties, then docs/a.md."

    assert nc.exact_items(text) == ["android/app/build.gradle", "android/local.properties", "docs/a.md"]


def test_a_near_item_is_restored_in_place() -> None:
    ctx, llm = context()
    llm.add("librarian/summarize_body", SOURCE.replace("consorzio_difesa", "consorzio_difeda")).add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=SOURCE, title="Nota"))

    assert llm.ops == ["librarian/summarize_body", "librarian/summarize_meta"]
    assert "consorzio_difesa.md" in cand.body and "difeda" not in cand.body


def test_an_altered_item_that_cannot_be_restored_is_asked_again_then_the_source_is_kept() -> None:
    ctx, llm = context()
    broken = IT + " Vedi https://other.test/zzz."
    llm.add("librarian/summarize_body", broken, broken).add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=SOURCE, title="Nota"))

    assert llm.ops == ["librarian/summarize_body", "librarian/summarize_body", "librarian/summarize_meta"]  # one retry only
    assert cand.body == SOURCE


def test_a_retry_that_fixes_the_item_is_used() -> None:
    ctx, llm = context()
    broken = IT + " Vedi https://other.test/zzz."
    llm.add("librarian/summarize_body", broken, SOURCE).add("librarian/summarize_meta", META)

    assert classic.summarize(ctx, Source(text=SOURCE, title="Nota")).body == SOURCE


def test_language_and_item_problems_share_one_retry() -> None:
    ctx, llm = context()
    llm.add("librarian/summarize_body", ES, IT + " Vedi https://other.test/zzz.").add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=SOURCE, title="Nota"))

    assert llm.ops.count("librarian/summarize_body") == 2 and cand.body == SOURCE


@pytest.mark.parametrize(
    "reply",
    [
        "<note>\nBody text.\n</note>",
        "<source>Body text.</source>",
        "```markdown\nBody text.\n```",
        "```markdown\nBody text.",
        "<note>\n```md\nBody text.\n```\n</note>",
    ],
)
def test_wrapper_tags_and_fences_are_stripped(reply: str) -> None:
    assert classic._plain_body(reply) == "Body text."


def test_a_repeated_section_is_removed_unless_the_source_repeats_it() -> None:
    body = "# A\n\none\n\n## B\n\ntwo\n\n## B\n\ntwo\n\n## C\n\nthree"
    assert nc.without_repeated_sections(body, "no repeats") == "# A\n\none\n\n## B\n\ntwo\n\n## C\n\nthree"
    assert nc.without_repeated_sections(body, "## B\ntwo\n## B\ntwo") == body


# -- summary --------------------------------------------------------------------------------


def test_a_summary_in_another_language_gives_way_to_the_first_sentence_of_the_body() -> None:
    ctx, llm = context()
    llm.add("librarian/summarize_body", "# Contratto\n\nIl contratto è firmato dalla società. Altro testo.")
    llm.add("librarian/summarize_meta", {**META, "summary": ES})

    cand = classic.summarize(ctx, Source(text=IT, title="Nota"))

    assert cand.summary == "Il contratto è firmato dalla società."


def test_a_short_summary_in_another_language_gives_way_to_the_first_sentence() -> None:
    ctx, llm = context()
    llm.add("librarian/summarize_body", "Il contratto è firmato dalla società. Altro testo.")
    llm.add("librarian/summarize_meta", {**META, "summary": "The contract is signed by the company."})

    assert classic.summarize(ctx, Source(text=IT, title="Nota")).summary == "Il contratto è firmato dalla società."


def test_a_short_summary_in_the_source_language_or_undecidable_is_kept() -> None:
    for summary in ("Il contratto è firmato dalla società.", "Contratto Acme 2024"):
        ctx, llm = context()
        llm.add("librarian/summarize_body", "Il contratto è firmato. Altro.").add("librarian/summarize_meta", {**META, "summary": summary})
        assert classic.summarize(ctx, Source(text=IT, title="Nota")).summary == summary
    assert nc.short_language("The contract is signed by the company.") == "English"
    assert nc.short_language("Contratto Acme 2024") is None


def test_a_note_with_nothing_to_check_makes_the_same_calls() -> None:
    ctx, llm = context()
    llm.add("librarian/summarize_body", "Il contratto.").add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=IT, title="Nota"))

    assert llm.ops == ["librarian/summarize_body", "librarian/summarize_meta"]
    assert (cand.title, cand.summary, cand.body) == ("Contratto", "Cosa dice il contratto.", "Il contratto.")


def test_echoes_finds_the_request_copied_into_the_note() -> None:
    request = "The previous version of the merged note dropped these values of the existing note; keep each of them"
    leaked = "- Project Overview: 2026-10-19? Wait need to map. The prompt mentions the previous version of the merged note dropped"

    assert nc.echoes(leaked, request, "Project Overview: 2026-10-19")
    assert not nc.echoes("- Project Overview: 2026-10-19, previously 2026-10-12", request, "Project Overview: 2026-10-19")
    assert not nc.echoes("keep each of them in the existing note", request, "please keep each of them in the existing note")


def test_a_retry_and_a_fallback_to_the_source_are_decisions_of_the_trace() -> None:
    ctx, llm = context()
    broken = IT + " Vedi https://other.test/zzz."
    llm.add("librarian/summarize_body", broken, broken).add("librarian/summarize_meta", META)

    classic.summarize(ctx, Source(text=SOURCE, title="Nota"))

    steps = [(d.step, d.fallback) for d in ctx.decisions]
    assert steps == [("note_retry", False), ("note_source", True)]


def test_a_retry_that_fixes_the_body_is_one_decision_and_no_fallback() -> None:
    ctx, llm = context()
    llm.add("librarian/summarize_body", IT + " Vedi https://other.test/zzz.", SOURCE).add("librarian/summarize_meta", META)

    classic.summarize(ctx, Source(text=SOURCE, title="Nota"))

    assert [d.step for d in ctx.decisions] == ["note_retry"]


def test_a_note_with_no_problem_leaves_no_decision() -> None:
    ctx, llm = context()
    llm.add("librarian/summarize_body", SOURCE).add("librarian/summarize_meta", META)

    classic.summarize(ctx, Source(text=SOURCE, title="Nota"))

    assert ctx.decisions == []


# -- quotations ------------------------------------------------------------------------------

QUOTED = 'Il sindaco ha detto: "Il ponte sarà riaperto entro maggio, come promesso." La giunta ha approvato.'


def test_a_quotation_the_source_does_not_have_is_found() -> None:
    assert nc.misquoted('Il sindaco: "Il ponte resterà chiuso fino a giugno." Fine.', QUOTED) == ["Il ponte resterà chiuso fino a giugno."]
    assert nc.misquoted("Disse \u201cil ponte sarà riaperto entro maggio\u201d.", QUOTED) == []  # other marks, same words
    assert nc.misquoted('Si legge "Il ponte, sarà riaperto entro maggio!" nel verbale.', QUOTED) == []  # punctuation does not count
    assert nc.misquoted('Detto "a maggio" e basta.', QUOTED) == []  # under four words: not judged


def test_inch_marks_and_stray_quotes_are_not_quotations() -> None:
    assert nc.misquoted('Lo schermo da 15" pesa poco; il modello da 17" costa di più e non ha la porta.', QUOTED) == []


def test_a_made_up_quotation_is_asked_again_then_the_source_is_kept() -> None:
    ctx, llm = context()
    made_up = 'Il sindaco ha detto: "Il ponte resterà chiuso fino a giugno." La giunta ha approvato.'
    llm.add("librarian/summarize_body", made_up, made_up).add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=QUOTED, title="Nota"))

    assert llm.ops == ["librarian/summarize_body", "librarian/summarize_body", "librarian/summarize_meta"]
    assert cand.body == QUOTED


# -- first sentence --------------------------------------------------------------------------


def test_the_first_sentence_is_taken_from_prose_not_from_a_list_or_code() -> None:
    body = "# Cheatsheet\n\n## Commands\n\n- Run locally: `gitlab-runner exec`\n\n```sh\nmake\n```\n\nThe commands we use most. More."
    assert nc.first_sentence(body) == "The commands we use most."
    assert nc.first_sentence("# T\n\n- Only a list item. Second.\n| a | b |") == "Only a list item."
    assert nc.first_sentence("Plain first line. Second.") == "Plain first line."
    assert nc.first_sentence("# T" + chr(10) + "----" + chr(10) + "Il testo vero. Altro.") == "Il testo vero."
