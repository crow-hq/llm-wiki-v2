# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Blind from the spec: a folder that holds too many direct notes after a single document is split into subfolders."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import CROW, Item, ModelError, Origin, Route, Wiki, WikiConfig
from llmw2.agents.prompts import Prompts
from llmw2.bundle.tree import LOG, Note
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import ContentLLM, FakeClassifier, FakeLLM

HERE, NEW, NONE = "Here", "New subfolder", "None of these"
SUMMARIZE, SPLIT, MERGE = "librarian/summarize", "librarian/split", "librarian/merge"
JSON_SUFFIX = "\n\nReply with a single JSON object and nothing else."

SPLIT_TEXT = """A folder of a knowledge base holds these notes directly (number. title: summary):
<notes>

The folder: <folder>: <description>
Its subfolders now (path: description):
<subfolders>

Open subfolders of <folder> for the specific subjects (one client, one supplier, a project, a product line, a person or
team, a recurring kind of document) that at least 2 of these notes share and no subfolder above covers. For each, list
the numbers of its notes: they are moved there. Notes that share their subject with no other listed note stay where they
are, and so does what is about the folder's subject as a whole. Never a subfolder for whatever is left over. Each note in
at most one subfolder. Never rename or move <folder>. Names: 1 to 3 lowercase words joined by hyphens, letters a-z and
digits; a description says what belongs there. It is fine to open none.

Reply with JSON: {"folders": [{"name": "...", "description": "...", "notes": [1, 2]}]}"""


# -- helpers ----------------------------------------------------------------------


def draft(title: str) -> dict[str, Any]:
    return {"title": title, "summary": f"What {title} says.", "tags": [], "body": "# Overview\n\nThe facts."}


def add_folder(wiki: Wiki, parent: str, name: str, description: str) -> None:
    wiki.store.create_folder(wiki.store.load().find(parent), name, description)


def add_note(wiki: Wiki, folder: str, title: str, summary: str | None = None) -> Note:
    return wiki.store.write_note(
        wiki.store.load().find(folder),
        title=title,
        summary=summary or f"About {title}.",
        body="# Overview\n\nSeeded.",
        tags=[],
        source={"resource": "/raw/seed.md", "title": "Seed"},
    )


def title(n: int) -> str:
    return f"Note {n:02d}"  # sorts as the numbers do: the tree order of the notes is the numbers'


def seed(wiki: Wiki, count: int, folder: str = "/sales", **summaries: str) -> None:
    for n in range(1, count + 1):
        add_note(wiki, folder, title(n), summaries.get(f"n{n}"))


@pytest.fixture
def sales(crow_wiki: Wiki) -> Wiki:
    add_folder(crow_wiki, "/", "sales", "Sales documents.")
    return crow_wiki


def file_note(wiki: Wiki, llm: FakeLLM, classifier: FakeClassifier, n: int, folder: str = "/sales") -> Any:
    """Ingest note n, routed to `folder`."""
    llm.add(SUMMARIZE, draft(title(n)))
    classifier.add_choice("route", folder, {folder: 0.9, NONE: 0.1}, 0.9)
    return wiki.ingest(f"Text of note {n}.")


def splits(llm: FakeLLM) -> int:
    return llm.ops.count(SPLIT)


def direct(wiki: Wiki, folder: str = "/sales") -> list[str]:
    return [n.title for n in wiki.store.load().find(folder).notes]  # type: ignore[union-attr]


def log_lines(wiki: Wiki, verb: str) -> list[str]:
    text = (wiki.cfg.bundle / LOG).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.startswith(f"* **{verb}**")]


def user_text(call: tuple[str, list[dict[str, str]]]) -> str:
    return call[1][-1]["content"].removesuffix(JSON_SUFFIX)


def groups(*specs: tuple[str, list[int]]) -> dict[str, Any]:
    return {"folders": [{"name": name, "description": f"{name.title()} things.", "notes": notes} for name, notes in specs]}


def expected_split(folder: str, description: str, notes: list[str], subfolders: str = "(none)") -> str:
    return (
        SPLIT_TEXT.replace("<notes>", "\n".join(notes))
        .replace("<folder>", folder)
        .replace("<description>", description)
        .replace("<subfolders>", subfolders)
    )


# -- the constants and models ----------------------------------------------------------------


def test_split_at_is_twelve() -> None:
    from llmw2 import wiki

    assert wiki.SPLIT_AT == 12


def test_split_plan_models() -> None:
    from llmw2.agents.data import SplitFolder, SplitPlan

    assert SplitPlan().folders == []
    assert SplitFolder(name="a", description="b").notes == []
    assert SplitPlan.model_validate({"folders": [{"name": "a", "description": "b", "notes": [1, 2]}]}).folders[0].notes == [1, 2]


# -- when it runs ------------------------------------------------------------------------------


def test_eleven_direct_notes_make_no_split_call(sales: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    seed(sales, 10)

    # ACT
    file_note(sales, llm, classifier, 11)

    # ASSERT
    assert llm.ops == [SUMMARIZE]
    assert log_lines(sales, "Split") == []


def test_the_twelfth_direct_note_makes_one_split_call_and_moves_the_groups(sales: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    seed(sales, 11)
    first, sixth = (sales.store.load().find("/sales").note(s) for s in ("note-01", "note-06"))  # type: ignore[union-attr]
    sales.store.link(first, sixth)  # type: ignore[arg-type]
    sales.store.archive_note(first)  # type: ignore[arg-type]
    llm.add(
        SPLIT,
        groups(
            ("renewals", [1, 2, 3, 99, 0]),  # out of range numbers are ignored
            ("partners", [3, 4, 5]),  # note 3 is taken: partners gets 4 and 5
            ("solo", [6]),  # one note: skipped
            ("dup", [7, 3]),  # 3 is taken: one note left: skipped
        ),
    )

    # ACT
    result = file_note(sales, llm, classifier, 12)

    # ASSERT
    assert llm.ops == [SUMMARIZE, SPLIT]
    tree = sales.store.load()
    assert [f.label for f in tree.find("/sales").subfolders] == ["/sales/partners", "/sales/renewals"]  # type: ignore[union-attr]
    assert [n.title for n in tree.find("/sales/renewals").notes] == [title(1), title(2), title(3)]  # type: ignore[union-attr]
    assert [n.title for n in tree.find("/sales/partners").notes] == [title(4), title(5)]  # type: ignore[union-attr]
    assert direct(sales) == [title(n) for n in (6, 7, 8, 9, 10, 11, 12)]
    assert tree.find("/sales").description == "Sales documents."  # type: ignore[union-attr]
    assert "renewals/note-01.md" in sales.store.read("sales/note-06.md").body  # the link follows the moved note
    assert sales.check() == []  # links and the archived copy still resolve
    decision = next(d for d in result.decisions if d.step == "split")
    assert (decision.decider, decision.choice, decision.fallback) == ("llm", "/sales: 2 opened", False)
    assert result.folder == "/sales"


def test_split_logs_one_split_line_and_marks_the_created_folders_by_split(sales: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    seed(sales, 11)
    llm.add(SPLIT, groups(("renewals", [1, 2]), ("partners", [3, 4])))

    # ACT
    file_note(sales, llm, classifier, 12)

    # ASSERT
    assert log_lines(sales, "Split") == ["* **Split**: [/sales](sales/index.md) - at 12 notes, 2 opened: /sales/renewals, /sales/partners"]
    created = [line for line in log_lines(sales, "Created folder") if "/sales/" in line]
    assert len(created) == 2 and all(line.endswith(", by split") for line in created)


def test_a_log_saved_as_latin_1_is_read_for_the_split_check(sales: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE: an editor saved log.md as Latin-1 ("é" is one byte, not UTF-8)
    seed(sales, 11)
    (sales.cfg.bundle / LOG).write_bytes("# Wiki log\n\n## 2026-10-01\n* **Note**: Café\n".encode("latin-1"))
    llm.add(SPLIT, groups(("renewals", [1, 2]), ("partners", [3, 4])))

    # ACT
    file_note(sales, llm, classifier, 12)

    # ASSERT
    assert splits(llm) == 1
    assert "Café" in (sales.cfg.bundle / LOG).read_text(encoding="utf-8")


def test_split_attempt_with_nothing_opened_is_logged_with_zero(sales: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    seed(sales, 11)
    llm.add(SPLIT, {"folders": []})

    # ACT
    result = file_note(sales, llm, classifier, 12)

    # ASSERT
    assert log_lines(sales, "Split") == ["* **Split**: [/sales](sales/index.md) - at 12 notes, 0 opened"]
    assert next(d for d in result.decisions if d.step == "split").choice == "/sales: 0 opened"
    assert len(direct(sales)) == 12


def test_next_attempt_is_at_the_next_multiple_of_twelve(sales: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    seed(sales, 11)
    llm.add(SPLIT, {"folders": []}, {"folders": []})
    file_note(sales, llm, classifier, 12)
    assert splits(llm) == 1

    # ACT
    for n in range(13, 24):  # 13th to 23rd
        file_note(sales, llm, classifier, n)
    after_23 = splits(llm)
    file_note(sales, llm, classifier, 24)

    # ASSERT
    assert after_23 == 1
    assert splits(llm) == 2
    assert log_lines(sales, "Split")[0].endswith("at 24 notes, 0 opened")


def test_refilling_a_split_folder_to_twelve_does_not_split_again_until_twenty_four(
    sales: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    seed(sales, 11)
    llm.add(SPLIT, groups(("renewals", [1, 2, 3])), {"folders": []})
    file_note(sales, llm, classifier, 12)
    assert len(direct(sales)) == 9 and splits(llm) == 1

    # ACT
    counts = {}
    for n in range(13, 28):  # direct notes: 10 at 13, 12 at 15, 24 at 27
        file_note(sales, llm, classifier, n)
        counts[len(direct(sales))] = splits(llm)

    # ASSERT
    assert counts[12] == 1  # twelve again: multiple 1 was tried
    assert counts[23] == 1
    assert counts[24] == 2


def test_a_new_wiki_on_the_same_bundle_does_not_split_again_at_the_same_multiple(
    bundle: Path, sales: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    seed(sales, 11)
    llm.add(SPLIT, {"folders": []}, {"folders": []})
    file_note(sales, llm, classifier, 12)
    again = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier)

    # ACT
    file_note(again, llm, classifier, 13)
    after_13 = splits(llm)
    for n in range(14, 25):
        file_note(again, llm, classifier, n)

    # ASSERT
    assert after_13 == 1
    assert splits(llm) == 2


def test_the_multiple_tried_is_kept_per_folder(sales: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    add_folder(sales, "/", "support", "Help.")
    seed(sales, 11)
    seed(sales, 11, "/support")
    llm.add(SPLIT, {"folders": []}, {"folders": []})
    file_note(sales, llm, classifier, 12)

    # ACT
    file_note(sales, llm, classifier, 12, "/support")

    # ASSERT
    assert splits(llm) == 2
    assert [line.split(" - ")[0] for line in log_lines(sales, "Split")] == [
        "* **Split**: [/support](support/index.md)",
        "* **Split**: [/sales](sales/index.md)",
    ]


def test_a_merged_document_also_triggers_the_check(sales: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    seed(sales, 12)
    llm.add(SUMMARIZE, draft("Note 01 update")).add(MERGE, draft(title(1))).add(SPLIT, {"folders": []})
    classifier.add_choice("route", "/sales", {"/sales": 0.9, NONE: 0.1}, 0.9)
    classifier.noul_scores["match"] = {'"Note 01"': 0.9}
    classifier.add_choice("consolidate", "Modify", {"Modify": 0.95, "New note": 0.05}, 0.9)

    # ACT
    result = sales.ingest("Text of the update.")

    # ASSERT
    assert result.action == "merged"
    assert llm.ops == [SUMMARIZE, MERGE, SPLIT]


def test_ingest_file_triggers_the_check(sales: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    seed(sales, 11)
    llm.add(SUMMARIZE, draft(title(12))).add(SPLIT, {"folders": []})
    classifier.add_choice("route", "/sales", {"/sales": 0.9, NONE: 0.1}, 0.9)

    # ACT
    sales.ingest_file(b"Text of note 12.", "note-12.md")

    # ASSERT
    assert llm.ops == [SUMMARIZE, SPLIT]


@pytest.mark.parametrize(("depth", "max_depth", "splits_expected"), [(2, 4, True), (3, 4, False), (2, 2, False), (1, 2, True)])
def test_a_folder_at_or_below_the_plan_depth_or_max_depth_is_never_split(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier, depth: int, max_depth: int, splits_expected: bool
) -> None:
    # ARRANGE
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", max_depth=max_depth), llm=llm, classifier=classifier)
    wiki.init()
    path = ""
    for name in ("a", "b", "c")[:depth]:
        add_folder(wiki, path or "/", name, f"Folder {name}.")
        path += f"/{name}"
    seed(wiki, 11, path)
    llm.add(SPLIT, {"folders": []})

    # ACT
    file_note(wiki, llm, classifier, 12, path)

    # ASSERT
    assert (splits(llm) == 1) is splits_expected


def test_never_at_the_root(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", max_depth=0), llm=llm, classifier=classifier)
    wiki.init()
    seed(wiki, 11, "/")
    llm.add(SUMMARIZE, draft(title(12)))

    # ACT
    wiki.ingest("Text of note 12.")

    # ASSERT
    assert llm.ops == [SUMMARIZE] and len(wiki.store.load().notes) == 12


def test_a_proposal_naming_an_existing_subfolder_is_skipped_and_nothing_moves_into_it(
    sales: Wiki, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    # ARRANGE
    add_folder(sales, "/sales", "emea", "EMEA deals.")
    add_note(sales, "/sales/emea", "Emea note")
    seed(sales, 11)
    llm.add(SPLIT, groups(("emea", [1, 2]), ("renewals", [3, 4])))

    # ACT
    file_note(sales, llm, classifier, 12)

    # ASSERT
    assert direct(sales, "/sales/emea") == ["Emea note"]
    assert direct(sales, "/sales/renewals") == [title(3), title(4)]
    assert title(1) in direct(sales) and title(2) in direct(sales)
    assert log_lines(sales, "Split") == ["* **Split**: [/sales](sales/index.md) - at 12 notes, 1 opened: /sales/renewals"]


# -- failures ---------------------------------------------------------------------------------------


class SplitFailsLLM(FakeLLM):
    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        if kwargs.get("op") == SPLIT:
            self.calls.append((SPLIT, [dict(m) for m in messages]))
            raise ModelError("down")
        return super().chat(messages, **kwargs)


@pytest.mark.parametrize("failure", ["malformed", "error"])
def test_a_failing_split_never_hurts_the_filed_document_and_counts_as_an_attempt(bundle: Path, failure: str) -> None:
    # ARRANGE
    tracker = UsageTracker()
    llm = FakeLLM(tracker) if failure == "malformed" else SplitFailsLLM(tracker)
    classifier = FakeClassifier(tracker)
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier)
    wiki.init()
    add_folder(wiki, "/", "sales", "Sales documents.")
    seed(wiki, 11)
    if failure == "malformed":
        llm.add(SPLIT, {"folders": "bad"}, {"folders": "bad"})  # the second one answers the retry

    # ACT
    result = file_note(wiki, llm, classifier, 12)  # type: ignore[arg-type]
    attempts = splits(llm)
    file_note(wiki, llm, classifier, 13)  # type: ignore[arg-type]

    # ASSERT
    assert result.action == "created" and len(direct(wiki)) == 13
    decision = next(d for d in result.decisions if d.step == "split")
    assert (decision.decider, decision.choice, decision.fallback) == ("llm", "/sales: error", True)
    assert log_lines(wiki, "Split") == ["* **Split**: [/sales](sales/index.md) - at 12 notes, 0 opened"]
    assert splits(llm) == attempts  # the next document does not retry at the same multiple
    assert wiki.check() == []


# -- the prompt -----------------------------------------------------------------------------------


def note_lines(wiki: Wiki, extra: str) -> list[str]:
    tree_notes = wiki.store.load().find("/sales").notes  # type: ignore[union-attr]
    lines = [f"{i}. {n.title}: {' '.join(n.summary.split())[:200]}" for i, n in enumerate(tree_notes, start=1)]
    return [*lines, f"{len(lines) + 1}. {title(12)}: {extra}"]


def test_the_split_prompt_is_exactly_the_specified_text(sales: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    long_summary = "Long  summary\nwith   spaces " + "z" * 250
    seed(sales, 11, n3=long_summary)
    lines = note_lines(sales, "What Note 12 says.")
    llm.add(SPLIT, {"folders": []})

    # ACT
    file_note(sales, llm, classifier, 12)

    # ASSERT
    op, messages = next(c for c in llm.calls if c[0] == SPLIT)
    assert messages[0]["content"] == Prompts().render("librarian/system")
    assert user_text((op, messages)) == expected_split("/sales", "Sales documents.", lines)
    assert len(lines[2].split(": ", 1)[1]) == 200  # the summary is cut at 200 characters, whitespace collapsed
    assert "Long summary with spaces z" in lines[2]


def test_the_split_prompt_lists_the_subfolders_with_their_descriptions(sales: Wiki, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    add_folder(sales, "/sales", "emea", "EMEA deals.")
    add_folder(sales, "/sales", "asia", "Asian deals.")
    seed(sales, 11)
    lines = note_lines(sales, "What Note 12 says.")
    llm.add(SPLIT, {"folders": []})

    # ACT
    file_note(sales, llm, classifier, 12)

    # ASSERT
    call = next(c for c in llm.calls if c[0] == SPLIT)
    assert user_text(call) == expected_split("/sales", "Sales documents.", lines, "- /sales/asia: Asian deals.\n- /sales/emea: EMEA deals.")


# -- classic mode and custom steps -----------------------------------------------------------------


def test_classic_mode_never_splits(classic_wiki: Wiki, llm: FakeLLM) -> None:
    # ARRANGE
    add_folder(classic_wiki, "/", "sales", "Sales documents.")
    seed(classic_wiki, 11)
    llm.add(SUMMARIZE, draft(title(12))).add("librarian/route", {"action": "descend", "subfolder": "sales"}, {"action": "here"})
    llm.add("librarian/find_match", {"match": None}).add("librarian/relate", {"related": []})

    # ACT
    classic_wiki.ingest("Text of note 12.")

    # ASSERT
    assert len(direct(classic_wiki)) == 12
    assert SPLIT not in llm.ops


def test_a_custom_route_in_crow_still_splits(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    # ARRANGE
    def my_route(ctx: Any, root: Any, cand: Any) -> Route:
        folder = root.find("/sales")
        return Route(folder, False, folder.ancestors())

    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier, steps=replace(CROW, name="custom", route=my_route))
    wiki.init()
    add_folder(wiki, "/", "sales", "Sales documents.")
    seed(wiki, 11)
    llm.add(SUMMARIZE, draft(title(12))).add(SPLIT, {"folders": []})

    # ACT
    wiki.ingest("Text of note 12.")

    # ASSERT
    assert llm.ops == [SUMMARIZE, SPLIT]


# -- ingest_many ----------------------------------------------------------------------------------------


class BatchLLM(ContentLLM):
    def _plan_more(self, user: str) -> Any:
        return {"folders": []}

    def _split(self, user: str) -> Any:
        return {"folders": []}


class RedClassifier(FakeClassifier):
    """Everything goes to /red (flat) or, by levels, into red; no match, no link."""

    def ask(self, state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
        self.calls.append((op, state, questions))
        self.usage.record("classifier", self.model, 50, 0, op=op)
        answers: dict[str, dict[str, Any]] = {}
        for key, question in questions.items():
            if question["type"] != "choice":
                answers[key] = {"type": "noul", "noul": 0.0}
                continue
            options = list(question["criteria"])
            pick = next((o for o in ("/red", "red", HERE, NEW) if o in options), options[0])
            other = next((o for o in options if o != pick), pick)
            answers[key] = {
                "type": "choice",
                "choice": pick,
                "probabilities": {pick: 0.95, other: 0.05} if other != pick else {pick: 1.0},
                "confidence": 0.9,
            }
        return answers


def red_wiki(bundle: Path, seeded: int = 11) -> tuple[Wiki, BatchLLM]:
    llm = BatchLLM(UsageTracker())
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", concurrency=1), llm=llm, classifier=RedClassifier(llm.usage))
    wiki.init()
    add_folder(wiki, "/", "red", "Red things.")
    seed(wiki, seeded, "/red")
    return wiki, llm


def doc(n: int) -> Item:
    return Item(f"doc-{n:02d}.md", data=f"topic: red-{n}\nline of document {n}.".encode(), origin=Origin("test", "acct", f"doc-{n:02d}"))


def test_a_batch_window_of_one_document_runs_the_split_check(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = red_wiki(bundle)

    # ACT
    report = wiki.ingest_many([doc(0)])

    # ASSERT
    assert report.stopped is None and len(report.results) == 1
    assert llm.ops.count(SPLIT) == 1
    assert not [op for op in llm.ops if "plan" in op]


def test_a_batch_window_of_several_documents_never_splits(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = red_wiki(bundle)

    # ACT
    report = wiki.ingest_many([doc(n) for n in range(3)])

    # ASSERT
    assert report.stopped is None and len(report.results) == 3
    assert len(wiki.store.load().find("/red").notes) == 14  # type: ignore[union-attr]
    assert SPLIT not in llm.ops


def test_the_last_window_of_one_document_splits_after_windows_that_never_did(bundle: Path) -> None:
    # ARRANGE
    wiki, llm = red_wiki(bundle, seeded=0)

    # ACT
    report = wiki.ingest_many([doc(n) for n in range(51)])

    # ASSERT
    assert report.stopped is None and len(report.results) == 51
    assert llm.ops.count(SPLIT) == 1
    assert llm.ops.index(SPLIT) > max(i for i, op in enumerate(llm.ops) if op == SUMMARIZE)
    assert log_lines(wiki, "Split") == ["* **Split**: [/red](red/index.md) - at 48 notes, 0 opened"]
