# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from spec-revisions-replace-2026-10-09.md: `revisions = "replace"`, a revision replaces the note and the old body is archived."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from llmw2 import NoteDraft, Origin, Source, StepContext, Wiki, WikiConfig
from llmw2.agents import revisions
from llmw2.agents.data import Candidate
from llmw2.agents.prompts import Prompts
from llmw2.bundle.document import OKFDocument
from llmw2.bundle.origin import content_hash
from llmw2.bundle.tree import Note
from llmw2.errors import ModelError
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import CLASSIFIER_TOKENS, FakeClassifier, FakeLLM

SUMMARIZE, ROUTE, FIND_MATCH, CONSOLIDATE, MERGE = (
    "librarian/summarize",
    "librarian/route",
    "librarian/find_match",
    "librarian/consolidate",
    "librarian/merge",
)
ANSWER = "researcher/answer"
HERE, NEW_FOLDER, MODIFY = "Here", "New subfolder", "Modify"
IDENTITY_OP, STILL_OP, HISTORY_OP = "revise/identity", "revise/still_holds", "ask/historical"

TITLE = "Wholesale programme review 2026"
REV_TITLE = "Wholesale programme review 2026 — rev. 2"
ORIGIN_ID = "reviews/wholesale-programme-review-2026.docx"
REV_ORIGIN_ID = "reviews/wholesale-programme-review-2026-rev2.docx"

# -- texts: long enough for 8-word shingles, deterministic ----------------------------------------------------------

WORDS = (  # noqa: SIM905
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo "
    "sierra tango uniform victor whiskey xray yankee zulu"
).split()


def sentence(tag: str, i: int) -> str:
    words = [WORDS[(i * 5 + j * 7) % len(WORDS)] for j in range(8)]
    return f"The {tag} note {i} covers {' '.join(words)} in detail."


def doc(n: int = 70, tag: str = "base", over: dict[int, str] | None = None, per: int = 4) -> str:
    """`n` distinct sentences in paragraphs of `per`; `over` replaces the sentence at an index."""
    sentences = [(over or {}).get(i) or sentence(tag, i) for i in range(n)]
    return "\n\n".join(" ".join(sentences[k : k + per]) for k in range(0, n, per))


CODE_OLD = "The part code is AB-1234 for every wholesale order."
CODE_NEW = "The part code is AB-5678 for every wholesale order."
DATE_OLD = "Delivery is due on 12 March 2026 at the main depot."
DATE_NEW = "Delivery is due on 15 April 2027 at the main depot."
OLD = doc(over={10: CODE_OLD, 31: DATE_OLD})
NEW = doc(over={10: CODE_NEW, 31: DATE_NEW})
OTHER = doc(tag="other")
OLD_BODY = "# Overview\n\nOLDBODYMARKER the programme review as first written.\n"


def words(n: int) -> str:
    return " ".join(f"w{i}" for i in range(n))


# -- helpers ----------------------------------------------------------------------------------------------------


class Scripted(FakeClassifier):
    """A FakeClassifier whose Noul for some ops is a function of the state (the text asked about), not of the question."""

    def __init__(self, usage: UsageTracker, by_state: dict[str, Callable[[str], float]] | None = None, **kw: Any) -> None:
        super().__init__(usage, **kw)
        self.by_state = dict(by_state or {})

    def ask(self, state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
        score = self.by_state.get(op)
        if score is None:
            return super().ask(state, questions, op=op)
        self.calls.append((op, state, questions))
        self.usage.record("classifier", self.model, *CLASSIFIER_TOKENS, op=op)
        return {key: {"type": "noul", "noul": score(state)} for key in questions}


def raising(state: str) -> float:
    raise ModelError("the classifier is down")


def make_wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier | None = None, **cfg: Any) -> Wiki:
    mode = "crow" if classifier is not None else "classic"
    wiki = Wiki(WikiConfig(bundle=bundle, mode=mode, revisions=cfg.pop("revisions", "replace"), **cfg), llm=llm, classifier=classifier)
    wiki.init()
    return wiki


def make_ctx(
    wiki: Wiki, classifier: FakeClassifier | None = None, *, source: Source | None = None, candidate: Candidate | None = None
) -> StepContext:
    ctx = StepContext(FakeLLM(UsageTracker()), classifier, Prompts(), wiki.cfg)
    ctx.source, ctx.candidate = source, candidate
    return ctx


def folder_of(wiki: Wiki, rel: str) -> Any:
    root = wiki.store.load()
    if root.find(rel) is None:
        wiki.store.create_folder(root, rel.strip("/"), f"Everything about {rel.strip('/')}.")
    return wiki.store.load().find(rel)


def ref_of(wiki: Wiki, text: str, *, title: str = TITLE, origin_id: str | None = ORIGIN_ID) -> dict[str, Any]:
    """A raw copy of `text` and the source entry a note citing it has (what the Librarian's save_source builds)."""
    origin = Origin("local", "", origin_id) if origin_id else None
    raw = wiki.store.save_raw(text, title=title, resource=None, origin=origin)
    ref: dict[str, Any] = {"resource": raw, "title": title, "hash": content_hash(text)}
    if origin is not None:
        ref["origin"] = origin.to_dict()
    return ref


def seed(
    wiki: Wiki,
    text: str = OLD,
    *,
    folder: str = "/",
    title: str = TITLE,
    origin_id: str | None = ORIGIN_ID,
    body: str = OLD_BODY,
    note_title: str = TITLE,
) -> Note:
    """A note `note_title` in `folder`, citing a raw copy of `text`."""
    ref = ref_of(wiki, text, title=title, origin_id=origin_id)
    return wiki.store.write_note(folder_of(wiki, folder), title=note_title, summary="The review.", body=body, tags=[], source=ref)


def add_copy(wiki: Wiki, note: Note, text: str, *, title: str = TITLE, origin_id: str | None = ORIGIN_ID) -> str:
    """The note cites one more raw copy; its resource."""
    ref = ref_of(wiki, text, title=title, origin_id=origin_id)
    wiki.store.update_note(note, title=note.title, summary=note.summary, body=note.main_body(), tags=[], source=ref)
    return str(ref["resource"])


def source_of(
    text: str = NEW, *, title: str | None = REV_TITLE, origin_id: str | None = REV_ORIGIN_ID, resource: str | None = None
) -> Source:
    return Source(text, title, resource, Origin("local", "", origin_id) if origin_id else None)


def candidate(body: str = "# Summary\n\nNEWSUMMARYMARKER the review as rewritten.", **kw: Any) -> Candidate:
    return Candidate(
        kw.pop("title", "Review 2026 rev 2"), kw.pop("summary", "What the revision says."), kw.pop("tags", ["topic"]), body, **kw
    )


def read(path: Path) -> OKFDocument:
    return OKFDocument.parse(path.read_text(encoding="utf-8"))


def decisions(ctx: StepContext) -> list[Any]:
    return [d for d in ctx.decisions if d.step == "revise"]


def archive_files(bundle: Path) -> list[Path]:
    return sorted(p for p in (bundle / ".archive").rglob("*.md")) if (bundle / ".archive").is_dir() else []


# -- config -------------------------------------------------------------------------------------------------------


def test_revisions_defaults_to_replace(bundle: Path) -> None:
    assert WikiConfig(bundle=bundle).revisions == "replace"


def test_revisions_from_the_environment(tmp_path: Path) -> None:
    assert WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_REVISIONS": "merge"}).revisions == "merge"
    assert WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path)}).revisions == "replace"


def test_revisions_rejects_other_values(tmp_path: Path) -> None:
    with pytest.raises((ValidationError, ValueError)):
        WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_REVISIONS": "overwrite"})


def test_constants_are_the_specified_ones() -> None:
    assert (revisions.NEW_IN_OLD, revisions.OLD_IN_NEW, revisions.SAME_DOC_MIN) == (0.6, 0.9, 0.7)
    assert (revisions.STILL_MIN, revisions.HISTORY_MIN) == (0.3, 0.5)
    assert (revisions.CHANGES_MAX_LINES, revisions.KEPT_MAX_CHARS, revisions.ARCHIVE_DIR) == (60, 8000, ".archive")


# -- normalize_name -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("Wholesale programme review 2026 — rev. 2", "wholesale programme review 2026", id="spec-title"),
        pytest.param("13-wholesale-programme-review-2026-rev2", "wholesale programme review 2026", id="spec-file"),
        pytest.param("lotto_005", "lotto 005", id="plain-number-stays"),
        pytest.param("Annex 3", "annex 3", id="number-without-marker-stays"),
        pytest.param("relazione-2025.pdf", "relazione 2025", id="extension-pdf"),
        pytest.param("Report.DOCX", "report", id="extension-docx-any-case"),
        pytest.param("notes.txt", "notes", id="extension-txt"),
        pytest.param("notes.md", "notes", id="extension-md"),
        pytest.param("02_report", "report", id="order-prefix-underscore"),
        pytest.param("13-foo-bar", "foo bar", id="order-prefix-dash"),
        pytest.param("7 report", "report", id="order-prefix-space"),
        pytest.param("Plan v2", "plan", id="marker-v2"),
        pytest.param("plan_v1.3", "plan", id="marker-v1.3"),
        pytest.param("Plan - rev 2", "plan", id="marker-rev-space"),
        pytest.param("Plan – revision 3", "plan", id="marker-revision-endash"),  # noqa: RUF001
        pytest.param("Plan — revisione 3", "plan", id="marker-revisione-emdash"),
        pytest.param("Plan version 2", "plan", id="marker-version"),
        pytest.param("Plan versione 2", "plan", id="marker-versione"),
        pytest.param("Plan draft", "plan", id="marker-draft"),
        pytest.param("Plan draft 2", "plan", id="marker-draft-2"),
        pytest.param("Plan-bozza", "plan", id="marker-bozza"),
        pytest.param("Plan_bozza 2", "plan", id="marker-bozza-2"),
        pytest.param("plan-v2.docx", "plan", id="marker-and-extension"),
        pytest.param("A--B__C", "a b c", id="non-alphanumerics-become-one-space"),
        pytest.param("  Plan  ", "plan", id="stripped"),
    ],
)
def test_normalize_name(name: str, expected: str) -> None:
    assert revisions.normalize_name(name) == expected


def test_normalize_name_keeps_apart_what_is_not_a_revision() -> None:
    assert revisions.normalize_name("relazione-2025.pdf") != revisions.normalize_name("relazione-2026.pdf")
    assert revisions.normalize_name("Budget 2025") != revisions.normalize_name("Budget 2026")
    assert revisions.normalize_name("lotto_005") != revisions.normalize_name("lotto_006")


def test_normalize_name_is_idempotent_on_the_spec_examples() -> None:
    for name in ("Wholesale programme review 2026 — rev. 2", "13-wholesale-programme-review-2026-rev2", "lotto_005"):
        once = revisions.normalize_name(name)
        assert revisions.normalize_name(once) == once


# -- containment, body_hash ----------------------------------------------------------------------------------------


def test_containment_of_identical_texts_is_one_both_ways() -> None:
    assert revisions.containment(words(20), words(20)) == (1.0, 1.0)


def test_containment_is_new_in_old_then_old_in_new() -> None:
    old, new = words(20), words(12)  # 13 shingles of 8 words in old, 5 in new, all of new's in old

    new_in_old, old_in_new = revisions.containment(old, new)

    assert new_in_old == pytest.approx(1.0)
    assert old_in_new == pytest.approx(5 / 13)
    flipped = revisions.containment(new, old)
    assert flipped[0] == pytest.approx(5 / 13) and flipped[1] == pytest.approx(1.0)


def test_containment_ignores_case() -> None:
    assert revisions.containment(words(20), words(20).upper()) == (1.0, 1.0)


def test_containment_of_disjoint_texts_is_zero() -> None:
    other = " ".join(f"z{i}" for i in range(20))
    assert revisions.containment(words(20), other) == (0.0, 0.0)


@pytest.mark.parametrize(("old", "new"), [("", words(20)), (words(20), ""), ("", ""), ("a b c", words(20)), (words(20), "one two")])
def test_containment_is_zero_when_a_text_has_no_shingle(old: str, new: str) -> None:
    assert revisions.containment(old, new) == (0.0, 0.0)


def test_body_hash_is_sha256_of_the_stripped_utf8_text() -> None:
    text = "Prezzo è 120 €.\n"
    expected = "sha256:" + hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
    assert revisions.body_hash(text) == expected
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", revisions.body_hash(text))


def test_body_hash_ignores_outer_whitespace_only() -> None:
    assert revisions.body_hash("\n  Body text\n\n") == revisions.body_hash("Body text")
    assert revisions.body_hash("Body text") == revisions.body_hash("Body text")
    assert revisions.body_hash("Body text") != revisions.body_hash("Body  text")
    assert revisions.body_hash("Body text") != revisions.body_hash("Body texts")


# -- the store ----------------------------------------------------------------------------------------------------


def test_write_note_records_the_hash_of_the_main_body(classic_wiki: Wiki, bundle: Path) -> None:
    folder = classic_wiki.store.load()
    note = classic_wiki.store.write_note(
        folder, title="Alpha", summary="A.", body="# Overview\n\nThe facts.", tags=[], source={"resource": "/raw/a.md", "title": "A"}
    )

    stored = read(bundle / "alpha.md").frontmatter["generated"]
    assert stored["body_hash"] == revisions.body_hash("# Overview\n\nThe facts.")
    assert stored["body_hash"] == note.frontmatter["generated"]["body_hash"] == revisions.body_hash(note.main_body())
    assert {"by", "at"} <= set(stored)


def test_update_note_records_the_hash_of_the_new_main_body_without_see_also(classic_wiki: Wiki, bundle: Path) -> None:
    store = classic_wiki.store
    root = store.load()
    src = {"resource": "/raw/a.md", "title": "A"}
    a = store.write_note(root, title="Alpha", summary="A.", body="Old body.", tags=[], source=src)
    b = store.write_note(root, title="Beta", summary="B.", body="Beta body.", tags=[], source=src)
    store.link(a, b)
    assert "See also" in a.body

    store.update_note(a, title="Alpha", summary="A.", body="# Overview\n\nNew body.", tags=[], source=src)

    stored = read(bundle / "alpha.md")
    assert "See also" in stored.body  # the links stay
    assert stored.frontmatter["generated"]["body_hash"] == revisions.body_hash("# Overview\n\nNew body.")
    assert stored.frontmatter["generated"]["body_hash"] == revisions.body_hash(a.main_body())


def test_link_leaves_the_hash_alone(classic_wiki: Wiki, bundle: Path) -> None:
    store = classic_wiki.store
    root = store.load()
    src = {"resource": "/raw/a.md", "title": "A"}
    a = store.write_note(root, title="Alpha", summary="A.", body="Alpha body.", tags=[], source=src)
    b = store.write_note(root, title="Beta", summary="B.", body="Beta body.", tags=[], source=src)
    before = (a.frontmatter["generated"]["body_hash"], b.frontmatter["generated"]["body_hash"])

    assert store.link(a, b)

    for name, note, hash_before in (("alpha", a, before[0]), ("beta", b, before[1])):
        stored = read(bundle / f"{name}.md")
        assert stored.frontmatter["generated"]["body_hash"] == hash_before == revisions.body_hash(note.main_body())


def test_update_note_writes_previous_version_only_when_given(classic_wiki: Wiki, bundle: Path) -> None:
    store = classic_wiki.store
    src = {"resource": "/raw/a.md", "title": "A"}
    note = store.write_note(store.load(), title="Alpha", summary="A.", body="Body.", tags=[], source=src)

    store.update_note(note, title="Alpha", summary="A.", body="Body 2.", tags=[], source=src)
    assert "previous_version" not in read(bundle / "alpha.md").frontmatter

    store.update_note(
        note, title="Alpha", summary="A.", body="Body 3.", tags=[], source=src, previous_version="/.archive/alpha--20260101-000000.md"
    )
    assert read(bundle / "alpha.md").frontmatter["previous_version"] == "/.archive/alpha--20260101-000000.md"


@pytest.mark.parametrize(("folder", "rel"), [("/", "alpha.md"), ("/fin/tax", "fin/tax/alpha.md")])
def test_archive_note_copies_the_note_under_dot_archive(classic_wiki: Wiki, bundle: Path, folder: str, rel: str) -> None:
    store = classic_wiki.store
    if folder != "/":
        top, _ = store.create_folder(store.load(), "fin", "Money.")
        store.create_folder(top, "tax", "Taxes.")
    src = {"resource": "/raw/a.md", "title": "A"}
    note = store.write_note(store.load().find(folder), title="Alpha", summary="A.", body="# Overview\n\nThe facts.", tags=["x"], source=src)
    other = store.write_note(store.load().find(folder), title="Beta", summary="B.", body="Beta.", tags=[], source=src)
    store.link(note, other)
    log_before = (bundle / "log.md").read_text(encoding="utf-8")
    on_disk = (bundle / rel).read_text(encoding="utf-8")

    archived = store.archive_note(note)

    assert re.fullmatch(rf"/\.archive/{re.escape(rel[:-3])}--\d{{8}}-\d{{6}}\.md", archived)
    copy = read(bundle / archived.lstrip("/"))
    assert (copy.frontmatter["status"], copy.frontmatter["archived_from"]) == ("archived", f"/{rel}")
    assert copy.frontmatter["archived_at"]
    assert copy.frontmatter["title"] == "Alpha" and copy.frontmatter["tags"] == ["x"]
    assert copy.body == read(bundle / rel).body  # the body as it was, See also included
    assert (bundle / rel).read_text(encoding="utf-8") == on_disk  # the live note is not touched
    assert "status" not in note.frontmatter and "archived_from" not in note.frontmatter
    assert (bundle / "log.md").read_text(encoding="utf-8") == log_before  # no log
    assert not list((bundle / ".archive").rglob("index.md"))  # no index


def test_the_archive_is_not_part_of_the_tree(classic_wiki: Wiki, bundle: Path) -> None:
    store = classic_wiki.store
    note = store.write_note(
        store.load(), title="Alpha", summary="A.", body="Body.", tags=[], source={"resource": "/raw/a.md", "title": "A"}
    )

    archived = store.archive_note(note)

    assert (bundle / archived.lstrip("/")).is_file()
    root = store.load()
    assert [n.rel for n in root.all_notes()] == ["alpha.md"]
    assert [f.rel for f in root.walk()] == [""]
    assert store.read(archived).frontmatter["status"] == "archived"  # but it can be read by path


def test_the_same_note_archived_twice_does_not_fail(classic_wiki: Wiki) -> None:
    store = classic_wiki.store
    note = store.write_note(
        store.load(), title="Alpha", summary="A.", body="Body.", tags=[], source={"resource": "/raw/a.md", "title": "A"}
    )

    first = store.archive_note(note)
    second = store.archive_note(note)

    assert first.startswith("/.archive/alpha--") and second.startswith("/.archive/alpha--")


# -- value_changes, changes_section, removed_strings ------------------------------------------------------------------


def test_value_changes_gives_old_new_and_the_new_sentence() -> None:
    got = revisions.value_changes(OLD, NEW)

    assert sorted(got) == sorted([("AB-1234", "AB-5678", CODE_NEW), ("12 March 2026", "15 April 2027", DATE_NEW)])


def test_value_changes_strips_edge_punctuation_of_the_values() -> None:
    old = doc(over={5: "Pay the fee of 250, then wait."})
    new = doc(over={5: "Pay the fee of 300, then wait."})

    assert revisions.value_changes(old, new) == [("250", "300", "Pay the fee of 300, then wait.")]


def test_value_changes_skips_a_replacement_without_digit_or_capital() -> None:
    old = doc(over={5: "The visit goes on quietly in the town."})
    new = doc(over={5: "The visit goes on slowly in the town."})
    assert revisions.value_changes(old, new) == []


def test_value_changes_skips_a_replacement_of_more_than_six_words() -> None:
    old = doc(over={5: "Start 1 2 3 4 5 6 7 8 finish."})
    new = doc(over={5: "Start 11 12 13 14 15 16 17 18 finish."})
    assert revisions.value_changes(old, new) == []


def test_value_changes_ignores_new_paragraphs_and_identical_texts() -> None:
    assert revisions.value_changes(OLD, OLD) == []
    assert revisions.value_changes(OLD, OLD + "\n\nA brand new paragraph about Rome 2027 and AB-9999.") == []


def test_changes_section_lists_each_change_with_the_new_value_in_bold() -> None:
    section = revisions.changes_section(OLD, NEW)

    assert section.startswith("## Changes from the previous version\n\n")
    lines = section.split("\n\n", 1)[1].splitlines()
    assert len(lines) == 2 and all(line.startswith("- ") for line in lines)
    code = next(line for line in lines if "AB-" in line)
    assert "**AB-5678**" in code and code.endswith("(previously: AB-1234)")
    assert code.startswith("- The part code is ")
    date = next(line for line in lines if "April" in line)
    assert "**15 April 2027**" in date and date.endswith("(previously: 12 March 2026)")


def test_changes_section_is_empty_without_changes() -> None:
    assert revisions.changes_section(OLD, OLD) == ""
    quiet_old = doc(over={5: "The visit goes on quietly in the town."})
    quiet_new = doc(over={5: "The visit goes on slowly in the town."})
    assert revisions.changes_section(quiet_old, quiet_new) == ""


def test_changes_section_writes_identical_lines_once() -> None:
    old = doc(over={3: CODE_OLD, 20: CODE_OLD})
    new = doc(over={3: CODE_NEW, 20: CODE_NEW})

    section = revisions.changes_section(old, new)

    assert section.count("(previously: AB-1234)") == 1


def test_changes_section_has_at_most_changes_max_lines() -> None:
    n = revisions.CHANGES_MAX_LINES + 10
    old = doc(n * 4, over={4 * i: f"The batch code is CD-{1000 + i} for lot {i}." for i in range(n)})
    new = doc(n * 4, over={4 * i: f"The batch code is CD-{5000 + i} for lot {i}." for i in range(n)})
    assert len(revisions.value_changes(old, new)) == n

    lines = [x for x in revisions.changes_section(old, new).splitlines() if x.startswith("- ")]

    assert len(lines) == revisions.CHANGES_MAX_LINES


def test_removed_strings_are_the_old_values_missing_from_the_new_text() -> None:
    assert revisions.removed_strings(OLD, NEW) == {"AB-1234", "12 March 2026"}


def test_removed_strings_leaves_out_an_old_value_the_new_text_still_has() -> None:
    new = NEW + "\n\nThe retired code AB-1234 is kept in the archive."

    assert revisions.removed_strings(OLD, new) == {"12 March 2026"}


# -- same_document ------------------------------------------------------------------------------------------------


def test_same_document_returns_the_last_copy_and_its_text(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)

    got = revisions.same_document(make_ctx(wiki), note, source_of())

    assert got is not None
    assert got[0] == note.frontmatter["sources"][-1]["resource"] and got[0].startswith("/raw/")
    assert got[1].strip() == OLD.strip()


def test_same_document_records_a_code_decision(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)
    ctx = make_ctx(wiki)

    assert revisions.same_document(ctx, note, source_of()) is not None

    [decision] = decisions(ctx)
    assert (decision.decider, decision.choice) == ("code", "replace")


def test_same_document_ignores_the_see_also_section(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)
    other = wiki.store.write_note(
        wiki.store.load(), title="Other", summary="O.", body="Other.", tags=[], source={"resource": "/raw/o.md", "title": "O"}
    )
    wiki.store.link(note, other)

    assert revisions.same_document(make_ctx(wiki), note, source_of()) is not None


def fails(wiki: Wiki, note: Note, source: Source, classifier: FakeClassifier | None = None, *, decided: bool = True) -> StepContext:
    ctx = make_ctx(wiki, classifier)
    assert revisions.same_document(ctx, note, source) is None
    if decided:
        [decision] = decisions(ctx)
        assert decision.choice.startswith("merge")
    return ctx


def test_same_document_is_none_with_revisions_merge(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm, revisions="merge")
    note = seed(wiki)

    fails(wiki, note, source_of(), decided=False)


def test_a_failed_check_says_which_one_in_the_log(bundle: Path, llm: FakeLLM, caplog: pytest.LogCaptureFixture) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)
    note.body = note.body.rstrip() + "\n\nA sentence somebody added by hand.\n"

    with caplog.at_level(logging.INFO):
        fails(wiki, note, source_of())

    assert [r for r in caplog.records if r.levelno == logging.INFO and r.name.startswith("llmw2")]


def test_same_document_is_none_when_the_body_was_edited_by_hand(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)
    note.body = note.body.rstrip() + "\n\nA sentence somebody added by hand.\n"

    ctx = fails(wiki, note, source_of())

    assert decisions(ctx)[0].decider == "code"


def test_same_document_is_none_for_a_note_without_a_body_hash(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)
    del note.frontmatter["generated"]["body_hash"]

    fails(wiki, note, source_of())


def test_same_document_is_none_for_a_note_without_generated(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)
    del note.frontmatter["generated"]

    fails(wiki, note, source_of())


def test_same_document_is_none_when_a_cited_copy_is_not_in_the_bundle(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = wiki.store.write_note(
        wiki.store.load(), title=TITLE, summary="S.", body=OLD_BODY, tags=[], source={"resource": "/raw/gone.md", "title": TITLE}
    )

    fails(wiki, note, source_of())


def test_same_document_is_none_when_a_source_is_not_a_raw_copy(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = wiki.store.write_note(
        wiki.store.load(), title=TITLE, summary="S.", body=OLD_BODY, tags=[], source={"resource": "https://example.com/a", "title": TITLE}
    )

    fails(wiki, note, source_of())


def test_same_document_ignores_revision_markers_in_the_title(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki, title="Wholesale programme review 2026 - v1")

    assert revisions.same_document(make_ctx(wiki), note, source_of(title="Wholesale Programme Review 2026 draft 3")) is not None


@pytest.mark.parametrize("title", ["Wholesale programme review 2027", "Retail programme review 2026", None])
def test_same_document_is_none_for_another_or_missing_title(bundle: Path, llm: FakeLLM, title: str | None) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)

    fails(wiki, note, source_of(title=title))


def test_same_document_is_none_when_the_notes_source_has_no_title(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)
    del note.frontmatter["sources"][-1]["title"]

    fails(wiki, note, source_of())


@pytest.mark.parametrize(
    ("note_file", "src_origin", "src_resource", "same"),
    [
        pytest.param("docs/plan-2026.docx", "docs/plan-2026.docx", None, True, id="same-origin-id"),
        pytest.param("old/plan-2026.docx", "new/plan-2026.docx", None, True, id="same-basename-other-folder"),
        pytest.param("docs/plan-2026-rev2.docx", "docs/plan-2026.docx", None, True, id="revision-marker-in-file-name"),
        pytest.param("docs/plan-2026.docx", "docs/budget-2026.docx", None, False, id="other-file-name"),
        pytest.param("docs/plan-2026.docx", "docs/plan-2025.docx", None, False, id="other-year-in-file-name"),
        pytest.param("docs/plan-2026.docx", None, "/files/plan-2026.docx", True, id="resource-when-no-origin"),
        pytest.param("docs/plan-2026.docx", None, "/files/budget-2026.docx", False, id="other-resource"),
        pytest.param("docs/plan-2026.docx", None, None, True, id="source-has-no-file-name"),
        pytest.param(None, "docs/budget-2026.docx", None, True, id="note-has-no-file-name"),
    ],
)
def test_same_document_compares_file_names_when_both_sides_have_one(
    bundle: Path, llm: FakeLLM, note_file: str | None, src_origin: str | None, src_resource: str | None, same: bool
) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki, origin_id=note_file)
    source = source_of(title=TITLE, origin_id=src_origin, resource=src_resource)

    if same:
        assert revisions.same_document(make_ctx(wiki), note, source) is not None
    else:
        fails(wiki, note, source)


def test_same_document_prefers_origin_id_over_resource_of_the_source(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki, origin_id="docs/plan-2026.docx")

    ok = source_of(title=TITLE, origin_id="docs/plan-2026.docx", resource="/somewhere/other-name.docx")

    assert revisions.same_document(make_ctx(wiki), note, ok) is not None


def test_the_notes_file_name_is_the_origin_not_the_raw_copy_resource(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki, origin_id="docs/plan-2026.docx")
    assert note.frontmatter["sources"][-1]["resource"].startswith("/raw/")  # a stamped name that never equals the original's

    assert revisions.same_document(make_ctx(wiki), note, source_of(title=TITLE, origin_id=None, resource="plan-2026.docx")) is not None


def test_same_document_is_none_when_the_note_mixes_another_document(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki, OTHER, title="Annual budget", origin_id="finance/annual-budget.xlsx")
    add_copy(wiki, note, OLD)

    fails(wiki, note, source_of())


def test_same_document_compares_with_the_most_recent_copy(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki, OTHER)  # an older copy that has nothing to do with the revision: same title, other text
    last = add_copy(wiki, note, OLD)

    got = revisions.same_document(make_ctx(wiki), note, source_of())

    assert got is not None and got[0] == last and got[1].strip() == OLD.strip()


def test_same_document_is_none_when_it_is_the_older_copy_that_matches(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki, OLD)
    add_copy(wiki, note, OTHER)  # the last copy is unrelated to the source

    fails(wiki, note, source_of())


def test_same_document_is_none_for_a_truncated_new_text(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)
    cut = OLD[: int(len(OLD) * 0.7)]
    new_in_old, old_in_new = revisions.containment(OLD, cut)
    assert new_in_old > 0.95 and old_in_new < revisions.OLD_IN_NEW

    fails(wiki, note, source_of(cut))


def test_same_document_is_none_when_the_new_text_is_mostly_new(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)
    grown = OLD + "\n\n" + doc(100, tag="extra")
    new_in_old, old_in_new = revisions.containment(OLD, grown)
    assert new_in_old < revisions.NEW_IN_OLD and old_in_new > 0.95

    fails(wiki, note, source_of(grown))


def test_same_document_is_none_for_an_unrelated_text(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)

    fails(wiki, note, source_of(OTHER))


# -- same_document in CROW: the identity question --------------------------------------------------------------------


def test_crow_asks_the_identity_question_and_goes_on_when_it_is_the_same_document(
    bundle: Path, llm: FakeLLM, tracker: UsageTracker
) -> None:
    classifier = Scripted(tracker, {IDENTITY_OP: lambda state: 0.1})
    wiki = make_wiki(bundle, llm, classifier)
    note = seed(wiki)
    ctx = make_ctx(wiki, classifier)

    assert revisions.same_document(ctx, note, source_of()) is not None

    [(op, _, questions)] = classifier.calls
    assert op == IDENTITY_OP and list(questions) == ["q"]
    assert questions["q"]["type"] == "noul"
    assert questions["q"]["instructions"].startswith("The changes from A to B replace what the document is about")
    [decision] = decisions(ctx)
    assert (decision.decider, decision.choice) == ("classifier", "replace")


def test_the_identity_state_shows_both_openings_and_the_value_changes(bundle: Path, llm: FakeLLM, tracker: UsageTracker) -> None:
    classifier = Scripted(tracker, {IDENTITY_OP: lambda state: 0.1})
    wiki = make_wiki(bundle, llm, classifier)
    note = seed(wiki)

    revisions.same_document(make_ctx(wiki, classifier), note, source_of())

    state = classifier.calls[0][1]
    assert state.startswith("Document A, title: ")
    assert "\nOpening of A:\n" in state and "Document B, title: " in state and "\nOpening of B:\n" in state
    assert OLD.strip()[:1200] in state and OLD.strip()[:1300] not in state  # 1200 characters of A
    assert NEW.strip()[:1200] in state and NEW.strip()[:1300] not in state
    assert "What B changes against A (2 shown, old → new):\n" in state
    assert re.search(r"^- .*\[AB-1234 → AB-5678\]$", state, re.M)
    assert re.search(r"^- .*\[12 March 2026 → 15 April 2027\]$", state, re.M)


def test_the_identity_state_shows_at_most_forty_changes(bundle: Path, llm: FakeLLM, tracker: UsageTracker) -> None:
    n = 50
    old = doc(600, over={12 * i: f"The batch code is CD-{1000 + i} for lot {i}." for i in range(n)})
    new = doc(600, over={12 * i: f"The batch code is CD-{5000 + i} for lot {i}." for i in range(n)})
    classifier = Scripted(tracker, {IDENTITY_OP: lambda state: 0.1})
    wiki = make_wiki(bundle, llm, classifier)
    note = seed(wiki, old)

    assert revisions.same_document(make_ctx(wiki, classifier), note, source_of(new)) is not None

    state = classifier.calls[0][1]
    assert "(40 shown" in state
    assert len(re.findall(r"^- .*\[.+ → .+\]$", state, re.M)) == 40


@pytest.mark.parametrize(("p", "same"), [(0.1, True), (0.29, True), (0.31, False), (0.9, False)])
def test_the_identity_threshold_is_one_minus_p_at_least_same_doc_min(
    bundle: Path, llm: FakeLLM, tracker: UsageTracker, p: float, same: bool
) -> None:
    classifier = Scripted(tracker, {IDENTITY_OP: lambda state: p})
    wiki = make_wiki(bundle, llm, classifier)
    note = seed(wiki)
    ctx = make_ctx(wiki, classifier)

    got = revisions.same_document(ctx, note, source_of())

    assert (got is not None) == same
    [decision] = decisions(ctx)
    assert decision.decider == "classifier"
    assert decision.choice == "replace" if same else decision.choice.startswith("merge")


def test_a_classifier_error_makes_the_check_fail(bundle: Path, llm: FakeLLM, tracker: UsageTracker) -> None:
    classifier = Scripted(tracker, {IDENTITY_OP: raising})
    wiki = make_wiki(bundle, llm, classifier)
    note = seed(wiki)

    ctx = fails(wiki, note, source_of(), classifier)

    assert decisions(ctx)[0].decider == "classifier"


def test_the_classifier_is_not_asked_when_a_code_check_fails_first(bundle: Path, llm: FakeLLM, tracker: UsageTracker) -> None:
    classifier = Scripted(tracker, {IDENTITY_OP: lambda state: 0.0})
    wiki = make_wiki(bundle, llm, classifier)
    note = seed(wiki)

    fails(wiki, note, source_of(title="Another document 2026"), classifier)

    assert classifier.calls == []


def test_the_classifier_is_never_asked_with_revisions_merge(bundle: Path, llm: FakeLLM, tracker: UsageTracker) -> None:
    classifier = Scripted(tracker, {IDENTITY_OP: lambda state: 0.0})
    wiki = make_wiki(bundle, llm, classifier, revisions="merge")
    note = seed(wiki)

    fails(wiki, note, source_of(), classifier, decided=False)

    assert classifier.calls == []


# -- kept_sentences -------------------------------------------------------------------------------------------------

KEEP = "The supplier code is XY-4455 and it stays valid."
KEEP_2 = "The second code is XY-5566 and it holds."
REMOVED = "Legacy code AB-1234 appears in the archive."
NO_VALUES = "Plain words about nothing special here."
IN_NEW = "Contact code QQ-1111 is unchanged."
NOT_IN_SOURCE = "Obsolete code RR-2222 was withdrawn."
MIXED = "Code XY-4455 replaces RR-2222 as stated."
OLD_K = "\n\n".join(["# Overview", KEEP, REMOVED, NO_VALUES, IN_NEW, NOT_IN_SOURCE, MIXED])
SOURCE_K = "\n\n".join(
    [
        "The new text confirms XY-4455 and XY-5566 once more.",
        "Retired code AB-1234 is mentioned for history, with QQ-1111 beside it.",
        "Nothing else changed here.",
    ]
)
NEW_BODY_K = "The summary mentions QQ-1111 only."


def kept(wiki: Wiki, old: str, source: str, new: str, removed: set[str], classifier: FakeClassifier | None = None) -> list[str]:
    return revisions.kept_sentences(make_ctx(wiki, classifier), old, source, new, removed)


def test_kept_sentences_keeps_a_value_sentence_the_new_body_lacks(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)

    got = kept(wiki, OLD_K, SOURCE_K, NEW_BODY_K, {"AB-1234"})

    assert got == [KEEP]  # not: the removed value, no value, value already in the new body, value gone from the source, a mix


def test_kept_sentences_without_the_removed_string_keeps_that_sentence_too_in_order(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)

    assert kept(wiki, OLD_K, SOURCE_K, NEW_BODY_K, set()) == [KEEP, REMOVED]


@pytest.mark.parametrize("date", ["12 March 2026", "March 12, 2026", "12 marzo 2026"])
def test_kept_sentences_keeps_a_full_date_only_if_the_source_has_it(bundle: Path, llm: FakeLLM, date: str) -> None:
    wiki = make_wiki(bundle, llm)
    old = f"Delivery was due on {date}, ref ZZ-9001."
    with_date = f"Delivery date {date} and ZZ-9001 confirmed again."
    values_only = "Section 12 covers 2026 terms. ZZ-9001 stands."

    assert kept(wiki, old, with_date, "Nothing relevant.", set()) == [old]
    assert kept(wiki, old, values_only, "Nothing relevant.", set()) == []  # the values are there, the date as a whole is not


def test_kept_sentences_is_capped_and_in_order(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    candidates = [f"The batch code is QA-{1000 + i} and it stays valid." for i in range(220)]
    old = "\n\n".join(candidates)
    assert sum(len(s) for s in candidates) > revisions.KEPT_MAX_CHARS

    got = kept(wiki, old, "\n\n".join(candidates), "Nothing relevant.", set())

    assert 100 <= len(got) < len(candidates)
    assert sum(len(s) for s in got) <= revisions.KEPT_MAX_CHARS
    assert got == candidates[: len(got)]


def test_kept_sentences_without_classifier_makes_no_call(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    ctx = make_ctx(wiki)

    revisions.kept_sentences(ctx, OLD_K, SOURCE_K, NEW_BODY_K, set())

    assert ctx.classifier is None and llm.calls == []


def test_kept_sentences_asks_the_classifier_for_each_candidate_only(bundle: Path, llm: FakeLLM, tracker: UsageTracker) -> None:
    classifier = Scripted(tracker, {STILL_OP: lambda state: 0.9})
    wiki = make_wiki(bundle, llm, classifier)

    got = kept(wiki, OLD_K, SOURCE_K, NEW_BODY_K, {"AB-1234"}, classifier)

    assert got == [KEEP]
    assert classifier.ops == [STILL_OP]  # one candidate, one call
    _, state, questions = classifier.calls[0]
    assert list(questions) == ["q"] and questions["q"]["type"] == "noul"
    assert questions["q"]["instructions"].startswith("The passage of the new version states the same facts as the sentence")
    assert state.startswith(f"Sentence (previous version):\n{KEEP}\n\nPassage of the new version:\n")


def test_the_passage_is_the_source_paragraph_with_the_best_score(bundle: Path, llm: FakeLLM, tracker: UsageTracker) -> None:
    classifier = Scripted(tracker, {STILL_OP: lambda state: 0.9})
    wiki = make_wiki(bundle, llm, classifier)
    with_value = "Supplier notes: XY-4455 stays valid."  # one value in common (10) and a few words
    with_words = "The supplier code is and it stays on the list of items for the whole year."  # many words, no value
    source = "\n\n".join(["Unrelated opening paragraph.", with_words, with_value, "Closing paragraph."])

    kept(wiki, KEEP, source, "Nothing relevant.", set(), classifier)

    state = classifier.calls[0][1]
    passage = state.split("Passage of the new version:\n", 1)[1]
    assert passage.strip() == with_value
    assert with_words not in state


@pytest.mark.parametrize(
    ("p", "keeps"),
    [(1.0, True), (revisions.STILL_MIN + 0.01, True), (revisions.STILL_MIN, True), (revisions.STILL_MIN - 0.01, False), (0.0, False)],
)
def test_a_sentence_stays_when_still_holds_reaches_still_min(
    bundle: Path, llm: FakeLLM, tracker: UsageTracker, p: float, keeps: bool
) -> None:
    classifier = Scripted(tracker, {STILL_OP: lambda state: p})
    wiki = make_wiki(bundle, llm, classifier)

    assert kept(wiki, KEEP, SOURCE_K, NEW_BODY_K, set(), classifier) == ([KEEP] if keeps else [])


def test_the_classifier_picks_which_sentences_stay(bundle: Path, llm: FakeLLM, tracker: UsageTracker) -> None:
    classifier = Scripted(tracker, {STILL_OP: lambda state: 0.9 if "XY-5566" in state.split("Passage")[0] else 0.1})
    wiki = make_wiki(bundle, llm, classifier)

    got = kept(wiki, "\n\n".join([KEEP, KEEP_2]), SOURCE_K, NEW_BODY_K, set(), classifier)

    assert got == [KEEP_2]
    assert classifier.ops == [STILL_OP, STILL_OP]


def test_a_classifier_error_drops_that_sentence_only(bundle: Path, llm: FakeLLM, tracker: UsageTracker) -> None:
    def score(state: str) -> float:
        if "XY-4455" in state.split("Passage")[0]:
            raise ModelError("down")
        return 0.9

    classifier = Scripted(tracker, {STILL_OP: score})
    wiki = make_wiki(bundle, llm, classifier)

    assert kept(wiki, "\n\n".join([KEEP, KEEP_2]), SOURCE_K, NEW_BODY_K, set(), classifier) == [KEEP_2]


# -- revise -------------------------------------------------------------------------------------------------------------


def test_revise_builds_the_new_body_from_the_summary_the_changes_and_the_kept_sentences(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki, body=OLD_BODY + f"\n{KEEP}\n")
    source = source_of(NEW + "\n\nThe new text confirms XY-4455 once more.")
    cand = candidate(fields={"k": "v"})
    ctx = make_ctx(wiki, source=source, candidate=cand)

    draft = revisions.revise(ctx, note, source)

    assert isinstance(draft, NoteDraft)
    assert draft.title == note.title  # the existing note's title
    assert (draft.summary, draft.tags, draft.fields) == (cand.summary, cand.tags, cand.fields)
    changes = revisions.changes_section(OLD, source.text)
    assert draft.body.startswith(cand.body + "\n\n" + changes)
    assert "OLDBODYMARKER" not in draft.body
    tail = "\n\n## From the previous version\n\n- " + KEEP
    assert draft.body.endswith(tail.rstrip()) or tail in draft.body
    assert draft.body.index("## Changes from the previous version") < draft.body.index("## From the previous version")


def test_revise_has_no_previous_version_section_when_nothing_is_kept(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)
    source = source_of()
    cand = candidate()

    draft = revisions.revise(make_ctx(wiki, source=source, candidate=cand), note, source)

    assert draft is not None
    assert "From the previous version" not in draft.body
    assert draft.body == cand.body + "\n\n" + revisions.changes_section(OLD, NEW)


def test_revise_without_value_changes_is_the_summary_alone(bundle: Path, llm: FakeLLM) -> None:
    quiet_old = doc(over={5: "The visit goes on quietly in the town."})
    quiet_new = doc(over={5: "The visit goes on slowly in the town."})
    wiki = make_wiki(bundle, llm)
    note = seed(wiki, quiet_old)
    source = source_of(quiet_new)
    cand = candidate()

    draft = revisions.revise(make_ctx(wiki, source=source, candidate=cand), note, source)

    assert draft is not None and draft.body.strip() == cand.body.strip()


def test_revise_is_none_when_it_is_not_the_same_document(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)
    source = source_of(title="Another document")

    assert revisions.revise(make_ctx(wiki, source=source, candidate=candidate()), note, source) is None


def test_revise_is_none_without_a_candidate(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)
    source = source_of()

    assert revisions.revise(make_ctx(wiki, source=source, candidate=None), note, source) is None


def test_revise_is_none_with_revisions_merge(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm, revisions="merge")
    note = seed(wiki)
    source = source_of()

    assert revisions.revise(make_ctx(wiki, source=source, candidate=candidate()), note, source) is None


def test_revise_in_crow_keeps_only_what_still_holds(bundle: Path, llm: FakeLLM, tracker: UsageTracker) -> None:
    classifier = Scripted(tracker, {IDENTITY_OP: lambda state: 0.1, STILL_OP: lambda state: 0.1})
    wiki = make_wiki(bundle, llm, classifier)
    note = seed(wiki, body=OLD_BODY + f"\n{KEEP}\n")
    source = source_of(NEW + "\n\nThe new text confirms XY-4455 once more.")

    draft = revisions.revise(make_ctx(wiki, classifier, source=source, candidate=candidate()), note, source)

    assert draft is not None and "From the previous version" not in draft.body
    assert classifier.ops == [IDENTITY_OP, STILL_OP]


# -- the Librarian ------------------------------------------------------------------------------------------------------


def summarize_reply(title: str = "Wholesale programme review 2026 rev 2") -> dict[str, Any]:
    return {
        "title": title,
        "summary": f"What {title} says.",
        "tags": ["Topic"],
        "body": "# Summary\n\nNEWSUMMARYMARKER the review as rewritten.",
    }


def merge_reply() -> dict[str, Any]:
    return {"title": TITLE, "summary": "Merged.", "tags": [], "body": "# Overview\n\nMERGEDBYLLM facts."}


def classic_ingest(bundle: Path, llm: FakeLLM, *, title: str = REV_TITLE, **cfg: Any) -> tuple[Wiki, Any]:
    wiki = make_wiki(bundle, llm, **cfg)
    seed(wiki)
    llm.add(SUMMARIZE, summarize_reply()).add(ROUTE, {"action": "new"})
    llm.add(FIND_MATCH, {"match": "wholesale-programme-review-2026.md"}).add(CONSOLIDATE, {"decision": "modify"})
    llm.add(MERGE, merge_reply())
    return wiki, wiki.ingest(NEW, title=title, origin=Origin("local", "", REV_ORIGIN_ID))


def check_replaced(wiki: Wiki, bundle: Path, result: Any, rel: str) -> OKFDocument:
    assert (result.action, result.note) == ("merged", rel)
    note = read(bundle / rel)
    [archived_file] = archive_files(bundle)
    archived_rel = "/" + archived_file.relative_to(bundle).as_posix()
    assert re.fullmatch(r"/\.archive/(.+/)?[^/]+--\d{8}-\d{6}\.md", archived_rel)
    old = read(archived_file)
    assert (old.frontmatter["status"], old.frontmatter["archived_from"]) == ("archived", "/" + rel)
    assert "OLDBODYMARKER" in old.body
    assert len(old.frontmatter["sources"]) == 1  # the note as it was
    assert note.frontmatter["previous_version"] == archived_rel
    assert "NEWSUMMARYMARKER" in note.body and "OLDBODYMARKER" not in note.body
    assert "## Changes from the previous version" in note.body
    assert "**AB-5678**" in note.body and "(previously: AB-1234)" in note.body
    assert note.frontmatter["title"] == TITLE  # the existing note's title
    assert note.frontmatter["description"] == "What Wholesale programme review 2026 rev 2 says."
    assert "topic" in note.frontmatter["tags"]
    assert len(note.frontmatter["sources"]) == 2
    assert note.frontmatter["generated"]["body_hash"] == revisions.body_hash(
        OKFDocument(note.frontmatter, note.body).body.split("# See also")[0]
    )
    log = (bundle / "log.md").read_text(encoding="utf-8")
    assert "**Revised**" in log and "**Merged**" not in log
    assert f"previous version archived at {archived_rel}" in log
    assert [n.rel for n in wiki.store.load().all_notes() if n.rel.startswith(".archive")] == []
    return note


def test_classic_a_revision_replaces_the_note_in_place_and_archives_the_old_one(bundle: Path, llm: FakeLLM) -> None:
    wiki, result = classic_ingest(bundle, llm)

    check_replaced(wiki, bundle, result, "wholesale-programme-review-2026.md")

    assert MERGE not in llm.ops  # the summary the model already wrote is the new body
    assert sorted(p.name for p in bundle.glob("*.md")) == ["index.md", "log.md", "wholesale-programme-review-2026.md"]


def test_classic_with_revisions_merge_the_normal_merge_happens(bundle: Path, llm: FakeLLM) -> None:
    _, result = classic_ingest(bundle, llm, revisions="merge")

    assert (result.action, result.note) == ("merged", "wholesale-programme-review-2026.md")
    assert MERGE in llm.ops
    note = read(bundle / "wholesale-programme-review-2026.md")
    assert "MERGEDBYLLM" in note.body and "previous_version" not in note.frontmatter
    assert archive_files(bundle) == [] and not (bundle / ".archive").exists()
    assert "**Merged**" in (bundle / "log.md").read_text(encoding="utf-8")


def test_classic_another_title_falls_back_to_the_normal_merge(bundle: Path, llm: FakeLLM) -> None:
    _, result = classic_ingest(bundle, llm, title="Retail programme review 2026")

    assert MERGE in llm.ops and result.action == "merged"
    assert archive_files(bundle) == []
    assert "previous_version" not in read(bundle / "wholesale-programme-review-2026.md").frontmatter


def test_classic_a_hand_edited_note_is_merged_not_replaced(bundle: Path, llm: FakeLLM) -> None:
    wiki = make_wiki(bundle, llm)
    note = seed(wiki)
    note.body = note.body.rstrip() + "\n\nAdded by a person.\n"
    (bundle / note.rel).write_text(OKFDocument(note.frontmatter, note.body).serialize(), encoding="utf-8")
    llm.add(SUMMARIZE, summarize_reply()).add(ROUTE, {"action": "new"})
    llm.add(FIND_MATCH, {"match": note.rel}).add(CONSOLIDATE, {"decision": "modify"}).add(MERGE, merge_reply())

    wiki.ingest(NEW, title=REV_TITLE, origin=Origin("local", "", REV_ORIGIN_ID))

    assert MERGE in llm.ops and archive_files(bundle) == []


def test_classic_the_new_note_is_a_candidate_for_the_next_revision(bundle: Path, llm: FakeLLM) -> None:
    wiki, result = classic_ingest(bundle, llm)
    check_replaced(wiki, bundle, result, "wholesale-programme-review-2026.md")
    third = NEW.replace("AB-5678", "AB-9999")
    time.sleep(1.1)  # the archive's name has a resolution of one second
    llm.add(SUMMARIZE, summarize_reply()).add(ROUTE, {"action": "new"})
    llm.add(FIND_MATCH, {"match": result.note}).add(CONSOLIDATE, {"decision": "modify"})

    again = wiki.ingest(
        third,
        title="Wholesale programme review 2026 — rev. 3",
        origin=Origin("local", "", "reviews/wholesale-programme-review-2026-rev3.docx"),
    )

    assert again.action == "merged" and len(archive_files(bundle)) == 2
    note = read(bundle / result.note)
    assert "(previously: AB-5678)" in note.body and "**AB-9999**" in note.body


def crow_setup(bundle: Path, llm: FakeLLM, tracker: UsageTracker, identity: float, **cfg: Any) -> tuple[Wiki, Scripted]:
    classifier = Scripted(tracker, {IDENTITY_OP: lambda state: identity})
    wiki = make_wiki(bundle, llm, classifier, **cfg)
    seed(wiki, folder="/wholesale")
    classifier.add_choice("route", "wholesale", {"wholesale": 0.9, NEW_FOLDER: 0.1}, 0.9)
    classifier.add_choice("route", HERE, {HERE: 0.9, NEW_FOLDER: 0.05, "None of these": 0.05}, 0.9)
    classifier.noul_scores["match"] = {f'"{TITLE}"': 0.9}
    classifier.add_choice("consolidate", MODIFY, {MODIFY: 0.9}, 0.9)
    llm.add(SUMMARIZE, summarize_reply()).add(MERGE, merge_reply())
    return wiki, classifier


def test_crow_a_revision_replaces_the_note_in_place_and_archives_the_old_one(bundle: Path, llm: FakeLLM, tracker: UsageTracker) -> None:
    wiki, classifier = crow_setup(bundle, llm, tracker, identity=0.1)

    result = wiki.ingest(NEW, title=REV_TITLE, origin=Origin("local", "", REV_ORIGIN_ID))

    check_replaced(wiki, bundle, result, "wholesale/wholesale-programme-review-2026.md")
    assert IDENTITY_OP in classifier.ops and MERGE not in llm.ops
    assert any(d.step == "revise" and d.choice == "replace" for d in result.decisions)


def test_crow_when_the_classifier_says_the_identity_changed_the_normal_merge_happens(
    bundle: Path, llm: FakeLLM, tracker: UsageTracker
) -> None:
    wiki, classifier = crow_setup(bundle, llm, tracker, identity=0.9)

    result = wiki.ingest(NEW, title=REV_TITLE, origin=Origin("local", "", REV_ORIGIN_ID))

    assert IDENTITY_OP in classifier.ops and MERGE in llm.ops
    assert result.action == "merged" and archive_files(bundle) == []
    assert "MERGEDBYLLM" in read(bundle / result.note).body
    assert any(d.step == "revise" and d.choice.startswith("merge") for d in result.decisions)
    assert "**Merged**" in (bundle / "log.md").read_text(encoding="utf-8")


def test_crow_with_revisions_merge_the_normal_merge_happens_and_nothing_extra_is_asked(
    bundle: Path, llm: FakeLLM, tracker: UsageTracker
) -> None:
    wiki, classifier = crow_setup(bundle, llm, tracker, identity=0.0, revisions="merge")

    result = wiki.ingest(NEW, title=REV_TITLE, origin=Origin("local", "", REV_ORIGIN_ID))

    assert MERGE in llm.ops and result.action == "merged"
    assert IDENTITY_OP not in classifier.ops and STILL_OP not in classifier.ops
    assert not any(d.step == "revise" for d in result.decisions)
    assert not (bundle / ".archive").exists()
    note = read(bundle / result.note)
    assert "previous_version" not in note.frontmatter and "MERGEDBYLLM" in note.body
    assert note.frontmatter["generated"]["body_hash"].startswith("sha256:")  # the one change that is always on


# -- the Researcher -----------------------------------------------------------------------------------------------------

NOTE_TITLE = "Pricing tiers"


def priced_wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier | None, *, archive: bool = True, **cfg: Any) -> tuple[Wiki, str]:
    """/finance/pricing-tiers.md revised once: the live note says 120, the archived one 100. Returns the wiki and the archive's path."""
    wiki = make_wiki(bundle, llm, classifier, **cfg)
    store = wiki.store
    top = folder_of(wiki, "/finance")
    src = {"resource": "https://example.com", "title": NOTE_TITLE}
    note = store.write_note(top, title=NOTE_TITLE, summary="About pricing.", body="Price was 100 per unit.", tags=[], source=src)
    other = store.write_note(top, title="Revenue policy", summary="About revenue.", body="Revenue is booked monthly.", tags=[], source=src)
    archived = store.archive_note(note)
    store.update_note(
        note, title=NOTE_TITLE, summary="About pricing.", body="Price is 120 per unit.", tags=[], source=src, previous_version=archived
    )
    assert other.rel
    if not archive:
        (bundle / archived.lstrip("/")).unlink()
    return wiki, archived


def retrieval(classifier: FakeClassifier, p_history: Callable[[str], float] | None) -> None:
    classifier.noul_scores["retrieve_folder"] = {'folder "finance"': 0.9}
    classifier.noul_scores["retrieve_note"] = {f'note titled "{NOTE_TITLE}"': 0.9}
    if p_history is not None:
        classifier.noul_scores[HISTORY_OP] = p_history


def answer_prompt(llm: FakeLLM) -> str:
    return next(msgs[-1]["content"] for op, msgs in llm.calls if op == ANSWER)


QUESTION = "What was the price per unit before the revision?"


def test_historical_question_adds_the_archived_note_right_after_its_note(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki, archived = priced_wiki(bundle, llm, classifier)
    retrieval(classifier, lambda text: 0.9)
    llm.add(ANSWER, f"It was 100 [{archived.lstrip('/')}].")

    answer = wiki.ask(QUESTION)

    assert answer.notes == ["finance/pricing-tiers.md", archived.lstrip("/")]
    prompt = answer_prompt(llm)
    assert "Price is 120 per unit." in prompt and "Price was 100 per unit." in prompt
    assert prompt.index("Price is 120") < prompt.index("Price was 100")
    assert answer.citations == [archived.lstrip("/")]  # the archive's path is a valid citation
    [call] = [c for c in classifier.calls if c[0] == HISTORY_OP]
    assert call[1] == QUESTION and list(call[2]) == ["q"]
    assert call[2]["q"]["instructions"].startswith("The question asks what a value or fact was in an earlier version")
    decision = next(d for d in answer.decisions if d.step == "historical")
    assert decision.decider == "classifier"


def test_the_archived_note_follows_its_own_note_not_the_end_of_the_list(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki, archived = priced_wiki(bundle, llm, classifier)
    retrieval(classifier, lambda text: 0.9)
    classifier.noul_scores["retrieve_note"] = {f'note titled "{NOTE_TITLE}"': 0.9, 'note titled "Revenue policy"': 0.8}
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    plain = [n for n in answer.notes if not n.startswith(".archive")]
    assert set(plain) == {"finance/pricing-tiers.md", "finance/revenue-policy.md"}
    assert answer.notes.index(archived.lstrip("/")) == answer.notes.index("finance/pricing-tiers.md") + 1


@pytest.mark.parametrize(("p", "added"), [(0.9, True), (0.51, True), (revisions.HISTORY_MIN, False), (0.2, False)])
def test_the_archive_is_added_only_above_history_min(bundle: Path, llm: FakeLLM, classifier: FakeClassifier, p: float, added: bool) -> None:
    wiki, archived = priced_wiki(bundle, llm, classifier)
    retrieval(classifier, lambda text: p)
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask("What is the price per unit now?")

    assert HISTORY_OP in classifier.ops
    assert (archived.lstrip("/") in answer.notes) == added
    assert "Price was 100" not in answer_prompt(llm) or added


def test_a_classifier_error_adds_nothing_and_the_answer_goes_on(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki, _ = priced_wiki(bundle, llm, classifier)

    def down(text: str) -> float:
        raise ModelError("down")

    retrieval(classifier, down)
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert answer.notes == ["finance/pricing-tiers.md"]


def test_a_missing_archive_file_is_skipped(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki, _ = priced_wiki(bundle, llm, classifier, archive=False)
    retrieval(classifier, lambda text: 0.9)
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert answer.notes == ["finance/pricing-tiers.md"]


def test_revisions_merge_never_asks_whether_the_question_is_historical(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki, _ = priced_wiki(bundle, llm, classifier, revisions="merge")
    retrieval(classifier, lambda text: 0.9)
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert HISTORY_OP not in classifier.ops
    assert answer.notes == ["finance/pricing-tiers.md"]
    assert not any(d.step == "historical" for d in answer.decisions)


def test_no_selected_note_with_a_previous_version_no_question_to_the_classifier(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier)
    top = folder_of(wiki, "/finance")
    wiki.store.write_note(
        top,
        title=NOTE_TITLE,
        summary="About pricing.",
        body="Price is 120.",
        tags=[],
        source={"resource": "https://example.com", "title": "S"},
    )
    retrieval(classifier, lambda text: 0.9)
    llm.add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert HISTORY_OP not in classifier.ops and answer.notes == ["finance/pricing-tiers.md"]


def test_classic_researcher_never_adds_the_archive(bundle: Path, llm: FakeLLM) -> None:
    wiki, archived = priced_wiki(bundle, llm, None)
    llm.add("researcher/navigate", {"open": ["finance"]}, {"select": ["finance/pricing-tiers.md"], "done": True}).add(ANSWER, "Answer.")

    answer = wiki.ask(QUESTION)

    assert archived.lstrip("/") not in answer.notes
