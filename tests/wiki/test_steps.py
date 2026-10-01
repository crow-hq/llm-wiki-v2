# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Customizable steps: every step of the ingest and ask flows is a replaceable function the core checks, then trusts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from llmw2 import (
    CLASSIC,
    CROW,
    Candidate,
    NoteDraft,
    Route,
    Source,
    StepContext,
    StepError,
    Steps,
    Wiki,
    WikiConfig,
    WikiError,
)
from llmw2.agents.base import Decision
from llmw2.agents.researcher import NO_ANSWER
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.tree import Folder, Note
from llmw2.models.usage import UsageReport
from tests.wiki.fakes import LLM_TOKENS, FakeClassifier, FakeLLM

SEED = "finance/seed.md"


# -- helpers ----------------------------------------------------------------------


def read(path: Path) -> OKFDocument:
    return OKFDocument.parse(path.read_text(encoding="utf-8"))


def raws(bundle: Path) -> list[Path]:
    return sorted((bundle / "raw").glob("*.md"))


def snapshot(bundle: Path) -> dict[str, bytes]:
    """Every file of the bundle, to prove a failed operation wrote nothing."""
    return {str(p.relative_to(bundle)): p.read_bytes() for p in sorted(bundle.rglob("*")) if p.is_file()}


def finance(root: Folder) -> Folder:
    folder = root.find("/finance")
    assert folder is not None
    return folder


# A step set that never calls a model: every step is a few lines of plain code, so a test swaps one and watches it drive the flow.


def o_summarize(ctx: StepContext, source: Source) -> Candidate:
    return Candidate(source.title or "Memo", "A memo.", [], source.text)


def o_route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
    return Route(finance(root), False, [])


def o_name_folder(ctx: StepContext, parent: Folder, cand: Candidate) -> tuple[str, str]:
    return "fresh", "Fresh things."


def o_match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
    return None


def o_consolidate(ctx: StepContext, note: Note, cand: Candidate) -> bool:
    return False


def o_merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
    return NoteDraft(title=note.title, summary=note.summary, body=source.text)


def o_relate(ctx: StepContext, note: Note, others: list[Note]) -> list[Note]:
    return []


def o_select(ctx: StepContext, root: Folder, question: str) -> list[Note]:
    return root.all_notes()


def o_answer(ctx: StepContext, question: str, notes: list[Note]) -> str:
    return "Answer."


OFFLINE = Steps(
    name="offline",
    summarize=o_summarize,
    route=o_route,
    name_folder=o_name_folder,
    match=o_match,
    consolidate=o_consolidate,
    merge=o_merge,
    relate=o_relate,
    select=o_select,
    answer=o_answer,
)


def pick_seed(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
    return next(n for n in pool if n.rel == SEED)


def always(ctx: StepContext, note: Note, cand: Candidate) -> bool:
    return True


MERGING = replace(OFFLINE, match=pick_seed, consolidate=always)


def build(bundle: Path, llm: FakeLLM, steps: Steps, *, mode: str = "classic", classifier: FakeClassifier | None = None) -> Wiki:
    """A wiki with /finance/seed.md in it, running `steps`."""
    wiki = Wiki(WikiConfig(bundle=bundle, mode=mode), llm=llm, classifier=classifier, steps=steps)
    wiki.init()
    folder, _ = wiki.store.create_folder(wiki.store.load().find("/"), "finance", "Money in and out.")
    wiki.store.write_note(
        folder, title="Seed", summary="Seeded.", body="Seed.", tags=[], source={"resource": "/raw/seed.md", "title": "Seed"}
    )
    return wiki


@pytest.fixture
def offline(bundle: Path, llm: FakeLLM) -> Callable[[Steps], Wiki]:
    """Builds a wiki over the (fake, unscripted: a call fails the test) LLM with the given steps."""
    return lambda steps: build(bundle, llm, steps)


# -- one replaced step each: the function is called with the documented arguments and drives the flow -----------------------


def test_extract_is_called_with_the_bytes_and_the_name_and_its_text_is_filed(offline: Callable[[Steps], Wiki], bundle: Path) -> None:
    calls: list[tuple[Any, bytes, str]] = []

    def extract(ctx: StepContext, data: bytes, filename: str) -> str:
        calls.append((ctx, data, filename))
        return "Text decoded by my own reader."

    result = offline(replace(OFFLINE, extract=extract)).ingest_file(b"\x00\x01binary", "blob.bin")

    [(ctx, data, filename)] = calls
    assert isinstance(ctx, StepContext) and (data, filename) == (b"\x00\x01binary", "blob.bin")
    assert "Text decoded by my own reader." in read(raws(bundle)[0]).body
    assert result.action == "created"


def test_summarize_gets_the_source_and_its_candidate_is_what_is_written(offline: Callable[[Steps], Wiki], bundle: Path) -> None:
    calls: list[Source] = []

    def summarize(ctx: StepContext, source: Source) -> Candidate:
        calls.append(source)
        return Candidate("My title", "My summary.", ["tag"], "My body.")

    result = offline(replace(OFFLINE, summarize=summarize)).ingest("Original text.", title="Memo", resource="https://x.test/a")

    [source] = calls
    assert (source.text, source.title, source.resource) == ("Original text.", "Memo", "https://x.test/a")
    fm = read(bundle / result.note).frontmatter
    assert (result.title, fm["title"], fm["description"], fm["tags"]) == ("My title", "My title", "My summary.", ["tag"])
    assert read(bundle / result.note).body.strip() == "My body."


def test_route_gets_the_root_and_the_candidate_and_its_folder_is_where_the_note_goes(
    offline: Callable[[Steps], Wiki], bundle: Path
) -> None:
    calls: list[tuple[Folder, Candidate]] = []

    def route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
        calls.append((root, cand))
        folder = root.find("/legal")
        assert folder is not None
        return Route(folder, False, [])

    wiki = offline(replace(OFFLINE, route=route))
    wiki.create_folder("/", "legal", "Contracts.")  # the route reads the tree loaded at ingest, so it sees it
    result = wiki.ingest("A contract.", title="Contract")

    [(root, cand)] = calls
    assert (root.rel, cand.title) == ("", "Contract")
    assert (result.folder, result.note) == ("/legal", "legal/contract.md")
    assert (bundle / "legal" / "contract.md").is_file()


def test_name_folder_gets_the_parent_and_the_candidate_and_names_the_new_folder(offline: Callable[[Steps], Wiki], bundle: Path) -> None:
    calls: list[tuple[Folder, Candidate]] = []

    def route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
        return Route(root, True, [])

    def name_folder(ctx: StepContext, parent: Folder, cand: Candidate) -> tuple[str, str]:
        calls.append((parent, cand))
        return "Brand New", "Fresh stuff."

    result = offline(replace(OFFLINE, route=route, name_folder=name_folder)).ingest("Something.", title="Memo")

    [(parent, cand)] = calls
    assert (parent.rel, cand.title) == ("", "Memo")
    assert (result.created_folders, result.folder, result.note) == (["/brand-new"], "/brand-new", "brand-new/memo.md")
    assert "* [brand-new](brand-new/index.md) - Fresh stuff." in read(bundle / "index.md").body.splitlines()


def test_match_gets_the_pool_and_the_candidate_and_a_note_of_the_pool_goes_to_consolidate(
    offline: Callable[[Steps], Wiki],
) -> None:
    calls: list[tuple[list[Note], Candidate]] = []
    consolidated: list[Note] = []
    found: list[Note] = []

    def match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
        calls.append((list(pool), cand))
        found.append(pool[0])
        return pool[0]

    def consolidate(ctx: StepContext, note: Note, cand: Candidate) -> bool:
        consolidated.append(note)
        return False

    result = offline(replace(OFFLINE, match=match, consolidate=consolidate)).ingest("Seeded again.", title="Memo")

    [(pool, cand)] = calls
    assert [n.rel for n in pool] == [SEED] and cand.title == "Memo"
    assert consolidated == found and consolidated[0] is found[0]  # the very note the match returned
    assert result.action == "created"  # consolidate said no


def test_match_returning_none_never_reaches_consolidate(offline: Callable[[Steps], Wiki]) -> None:
    def consolidate(ctx: StepContext, note: Note, cand: Candidate) -> bool:
        raise AssertionError("no match: nothing to consolidate")

    result = offline(replace(OFFLINE, consolidate=consolidate)).ingest("Seeded again.")

    assert result.action == "created"


def test_consolidate_true_merges_and_false_writes_a_new_note(offline: Callable[[Steps], Wiki], bundle: Path) -> None:
    calls: list[tuple[Note, Candidate]] = []

    def consolidate(ctx: StepContext, note: Note, cand: Candidate) -> bool:
        calls.append((note, cand))
        return len(calls) == 1  # merge the first, keep the second apart

    wiki = offline(replace(MERGING, consolidate=consolidate))

    first = wiki.ingest("One.", title="First")
    second = wiki.ingest("Two.", title="Second")

    assert [n.rel for n, _ in calls] == [SEED, SEED] and calls[0][1].title == "First"
    assert (first.action, first.note) == ("merged", SEED)
    assert (second.action, second.note) == ("created", "finance/second.md")


def test_merge_gets_the_note_and_the_source_and_its_draft_rewrites_the_note(offline: Callable[[Steps], Wiki], bundle: Path) -> None:
    calls: list[tuple[Note, Source]] = []

    def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        calls.append((note, source))
        return NoteDraft(title="Seed, updated", summary="Updated.", tags=["new"], body="# Overview\n\nMerged by my function.")

    result = offline(replace(MERGING, merge=merge)).ingest("New facts.", title="Memo")

    [(note, source)] = calls
    assert (note.rel, source.text) == (SEED, "New facts.")
    assert (result.action, result.note, result.title) == ("merged", SEED, "Seed, updated")
    doc = read(bundle / SEED)
    assert "Merged by my function." in doc.body and doc.frontmatter["description"] == "Updated."
    assert len(doc.frontmatter["sources"]) == 2


def test_relate_gets_the_new_note_and_the_others_and_links_what_it_returns(offline: Callable[[Steps], Wiki], bundle: Path) -> None:
    calls: list[tuple[Note, list[Note]]] = []

    def relate(ctx: StepContext, note: Note, others: list[Note]) -> list[Note]:
        calls.append((note, list(others)))
        return others

    result = offline(replace(OFFLINE, relate=relate)).ingest("Pricing.", title="Memo")

    [(note, others)] = calls
    assert note.rel == result.note == "finance/memo.md"
    assert [n.rel for n in others] == [SEED]  # the notes next to it, never itself
    assert result.related == [SEED]
    assert "[Memo](memo.md)" in read(bundle / SEED).body


def test_select_gets_the_root_and_the_question_and_its_notes_are_read_by_answer(offline: Callable[[Steps], Wiki]) -> None:
    calls: list[tuple[Folder, str]] = []
    read_by_answer: list[list[str]] = []

    def select(ctx: StepContext, root: Folder, question: str) -> list[Note]:
        calls.append((root, question))
        return [n for n in root.all_notes() if n.rel == SEED]

    def answer(ctx: StepContext, question: str, notes: list[Note]) -> str:
        read_by_answer.append([n.rel for n in notes])
        return f"Found [{SEED}]."

    result = offline(replace(OFFLINE, select=select, answer=answer)).ask("What is seeded?")

    [(root, question)] = calls
    assert (root.rel, question) == ("", "What is seeded?")
    assert read_by_answer == [[SEED]]
    assert (result.text, result.notes, result.citations) == (f"Found [{SEED}].", [SEED], [SEED])


def test_no_selected_note_means_no_answer_without_calling_answer(offline: Callable[[Steps], Wiki]) -> None:
    def select(ctx: StepContext, root: Folder, question: str) -> list[Note]:
        return []

    def answer(ctx: StepContext, question: str, notes: list[Note]) -> str:
        raise AssertionError("nothing selected: nothing to answer")

    result = offline(replace(OFFLINE, select=select, answer=answer)).ask("What?")

    assert (result.text, result.notes, result.citations) == (NO_ANSWER, [], [])


def test_answer_gets_the_question_and_the_notes_and_the_core_keeps_only_citations_of_notes_read(
    offline: Callable[[Steps], Wiki],
) -> None:
    calls: list[tuple[str, list[Note]]] = []

    def answer(ctx: StepContext, question: str, notes: list[Note]) -> str:
        calls.append((question, list(notes)))
        return f"It is seeded [{SEED}] and invented [ghost.md]."

    result = offline(replace(OFFLINE, answer=answer)).ask("What is seeded?")

    [(question, notes)] = calls
    assert (question, [n.rel for n in notes]) == ("What is seeded?", [SEED])
    assert result.text == f"It is seeded [{SEED}] and invented [ghost.md]."
    assert result.citations == [SEED]


# -- output checks: StepError naming the step, nothing written ----------------------------------------------------------------

GHOST_NOTE = Note(Path("/nowhere/ghost.md"), "ghost.md", {"title": "Ghost"}, "Ghost.")
GHOST_FOLDER = Folder(Path("/nowhere/ghost"), "ghost")


def bad_candidate(**changes: Any) -> Callable[..., Candidate]:
    fields: dict[str, Any] = {"title": "T", "summary": "S", "tags": [], "body": "B"} | changes
    return lambda ctx, source: Candidate(**fields)


def bad_draft(**changes: Any) -> Callable[..., NoteDraft]:
    fields: dict[str, Any] = {"title": "T", "summary": "S", "tags": [], "body": "B"} | changes
    return lambda ctx, note, source: NoteDraft(**fields)


def foreign_copy(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note:
    """Looks exactly like a note of the pool, but is not the object the core handed over."""
    note = pool[0]
    return Note(note.path, note.rel, dict(note.frontmatter), note.body, note.folder)


# (step, steps with that step broken, how to run). "ingest" files a text, "file" a file, "ask" asks a question.
INGEST_ERRORS = [
    pytest.param("extract", replace(OFFLINE, extract=lambda ctx, data, filename: 5), "file", id="extract-not-a-str"),
    pytest.param("summarize", replace(OFFLINE, summarize=lambda ctx, source: "a title"), "ingest", id="summarize-not-a-candidate"),
    pytest.param("summarize", replace(OFFLINE, summarize=bad_candidate(title="   ")), "ingest", id="summarize-blank-title"),
    pytest.param("summarize", replace(OFFLINE, summarize=bad_candidate(tags="tag")), "ingest", id="summarize-tags-not-a-list"),
    pytest.param("summarize", replace(OFFLINE, summarize=bad_candidate(tags=[1])), "ingest", id="summarize-tags-not-str"),
    pytest.param("summarize", replace(OFFLINE, summarize=bad_candidate(fields=["x"])), "ingest", id="summarize-fields-not-a-dict"),
    pytest.param("summarize", replace(OFFLINE, summarize=bad_candidate(fields={"x": object()})), "ingest", id="summarize-fields-not-yaml"),
    pytest.param("route", replace(OFFLINE, route=lambda ctx, root, cand: "/finance"), "ingest", id="route-not-a-route"),
    pytest.param(
        "route", replace(OFFLINE, route=lambda ctx, root, cand: Route(GHOST_FOLDER, False, [])), "ingest", id="route-foreign-folder"
    ),
    pytest.param(
        "route",
        replace(OFFLINE, route=lambda ctx, root, cand: Route(finance(root), False, [GHOST_FOLDER])),
        "ingest",
        id="route-foreign-candidate",
    ),
    pytest.param(
        "name_folder",
        replace(OFFLINE, route=lambda ctx, root, cand: Route(root, True, []), name_folder=lambda ctx, p, c: "fresh"),
        "ingest",
        id="name_folder-not-a-tuple",
    ),
    pytest.param(
        "name_folder",
        replace(OFFLINE, route=lambda ctx, root, cand: Route(root, True, []), name_folder=lambda ctx, p, c: ("a", "b", "c")),
        "ingest",
        id="name_folder-three-items",
    ),
    pytest.param(
        "name_folder",
        replace(OFFLINE, route=lambda ctx, root, cand: Route(root, True, []), name_folder=lambda ctx, p, c: (1, "d")),
        "ingest",
        id="name_folder-not-str",
    ),
    pytest.param(
        "name_folder",
        replace(OFFLINE, route=lambda ctx, root, cand: Route(root, True, []), name_folder=lambda ctx, p, c: ("!!!", "d")),
        "ingest",
        id="name_folder-empty-slug",
    ),
    pytest.param("match", replace(OFFLINE, match=lambda ctx, pool, cand: "finance/seed.md"), "ingest", id="match-a-str"),
    pytest.param("match", replace(OFFLINE, match=lambda ctx, pool, cand: GHOST_NOTE), "ingest", id="match-note-not-in-pool"),
    pytest.param("match", replace(OFFLINE, match=foreign_copy), "ingest", id="match-copy-of-a-pool-note"),
    pytest.param("consolidate", replace(MERGING, consolidate=lambda ctx, note, cand: "yes"), "ingest", id="consolidate-not-a-bool"),
    pytest.param("merge", replace(MERGING, merge=lambda ctx, note, source: bad_candidate()(ctx, source)), "ingest", id="merge-not-a-draft"),
    pytest.param("merge", replace(MERGING, merge=bad_draft(title=" ")), "ingest", id="merge-blank-title"),
    pytest.param("merge", replace(MERGING, merge=bad_draft(fields={"x": object()})), "ingest", id="merge-fields-not-yaml"),
]


@pytest.mark.parametrize(("step", "steps", "how"), INGEST_ERRORS)
def test_a_step_output_the_next_step_cannot_use_stops_the_ingest_before_anything_is_written(
    offline: Callable[[Steps], Wiki], bundle: Path, step: str, steps: Steps, how: str
) -> None:
    wiki = offline(steps)
    before = snapshot(bundle)

    with pytest.raises(StepError) as raised:
        if how == "file":
            wiki.ingest_file(b"Some text.", "memo.txt")
        else:
            wiki.ingest("Some text.", title="Memo")

    assert raised.value.step == step and f"{step}:" in str(raised.value)
    assert snapshot(bundle) == before  # no note, no raw copy, no index change, no log entry


# relate runs after the note is written: the core cannot take the note back, but it links nothing and logs nothing
RELATE_ERRORS = [
    pytest.param(lambda ctx, note, others: "x", id="not-a-list"),
    pytest.param(lambda ctx, note, others: [note], id="the-note-itself"),
    pytest.param(lambda ctx, note, others: [GHOST_NOTE], id="note-not-in-others"),
    pytest.param(lambda ctx, note, others: ["finance/seed.md"], id="not-notes"),
]


@pytest.mark.parametrize("relate", RELATE_ERRORS)
def test_a_relate_output_that_is_not_a_list_of_the_others_is_a_step_error_and_links_and_logs_nothing(
    offline: Callable[[Steps], Wiki], bundle: Path, relate: Callable[..., Any]
) -> None:
    wiki = offline(replace(OFFLINE, relate=relate))
    seed, log = (bundle / SEED).read_bytes(), (bundle / "log.md").read_bytes()

    with pytest.raises(StepError) as raised:
        wiki.ingest("Some text.", title="Memo")

    assert raised.value.step == "relate" and "relate:" in str(raised.value)
    assert (bundle / SEED).read_bytes() == seed and (bundle / "log.md").read_bytes() == log


ASK_ERRORS = [
    pytest.param("select", replace(OFFLINE, select=lambda ctx, root, question: "overview.md"), id="select-not-a-list"),
    pytest.param("select", replace(OFFLINE, select=lambda ctx, root, question: [GHOST_NOTE]), id="select-note-not-in-tree"),
    pytest.param("select", replace(OFFLINE, select=lambda ctx, root, question: [SEED]), id="select-not-notes"),
    pytest.param("answer", replace(OFFLINE, answer=lambda ctx, question, notes: 42), id="answer-not-a-str"),
]


@pytest.mark.parametrize(("step", "steps"), ASK_ERRORS)
def test_a_select_or_answer_output_the_flow_cannot_use_is_a_step_error(
    offline: Callable[[Steps], Wiki], bundle: Path, step: str, steps: Steps
) -> None:
    wiki = offline(steps)
    before = snapshot(bundle)

    with pytest.raises(StepError) as raised:
        wiki.ask("What?")

    assert raised.value.step == step and f"{step}:" in str(raised.value)
    assert snapshot(bundle) == before


def test_step_error_is_a_wiki_error() -> None:
    assert issubclass(StepError, WikiError)


def test_empty_text_from_extract_stays_an_input_error(offline: Callable[[Steps], Wiki], bundle: Path) -> None:
    wiki = offline(replace(OFFLINE, extract=lambda ctx, data, filename: "  \n"))
    before = snapshot(bundle)

    with pytest.raises(ValueError, match="empty"):
        wiki.ingest_file(b"x", "memo.txt")

    assert snapshot(bundle) == before


def test_a_custom_step_does_not_see_the_tree_through_the_context(offline: Callable[[Steps], Wiki]) -> None:
    seen: list[StepContext] = []

    def summarize(ctx: StepContext, source: Source) -> Candidate:
        seen.append(ctx)
        return o_summarize(ctx, source)

    offline(replace(OFFLINE, summarize=summarize)).ingest("Text.")

    assert not hasattr(seen[0], "store")  # extract and summarize stay pure: no store access


# -- CROW steps need the classifier ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "steps"),
    [
        pytest.param("route", replace(OFFLINE, route=CROW.route), id="route"),
        pytest.param("match", replace(OFFLINE, match=CROW.match), id="match"),
        pytest.param("consolidate", replace(MERGING, consolidate=CROW.consolidate), id="consolidate"),
    ],
)
def test_a_crow_decision_step_without_a_classifier_is_a_step_error_naming_it_and_writes_nothing(
    bundle: Path, llm: FakeLLM, step: str, steps: Steps
) -> None:
    wiki = build(bundle, llm, steps, mode="classic")  # classic mode builds no classifier
    before = snapshot(bundle)

    with pytest.raises(StepError, match=f"{step}: needs the classifier") as raised:
        wiki.ingest("Some text.", title="Memo")

    assert raised.value.step == step
    assert snapshot(bundle) == before
    assert llm.calls == []


def test_crow_relate_without_a_classifier_is_a_step_error_naming_it(bundle: Path, llm: FakeLLM) -> None:
    wiki = build(bundle, llm, replace(OFFLINE, relate=CROW.relate), mode="classic")

    with pytest.raises(StepError, match="relate: needs the classifier") as raised:
        wiki.ingest("Some text.", title="Memo")

    assert raised.value.step == "relate"


def test_crow_select_without_a_classifier_is_a_step_error_naming_it(bundle: Path, llm: FakeLLM) -> None:
    wiki = build(bundle, llm, replace(OFFLINE, select=CROW.select), mode="classic")

    with pytest.raises(StepError, match="select: needs the classifier") as raised:
        wiki.ask("What?")

    assert raised.value.step == "select"
    assert llm.calls == []


# -- fields ---------------------------------------------------------------------------------------------------


def test_decision_steps_read_the_source_fields_through_the_context(offline: Callable[[Steps], Wiki]) -> None:
    seen: dict[str, Any] = {}

    def spy(name: str, inner: Callable[..., Any]) -> Callable[..., Any]:
        def step(ctx: StepContext, *args: Any) -> Any:
            seen[name] = ctx.source.fields if ctx.source else None
            return inner(ctx, *args)

        return step

    fields = {"category": "chem", "number": 7}
    steps = replace(
        OFFLINE,
        summarize=spy("summarize", o_summarize),
        route=lambda ctx, root, cand: (seen.__setitem__("route", ctx.source.fields), Route(root, True, []))[1],
        name_folder=spy("name_folder", o_name_folder),
        relate=spy("relate", o_relate),
    )

    # a route to the root makes name_folder run; the new folder holds nothing, so only relate sees no one to relate
    offline(steps).ingest("Text.", fields=fields)

    assert {name: seen[name] for name in ("summarize", "route", "name_folder")} == {
        k: fields for k in ("summarize", "route", "name_folder")
    }


def test_match_consolidate_and_merge_read_the_source_fields_through_the_context(offline: Callable[[Steps], Wiki]) -> None:
    seen: dict[str, Any] = {}

    def spy(name: str, inner: Callable[..., Any]) -> Callable[..., Any]:
        def step(ctx: StepContext, *args: Any) -> Any:
            seen[name] = ctx.source.fields if ctx.source else None
            return inner(ctx, *args)

        return step

    steps = replace(MERGING, match=spy("match", pick_seed), consolidate=spy("consolidate", always), merge=spy("merge", o_merge))

    offline(steps).ingest("Text.", fields={"k": "v"})

    assert seen == {"match": {"k": "v"}, "consolidate": {"k": "v"}, "merge": {"k": "v"}}


def test_ingest_file_passes_fields_to_the_source_and_ask_steps_have_no_source(offline: Callable[[Steps], Wiki]) -> None:
    seen: list[Any] = []

    def summarize(ctx: StepContext, source: Source) -> Candidate:
        seen.append(source.fields)
        return o_summarize(ctx, source)

    def select(ctx: StepContext, root: Folder, question: str) -> list[Note]:
        seen.append(ctx.source)
        return o_select(ctx, root, question)

    wiki = offline(replace(OFFLINE, summarize=summarize, select=select))
    wiki.ingest_file(b"Some text.", "memo.txt", fields={"year": 2024})
    wiki.ask("What?")

    assert seen == [{"year": 2024}, None]


def test_source_fields_default_to_empty() -> None:
    assert Source("text").fields == {}
    assert Candidate("t", "s", [], "b").fields == {}


def test_candidate_fields_are_written_under_the_fields_key_and_absent_when_empty(offline: Callable[[Steps], Wiki], bundle: Path) -> None:
    def summarize(ctx: StepContext, source: Source) -> Candidate:
        return Candidate("With fields", "S.", [], "B.", fields={"number": "EP1", "year": 2024, "nested": {"a": [1, 2]}})

    with_fields = offline(replace(OFFLINE, summarize=summarize)).ingest("One.")

    fm = read(bundle / with_fields.note).frontmatter
    assert fm["fields"] == {"number": "EP1", "year": 2024, "nested": {"a": [1, 2]}}
    assert read(bundle / SEED).frontmatter.get("fields") is None


def test_a_candidate_without_fields_leaves_no_fields_key(offline: Callable[[Steps], Wiki], bundle: Path) -> None:
    result = offline(OFFLINE).ingest("Text.", fields={"given": "by the caller"})  # the default does not copy source.fields

    assert "fields" not in read(bundle / result.note).frontmatter


def test_a_merge_updates_the_existing_fields_with_the_draft_ones(offline: Callable[[Steps], Wiki], bundle: Path) -> None:
    def summarize(ctx: StepContext, source: Source) -> Candidate:
        return Candidate("Patent", "S.", [], "B.", fields={"a": 1, "b": 2})

    def merge(ctx: StepContext, note: Note, source: Source) -> NoteDraft:
        return NoteDraft(title="Patent", summary="S.", body="B2.", fields={"b": 3, "c": 4})

    def match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
        return next((n for n in pool if n.title == "Patent"), None)

    wiki = offline(replace(MERGING, summarize=summarize, match=match, merge=merge))
    created = wiki.ingest("One.")
    assert read(bundle / created.note).frontmatter["fields"] == {"a": 1, "b": 2}

    merged = wiki.ingest("Two.")

    assert (merged.action, merged.note) == ("merged", created.note)
    assert read(bundle / created.note).frontmatter["fields"] == {"a": 1, "b": 3, "c": 4}


# -- provenance ---------------------------------------------------------------------------------------------------


def one(ctx: StepContext, source: Source) -> Candidate:
    return Candidate("One", "S.", [], "B.")


def two(ctx: StepContext, source: Source) -> Candidate:
    title = "Two"
    return Candidate(title, "S.", [], "B.")


def test_steps_default_to_the_classic_functions_and_the_default_name_is_custom() -> None:
    steps = Steps(summarize=one)

    assert steps.name == "custom"
    assert steps.summarize is one and steps.route is CLASSIC.route and steps.answer is CLASSIC.answer
    assert (CLASSIC.name, CROW.name) == ("classic", "crow")


def test_note_source_and_log_line_carry_the_steps_name_and_hash(offline: Callable[[Steps], Wiki], bundle: Path) -> None:
    steps = Steps(summarize=one, route=o_route, match=o_match, consolidate=o_consolidate, relate=o_relate)

    result = offline(steps).ingest("Text.")

    [source] = read(bundle / result.note).frontmatter["sources"]
    assert source["steps"] == {"name": "custom", "hash": steps.hash}
    assert f"(custom {steps.hash})" in (bundle / "log.md").read_text(encoding="utf-8")


def test_a_merge_adds_the_steps_entry_to_the_new_source(offline: Callable[[Steps], Wiki], bundle: Path) -> None:
    offline(MERGING).ingest("Text.")

    [seeded, merged] = read(bundle / SEED).frontmatter["sources"]
    assert "steps" not in seeded
    assert merged["steps"] == {"name": "offline", "hash": MERGING.hash}


def test_classic_provenance(classic_wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    llm.add("librarian/summarize", {"title": "Acme", "summary": "S.", "body": "B."})
    llm.add("librarian/route", {"action": "here"}).add("librarian/name_folder", {"name": "acme", "description": "Acme things."})

    result = classic_wiki.ingest("Acme text.")

    assert classic_wiki.steps is CLASSIC
    assert read(bundle / result.note).frontmatter["sources"][0]["steps"] == {"name": "classic", "hash": CLASSIC.hash}
    assert f"(classic {CLASSIC.hash})" in (bundle / "log.md").read_text(encoding="utf-8")


def test_crow_provenance(crow_wiki: Wiki, llm: FakeLLM, bundle: Path) -> None:
    llm.add("librarian/summarize", {"title": "Acme", "summary": "S.", "body": "B."})
    llm.add("librarian/name_folder", {"name": "acme", "description": "Acme things."})

    result = crow_wiki.ingest("Acme text.")

    assert crow_wiki.steps is CROW
    assert read(bundle / result.note).frontmatter["sources"][0]["steps"] == {"name": "crow", "hash": CROW.hash}
    assert f"(crow {CROW.hash})" in (bundle / "log.md").read_text(encoding="utf-8")


def test_the_hash_is_eight_hex_characters_and_stable() -> None:
    assert len(CLASSIC.hash) == 8 and int(CLASSIC.hash, 16) >= 0
    assert CLASSIC.hash == CLASSIC.hash == replace(CLASSIC).hash
    assert replace(CROW, name="renamed").hash == CROW.hash  # the hash is of the code, not the name
    assert CROW.hash != CLASSIC.hash  # CROW replaces five steps


def test_the_hash_changes_when_a_step_code_changes() -> None:
    assert Steps(summarize=one).hash == Steps(summarize=one).hash
    assert Steps(summarize=one).hash != Steps(summarize=two).hash
    assert replace(CROW, answer=o_answer).hash != CROW.hash


def test_a_partial_hashes_as_the_function_it_wraps() -> None:
    def first(n: int, ctx: StepContext, source: Source) -> Candidate:
        return Candidate("A", "a", [], source.text)

    def second(n: int, ctx: StepContext, source: Source) -> Candidate:
        return Candidate("B", "b", [], source.text)

    assert Steps(summarize=partial(first, 1)).hash != Steps(summarize=partial(second, 1)).hash
    assert Steps(summarize=partial(first, 1)).hash == Steps(summarize=partial(first, 2)).hash  # bound values are not seen


# -- model calls and decisions made through the context ------------------------------------------------------------------------


def test_model_calls_and_decisions_of_a_custom_ingest_step_are_counted(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    def summarize(ctx: StepContext, source: Source) -> Candidate:
        draft = ctx.ask_json("librarian/summarize", NoteDraft, source_title="Memo", source_resource="(none)", source_text=source.text)
        return Candidate(draft.title, draft.summary, [], draft.body)

    def route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
        ctx.decide("route", "rule", "/finance")
        return Route(finance(root), False, [])

    def match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
        assert ctx.classifier is not None
        ctx.classifier.nouls("state", {"q": "Same subject?"}, op="match")
        return None

    wiki = build(bundle, llm, replace(OFFLINE, summarize=summarize, route=route, match=match), mode="crow", classifier=classifier)
    llm.add("librarian/summarize", {"title": "Drafted", "summary": "S.", "body": "B."})

    result = wiki.ingest("Text.")

    assert result.title == "Drafted"
    assert (result.usage.llm.calls, result.usage.llm.input_tokens, result.usage.llm.output_tokens) == (1, *LLM_TOKENS)
    assert result.usage.classifier.calls == 1
    assert Decision("route", "rule", "/finance") in result.decisions
    assert wiki.usage.llm.calls == 1 and wiki.usage.classifier.calls == 1


def test_model_calls_and_decisions_of_a_custom_ask_step_are_counted(offline: Callable[[Steps], Wiki], llm: FakeLLM) -> None:
    def select(ctx: StepContext, root: Folder, question: str) -> list[Note]:
        ctx.decide("select", "rule", "everything")
        return root.all_notes()

    def answer(ctx: StepContext, question: str, notes: list[Note]) -> str:
        return ctx.ask_text("researcher/answer", question=question, notes="\n".join(n.rel for n in notes))

    llm.add("researcher/answer", f"Seeded [{SEED}].")

    result = offline(replace(OFFLINE, select=select, answer=answer)).ask("What is seeded?")

    assert result.text == f"Seeded [{SEED}]." and result.citations == [SEED]
    assert (result.usage.llm.calls, result.usage.llm.input_tokens) == (1, LLM_TOKENS[0])
    assert Decision("select", "rule", "everything") in result.decisions
    assert llm.ops == ["researcher/answer"]


# -- Wiki picks the steps -----------------------------------------------------------------------------------------------------


def test_wiki_without_steps_picks_classic_or_crow_by_mode(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    assert Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm).steps is CLASSIC
    assert Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier).steps is CROW


def test_wiki_with_steps_uses_them_whatever_the_mode(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    steps = replace(CROW, name="mine")

    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier, steps=steps)

    assert wiki.steps is steps
    assert wiki.classifier is classifier  # mode still decides the classifier is built


# -- the patents scenario: what is already known comes in Source.fields, nothing calls a model ---------------------------------

RECORDS = [
    {"number": "EP100", "title": "Catalyst process", "category": "chemistry", "applicant": "Acme", "summary": "A catalyst."},
    {"number": "EP101", "title": "Solvent recovery", "category": "chemistry/organic", "applicant": "Acme", "summary": "Recovers solvents."},
    {"number": "EP102", "title": "Ester synthesis", "category": "chemistry/organic", "applicant": "Acme", "summary": "Makes esters."},
    {"number": "EP200", "title": "Gate oxide", "category": "electronics", "applicant": "Beta", "summary": "A thin oxide."},
    {"number": "EP201", "title": "Wafer dicing", "category": "electronics/semiconductors", "applicant": "Beta", "summary": "Cuts wafers."},
]


def patent_summarize(ctx: StepContext, source: Source) -> Candidate:
    f = source.fields
    keep = {k: f[k] for k in ("number", "category", "applicant")}
    return Candidate(f"{f['number']} {source.title}", f["summary"], [f["category"].split("/")[0]], source.text, fields=keep)


def patent_route(ctx: StepContext, root: Folder, cand: Candidate) -> Route:
    """The whole category path, opened in one go below the root (levels that exist are reused)."""
    assert ctx.source is not None
    parts = ctx.source.fields["category"].split("/")
    deepest = root
    for part in parts:
        if (sub := deepest.subfolder(part)) is None:
            break
        deepest = sub
    return Route(root, False, [deepest], create=[(part, f"Patents on {part}.") for part in parts])


def never_match(ctx: StepContext, pool: list[Note], cand: Candidate) -> Note | None:
    return None


def same_applicant(ctx: StepContext, note: Note, others: list[Note]) -> list[Note]:
    assert ctx.source is not None
    return [o for o in others if o.frontmatter.get("fields", {}).get("applicant") == ctx.source.fields["applicant"]]


def by_category(ctx: StepContext, root: Folder, question: str) -> list[Note]:
    """The question is a category: the notes whose frontmatter fields say they belong to it or below it."""
    return [n for n in root.all_notes() if n.frontmatter.get("fields", {}).get("category", "").startswith(question)]


def list_patents(ctx: StepContext, question: str, notes: list[Note]) -> str:
    return "; ".join(f"{n.frontmatter['fields']['number']} [{n.rel}]" for n in notes) + " [made-up.md]"


PATENTS = Steps(
    name="patents",
    summarize=patent_summarize,
    route=patent_route,
    match=never_match,
    consolidate=o_consolidate,
    merge=o_merge,
    relate=same_applicant,
    select=by_category,
    answer=list_patents,
)


@pytest.fixture
def patents(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier, steps=PATENTS)
    wiki.init()
    return wiki


def ingest_all(wiki: Wiki) -> list[Any]:
    return [
        wiki.ingest(f"Full text of {r['number']}.", title=r["title"], fields={k: v for k, v in r.items() if k != "title"}) for r in RECORDS
    ]


def test_patents_are_filed_by_category_without_a_single_model_call(
    patents: Wiki, llm: FakeLLM, classifier: FakeClassifier, bundle: Path
) -> None:
    results = ingest_all(patents)

    assert llm.calls == [] and classifier.calls == []
    assert patents.usage == UsageReport() and all(r.usage == UsageReport() for r in results)
    assert [r.action for r in results] == ["created"] * 5
    assert [r.folder for r in results] == [
        "/chemistry",
        "/chemistry/organic",
        "/chemistry/organic",
        "/electronics",
        "/electronics/semiconductors",
    ]
    assert [r.created_folders for r in results] == [
        ["/chemistry"],
        ["/chemistry/organic"],
        [],
        ["/electronics"],
        ["/electronics/semiconductors"],
    ]
    assert [r.note.rsplit("/", 1)[0] for r in results] == [
        "chemistry",
        "chemistry/organic",
        "chemistry/organic",
        "electronics",
        "electronics/semiconductors",
    ]
    assert "* [organic](organic/index.md) - Patents on organic." in read(bundle / "chemistry" / "index.md").body.splitlines()
    assert patents.check() == []


def test_a_patent_note_keeps_what_was_known_in_its_frontmatter(patents: Wiki, bundle: Path) -> None:
    first, *_ = ingest_all(patents)

    fm = read(bundle / first.note).frontmatter

    assert fm["title"] == "EP100 Catalyst process" and fm["description"] == "A catalyst." and fm["tags"] == ["chemistry"]
    assert fm["fields"] == {"number": "EP100", "category": "chemistry", "applicant": "Acme"}
    assert fm["sources"][0]["steps"] == {"name": "patents", "hash": PATENTS.hash}
    assert len(raws(bundle)) == 5


def test_patents_of_one_applicant_are_linked_by_the_custom_relate(patents: Wiki, bundle: Path) -> None:
    results = ingest_all(patents)

    # others = the notes of the folder the note was routed to, and of the one it was written in
    assert [r.related for r in results] == [[], [results[0].note], [results[1].note], [], [results[3].note]]
    assert results[2].note.rsplit("/", 1)[1] in read(bundle / results[1].note).body  # the back link


def test_ask_selects_on_frontmatter_fields_and_answers_without_a_model(patents: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    results = ingest_all(patents)
    chemistry = sorted(r.note for r in results[:3])

    answer = patents.ask("chemistry")

    assert sorted(answer.notes) == chemistry
    assert sorted(answer.citations) == chemistry  # "made-up.md" was not read: the core drops it
    assert all(f"{n} " in answer.text or f"[{n}]" in answer.text for n in chemistry)
    assert "EP100" in answer.text and "EP200" not in answer.text
    assert llm.calls == [] and classifier.calls == [] and answer.usage == UsageReport()


def test_ask_on_a_narrower_category_returns_only_that_one(patents: Wiki) -> None:
    results = ingest_all(patents)

    answer = patents.ask("electronics/semiconductors")

    assert (answer.notes, answer.citations) == ([results[4].note], [results[4].note])


def test_ask_on_an_unknown_category_has_no_answer(patents: Wiki) -> None:
    ingest_all(patents)

    answer = patents.ask("mechanics")

    assert (answer.text, answer.notes, answer.citations) == (NO_ANSWER, [], [])
