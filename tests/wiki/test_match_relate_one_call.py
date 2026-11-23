# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""CROW's `match` asks, in its own request, the relate questions of the note the document becomes: a new note's `relate`
takes those answers (same state, same questions) and asks only about the notes found by rare values; a merged note asks its own."""

from __future__ import annotations

import random
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import CROW, Wiki, WikiConfig
from llmw2.agents import linking, steps_classic, steps_crow
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import StepContext, decision_hash
from llmw2.agents.steps_crow import JOINT_OP, RelateAhead
from llmw2.bundle.tree import Folder, Note
from llmw2.config import CrowConfig
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_rare_value_links import make_root

HERE, MODIFY, NEW_NOTE = "Here", "Modify", "New note"
SUMMARIZE, MERGE = "librarian/summarize", "librarian/merge"
CODE = "ZK-4471"
LEVELS = replace(CROW, name="levels", route=steps_crow._route_by_levels)  # the per-level walk: routes scripted one Choice per level


def draft(title: str, body: str) -> dict[str, Any]:
    return {"title": title, "summary": f"What {title} says.", "tags": [], "body": body}


def make(bundle: Path, llm: FakeLLM, classifier: FakeClassifier, steps: Any = LEVELS, **crow: Any) -> Wiki:
    """alpha holds two notes; beta, a distant folder, a ledger holding CODE (found only by rare values)."""
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", crow=CrowConfig(**crow)), llm=llm, classifier=classifier, steps=steps)
    wiki.init()
    for name in ("alpha", "beta"):
        wiki.store.create_folder(wiki.store.load().find("/"), name, f"{name} things.")
    for folder, title, body in (
        ("/alpha", "Alpha note", "# Overview\n\nSeeded."),
        ("/alpha", "Alpha two", "# Overview\n\nAlso seeded."),
        ("/beta", "Beta ledger", f"# Overview\n\nThe ledger lists {CODE}."),
    ):
        source = {"resource": "/raw/s.md", "title": "S"}
        wiki.store.write_note(wiki.store.load().find(folder), title=title, summary=f"About {title}.", body=body, tags=[], source=source)
    classifier.add_choice("route", "alpha", {"alpha": 0.9, "beta": 0.05, "New subfolder": 0.05}, 0.9)
    classifier.add_choice("route", HERE, {HERE: 0.9, "New subfolder": 0.05, "None of these": 0.05}, 0.9)
    return wiki


def questions(classifier: FakeClassifier, op: str) -> list[str]:
    return [q["instructions"] for o, _, qs in classifier.calls if o == op for q in qs.values()]


def test_a_new_note_asks_match_and_relate_in_one_request(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make(bundle, llm, classifier)
    llm.add(SUMMARIZE, draft("Q3 revenue", "# Overview\n\nRevenue grew."))
    classifier.noul_scores["relate"] = {'"Alpha two"': 0.9}

    result = wiki.ingest("Revenue grew.")

    assert result.action == "created"
    assert classifier.ops.count(JOINT_OP) == 1
    assert "relate" not in classifier.ops and "match" not in classifier.ops
    joint = next(qs for o, _, qs in classifier.calls if o == JOINT_OP)
    assert sorted(k[0] for k in joint) == ["m", "m", "r", "r"]  # both alpha notes, once per step
    assert result.related == ["alpha/alpha-two.md"]
    relate = next(d for d in result.decisions if d.step == "relate")
    assert relate.scores == {"alpha/alpha-two.md": 0.9, "alpha/alpha-note.md": 0.0}


def test_match_reads_its_own_answers_and_relate_its_own(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make(bundle, llm, classifier)
    llm.add(SUMMARIZE, draft("Q3 revenue", "# Overview\n\nRevenue grew."))
    classifier.noul_scores["match"] = {'"Alpha note"': 0.9}  # match finds it; relate does not link it
    classifier.add_choice("consolidate", NEW_NOTE, {MODIFY: 0.1, NEW_NOTE: 0.9}, 0.9)

    result = wiki.ingest("Revenue grew.")

    assert next(d for d in result.decisions if d.step == "match").choice == "alpha/alpha-note.md"
    assert result.action == "created"
    assert result.related == []
    assert "relate" not in classifier.ops  # the note was not merged: its relate answers came with the match


def test_notes_found_by_rare_values_are_asked_with_the_match(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make(bundle, llm, classifier)
    llm.add(SUMMARIZE, draft("Q3 revenue", f"# Overview\n\nInvoice {CODE} paid."))
    classifier.noul_scores["relate"] = {'"Beta ledger"': 0.9, '"Alpha note"': 0.9}

    result = wiki.ingest("Invoice paid.")

    assert classifier.ops[-1] == JOINT_OP and "relate" not in classifier.ops  # found before the note is written, as after
    joint = next(qs for o, _, qs in classifier.calls if o == JOINT_OP)
    assert any('"Beta ledger"' in q["instructions"] for k, q in joint.items() if k.startswith("r"))
    assert next(d for d in result.decisions if d.step == "relate_candidates").choice == "1 by rare values"
    assert sorted(result.related) == ["alpha/alpha-note.md", "beta/beta-ledger.md"]


def test_a_merged_note_asks_its_own_relate(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make(bundle, llm, classifier)
    llm.add(SUMMARIZE, draft("Q3 revenue", "# Overview\n\nRevenue grew."))
    llm.add(MERGE, draft("Alpha note", "# Overview\n\nSeeded. Revenue grew."))
    classifier.noul_scores["match"] = {'"Alpha note"': 0.9}
    classifier.noul_scores["relate"] = {'"Alpha two"': 0.9}
    classifier.add_choice("consolidate", MODIFY, {MODIFY: 0.9, NEW_NOTE: 0.1}, 0.9)

    result = wiki.ingest("Revenue grew.")

    assert result.action == "merged"
    assert classifier.ops[-1] == "relate"  # the merged note reads otherwise than the document: asked again, against itself
    assert result.related == ["alpha/alpha-two.md"]


def test_switched_off_match_and_relate_ask_apart_as_before(
    bundle: Path, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = make(bundle, llm, classifier, joint_match_relate=False)
    llm.add(SUMMARIZE, draft("Q3 revenue", f"# Overview\n\nInvoice {CODE} paid."))
    classifier.noul_scores["relate"] = {'"Beta ledger"': 0.9, '"Alpha note"': 0.9}
    found = linking.rare_scores
    ahead: list[bool] = []

    def spy(*args: Any, **kwargs: Any) -> Any:
        ahead.append(bool(kwargs.get("ahead")))
        return found(*args, **kwargs)

    monkeypatch.setattr(linking, "rare_scores", spy)

    result = wiki.ingest("Invoice paid.")

    assert classifier.ops[-2:] == ["match", "relate"] and JOINT_OP not in classifier.ops
    assert ahead == [False]  # the notes sharing rare values found once, after the write
    assert len(questions(classifier, "relate")) == 3  # both alpha notes and the ledger, in one request
    assert sorted(result.related) == ["alpha/alpha-note.md", "beta/beta-ledger.md"]


def test_the_switch_leaves_the_decision_hash_alone(tmp_path: Path) -> None:
    on, off = (WikiConfig(bundle=tmp_path, mode="crow", crow=CrowConfig(joint_match_relate=v)) for v in (True, False))

    assert decision_hash(CROW, on, Prompts()) == decision_hash(CROW, off, Prompts())


def test_steps_with_another_relate_get_the_plain_match(bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make(bundle, llm, classifier, steps=replace(LEVELS, relate=steps_classic.relate))
    llm.add(SUMMARIZE, draft("Q3 revenue", "# Overview\n\nRevenue grew."))
    llm.add("librarian/relate", {"related": []})

    wiki.ingest("Revenue grew.")

    assert "match" in classifier.ops and JOINT_OP not in classifier.ops


# -- the step, on its own ---------------------------------------------------------------------


def ctx_of(tmp_path: Path, classifier: FakeClassifier, llm: FakeLLM) -> StepContext:
    return StepContext(llm, classifier, Prompts(), WikiConfig(bundle=tmp_path / "w", mode="crow"))


def note(folder: Folder, title: str, body: str = "Body.") -> Note:
    front = {"title": title, "description": f"About {title}."}
    n = Note(path=folder.path / f"{title}.md", rel=f"{folder.rel}/{title}.md", frontmatter=front, body=body, folder=folder)
    folder.notes.append(n)
    return n


def test_relate_asks_again_when_the_state_is_not_the_one_match_read(tmp_path: Path, classifier: FakeClassifier, llm: FakeLLM) -> None:
    root = Folder(path=tmp_path, rel="")
    folder = Folder(path=tmp_path / "a", rel="a", parent=root)
    other, mine = note(folder, "other"), note(folder, "mine")
    ctx = ctx_of(tmp_path, classifier, llm)
    question = ctx.prompts.render("classifier/relate", title=other.title, summary=other.summary)
    ctx.relate_ahead = RelateAhead([other], "a", state="not this note", answers={(other.rel, question): 0.9})
    classifier.noul_scores["relate"] = {}

    assert steps_crow.relate(ctx, mine, [other]) == []
    assert classifier.ops == ["relate"]

    ctx.relate_ahead.state = mine.compact(ctx.cfg.classifier.state_chars)
    classifier.calls.clear()
    assert steps_crow.relate(ctx, mine, [other]) == [other]  # the same state: the answer kept, nothing asked
    assert classifier.ops == []


def test_relate_asks_only_the_notes_match_did_not_ask_about(tmp_path: Path, classifier: FakeClassifier, llm: FakeLLM) -> None:
    root = Folder(path=tmp_path, rel="")
    folder = Folder(path=tmp_path / "a", rel="a", parent=root)
    known, unknown, mine = note(folder, "known"), note(folder, "unknown"), note(folder, "mine")
    ctx = ctx_of(tmp_path, classifier, llm)
    question = ctx.prompts.render("classifier/relate", title=known.title, summary=known.summary)
    state = mine.compact(ctx.cfg.classifier.state_chars)
    ctx.relate_ahead = RelateAhead([known], "a", state=state, answers={(known.rel, question): 0.9})
    classifier.noul_scores["relate"] = {'"unknown"': 0.8}

    assert steps_crow.relate(ctx, mine, [known, unknown]) == [known, unknown]
    assert classifier.ops == ["relate"]
    (asked,) = [qs for _, _, qs in classifier.calls]
    assert len(asked) == 1 and '"unknown"' in next(iter(asked.values()))["instructions"]


# -- the notes found by rare values, ahead and after ------------------------------------------


def _wiki_with_values(rng: random.Random, notes: int, values: int) -> Folder:
    """`notes` notes in three folders, each holding a few of `values` codes (some held by few notes, some by many)."""
    bodies = {}
    for i in range(notes):
        held = rng.sample(range(values), rng.randint(1, 4))
        bodies[f"f{i % 3}/n{i}.md"] = " ".join(f"Code ZQ-{v:04d}." for v in held)
    return make_root(bodies)


@pytest.mark.parametrize("seed", range(12))
def test_rare_scores_ahead_are_the_scores_once_the_note_is_written(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    root = _wiki_with_values(rng, rng.choice([20, 59, 60, 61, 100]), rng.choice([8, 30, 120]))  # 60 and 61 straddle the cap of 3
    folder = root.find("f1")
    body = " ".join(f"Code ZQ-{v:04d}." for v in rng.sample(range(8), 3))
    plain = {"title": "Plain", "description": "Plain."}
    to_be = Note(path=folder.path / ".to-be.md", rel="f1/.to-be.md", frontmatter=dict(plain), body=body, folder=folder)
    exclude = {n.rel for n in folder.notes}

    ahead = [(n.rel, round(s, 9)) for n, s in linking.rare_scores(root, to_be, exclude, ahead=True)]
    written = Note(path=folder.path / "new.md", rel="f1/new.md", frontmatter=dict(plain), body=body, folder=folder)
    folder.notes.append(written)
    after = [(n.rel, round(s, 9)) for n, s in linking.rare_scores(root, written, exclude | {written.rel})]

    assert ahead == after


def test_rare_scores_run_one_at_a_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The filer's section and the rehearsal of its next entry call `rare_scores` from two threads on one index: while one
    updates it, the other waits."""
    root = _wiki_with_values(random.Random(1), 40, 30)
    note = root.all_notes()[0]
    inside, release = threading.Event(), threading.Event()
    put = linking._Index.put

    def slow_put(self: Any, *args: Any) -> None:
        if threading.current_thread().name == "first" and not inside.is_set():
            inside.set()
            release.wait(5)
        put(self, *args)

    monkeypatch.setattr(linking._Index, "put", slow_put)
    linking._indexes.clear()
    out: dict[str, Any] = {}
    first = threading.Thread(target=lambda: out.setdefault("first", linking.rare_scores(root, note, set())), name="first")
    second = threading.Thread(target=lambda: out.setdefault("second", linking.rare_scores(root, note, set())), name="second")
    first.start()
    assert inside.wait(5)
    second.start()
    second.join(0.5)
    assert second.is_alive()  # it waits for the first call to end
    release.set()
    first.join(5)
    second.join(5)
    assert [(n.rel, s) for n, s in out["first"]] == [(n.rel, s) for n, s in out["second"]]
