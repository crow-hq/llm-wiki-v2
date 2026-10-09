# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from spec-conflict-hint-2026-10-09.md: the CROW answer gets CONFLICT_HINT when the notes read may disagree."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents.conflicts import CONFLICT, CONFLICT_HINT, CONFLICT_MIN, MAX_NOTES, PASSAGE_CHARS, conflict_state
from llmw2.agents.revisions import HISTORY_HINT
from llmw2.bundle.tree import Note
from llmw2.errors import ModelError
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_researcher_crow import ANSWER, add_note, make_wiki
from tests.wiki.test_researcher_crow import answer_prompt as crow_answer_prompt
from tests.wiki.test_revisions_replace import HISTORY_OP, NOTE_TITLE, QUESTION, answer_prompt, priced_wiki, retrieval

CONFLICT_OP = "ask/conflict"
ASKED = "How is revenue booked?"


def make_note(title: str, body: str) -> Note:
    return Note(path=Path(f"{title}.md"), rel=f"{title}.md", frontmatter={"title": title}, body=body)


def down(text: str) -> float:
    raise ModelError("down")


def constant(p: float) -> Callable[[str], float]:
    return lambda text: p


def two_notes(classifier: FakeClassifier, check: Callable[[str], float]) -> None:
    """Retrieval picks Overview and Revenue policy; the conflict check answers with `check`."""
    classifier.noul_scores["retrieve_folder"] = {'folder "finance"': 0.9}
    classifier.noul_scores["retrieve_note"] = {'note titled "Overview"': 0.9, 'note titled "Revenue policy"': 0.9}
    classifier.noul_scores[CONFLICT_OP] = check


# -- constants ----------------------------------------------------------------------------------------------------


def test_constants() -> None:
    assert CONFLICT == "The notes do not all agree on the answer to the question: at least one gives a value that another contradicts."
    assert CONFLICT_MIN == 0.4
    assert CONFLICT_HINT == (
        "(The notes may disagree on this. If two notes give different values for what the question asks, "
        "report every value, say which note gives which and which source is more recent, and do not pick one silently.)"
    )
    assert PASSAGE_CHARS == 900
    assert MAX_NOTES == 8


# -- conflict_state -----------------------------------------------------------------------------------------------


def test_state_format() -> None:
    notes = [make_note("Alpha", "Revenue is booked monthly."), make_note("Beta", "Revenue is booked weekly.")]

    state = conflict_state("How is revenue booked?", notes)

    assert (
        state
        == "Question: How is revenue booked?\n\n### Note: Alpha\nRevenue is booked monthly.\n\n### Note: Beta\nRevenue is booked weekly."
    )


def test_state_picks_the_paragraph_sharing_most_words() -> None:
    body = "Intro about nothing.\n\nThe widget price is 120 per unit.\n\nThe price of shipping is low."

    state = conflict_state("What is the widget price per unit?", [make_note("N", body)])

    assert state.endswith("### Note: N\nThe widget price is 120 per unit.")


def test_state_counts_only_words_of_three_or_more_chars_in_lowercase() -> None:
    # "is" and "of" are shorter than 3 characters and do not count; matching is lowercase
    body = "Is of is of is of.\n\nRevenue BOOKED here."

    state = conflict_state("Is it of revenue booked", [make_note("N", body)])

    assert state.endswith("### Note: N\nRevenue BOOKED here.")


def test_state_ties_go_to_the_first_paragraph() -> None:
    body = "First says price.\n\nSecond says price.\n\nThird says price."

    state = conflict_state("price?", [make_note("N", body)])

    assert state.endswith("### Note: N\nFirst says price.")


def test_state_with_no_shared_word_takes_the_first_paragraph() -> None:
    state = conflict_state("zzz qqq", [make_note("N", "First one.\n\nSecond one.")])

    assert state.endswith("### Note: N\nFirst one.")


def test_state_drops_empty_paragraphs() -> None:
    state = conflict_state("price", [make_note("N", "\n\n\n\nThe price is 5.\n\n\n\n")])

    assert state.endswith("### Note: N\nThe price is 5.")


def test_state_cuts_the_passage_at_passage_chars() -> None:
    long = "price " * 400

    state = conflict_state("price", [make_note("N", long)])

    passage = state.split("### Note: N\n")[1]
    assert len(passage) == PASSAGE_CHARS
    assert passage == long[:PASSAGE_CHARS]


def test_state_keeps_a_short_passage_whole() -> None:
    body = "x" * (PASSAGE_CHARS - 10)

    assert conflict_state("q", [make_note("N", body)]).endswith(body)


def test_state_has_at_most_max_notes_notes() -> None:
    notes = [make_note(f"Note{i}", f"Body {i}.") for i in range(MAX_NOTES + 3)]

    state = conflict_state("body", notes)

    assert state.count("### Note: ") == MAX_NOTES
    assert f"### Note: Note{MAX_NOTES - 1}\n" in state
    assert f"Note{MAX_NOTES}" not in state


def test_state_note_without_paragraph_has_an_empty_passage() -> None:
    state = conflict_state("price", [make_note("Empty", ""), make_note("Full", "The price.")])

    assert state == "Question: price\n\n### Note: Empty\n\n\n### Note: Full\nThe price."


def test_state_ignores_the_see_also_section() -> None:
    body = "Revenue is booked monthly.\n\n## See also\n\n- [Other](other.md)\n"

    state = conflict_state("see also other", [make_note("N", body)])

    assert "other.md" not in state


# -- the Researcher, CROW -----------------------------------------------------------------------------------------


def test_hint_follows_the_question_when_the_notes_may_disagree(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    two_notes(classifier, constant(0.9))
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(ASKED)

    prompt = crow_answer_prompt(llm)
    assert len(answer.notes) == 2
    assert f"{ASKED}\n\n{CONFLICT_HINT}" in prompt
    assert prompt.count(CONFLICT_HINT) == 1
    assert prompt.index(ASKED) < prompt.index(CONFLICT_HINT)


def test_one_call_with_the_op_the_statement_and_the_state(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    two_notes(classifier, constant(0.9))
    llm.add(ANSWER, "Answer.")

    wiki.ask(ASKED)

    calls = [c for c in classifier.calls if c[0] == CONFLICT_OP]
    assert len(calls) == 1
    _, state, questions = calls[0]
    assert state.startswith(f"Question: {ASKED}\n\n### Note: ")
    assert state.count("### Note: ") == 2
    assert [q["instructions"] for q in questions.values()] == [CONFLICT]


def test_hint_present_just_above_conflict_min(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    two_notes(classifier, constant(CONFLICT_MIN + 0.01))
    llm.add(ANSWER, "Answer.")

    wiki.ask(ASKED)

    assert CONFLICT_HINT in crow_answer_prompt(llm)


@pytest.mark.parametrize("p", [CONFLICT_MIN, 0.3, 0.0])
def test_hint_absent_when_not_above_conflict_min(bundle: Path, llm: FakeLLM, classifier: FakeClassifier, p: float) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    two_notes(classifier, constant(p))
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(ASKED)

    prompt = crow_answer_prompt(llm)
    assert CONFLICT_HINT not in prompt
    assert ASKED in prompt
    decision = next(d for d in answer.decisions if d.step == "conflict")
    assert decision.choice == "none"
    assert decision.confidence == p


def test_conflict_decision_is_recorded_with_the_probability(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    two_notes(classifier, constant(0.8))
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(ASKED)

    decisions = [d for d in answer.decisions if d.step == "conflict"]
    assert len(decisions) == 1
    assert decisions[0].decider == "classifier"
    assert decisions[0].choice == "hint"
    assert decisions[0].confidence == 0.8
    assert not decisions[0].fallback


def test_hint_absent_with_one_note_read(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    classifier.noul_scores["retrieve_note"] = {'note titled "Overview"': 0.9}
    classifier.noul_scores[CONFLICT_OP] = constant(0.9)
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(ASKED)

    assert answer.notes == ["overview.md"]
    assert CONFLICT_OP not in classifier.ops
    assert CONFLICT_HINT not in crow_answer_prompt(llm)
    assert not any(d.step == "conflict" for d in answer.decisions)


def test_hint_absent_when_the_classifier_fails(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    two_notes(classifier, down)
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(ASKED)

    prompt = crow_answer_prompt(llm)
    assert CONFLICT_HINT not in prompt
    assert ASKED in prompt
    decision = next(d for d in answer.decisions if d.step == "conflict")
    assert decision.decider == "classifier"
    assert decision.choice == "error"
    assert decision.fallback


def test_the_question_is_unchanged_outside_the_answer_step(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    two_notes(classifier, constant(0.9))
    llm.add(ANSWER, "Answer.")

    wiki.ask(ASKED)

    assert all(CONFLICT_HINT not in state for op, state, _ in classifier.calls if op.startswith("retrieve"))
    assert all(CONFLICT_HINT not in m["content"] for op, msgs in llm.calls if op != ANSWER for m in msgs)


def test_hint_goes_after_the_history_hint_when_both_apply(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki, archived = priced_wiki(bundle, llm, classifier)
    retrieval(classifier, constant(0.9))
    classifier.noul_scores["retrieve_note"] = {f'note titled "{NOTE_TITLE}"': 0.9, 'note titled "Revenue policy"': 0.9}
    classifier.noul_scores[CONFLICT_OP] = constant(0.9)
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert archived.lstrip("/") in answer.notes
    assert HISTORY_OP in classifier.ops
    assert f"{QUESTION}\n\n{HISTORY_HINT}\n\n{CONFLICT_HINT}" in answer_prompt(llm)


def test_history_hint_alone_when_there_is_no_conflict(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki, _ = priced_wiki(bundle, llm, classifier)
    retrieval(classifier, constant(0.9))
    classifier.noul_scores["retrieve_note"] = {f'note titled "{NOTE_TITLE}"': 0.9, 'note titled "Revenue policy"': 0.9}
    classifier.noul_scores[CONFLICT_OP] = constant(0.1)
    llm.add(ANSWER, "Answer.")

    wiki.ask(QUESTION)

    prompt = answer_prompt(llm)
    assert f"{QUESTION}\n\n{HISTORY_HINT}" in prompt
    assert CONFLICT_HINT not in prompt


# -- classic ------------------------------------------------------------------------------------------------------


def test_classic_mode_makes_no_call_and_adds_no_hint(bundle: Path, llm: FakeLLM) -> None:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm)
    wiki.init()
    top, _ = wiki.store.create_folder(wiki.store.load(), "finance", "Everything about finance.")
    add_note(wiki, top, "Revenue policy", "Revenue is booked monthly.")
    add_note(wiki, top, "Revenue rules", "Revenue is booked weekly.")
    llm.add(
        "researcher/navigate",
        {"open": ["finance"]},
        {"select": ["finance/revenue-policy.md", "finance/revenue-rules.md"], "done": True},
    ).add(ANSWER, "Answer.")

    answer = wiki.ask(ASKED)

    assert len(answer.notes) == 2
    prompt = crow_answer_prompt(llm)
    assert CONFLICT_HINT not in prompt
    assert ASKED in prompt
    assert not any(d.step == "conflict" for d in answer.decisions)
