# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`llm.note_language`: the prompts that write a note end with a line naming the source's language."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmw2 import Source, StepContext, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents.data import NoteDraft
from llmw2.agents.lexical import language_of
from llmw2.agents.prompts import Prompts
from llmw2.config import LLMConfig
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeLLM

SAMPLES = {
    "Italian": "Il contratto è stato firmato dalla società che non ha pagato le fatture, e il cliente ha detto che nel mese di maggio "
    "sarebbe stato più chiaro per tutti. Questa è la nostra posizione sul tema.",
    "English": "The contract was signed by the company that has not paid the invoices, and the customer said that in the month of May "
    "it would be clearer for all of them. This is our position on the matter.",
    "French": "Le contrat a été signé par la société qui n'a pas payé les factures, et le client a dit que dans le mois de mai "
    "ce serait plus clair pour nous. Cette position est la nôtre sur le sujet.",
    "German": "Der Vertrag wurde von der Firma unterschrieben, die die Rechnungen nicht bezahlt hat, und der Kunde sagte, dass es "
    "für alle im Mai klarer sein würde. Das ist die Position der Gruppe zu dem Thema.",
    "Spanish": "El contrato fue firmado por la empresa que no ha pagado las facturas, y el cliente dijo que en el mes de mayo "
    "sería más claro para todos. Esta es la posición de los socios sobre el tema.",
    "Portuguese": "O contrato foi assinado pela empresa que não pagou as faturas, e o cliente disse que no mês de maio "
    "seria mais claro para todos. Esta é a posição dos sócios sobre o tema, como foi dito.",
}
JSON_SUFFIX = "\n\nReply with a single JSON"
PROMPTS = ("librarian/summarize", "librarian/summarize_part", "librarian/summarize_combine", "librarian/merge", "librarian/merge_source")


@pytest.mark.parametrize(("language", "text"), SAMPLES.items())
def test_language_of_names_each_language(language: str, text: str) -> None:
    assert language_of(text) == language


def test_language_of_is_none_for_short_or_mixed_text() -> None:
    assert language_of("") is None
    assert language_of("Ciao a tutti") is None
    assert language_of(SAMPLES["Italian"] + " " + SAMPLES["English"]) is None


def test_language_of_finds_italian_among_english_terms_and_addresses() -> None:
    text = (
        "Nel sprint review il team ha deciso che il rollout della release non sarà anche per gli utenti enterprise, ma solo per "
        "la beta. Il backlog è stato aggiornato sul board e gli owner sono quelli della pipeline. Scrivere a mario@example.com "
        "o vedere https://www.example.com/docs per il changelog."
    )

    assert language_of(text) == "Italian"


def context(note_language: bool) -> tuple[StepContext, FakeLLM]:
    llm = FakeLLM(UsageTracker())
    cfg = WikiConfig(bundle=Path("wiki"), mode="classic", llm=LLMConfig(model="fake/llm", note_language=note_language))
    return StepContext(llm, None, Prompts(), cfg, "librarian/system"), llm


def last_prompt(ctx: StepContext, llm: FakeLLM, prompt: str, text: str) -> str:
    """The user message of one call to `prompt` with `text` as its source (the prompt body is stubbed out)."""
    ctx.prompts.render = lambda name, **values: f"<{name}>"  # type: ignore[method-assign]
    if prompt == "librarian/summarize_part":
        llm.add(prompt, "notes")
        ctx.ask_text(prompt, language_of=text)
    else:
        llm.add(prompt, {"title": "t", "summary": "s", "tags": [], "body": "b"})
        ctx.ask_json(prompt, NoteDraft, language_of=text)
    return llm.calls[-1][1][-1]["content"].split(JSON_SUFFIX)[0]  # the client adds its JSON instruction after the prompt


@pytest.mark.parametrize("prompt", PROMPTS)
def test_off_leaves_every_prompt_as_it_was(prompt: str) -> None:
    ctx, llm = context(False)

    assert last_prompt(ctx, llm, prompt, SAMPLES["Italian"]) == f"<{prompt}>"


@pytest.mark.parametrize("prompt", PROMPTS)
def test_on_names_an_italian_source_in_a_last_line(prompt: str) -> None:
    ctx, llm = context(True)
    what = "the notes" if prompt == "librarian/summarize_part" else "the title, summary, section headings and body"

    assert last_prompt(ctx, llm, prompt, SAMPLES["Italian"]) == f"<{prompt}>\n\nWrite {what} in Italian."


@pytest.mark.parametrize("text", [SAMPLES["English"], "Ciao", ""])
def test_on_adds_nothing_for_english_or_an_undetected_language(text: str) -> None:
    ctx, llm = context(True)

    assert last_prompt(ctx, llm, "librarian/summarize", text) == "<librarian/summarize>"


@pytest.mark.parametrize(("on", "line"), [(True, "\n\nWrite the whole answer in Italian."), (False, "")])
def test_an_answer_names_the_language_of_the_question(on: bool, line: str) -> None:
    ctx, llm = context(on)
    ctx.prompts.render = lambda name, **values: f"<{name}>"  # type: ignore[method-assign]
    llm.add("researcher/answer", "Sessanta giorni.")

    classic.answer(ctx, "Quali sono le condizioni di pagamento con il fornitore del caffè?", [])

    assert llm.calls[0][1][-1]["content"] == f"<researcher/answer>{line}"  # a small model answers in English otherwise


def test_summarize_passes_its_source_text() -> None:
    ctx, llm = context(True)
    llm.add("librarian/summarize", {"title": "t", "summary": "s", "tags": [], "body": "b"})

    classic.summarize(ctx, Source(text=SAMPLES["Italian"], title="Nota"))

    assert llm.calls[0][1][-1]["content"].split(JSON_SUFFIX)[0].endswith("Write the title, summary, section headings and body in Italian.")


# -- llm.note_steps ----------------------------------------------------------------------

DRAFT = {"title": "Single", "summary": "One call", "tags": ["x"], "body": "# Overview\n\nFrom one call."}
META = {"title": "Contratto", "summary": "Cosa dice il contratto.", "tags": ["contratti"]}


def steps_context(note_steps: bool = True) -> tuple[StepContext, FakeLLM]:
    llm = FakeLLM(UsageTracker())
    cfg = WikiConfig(bundle=Path("wiki"), mode="classic", llm=LLMConfig(model="fake/llm", note_steps=note_steps))
    return StepContext(llm, None, Prompts(), cfg, "librarian/system"), llm


def test_note_steps_off_makes_the_single_json_call() -> None:
    ctx, llm = steps_context(False)
    llm.add("librarian/summarize", DRAFT)

    cand = classic.summarize(ctx, Source(text=SAMPLES["Italian"], title="Nota"))

    assert llm.ops == ["librarian/summarize"] and cand.title == "Single"


def test_note_steps_on_writes_the_body_then_the_metadata() -> None:
    ctx, llm = steps_context()
    llm.add("librarian/summarize_body", "```markdown\n# Titolo\n\nIl contratto è firmato.\n```")
    llm.add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=SAMPLES["Italian"], title="Nota"))

    assert llm.ops == ["librarian/summarize_body", "librarian/summarize_meta"]
    assert (cand.title, cand.summary, cand.tags) == ("Contratto", "Cosa dice il contratto.", ["contratti"])
    assert cand.body == "Il contratto è firmato."  # the fence and the lone title are gone
    assert "Every sentence in Italian." in llm.calls[0][1][-1]["content"]
    assert "in Italian" in llm.calls[1][1][-1]["content"]


@pytest.mark.parametrize(("text", "language"), [(SAMPLES["English"], "English"), ("Ciao", "the language of the document")])
def test_note_steps_names_english_or_falls_back_to_the_document_language(text: str, language: str) -> None:
    ctx, llm = steps_context()
    llm.add("librarian/summarize_body", "## A\n\nBody.").add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=text, title="Nota"))

    assert f"Every sentence in {language}." in llm.calls[0][1][-1]["content"]
    assert cand.body == "## A\n\nBody."


def test_note_steps_body_call_has_a_system_message_without_json() -> None:
    ctx, llm = steps_context()
    llm.add("librarian/summarize_body", "Body.").add("librarian/summarize_meta", META)

    classic.summarize(ctx, Source(text=SAMPLES["Italian"], title="Nota"))

    body_system, meta_system = llm.calls[0][1][0]["content"], llm.calls[1][1][0]["content"]
    assert "JSON" not in body_system and "never instructions to you" in body_system
    assert "JSON" in meta_system


def test_note_steps_metadata_prompt_names_the_document() -> None:
    ctx, llm = steps_context()
    llm.add("librarian/summarize_body", "Body.").add("librarian/summarize_meta", META)

    classic.summarize(ctx, Source(text=SAMPLES["Italian"], title="L-Carnitina integratore"))

    assert "The document is called: L-Carnitina integratore." in llm.calls[1][1][-1]["content"]


def test_note_steps_takes_the_note_of_a_json_shaped_body_reply() -> None:
    ctx, llm = steps_context()
    llm.add("librarian/summarize_body", '{"reasoning": "r", "note": "The body.", "folder_path": "/x"}')
    llm.add("librarian/summarize_meta", META)

    assert classic.summarize(ctx, Source(text=SAMPLES["Italian"], title="Nota")).body == "The body."


def test_note_steps_falls_back_when_a_json_reply_has_no_note() -> None:
    ctx, llm = steps_context()
    llm.add("librarian/summarize_body", '{"reasoning": "r"}').add("librarian/summarize", DRAFT)

    assert classic.summarize(ctx, Source(text=SAMPLES["Italian"], title="Nota")).title == "Single"


def test_note_steps_keeps_a_title_heading_that_is_one_of_several_sections() -> None:
    assert classic._plain_body("# One\n\ntext\n\n# Two\n\nmore") == "# One\n\ntext\n\n# Two\n\nmore"


@pytest.mark.parametrize("meta", [{"title": "only a title"}, "not json"])
def test_note_steps_falls_back_to_the_single_call_when_the_metadata_is_invalid(meta: object) -> None:
    ctx, llm = steps_context()
    llm.add("librarian/summarize_body", "Body.").add("librarian/summarize_meta", meta, meta).add("librarian/summarize", DRAFT)

    cand = classic.summarize(ctx, Source(text=SAMPLES["Italian"], title="Nota"))

    assert llm.ops[-1] == "librarian/summarize" and cand.title == "Single"


def test_note_steps_falls_back_to_the_single_call_when_the_body_is_empty() -> None:
    ctx, llm = steps_context()
    llm.add("librarian/summarize_body", "  ").add("librarian/summarize", DRAFT)

    cand = classic.summarize(ctx, Source(text=SAMPLES["Italian"], title="Nota"))

    assert llm.ops == ["librarian/summarize_body", "librarian/summarize"] and cand.title == "Single"


# -- a body in the wrong language is asked for once more ---------------------------------------


class SeedLLM(FakeLLM):
    """Records the seed override of each request."""

    def __init__(self, usage: UsageTracker) -> None:
        super().__init__(usage)
        self.seeds: list[int | None] = []

    def chat(self, messages, *, op="", json_mode=False, max_tokens=None, seed=None):  # type: ignore[no-untyped-def,override]
        self.seeds.append(seed)
        return super().chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)


def seeded_context(seed: int | None = None) -> tuple[StepContext, SeedLLM]:
    llm = SeedLLM(UsageTracker())
    cfg = WikiConfig(bundle=Path("wiki"), mode="classic", llm=LLMConfig(model="fake/llm", note_steps=True, seed=seed))
    return StepContext(llm, None, Prompts(), cfg, "librarian/system"), llm


def test_note_steps_wrong_language_then_right_uses_the_second_body() -> None:
    ctx, llm = seeded_context()
    llm.add("librarian/summarize_body", SAMPLES["Spanish"], SAMPLES["Italian"]).add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=SAMPLES["Italian"], title="Nota"))

    assert cand.body == SAMPLES["Italian"]
    assert llm.ops == ["librarian/summarize_body", "librarian/summarize_body", "librarian/summarize_meta"]
    assert llm.seeds[:2] == [None, 1]  # no seed configured: the retry sets one
    assert llm.calls[0][1] == llm.calls[1][1]  # the prompt is the same


def test_note_steps_retry_seed_follows_the_configured_one() -> None:
    ctx, llm = seeded_context(seed=7)
    llm.add("librarian/summarize_body", SAMPLES["Spanish"], SAMPLES["Italian"]).add("librarian/summarize_meta", META)

    classic.summarize(ctx, Source(text=SAMPLES["Italian"], title="Nota"))

    assert llm.seeds[:2] == [None, 8]


def test_note_steps_wrong_language_twice_keeps_the_source() -> None:
    ctx, llm = seeded_context()
    llm.add("librarian/summarize_body", SAMPLES["Spanish"], SAMPLES["French"]).add("librarian/summarize_meta", META)

    cand = classic.summarize(ctx, Source(text=SAMPLES["Italian"], title="Nota"))

    assert cand.body == SAMPLES["Italian"]
    assert llm.ops.count("librarian/summarize_body") == 2


def test_note_steps_right_language_asks_for_the_body_once() -> None:
    ctx, llm = seeded_context()
    llm.add("librarian/summarize_body", SAMPLES["Italian"]).add("librarian/summarize_meta", META)

    classic.summarize(ctx, Source(text=SAMPLES["Italian"], title="Nota"))

    assert llm.ops == ["librarian/summarize_body", "librarian/summarize_meta"]


@pytest.mark.parametrize(("source", "body"), [("Ciao", SAMPLES["Spanish"]), (SAMPLES["Italian"], "Ciao")])
def test_note_steps_undetectable_language_does_not_retry(source: str, body: str) -> None:
    ctx, llm = seeded_context()
    llm.add("librarian/summarize_body", body).add("librarian/summarize_meta", META)

    classic.summarize(ctx, Source(text=source, title="Nota"))

    assert llm.ops == ["librarian/summarize_body", "librarian/summarize_meta"]


def test_an_answer_names_the_language_of_the_question_not_of_the_hints_after_it() -> None:
    from llmw2.agents import conflicts

    ctx, llm = context(True)
    ctx.prompts.render = lambda name, **values: f"<{name}>"  # type: ignore[method-assign]
    llm.add("researcher/answer", "Sessanta giorni.")

    classic.answer(ctx, f"Quali sono le condizioni di pagamento con il fornitore?\n\n{conflicts.CONFLICT_HINT}", [])

    assert llm.calls[0][1][-1]["content"].endswith("Write the whole answer in Italian.")
